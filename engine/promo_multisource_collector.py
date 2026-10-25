from __future__ import annotations

"""Multi-source promo collector for public Indonesian retail-promo pages.

Sources:
- Hemat.id: structured product cards / listing pages
- Katalogpromosi.com: promo article text and item lists
- Scanharga.com: catalog/article discovery, textual offers, and catalog-image URLs

This script only requests public pages, uses low request rates, and does not try to
bypass access restrictions. It does not claim aggregator data is officially verified.
"""

import argparse
import csv
import hashlib
import json
import re
import time
from collections import OrderedDict, Counter
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from promo_names import clean_product_name, name_variants
from promo_merchant_utils import infer_merchant_candidate_from_title, infer_merchant_candidate_from_url, is_dynamic_merchant_label
from promo_product_validation import product_candidate_rejection_reason, product_candidate_review_reason
from promo_evidence import find_amount_evidence, find_discount_evidence, extract_period_evidence
from promo_categories import classify_promo_category

ENGINE_DIR = Path(__file__).resolve().parent
ROOT_DIR = ENGINE_DIR.parent
INTERNAL_DIR = ROOT_DIR / "_internal"
OUTPUT_DIR = ROOT_DIR / "hasil"
TZ = ZoneInfo("Asia/Jakarta")
TODAY = datetime.now(TZ).date()

# Keep request volume intentionally moderate. Increase these only if the sites allow it.
TIMEOUT = 15
DELAY_SECONDS = 0.8
MAX_LISTING_PAGES_PER_SOURCE = 12
MAX_ARTICLES_PER_SOURCE = 30
MAX_IMAGES_PER_ARTICLE = 20

OUT_ALL = INTERNAL_DIR / "promo_multisource_products_all.csv"
OUT_ACTIVE = INTERNAL_DIR / "promo_multisource_products_active.csv"
OUT_REVIEW = INTERNAL_DIR / "promo_multisource_products_review.csv"
OUT_ARTICLES = INTERNAL_DIR / "promo_multisource_articles.csv"
OUT_IMAGES = INTERNAL_DIR / "promo_multisource_catalog_images.csv"
OUT_LOG = INTERNAL_DIR / "promo_multisource_runlog.json"
OUT_REJECTED_CANDIDATES = INTERNAL_DIR / "promo_multisource_rejected_candidates.csv"
REJECTED_PRODUCT_CANDIDATES: list[dict] = []

SOURCE_SEEDS = {
    "Hemat.id": [
        "https://www.hemat.id/katalog-alfamart/",
        "https://www.hemat.id/katalog-alfamart,indomaret/promo-promo/",
    ],
    "Katalogpromosi.com": [
        "https://katalogpromosi.com/",
        "https://katalogpromosi.com/katalog-belanja/",
    ],
    "Scanharga.com": [
        "https://www.scanharga.com/",
        "https://www.scanharga.com/search/label/Indomaret",
        "https://www.scanharga.com/search/label/Alfamart",
        "https://www.scanharga.com/search/label/Superindo",
        "https://www.scanharga.com/search/label/Hypermart",
        "https://www.scanharga.com/search/label/Alfamidi",
        "https://www.scanharga.com/search/label/Swalayan",
    ],
}

ALIAS_GROUPS = [
    ("Super Indo", ("super indo", "superindo")),
    ("Hari Hari Swalayan", ("hari hari swalayan", "hari hari")),
    ("Farmers Market", ("farmers market",)),
    ("Ranch Market", ("ranch market",)),
    ("Lotte Mart", ("lotte mart", "lottemart")),
    ("Hypermart", ("hypermart",)),
    ("Alfamidi", ("alfamidi",)),
    ("Alfamart", ("alfamart", "alfagift")),
    ("Indomaret", ("indomaret", "klik indomaret")),
    ("Yogya", ("toserba yogya", "yomart", "yogya")),
    ("Tip Top", ("tip top",)),
    ("Transmart", ("transmart",)),
    ("Carrefour", ("carrefour",)),
    ("Guardian", ("guardian",)),
    ("Watsons", ("watsons",)),
    ("Foodhall", ("the foodhall", "foodhall")),
    ("AEON", ("aeon",)),
    ("Hero", ("hero supermarket",)),
    ("Indogrosir", ("indogrosir",)),
    ("Lulu Hypermarket", ("lulu hypermarket",)),
    ("Popeyes", ("popeyes",)),
    ("HokBen", ("hokben",)),
    ("KFC", ("kfc",)),
    ("McDonald's", ("mcdonald's", "mcdonalds", "mcd")),
    ("Pizza Hut", ("pizza hut",)),
    ("Burger King", ("burger king",)),
    ("Domino's Pizza", ("domino's pizza", "domino pizza", "domino's")),
    ("J.CO", ("j.co", "jco donuts", "jco")),
    ("Starbucks", ("starbucks",)),
    ("Chatime", ("chatime",)),
    ("Richeese Factory", ("richeese factory", "richeese")),
    ("Yoshinoya", ("yoshinoya",)),
    ("Subway", ("subway",)),
]

MONTHS = {
    "januari": 1, "jan": 1,
    "februari": 2, "feb": 2,
    "maret": 3, "mar": 3,
    "april": 4, "apr": 4,
    "mei": 5,
    "juni": 6, "jun": 6,
    "juli": 7, "jul": 7,
    "agustus": 8, "agu": 8, "ags": 8, "agst": 8,
    "september": 9, "sept": 9, "sep": 9,
    "oktober": 10, "okt": 10,
    "november": 11, "nov": 11,
    "desember": 12, "des": 12,
}
MONTH_PATTERN = "|".join(sorted(MONTHS, key=len, reverse=True))
DATE_SEP = r"(?:-|–|—|sampai|hingga|s\.d\.|s/d|sd)"
PROMO_WORDS = re.compile(
    r"promo|promosi|diskon|katalog|jsm|psm|hemat|gratis|tebus murah|"
    r"harga spesial|harga promo|weekend|weekday|cashback|voucher|kupon|"
    r"buy\s*1|beli\s+\d|sale|potongan harga",
    re.IGNORECASE,
)
PRICE_SIGNAL = re.compile(
    r"harga\s+promo|harga\s+normal|diskon\s*\d|\bRp\.?\s*\d|"
    r"beli\s+\d|gratis\s+\d|tebus\s+murah|cashback|voucher|kupon",
    re.IGNORECASE,
)
NOISE_START = re.compile(
    r"^(?:syarat(?:\s+dan\s+ketentuan)?|s&k\b|ketentuan\b|berlaku\b|"
    r"promo berlaku\b|periode promo\b|tanggal promo\b|cek juga\b|"
    r"jangan lewatkan\b|yuk\b|ayo\b|buruan\b|info lengkap\b|"
    r"ikuti\b|sumber\b|catatan\b|minimal belanja\b|limit maks\b|"
    r"khusus pembayaran\b|tidak berlaku\b|tersedia nasional\b)",
    re.IGNORECASE,
)

PRODUCT_FIELDS = [
    "product_text", "original_product_text", "product_name_variants", "product_category", "product_subcategory", "normal_price", "promo_price", "discount_percent",
    "merchant_name", "discount_savings_rp", "discount_note", "deal_type",
    "purchase_condition", "offer_text", "valid_from", "valid_until",
    "period_precision", "promo_status", "campaign_title", "campaign_type", "published_at",
    "source_name", "source_type", "verification_status", "source_url",
    "source_page_url", "source_item_url", "region_or_channel", "scraped_at",
    "price_parse_method", "price_basis", "discount_basis", "price_quality_flag",
    "promo_price_evidence_text", "normal_price_evidence_text", "discount_evidence_text",
    "period_evidence_text", "period_evidence_source",
    "raw_line", "retained_from_previous_run", "product_validation_status", "product_review_reason",
]
ACTIVE_FIELDS = PRODUCT_FIELDS
REVIEW_FIELDS = PRODUCT_FIELDS + ["review_reason"]
ARTICLE_FIELDS = [
    "source_name", "title", "source_url", "published_at", "merchant_name",
    "valid_from", "valid_until", "period_precision", "promo_status",
    "product_rows_extracted", "image_count", "scrape_status", "note", "scraped_at",
]
IMAGE_FIELDS = [
    "source_name", "campaign_title", "article_url", "image_url", "image_alt",
    "period_status", "scraped_at",
]


def say(message: str) -> None:
    print(message, flush=True)


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize(value: object) -> str:
    text = clean_text(value).casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def safe_url(url: str) -> str:
    if not url:
        return ""
    url, _ = urldefrag(url.strip())
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""
    return parsed._replace(fragment="").geturl()


def same_site(url: str, source_name: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if source_name == "Hemat.id":
        return host in {"hemat.id", "www.hemat.id"}
    if source_name == "Katalogpromosi.com":
        return host in {"katalogpromosi.com", "www.katalogpromosi.com"}
    if source_name == "Scanharga.com":
        return host in {"scanharga.com", "www.scanharga.com"}
    return False


def parse_idr(value: object) -> int | None:
    """Parse Indonesian Rupiah: '129.900' -> 129900, 'Rp 1.299.000' -> 1299000."""
    if value is None:
        return None
    text = clean_text(value)
    if not text:
        return None
    # Currency values are integer IDR; punctuation is treated as grouping.
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def parse_percent(value: object) -> float | None:
    """Parse percentage without confusing comma decimals with thousands separators."""
    if value is None:
        return None
    text = clean_text(value).replace("%", "").replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        # Indonesian style 1.234,5 -> 1234.5
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    return number if 0 <= number <= 100 else None


def merchant_mentions(*texts: object) -> list[str]:
    """Return all retailer names explicitly mentioned by a text/context."""
    haystack = " | ".join(clean_text(t).casefold() for t in texts if t)
    found = []
    for canonical, aliases in ALIAS_GROUPS:
        matched = False
        for alias in aliases:
            if re.search(rf"(?<!\w){re.escape(alias.casefold())}(?!\w)", haystack):
                matched = True
                break
        if matched and canonical not in found:
            found.append(canonical)
    return found


def unambiguous_merchant(*texts: object) -> str | None:
    """Do not guess a retailer from a listing URL mentioning multiple chains."""
    found = merchant_mentions(*texts)
    return found[0] if len(found) == 1 else None


def infer_merchant(*texts: object) -> str | None:
    """Legacy first-match helper for article titles; use unambiguous_merchant for cards."""
    haystack = " | ".join(clean_text(t).casefold() for t in texts if t)
    checks = []
    for canonical, aliases in ALIAS_GROUPS:
        for alias in aliases:
            checks.append((len(alias), canonical, alias))
    checks.sort(reverse=True)
    for _, canonical, alias in checks:
        if alias in haystack:
            # Avoid false matches on the shortest generic aliases.
            if alias in {"mcd", "kfc", "jco", "aeon", "subway"}:
                if not re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", haystack):
                    continue
            return canonical
    return None


def infer_merchant_dynamic(title: object = "", page_url: object = "", context_label: object = "") -> str | None:
    """Resolve a known alias or infer an unknown merchant from title/context/slug.

    Known aliases are preferred only when they appear in the focal title prefix or
    when no clear unknown focal label is available. This avoids mistaking a bank or
    payment partner mentioned later in an article for the retailer running the promo.
    """
    candidate = infer_merchant_candidate_from_title(title)
    if candidate:
        known_candidate = infer_merchant(candidate)
        if known_candidate:
            return known_candidate
        return candidate

    # For titles without a parseable campaign prefix, preserve the established alias
    # inference behavior for compatibility with current sources.
    known = infer_merchant(title, page_url, context_label)
    if known:
        return known

    label = clean_text(context_label)
    if is_dynamic_merchant_label(label):
        return label.strip(" :|–—-")

    return infer_merchant_candidate_from_url(page_url)


def canonical_source_type(source_name: str) -> str:
    return {
        "Hemat.id": "aggregator_product_listing",
        "Katalogpromosi.com": "aggregator_article",
        "Scanharga.com": "aggregator_article_or_catalog_image",
    }.get(source_name, "aggregator")


def parse_month(token: str) -> int | None:
    return MONTHS.get(token.casefold().rstrip("."))


def make_date(day: str, month_token: str, year: str) -> date | None:
    month = parse_month(month_token)
    if month is None:
        return None
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def parse_period_range(text: str) -> tuple[str | None, str | None, str]:
    """Return explicitly stated range / single date; month-only claims are not confirmed."""
    text = clean_text(text).casefold()
    # e.g. 15 Oktober - 18 November 2026
    cross = re.search(
        rf"\b(?P<d1>\d{{1,2}})\s+(?P<m1>{MONTH_PATTERN})\s*{DATE_SEP}\s*"
        rf"(?P<d2>\d{{1,2}})\s+(?P<m2>{MONTH_PATTERN})\s+(?P<y>\d{{4}})\b",
        text,
        re.IGNORECASE,
    )
    if cross:
        start = make_date(cross.group("d1"), cross.group("m1"), cross.group("y"))
        end = make_date(cross.group("d2"), cross.group("m2"), cross.group("y"))
        if start and end and end >= start:
            return start.isoformat(), end.isoformat(), "range"

    # e.g. 15-18 Oktober 2026 / 15 s.d. 18 Okt 2026
    same = re.search(
        rf"\b(?P<d1>\d{{1,2}})\s*{DATE_SEP}\s*(?P<d2>\d{{1,2}})\s+"
        rf"(?P<m>{MONTH_PATTERN})\s+(?P<y>\d{{4}})\b",
        text,
        re.IGNORECASE,
    )
    if same:
        start = make_date(same.group("d1"), same.group("m"), same.group("y"))
        end = make_date(same.group("d2"), same.group("m"), same.group("y"))
        if start and end and end >= start:
            return start.isoformat(), end.isoformat(), "range"

    # e.g. 15 Oktober 2026 - 18 Oktober 2026
    repeated = re.search(
        rf"\b(?P<d1>\d{{1,2}})\s+(?P<m1>{MONTH_PATTERN})\s+(?P<y1>\d{{4}})\s*{DATE_SEP}\s*"
        rf"(?P<d2>\d{{1,2}})\s+(?P<m2>{MONTH_PATTERN})\s+(?P<y2>\d{{4}})\b",
        text,
        re.IGNORECASE,
    )
    if repeated:
        start = make_date(repeated.group("d1"), repeated.group("m1"), repeated.group("y1"))
        end = make_date(repeated.group("d2"), repeated.group("m2"), repeated.group("y2"))
        if start and end and end >= start:
            return start.isoformat(), end.isoformat(), "range"

    # A single calendar date only counts as a one-day promo if explicitly phrased as a date.
    single = re.search(
        rf"\b(?:tanggal|berlaku tanggal|promo tanggal)\s+(?P<d>\d{{1,2}})\s+"
        rf"(?P<m>{MONTH_PATTERN})\s+(?P<y>\d{{4}})\b",
        text,
        re.IGNORECASE,
    )
    if single:
        d = make_date(single.group("d"), single.group("m"), single.group("y"))
        if d:
            return d.isoformat(), d.isoformat(), "single_day"
    return None, None, "unknown"


def parse_end_only(text: str) -> str | None:
    text = clean_text(text).casefold()
    m = re.search(
        rf"\b(?:sampai|hingga|s\.d\.|s/d)\s*(?P<d>\d{{1,2}})\s+(?P<m>{MONTH_PATTERN})\s+(?P<y>\d{{4}})\b",
        text,
        re.IGNORECASE,
    )
    if m:
        d = make_date(m.group("d"), m.group("m"), m.group("y"))
        return d.isoformat() if d else None
    return None


def determine_status(start: str | None, end: str | None, precision: str) -> str:
    try:
        ds = date.fromisoformat(start) if start else None
        de = date.fromisoformat(end) if end else None
    except ValueError:
        return "unknown"
    if ds and de and de < ds:
        return "unknown"
    if de and de < TODAY:
        return "expired"
    estimated_precisions = {"month", "until"}
    if ds and ds > TODAY:
        return "upcoming_estimated" if precision in estimated_precisions else "upcoming"
    if ds and de and ds <= TODAY <= de:
        if precision in {"range", "single_day"}:
            return "active"
        if precision in estimated_precisions:
            return "active_estimated"
    if de and de >= TODAY and not ds:
        return "active_estimated"
    return "unknown"


def parse_rupiah_label(text: str, label: str) -> int | None:
    m = re.search(rf"{label}\s*[:\-]?\s*Rp\.?\s*([0-9][0-9.,]*)", text, re.IGNORECASE)
    return parse_idr(m.group(1)) if m else None


def derive_campaign_type(title: str) -> str:
    t = clean_text(title).casefold()
    checks = [
        (r"\bjsm\b", "JSM"), (r"\bpsm\b", "PSM"),
        (r"member|harga member", "Harga Member"),
        (r"tebus murah|pwp", "Tebus Murah / PWP"),
        (r"beli banyak|beli \d+.*hemat", "Beli Banyak Lebih Hemat"),
        (r"weekend|akhir pekan", "Weekend Deals"),
        (r"payday|gajian", "Payday / Gajian"),
        (r"katalog", "Katalog Promo"),
        (r"promo|diskon|sale", "Promo Umum"),
    ]
    for pattern, label in checks:
        if re.search(pattern, t):
            return label
    return "Lainnya"


def parse_explicit_discount_amount(text: str) -> int | None:
    """Extract a stated nominal saving; never treat 'Diskon RpX' as product price.

    Returns None for percentage discounts and for amounts that are not explicitly
    labelled as a saving. The caller must still check whether a comparable
    normal/promo price pair is present in the same offer.
    """
    source = clean_text(text)
    if not source or re.search(r"\b(?:diskon|potongan|hemat)\s*\d+(?:[.,]\d+)?\s*%", source, re.I):
        return None
    match = re.search(
        r"\b(?:diskon|potongan(?:\s+harga)?|hemat)\s*(?:nominal\s*)?rp\.?\s*([0-9][0-9.,]*)",
        source,
        re.I,
    )
    if not match:
        return None
    # Only one explicitly stated saving is supported by this parser.
    return parse_idr(match.group(1))


def has_explicit_price_pair(text: str) -> bool:
    """Whether offer text explicitly contains both promo and normal prices."""
    source = clean_text(text)
    has_normal = bool(re.search(r"\bharga\s+(?:normal|asli|reguler)", source, re.I))
    has_promo = bool(
        re.search(r"\bharga\s+(?:promo|spesial|setelah\s+diskon)", source, re.I)
        or re.search(r"\b(?:dari|dibanding(?:kan)?)\s+harga\s+(?:normal|asli|reguler)", source, re.I)
    )
    return has_normal and has_promo and len(re.findall(r"\brp\.?\s*[0-9][0-9.,]*", source, re.I)) >= 2


def parse_offer_text(product_text: str, offer_text: str, source_mode: str) -> dict:
    offer = clean_text(offer_text)
    whole = f"{product_text} {offer}".strip()
    normal = parse_rupiah_label(whole, r"harga\s+normal")
    promo = parse_rupiah_label(whole, r"harga\s+promo")
    method = "not_extracted"
    basis = "no_single_price"
    purchase_condition = None
    deal_type = None
    quality_flags: list[str] = []

    condition = re.search(r"\b(beli\s+\d+(?:\s+(?:pcs?|pack|item))?(?:\s+gratis\s+\d+)?|buy\s*\d+\s*get\s*\d+)\b", offer, re.IGNORECASE)
    if condition:
        purchase_condition = clean_text(condition.group(1))

    if re.search(r"\bbeli\s*\d+\s*gratis\s*\d+|\bbuy\s*\d+\s*get\s*\d+", offer, re.IGNORECASE):
        deal_type = "Beli Gratis"
    elif re.search(r"\btebus\s+murah\b|\bpwp\b", offer, re.IGNORECASE):
        deal_type = "Tebus Murah / PWP"
    elif re.search(r"\bcashback\b", offer, re.IGNORECASE):
        deal_type = "Cashback"
    elif re.search(r"\bvoucher\b|\bkupon\b", offer, re.IGNORECASE):
        deal_type = "Kupon / Voucher"
    elif re.search(r"\bdiskon\s*\d", offer, re.IGNORECASE):
        deal_type = "Diskon Persentase"
    elif purchase_condition:
        deal_type = "Bundle Purchase"

    explicit_savings = parse_explicit_discount_amount(offer)
    if (
        explicit_savings is not None
        and normal is None
        and promo is None
        and not has_explicit_price_pair(offer)
        and len(re.findall(r"\brp\.?\s*[0-9][0-9.,]*", offer, re.I)) == 1
    ):
        return {
            "normal_price": None,
            "promo_price": None,
            "discount_percent": None,
            "discount_savings_rp": explicit_savings,
            "discount_basis": "explicit_amount_discount",
            "price_parse_method": "explicit_discount_amount_not_product_price",
            "price_basis": "discount_amount_offer",
            "purchase_condition": purchase_condition,
            "deal_type": "Potongan Nominal",
            "price_quality_flag": None,
        }

    if promo is not None:
        method = "explicit_promo_label"
        basis = "offer_price"
    elif source_mode == "hemat" and re.match(r".*\bRp\.?\s*\d", offer, re.IGNORECASE):
        # Hemat card text may contain explicit price labels outside offer_text.
        promo_match = re.search(r"Harga Promo\s*Rp\.?\s*([0-9][0-9.,]*)", whole, re.IGNORECASE)
        if promo_match:
            promo = parse_idr(promo_match.group(1))
            method, basis = "explicit_promo_label", "offer_price"
    elif source_mode in {"article", "line"}:
        # Only interpret an unlabelled price as the offer price if the offer starts
        # with a price or an explicit bundle statement, avoiding payment-minimum amounts.
        direct = re.match(r"^\s*Rp\.?\s*([0-9][0-9.,]*)\b", offer, re.IGNORECASE)
        bundle = re.match(r"^\s*beli\s+\d+.*?Rp\.?\s*([0-9][0-9.,]*)\b", offer, re.IGNORECASE)
        if bundle:
            promo = parse_idr(bundle.group(1))
            method, basis = "explicit_bundle_offer_amount", "bundle_offer_amount"
        elif direct:
            promo = parse_idr(direct.group(1))
            method, basis = "offer_amount_without_promo_label", "offer_price"
        elif promo is None and normal is not None:
            price_matches = list(re.finditer(r"Rp\.?\s*([0-9][0-9.,]*)", offer, re.IGNORECASE))
            alternatives = [parse_idr(m.group(1)) for m in price_matches]
            alternatives = [x for x in alternatives if x is not None and x != normal]
            if alternatives and re.search(r"diskon|promo|harga setelah", offer, re.IGNORECASE):
                promo = alternatives[0]
                method, basis = "inferred_from_multiple_prices", "offer_price"
                quality_flags.append("price_inferred")

    explicit_discount_match = re.search(r"\bdiskon\s*(\d{1,3}(?:[.,]\d+)?)\s*%", offer, re.IGNORECASE)
    explicit_discount = parse_percent(explicit_discount_match.group(1)) if explicit_discount_match else None
    if explicit_discount is not None:
        discount_percent = explicit_discount
        discount_basis = "source_explicit_product_claim"
    elif normal and promo and normal > 0 and promo >= 0 and promo < normal and basis == "offer_price" and not purchase_condition:
        discount_percent = round((normal - promo) / normal * 100, 1)
        discount_basis = "calculated_from_prices"
    else:
        discount_percent = None
        discount_basis = None

    savings = normal - promo if normal and promo is not None and normal > promo and basis == "offer_price" and not purchase_condition else None
    if normal is not None and promo is not None and promo > normal:
        quality_flags.append("promo_price_above_normal_price")
    if explicit_discount is not None and normal and promo and normal > 0 and promo < normal:
        calculated = round((normal - promo) / normal * 100, 1)
        if abs(calculated - explicit_discount) >= 5:
            quality_flags.append("explicit_discount_differs_from_price_calculation")
    if basis == "bundle_offer_amount":
        discount_percent = None
        discount_basis = None
        savings = None
        quality_flags.append("bundle_total_not_unit_price")

    return {
        "normal_price": normal,
        "promo_price": promo,
        "discount_percent": discount_percent,
        "discount_savings_rp": savings,
        "discount_basis": discount_basis,
        "price_parse_method": method,
        "price_basis": basis,
        "purchase_condition": purchase_condition,
        "deal_type": deal_type,
        "price_quality_flag": " | ".join(quality_flags) or None,
    }


def strip_product_name(text: str) -> tuple[str | None, str | None]:
    line = clean_text(text).strip("•*-–— ")
    if not line or len(line) < 5 or len(line) > 600 or NOISE_START.search(line):
        return None, None

    arrow_match = re.search(r"\s(?:→|➜|->|⇒)\s", line)
    if arrow_match:
        name = clean_text(line[:arrow_match.start()])
        offer = clean_text(line[arrow_match.end():])
        if name and offer and PRICE_SIGNAL.search(offer):
            return name, offer

    marker_patterns = [
        r"\bHarga\s+Promo\b", r"\bHarga\s+Normal\b",
        r"\bDiskon\s*\d{1,3}(?:[.,]\d+)?\s*%", r"\bPotongan(?:\s+Harga)?\s*\d+\s*%",
        r"\bBeli\s+\d+\b", r"\bBuy\s*1\b", r"\bTebus\s+Murah\b",
        r"\bCashback\b", r"\bVoucher\b", r"\bKupon\b", r"\bRp\.?\s*\d",
    ]
    positions = []
    for pattern in marker_patterns:
        m = re.search(pattern, line, re.IGNORECASE)
        if m:
            positions.append((m.start(), m.end()))
    if not positions:
        return None, None
    start = min(positions)[0]
    name = clean_text(line[:start]).rstrip(":-–— ")
    offer = clean_text(line[start:])
    if not name or not offer or len(name) < 3 or len(name.split()) > 30:
        return None, None
    if not PRICE_SIGNAL.search(offer):
        return None, None
    return name, offer


def parse_promo_metadata(title: str, visible_text: str, card_context: str = "") -> dict:
    start, end, precision = parse_period_range(title)
    if precision == "unknown":
        # The lead text can contain a dedicated 'Periode Promo' line. Only use a
        # day-range expression from this context; never guess a full month as a date.
        start2, end2, precision2 = parse_period_range(visible_text[:5000])
        if start2 and end2:
            start, end, precision = start2, end2, precision2
    if not end:
        end = parse_end_only(card_context) or parse_end_only(visible_text[:1500])
        if end:
            precision = "until"
    if precision == "unknown":
        # Month/year in an article title is a reviewable period estimate, not
        # a confirmed full-range promo. Keep it visible as active_estimated or
        # upcoming_estimated instead of incorrectly leaving it unknown.
        month_match = re.search(rf"\b(?P<month>{MONTH_PATTERN})\s+(?P<year>\d{{4}})\b", clean_text(title).casefold(), re.I)
        if not month_match:
            month_match = re.search(rf"\b(?P<month>{MONTH_PATTERN})\s+(?P<year>\d{{4}})\b", clean_text(visible_text[:1500]).casefold(), re.I)
        if month_match:
            month = parse_month(month_match.group("month"))
            year = int(month_match.group("year"))
            if month:
                month_start = date(year, month, 1)
                next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
                start, end, precision = month_start.isoformat(), (next_month - date.resolution).isoformat(), "month"
    evidence_body = clean_text(" ".join(part for part in (visible_text[:5000], card_context) if part))
    period_evidence_text, period_evidence_source = extract_period_evidence(
        title, evidence_body, precision
    )
    return {
        "valid_from": start,
        "valid_until": end,
        "period_precision": precision,
        "period_evidence_text": period_evidence_text,
        "period_evidence_source": period_evidence_source,
        "promo_status": determine_status(start, end, precision),
    }


def article_candidate_priority(candidate: dict) -> tuple[int, str, str]:
    """Rank by period evidence in the visible link title, never by permalink year.

    Some Scanharga permalinks were created years ago but their article content
    is refreshed with current promotions, so URL path dates are not reliable
    freshness evidence. If a listing title carries a period, use that instead.
    """
    title = clean_text(candidate.get("title_hint") or "")
    period = parse_promo_metadata(title, "")
    status_rank = {
        "active": 6,
        "active_estimated": 5,
        "upcoming": 4,
        "upcoming_estimated": 3,
        "unknown": 2,
        "expired": 1,
    }.get(period.get("promo_status"), 0)
    return status_rank, str(period.get("valid_until") or ""), str(period.get("valid_from") or "")


def find_article_container(soup: BeautifulSoup):
    selectors = [
        "article .entry-content", ".entry-content", ".post-body", ".post-body-container",
        ".post-content", "[itemprop='articleBody']", ".detail__body", ".detail-content",
        "article", "main",
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node and len(node.get_text(" ", strip=True)) > 100:
            container = node
            break
    else:
        container = soup.body or soup
    for node in container.select(
        "aside, nav, footer, header, [class*='related'], [class*='popular'], "
        "[class*='breadcrumb'], [class*='share'], [class*='comment'], script, style"
    ):
        node.decompose()
    return container


def title_from_page(soup: BeautifulSoup, fallback: str = "") -> str:
    node = soup.find("h1") or soup.find("h2", class_=re.compile("post-title|entry-title", re.I))
    if node:
        return clean_text(node.get_text(" ", strip=True))
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return clean_text(og["content"])
    if soup.title:
        return clean_text(soup.title.get_text(" ", strip=True))
    return fallback


def published_from_page(soup: BeautifulSoup) -> str | None:
    candidates = [
        soup.find("meta", attrs={"property": "article:published_time"}),
        soup.find("meta", attrs={"name": "pubdate"}),
        soup.find("time"),
    ]
    for node in candidates:
        if not node:
            continue
        value = node.get("content") or node.get("datetime") or node.get_text(" ", strip=True)
        if value:
            return clean_text(value)
    return None


def nearest_context(anchor) -> tuple[str, str | None]:
    """Find compact nearby HTML context containing the card end date/retailer."""
    best_text = clean_text(anchor.get_text(" ", strip=True))
    best_merchant = None
    for parent in anchor.parents:
        if getattr(parent, "name", None) in {"body", "html"}:
            break
        text = clean_text(parent.get_text(" ", strip=True))
        if not text:
            continue
        link_merchants = []
        for link in parent.find_all("a")[:20]:
            if link is anchor:
                continue
            label = clean_text(link.get_text(" ", strip=True))
            if not label or len(label) > 55:
                continue
            mentioned = merchant_mentions(label)
            # Consider only labels that identify exactly one retailer. If a card
            # wrapper contains multiple retailer links, leave it ambiguous.
            if len(mentioned) == 1 and mentioned[0] not in link_merchants:
                link_merchants.append(mentioned[0])
        merchant = link_merchants[0] if len(link_merchants) == 1 else None
        if merchant and not best_merchant:
            best_merchant = merchant
        elif not merchant:
            # Do not require a hard-coded alias for a new retailer. A single short
            # sibling link label in the card context can provide a dynamic candidate.
            dynamic_labels = []
            for link in parent.find_all("a")[:20]:
                if link is anchor:
                    continue
                label = clean_text(link.get_text(" ", strip=True))
                if not label or len(label) > 55 or not is_dynamic_merchant_label(label):
                    continue
                if PRICE_SIGNAL.search(label) or PROMO_WORDS.search(label):
                    continue
                if label.casefold() not in {x.casefold() for x in dynamic_labels}:
                    dynamic_labels.append(label)
            if len(dynamic_labels) == 1 and not best_merchant:
                best_merchant = dynamic_labels[0]
        if len(text) <= max(700, len(best_text) + 180):
            best_text = text
        if parse_end_only(text) and (merchant or len(text) <= max(700, len(best_text) + 180)):
            best_text = text
            if merchant:
                best_merchant = merchant
            break
    return best_text, best_merchant


def make_product_row(
    *, source_name: str, product_text: str, offer_text: str, title: str,
    merchant: str | None, meta: dict, source_url: str, source_page_url: str,
    source_item_url: str | None = None, raw_line: str = "", source_mode: str = "article",
    region_or_channel: str | None = None,
    published_at: str | None = None,
) -> dict:
    original_product_text = clean_text(product_text)
    normalized_product_text = clean_product_name(original_product_text)
    pricing = parse_offer_text(original_product_text, offer_text, source_mode)
    if not merchant:
        if source_name == "Hemat.id":
            # Combined category URLs can mention multiple retailers. Do not pick the
            # first one by substring; preserve unknown merchant for review instead.
            merchant = unambiguous_merchant(title) or unambiguous_merchant(source_item_url or "")
        else:
            merchant = infer_merchant_dynamic(title, source_page_url)
    discount_note = None
    if pricing.get("discount_basis") == "source_explicit_product_claim":
        discount_note = "Persentase dari sumber"
    elif pricing.get("discount_basis") == "calculated_from_prices":
        discount_note = "Dihitung dari harga"
    if pricing.get("price_quality_flag"):
        discount_note = (discount_note + " | " if discount_note else "") + "Perlu cek"
    promo_price_evidence = find_amount_evidence(offer_text, pricing.get("promo_price"), "promo")
    normal_price_evidence = find_amount_evidence(offer_text, pricing.get("normal_price"), "normal")
    if pricing.get("discount_basis") == "source_explicit_product_claim":
        discount_evidence = find_discount_evidence(offer_text, pricing.get("discount_percent"))
    elif pricing.get("discount_basis") == "calculated_from_prices" and promo_price_evidence and normal_price_evidence:
        discount_evidence = f"Harga normal: {normal_price_evidence} | Harga promo: {promo_price_evidence}"
    elif pricing.get("discount_basis") == "bundle_estimate_same_item_value":
        discount_evidence = clean_text(offer_text) or None
    else:
        discount_evidence = None

    row = {
        "product_text": normalized_product_text or original_product_text,
        "original_product_text": original_product_text,
        "product_name_variants": original_product_text,
        "normal_price": pricing.get("normal_price"),
        "promo_price": pricing.get("promo_price"),
        "discount_percent": pricing.get("discount_percent"),
        "merchant_name": merchant,
        "discount_savings_rp": pricing.get("discount_savings_rp"),
        "discount_note": discount_note,
        "deal_type": pricing.get("deal_type"),
        "purchase_condition": pricing.get("purchase_condition"),
        "offer_text": offer_text or None,
        **meta,
        "campaign_title": title or None,
        "campaign_type": derive_campaign_type(title),
        "published_at": published_at,
        "source_name": source_name,
        "source_type": canonical_source_type(source_name),
        "verification_status": "aggregator_only",
        "source_url": source_page_url,
        "source_page_url": source_page_url,
        "source_item_url": source_item_url,
        "region_or_channel": region_or_channel,
        "scraped_at": now_iso(),
        "price_parse_method": pricing.get("price_parse_method"),
        "price_basis": pricing.get("price_basis"),
        "discount_basis": pricing.get("discount_basis"),
        "price_quality_flag": pricing.get("price_quality_flag"),
        "promo_price_evidence_text": promo_price_evidence,
        "normal_price_evidence_text": normal_price_evidence,
        "discount_evidence_text": discount_evidence,
        "period_evidence_text": meta.get("period_evidence_text"),
        "period_evidence_source": meta.get("period_evidence_source"),
        "raw_line": raw_line or None,
    }
    row["product_category"], row["product_subcategory"] = classify_promo_category(row)
    return row


def extract_hemat_cards(soup: BeautifulSoup, page_url: str) -> list[dict]:
    rows: list[dict] = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        anchor_text = clean_text(anchor.get_text(" ", strip=True))
        if not anchor_text or not PRICE_SIGNAL.search(anchor_text):
            continue
        # Product-card links typically have a clear product description and price.
        product_name, offer_text = strip_product_name(anchor_text)
        if not product_name or not offer_text:
            continue
        context, context_merchant = nearest_context(anchor)
        title = title_from_page(soup, page_url)
        metadata = parse_promo_metadata(title, "", context)
        # Some listing cards explicitly label an offer as expired without showing
        # a complete date. Honor that card-level label; never borrow another card's date.
        if re.search(r"\bexpired\b|\bkedaluwarsa\b", context, re.IGNORECASE):
            metadata["promo_status"] = "expired"
        item_url = safe_url(urljoin(page_url, anchor.get("href", "")))
        card_merchant = context_merchant or unambiguous_merchant(anchor_text, context)
        if not card_merchant:
            # A single-chain page is a usable fallback; never infer a single chain
            # from a combined listing URL such as /katalog-alfamart,hypermart/.
            card_merchant = unambiguous_merchant(urlparse(page_url).path)
        if not card_merchant:
            card_merchant = infer_merchant_dynamic(title, page_url, context_label=context)
        row = make_product_row(
            source_name="Hemat.id", product_text=product_name, offer_text=offer_text,
            title=title, merchant=card_merchant,
            meta=metadata, source_url=page_url, source_page_url=page_url,
            source_item_url=item_url, raw_line=anchor_text, source_mode="hemat",
        )
        rejection_reason = product_candidate_rejection_reason(row, source_mode="hemat_card")
        if rejection_reason:
            REJECTED_PRODUCT_CANDIDATES.append({
                "source_name": "Hemat.id", "campaign_title": title, "source_url": page_url,
                "source_item_url": item_url, "raw_line": anchor_text,
                "product_text": row.get("product_text"), "offer_text": offer_text,
                "rejection_reason": rejection_reason, "scraped_at": now_iso(),
            })
            continue
        review_reason = product_candidate_review_reason(row, source_mode="hemat_card")
        row["product_validation_status"] = "review" if review_reason else "eligible"
        row["product_review_reason"] = review_reason
        if not row.get("promo_price") and not row.get("discount_percent") and not row.get("purchase_condition"):
            continue
        key = (normalize(row["product_text"]), normalize(row.get("offer_text")), row.get("promo_price"), row.get("merchant_name"), row.get("valid_until"))
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def skip_article_line(line: str) -> bool:
    if not line or len(line) < 8 or len(line) > 650:
        return True
    if NOISE_START.search(line):
        return True
    return False


def iter_article_lines(container) -> list[str]:
    lines: list[str] = []
    seen = set()
    # Prefer list items since Katalogpromosi commonly expresses products as
    # 'PRODUCT → Rp ...'. Avoid processing nested paragraphs twice.
    for node in container.find_all(["li", "p"]):
        if node.name == "p" and node.find_parent("li") is not None:
            continue
        if node.find(["li", "p"]):
            continue
        line = clean_text(node.get_text(" ", strip=True))
        if not line or line in seen:
            continue
        seen.add(line)
        if PRICE_SIGNAL.search(line) and not skip_article_line(line):
            lines.append(line)
    # Some pages use plain divs/paragraph-like blocks without li or p elements.
    if not lines:
        for node in container.find_all(["div", "span"]):
            if node.find(["div", "span", "li", "p"]):
                continue
            line = clean_text(node.get_text(" ", strip=True))
            if line and line not in seen and PRICE_SIGNAL.search(line) and not skip_article_line(line):
                seen.add(line)
                lines.append(line)
    return lines


def extract_article_rows(container, source_name: str, title: str, article_url: str, meta: dict, published_at: str | None = None) -> list[dict]:
    rows: list[dict] = []
    merchant = infer_merchant_dynamic(title, article_url)
    seen = set()
    for line in iter_article_lines(container):
        product_name, offer = strip_product_name(line)
        if not product_name or not offer:
            continue
        # Do not interpret payment minimums or general conditions as products.
        if NOISE_START.search(product_name):
            continue
        row = make_product_row(
            source_name=source_name, product_text=product_name, offer_text=offer,
            title=title, merchant=merchant, meta=meta,
            source_url=article_url, source_page_url=article_url,
            source_item_url=None, raw_line=line, source_mode="article", published_at=published_at,
        )
        rejection_reason = product_candidate_rejection_reason(row, source_mode="article")
        if rejection_reason:
            REJECTED_PRODUCT_CANDIDATES.append({
                "source_name": source_name, "campaign_title": title, "source_url": article_url,
                "source_item_url": None, "raw_line": line, "product_text": row.get("product_text"),
                "offer_text": offer, "rejection_reason": rejection_reason, "scraped_at": now_iso(),
            })
            continue
        review_reason = product_candidate_review_reason(row, source_mode="article")
        row["product_validation_status"] = "review" if review_reason else "eligible"
        row["product_review_reason"] = review_reason
        if not (row.get("promo_price") is not None or row.get("discount_percent") is not None or row.get("purchase_condition")):
            continue
        key = (normalize(product_name), normalize(offer), row.get("promo_price"), row.get("normal_price"))
        if key not in seen:
            seen.add(key)
            rows.append(row)
    return rows


def image_rows_from_article(container, source_name: str, title: str, article_url: str, period_status: str) -> list[dict]:
    images = []
    seen = set()
    for img in container.find_all("img")[:MAX_IMAGES_PER_ARTICLE]:
        url = img.get("data-src") or img.get("data-lazy-src") or img.get("src") or ""
        url = safe_url(urljoin(article_url, url))
        if not url or url in seen or url.startswith("data:"):
            continue
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            continue
        # Avoid tracking pixels and tiny known icons when width is explicit.
        width = img.get("width")
        try:
            if width and int(width) < 120:
                continue
        except (ValueError, TypeError):
            pass
        seen.add(url)
        images.append({
            "source_name": source_name,
            "campaign_title": title,
            "article_url": article_url,
            "image_url": url,
            "image_alt": clean_text(img.get("alt") or img.get("title") or "") or None,
            "period_status": period_status,
            "scraped_at": now_iso(),
        })
    return images


def candidate_title(anchor) -> str:
    text = clean_text(anchor.get_text(" ", strip=True))
    if len(text) > 6 and text.casefold() not in {"read more", "read more »", "selengkapnya"}:
        return text
    for parent in anchor.parents:
        if getattr(parent, "name", None) in {"body", "html"}:
            break
        h = parent.find(["h1", "h2", "h3", "h4"])
        if h:
            candidate = clean_text(h.get_text(" ", strip=True))
            if len(candidate) > 6:
                return candidate
    return clean_text(anchor.get("title") or anchor.get("aria-label") or "")


def is_article_candidate(source_name: str, url: str, title: str) -> bool:
    path = urlparse(url).path.lower()
    if source_name == "Hemat.id":
        return False  # Hemat products are extracted from listing cards, not article pages.
    if not title or not PROMO_WORDS.search(title):
        # Still allow dated Scanharga catalogue articles, which might omit the word promo in link text.
        if not (source_name == "Scanharga.com" and re.search(r"/\d{4}/\d{1,2}/[^/]+\.html$", path)):
            return False
    if source_name == "Katalogpromosi.com":
        pieces = [p for p in path.strip("/").split("/") if p]
        if not pieces or len(pieces) > 2:
            return False
        if pieces[0].lower() in {
            "kategori", "category", "tag", "katalog-belanja", "page",
            "wp-content", "wp-json", "promo-jsm", "promo-weekday",
            "terbaru", "restoran", "event", "kupon", "kategori-promo",
        }:
            return False
        if "?s=" in url or path.endswith(".jpg") or path.endswith(".png"):
            return False
        return True
    if source_name == "Scanharga.com":
        return bool(re.search(r"/\d{4}/\d{1,2}/[^/]+\.html$", path))
    return False


def is_listing_link(source_name: str, url: str, text: str) -> bool:
    path = urlparse(url).path.lower()
    txt = clean_text(text).casefold()
    if source_name == "Hemat.id":
        return any(mark in path for mark in ("/katalog-", "/promo-promo/")) or bool(re.search(r"/page/\d+/?$", path)) or "promo halaman" in txt
    if source_name == "Katalogpromosi.com":
        return (
            bool(re.search(r"/page/\d+/?$", path))
            or path in {"/katalog-belanja/", "/terbaru/", "/promo-jsm/", "/promo-weekday/"}
            or "older posts" in txt or "posting lama" in txt or "berikutnya" in txt
        )
    if source_name == "Scanharga.com":
        return (
            "/search/label/" in path
            or bool(re.search(r"/search\?updated-max=", url))
            or "older posts" in txt or "posting lama" in txt or "posting berikutnya" in txt
        )
    return False


def fetch_page(
    session: requests.Session,
    url: str,
    source_name: str | None = None,
) -> tuple[BeautifulSoup | None, dict]:
    info = {
        "url": url,
        "status": "unknown",
        "http_status": None,
        "error": None,
        "final_url": None,
    }
    try:
        response = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        info["http_status"] = response.status_code
        final_url = getattr(response, "url", None) or url
        info["final_url"] = final_url
        if response.status_code in {401, 403, 429}:
            info["status"] = "blocked_or_limited"
            info["error"] = f"HTTP {response.status_code}; tidak mencoba melewati pembatasan."
            return None, info
        if 500 <= response.status_code < 600:
            info["status"] = "server_error"
            info["error"] = f"HTTP {response.status_code}; sumber dihentikan untuk run ini."
            return None, info
        # A stale/deleted article is an URL-level problem, not evidence that the
        # whole source is unavailable. Continue discovering and parsing other URLs.
        if response.status_code == 404:
            info["status"] = "not_found"
            info["error"] = "HTTP 404; URL tidak ditemukan, lanjut ke URL sumber berikutnya."
            return None, info
        if response.status_code == 410:
            info["status"] = "gone"
            info["error"] = "HTTP 410; URL telah dihapus, lanjut ke URL sumber berikutnya."
            return None, info
        if 400 <= response.status_code < 500:
            info["status"] = "client_error"
            info["error"] = f"HTTP {response.status_code}; URL tidak dapat diproses, sumber lain tetap dicoba."
            return None, info
        response.raise_for_status()

        # Do not attribute a redirected page on an unrelated host to the
        # aggregator that linked to it. Only the same configured source host
        # (including its www alias) may be parsed as source content.
        if source_name and not same_site(final_url, source_name):
            info["status"] = "redirected_offsite"
            info["error"] = (
                "Redirect keluar dari domain sumber; halaman tidak diproses."
            )
            return None, info

        content_type = response.headers.get("Content-Type", "")
        body_start = response.text.lstrip().casefold()
        looks_like_html = body_start.startswith(("<!doctype html", "<html"))
        if "html" not in content_type.casefold() and not looks_like_html:
            info["status"] = "not_html"
            return None, info
        info["status"] = "ok"
        return BeautifulSoup(response.text, "html.parser"), info
    except requests.RequestException as exc:
        info["status"] = "request_error"
        info["error"] = f"{type(exc).__name__}: {exc}"
        return None, info


# Store article records per run without introducing a global data dependency.
def run_source(session: requests.Session, source_name: str) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    # Local discovery with explicit article record collection. Kept separate
    # to make failures and partial results visible in the run log.
    seeds = SOURCE_SEEDS[source_name]
    queue = list(OrderedDict.fromkeys(seeds))
    seen_pages: set[str] = set()
    articles: OrderedDict[str, dict] = OrderedDict()
    products: list[dict] = []
    listing_log: list[dict] = []
    images: list[dict] = []
    article_records: list[dict] = []
    page_index = 0
    source_access_blocked = False

    while queue and page_index < MAX_LISTING_PAGES_PER_SOURCE:
        url = safe_url(queue.pop(0))
        if not url or url in seen_pages or not same_site(url, source_name):
            continue
        seen_pages.add(url)
        page_index += 1
        say(f"[{source_name} listing {page_index}/{MAX_LISTING_PAGES_PER_SOURCE}] {url}")
        soup, fetch_log = fetch_page(session, url, source_name)
        listing_log.append({"source_name": source_name, "phase": "listing", **fetch_log})
        if soup is None:
            if fetch_log.get("status") in {"blocked_or_limited", "server_error", "request_error", "redirected_offsite"}:
                source_access_blocked = True
                say(
                    f"[{source_name}] menghentikan penelusuran setelah "
                    f"{fetch_log.get('status')} pada halaman listing."
                )
                break
            time.sleep(DELAY_SECONDS)
            continue
        final_url = fetch_log.get("final_url") or url

        if source_name == "Hemat.id":
            rows = extract_hemat_cards(soup, final_url)
            products.extend(rows)

        for anchor in soup.find_all("a", href=True):
            target = safe_url(urljoin(final_url, anchor.get("href", "")))
            if not target or not same_site(target, source_name):
                continue
            text = candidate_title(anchor)
            if is_article_candidate(source_name, target, text):
                if target not in articles:
                    articles[target] = {"source_name": source_name, "url": target, "title_hint": text, "found_on": final_url}
            elif is_listing_link(source_name, target, text) and target not in seen_pages and target not in queue:
                queue.append(target)
        time.sleep(DELAY_SECONDS)

    # A blocked listing makes the source run incomplete. Discard any partial
    # product extraction so main() can preserve the source's last-good rows.
    # Do not continue to article URLs on the same source after a limit/server error.
    if source_access_blocked:
        return [], article_records, [], listing_log

    # Prefer article candidates whose visible title says that the offer is
    # current/upcoming. The stable sort keeps discovery order for ties.
    ranked_articles = sorted(articles.values(), key=article_candidate_priority, reverse=True)
    selected_articles = ranked_articles[:MAX_ARTICLES_PER_SOURCE]
    for i, candidate in enumerate(selected_articles, start=1):
        article_url = candidate["url"]
        say(f"[{source_name} article {i}/{len(selected_articles)}] {article_url}")
        soup, fetch_log = fetch_page(session, article_url, source_name)
        record = {
            "source_name": source_name,
            "title": candidate.get("title_hint") or "",
            "source_url": article_url,
            "published_at": None,
            "merchant_name": None,
            "valid_from": None,
            "valid_until": None,
            "period_precision": "unknown",
            "promo_status": "unknown",
            "product_rows_extracted": 0,
            "period_evidence_text": None,
            "period_evidence_source": None,
            "image_count": 0,
            "scrape_status": fetch_log["status"],
            "note": fetch_log.get("error"),
            "scraped_at": now_iso(),
        }
        if soup is None:
            listing_log.append({"source_name": source_name, "phase": "article", **fetch_log})
            article_records.append(record)
            if fetch_log.get("status") in {"blocked_or_limited", "server_error", "request_error", "redirected_offsite"}:
                source_access_blocked = True
                say(
                    f"[{source_name}] menghentikan pengambilan artikel "
                    f"setelah {fetch_log.get('status')}."
                )
                break
            continue
        final_url = fetch_log.get("final_url") or article_url
        title = title_from_page(soup, candidate.get("title_hint", ""))
        container = find_article_container(soup)
        body_text = clean_text(container.get_text(" ", strip=True))
        period = parse_promo_metadata(title, body_text)
        published = published_from_page(soup)
        merchant = infer_merchant_dynamic(title, final_url)
        article_rows = extract_article_rows(container, source_name, title, final_url, period, published_at=published)
        article_images = image_rows_from_article(container, source_name, title, final_url, period["promo_status"])
        products.extend(article_rows)
        images.extend(article_images)
        record.update({
            "title": title,
            "published_at": published,
            "merchant_name": merchant,
            **period,
            "product_rows_extracted": len(article_rows),
            "period_evidence_text": period.get("period_evidence_text"),
            "period_evidence_source": period.get("period_evidence_source"),
            "image_count": len(article_images),
            "scrape_status": "ok",
            "note": None if article_rows else "Tidak ada baris produk tekstual yang cukup jelas; cek file katalog gambar bila tersedia.",
            "scraped_at": now_iso(),
        })
        article_records.append(record)
        listing_log.append({"source_name": source_name, "phase": "article", **fetch_log, "title": title, "product_rows": len(article_rows), "image_count": len(article_images)})
        time.sleep(DELAY_SECONDS)

    # If blocked partway through article fetches, do not publish partial rows as
    # a complete source refresh. main() then falls back to last-good rows.
    if source_access_blocked:
        return [], article_records, [], listing_log

    return products, article_records, images, listing_log


def dedupe_rows(rows: list[dict]) -> list[dict]:
    groups: OrderedDict[tuple, list[dict]] = OrderedDict()
    for row in rows:
        key = (
            normalize(row.get("merchant_name")),
            normalize(row.get("product_text")),
            normalize(row.get("offer_text")),
            str(row.get("normal_price") or ""),
            str(row.get("promo_price") or ""),
            str(row.get("valid_from") or ""),
            str(row.get("valid_until") or ""),
            normalize(row.get("purchase_condition")),
        )
        groups.setdefault(key, []).append(row)
    output = []
    for key, group in groups.items():
        best = dict(max(group, key=lambda r: sum(bool(r.get(k)) for k in (
            "merchant_name", "normal_price", "promo_price", "discount_percent",
            "valid_from", "valid_until", "source_url", "offer_text",
        ))))
        statuses = {str(r.get("product_validation_status") or "eligible") for r in group}
        best["product_validation_status"] = "eligible" if "eligible" in statuses else ("review" if "review" in statuses else "rejected")
        review_reasons = list(OrderedDict.fromkeys(r.get("product_review_reason") for r in group if r.get("product_review_reason")))
        best["product_review_reason"] = " | ".join(review_reasons) or None
        all_urls = list(OrderedDict.fromkeys(r.get("source_url") for r in group if r.get("source_url")))
        all_names = list(OrderedDict.fromkeys(r.get("source_name") for r in group if r.get("source_name")))
        best["source_url"] = all_urls[0] if all_urls else best.get("source_url")
        best["source_urls"] = " | ".join(all_urls)
        best["source_names"] = " | ".join(all_names)
        variants = []
        for item in group:
            variants.extend(name_variants(item.get("product_name_variants")))
            variants.extend(name_variants(item.get("original_product_text")))
        variants = list(OrderedDict.fromkeys(variants))
        best["product_name_variants"] = " | ".join(variants)
        if variants:
            best["original_product_text"] = variants[0]
        best["retained_from_previous_run"] = any(
            str(r.get("retained_from_previous_run", "")).strip().casefold() in {"1", "true", "yes", "ya", "y"}
            for r in group
        )
        best["duplicate_count"] = len(group)
        best["cross_source_duplicate"] = len(all_names) > 1
        best["duplicate_group_id"] = hashlib.sha1(repr(key).encode("utf-8")).hexdigest()[:12]
        # If equivalent sources disagree on prices, flag rather than silently treating one as truth.
        prices = {(r.get("normal_price"), r.get("promo_price")) for r in group if r.get("normal_price") or r.get("promo_price")}
        if len(prices) > 1:
            existing = best.get("price_quality_flag")
            best["price_quality_flag"] = " | ".join(x for x in [existing, "cross_source_price_disagreement"] if x)
        output.append(best)
    return output


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def row_review_reason(row: dict) -> str | None:
    issues = []
    if row.get("product_validation_status") == "review" and row.get("product_review_reason"):
        issues.append("product_identity:" + str(row.get("product_review_reason")))
    if row.get("promo_status") in {"unknown", "active_estimated"}:
        issues.append(f"period:{row.get('promo_status')}")
    if not row.get("merchant_name"):
        issues.append("merchant_not_identified")
    if row.get("price_quality_flag"):
        issues.append(row["price_quality_flag"])
    if row.get("discount_basis") is None and row.get("normal_price") and row.get("promo_price"):
        issues.append("discount_not_calculated_check_price_basis")
    try:
        duplicate_count = int(row.get("duplicate_count", 1) or 1)
    except (TypeError, ValueError):
        duplicate_count = 1
    if duplicate_count > 1 and row.get("cross_source_price_disagreement"):
        issues.append("cross_source_duplicate_review")
    return " | ".join(issues) or None


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_previous_product_rows(path: Path) -> list[dict]:
    """Load the last good collector output, if present, for source-level fallback."""
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except (OSError, csv.Error):
        return []


def sources_in_row(row: dict) -> set[str]:
    values = [row.get("source_name", ""), row.get("source_names", "")]
    return {piece.strip() for value in values for piece in str(value or "").split(" | ") if piece.strip()}


def build_source_health_report(source_names, products, request_log) -> dict:
    """Summarize per-source request, freshness, and evidence coverage without web access."""
    report = {}
    for source_name in source_names:
        source_requests = [item for item in request_log if item.get("source_name") == source_name]
        source_products = [item for item in products if source_name in sources_in_row(item)]
        fresh_source_products = [
            item for item in source_products
            if str(item.get("retained_from_previous_run", "")).casefold() not in {"true", "1", "yes"}
        ]
        price_rows = [item for item in source_products if item.get("promo_price") not in (None, "")]
        discount_rows = [item for item in source_products if item.get("discount_percent") not in (None, "")]
        period_rows = [item for item in source_products if item.get("promo_status") in {"active", "active_estimated", "upcoming", "upcoming_estimated"}]
        ok_requests = sum(item.get("status") == "ok" for item in source_requests)
        retained = len(source_products) - len(fresh_source_products)
        if fresh_source_products and ok_requests:
            status = "fresh"
        elif source_products and not fresh_source_products:
            status = "preserved_previous"
        elif ok_requests:
            status = "reachable_no_products"
        elif source_requests:
            status = "request_failed"
        else:
            status = "no_request"
        report[source_name] = {
            "request_count": len(source_requests),
            "successful_requests": ok_requests,
            "failed_requests": sum(item.get("status") in {"request_error", "error", "not_html", "not_found", "gone", "client_error"} for item in source_requests),
            "not_found_requests": sum(item.get("status") == "not_found" for item in source_requests),
            "gone_requests": sum(item.get("status") == "gone" for item in source_requests),
            "other_client_error_requests": sum(item.get("status") == "client_error" for item in source_requests),
            "blocked_or_limited_requests": sum(item.get("status") == "blocked_or_limited" for item in source_requests),
            "product_rows_in_output": len(source_products),
            "fresh_product_rows": len(fresh_source_products),
            "preserved_previous_rows": retained,
            "rows_with_promo_price": len(price_rows),
            "promo_price_evidence_coverage": round(sum(bool(item.get("promo_price_evidence_text")) for item in price_rows) / len(price_rows), 3) if price_rows else None,
            "rows_with_normal_price": sum(item.get("normal_price") not in (None, "") for item in source_products),
            "rows_with_discount": len(discount_rows),
            "discount_evidence_coverage": round(sum(bool(item.get("discount_evidence_text")) for item in discount_rows) / len(discount_rows), 3) if discount_rows else None,
            "rows_with_period": len(period_rows),
            "period_evidence_coverage": round(sum(bool(item.get("period_evidence_text")) for item in period_rows) / len(period_rows), 3) if period_rows else None,
            "status": status,
        }
    return report


def run_self_tests() -> None:
    REJECTED_PRODUCT_CANDIDATES.clear()
    assert parse_idr("129.900") == 129900
    assert parse_idr("Rp 1.299.000") == 1299000
    assert parse_percent("25,5") == 25.5
    assert parse_percent("14%") == 14.0
    start, end, precision = parse_period_range("Promo JSM Indomaret 15-18 Oktober 2026 Minggu Ini")
    assert (start, end, precision) == ("2026-10-15", "2026-10-18", "range")
    start, end, precision = parse_period_range("Promo Super Indo 23 - 25 Oktober 2026")
    assert (start, end, precision) == ("2026-10-23", "2026-10-25", "range")

    html = '''<article><h1>Promo JSM Indomaret 23 - 25 Oktober 2026</h1><div class="entry-content"><ul>
    <li>FILMA Minyak Goreng 2L Pch → Rp. 41.800</li>
    <li>INDOMIE Ayam Bawang Pck → Beli 5 Rp. 14.000</li>
    </ul></div></article>'''
    soup = BeautifulSoup(html, "html.parser")
    title = title_from_page(soup)
    container = find_article_container(soup)
    meta = parse_promo_metadata(title, clean_text(container.get_text(" ", strip=True)))
    rows = extract_article_rows(container, "Katalogpromosi.com", title, "https://katalogpromosi.com/promo-jsm-test/", meta)
    assert len(rows) == 2, f"Expected 2 offer rows, got {len(rows)}"
    assert rows[0]["promo_price"] == 41800
    assert rows[0]["merchant_name"] == "Indomaret"
    assert rows[1]["price_basis"] == "bundle_offer_amount"

    html = '''<div class="product"><a href="/item/1">400 ml FRES&NAT Body Wash Diskon 35%, Harga Promo Rp12.900, Harga Normal Rp19.900</a><span>Sampai 31 Okt 2026</span><a href="/katalog-alfamart/">Alfamart</a></div>'''
    soup = BeautifulSoup(html, "html.parser")
    rows = extract_hemat_cards(soup, "https://www.hemat.id/katalog-alfamart/")
    assert rows and rows[0]["promo_price"] == 12900 and rows[0]["normal_price"] == 19900
    assert rows[0]["discount_percent"] == 35.0

    # Never infer Alfamart as the merchant merely because a combined Hemat URL
    # contains "alfamart,hypermart". Ambiguous retailer attribution stays unknown.
    ambiguous = make_product_row(
        source_name="Hemat.id",
        product_text="Aice Mochi Vanilla Chocolate 270 g",
        offer_text="Harga Promo Rp9.900",
        title="Katalog Promo Alfamart, Hypermart",
        merchant=None,
        meta=parse_promo_metadata("Katalog Promo Alfamart, Hypermart", "", ""),
        source_url="https://www.hemat.id/katalog-alfamart,hypermart/",
        source_page_url="https://www.hemat.id/katalog-alfamart,hypermart/",
        source_mode="hemat",
    )
    assert ambiguous["merchant_name"] is None, "Combined Hemat listing must not guess the first retailer"

    # Regression: campaign editorial headings containing a percentage are not SKUs.
    heading_html = '''<article><div class="entry-content">
    <p>Apa Benefit yang Didapatkan Promo Paris Baguette BNI Wonderful Weekend: Diskon 50%</p>
    <p>Apa Saja Penawaran Dalam Promo Paris Baguette BNI Wonderful Weekend: Diskon 50%</p>
    <p>Bagaimana Cara Mendapatkan Promo Dan Mekanisme Promo Paris Baguette BNI Wonderful Weekend: Diskon 50%</p>
    <ul><li>FRES&amp;NAT Body Wash 400 ml → Harga Promo Rp12.900, Harga Normal Rp19.900</li></ul>
    </div></article>'''
    heading_soup = BeautifulSoup(heading_html, "html.parser")
    heading_container = find_article_container(heading_soup)
    heading_title = "Promo Paris Baguette BNI Wonderful Weekend 23-24 Oktober 2026"
    heading_meta = parse_promo_metadata(heading_title, clean_text(heading_container.get_text(" ", strip=True)))
    before_rejected = len(REJECTED_PRODUCT_CANDIDATES)
    heading_rows = extract_article_rows(heading_container, "Katalogpromosi.com", heading_title, "https://katalogpromosi.com/promo-paris-baguette-test/", heading_meta)
    assert len(heading_rows) == 1, f"Expected only the product line, got {len(heading_rows)} rows"
    assert "fres&nat" in heading_rows[0]["product_text"].casefold()
    newly_rejected = REJECTED_PRODUCT_CANDIDATES[before_rejected:]
    assert sum(1 for x in newly_rejected if x["rejection_reason"] == "editorial_heading_misparsed_as_product") >= 3

    retained = {
        "source_name": "Hemat.id",
        "source_names": "Hemat.id | Scanharga.com",
        "product_text": "Sample Product",
        "offer_text": "Harga promo Rp8.000",
        "retained_from_previous_run": "true",
    }
    assert "Scanharga.com" in sources_in_row(retained)
    merged_retained = dedupe_rows([retained])
    assert merged_retained[0]["retained_from_previous_run"] is True
    print("SELF-TEST PASSED: harga, persen, periode, parser sumber, merchant ambigu, dan last-good fallback.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape katalog promo publik dari Hemat.id, Katalogpromosi.com, dan Scanharga.com.")
    parser.add_argument("--self-test", action="store_true", help="Uji parser lokal tanpa koneksi internet.")
    args = parser.parse_args()
    if args.self_test:
        run_self_tests()
        return

    INTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; PromoCatalogCollector/1.0; contact: local-script)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    })

    # Keep last-good data available in case an upstream page goes temporarily empty,
    # is blocked, or changes its HTML structure.
    previous_products = read_previous_product_rows(OUT_ALL)
    all_products: list[dict] = []
    all_articles: list[dict] = []
    all_images: list[dict] = []
    request_log: list[dict] = []
    preserved_previous_rows_by_source: dict[str, int] = {}
    fresh_rows_by_source: Counter = Counter()

    try:
        for source_name in SOURCE_SEEDS:
            try:
                products, articles, images, log = run_source(session, source_name)
                all_products.extend(products)
                all_articles.extend(articles)
                all_images.extend(images)
                request_log.extend(log)
                say(f"[{source_name}] produk={len(products)}, artikel={len(articles)}, gambar={len(images)}")
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                request_log.append({"source_name": source_name, "phase": "source", "status": "error", "error": f"{type(exc).__name__}: {exc}"})
                say(f"[{source_name}] error: {type(exc).__name__}: {exc}")

        # If an individual source yields no textual products in this run, preserve its
        # last-good product rows. This avoids wiping a source because of a temporary
        # outage or HTML-layout change. Their original scraped_at remains visible for audit.
        fresh_rows_by_source = Counter(clean_text(row.get("source_name")) for row in all_products if row.get("source_name"))
        for source_name in SOURCE_SEEDS:
            if fresh_rows_by_source.get(source_name, 0) > 0:
                continue
            source_previous = [row for row in previous_products if source_name in sources_in_row(row)]
            if source_previous:
                for previous in source_previous:
                    retained = dict(previous)
                    retained["retained_from_previous_run"] = "true"
                    # Preserve the old source name so downstream auditing can identify the fallback.
                    all_products.append(retained)
                preserved_previous_rows_by_source[source_name] = len(source_previous)

        # Exact cross-source deduplication only; differing dates/prices remain separate and traceable.
        products = dedupe_rows(all_products)
        for row in products:
            row.setdefault("retained_from_previous_run", False)
            row["review_reason"] = row_review_reason(row)

        active = [r for r in products if r.get("promo_status") in {"active", "active_estimated"} and r.get("product_validation_status") != "review"]
        review = [r for r in products if r.get("review_reason") or r.get("product_validation_status") == "review"]

        write_csv(OUT_ALL, products, PRODUCT_FIELDS + ["source_urls", "source_names", "duplicate_count", "cross_source_duplicate", "duplicate_group_id", "review_reason"])
        write_csv(OUT_ACTIVE, active, ACTIVE_FIELDS + ["source_urls", "source_names", "duplicate_count", "cross_source_duplicate", "duplicate_group_id"])
        write_csv(OUT_REVIEW, review, REVIEW_FIELDS + ["source_urls", "source_names", "duplicate_count", "cross_source_duplicate", "duplicate_group_id"])
        write_csv(OUT_ARTICLES, all_articles, ARTICLE_FIELDS)
        write_csv(OUT_IMAGES, all_images, IMAGE_FIELDS)
        write_csv(OUT_REJECTED_CANDIDATES, REJECTED_PRODUCT_CANDIDATES, [
            "source_name", "campaign_title", "source_url", "source_item_url",
            "raw_line", "product_text", "offer_text", "rejection_reason", "scraped_at",
        ])

        source_health = build_source_health_report(SOURCE_SEEDS, all_products, request_log)

        report = {
            "built_at": now_iso(),
            "reference_date": TODAY.isoformat(),
            "sources": list(SOURCE_SEEDS),
            "source_health": source_health,
            "article_selection_policy": (
                "Prioritize active/current and upcoming periods parsed from visible article titles; "
                "month-only periods are estimates; permalink year is not treated as freshness evidence."
            ),
            "request_count": len(request_log),
            "successful_requests": sum(1 for x in request_log if x.get("status") == "ok"),
            "blocked_or_limited_requests": sum(1 for x in request_log if x.get("status") == "blocked_or_limited"),
            "fresh_product_rows_before_fallback": sum(fresh_rows_by_source.values()),
            "preserved_previous_rows_by_source": preserved_previous_rows_by_source,
            "all_product_rows_before_dedup": len(all_products),
            "product_rows_after_exact_dedup": len(products),
            "active_rows_including_end_date_estimates": len(active),
            "review_rows": len(review),
            "merchant_counts": dict(Counter(r.get("merchant_name") or "UNKNOWN" for r in active)),
            "rows_by_source": dict(Counter(r.get("source_name") or "UNKNOWN" for r in all_products)),
            "rows_with_explicit_discount": sum(r.get("discount_basis") == "source_explicit_product_claim" for r in products),
            "rows_with_price_calculated_discount": sum(r.get("discount_basis") == "calculated_from_prices" for r in products),
            "image_references_found": len(all_images),
            "rejected_non_product_candidates": len(REJECTED_PRODUCT_CANDIDATES),
            "rejection_reasons": dict(Counter(x.get("rejection_reason", "unknown") for x in REJECTED_PRODUCT_CANDIDATES)),
            "output_files": [OUT_ALL.name, OUT_ACTIVE.name, OUT_REVIEW.name, OUT_ARTICLES.name, OUT_IMAGES.name, OUT_REJECTED_CANDIDATES.name, OUT_LOG.name],
            "limitations": [
                "Data aggregator diberi verification_status=aggregator_only; bukan verifikasi resmi retailer.",
                "Brosur yang hanya berupa gambar dicatat di catalog_images.csv; OCR tidak dijalankan dan produk tidak ditebak dari gambar.",
                "Artikel tanpa periode eksplisit dapat berstatus active_estimated atau unknown dan perlu diperiksa.",
                "Situs yang mengembalikan 401/403/429 tidak dicoba dilewati.",
            ],
            "request_log": request_log,
        }
        write_json(OUT_LOG, report)
        say("\nSELESAI - MULTI-SOURCE PROMO COLLECTOR")
        say(f"Baris baru hasil scrape   : {sum(fresh_rows_by_source.values())}")
        say(f"Baris lama dipertahankan  : {sum(preserved_previous_rows_by_source.values())}")
        say(f"Produk sebelum deduplikasi : {len(all_products)}")
        say(f"Produk setelah deduplikasi: {len(products)}")
        say(f"Promo aktif/estimasi      : {len(active)}")
        say(f"Baris perlu review        : {len(review)}")
        say(f"Referensi gambar katalog  : {len(all_images)}")
        say(f"Kandidat non-produk ditolak: {len(REJECTED_PRODUCT_CANDIDATES)}")
        say(f"Data teknis tersimpan     : {INTERNAL_DIR.name}/")
        say(f"Katalog akhir             : {OUTPUT_DIR.name}/Katalog_Promo_Final.xlsx dan .csv")
    finally:
        session.close()


if __name__ == "__main__":
    main()
