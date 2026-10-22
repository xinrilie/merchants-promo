from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


# =========================================================
# KONFIGURASI
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
INTERNAL_DIR = ROOT_DIR / "_internal"

# Scrape outputs belong in _internal, not mixed with source code in engine/.
OUT_JSON = INTERNAL_DIR / "promo_catalog_all_merchants.json"
OUT_CSV = INTERNAL_DIR / "promo_catalog_all_merchants.csv"
RUN_LOG = INTERNAL_DIR / "promo_catalog_all_merchants_runlog.json"
ATTEMPT_JSON = INTERNAL_DIR / "promo_catalog_all_merchants_latest_attempt.json"
HISTORY_DIR = INTERNAL_DIR / "promo_catalog_all_merchants_history"
MANUAL_URLS = BASE_DIR / "promo_sources.txt"

TODAY = datetime.now(ZoneInfo("Asia/Jakarta")).date()
ARTICLE_HOST = "momsmoney.kontan.co.id"
HEMAT_CATEGORY_URL = "https://momsmoney.kontan.co.id/rubrik/342/Hemat"

TIMEOUT = 15
DELAY = 0.4
MAX_LISTING_PAGES = 8
MAX_ARTICLES_TO_FETCH = 60
MIN_ACTIVE_ARTICLES = 2
MIN_ACTIVE_PRODUCT_ROWS = 5

MONTHS = {
    "januari": 1,
    "februari": 2,
    "maret": 3,
    "april": 4,
    "mei": 5,
    "juni": 6,
    "juli": 7,
    "agustus": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "desember": 12,
}

# Ini hanya dipakai untuk mengenali judul artikel promo, bukan untuk
# membatasi merchant. Artikel promo merchant apa pun tetap bisa lolos.
PROMO_TITLE_PATTERN = re.compile(
    r"\bpromo\b|\bpromosi\b|\bkatalog\b|\bdiskon\b|\bgratis\b|"
    r"\btebus\s+murah\b|\bweekend\s+deals?\b|\bbuy\s*1\b|"
    r"\bbeli\s+\d+\b|\bharga\s+(?:promo|spesial)\b|\bkupon\b|"
    r"\bcashback\b|\bvoucher\b|\bflash\s*sale\b|\bpaket\s+hemat\b|"
    r"\bpotongan\s+harga\b|\bjsm\b|\bpsm\b|\bice\s*cream\s+fair\b|"
    r"\bsuper\s+hemat\b|\bweekdays?\b|\bweekend\b",
    re.IGNORECASE,
)

OFFER_PATTERN = re.compile(
    r"\bRp\.?\s*\d|\bgratis\b|\bbeli\s+\d|\bdiskon\b|"
    r"\bharga\s+(?:promo|normal|spesial)\b|\btebus\s+murah\b|"
    r"\bcashback\b|\bvoucher\b|\bpotongan\b|\btambah\s+Rp\b",
    re.IGNORECASE,
)

NOISE_PREFIX = re.compile(
    r"^(?:buruan\b|bagi moms\b|selain\b|supaya\b|nah\b|melansir\b|"
    r"pembayaran\b|khusus pembayaran\b|berlaku untuk\b|maksimal\b|"
    r"tidak berlaku\b|minimal belanja\b|diskon aplikasi\b|baca juga\b|"
    r"cek berita\b|kuota harian\b|syarat dan ketentuan\b)",
    re.IGNORECASE,
)

# Daftar ini hanya untuk mengisi metadata merchant. Merchant yang tidak ada
# di sini tetap discan; field merchant_name-nya akan bernilai null.
MERCHANT_ALIASES = [
    ("McDonald's", ("mcdonald's", "mcdonalds", "mcd")),
    ("Richeese Factory", ("richeese factory", "richeese")),
    ("Pizza Hut", ("pizza hut",)),
    ("Burger King", ("burger king",)),
    ("Domino's Pizza", ("domino's pizza", "domino pizza", "domino's")),
    ("KFC", ("kfc",)),
    ("A&W", ("a&w", "a and w")),
    ("HokBen", ("hokben",)),
    ("Popeyes", ("popeyes",)),
    ("Subway", ("subway",)),
    ("Starbucks", ("starbucks",)),
    ("Kopi Kenangan", ("kopi kenangan",)),
    ("Fore Coffee", ("fore coffee",)),
    ("J.CO", ("j.co", "jco donuts", "jco")),
    ("Wingstop", ("wingstop",)),
    ("Bakmi GM", ("bakmi gm", "bakmi gm")),
    ("Ichiban Sushi", ("ichiban sushi", "ichiban")),
    ("Gokana", ("gokana",)),
    ("Es Teler 77", ("es teler 77", "esteler 77")),
    ("Alfamart", ("alfamart", "alfagift")),
    ("Indomaret", ("indomaret",)),
    ("Alfamidi", ("alfamidi",)),
    ("Super Indo", ("superindo", "super indo")),
    ("Hypermart", ("hypermart",)),
    ("Transmart", ("transmart",)),
    ("Lotte Mart", ("lotte mart", "lottemart")),
    ("Ranch Market", ("ranch market",)),
    ("Farmers Market", ("farmers market",)),
    ("Foodhall", ("foodhall", "the foodhall")),
    ("AEON", ("aeon",)),
    ("Hero", ("hero supermarket",)),
    ("Tip Top", ("tip top",)),
    ("Matahari", ("matahari",)),
    ("Guardian", ("guardian",)),
    ("Watsons", ("watsons",)),
    ("Sociolla", ("sociolla",)),
    ("MR.DIY", ("mr.diy", "mr diy")),
    ("IKEA", ("ikea",)),
    ("INFORMA", ("informa",)),
    ("ACE", ("ace hardware", "ace indonesia")),
    ("Shopee", ("shopee",)),
    ("Tokopedia", ("tokopedia",)),
    ("Blibli", ("blibli",)),
    ("Lazada", ("lazada",)),
]


# =========================================================
# UTILITAS
# =========================================================


def say(message: str) -> None:
    print(message, flush=True)


def timestamp() -> str:
    return datetime.now(ZoneInfo("Asia/Jakarta")).isoformat()


def normalize_text(value) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_article_url(url: str) -> str:
    """Hanya menerima URL artikel Momsmoney/Kontan dan menstabilkan URL."""
    if not url:
        return ""

    url, _ = urldefrag(url.strip())
    parsed = urlparse(url)

    if (parsed.hostname or "").lower() != ARTICLE_HOST:
        return ""
    if not parsed.path.startswith("/news/"):
        return ""

    return parsed._replace(
        scheme="https",
        netloc=ARTICLE_HOST,
        query="",
        fragment="",
    ).geturl().rstrip("/")


def is_article_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme in {"http", "https"}
        and (parsed.hostname or "").lower() == ARTICLE_HOST
        and parsed.path.startswith("/news/")
    )


def is_relevant_title(title: str) -> bool:
    """Filter generik promo; tidak mengunci judul pada Alfamart/Alfagift."""
    text = normalize_text(title)
    return bool(text and PROMO_TITLE_PATTERN.search(text))


def infer_merchant(title: str) -> str | None:
    """Deteksi merchant dengan batas kata agar alias pendek tidak false-positive."""
    text = normalize_text(title)
    checks = []
    for canonical, aliases in MERCHANT_ALIASES:
        for alias in aliases:
            checks.append((len(alias), canonical, alias))
    checks.sort(reverse=True)

    for _, canonical, alias in checks:
        escaped = re.escape(normalize_text(alias)).replace(r"\ ", r"\s+")
        if re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text):
            return canonical
    return None


def campaign_type(title: str) -> str:
    text = normalize_text(title)
    patterns = [
        ("JSM", r"\bjsm\b"),
        ("PSM", r"\bpsm\b"),
        ("Weekend Deals", r"weekend\s+deals?"),
        ("Weekday", r"\bweekday\b"),
        ("Ice Cream Fair", r"ice\s*cream\s+fair"),
        ("Tebus Murah", r"tebus\s+murah"),
        ("Beli Banyak Lebih Hemat", r"beli banyak lebih hemat"),
        ("Super Hemat", r"super hemat"),
        ("Payday / Gajian", r"payday|gajian|gantung gajian"),
        ("Kupon / Voucher", r"kupon|voucher"),
        ("Diskon", r"diskon|potongan harga"),
        ("Beli Gratis", r"beli\s+\d+.*gratis\s+\d+|buy\s*1.*get\s*1"),
        ("Katalog Promo", r"katalog"),
        ("Promo Umum", r"promo|promosi"),
    ]
    for label, pattern in patterns:
        if re.search(pattern, text):
            return label
    return "Lainnya"


def rupiah_numbers(text: str) -> list[int]:
    matches = re.findall(
        r"\bRp\.?\s*([0-9][0-9.,]*)",
        text,
        flags=re.IGNORECASE,
    )
    result = []
    for value in matches:
        digits = re.sub(r"\D", "", value)
        if digits:
            result.append(int(digits))
    return result


def labeled_price(text: str, label_pattern: str) -> int | None:
    match = re.search(
        rf"(?:{label_pattern})\s*[:\-]?\s*Rp\.?\s*([0-9][0-9.,]*)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else None


# =========================================================
# PERIODE PROMO DAN STATUS AKTIF
# =========================================================


# =========================================================
# KUALITAS DATA: PERIODE, DISKON, DAN NORMALISASI
# =========================================================


def parse_period_details(text: str) -> dict:
    """Mengurai periode beserta tingkat kepastian dan teks yang cocok."""
    normalized = normalize_text(text)
    month_pattern = "|".join(MONTHS)
    separator = r"(?:[-–—]|sampai|hingga|s\.d\.|s/d)"
    patterns = [
        ("range_cross_month", "range", rf"\b(\d{{1,2}})\s+({month_pattern})\s*{separator}\s*(\d{{1,2}})\s+({month_pattern})\s+(\d{{4}})\b"),
        ("range_same_month", "range", rf"\b(\d{{1,2}})\s*{separator}\s*(\d{{1,2}})\s+({month_pattern})\s+(\d{{4}})\b"),
        ("single_day", "single_day", rf"\b(\d{{1,2}})\s+({month_pattern})\s+(\d{{4}})\b"),
    ]
    for ptype, precision, pattern in patterns:
        m = re.search(pattern, normalized)
        if not m:
            continue
        # "hingga 22 Oktober" is an end-date constraint, not a one-day campaign.
        if ptype == "single_day" and re.search(r"(?:hingga|sampai|s\.d\.|s/d)\s*$", normalized[:m.start()]):
            continue
        try:
            if ptype == "range_cross_month":
                d1, m1, d2, m2, year = m.groups()
                start = date(int(year), MONTHS[m1], int(d1))
                end = date(int(year), MONTHS[m2], int(d2))
            elif ptype == "range_same_month":
                d1, d2, month_name, year = m.groups()
                start = date(int(year), MONTHS[month_name], int(d1))
                end = date(int(year), MONTHS[month_name], int(d2))
            else:
                day, month_name, year = m.groups()
                start = date(int(year), MONTHS[month_name], int(day))
                end = start
            if end < start:
                continue
            return {
                "valid_from": start.isoformat(),
                "valid_until": end.isoformat(),
                "period_precision": precision,
                "matched_period_text": m.group(0),
            }
        except (ValueError, KeyError):
            continue

    # End date only. The start date is not guessed here.
    m = re.search(rf"\b(?:hingga|sampai|s\.d\.|s/d)\s*(\d{{1,2}})\s+({month_pattern})\s+(\d{{4}})\b", normalized)
    if m:
        day, month_name, year = m.groups()
        try:
            end = date(int(year), MONTHS[month_name], int(day))
            return {
                "valid_from": None,
                "valid_until": end.isoformat(),
                "period_precision": "until",
                "matched_period_text": m.group(0),
            }
        except ValueError:
            pass

    # Month/year is only an estimate; it must not silently become an exact range.
    m = re.search(rf"\b({month_pattern})\s+(\d{{4}})\b", normalized)
    if m:
        month_name, year = m.groups()
        month = MONTHS[month_name]
        start = date(int(year), month, 1)
        next_month = date(int(year) + 1, 1, 1) if month == 12 else date(int(year), month + 1, 1)
        end = next_month - timedelta(days=1)
        return {
            "valid_from": start.isoformat(),
            "valid_until": end.isoformat(),
            "period_precision": "month",
            "matched_period_text": m.group(0),
        }

    return {
        "valid_from": None,
        "valid_until": None,
        "period_precision": "unknown",
        "matched_period_text": None,
    }


def extract_period_details(title: str, lead_text: str, published_at: str | None = None) -> dict:
    """Judul diutamakan jika eksplisit; lead mengisi/meningkatkan presisi."""
    title_period = parse_period_details(title)
    lead_period = parse_period_details(lead_text)
    flags = []

    title_known = title_period["period_precision"] != "unknown"
    lead_known = lead_period["period_precision"] != "unknown"
    exact = {"range", "single_day"}

    if title_known and lead_known:
        title_dates = (title_period["valid_from"], title_period["valid_until"])
        lead_dates = (lead_period["valid_from"], lead_period["valid_until"])
        if title_dates != lead_dates:
            flags.append("title_lead_period_differs")
        # If title is only month-level while lead has an exact date range,
        # prefer the more precise lead range and retain a diagnostic flag.
        if title_period["period_precision"] == "month" and lead_period["period_precision"] in exact:
            chosen = lead_period
            flags.append("used_more_precise_lead_period")
        else:
            chosen = title_period
    elif title_known:
        chosen = title_period
    elif lead_known:
        chosen = lead_period
        flags.append("period_taken_from_lead")
    else:
        chosen = title_period

    start = chosen["valid_from"]
    end = chosen["valid_until"]
    precision = chosen["period_precision"]

    # For an end-date-only promo, publication date can serve as a reviewable
    # lower-bound estimate, never as an exact start date.
    if precision == "until" and not start and published_at:
        date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})", str(published_at))
        if date_match:
            try:
                published_date = date.fromisoformat(date_match.group(1))
                end_date = date.fromisoformat(end) if end else None
                if end_date and published_date <= end_date:
                    start = published_date.isoformat()
                    flags.append("start_inferred_from_published_at")
            except ValueError:
                pass

    return {
        "valid_from": start,
        "valid_until": end,
        "period_precision": precision,
        "period_flags": " | ".join(flags) if flags else None,
        "matched_period_text": chosen.get("matched_period_text"),
    }


def parse_explicit_discount(text: str) -> tuple[float | None, str | None]:
    """Mengambil persen hanya dari teks penawaran produk, bukan judul kampanye."""
    text = normalize_text(text)
    patterns = [
        r"(?:diskon|potongan(?:\s+harga)?|hemat)\s*(?:hingga|sampai|up\s+to)\s*(\d{1,2}(?:[.,]\d+)?)\s*%",
        r"(?:diskon|potongan(?:\s+harga)?|hemat)\s*(\d{1,2}(?:[.,]\d+)?)\s*%",
        r"(\d{1,2}(?:[.,]\d+)?)\s*%\s*(?:off|diskon)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            value = float(m.group(1).replace(",", "."))
            if 0 <= value <= 100:
                prefix = text[max(0, m.start() - 24):m.start()]
                is_max = bool(re.search(r"hingga|sampai|up to", prefix)) or "hingga" in m.group(0) or "sampai" in m.group(0) or "up to" in m.group(0)
                return round(value, 1), ("explicit_max" if is_max else "explicit_offer")
    return None, None


def bundle_discount_estimate(text: str) -> float | None:
    """Estimasi matematika buy-X-free-Y dengan asumsi item gratis bernilai sama."""
    m = re.search(r"\bbeli\s*(\d+)\s*gratis\s*(\d+)\b|\bbuy\s*(\d+)\s*get\s*(\d+)\b", normalize_text(text), re.I)
    if not m:
        return None
    x, y = (int(m.group(1) or m.group(3)), int(m.group(2) or m.group(4)))
    if x <= 0 or y <= 0:
        return None
    return round(100 * y / (x + y), 1)


def discount_fields(
    offer_text: str,
    promo_price: int | None,
    normal_price: int | None,
    price_basis: str | None,
    purchase_condition: str | None = None,
    deal_type: str | None = None,
    quality_flags: list[str] | None = None,
) -> dict:
    """Diskon jelas dipisahkan antara persen eksplisit, hitungan harga, dan estimasi bundle."""
    flags = quality_flags if quality_flags is not None else []
    explicit, explicit_basis = parse_explicit_discount(offer_text)
    calc = None
    savings = None
    safe_basis = (price_basis or "").casefold()
    offer_norm = normalize_text(offer_text)
    bundle = bool(bundle_discount_estimate(offer_text)) or bool(re.search(r"\bbeli\s*\d+\b", offer_norm))
    additional = bool(re.search(r"\btambah\s+rp\b", offer_norm))
    unsafe_unit = bool(re.search(r"potongan|\/\s*(?:pcs|pc|item|potongan|per)\b", offer_norm))

    if (
        promo_price is not None and normal_price is not None
        and promo_price > 0 and normal_price > 0
        and normal_price > promo_price
        and safe_basis == "offer_price"
        and not bundle and not additional and not unsafe_unit
    ):
        calc = round((normal_price - promo_price) / normal_price * 100, 1)
        savings = normal_price - promo_price

    estimated_bundle = bundle_discount_estimate(offer_text)
    if estimated_bundle is not None:
        flags.append("bundle_discount_is_estimate_same_item_value")

    if explicit is not None:
        discount = explicit
        discount_basis = explicit_basis
        confidence = "medium" if explicit_basis == "explicit_max" else "high"
        if calc is not None and abs(explicit - calc) > 2:
            flags.append("stated_discount_differs_from_price_calculation")
    elif calc is not None:
        discount = calc
        discount_basis = "calculated_from_unit_prices"
        confidence = "high"
    elif estimated_bundle is not None:
        discount = estimated_bundle
        discount_basis = "bundle_estimate_same_item_value"
        confidence = "low"
    else:
        discount = None
        discount_basis = "not_available"
        confidence = "unknown"

    return {
        "discount_percent": discount,
        "discount_percent_basis": discount_basis,
        "discount_percent_confidence": confidence,
        "discount_percent_calculated": calc,
        "discount_savings_rp": savings,
        "estimated_bundle_discount_percent": estimated_bundle,
    }


def parse_period_from_text(text: str) -> tuple[str | None, str | None]:
    text = normalize_text(text)
    month_pattern = "|".join(MONTHS)
    separator = r"(?:[-–—]|sampai|hingga|s\.d\.|s/d)"

    patterns = [
        (
            "cross_month",
            rf"\b(\d{{1,2}})\s+({month_pattern})\s*{separator}\s*"
            rf"(\d{{1,2}})\s+({month_pattern})\s+(\d{{4}})\b",
        ),
        (
            "same_month",
            rf"\b(\d{{1,2}})\s*{separator}\s*(\d{{1,2}})\s+"
            rf"({month_pattern})\s+(\d{{4}})\b",
        ),
        (
            "single_day",
            rf"\b(\d{{1,2}})\s+({month_pattern})\s+(\d{{4}})\b",
        ),
    ]

    for pattern_type, pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            if pattern_type == "cross_month":
                d1, m1, d2, m2, year = match.groups()
                start = date(int(year), MONTHS[m1], int(d1))
                end = date(int(year), MONTHS[m2], int(d2))
            elif pattern_type == "same_month":
                d1, d2, month_name, year = match.groups()
                month = MONTHS[month_name]
                start = date(int(year), month, int(d1))
                end = date(int(year), month, int(d2))
            else:
                day, month_name, year = match.groups()
                start = date(int(year), MONTHS[month_name], int(day))
                end = start

            if end < start:
                continue
            return start.isoformat(), end.isoformat()
        except (ValueError, KeyError):
            continue

    # Month-only title (e.g. "Promo Popeyes ... Oktober 2026") is useful
    # for candidate prioritization, but the final status is marked estimated.
    month_match = re.search(rf"\b({month_pattern})\s+(\d{{4}})\b", text)
    if month_match:
        month_name, year = month_match.groups()
        month = MONTHS[month_name]
        start = date(int(year), month, 1)
        if month == 12:
            next_month = date(int(year) + 1, 1, 1)
        else:
            next_month = date(int(year), month + 1, 1)
        end = next_month - timedelta(days=1)
        return start.isoformat(), end.isoformat()

    return None, None


def extract_period(title: str, lead_text: str) -> tuple[str | None, str | None]:
    # Prioritaskan periode yang ditulis di judul, lalu paragraf pembuka artikel.
    start, end = parse_period_from_text(title)
    if start and end:
        return start, end
    return parse_period_from_text(lead_text)


def promo_status(
    valid_from: str | None,
    valid_until: str | None,
    period_precision: str | None = None,
) -> str:
    """Pisahkan status yang terkonfirmasi dari status yang hanya estimasi."""
    if not valid_from or not valid_until:
        return "unknown"
    try:
        start = date.fromisoformat(valid_from)
        end = date.fromisoformat(valid_until)
    except (ValueError, TypeError):
        return "unknown"
    if end < start:
        return "unknown"
    if TODAY < start:
        return "upcoming_estimated" if period_precision in {"month", "until"} else "upcoming"
    if TODAY > end:
        return "expired"
    if period_precision in {"month", "until"}:
        return "active_estimated"
    if period_precision in {"range", "single_day"}:
        return "active"
    return "active_estimated"


# =========================================================
# DISCOVERY ARTIKEL
# =========================================================


def get_anchor_title(anchor) -> str:
    title = anchor.get_text(" ", strip=True)
    if title and len(title) > 4:
        return title

    for parent in anchor.parents:
        if parent.name in {"h1", "h2", "h3", "h4"}:
            return parent.get_text(" ", strip=True)
        heading = parent.find(["h1", "h2", "h3", "h4"])
        if heading:
            candidate = heading.get_text(" ", strip=True)
            if len(candidate) > 4:
                return candidate
        if parent.name in {"article", "body"}:
            break

    return (anchor.get("title") or anchor.get("aria-label") or "").strip()


def read_manual_urls() -> list[str]:
    if not MANUAL_URLS.exists():
        return []
    urls = []
    for line in MANUAL_URLS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if is_article_url(line):
            urls.append(clean_article_url(line))
    return list(OrderedDict.fromkeys(urls))


def discover_articles(session: requests.Session) -> tuple[list[dict], list[dict]]:
    candidates: list[dict] = []
    listing_log: list[dict] = []
    seen_urls: set[str] = set()

    for page_number in range(1, MAX_LISTING_PAGES + 1):
        listing_url = HEMAT_CATEGORY_URL
        if page_number > 1:
            listing_url += f"?page={page_number}"

        say(f"[DAFTAR {page_number}/{MAX_LISTING_PAGES}] {listing_url}")
        record = {
            "page": page_number,
            "url": listing_url,
            "status": "unknown",
            "links_seen": 0,
            "promo_articles_found": 0,
            "error": None,
        }

        try:
            response = session.get(
                listing_url,
                timeout=TIMEOUT,
                allow_redirects=True,
            )
            if response.status_code in (401, 403, 429):
                record["status"] = "blocked_or_limited"
                record["error"] = (
                    f"HTTP {response.status_code}; tidak mencoba melewati proteksi."
                )
                listing_log.append(record)
                say(f"  ! {record['error']}")
                break

            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            links = soup.select('a[href*="/news/"]')
            record["links_seen"] = len(links)
            record["status"] = "ok"

            for anchor in links:
                absolute_url = urljoin(response.url, anchor.get("href", ""))
                article_url = clean_article_url(absolute_url)
                if not article_url or article_url in seen_urls:
                    continue

                title = get_anchor_title(anchor)
                if not is_relevant_title(title):
                    continue

                seen_urls.add(article_url)
                start, end = parse_period_from_text(title)
                title_status = promo_status(start, end)
                status_rank = {
                    "active": 4,
                    "upcoming": 3,
                    "unknown": 2,
                    "expired": 1,
                }.get(title_status, 0)

                candidates.append({
                    "title_from_listing": title,
                    "url": article_url,
                    "discovery_source": "Momsmoney Hemat listing",
                    "query_term": f"halaman_{page_number}",
                    "title_status_hint": title_status,
                    "title_status_rank": status_rank,
                    "title_period_end_hint": end,
                })
                record["promo_articles_found"] += 1

            listing_log.append(record)
            say(
                f"  -> {record['links_seen']} tautan; "
                f"{record['promo_articles_found']} artikel promo"
            )
        except Exception as exc:
            record["status"] = "error"
            record["error"] = f"{type(exc).__name__}: {exc}"
            listing_log.append(record)
            say(f"  ! Gagal membaca daftar: {exc}")

        time.sleep(DELAY)

    # Promo yang tampak aktif dari judul diproses lebih dahulu, tetapi status
    # final tetap dihitung ulang dari halaman artikel yang sebenarnya.
    candidates.sort(
        key=lambda item: (
            item.get("title_status_rank", 0),
            item.get("title_period_end_hint") or "",
            item.get("url") or "",
        ),
        reverse=True,
    )
    return candidates, listing_log


# =========================================================
# EKSTRAKSI BARIS PRODUK / PENAWARAN
# =========================================================


def get_leaf_text_lines(node) -> list[str]:
    """Pisahkan penawaran yang dipisah <br> tanpa kehilangan spasi antar elemen."""
    fragment = BeautifulSoup(str(node), "html.parser")
    sentinel = "__PROMO_HTML_LINE_BREAK_7F3A__"
    for br in fragment.find_all("br"):
        br.replace_with(f" {sentinel} ")

    text = fragment.get_text(" ", strip=True)
    text = re.sub(rf"\s*{re.escape(sentinel)}\s*", "\n", text)
    lines = []
    for line in re.split(r"[\r\n]+", text):
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return lines


def split_product_offer(line: str) -> tuple[str | None, str | None]:
    """Pisahkan nama produk dengan teks promo; baris editorial diabaikan."""
    text = re.sub(r"\s+", " ", line).strip()
    delimiter_pattern = re.compile(r":|[–—]")
    offer_start = re.compile(
        r"^(?:Rp\.?\s*\d|harga\b|promo\b|diskon\b|beli\s+\d|"
        r"gratis\b|tebus\s+murah\b|cashback\b|voucher\b|"
        r"potongan\b|tambah\s+Rp\b)",
        re.IGNORECASE,
    )

    for match in delimiter_pattern.finditer(text):
        name_candidate = text[:match.start()].strip()
        offer_candidate = text[match.end():].strip()
        if not name_candidate or not offer_candidate:
            continue
        if len(name_candidate) > 140 or len(name_candidate.split()) > 20:
            continue
        if offer_start.match(offer_candidate):
            return name_candidate, offer_candidate
    return None, None


def parse_product_line(
    raw_line: str,
    section: str | None,
    source_url: str,
) -> dict | None:
    line = re.sub(r"\s+", " ", raw_line).strip()
    line = re.sub(r"^[\-*\u2022]+\s*", "", line)

    if len(line) < 8 or len(line) > 360 or NOISE_PREFIX.search(line):
        return None

    product_name, offer_text = split_product_offer(line)
    if not product_name or not offer_text or not OFFER_PATTERN.search(offer_text):
        return None

    payment_prefix = re.compile(
        r"^(?:khusus pembayaran\b|pembayaran\b|minimal belanja\b|"
        r"berlaku untuk\b|maksimal\b|tidak berlaku\b|kuota\b|diskon aplikasi\b)",
        re.IGNORECASE,
    )
    if payment_prefix.match(product_name):
        return None

    prices = rupiah_numbers(offer_text)
    normal_price = labeled_price(offer_text, r"harga\s+normal|normal")
    promo_price = labeled_price(
        offer_text,
        r"harga\s+promo|harga\s+spesial|harga\s+setelah\s+diskon|promo",
    )

    quality_flags: list[str] = []
    price_method = "not_extracted"
    price_basis = "no_single_price"

    # "Tambah Rp5.000 dapat 2 pcs" bukan harga satu unit.
    additional_payment = re.search(r"\btambah\s+Rp\.?\s*[0-9]", offer_text, re.I)
    if additional_payment:
        add_match = re.search(r"\btambah\s+(Rp\.?\s*[0-9][0-9.,]*)", offer_text, re.I)
        promo_price = None
        price_method = "additional_payment_not_unit_price"
        price_basis = "additional_payment_amount"
        if add_match:
            quality_flags.append("additional_payment_amount_not_unit_price")

    normal_unit_context = re.search(
        r"(?:harga\s+normal|normal)\s*[:\-]?\s*"
        r"Rp\.?\s*[0-9][0-9.,]*([^,;)]{0,40})",
        offer_text,
        flags=re.IGNORECASE,
    )
    if (
        normal_price is not None
        and normal_unit_context
        and re.search(
            r"/\s*(?:potongan|pcs|pc|item|2pcs|per)|potongan",
            normal_unit_context.group(1),
            flags=re.IGNORECASE,
        )
    ):
        normal_price = None
        quality_flags.append("normal_price_ambiguous_unit")

    # Lepas label awal agar pola seperti "Promo: Beli 3 Gratis 1"
    # tetap dikenali sebagai penawaran bundle.
    offer_terms = re.sub(
        r"^(?:(?:harga\s+)?promo|diskon|penawaran)\s*[:\-]?\s*",
        "",
        offer_text,
        flags=re.IGNORECASE,
    )
    purchase_match = re.match(
        r"^(beli\s+\d+\s+gratis\s+\d+)",
        offer_terms,
        flags=re.IGNORECASE,
    )
    bundle_match = re.match(r"^beli\s+\d+\b", offer_terms, re.IGNORECASE)

    if not additional_payment:
        if promo_price is not None:
            price_method = "explicit_promo_label"
            price_basis = "bundle_offer_amount" if bundle_match else "offer_price"
        elif prices and bundle_match:
            promo_price = prices[0]
            price_method = "explicit_bundle_offer_amount"
            price_basis = "bundle_offer_amount"
        elif prices and re.match(r"^Rp\.?\s*\d", offer_text, re.IGNORECASE):
            promo_price = prices[0]
            price_method = "offer_amount_without_promo_label"
            price_basis = "offer_price"
        elif prices and normal_price is not None:
            alternatives = [value for value in prices if value != normal_price]
            if alternatives:
                promo_price = alternatives[0]
                price_method = "inferred_from_multiple_prices"
                price_basis = "offer_price"
                quality_flags.append("price_inferred")
        elif prices:
            promo_price = prices[0]
            price_method = "first_offer_amount_needs_verification"
            price_basis = "unverified_offer_amount"
            quality_flags.append("price_inferred")
        elif purchase_match:
            price_method = "no_single_price_bundle_offer"
            price_basis = "non_numeric_bundle_offer"

    if (
        promo_price is not None
        and normal_price is not None
        and promo_price > normal_price
    ):
        quality_flags.append("promo_above_normal_review")

    deal_match = re.search(
        r"\bbeli\s*\d+\s*gratis\s*\d+|"
        r"\bbeli\s*\d+\s*hemat|\btebus\s+murah\b|"
        r"\bbuy\s*1\s*get\s*1\b",
        offer_text,
        flags=re.IGNORECASE,
    )

    purchase_condition = (
        purchase_match.group(1) if purchase_match
        else (bundle_match.group(0) if bundle_match else None)
    )
    deal_type = deal_match.group(0) if deal_match else None
    discount_info = discount_fields(
        offer_text=offer_text,
        promo_price=promo_price,
        normal_price=normal_price,
        price_basis=price_basis,
        purchase_condition=purchase_condition,
        deal_type=deal_type,
        quality_flags=quality_flags,
    )

    return {
        "product_text": product_name,
        "promo_price": promo_price,
        "normal_price": normal_price,
        "price_parse_method": price_method,
        "price_basis": price_basis,
        "purchase_condition": purchase_condition,
        "price_quality_flag": " | ".join(dict.fromkeys(quality_flags)) if quality_flags else None,
        **discount_info,
        "offer_text": offer_text,
        "deal_type": deal_type,
        "section": section,
        "source_url": source_url,
        "raw_line": line,
    }


def article_container(soup: BeautifulSoup):
    """Pilih badan artikel dan buang sidebar/rekomendasi."""
    selectors = [
        ".detail__body",
        ".detail__body-text",
        ".detail-body",
        ".detail-content",
        "[class*='article-content']",
        "article",
        "main",
    ]
    container = None
    for selector in selectors:
        candidate = soup.select_one(selector)
        if candidate and len(candidate.get_text(" ", strip=True)) > 200:
            container = candidate
            break
    if container is None:
        container = soup.body or soup

    for unwanted in container.select(
        "aside, [class*='related-article'], [class*='related-news'], "
        "[class*='recommendation'], [class*='most-read'], "
        "[class*='popular-news'], [class*='social-share'], [class*='breadcrumb']"
    ):
        unwanted.decompose()
    return container


def extract_product_rows(container, source_url: str) -> list[dict]:
    rows = []
    seen = set()
    section = None

    for node in container.find_all(["h2", "h3", "h4", "li", "p"]):
        if node.name in {"h2", "h3", "h4"}:
            heading = node.get_text(" ", strip=True)
            if heading:
                section = heading[:150]
            continue

        # Jika membungkus p/li lain, proses node anak untuk menghindari duplikasi.
        if node.find(["li", "p"]):
            continue

        for line in get_leaf_text_lines(node):
            parsed = parse_product_line(line, section, source_url)
            if not parsed:
                continue
            key = (
                normalize_text(parsed["product_text"]),
                normalize_text(parsed["offer_text"]),
                parsed.get("promo_price"),
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(parsed)
    return rows


# =========================================================
# SCRAPE SATU ARTIKEL
# =========================================================


def fetch_article(session: requests.Session, candidate: dict) -> dict:
    url = candidate["url"]
    result = {
        "requested_url": url,
        "source_url": None,
        "discovery_source": candidate.get("discovery_source"),
        "query_term": candidate.get("query_term"),
        "title": None,
        "merchant_name": None,
        "source_type": "secondary",
        "source_name": "Momsmoney / Kontan",
        "campaign_type": "unknown",
        "published_at": None,
        "valid_from": None,
        "valid_until": None,
        "period_precision": "unknown",
        "period_flags": None,
        "matched_period_text": None,
        "campaign_discount_max_percent": None,
        "promo_status": "unknown",
        "status": "unknown",
        "product_count": 0,
        "products": [],
        "note": None,
    }

    try:
        response = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        if response.status_code in (401, 403, 429):
            result["status"] = "blocked_or_limited"
            result["note"] = "Akses ditolak/dibatasi; proteksi tidak dicoba dilewati."
            return result
        response.raise_for_status()

        final_url = clean_article_url(response.url)
        if not final_url:
            result["status"] = "invalid_source_url"
            result["note"] = "URL akhir bukan halaman artikel Momsmoney yang dikenali."
            return result

        soup = BeautifulSoup(response.text, "html.parser")
        h1 = soup.find("h1")
        title = h1.get_text(" ", strip=True) if h1 else ""
        result["title"] = title
        result["source_url"] = final_url

        if not is_relevant_title(title):
            result["status"] = "irrelevant_article"
            result["note"] = "Judul halaman tidak memenuhi pola artikel promo."
            return result

        result["merchant_name"] = infer_merchant(title)
        result["campaign_type"] = campaign_type(title)

        published_meta = (
            soup.find("meta", attrs={"property": "article:published_time"})
            or soup.find("meta", attrs={"name": "pubdate"})
        )
        if published_meta:
            result["published_at"] = (
                published_meta.get("content") or published_meta.get("datetime")
            )
        if not result["published_at"]:
            time_node = soup.find("time")
            if time_node:
                result["published_at"] = (
                    time_node.get("datetime")
                    or time_node.get_text(" ", strip=True)
                )

        container = article_container(soup)
        lead_paragraphs = [
            p.get_text(" ", strip=True)
            for p in container.find_all("p")
            if p.get_text(" ", strip=True)
        ]
        lead_text = "\n".join(lead_paragraphs[:10])[:4500]

        period_info = extract_period_details(title, lead_text, result.get("published_at"))
        start = period_info["valid_from"]
        end = period_info["valid_until"]
        result["valid_from"] = start
        result["valid_until"] = end
        result["period_precision"] = period_info["period_precision"]
        result["period_flags"] = period_info["period_flags"]
        result["matched_period_text"] = period_info["matched_period_text"]
        result["promo_status"] = promo_status(start, end, period_info["period_precision"])
        campaign_discount, campaign_discount_type = parse_explicit_discount(title)
        result["campaign_discount_max_percent"] = campaign_discount if campaign_discount_type == "explicit_max" else None

        products = extract_product_rows(container, final_url)
        for product in products:
            product["campaign_title"] = title
            product["merchant_name"] = result["merchant_name"]
            product["campaign_type"] = result["campaign_type"]
            product["valid_from"] = start
            product["valid_until"] = end
            product["period_precision"] = period_info["period_precision"]
            product["period_flags"] = period_info["period_flags"]
            product["published_at"] = result.get("published_at")
            product["campaign_discount_max_percent"] = result["campaign_discount_max_percent"]
            product["promo_status"] = result["promo_status"]

        result["products"] = products
        result["product_count"] = len(products)
        result["status"] = "ok"
        if result["promo_status"] == "unknown":
            result["note"] = (
                "Tanggal mulai/akhir promo tidak dapat dipastikan; "
                "artikel tidak dimasukkan ke katalog aktif."
            )
    except Exception as exc:
        result["status"] = "error"
        result["note"] = f"{type(exc).__name__}: {exc}"

    return result


# =========================================================
# VALIDASI DAN EKSPOR
# =========================================================


def _dedupe_text(value) -> str:
    text = normalize_text(value)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def deduplicate_products(rows: list[dict]) -> list[dict]:
    """Satukan exact offer lintas artikel tanpa membuang provenance sumber."""
    grouped: OrderedDict[tuple, list[dict]] = OrderedDict()
    for row in rows:
        key = (
            _dedupe_text(row.get("merchant_name")),
            _dedupe_text(row.get("product_text")),
            _dedupe_text(row.get("offer_text")),
            str(row.get("promo_price") or ""),
            str(row.get("normal_price") or ""),
            str(row.get("valid_from") or ""),
            str(row.get("valid_until") or ""),
            _dedupe_text(row.get("purchase_condition")),
            _dedupe_text(row.get("deal_type")),
        )
        grouped.setdefault(key, []).append(row)

    output = []
    for group in grouped.values():
        # Use the best populated row; preserve each source/title separately.
        def quality(row):
            return sum(bool(row.get(k)) for k in (
                "merchant_name", "published_at", "period_precision", "promo_price",
                "normal_price", "discount_percent", "price_parse_method", "price_quality_flag",
            ))
        best = dict(max(group, key=quality))
        urls = list(OrderedDict.fromkeys(r.get("source_url", "") for r in group if r.get("source_url")))
        titles = list(OrderedDict.fromkeys(r.get("campaign_title", "") for r in group if r.get("campaign_title")))
        best["source_urls"] = " | ".join(urls)
        best["source_campaign_titles"] = " | ".join(titles)
        best["duplicate_count"] = len(group)
        best["duplicate_group_id"] = hashlib.sha1(repr(key).encode("utf-8")).hexdigest()[:12]
        best["overlapping_offer_count"] = 1
        best["overlap_period_flag"] = None
        if len(group) > 1:
            old_flag = best.get("price_quality_flag") or ""
            flag = "exact_duplicate_collapsed_across_articles"
            best["price_quality_flag"] = " | ".join(x for x in (old_flag, flag) if x)
        output.append(best)
    return output


def consolidate_overlapping_products(rows: list[dict]) -> list[dict]:
    """Gabungkan penawaran identik dengan interval tanggal pasti yang saling overlap.

    Baris month/until (tanggal estimasi) tidak digabung dengan periode pasti.
    Provenance tiap artikel dipertahankan di source_urls/source_campaign_titles.
    """
    grouped: OrderedDict[tuple, list[tuple[date, date, dict]]] = OrderedDict()
    passthrough = []
    for row in rows:
        if row.get("period_precision") not in {"range", "single_day"}:
            passthrough.append(dict(row))
            continue
        try:
            start = date.fromisoformat(row["valid_from"])
            end = date.fromisoformat(row["valid_until"])
        except (ValueError, TypeError, KeyError):
            passthrough.append(dict(row))
            continue
        key = (
            _dedupe_text(row.get("merchant_name")),
            _dedupe_text(row.get("product_text")),
            _dedupe_text(row.get("offer_text")),
            str(row.get("promo_price") or ""),
            str(row.get("normal_price") or ""),
            _dedupe_text(row.get("purchase_condition")),
            _dedupe_text(row.get("deal_type")),
        )
        grouped.setdefault(key, []).append((start, end, dict(row)))

    output = list(passthrough)
    for key, items in grouped.items():
        items.sort(key=lambda item: (item[0], item[1]))
        intervals = []
        for start, end, row in items:
            if intervals and start <= intervals[-1]["end"]:
                intervals[-1]["end"] = max(intervals[-1]["end"], end)
                intervals[-1]["members"].append(row)
            else:
                intervals.append({"start": start, "end": end, "members": [row]})

        for interval in intervals:
            members = interval["members"]
            target_start, target_end = interval["start"], interval["end"]

            matching = []
            for member in members:
                try:
                    if (date.fromisoformat(member["valid_from"]) == target_start
                            and date.fromisoformat(member["valid_until"]) == target_end):
                        matching.append(member)
                except (ValueError, TypeError, KeyError):
                    pass
            candidates = matching or members

            def score(row):
                return sum(bool(row.get(field)) for field in (
                    "published_at", "promo_price", "normal_price", "discount_percent",
                    "price_parse_method", "source_url", "merchant_name", "campaign_title",
                ))

            best = dict(max(candidates, key=score))
            urls = list(OrderedDict.fromkeys(
                [r.get("source_url") for r in members if r.get("source_url")]
                + [u for r in members for u in (r.get("source_urls", "").split(" | ") if r.get("source_urls") else []) if u]
            ))
            titles = list(OrderedDict.fromkeys(
                [r.get("campaign_title") for r in members if r.get("campaign_title")]
                + [t for r in members for t in (r.get("source_campaign_titles", "").split(" | ") if r.get("source_campaign_titles") else []) if t]
            ))
            best["source_urls"] = " | ".join(urls)
            best["source_campaign_titles"] = " | ".join(titles)
            best["source_url"] = best.get("source_url") or (urls[0] if urls else None)
            best["valid_from"] = target_start.isoformat()
            best["valid_until"] = target_end.isoformat()
            best["period_precision"] = "single_day" if target_start == target_end else "range"
            best["promo_status"] = promo_status(best["valid_from"], best["valid_until"], best["period_precision"])
            best["duplicate_count"] = sum(int(r.get("duplicate_count") or 1) for r in members)
            best["overlapping_offer_count"] = len(members)
            best["duplicate_group_id"] = hashlib.sha1(repr(key).encode("utf-8")).hexdigest()[:12]
            best["overlap_period_flag"] = None
            if len(members) > 1:
                existing_flags = []
                price_flags = []
                for member in members:
                    existing_flags.extend((member.get("period_flags") or "").split(" | "))
                    price_flags.extend((member.get("price_quality_flag") or "").split(" | "))
                existing_flags.append("same_offer_overlapping_periods_consolidated")
                best["period_flags"] = " | ".join(dict.fromkeys(x for x in existing_flags if x))
                best["price_quality_flag"] = " | ".join(dict.fromkeys(x for x in price_flags if x)) or None
                campaign_claims = [r.get("campaign_discount_max_percent") for r in members if r.get("campaign_discount_max_percent") is not None]
                if campaign_claims:
                    best["campaign_discount_max_percent"] = max(campaign_claims)
                best["overlap_period_flag"] = "merged_same_offer_overlapping_periods"
            output.append(best)

    output.sort(key=lambda row: (
        row.get("merchant_name") or "",
        row.get("campaign_title") or "",
        row.get("product_text") or "",
        row.get("valid_from") or "",
    ))
    return output


def write_review_csv(path: Path, rows: list[dict]) -> None:
    """Ekspor kandidat dengan periode estimasi agar tidak tercampur katalog pasti."""
    write_csv(path, rows)


def flatten_products(articles: list[dict]) -> list[dict]:
    rows = []
    for article in articles:
        rows.extend(article.get("products", []))
    return rows


def save_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = [
        "product_text", "merchant_name", "campaign_title", "campaign_type",
        "promo_status", "valid_from", "valid_until", "period_precision", "period_flags",
        "promo_price", "normal_price", "discount_percent", "discount_percent_basis",
        "discount_percent_confidence", "discount_percent_calculated", "discount_savings_rp",
        "estimated_bundle_discount_percent", "campaign_discount_max_percent",
        "price_parse_method", "price_basis", "purchase_condition", "price_quality_flag",
        "deal_type", "offer_text", "section", "published_at", "source_url",
        "source_urls", "source_campaign_titles", "duplicate_count", "raw_line",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fields,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def validate_run(active_articles: list[dict], active_products: list[dict]) -> dict:
    valid_products = [
        row for row in active_products
        if row.get("product_text") and row.get("source_url")
        and row.get("promo_status") == "active"
    ]
    reasons = []

    if len(active_articles) < MIN_ACTIVE_ARTICLES:
        reasons.append(
            f"Artikel promo aktif valid hanya {len(active_articles)}; "
            f"minimal {MIN_ACTIVE_ARTICLES}."
        )
    if len(valid_products) < MIN_ACTIVE_PRODUCT_ROWS:
        reasons.append(
            f"Baris produk promo aktif hanya {len(valid_products)}; "
            f"minimal {MIN_ACTIVE_PRODUCT_ROWS}."
        )

    return {
        "valid": not reasons,
        "active_articles": len(active_articles),
        "active_product_rows": len(valid_products),
        "reasons": reasons,
    }


# =========================================================
# MAIN
# =========================================================


def main() -> None:
    INTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131 Safari/537.36"
        )
    })

    try:
        candidates, listing_log = discover_articles(session)

        # URL manual opsional untuk artikel promo yang belum muncul di listing.
        for url in read_manual_urls():
            if all(item["url"] != url for item in candidates):
                candidates.append({
                    "title_from_listing": "",
                    "url": url,
                    "discovery_source": "manual_url",
                    "query_term": "manual",
                    "title_status_hint": "unknown",
                    "title_status_rank": 0,
                })

        selected = candidates[:MAX_ARTICLES_TO_FETCH]
        say(f"\nArtikel promo dipilih untuk diperiksa: {len(selected)}")
        articles_checked = []

        for index, candidate in enumerate(selected, start=1):
            say(f"\n[ARTIKEL {index}/{len(selected)}] {candidate['url']}")
            item = fetch_article(session, candidate)
            articles_checked.append(item)
            say(
                f"  -> {item['status']} | merchant={item.get('merchant_name') or 'unknown'} | "
                f"produk={item.get('product_count', 0)} | "
                f"periode={item.get('valid_from') or '?'} s.d. "
                f"{item.get('valid_until') or '?'} | {item.get('promo_status')}"
            )
            if item.get("note"):
                say(f"  Catatan: {item['note']}")
            time.sleep(DELAY)

        # Katalog final sangat ketat: hanya artikel yang berhasil dibaca dan
        # mempunyai tanggal promo yang secara eksplisit aktif hari ini.
        active_articles = [
            item for item in articles_checked
            if item.get("status") == "ok" and item.get("promo_status") == "active"
        ]
        estimated_articles = [
            item for item in articles_checked
            if item.get("status") == "ok" and item.get("promo_status") == "active_estimated"
        ]
        active_products_raw = flatten_products(active_articles)
        active_products_exact = deduplicate_products(active_products_raw)
        active_products = consolidate_overlapping_products(active_products_exact)
        estimated_products = deduplicate_products(flatten_products(estimated_articles))
        review_candidates = list(flatten_products(estimated_articles))
        for row in active_products_raw:
            if row.get("period_flags") or row.get("price_quality_flag") or row.get("discount_percent_confidence") == "low":
                review_candidates.append(row)
        review_products = consolidate_overlapping_products(deduplicate_products(review_candidates))
        all_products_seen = flatten_products(articles_checked)

        validation = validate_run(active_articles, active_products)
        summary = {
            "checked_at": timestamp(),
            "reference_date": TODAY.isoformat(),
            "module": "All merchants - active promo collector",
            "source_type": "secondary_public_articles",
            "listing_url": HEMAT_CATEGORY_URL,
            "listing_pages_checked": len(listing_log),
            "candidate_articles_found": len(candidates),
            "articles_checked": len(articles_checked),
            "successful_articles": sum(a.get("status") == "ok" for a in articles_checked),
            "active_articles": len(active_articles),
            "active_estimated_articles": len(estimated_articles),
            "upcoming_articles": sum(a.get("promo_status") in {"upcoming", "upcoming_estimated"} for a in articles_checked),
            "expired_articles": sum(a.get("promo_status") == "expired" for a in articles_checked),
            "unknown_period_articles": sum(a.get("promo_status") == "unknown" for a in articles_checked),
            "products_from_all_checked_articles": len(all_products_seen),
            "active_product_rows_before_dedup": len(active_products_raw),
            "active_product_rows_after_exact_dedup": len(active_products_exact),
            "active_product_rows": len(active_products),
            "active_overlapping_offer_rows_absorbed": len(active_products_exact) - len(active_products),
            "active_estimated_product_rows": len(estimated_products),
            "rows_with_price_quality_flags": sum(
                bool(row.get("price_quality_flag")) for row in active_products
            ),
        }

        # File attempt menyimpan audit lengkap, termasuk artikel expired/unknown.
        attempt_payload = {
            **summary,
            "validation": validation,
            "listing_search_log": listing_log,
            "articles_checked": articles_checked,
            "active_articles": active_articles,
            "active_products": active_products,
            "active_estimated_articles": estimated_articles,
            "active_estimated_products": estimated_products,
        }
        save_json(ATTEMPT_JSON, attempt_payload)

        if validation["valid"]:
            catalog_payload = {
                **summary,
                "catalog_scope": "only_explicitly_active_promotions",
                "validation": validation,
                "listing_search_log": listing_log,
                "articles": active_articles,
                "products": active_products,
            }
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            history_file = HISTORY_DIR / (
                datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y%m%d_%H%M%S")
                + ".json"
            )
            save_json(history_file, catalog_payload)
            save_json(OUT_JSON, catalog_payload)
            write_csv(OUT_CSV, active_products)
            review_csv = INTERNAL_DIR / "promo_catalog_all_merchants_review.csv"
            write_review_csv(review_csv, review_products)
            output_status = "updated"
        else:
            output_status = "preserved_previous_catalog"

        run_log = {
            **summary,
            "output_status": output_status,
            "validation": validation,
            "listing_search_log": listing_log,
            "articles": [
                {
                    "title": item.get("title"),
                    "source_url": item.get("source_url"),
                    "merchant_name": item.get("merchant_name"),
                    "status": item.get("status"),
                    "promo_status": item.get("promo_status"),
                    "valid_from": item.get("valid_from"),
                    "valid_until": item.get("valid_until"),
                    "period_precision": item.get("period_precision"),
                    "period_flags": item.get("period_flags"),
                    "product_count": item.get("product_count"),
                    "note": item.get("note"),
                }
                for item in articles_checked
            ],
        }
        save_json(RUN_LOG, run_log)

        elapsed = time.monotonic() - started
        say("\n" + "=" * 60)
        say("SELESAI - ALL MERCHANTS ACTIVE PROMO COLLECTOR")
        say("=" * 60)
        say(f"Tanggal acuan            : {TODAY.isoformat()}")
        say(f"Artikel kandidat         : {len(candidates)}")
        say(f"Artikel diperiksa        : {len(articles_checked)}")
        say(f"Artikel sukses            : {summary['successful_articles']}")
        say(f"Artikel promo aktif (pasti): {len(active_articles)}")
        say(f"Artikel aktif (estimasi)  : {len(estimated_articles)}")
        say(f"Artikel promo mendatang  : {summary['upcoming_articles']}")
        say(f"Artikel kedaluwarsa      : {summary['expired_articles']}")
        say(f"Periode tidak diketahui  : {summary['unknown_period_articles']}")
        say(f"Baris produk aktif mentah       : {len(active_products_raw)}")
        say(f"Setelah exact dedup             : {len(active_products_exact)}")
        say(f"Baris produk aktif terkonsolidasi: {len(active_products)}")
        say(f"Baris aktif estimasi            : {len(estimated_products)}")
        say(f"Baris yang perlu ditinjau        : {len(review_products)}")
        say(f"Validasi                 : {validation['valid']}")
        say(f"Status katalog           : {output_status}")
        say(f"Waktu                    : {elapsed:.1f} detik")
        say(f"JSON katalog             : {OUT_JSON}")
        say(f"CSV katalog              : {OUT_CSV}")
        say(f"CSV periode estimasi     : {INTERNAL_DIR / 'promo_catalog_all_merchants_review.csv'}")
        say(f"Percobaan terbaru        : {ATTEMPT_JSON}")
        say(f"Run log                  : {RUN_LOG}")
        if validation["reasons"]:
            say("Katalog tidak diperbarui:")
            for reason in validation["reasons"]:
                say(f"- {reason}")
        say("=" * 60)
    finally:
        session.close()


if __name__ == "__main__":
    main()
