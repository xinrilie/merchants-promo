from __future__ import annotations

"""Shared campaign/date/discount utilities used by the three aggregator sources.

This module contains parsing primitives only; it performs no network access and
has no dependency on a particular source adapter.
"""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TODAY = datetime.now(ZoneInfo("Asia/Jakarta")).date()



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
    ("Papaya Fresh Gallery", ("papaya fresh gallery", "papaya fresh")),
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


def timestamp() -> str:
    return datetime.now(ZoneInfo("Asia/Jakarta")).isoformat()


def normalize_text(value) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


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


def promo_status(
    valid_from: str | None,
    valid_until: str | None,
    period_precision: str | None = None,
    today: date | None = None,
) -> str:
    """Pisahkan status yang terkonfirmasi dari status yang hanya estimasi."""
    today = today or TODAY
    if not valid_from or not valid_until:
        return "unknown"
    try:
        start = date.fromisoformat(valid_from)
        end = date.fromisoformat(valid_until)
    except (ValueError, TypeError):
        return "unknown"
    if end < start:
        return "unknown"
    if today < start:
        return "upcoming_estimated" if period_precision in {"month", "until"} else "upcoming"
    if today > end:
        return "expired"
    if period_precision in {"month", "until"}:
        return "active_estimated"
    if period_precision in {"range", "single_day"}:
        return "active"
    return "active_estimated"
