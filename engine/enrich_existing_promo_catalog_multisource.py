from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, OrderedDict, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import math

from promo_names import (
    clean_product_name, name_variants, product_name_similarity,
    product_names_can_auto_merge, product_name_block_key,
    product_name_size_signature, product_name_variant_signature,
)
from promo_product_validation import PRODUCT_SPEC_RE, product_candidate_rejection_reason, product_candidate_review_reason
from promo_multisource_collector import (
    infer_merchant_dynamic,
    parse_explicit_discount_amount,
    has_explicit_price_pair,
)
from promo_evidence import find_amount_evidence, find_discount_evidence, extract_period_evidence, period_certainty, source_verification_label
from promo_categories import CATEGORY_TAXONOMY, CATEGORY_NAMES, classify_promo_category
from promo_campaign_utils import (
    TODAY, MERCHANT_ALIASES, campaign_type, discount_fields, normalize_text,
    parse_explicit_discount, parse_period_details, promo_status, timestamp,
)
from promo_price_reference import (
    PRICE_REFERENCE_MAX_AGE_DAYS, REFERENCE_FIELDS, load_reference_database, update_reference_database,
    find_reference_price, EXPLICIT_NORMAL_PRICE_LABEL_RE,
)
from promo_market_prices import (
    MARKET_REFERENCE_CSV, load_market_reference_database, compare_market_prices,
)
from promo_poster_overrides import apply_reviewed_poster_prices, DEFAULT_OVERRIDES_CSV

ENGINE_DIR = Path(__file__).resolve().parent
ROOT_DIR = ENGINE_DIR.parent
INPUT_DIR = ROOT_DIR / "input"
INTERNAL_DIR = ROOT_DIR / "_internal"
OUTPUT_DIR = ROOT_DIR / "hasil"
MULTISOURCE_CSV = INTERNAL_DIR / "promo_multisource_products_all.csv"  # hasil collector tiga sumber aktif
MULTISOURCE_RUN_LOG = INTERNAL_DIR / "promo_multisource_runlog.json"
OUT_ACTIVE = OUTPUT_DIR / "Katalog_Promo_Final.csv"
OUT_CONFIRMED = INTERNAL_DIR / "promo_catalog_multisource_confirmed.csv"
OUT_REVIEW = INTERNAL_DIR / "promo_catalog_multisource_review.csv"
OUT_FULL = INTERNAL_DIR / "promo_catalog_multisource_full_audit.csv"
OUT_REPORT = INTERNAL_DIR / "promo_catalog_multisource_quality_report.json"
OUT_GATE_REPORT = INTERNAL_DIR / "promo_export_quality_gate_report.json"
OUT_POSSIBLE_DUPES = INTERNAL_DIR / "promo_possible_duplicate_candidates.csv"
OUT_XLSX = OUTPUT_DIR / "Katalog_Promo_Final.xlsx"
# Mirrors the simple 13-column "Promo" sheet exactly; the existing main CSV
# intentionally remains the wider machine-readable catalog with provenance.
OUT_SIMPLE_CSV = OUTPUT_DIR / "Katalog_Promo_Final_Ringkas.csv"
OUT_REJECTED_PRODUCTS = INTERNAL_DIR / "promo_product_rows_rejected.csv"
CATEGORY_REVIEW_LIST_CSV = INPUT_DIR / "category_review_list.csv"
PRICE_REFERENCE_CSV = ROOT_DIR / "data" / "product_price_reference.csv"
MARKET_CANDIDATES_CSV = ROOT_DIR / "data" / "product_market_price_candidates.csv"

FIELDS = [
    "product_text", "original_product_text", "product_name_variants", "product_category", "product_subcategory",
    "previous_product_category", "previous_product_subcategory", "merchant_name", "campaign_title", "campaign_type",
    "promo_status", "valid_from", "valid_until", "period_precision",
    "period_flags", "matched_period_text", "period_evidence_text", "period_evidence_source", "period_certainty", "published_at",
    "promo_price", "normal_price", "normal_price_provenance", "reference_normal_price",
    "reference_price_source_name", "reference_price_merchant_name", "reference_price_match_scope",
    "reference_price_source_url", "reference_price_evidence_text", "reference_price_observed_at",
    "reference_price_age_days", "reference_price_match_status", "reference_price_record_origin",
    "reference_price_low", "reference_price_high", "reference_price_unit", "reference_price_note",
    "reference_primary_display_allowed",
    "market_reference_price", "market_reference_low", "market_reference_high",
    "market_reference_source_count", "market_reference_sources", "market_reference_source_urls",
    "market_reference_observed_at", "market_reference_status", "market_reference_below_median_percent",
    "market_reference_savings", "market_reference_confidence", "market_reference_note",
    "discount_percent", "discount_percent_basis",
    "discount_percent_confidence", "discount_percent_calculated", "discount_savings_rp",
    "estimated_bundle_discount_percent", "campaign_discount_percent",
    "promo_price_evidence_text", "normal_price_evidence_text", "discount_evidence_text",
    "campaign_discount_basis", "price_parse_method", "price_basis",
    "purchase_condition", "deal_type", "offer_text", "section",
    "source_url", "source_urls", "source_campaign_titles", "source_dataset",
    "source_name", "source_names", "source_type", "verification_status", "source_verification_label", "merchant_resolution_method",
    "source_page_url", "source_item_url", "region_or_channel", "scraped_at", "freshness_status",
    "source_discount_claim_percent",
    "duplicate_count", "duplicate_group_id", "cross_dataset_duplicate", "overlapping_offer_count",
    "overlap_period_flag", "price_quality_flag", "data_quality_flags", "retained_from_previous_run", "raw_line",
    "product_validation_status", "product_rejection_reason", "product_review_reason", "export_gate_flags",
]


def as_decimal(value):
    """Parse angka desimal lokal seperti 25,5 atau 25.5 tanpa menghapus desimal."""
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return None
        return Decimal(str(value))

    text = str(value).strip()
    text = re.sub(r"(?i)\b(?:rp|idr)\b", "", text)
    text = text.replace("%", "").replace(" ", "")
    text = re.sub(r"[^0-9.,+\-]", "", text)
    if not text or not re.search(r"\d", text):
        return None

    # Jika titik dan koma sama-sama ada, pemisah paling kanan dianggap desimal.
    if "." in text and "," in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        # Untuk persen, koma tunggal adalah desimal; ribuan tidak relevan.
        text = text.replace(",", ".")

    try:
        number = Decimal(text)
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def as_int(value):
    """Parse nominal rupiah Indonesia: 129.900 -> 129900, 1.299.900,00 -> 1299900."""
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    text = str(value).strip()
    text = re.sub(r"(?i)\b(?:rp|idr)\b", "", text)
    text = text.replace(" ", "")
    text = re.sub(r"[^0-9.,+\-]", "", text)
    if not text or not re.search(r"\d", text):
        return None

    if "." in text and "," in text:
        # Format Indonesia: 1.299.900,00; format internasional: 1,299.00.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "." in text or "," in text:
        separator = "." if "." in text else ","
        if text.count(separator) > 1:
            # Pemisah berulang pada nominal dianggap pemisah ribuan.
            text = text.replace(separator, "")
        else:
            left, right = text.split(separator, 1)
            if len(right) == 3 and left.lstrip("+-").isdigit():
                # Penulisan rupiah Indonesia seperti 129.900.
                text = left + right
            elif len(right) in (1, 2):
                # Desimal nominal, bila ada, dibulatkan menjadi rupiah utuh.
                text = left + "." + right
            else:
                text = left + right

    try:
        number = Decimal(text)
        if not number.is_finite():
            return None
        return int(number.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None


FRESHNESS_MAX_AGE_DAYS = 7
FRESHNESS_DISPLAY_LABELS = {
    "fresh": "Segar (≤7 hari)",
    "preserved_previous": "Katalog lama dipertahankan",
    "stale": "Usang (>7 hari)",
    "historical_or_unverified": "Historis / timestamp tidak tersedia",
    "invalid_timestamp": "Timestamp tidak valid/masa depan",
}


def classify_row_freshness(scraped_at, retained_from_previous_run=False, now_dt=None):
    """Classify one row's recency independently from its source verification."""
    if truthy(retained_from_previous_run):
        return "preserved_previous"
    stamp = str(scraped_at or "").strip()
    if not stamp:
        return "historical_or_unverified"
    now_dt = now_dt or datetime.now().astimezone()
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        age_seconds = (now_dt - parsed.astimezone(now_dt.tzinfo)).total_seconds()
        if age_seconds < 0:
            return "invalid_timestamp"
        if age_seconds > FRESHNESS_MAX_AGE_DAYS * 86400:
            return "stale"
        return "fresh"
    except (TypeError, ValueError, OverflowError):
        # A missing timestamp is historical/unverified; a present but malformed
        # timestamp is a separate data-quality condition and must be visible as such.
        return "invalid_timestamp"


def combine_freshness_statuses(statuses):
    """Combine provenance recency from equivalent rows without overstating freshness."""
    flattened = []
    for value in statuses:
        value = str(value or "").strip()
        if not value:
            continue
        if value.startswith("mixed:"):
            flattened.extend(part for part in value.partition(":")[2].split("|") if part)
        else:
            flattened.append(value)
    values = list(OrderedDict.fromkeys(flattened))
    if not values:
        return "historical_or_unverified"
    if len(values) == 1:
        return values[0]
    order = ["fresh", "preserved_previous", "stale", "historical_or_unverified", "invalid_timestamp"]
    ordered = [value for value in order if value in values] + [value for value in values if value not in order]
    labels = [FRESHNESS_DISPLAY_LABELS.get(value, value) for value in ordered]
    return "mixed:" + "|".join(ordered)


def freshness_display_label(status):
    status = str(status or "historical_or_unverified")
    if status.startswith("mixed:"):
        parts = status.partition(":")[2].split("|")
        return "Campuran: " + " + ".join(FRESHNESS_DISPLAY_LABELS.get(part, part) for part in parts)
    return FRESHNESS_DISPLAY_LABELS.get(status, FRESHNESS_DISPLAY_LABELS["historical_or_unverified"])


def latest_scrape_timestamp(rows):
    parsed_values = []
    for row in rows:
        stamp = str(row.get("scraped_at") or "").strip()
        if not stamp:
            continue
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.astimezone()
            parsed_values.append((parsed.timestamp(), stamp))
        except (TypeError, ValueError, OverflowError, OSError):
            continue
    return max(parsed_values)[1] if parsed_values else None


def norm_key(value) -> str:
    value = normalize_text(value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def pipe_unique(values):
    result = []
    for value in values:
        if value in (None, ""):
            continue
        for piece in str(value).split(" | "):
            piece = piece.strip()
            if piece and piece not in result:
                result.append(piece)
    return result


def truthy(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "ya", "y"}


GENERIC_MERCHANT_VALUES = {"", "lainnya", "other", "unknown", "null", "none", "n/a", "belum diketahui"}


def _merchant_text_normalize(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).split())


def _known_merchants_in_text(value):
    """Return canonical known merchant aliases found as whole phrases in one context."""
    text = _merchant_text_normalize(value)
    if not text:
        return set()
    hits = set()
    aliases = []
    for canonical, alias_values in MERCHANT_ALIASES:
        for alias in alias_values:
            normalized_alias = _merchant_text_normalize(alias)
            if normalized_alias:
                aliases.append((len(normalized_alias.split()), len(normalized_alias), canonical, normalized_alias))
    # Prefer long phrases; the returned set keeps the result conservative when a
    # title contains both a merchant and a named payment/partner brand.
    aliases.sort(reverse=True)
    for _, _, canonical, alias in aliases:
        if re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", text):
            hits.add(canonical)
    return hits


def _path_text(url):
    try:
        from urllib.parse import urlparse
        return _merchant_text_normalize(urlparse(str(url or "")).path.replace("-", " ").replace("_", " "))
    except (TypeError, ValueError):
        return ""


def infer_merchant_from_context(title, source_url=None, source_urls=None, source_titles=None):
    """Resolve merchant only when title/URL evidence points to one consistent brand.

    A generic stored label such as `Lainnya` is never retained as if it were a
    verified merchant. Conflicting context stays unresolved for the quality gate.
    """
    title_text = str(title or "").strip()
    url_values = pipe_unique([source_url or "", source_urls or ""])
    title_values = pipe_unique([title_text, source_titles or ""])
    # Pipe-delimited merged titles/URLs are checked individually, not as a blob.
    contexts = []
    if title_text:
        contexts.append(("campaign_title", _known_merchants_in_text(title_text)))
    for candidate_url in url_values:
        path_text = _path_text(candidate_url)
        if path_text:
            contexts.append(("source_url", _known_merchants_in_text(path_text)))
    for title_blob in title_values:
        for candidate_title in re.split(r"\s*\|\s*", title_blob):
            if candidate_title and candidate_title != title_text:
                contexts.append(("source_campaign_titles", _known_merchants_in_text(candidate_title)))
    # A single context that names exactly one merchant is strong evidence. All
    # other single-merchant contexts must agree with it.
    single_hits = [(kind, next(iter(hits))) for kind, hits in contexts if len(hits) == 1]
    multiple_hits = [hits for _, hits in contexts if len(hits) > 1]
    if single_hits:
        candidates = {name for _, name in single_hits}
        if len(candidates) == 1 and not multiple_hits:
            chosen = next(iter(candidates))
            source_kinds = {kind for kind, name in single_hits if name == chosen}
            if "source_url" in source_kinds and "campaign_title" in source_kinds:
                return chosen, "title_url_known_alias"
            return chosen, "known_alias_from_title_or_url"
        # The URL slug can disambiguate an article title listing a partner and
        # merchant only when its own slug identifies exactly one of those names.
        url_hits = [name for kind, name in single_hits if kind == "source_url"]
        if len(set(url_hits)) == 1 and all(name in set.union(set(), *multiple_hits) for name in url_hits):
            non_url = {name for kind, name in single_hits if kind != "source_url"}
            if not non_url or non_url == set(url_hits):
                return url_hits[0], "source_url_known_alias"
        return None, "conflicting_merchant_context"
    if multiple_hits:
        return None, "conflicting_merchant_context"
    # Preserve support for a genuinely new retailer when a campaign title has a
    # clear promo/catalog prefix but its merchant is not in the alias table.
    inferred = infer_merchant_dynamic(title_text, source_url or "", "")
    if inferred and _merchant_text_normalize(inferred) not in {_merchant_text_normalize(v) for v in GENERIC_MERCHANT_VALUES}:
        return inferred, "campaign_title_inference"
    return None, "merchant_unresolved"


def canonical_merchant(value, title, source_url=None, source_urls=None, source_titles=None):
    raw_value = (value or "").strip()
    if raw_value.casefold() not in GENERIC_MERCHANT_VALUES:
        canonical_map = {
            "superindo": "Super Indo",
            "super indo": "Super Indo",
            "mcdonalds": "McDonald's",
            "mcdonald's": "McDonald's",
            "jco": "J.CO",
            "j.co": "J.CO",
            "lotte mart": "Lotte Mart",
        }
        normalized = _merchant_text_normalize(raw_value)
        known = _known_merchants_in_text(raw_value)
        if len(known) == 1 and normalized == _merchant_text_normalize(next(iter(known))):
            return next(iter(known)), "source_merchant_field"
        return canonical_map.get(raw_value.casefold(), raw_value), "source_merchant_field"
    inferred, method = infer_merchant_from_context(title, source_url, source_urls, source_titles)
    return inferred, method


def canonical_campaign_type(value, title):
    value = (value or "").strip()
    mapping = {
        "promo": "Promo Umum",
        "promo umum": "Promo Umum",
        "katalog": "Katalog Promo",
        "katalog promo": "Katalog Promo",
        "weekend": "Weekend Deals",
        "kupon": "Kupon / Voucher",
    }
    if value:
        return mapping.get(value.casefold(), value)
    return campaign_type(title or "")


def derive_deal_type(offer_text, existing=None):
    if existing:
        return existing
    text = normalize_text(offer_text)
    patterns = [
        (r"\bbeli\s*\d+\s*gratis\s*\d+|\bbuy\s*\d+\s*get\s*\d+", "Beli Gratis"),
        (r"\bdiskon\s*\d+(?:[.,]\d+)?\s*%|\bpotongan(?:\s+harga)?\s*\d+\s*%", "Diskon Persentase"),
        (r"\btebus\s+murah\b", "Tebus Murah"),
        (r"\bcashback\b", "Cashback"),
        (r"\bvoucher\b|\bkupon\b", "Kupon / Voucher"),
        (r"\btambah\s+rp\b", "Additional Payment"),
        (r"\bbeli\s+\d+\b", "Bundle Purchase"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, text):
            return label
    return None


def period_meta(row, title):
    """Pertahankan tingkat kepastian tanggal; jangan menebak range dari dua tanggal saja."""
    start = (row.get("valid_from") or "").strip() or None
    end = (row.get("valid_until") or "").strip() or None
    precision = (row.get("period_precision") or "").strip().casefold()
    flags = pipe_unique([row.get("period_flags") or ""])
    matched = (row.get("matched_period_text") or "").strip() or None

    known_precisions = {"range", "single_day", "month", "until", "unverified", "conflict"}
    if precision not in known_precisions:
        precision = "unknown"

    title_meta = parse_period_details(title or "")
    title_precision = title_meta.get("period_precision", "unknown")
    exact_precisions = {"range", "single_day"}

    # Judul dengan tanggal eksplisit merupakan bukti yang bisa ditelusuri.
    # Jika tanggal lama berbeda, gunakan tanggal judul tetapi tandai konfliknya.
    if title_precision in exact_precisions:
        title_start = title_meta.get("valid_from")
        title_end = title_meta.get("valid_until")
        if start and end and (start != title_start or end != title_end):
            flags = pipe_unique([*flags, "title_period_mismatch_with_catalog_dates"])
            start, end = title_start, title_end
            precision = title_precision
            matched = title_meta.get("matched_period_text") or matched
        elif precision in {"unknown", "unverified", "conflict"} or not start or not end:
            start, end = title_start, title_end
            precision = title_precision
            matched = title_meta.get("matched_period_text") or matched
        elif not precision:
            precision = title_precision
    elif title_precision == "month" and precision == "unknown":
        # Rentang satu bulan tetap berstatus estimasi, tidak dianggap tanggal pasti.
        title_start = title_meta.get("valid_from")
        title_end = title_meta.get("valid_until")
        if start and end and (start != title_start or end != title_end):
            flags = pipe_unique([*flags, "title_period_mismatch_with_catalog_dates"])
        start, end = title_start, title_end
        precision = "month"
        matched = title_meta.get("matched_period_text") or matched

    # Tanggal pada CSV tanpa metadata asal tidak boleh otomatis dianggap range pasti.
    if precision == "unknown" and start and end:
        precision = "unverified"
        flags = pipe_unique([*flags, "period_precision_missing_source_evidence"])
    elif precision == "unknown" and (start or end):
        flags = pipe_unique([*flags, "incomplete_period_dates"])

    # Periode berakhir pada suatu tanggal tanpa awal yang jelas memakai tanggal publikasi
    # hanya sebagai lower bound dan tetap berstatus estimasi.
    if precision == "until" and not start and row.get("published_at"):
        m = re.search(r"\b(\d{4}-\d{2}-\d{2})", str(row.get("published_at")))
        if m:
            try:
                pub_date = date.fromisoformat(m.group(1))
                end_date = date.fromisoformat(end) if end else None
                if end_date and pub_date <= end_date:
                    start = pub_date.isoformat()
                    flags = pipe_unique([*flags, "start_inferred_from_published_at"])
            except ValueError:
                pass

    return start, end, precision or "unknown", " | ".join(flags) or None, matched


def current_status(start, end, precision, period_flags=None, source_status=None):
    # A card with only an explicit "Sampai DD MMM YYYY" end date may still be
    # current, but it is an estimate because the starting date is not known.
    if not end:
        return "expired" if source_status == "expired" else "unknown"
    if not start:
        try:
            end_date = date.fromisoformat(end)
        except (TypeError, ValueError):
            return "unknown"
        if end_date < TODAY:
            return "expired"
        if (precision or "").casefold() == "until":
            return "active_estimated"
        return "unknown"
    status = promo_status(start, end, precision, today=TODAY)
    if (precision or "").casefold() == "unverified" and status == "upcoming":
        status = "upcoming_estimated"
    flags = set(pipe_unique([period_flags or ""]))
    material_conflict = any(
        marker in flags
        for marker in (
            "title_lead_period_differs",
            "title_period_mismatch_with_catalog_dates",
            "source_dates_differ_from_title_period",
        )
    )
    if material_conflict and status == "active":
        return "active_estimated"
    if material_conflict and status == "upcoming":
        return "upcoming_estimated"
    return status


def campaign_discount(title):
    value, basis = parse_explicit_discount(title or "")
    if value is not None:
        return value, f"title_{basis}"
    # Keep soft headline claims visible, but don't apply them to every SKU as
    # a product discount.
    m = re.search(r"diskon\s+hampir\s+(\d{1,2}(?:[.,]\d+)?)\s*%", normalize_text(title or ""))
    if m:
        return float(m.group(1).replace(",", ".")), "title_approximate_claim"
    return None, None


UNIT_CONTEXT_PATTERN = re.compile(
    r"(?:/\s*(?:\d+(?:[.,]\d+)?\s*)?(?:gr|gram|g|kg|ml|liter|litre|ltr|l)\b"
    r"|\bper\s+(?:\d+(?:[.,]\d+)?\s*)?(?:gr|gram|g|kg|ml|liter|litre|ltr|l)\b)",
    re.IGNORECASE,
)


GIFT_MARKER_RE = re.compile(
    r"\b(?:gratis|free|bonus|hadiah(?:\s+berupa)?|dapat\s+gratis)\s+([^,;|.]{1,90})",
    re.IGNORECASE,
)
GIFT_STOP_RE = re.compile(
    r"\b(?:dengan|senilai|seharga|hingga|sampai|diskon|potongan|hemat|harga|rp|periode|"
    r"berlaku|min(?:imum)?|minimal|ketentuan|cashback|voucher|kupon|cash\s*back|qris)\b",
    re.IGNORECASE,
)
SKU_TOKEN_STOPWORDS = {
    "beli", "buy", "get", "gratis", "free", "bonus", "hadiah", "berupa", "dapat",
    "produk", "item", "unit", "pcs", "pc", "buah", "pack", "pak", "box", "dus",
    "kemasan", "ukuran", "varian", "isi", "dengan", "untuk", "harga", "promo",
    "normal", "diskon", "potongan", "hemat", "hingga", "sampai", "rp", "idr",
    "per", "setiap", "ekstra", "tambahan", "dan", "atau", "di", "ke", "dari",
    "ongkir", "pengiriman", "shipping", "delivery", "voucher", "kupon", "cashback",
    "reward", "poin", "saldo", "qris", "pembayaran", "transaksi", "minimum", "minimal",
}


def _sku_identity_tokens(value):
    text = normalize_text(str(value or ""))
    tokens = re.findall(r"[a-z]+", text)
    return {token for token in tokens if token not in SKU_TOKEN_STOPWORDS and len(token) > 1}


def cross_sku_gift_description(row):
    """Return explicit gift-SKU text when it differs from the product on the row.

    Generic mechanics such as `Beli 1 Gratis 1` have no gift SKU name and are not
    classified as cross-SKU. This is intentionally conservative.
    """
    product_tokens = _sku_identity_tokens(row.get("product_text") or row.get("original_product_text"))
    if not product_tokens:
        return None
    text_parts = [row.get(k) or "" for k in ("offer_text", "purchase_condition", "deal_type", "raw_line")]
    seen = set()
    for text in text_parts:
        for match in GIFT_MARKER_RE.finditer(str(text)):
            description = match.group(1).strip(" :-–—")
            stop = GIFT_STOP_RE.search(description)
            if stop:
                description = description[:stop.start()].strip(" :-–—")
            gift_tokens = _sku_identity_tokens(description)
            if not gift_tokens:
                continue
            signature = tuple(sorted(gift_tokens))
            if signature in seen:
                continue
            seen.add(signature)
            union = product_tokens | gift_tokens
            similarity = len(product_tokens & gift_tokens) / len(union) if union else 1.0
            # Any named item whose SKU tokens materially differ from the row SKU
            # is treated as a separate item, even if its category/brand overlaps.
            if similarity < 0.80:
                return description
    return None


def safe_discount_fields(offer_text, promo_price, normal_price, price_basis,
                         purchase_condition=None, deal_type=None, quality_flags=None,
                         product_text=None, original_product_text=None, raw_line=None):
    """Hitung diskon konservatif dan pisahkan hadiah lintas-SKU dari diskon produk."""
    flags = quality_flags if quality_flags is not None else []
    result = discount_fields(
        offer_text=offer_text,
        promo_price=promo_price,
        normal_price=normal_price,
        price_basis=price_basis,
        purchase_condition=purchase_condition,
        deal_type=deal_type,
        quality_flags=flags,
    )
    if (UNIT_CONTEXT_PATTERN.search(str(offer_text or ""))
            and result.get("discount_percent_basis") == "calculated_from_unit_prices"):
        result["discount_percent"] = None
        result["discount_percent_calculated"] = None
        result["discount_savings_rp"] = None
        result["discount_percent_basis"] = "unit_context_review"
        result["discount_percent_confidence"] = "unknown"
        flags.append("price_unit_context_requires_review")

    gift_row = {
        "product_text": product_text,
        "original_product_text": original_product_text,
        "offer_text": offer_text,
        "purchase_condition": purchase_condition,
        "deal_type": deal_type,
        "raw_line": raw_line,
    }
    gift_description = cross_sku_gift_description(gift_row)
    if gift_description and result.get("discount_percent_basis") == "bundle_estimate_same_item_value":
        result["discount_percent"] = None
        result["discount_percent_calculated"] = None
        result["discount_savings_rp"] = None
        result["estimated_bundle_discount_percent"] = None
        result["discount_percent_basis"] = "cross_sku_bundle_review"
        result["discount_percent_confidence"] = "unknown"
        flags.append("cross_sku_gift_not_product_discount")
    return result


def _validated_value_evidence(raw, amount, evidence_field, kind, fallback_texts):
    """Return matching source evidence; reconstruct from visible offer text if possible."""
    evidence = str(raw.get(evidence_field) or "").strip()
    if amount in (None, ""):
        return evidence or None, None
    matching = find_amount_evidence(evidence, amount, kind) if evidence else None
    if matching:
        return matching, None
    for source_text in fallback_texts:
        matching = find_amount_evidence(source_text, amount, kind)
        if matching:
            return matching, f"{evidence_field}_reconstructed_from_visible_offer_text"
    return None, f"{evidence_field}_missing_or_mismatched"


def _period_provenance(raw, title, offer, raw_line, start, end, precision, flags, matched):
    evidence = str(raw.get("period_evidence_text") or "").strip() or matched
    evidence_source = str(raw.get("period_evidence_source") or "").strip() or None
    if evidence:
        extracted, extracted_source = extract_period_evidence(title, " ".join([offer, raw_line, evidence]), precision, evidence)
        if extracted:
            evidence = extracted
            evidence_source = evidence_source or extracted_source
        else:
            evidence = None
            evidence_source = None
    if not evidence:
        evidence, evidence_source = extract_period_evidence(
            title, " ".join([offer, raw_line, str(raw.get("published_at") or "")]), precision
        )
    if precision in {"range", "single_day"} and not evidence:
        precision = "unverified"
        flags = pipe_unique([*flags, "period_evidence_missing_unverified"])
    return evidence or None, evidence_source, precision, flags


def load_source(path, source_label):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    output = []
    for original in rows:
        raw = dict(original)
        title = (raw.get("campaign_title") or "").strip()
        merchant, merchant_resolution_method = canonical_merchant(
            raw.get("merchant_name") or raw.get("merchant"),
            title,
            raw.get("source_url") or raw.get("source_page_url") or raw.get("source_item_url"),
            raw.get("source_urls"),
            raw.get("source_campaign_titles"),
        )
        original_product = (raw.get("original_product_text") or raw.get("product_text") or "").strip()
        source_product = (raw.get("product_text") or "").strip()
        product = clean_product_name(source_product or original_product)
        # Some earlier parsers accidentally truncated a long SKU to its first word
        # (e.g. "Pasti" from "Pasti Hemat 1000 sheet Facial Tissue"). Recover the
        # richer original label only when it carries explicit item-size evidence;
        # do not restore longer editorial/campaign snippets indiscriminately.
        original_clean = clean_product_name(original_product)
        product_tokens = re.findall(r"[a-z0-9]+", product.casefold())
        original_tokens = re.findall(r"[a-z0-9]+", original_clean.casefold())
        if (
            len(product_tokens) <= 2
            and len(original_tokens) >= 4
            and not PRODUCT_SPEC_RE.search(product)
            and PRODUCT_SPEC_RE.search(original_clean)
        ):
            product = original_clean
        offer = (raw.get("offer_text") or raw.get("detail") or "").strip()
        raw_line = (raw.get("raw_line") or "").strip()
        if not offer:
            offer = raw_line
        promo = as_int(raw.get("promo_price"))
        normal = as_int(raw.get("normal_price"))
        basis = (raw.get("price_basis") or "").strip()
        purchase_condition = (raw.get("purchase_condition") or "").strip() or None
        # Older collector output sometimes stored "Diskon RpX" as if it were
        # the selling price. Repair such rows at ingestion, before price gates.
        explicit_savings = parse_explicit_discount_amount(offer)
        is_explicit_amount_discount = bool(
            explicit_savings is not None
            and not has_explicit_price_pair(offer)
            and len(re.findall(r"\\brp\\.?\\s*[0-9][0-9.,]*", offer, re.I)) == 1
        )
        if is_explicit_amount_discount:
            promo = None
            normal = None
            basis = "discount_amount_offer"
            if not purchase_condition:
                condition_match = re.search(
                    r"\\bbeli\\s*\\d+(?:\\s+(?:pcs?|pack|item|pc))?",
                    offer,
                    re.I,
                )
                purchase_condition = clean_product_name(condition_match.group(0)) if condition_match else None
        deal_type = "Potongan Nominal" if is_explicit_amount_discount else derive_deal_type(offer, raw.get("deal_type"))

        start, end, precision, period_flags, matched = period_meta(raw, title)
        period_flags_list = pipe_unique([period_flags or ""])
        period_evidence, period_evidence_source, precision, period_flags_list = _period_provenance(
            raw, title, offer, raw_line, start, end, precision, period_flags_list, matched
        )
        period_flags = " | ".join(period_flags_list) or None
        status = current_status(start, end, precision, period_flags, raw.get("promo_status"))

        # Calculate product-level discount from the SKU's offer text only.
        quality_flags = pipe_unique([raw.get("price_quality_flag") or ""])
        if is_explicit_amount_discount:
            quality_flags = [
                flag for flag in quality_flags
                if flag not in {
                    "bundle_total_not_unit_price",
                    "promo_price_parse_failed_or_unverified",
                    "promo_price_without_matching_offer_evidence",
                }
            ]
        provenance_review_reasons = []
        evidence_fallbacks = [offer, raw_line]
        promo_evidence, promo_evidence_issue = _validated_value_evidence(
            raw, promo, "promo_price_evidence_text", "promo", evidence_fallbacks
        )
        normal_evidence, normal_evidence_issue = _validated_value_evidence(
            raw, normal, "normal_price_evidence_text", "normal", evidence_fallbacks
        )
        if promo_evidence_issue == "promo_price_evidence_text_reconstructed_from_visible_offer_text":
            quality_flags = pipe_unique([*quality_flags, promo_evidence_issue])
        elif promo_evidence_issue:
            promo = None
            quality_flags = pipe_unique([*quality_flags, "promo_price_without_matching_offer_evidence"])
            provenance_review_reasons.append("Harga promo tidak memiliki bukti teks yang cocok")
        if normal_evidence_issue == "normal_price_evidence_text_reconstructed_from_visible_offer_text":
            quality_flags = pipe_unique([*quality_flags, normal_evidence_issue])
        elif normal_evidence_issue:
            normal = None
            quality_flags = pipe_unique([*quality_flags, "normal_price_without_matching_offer_evidence"])
            provenance_review_reasons.append("Harga normal tidak memiliki bukti teks yang cocok")
        if raw.get("promo_price") not in (None, "") and promo is None and not is_explicit_amount_discount:
            quality_flags = pipe_unique([*quality_flags, "promo_price_parse_failed_or_unverified"])
        if raw.get("normal_price") not in (None, "") and normal is None:
            quality_flags = pipe_unique([*quality_flags, "normal_price_parse_failed_or_unverified"])
        if promo is not None and normal is not None and promo > normal:
            quality_flags = pipe_unique([*quality_flags, "promo_price_above_normal_review"])
        discount = safe_discount_fields(
            offer_text=offer,
            promo_price=promo,
            normal_price=normal,
            price_basis=basis,
            purchase_condition=purchase_condition,
            deal_type=deal_type,
            quality_flags=quality_flags,
            product_text=product,
            original_product_text=original_product,
            raw_line=raw_line,
        )
        if is_explicit_amount_discount:
            discount["discount_percent"] = None
            discount["discount_percent_calculated"] = None
            discount["discount_percent_basis"] = "explicit_amount_discount"
            discount["discount_percent_confidence"] = "unknown"
            discount["discount_savings_rp"] = explicit_savings
            discount["discount_evidence_text"] = offer
        campaign_disc, campaign_disc_basis = campaign_discount(title)
        discount_evidence = str(raw.get("discount_evidence_text") or "").strip() or None
        if discount["discount_percent"] not in (None, ""):
            if discount["discount_percent_basis"] in {"explicit_offer", "explicit_max"}:
                discount_evidence = find_discount_evidence(offer, discount["discount_percent"])
                if not discount_evidence:
                    discount["discount_percent"] = None
                    discount["discount_percent_basis"] = "not_available"
                    discount["discount_percent_confidence"] = "unknown"
                    quality_flags = pipe_unique([*quality_flags, "discount_without_matching_offer_evidence"])
                    provenance_review_reasons.append("Diskon persen tidak memiliki bukti pada teks penawaran produk")
            elif discount["discount_percent_basis"] == "calculated_from_unit_prices":
                if promo is not None and normal is not None and promo_evidence and normal_evidence:
                    discount_evidence = f"Harga normal: {normal_evidence} | Harga promo: {promo_evidence}"
                else:
                    discount["discount_percent"] = None
                    discount["discount_percent_calculated"] = None
                    discount["discount_savings_rp"] = None
                    discount["discount_percent_basis"] = "not_available"
                    discount["discount_percent_confidence"] = "unknown"
                    quality_flags = pipe_unique([*quality_flags, "calculated_discount_without_two_price_evidence_values"])
                    provenance_review_reasons.append("Diskon hitungan memerlukan bukti harga normal dan promo")
            elif discount["discount_percent_basis"] == "bundle_estimate_same_item_value":
                discount_evidence = offer if re.search(r"\b(?:beli|buy)\s*\d+.*?(?:gratis|get)\s*\d+", offer, re.I) else None
        if not discount_evidence:
            discount_evidence = None

        source_name_value = (raw.get("source_name") or "").strip()
        source_type_value = (raw.get("source_type") or "").strip()
        if not source_type_value:
            if "promo_multisource" in source_label.casefold() or source_name_value in {"Hemat.id", "Katalogpromosi.com", "Scanharga.com"}:
                source_type_value = "aggregator"
        source_name_value = source_name_value or None
        source_type_value = source_type_value or None
        verification_status_value = (raw.get("verification_status") or "").strip()
        if not verification_status_value:
            if source_type_value == "aggregator" or "promo_multisource" in source_label.casefold():
                verification_status_value = "aggregator_only"
            else:
                verification_status_value = "historical_baseline_unknown"

        scraped_at_value = (raw.get("scraped_at") or "").strip() or None
        retained_value = truthy(raw.get("retained_from_previous_run"))
        row_freshness = classify_row_freshness(scraped_at_value, retained_value)
        price_method = (raw.get("price_parse_method") or "").strip()
        if is_explicit_amount_discount:
            price_method = "explicit_discount_amount_not_product_price"
        if not price_method:
            if promo is not None and normal is not None:
                price_method = "source_prices_with_discount_recalculated"
            elif promo is not None:
                price_method = "source_promo_price_only"
            elif purchase_condition:
                price_method = "non_numeric_bundle_offer"
            else:
                price_method = "not_extracted"

        row = {
            "product_text": product,
            "original_product_text": original_product or None,
            "product_name_variants": (raw.get("product_name_variants") or original_product or product or "").strip() or None,
            # Retain the last source-side category only for a reproducible audit sheet.
            # The public category columns are always recomputed by the current classifier.
            "previous_product_category": (raw.get("product_category") or raw.get("Kategori Produk") or "").strip() or None,
            "previous_product_subcategory": (raw.get("product_subcategory") or raw.get("Subkategori") or "").strip() or None,
            "merchant_name": merchant,
            "campaign_title": title,
            "campaign_type": canonical_campaign_type(raw.get("campaign_type"), title),
            "promo_status": status,
            "valid_from": start,
            "valid_until": end,
            "period_precision": precision,
            "period_flags": period_flags,
            "matched_period_text": matched,
            "period_evidence_text": period_evidence,
            "period_evidence_source": period_evidence_source,
            "period_certainty": period_certainty(precision, status, period_flags, period_evidence),
            "published_at": (raw.get("published_at") or "").strip() or None,
            "promo_price": promo,
            "normal_price": normal,
            "normal_price_provenance": "current_offer_evidence" if normal is not None and normal_evidence else "not_available",
            "reference_normal_price": None,
            "reference_price_source_name": None,
            "reference_price_source_url": None,
            "reference_price_evidence_text": None,
            "reference_price_observed_at": None,
            "reference_price_age_days": None,
            "reference_price_match_status": "not_checked",
            "promo_price_evidence_text": promo_evidence,
            "normal_price_evidence_text": normal_evidence,
            "discount_evidence_text": discount_evidence,
            **discount,
            "campaign_discount_percent": campaign_disc,
            "campaign_discount_basis": campaign_disc_basis,
            "price_parse_method": price_method,
            "price_basis": basis or None,
            "explicit_discount_amount_only": is_explicit_amount_discount,
            "purchase_condition": purchase_condition,
            "deal_type": deal_type,
            "offer_text": offer or None,
            "section": (raw.get("section") or "").strip() or None,
            "source_url": (raw.get("source_url") or "").strip() or None,
            "source_urls": (raw.get("source_urls") or "").strip() or None,
            "source_campaign_titles": (raw.get("source_campaign_titles") or "").strip() or None,
            "source_dataset": source_label,
            "source_name": source_name_value,
            "source_names": (raw.get("source_names") or "").strip() or None,
            "source_type": source_type_value,
            "verification_status": verification_status_value,
            "source_verification_label": source_verification_label(verification_status_value),
            "merchant_resolution_method": (raw.get("merchant_resolution_method") or "").strip() or merchant_resolution_method,
            "source_page_url": (raw.get("source_page_url") or "").strip() or None,
            "source_item_url": (raw.get("source_item_url") or "").strip() or None,
            "region_or_channel": (raw.get("region_or_channel") or "").strip() or None,
            "scraped_at": scraped_at_value,
            "freshness_status": row_freshness,
            "source_discount_claim_percent": None,
            "retained_from_previous_run": retained_value,
            "duplicate_count": as_int(raw.get("duplicate_count")) or 1,
            "cross_dataset_duplicate": False,
            "overlapping_offer_count": 1,
            "overlap_period_flag": None,
            "price_quality_flag": " | ".join(pipe_unique(quality_flags)) or None,
            "data_quality_flags": " | ".join(pipe_unique([
                period_flags or "",
                "missing_merchant_name" if not merchant else "",
                "merchant_inferred_from_title_url" if merchant and merchant_resolution_method in {"title_url_known_alias", "known_alias_from_title_or_url", "source_url_known_alias", "campaign_title_inference"} else "",
                "missing_product_text" if not product else "",
                "missing_source_url" if not raw.get("source_url") else "",
                "retained_from_previous_run" if truthy(raw.get("retained_from_previous_run")) else "",
                "discount_is_estimated" if discount["discount_percent_basis"] == "bundle_estimate_same_item_value" else "",
                *provenance_review_reasons,
            ])) or None,
            "raw_line": raw_line or None,
        }
        row["product_category"], row["product_subcategory"] = classify_promo_category(row)
        validation_context = dict(row)
        validation_context["source_mode"] = " ".join([
            source_label,
            str(row.get("source_name") or ""),
            str(row.get("source_type") or ""),
        ])
        reject_reason = product_candidate_rejection_reason(validation_context, source_mode=validation_context["source_mode"])
        review_reason = None if reject_reason else product_candidate_review_reason(
            validation_context, source_mode=validation_context["source_mode"]
        )
        if not reject_reason and provenance_review_reasons:
            review_reason = " | ".join(pipe_unique([review_reason or "", *provenance_review_reasons]))
        row["product_validation_status"] = "rejected" if reject_reason else ("review" if review_reason else "eligible")
        row["product_rejection_reason"] = reject_reason
        row["product_review_reason"] = review_reason
        if reject_reason:
            row["data_quality_flags"] = " | ".join(pipe_unique([
                row.get("data_quality_flags") or "",
                "excluded_from_catalog:" + reject_reason,
            ])) or None
        elif review_reason:
            row["data_quality_flags"] = " | ".join(pipe_unique([
                row.get("data_quality_flags") or "",
                "product_identity_review:" + review_reason,
            ])) or None
        if review_reason == "short_name_with_payment_offer_needs_review":
            # The parsed amount is a payment/cashback benefit, not an item price.
            # Preserve the original wording in offer_text/raw_line for audit, but
            # clear product price/discount columns so they cannot be mistaken for
            # the selling price of a truncated product name.
            row["promo_price"] = None
            row["normal_price"] = None
            row["promo_price_evidence_text"] = None
            row["normal_price_evidence_text"] = None
            row["discount_evidence_text"] = None
            row["discount_percent"] = None
            row["discount_percent_basis"] = "not_available"
            row["discount_percent_confidence"] = "unknown"
            row["discount_percent_calculated"] = None
            row["discount_savings_rp"] = None
            row["estimated_bundle_discount_percent"] = None
            row["price_basis"] = None
            row["price_parse_method"] = "payment_benefit_amount_not_product_price"
            row["price_quality_flag"] = " | ".join(pipe_unique([
                row.get("price_quality_flag") or "",
                "payment_benefit_amount_not_product_price",
            ])) or None
            row["data_quality_flags"] = " | ".join(pipe_unique([
                row.get("data_quality_flags") or "",
                "payment_benefit_amount_not_product_price",
            ])) or None

        # If name normalization strips the whole candidate (e.g. source phrases
        # like "Potongan harga" or "Harga spesial"), this is not a product SKU.
        # Keep the source row for review/audit but never publish its attached amount
        # as the price of an unidentified product.
        if not product.strip():
            row["product_validation_status"] = "review"
            row["product_rejection_reason"] = None
            row["product_review_reason"] = "Nama produk tidak teridentifikasi setelah pembersihan teks"
            row["product_category"] = "Lainnya / Perlu Review"
            row["product_subcategory"] = "Produk belum teridentifikasi"
            for field in (
                "promo_price", "normal_price", "promo_price_evidence_text",
                "normal_price_evidence_text", "discount_evidence_text",
                "discount_percent", "discount_percent_calculated",
                "discount_savings_rp", "estimated_bundle_discount_percent",
            ):
                row[field] = None
            row["discount_percent_basis"] = "not_available"
            row["discount_percent_confidence"] = "unknown"
            row["price_basis"] = None
            row["price_parse_method"] = "unidentified_amount_not_product_price"
            row["price_quality_flag"] = " | ".join(pipe_unique([
                row.get("price_quality_flag") or "",
                "product_identity_missing_amount_not_product_price",
            ])) or None
            row["data_quality_flags"] = " | ".join(pipe_unique([
                row.get("data_quality_flags") or "",
                "product_identity_missing_after_name_cleaning",
                "product_identity_review:Nama produk tidak teridentifikasi setelah pembersihan teks",
                "product_identity_missing_amount_not_product_price",
            ])) or None
        output.append(row)
    return output


def merge_rows(rows):
    groups = OrderedDict()
    for row in rows:
        key = (
            norm_key(row.get("merchant_name")),
            norm_key(row.get("product_text")),
            norm_key(row.get("offer_text")),
            str(row.get("promo_price") or ""),
            str(row.get("normal_price") or ""),
            str(row.get("valid_from") or ""),
            str(row.get("valid_until") or ""),
            norm_key(row.get("purchase_condition")),
            norm_key(row.get("deal_type")),
        )
        groups.setdefault(key, []).append(row)

    merged = []
    for key, group in groups.items():
        def score(r):
            return sum(bool(r.get(k)) for k in (
                "published_at", "period_precision", "period_flags", "promo_price",
                "normal_price", "discount_percent", "price_parse_method", "price_quality_flag",
                "merchant_name", "source_url",
            ))
        best = dict(max(group, key=score))
        validation_statuses = {str(r.get("product_validation_status") or "eligible") for r in group}
        # Any independent source with stronger product evidence can corroborate an
        # ambiguous label; otherwise keep it routed to review rather than confirmed.
        best["product_validation_status"] = "eligible" if "eligible" in validation_statuses else (
            "review" if "review" in validation_statuses else "rejected"
        )
        review_reasons = pipe_unique([r.get("product_review_reason") or "" for r in group])
        best["product_review_reason"] = " | ".join(review_reasons) or None
        raw_name_variants = []
        for candidate in group:
            raw_name_variants.extend(name_variants(candidate.get("product_name_variants")))
            raw_name_variants.extend(name_variants(candidate.get("original_product_text")))
        raw_name_variants = list(OrderedDict.fromkeys(raw_name_variants))
        best["product_name_variants"] = " | ".join(raw_name_variants) or best.get("product_name_variants")
        if raw_name_variants:
            best["original_product_text"] = raw_name_variants[0]
        for field in FIELDS:
            if best.get(field) in (None, ""):
                for candidate in group:
                    if candidate.get(field) not in (None, ""):
                        best[field] = candidate[field]
                        break

        sources = pipe_unique([r.get("source_dataset") for r in group])
        urls = pipe_unique([r.get("source_url") for r in group] + [r.get("source_urls") for r in group])
        titles = pipe_unique([r.get("campaign_title") for r in group] + [r.get("source_campaign_titles") for r in group])
        best["source_dataset"] = " | ".join(sources)
        best["source_urls"] = " | ".join(urls)
        best["source_campaign_titles"] = " | ".join(titles)
        actual_sources = pipe_unique([r.get("source_name") or "" for r in group] + [r.get("source_names") or "" for r in group])
        best["source_names"] = " | ".join(actual_sources) or None
        best["cross_dataset_duplicate"] = len(sources) > 1 or len(actual_sources) > 1
        source_types = pipe_unique([r.get("source_type") or "" for r in group])
        best["source_type"] = " | ".join(source_types) or None
        verification = pipe_unique([r.get("verification_status") or "" for r in group])
        best["verification_status"] = " | ".join(verification) or None
        best["duplicate_count"] = sum(as_int(r.get("duplicate_count")) or 1 for r in group)
        best["retained_from_previous_run"] = any(truthy(r.get("retained_from_previous_run")) for r in group)
        best["freshness_status"] = combine_freshness_statuses([
            r.get("freshness_status") or classify_row_freshness(r.get("scraped_at"), r.get("retained_from_previous_run"))
            for r in group
        ])
        best["scraped_at"] = latest_scrape_timestamp(group) or best.get("scraped_at")
        best["cross_dataset_duplicate"] = len(sources) > 1
        best["overlapping_offer_count"] = 1
        best["overlap_period_flag"] = None
        best["duplicate_group_id"] = hashlib.sha1("|".join(key).encode("utf-8")).hexdigest()[:12]

        # Recompute discount based on merged fields, never from campaign headline.
        flags = pipe_unique([r.get("price_quality_flag") or "" for r in group])
        discount = safe_discount_fields(
            best.get("offer_text") or "",
            as_int(best.get("promo_price")),
            as_int(best.get("normal_price")),
            best.get("price_basis"),
            best.get("purchase_condition"),
            best.get("deal_type"),
            flags,
            product_text=best.get("product_text"),
            original_product_text=best.get("original_product_text"),
            raw_line=best.get("raw_line"),
        )
        best.update(discount)
        best["price_quality_flag"] = " | ".join(pipe_unique(flags)) or None
        best["data_quality_flags"] = " | ".join(pipe_unique([
            best.get("period_flags") or "",
            "exact_duplicate_collapsed" if best["duplicate_count"] > 1 else "",
            "missing_merchant_name" if not best.get("merchant_name") else "",
            "missing_product_text" if not best.get("product_text") else "",
            "missing_source_url" if not best.get("source_url") else "",
            "retained_from_previous_run" if truthy(best.get("retained_from_previous_run")) else "",
            "discount_is_estimated" if best.get("discount_percent_basis") == "bundle_estimate_same_item_value" else "",
        ])) or None
        merged.append(best)
    return merged


def mark_overlapping_offers(rows):
    """Jangan auto-merge beda periode; tandai untuk pemeriksaan manual."""
    groups = defaultdict(list)
    for idx, row in enumerate(rows):
        key = (
            norm_key(row.get("merchant_name")), norm_key(row.get("product_text")),
            norm_key(row.get("offer_text")), str(row.get("promo_price") or ""),
            str(row.get("normal_price") or ""), norm_key(row.get("purchase_condition")),
            norm_key(row.get("deal_type")),
        )
        groups[key].append(idx)

    for indices in groups.values():
        if len(indices) < 2:
            continue
        parsed = []
        for idx in indices:
            row = rows[idx]
            try:
                a, b = date.fromisoformat(row["valid_from"]), date.fromisoformat(row["valid_until"])
                parsed.append((idx, a, b))
            except (ValueError, TypeError):
                continue
        for idx, start, end in parsed:
            overlaps = [j for j, a, b in parsed if j != idx and start <= b and a <= end]
            rows[idx]["overlapping_offer_count"] = 1 + len(overlaps)
            if overlaps:
                rows[idx]["overlap_period_flag"] = "same_offer_overlapping_dates_keep_separate"
                rows[idx]["data_quality_flags"] = " | ".join(pipe_unique([
                    rows[idx].get("data_quality_flags") or "",
                    "same_offer_overlapping_dates_keep_separate",
                ]))
    return rows



def consolidate_overlapping_offers(rows):
    """Gabungkan offer yang identik dari artikel berbeda jika rentang tanggalnya overlap.

    Hanya rentang presisi (range/single_day) yang digabung. Data month/until yang
    sifatnya estimasi tetap dipisah agar ketidakpastian tanggal tidak disembunyikan.
    """
    groups = defaultdict(list)
    passthrough = []
    for row in rows:
        period_flag_text = str(row.get("period_flags") or "")
        has_period_conflict = any(marker in period_flag_text for marker in (
            "title_lead_period_differs",
            "title_period_mismatch_with_catalog_dates",
            "source_dates_differ_from_title_period",
        ))
        if row.get("period_precision") not in {"range", "single_day"} or has_period_conflict:
            passthrough.append(dict(row))
            continue
        try:
            start = date.fromisoformat(row["valid_from"])
            end = date.fromisoformat(row["valid_until"])
        except (ValueError, TypeError):
            passthrough.append(dict(row))
            continue
        key = (
            norm_key(row.get("merchant_name")), norm_key(row.get("product_text")),
            norm_key(row.get("offer_text")), str(row.get("promo_price") or ""),
            str(row.get("normal_price") or ""), norm_key(row.get("purchase_condition")),
            norm_key(row.get("deal_type")),
        )
        groups[key].append((start, end, dict(row)))

    output = list(passthrough)
    merged_group_count = 0
    merged_source_row_count = 0
    for key, items in groups.items():
        items.sort(key=lambda x: (x[0], x[1]))
        intervals = []
        for start, end, row in items:
            if intervals and start <= intervals[-1]["end"]:
                current = intervals[-1]
                current["end"] = max(current["end"], end)
                current["members"].append(row)
            else:
                intervals.append({"start": start, "end": end, "members": [row]})
        for interval in intervals:
            members = interval["members"]
            # Prefer a source article whose dates match the consolidated interval;
            # this avoids displaying a narrower title over a wider merged date range.
            matching_range_members = []
            for member in members:
                try:
                    member_start = date.fromisoformat(member["valid_from"])
                    member_end = date.fromisoformat(member["valid_until"])
                    if member_start == interval["start"] and member_end == interval["end"]:
                        matching_range_members.append(member)
                except (ValueError, TypeError):
                    pass
            candidate_members = matching_range_members or members
            best = dict(max(candidate_members, key=lambda r: sum(bool(r.get(k)) for k in (
                "published_at", "promo_price", "normal_price", "discount_percent",
                "price_parse_method", "source_url", "merchant_name", "campaign_title",
            ))))
            all_name_variants = []
            for member in members:
                all_name_variants.extend(name_variants(member.get("product_name_variants")))
                all_name_variants.extend(name_variants(member.get("original_product_text")))
            all_name_variants = list(OrderedDict.fromkeys(all_name_variants))
            best["product_name_variants"] = " | ".join(all_name_variants) or best.get("product_name_variants")
            if all_name_variants:
                best["original_product_text"] = all_name_variants[0]
            all_urls = pipe_unique([r.get("source_url") for r in members] + [r.get("source_urls") for r in members])
            all_titles = pipe_unique([r.get("campaign_title") for r in members] + [r.get("source_campaign_titles") for r in members])
            all_datasets = pipe_unique([r.get("source_dataset") for r in members])
            best["source_urls"] = " | ".join(all_urls)
            best["source_campaign_titles"] = " | ".join(all_titles)
            best["source_dataset"] = " | ".join(all_datasets)
            best["source_url"] = best.get("source_url") or (all_urls[0] if all_urls else None)
            best["valid_from"] = interval["start"].isoformat()
            best["valid_until"] = interval["end"].isoformat()
            best["period_precision"] = "single_day" if interval["start"] == interval["end"] else "range"
            if len(members) > 1:
                merged_group_count += 1
                merged_source_row_count += len(members) - 1
                best["period_flags"] = " | ".join(pipe_unique([
                    *[r.get("period_flags") or "" for r in members],
                    "same_offer_overlapping_periods_consolidated",
                ]))
                best["overlap_period_flag"] = "merged_same_offer_overlapping_periods"
                best["price_quality_flag"] = " | ".join(pipe_unique([r.get("price_quality_flag") or "" for r in members])) or None
                campaign_claims = [float(as_decimal(r.get("campaign_discount_percent"))) for r in members if as_decimal(r.get("campaign_discount_percent")) is not None]
                if campaign_claims:
                    best["campaign_discount_percent"] = max(campaign_claims)
                    best["campaign_discount_basis"] = "highest_claim_in_source_titles_not_product_discount"
            best["promo_status"] = current_status(best["valid_from"], best["valid_until"], best["period_precision"], best.get("period_flags"))
            best["duplicate_count"] = sum(as_int(r.get("duplicate_count")) or 1 for r in members)
            best["overlapping_offer_count"] = len(members)
            best["freshness_status"] = combine_freshness_statuses([
                r.get("freshness_status") or classify_row_freshness(r.get("scraped_at"), r.get("retained_from_previous_run"))
                for r in members
            ])
            best["scraped_at"] = latest_scrape_timestamp(members) or best.get("scraped_at")
            best["cross_dataset_duplicate"] = len(all_datasets) > 1
            best["duplicate_group_id"] = hashlib.sha1(repr(key).encode("utf-8")).hexdigest()[:12]
            best["data_quality_flags"] = " | ".join(pipe_unique([
                best.get("period_flags") or "",
                "overlapping_offer_sources_consolidated" if len(members) > 1 else "",
                "missing_merchant_name" if not best.get("merchant_name") else "",
                "missing_product_text" if not best.get("product_text") else "",
                "missing_source_url" if not best.get("source_url") else "",
                "discount_is_estimated" if best.get("discount_percent_basis") == "bundle_estimate_same_item_value" else "",
            ])) or None
            output.append(best)
    output.sort(key=lambda r: (
        r.get("merchant_name") or "",
        r.get("campaign_title") or "",
        r.get("product_text") or "",
        r.get("valid_from") or "",
    ))
    return output, merged_group_count, merged_source_row_count


# Cross-source product/offer deduplication. This is deliberately stricter than
# matching on name alone: merchant, overlapping dates, compatible offer type,
# and non-conflicting prices must also agree.
BUNDLE_RATIO_RE = re.compile(
    r"\b(?:beli|buy)\s*(\d+)\s*(?:pcs?|buah|item|produk)?\s*(?:gratis|free|get)\s*(\d+)\b",
    re.IGNORECASE,
)


def _row_dates(row):
    try:
        start = date.fromisoformat(str(row.get("valid_from") or ""))
        end = date.fromisoformat(str(row.get("valid_until") or ""))
        if end < start:
            return None
        return start, end
    except (ValueError, TypeError):
        return None


def _bundle_ratio(row):
    text = " ".join(str(row.get(k) or "") for k in ("offer_text", "purchase_condition", "deal_type"))
    m = BUNDLE_RATIO_RE.search(text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _offer_family(row):
    text = normalize_text(" ".join(str(row.get(k) or "") for k in (
        "offer_text", "purchase_condition", "deal_type"
    )))
    ratio = _bundle_ratio(row)
    if ratio:
        return ("bundle", ratio)
    if re.search(r"\bcashback\b", text):
        return ("cashback", None)
    if re.search(r"\b(?:voucher|kupon)\b", text):
        return ("voucher", None)
    if re.search(r"\btebus\s+murah\b", text):
        return ("redemption", None)
    if re.search(r"\btambah\s+rp\b|\badditional payment\b", text):
        return ("add_on", None)
    if re.search(r"\b(?:diskon|discount|potongan)\b", text) or row.get("discount_percent") not in (None, ""):
        return ("discount", None)
    if re.search(r"\bbeli\s+\d+\b", text):
        return ("bundle_purchase", None)
    return ("general", None)


def _valid_discount_number(row):
    try:
        value = float(row.get("discount_percent"))
        return value if 0 <= value <= 100 else None
    except (TypeError, ValueError):
        return None


def _compatible_prices(a, b):
    # Two explicit values for the same field must agree. Missing values can be
    # filled from the other source after this compatibility check.
    for field in ("promo_price", "normal_price"):
        x, y = as_int(a.get(field)), as_int(b.get(field))
        if x is not None and y is not None and x != y:
            return False

    dx, dy = _valid_discount_number(a), _valid_discount_number(b)
    bx = str(a.get("discount_percent_basis") or "")
    by = str(b.get("discount_percent_basis") or "")
    low_x = a.get("discount_percent_confidence") == "low" or "estimate" in bx
    low_y = b.get("discount_percent_confidence") == "low" or "estimate" in by
    if dx is not None and dy is not None and not (low_x or low_y):
        # Percentages can be rounded by a retailer. A larger gap usually means
        # different variants, units, or promotional conditions.
        if abs(dx - dy) > 3.0:
            return False

    # A claimed discount must not contradict a fully price-calculated discount.
    calc_a = as_decimal(a.get("discount_percent_calculated"))
    calc_b = as_decimal(b.get("discount_percent_calculated"))
    claim_a = as_decimal(a.get("discount_percent")) if "explicit" in bx else None
    claim_b = as_decimal(b.get("discount_percent")) if "explicit" in by else None
    if calc_a is not None and claim_b is not None and abs(float(calc_a - claim_b)) > 4.0:
        return False
    if calc_b is not None and claim_a is not None and abs(float(calc_b - claim_a)) > 4.0:
        return False
    return True


def _purchase_quantity_signature(row):
    """Return an explicit buy-quantity from offer text/condition, if present."""
    text = normalize_text(" ".join(str(row.get(k) or "") for k in (
        "purchase_condition", "offer_text", "deal_type"
    )))
    match = re.search(
        r"\b(?:beli|buy)\s*(\d+)\s*(?:pcs?|buah|item|unit|pack|pak|box|kemasan)?\b",
        text,
        re.IGNORECASE,
    )
    return int(match.group(1)) if match else None


def _gift_item_signature(row):
    description = cross_sku_gift_description(row)
    tokens = _sku_identity_tokens(description)
    return tuple(sorted(tokens)) if tokens else None


def _compatible_offers(a, b):
    # If one source names a distinct gift SKU and the other does not, or the two
    # sources name different gift items, keep the offers separate for review.
    gift_a, gift_b = _gift_item_signature(a), _gift_item_signature(b)
    if (gift_a is not None or gift_b is not None) and gift_a != gift_b:
        return False
    fa, fb = _offer_family(a), _offer_family(b)
    if fa[0] == fb[0] == "bundle" and fa[1] != fb[1]:
        return False
    # Different quantity tiers must stay separate, even when promo-price fields
    # are empty or identical (e.g. Beli 5 pcs diskon Rp10.000 vs Beli 10 pcs).
    qty_a, qty_b = _purchase_quantity_signature(a), _purchase_quantity_signature(b)
    if qty_a is not None and qty_b is not None and qty_a != qty_b:
        return False

    if fa[0] == fb[0] == "general":
        # General/unspecified offers need stronger textual evidence; otherwise
        # the same SKU could be on two different campaigns at once.
        offer_a = norm_key(a.get("offer_text"))
        offer_b = norm_key(b.get("offer_text"))
        title_a = norm_key(a.get("campaign_title"))
        title_b = norm_key(b.get("campaign_title"))
        if not (offer_a and offer_a == offer_b) and not (title_a and title_a == title_b):
            return False

    if fa[0] != fb[0] and "general" not in {fa[0], fb[0]}:
        # A bundle may be represented by an equivalent percentage discount by
        # a different aggregator, but only if the stated percentage matches the
        # bundle's theoretical same-item-value estimate.
        bundle = fa if fa[0] == "bundle" else fb if fb[0] == "bundle" else None
        other = b if bundle is fa else a if bundle is fb else None
        if bundle and other and _offer_family(other)[0] == "discount":
            buy, free = bundle[1]
            expected = 100.0 * free / (buy + free)
            disc = _valid_discount_number(other)
            basis = str(other.get("discount_percent_basis") or "")
            if disc is None or ("estimate" not in basis and abs(disc - expected) > 3.0):
                return False
        else:
            return False
    elif fa[0] != fb[0] and "general" in {fa[0], fb[0]}:
        # A generic source card may be matched to a specific offer only if the
        # text is essentially empty/generic or the known price is shared.
        general_row = a if fa[0] == "general" else b
        specific_row = b if general_row is a else a
        generic_text = norm_key(general_row.get("offer_text"))
        generic_labels = {"", "promo", "promosi", "harga promo", "penawaran promo"}
        shares_price = any(
            as_int(general_row.get(field)) is not None
            and as_int(general_row.get(field)) == as_int(specific_row.get(field))
            for field in ("promo_price", "normal_price")
        )
        if generic_text not in generic_labels and not shares_price:
            return False

    # Do not auto-merge prices quoted per weight/volume with pack prices.
    if UNIT_CONTEXT_PATTERN.search(str(a.get("offer_text") or "")) or UNIT_CONTEXT_PATTERN.search(str(b.get("offer_text") or "")):
        return False
    return _compatible_prices(a, b)


def _compatible_periods(a, b):
    da, db = _row_dates(a), _row_dates(b)
    if not da or not db:
        return False
    # Exact date windows must overlap; estimated month windows can overlap an
    # exact article period and will inherit the exact period from the stronger row.
    return da[0] <= db[1] and db[0] <= da[1]


def _equivalent_offer_rows(a, b):
    merchant_a = norm_key(a.get("merchant_name"))
    merchant_b = norm_key(b.get("merchant_name"))
    # Unknown merchant must not be silently assigned to a known retailer.
    if not merchant_a or not merchant_b or merchant_a != merchant_b:
        return False
    if not product_names_can_auto_merge(a.get("product_text"), b.get("product_text")):
        return False
    if has_material_period_conflict(a) or has_material_period_conflict(b):
        return False
    if not _compatible_periods(a, b):
        return False
    return _compatible_offers(a, b)


def _offer_quality_score(row):
    precision = row.get("period_precision")
    return (
        12 * int(precision in {"range", "single_day"})
        + 8 * int(as_int(row.get("normal_price")) is not None and as_int(row.get("promo_price")) is not None)
        + 4 * int(_valid_discount_number(row) is not None)
        + 3 * int(bool(row.get("source_item_url")))
        + 2 * int(bool(row.get("source_url")))
        + 1 * int(bool(row.get("campaign_title")))
        - 3 * int(row.get("discount_percent_confidence") == "low")
        - 3 * int(bool(row.get("price_quality_flag")))
    )


# Cross-source merchant corroboration for product cards from combined Hemat listings.
# A multi-retailer category URL alone is ambiguous. We only resolve it when a non-Hemat
# source explicitly names one retailer and the same product/offer/period/price evidence agrees.
_MERCHANT_MATCH_ALIASES = {
    "alfamart": ("alfamart", "alfagift"),
    "alfamidi": ("alfamidi",),
    "indomaret": ("indomaret", "klik indomaret"),
    "hypermart": ("hypermart",),
    "super indo": ("super indo", "superindo"),
    "lotte mart": ("lotte mart", "lottemart"),
    "transmart": ("transmart",),
    "guardian": ("guardian",),
    "watsons": ("watsons",),
    "aeon": ("aeon",),
    "tip top": ("tip top",),
    "hero": ("hero supermarket",),
    "farmers market": ("farmers market",),
    "ranch market": ("ranch market",),
    "foodhall": ("foodhall", "the foodhall"),
    "indogrosir": ("indogrosir",),
    "lulu hypermarket": ("lulu hypermarket",),
    "hokben": ("hokben",),
    "popeyes": ("popeyes",),
    "kfc": ("kfc",),
    "mcdonald's": ("mcdonald's", "mcdonalds"),
    "pizza hut": ("pizza hut",),
    "burger king": ("burger king",),
    "j.co": ("j.co", "jco"),
    "starbucks": ("starbucks",),
    "chatime": ("chatime",),
    "yogya": ("yogya", "yomart"),
}


def _all_row_urls(row):
    return pipe_unique([
        row.get("source_url") or "", row.get("source_urls") or "",
        row.get("source_page_url") or "", row.get("source_item_url") or "",
    ])


def _is_hemat_source(row):
    text = " ".join([str(row.get("source_name") or ""), * _all_row_urls(row)]).casefold()
    return "hemat.id" in text or "hemat.id" in str(row.get("source_name") or "").casefold()


def _mentions_merchant(text, merchant):
    if not text or not merchant:
        return False
    canonical = norm_key(merchant)
    aliases = _MERCHANT_MATCH_ALIASES.get(canonical, (str(merchant),))
    haystack = str(text).casefold()
    for alias in aliases:
        alias = str(alias).casefold().strip()
        if alias and re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", haystack):
            return True
    return False


def _row_mentions_merchant(row, merchant, fields):
    return any(_mentions_merchant(row.get(field) or "", merchant) for field in fields)


def _offer_evidence_compatible_without_merchant(a, b):
    """Strong product/period/offer match used only to resolve an ambiguous aggregator merchant."""
    if not product_names_can_auto_merge(a.get("product_text"), b.get("product_text")):
        return False
    if has_material_period_conflict(a) or has_material_period_conflict(b):
        return False
    if not _compatible_periods(a, b):
        return False
    if not _compatible_offers(a, b):
        return False

    # Require at least one concrete corroborating signal, not just similar text.
    same_promo = (as_int(a.get("promo_price")) is not None
                  and as_int(a.get("promo_price")) == as_int(b.get("promo_price")))
    same_normal = (as_int(a.get("normal_price")) is not None
                   and as_int(a.get("normal_price")) == as_int(b.get("normal_price")))
    bundle_a, bundle_b = _bundle_ratio(a), _bundle_ratio(b)
    same_bundle = bundle_a is not None and bundle_a == bundle_b
    da, db = _valid_discount_number(a), _valid_discount_number(b)
    same_explicit_discount = (
        da is not None and db is not None and abs(da - db) <= 1.0
        and "explicit" in str(a.get("discount_percent_basis") or "").casefold()
        and "explicit" in str(b.get("discount_percent_basis") or "").casefold()
    )

    # Aggregators sometimes render Beli 1 Gratis 1 as a 50% discount card.
    # The ordinary offer compatibility check above already verifies that the
    # discount agrees with the bundle ratio; count that as corroborating evidence.
    family_a, family_b = _offer_family(a), _offer_family(b)
    bundle_discount_equivalent = False
    if {family_a[0], family_b[0]} == {"bundle", "discount"}:
        bundle = family_a if family_a[0] == "bundle" else family_b
        discount_row = b if family_a[0] == "bundle" else a
        ratio = bundle[1]
        discount_value = _valid_discount_number(discount_row)
        if ratio and discount_value is not None:
            expected = 100.0 * ratio[1] / (ratio[0] + ratio[1])
            basis = str(discount_row.get("discount_percent_basis") or "").casefold()
            bundle_discount_equivalent = (
                "estimate" in basis or abs(discount_value - expected) <= 3.0
            )

    return (
        same_promo or (same_normal and same_promo) or same_bundle
        or same_explicit_discount or bundle_discount_equivalent
    )


def _cross_source_merchant_candidate(unknown_row, known_row):
    """Return known merchant only for a strong Hemat combined-listing cross-check."""
    if norm_key(unknown_row.get("merchant_name")):
        return None
    merchant = (known_row.get("merchant_name") or "").strip()
    if not merchant or not _is_hemat_source(unknown_row) or _is_hemat_source(known_row):
        return None

    # The combined Hemat URL must mention this retailer, and the comparison source
    # must independently identify that retailer in its campaign title or URL.
    unknown_url_text = " ".join(_all_row_urls(unknown_row))
    known_claim_fields = ("campaign_title", "source_campaign_titles", "source_url", "source_page_url")
    if not _mentions_merchant(unknown_url_text, merchant):
        return None
    if not _row_mentions_merchant(known_row, merchant, known_claim_fields):
        return None

    # The evidence must come from genuinely different pages/sources.
    unknown_page = norm_key(unknown_row.get("source_page_url") or unknown_row.get("source_url"))
    known_page = norm_key(known_row.get("source_page_url") or known_row.get("source_url"))
    if unknown_page and known_page and unknown_page == known_page:
        return None
    if not _offer_evidence_compatible_without_merchant(unknown_row, known_row):
        return None
    return merchant


def resolve_ambiguous_hemat_merchants(rows):
    """Resolve an unknown Hemat card merchant only when exactly one retailer is corroborated.

    If compatible matches point to more than one retailer, leave the merchant unknown and
    retain the record for manual review rather than guessing.
    """
    blocks = defaultdict(list)
    for idx, row in enumerate(rows):
        block = product_name_block_key(row.get("product_text"))
        if block:
            blocks[block].append((idx, row))

    proposed = {}
    for idx, row in enumerate(rows):
        if norm_key(row.get("merchant_name")) or not _is_hemat_source(row):
            continue
        block = product_name_block_key(row.get("product_text"))
        retailer_candidates = {}
        for other_idx, other in blocks.get(block, []):
            if idx == other_idx or not norm_key(other.get("merchant_name")):
                continue
            merchant = _cross_source_merchant_candidate(row, other)
            if merchant:
                retailer_candidates.setdefault(norm_key(merchant), merchant)
        if len(retailer_candidates) == 1:
            proposed[idx] = next(iter(retailer_candidates.values()))

    # Apply only after all matches have been evaluated, so inference does not cascade.
    for idx, merchant in proposed.items():
        row = rows[idx]
        row["merchant_name"] = merchant
        row["merchant_resolution_method"] = "cross_source_corroboration_unverified"
        row["data_quality_flags"] = " | ".join(pipe_unique([
            row.get("data_quality_flags") or "",
            "merchant_inferred_by_matching_same_offer_across_sources",
        ])) or None
    return rows


def merge_equivalent_cross_source_offers(rows):
    """Collapse same-product offers across source pages when dates/offers/prices agree.

    Rows with conflicting prices, non-overlapping dates, different bundle ratios,
    unit-price ambiguity, or unresolved date conflicts remain separate for review.
    """
    rows = resolve_ambiguous_hemat_merchants([dict(row) for row in rows])
    by_product = defaultdict(list)
    for row in rows:
        merchant_key = norm_key(row.get("merchant_name"))
        product_key = product_name_block_key(row.get("product_text"))
        # Block by merchant + first product token, not the entire name, so
        # near-identical spellings can enter the same fuzzy-comparison cluster.
        if merchant_key and product_key:
            by_product[(merchant_key, product_key)].append(dict(row))

    output = []
    merged_groups = 0
    absorbed_rows = 0
    for _, product_rows in by_product.items():
        # Prefer the most evidence-rich rows as cluster representatives.
        product_rows.sort(key=_offer_quality_score, reverse=True)
        clusters = []
        for row in product_rows:
            placed = False
            for cluster in clusters:
                if all(_equivalent_offer_rows(row, member) for member in cluster):
                    cluster.append(row)
                    placed = True
                    break
            if not placed:
                clusters.append([row])

        for cluster in clusters:
            if len(cluster) == 1:
                output.append(cluster[0])
                continue

            merged_groups += 1
            absorbed_rows += len(cluster) - 1
            ordered = sorted(cluster, key=_offer_quality_score, reverse=True)
            best = dict(ordered[0])

            # Fill missing values from compatible members without overwriting
            # stronger source values with estimates.
            for field in FIELDS:
                if best.get(field) in (None, ""):
                    for candidate in ordered[1:]:
                        if candidate.get(field) not in (None, ""):
                            best[field] = candidate[field]
                            break

            all_variants = []
            all_urls = []
            all_titles = []
            all_names = []
            all_datasets = []
            all_source_types = []
            all_verification = []
            all_region_channels = []
            for member in cluster:
                all_variants.extend(name_variants(member.get("product_name_variants")))
                all_variants.extend(name_variants(member.get("original_product_text")))
                all_urls.extend(pipe_unique([member.get("source_url") or "", member.get("source_urls") or "", member.get("source_page_url") or "", member.get("source_item_url") or ""]))
                all_titles.extend(pipe_unique([member.get("campaign_title") or "", member.get("source_campaign_titles") or ""]))
                all_names.extend(pipe_unique([member.get("source_name") or "", member.get("source_names") or ""]))
                all_datasets.extend(pipe_unique([member.get("source_dataset") or ""]))
                all_source_types.extend(pipe_unique([member.get("source_type") or ""]))
                all_verification.extend(pipe_unique([member.get("verification_status") or ""]))
                all_region_channels.extend(pipe_unique([member.get("region_or_channel") or ""]))

            all_variants = list(OrderedDict.fromkeys(v for v in all_variants if v))
            if all_variants:
                best["product_name_variants"] = " | ".join(all_variants)
                best["original_product_text"] = all_variants[0]
            best["source_urls"] = " | ".join(OrderedDict.fromkeys(v for v in all_urls if v))
            best["source_campaign_titles"] = " | ".join(OrderedDict.fromkeys(v for v in all_titles if v))
            best["source_names"] = " | ".join(OrderedDict.fromkeys(v for v in all_names if v)) or None
            best["source_dataset"] = " | ".join(OrderedDict.fromkeys(v for v in all_datasets if v)) or None
            best["source_type"] = " | ".join(OrderedDict.fromkeys(v for v in all_source_types if v)) or None
            best["verification_status"] = " | ".join(OrderedDict.fromkeys(v for v in all_verification if v)) or None
            best["region_or_channel"] = " | ".join(OrderedDict.fromkeys(v for v in all_region_channels if v)) or None
            best["freshness_status"] = combine_freshness_statuses([
                m.get("freshness_status") or classify_row_freshness(m.get("scraped_at"), m.get("retained_from_previous_run"))
                for m in cluster
            ])
            best["scraped_at"] = latest_scrape_timestamp(cluster) or best.get("scraped_at")
            best["duplicate_count"] = sum(as_int(m.get("duplicate_count")) or 1 for m in cluster)
            best["overlapping_offer_count"] = len(cluster)
            dataset_parts = pipe_unique([best.get("source_dataset") or ""])
            source_name_parts = pipe_unique([best.get("source_names") or ""])
            best["cross_dataset_duplicate"] = len(dataset_parts) > 1 or len(source_name_parts) > 1
            best["duplicate_group_id"] = hashlib.sha1(
                (norm_key(best.get("merchant_name")) + "|" + norm_key(best.get("product_text")) + "|" + str(best.get("valid_from")) + "|" + str(best.get("valid_until")) + "|" + str(best.get("promo_price"))).encode("utf-8")
            ).hexdigest()[:12]

            # Keep the best (usually explicit) period window. Do not widen beyond
            # a reliable exact source merely because another site's date is estimated.
            exact_members = [m for m in ordered if m.get("period_precision") in {"range", "single_day"} and _row_dates(m)]
            if exact_members:
                date_best = exact_members[0]
                best["valid_from"] = date_best.get("valid_from")
                best["valid_until"] = date_best.get("valid_until")
                best["period_precision"] = date_best.get("period_precision")
                best["matched_period_text"] = date_best.get("matched_period_text") or best.get("matched_period_text")
                best["period_flags"] = date_best.get("period_flags")
                best["promo_status"] = current_status(best.get("valid_from"), best.get("valid_until"), best.get("period_precision"), best.get("period_flags"), best.get("promo_status"))

            # Recalculate using merged price evidence. Campaign headline claims are
            # never copied to the product-level discount.
            flags = pipe_unique([m.get("price_quality_flag") or "" for m in cluster])
            discount = safe_discount_fields(
                best.get("offer_text") or "",
                as_int(best.get("promo_price")),
                as_int(best.get("normal_price")),
                best.get("price_basis"),
                best.get("purchase_condition"),
                best.get("deal_type"),
                flags,
                product_text=best.get("product_text"),
                original_product_text=best.get("original_product_text"),
                raw_line=best.get("raw_line"),
            )
            best.update(discount)
            best["price_quality_flag"] = " | ".join(pipe_unique(flags)) or None
            best["data_quality_flags"] = " | ".join(pipe_unique([
                best.get("data_quality_flags") or "",
                "equivalent_offer_merged_across_sources",
                "discount_is_estimated" if best.get("discount_percent_basis") == "bundle_estimate_same_item_value" else "",
            ])) or None
            output.append(best)

    # Retain rows with no merchant/name rather than silently losing them.
    for row in rows:
        if not norm_key(row.get("merchant_name")) or not product_name_block_key(row.get("product_text")):
            output.append(dict(row))

    output.sort(key=lambda r: (
        (r.get("merchant_name") or "~").casefold(),
        (r.get("product_text") or "").casefold(),
        r.get("valid_from") or "",
        r.get("promo_price") or 0,
    ))
    return output, merged_groups, absorbed_rows

PERIOD_CONFLICT_MARKERS = (
    "title_lead_period_differs",
    "title_period_mismatch_with_catalog_dates",
    "source_dates_differ_from_title_period",
)


def has_material_period_conflict(row):
    flags = str(row.get("period_flags") or "")
    return any(marker in flags for marker in PERIOD_CONFLICT_MARKERS)


GENERIC_PRODUCT_NAME_RE = re.compile(
    r"^(?:produk(?:\s+(?:belum\s+teridentifikasi|pilihan|tertentu))?|item\s+pilihan|"
    r"potongan(?:\s+harga)?|harga\s+(?:promo|spesial|normal)|promo(?:si)?|diskon|"
    r"min(?:imum)?\.?\s*(?:belanja|pembelanjaan|transaksi)|syarat\s+belanja|"
    r"penawaran\s+(?:menarik|spesial))$",
    re.I,
)
NON_PRODUCT_AMOUNT_CONTEXT_RE = re.compile(
    r"\b(?:cashback|cash\s*back|qris|voucher|kupon|minimum|minimal|min\.?|"
    r"belanja|pembelanjaan|transaksi|pembayaran|kartu\s+kredit|poin|reward|saldo)\b",
    re.I,
)
EXPLICIT_PRODUCT_PRICE_LABEL_RE = re.compile(
    r"\b(?:harga\s+(?:promo|normal|spesial|setelah\s+diskon)|promo\s+rp|normal\s+rp)\b",
    re.I,
)



def _reference_block_reason(row):
    """Do not compare a single-item historical price against conditional bundles."""
    text = " ".join(str(row.get(k) or "") for k in ("offer_text", "purchase_condition", "deal_type", "raw_line"))
    if cross_sku_gift_description(row):
        return "cross_sku_gift_not_comparable"
    if re.search(r"\b(?:beli|buy)\s*\d+.*?(?:gratis|get)\s*\d+|\b(?:bonus|free gift|hadiah)\b", text, re.I):
        return "bundle_not_comparable"
    if re.search(r"\b(?:cashback|cash\s*back|qris|voucher|kupon|minimum|minimal|min\.?|pembayaran|transaksi|kartu\s+kredit|tebus\s+murah|harga\s+member)\b", text, re.I):
        return "conditional_or_payment_offer_not_comparable"
    if str(row.get("purchase_condition") or "").strip():
        return "purchase_condition_not_comparable"
    if str(row.get("price_basis") or "").casefold() != "offer_price":
        return "price_basis_not_unit_offer"
    return None


EXPLICIT_NORMAL_PRICE_TEXT_RE = re.compile(
    r"(?:harga\s+(?:normal|asli|reguler)(?:nya)?)\s*(?:adalah\s*)?(?:[:=]\s*)?(?:rp\.?\s*)?(\d[\d.,]*\d|\d)",
    re.I,
)
CONDITIONAL_PRICE_TEXT_RE = re.compile(
    r"\b(?:beli|buy)\s*\d+.*?(?:gratis|get)\s*\d+|"
    r"\b(?:cashback|cash\s*back|qris|voucher|kupon|minimum|minimal|min\.?\s*belanja|"
    r"pembayaran|transaksi|kartu\s+kredit|tebus\s+murah|harga\s+member|"
    r"khusus\s+member|maksimal\s+\d+\s*pcs)\b|"
    r"\b(?:bonus|free\s+gift|hadiah)\b|\b(?:bundling|bundle|paket)\b",
    re.I,
)


def recover_explicit_normal_price_from_offer(row):
    """Recover a clearly labelled source normal price missed by the initial parser.

    Do not recover amounts from buy-X/get-Y, bundle, payment, member, or minimum-
    purchase offers, because those may not be comparable single-item prices.
    """
    if as_int(row.get("normal_price")) is not None:
        return False
    text = " ".join(str(row.get(k) or "") for k in (
        "offer_text", "normal_price_evidence_text", "raw_line"
    )).strip()
    if not text or CONDITIONAL_PRICE_TEXT_RE.search(text):
        return False
    if str(row.get("price_basis") or "").casefold() != "offer_price":
        return False
    match = EXPLICIT_NORMAL_PRICE_TEXT_RE.search(text)
    if not match:
        return False
    amount = as_int(match.group(1))
    if amount is None or amount <= 0:
        return False
    evidence = find_amount_evidence(text, amount, "normal")
    if not evidence:
        return False
    row["normal_price"] = amount
    row["normal_price_provenance"] = "current_offer_evidence"
    row["normal_price_evidence_text"] = evidence
    row["normal_price_recovered_from_offer_text"] = True
    flags = [part.strip() for part in str(row.get("price_quality_flag") or "").split("|") if part.strip()]
    flags = [part for part in flags if part not in {
        "normal_price_parse_failed_or_unverified", "normal_price_without_matching_offer_evidence"
    }]
    row["price_quality_flag"] = " | ".join(flags) or None
    return True


def apply_price_reference(rows, references, today=TODAY, max_age_days=PRICE_REFERENCE_MAX_AGE_DAYS):
    """Attach source-backed historical prices; never use guessed/AI estimates.

    User-supplied and AI-generated estimate rows from legacy databases are ignored.
    An absent normal price is handled by reviewed poster OCR or remains blank.
    """
    references = [ref for ref in references if str(ref.get("record_origin") or "").casefold() != "user_supplied_estimate"]
    summary = {
        "rows_checked": 0,
        "exact_recent_references_found": 0,
        "cross_merchant_references_found": 0,
        "reference_calculated_discounts": 0,
        "reference_prices_not_below_promo": 0,
        "no_exact_reference": 0,
        "stale_reference_not_used": 0,
        "ambiguous_reference_not_used": 0,
        "not_comparable_offer_rows": 0,
        "user_range_reference_matches": 0,
        "reference_matches_not_used_due_price_sanity": 0,
        "normal_prices_recovered_from_offer_text": 0,
        "discounts_calculated_from_recovered_source_prices": 0,
    }
    for row in rows:
        row.setdefault("reference_normal_price", None)
        row.setdefault("reference_price_source_name", None)
        row.setdefault("reference_price_merchant_name", None)
        row.setdefault("reference_price_match_scope", None)
        row.setdefault("reference_price_source_url", None)
        row.setdefault("reference_price_evidence_text", None)
        row.setdefault("reference_price_observed_at", None)
        row.setdefault("reference_price_age_days", None)
        row.setdefault("reference_price_match_status", "not_checked")
        row.setdefault("reference_price_record_origin", None)
        row.setdefault("reference_price_low", None)
        row.setdefault("reference_price_high", None)
        row.setdefault("reference_price_unit", None)
        row.setdefault("reference_price_note", None)
        row.setdefault("reference_primary_display_allowed", False)
        recovered_normal_price = recover_explicit_normal_price_from_offer(row)
        if as_int(row.get("normal_price")) is not None:
            if row.get("normal_price_provenance") != "poster_ocr_reviewed":
                row["normal_price_provenance"] = "current_offer_evidence"
            row["reference_price_match_status"] = "not_needed_current_normal_price_available"
            row["reference_primary_display_allowed"] = True
            if recovered_normal_price or row.get("normal_price_provenance") == "poster_ocr_reviewed":
                promo = as_int(row.get("promo_price"))
                normal = as_int(row.get("normal_price"))
                if promo is not None and promo > 0 and normal is not None and normal > promo:
                    calculated = round((normal - promo) / normal * 100, 1)
                    existing_discount = as_decimal(row.get("discount_percent"))
                    if existing_discount is None or abs(float(existing_discount) - calculated) > float(DISCOUNT_PRICE_MATH_TOLERANCE):
                        if existing_discount is not None and row.get("source_discount_claim_percent") in (None, ""):
                            row["source_discount_claim_percent"] = float(existing_discount)
                        row["discount_percent"] = calculated
                        row["discount_percent_calculated"] = calculated
                        row["discount_percent_basis"] = "calculated_from_unit_prices"
                        row["discount_percent_confidence"] = "medium"
                        row["discount_savings_rp"] = normal - promo
                        row["discount_evidence_text"] = (
                            f"Harga promo Rp{promo:,} dibanding harga normal Rp{normal:,} yang tertulis di detail penawaran"
                        )
                        row["discount_mismatch_note"] = "Diskon dihitung dari harga normal yang tertulis eksplisit; klaim sumber lama disimpan untuk pemeriksaan" if existing_discount is not None else None
                        summary["discounts_calculated_from_recovered_source_prices"] += 1
                    else:
                        row["discount_percent_calculated"] = calculated
                        row["discount_savings_rp"] = normal - promo
                summary["normal_prices_recovered_from_offer_text"] += 1
            continue
        if row.get("product_validation_status") != "eligible" or not row.get("merchant_name") or not row.get("product_text"):
            row["reference_price_match_status"] = "identity_not_eligible"
            continue
        summary["rows_checked"] += 1
        ref = find_reference_price(
            references, row.get("product_text"), row.get("merchant_name"), today,
            max_age_days=max_age_days, region_or_channel=row.get("region_or_channel"),
        )
        row["reference_price_match_status"] = ref.get("status", "no_exact_match")
        if ref.get("status") not in {"matched_recent_exact", "matched_recent_cross_merchant"}:
            if ref.get("status") == "reference_too_old":
                summary["stale_reference_not_used"] += 1
            elif ref.get("status") == "conflicting_prices_same_day":
                summary["ambiguous_reference_not_used"] += 1
            else:
                summary["no_exact_reference"] += 1
            row["reference_price_age_days"] = ref.get("age_days")
            continue

        ref_price = as_int(ref.get("normal_price"))
        if ref_price is None or ref_price <= 0:
            row["reference_price_match_status"] = "reference_price_invalid"
            continue
        row["reference_normal_price"] = ref_price
        row["reference_price_source_name"] = ref.get("source_name")
        row["reference_price_merchant_name"] = ref.get("merchant_name")
        row["reference_price_match_scope"] = ref.get("match_scope") or "same_merchant"
        row["reference_price_source_url"] = ref.get("source_url")
        row["reference_price_evidence_text"] = ref.get("evidence_text")
        row["reference_price_observed_at"] = ref.get("observed_at")
        row["reference_price_age_days"] = ref.get("age_days")
        row["reference_price_record_origin"] = ref.get("record_origin")
        row["reference_price_low"] = as_int(ref.get("normal_price_low")) or ref_price
        row["reference_price_high"] = as_int(ref.get("normal_price_high")) or ref_price
        row["reference_price_unit"] = ref.get("price_unit")
        row["reference_price_note"] = ref.get("reference_note")
        row["normal_price_provenance"] = (
            "user_supplied_reference_estimate" if ref.get("record_origin") == "user_supplied_estimate"
            else "recent_price_reference"
        )
        summary["exact_recent_references_found"] += 1
        if ref.get("match_scope") == "cross_merchant":
            summary["cross_merchant_references_found"] += 1
        if ref.get("record_origin") == "user_supplied_estimate":
            summary["user_range_reference_matches"] += 1

        promo = as_int(row.get("promo_price"))
        # A missing offer price does not prevent the reference from being displayed,
        # but a discount can only be calculated when a numeric promo price exists.
        if promo is None or promo <= 0:
            row["reference_primary_display_allowed"] = True
            row["reference_price_match_status"] = "matched_recent_user_range_no_promo_price" if ref.get("record_origin") == "user_supplied_estimate" else row["reference_price_match_status"]
            continue

        blocked = _reference_block_reason(row)
        if blocked:
            row["reference_price_match_status"] = blocked
            summary["not_comparable_offer_rows"] += 1
            continue

        ref_low = as_int(ref.get("normal_price_low")) or ref_price
        ref_high = as_int(ref.get("normal_price_high")) or ref_price
        # Avoid filling an alleged original price if the promo amount is implausibly
        # outside the supplied range, which often signals a pack/unit mismatch.
        if promo > ref_high or promo < ref_low * 0.25:
            row["reference_price_match_status"] = "promo_price_outside_reference_range"
            row["reference_primary_display_allowed"] = False
            summary["reference_matches_not_used_due_price_sanity"] += 1
            continue
        row["reference_primary_display_allowed"] = promo <= ref_price

        # For user ranges, calculate from the midpoint and keep any source discount
        # claim in its dedicated review column. Bundles and conditional offers were
        # already excluded above.
        if row.get("discount_percent") not in (None, "") or row.get("estimated_bundle_discount_percent") not in (None, ""):
            origin = ref.get("record_origin")
            basis = str(row.get("discount_percent_basis") or "").casefold()
            if origin == "user_supplied_estimate" and basis in {"explicit_offer", "explicit_max"}:
                if row.get("source_discount_claim_percent") in (None, ""):
                    row["source_discount_claim_percent"] = row.get("discount_percent")
                row["discount_percent"] = None
                row["discount_percent_calculated"] = None
                row["discount_savings_rp"] = None
                row["discount_percent_basis"] = None
                row["discount_percent_confidence"] = None
            else:
                row["reference_price_match_status"] = (
                    "matched_cross_merchant_reference_discount_claim_already_present"
                    if ref.get("match_scope") == "cross_merchant"
                    else "matched_reference_discount_claim_already_present"
                )
                continue

        if ref_price <= promo:
            row["reference_price_match_status"] = (
                "matched_cross_merchant_reference_not_higher_than_promo"
                if ref.get("match_scope") == "cross_merchant"
                else "matched_reference_not_higher_than_promo"
            )
            summary["reference_prices_not_below_promo"] += 1
            continue

        percent = round((ref_price - promo) / ref_price * 100, 1)
        savings = ref_price - promo
        row["discount_percent"] = percent
        row["discount_percent_calculated"] = percent
        row["discount_savings_rp"] = savings
        row["discount_percent_basis"] = "calculated_from_price_reference"
        row["discount_percent_confidence"] = "low" if ref.get("record_origin") == "user_supplied_estimate" else "medium"
        row["discount_evidence_text"] = (
            f"Harga promo Rp{promo:,} dibanding rentang acuan Rp{ref_low:,}–Rp{ref_high:,}; "
            f"titik tengah Rp{ref_price:,} dari {ref.get('source_name')} (bukan harga normal terverifikasi retailer)"
            if ref.get("record_origin") == "user_supplied_estimate" else
            f"Harga promo Rp{promo:,} dibanding referensi harga normal Rp{ref_price:,}; "
            f"observasi {ref.get('observed_at')} dari {ref.get('source_name')}"
        )
        row["data_quality_flags"] = " | ".join(pipe_unique([
            row.get("data_quality_flags") or "",
            "discount_calculated_from_user_supplied_reference_range" if ref.get("record_origin") == "user_supplied_estimate" else "discount_calculated_from_recent_historical_price_reference",
        ])) or None
        if ref.get("match_scope") == "cross_merchant":
            row["reference_price_match_status"] = "matched_cross_merchant_and_discount_calculated_from_reference"
        else:
            row["reference_price_match_status"] = "matched_and_discount_calculated_from_reference"
        summary["reference_calculated_discounts"] += 1
    summary["reference_database_records"] = len(references)
    summary["max_age_days"] = max_age_days
    return summary


def discount_basis_display(row):
    basis = str(row.get("discount_percent_basis") or "").casefold()
    if basis == "calculated_from_price_reference":
        if row.get("reference_price_record_origin") == "user_supplied_estimate":
            scope_text = " dari acuan merchant lain" if row.get("reference_price_match_scope") == "cross_merchant" else ""
            return "Estimasi dari titik tengah rentang referensi pengguna" + scope_text
        age = row.get("reference_price_age_days")
        age_text = f" ({age} hari)" if age not in (None, "") else ""
        scope_text = " merchant lain" if row.get("reference_price_match_scope") == "cross_merchant" else ""
        return "Hitungan dari referensi harga historis" + scope_text + age_text
    if basis == "calculated_from_unit_prices":
        return "Hitungan dari dua harga di penawaran"
    if basis == "explicit_offer":
        return "Klaim diskon tertulis pada penawaran"
    if basis == "explicit_max":
        return "Klaim diskon maksimum dari sumber"
    if basis == "bundle_estimate_same_item_value":
        return "Estimasi bundling; bukan diskon terkonfirmasi"
    if basis == "cross_sku_bundle_review":
        return "Hadiah lintas produk; perlu review"
    if basis == "explicit_amount_discount":
        return "Potongan nominal tertulis; persentase hanya dihitung bila pembanding sebanding"
    if basis == "discount_price_mismatch_review":
        return "Klaim diskon berbeda dari hitungan; review"
    return "Belum diketahui"

def reference_price_status_display(row):
    status = str(row.get("reference_price_match_status") or "").casefold()
    if row.get("reference_price_record_origin") == "user_supplied_estimate":
        if status == "matched_and_discount_calculated_from_reference":
            return "Diskon estimasi dari rentang acuan pengguna (titik tengah)"
        if status == "matched_cross_merchant_and_discount_calculated_from_reference":
            return "Diskon estimasi dari rentang acuan pengguna; fallback merchant lain"
        if status == "matched_recent_user_range_no_promo_price":
            return "Rentang acuan pengguna; belum ada harga promo numerik"
        if status == "promo_price_outside_reference_range":
            return "Referensi pengguna ada; harga promo di luar rentang, perlu cek SKU/satuan"
        if status in {"matched_recent_exact", "matched_recent_cross_merchant"}:
            return "Rentang acuan dari pengguna; bukan harga retailer terverifikasi"
        if status == "matched_reference_not_higher_than_promo":
            return "Referensi pengguna tidak lebih tinggi dari harga promo"
        if status == "matched_cross_merchant_reference_not_higher_than_promo":
            return "Referensi pengguna/merchant lain tidak lebih tinggi dari harga promo"
    labels = {
        "not_checked": "Belum diperiksa",
        "not_needed_current_normal_price_available": "Harga normal ada pada penawaran saat ini",
        "not_requested_no_valid_promo_price": "Harga promo tidak tersedia/valid",
        "identity_not_eligible": "Identitas produk belum cukup",
        "matched_recent_exact": "Referensi merchant sama; harga pembanding tersedia",
        "matched_recent_cross_merchant": "Fallback: referensi dari merchant lain",
        "matched_and_discount_calculated_from_reference": "Referensi merchant sama; diskon dihitung dari harga historis",
        "matched_cross_merchant_and_discount_calculated_from_reference": "Diskon estimasi dari harga merchant lain",
        "matched_cross_merchant_reference_discount_claim_already_present": "Referensi merchant lain; memakai klaim diskon sumber",
        "matched_cross_merchant_reference_not_higher_than_promo": "Referensi merchant lain tidak lebih tinggi dari harga promo",
        "matched_reference_discount_claim_already_present": "Referensi cocok; memakai klaim diskon sumber",
        "matched_reference_not_higher_than_promo": "Referensi merchant sama tidak lebih tinggi dari harga promo",
        "matched_recent_user_range_no_promo_price": "Estimasi referensi pengguna; harga promo tidak tersedia",
        "promo_price_outside_reference_range": "Harga promo di luar rentang referensi; tidak dipakai untuk hitung diskon",
        "reference_too_old": "Referensi terlalu lama; tidak digunakan",
        "conflicting_prices_same_day": "Harga referensi konflik; tidak digunakan",
        "no_exact_match": "Belum ada referensi yang cocok persis",
        "bundle_not_comparable": "Referensi ada; bundling tidak dibandingkan",
        "cross_sku_gift_not_comparable": "Referensi ada; hadiah SKU lain tidak dibandingkan",
        "conditional_or_payment_offer_not_comparable": "Referensi ada; promo pembayaran/layanan dikecualikan",
        "purchase_condition_not_comparable": "Referensi ada; promo bersyarat dikecualikan",
        "price_basis_not_unit_offer": "Harga bukan harga satuan; referensi tidak dipakai",
        "reference_price_invalid": "Nilai referensi tidak valid",
        "reference_discount_failed_quality_gate": "Hitungan referensi gagal pemeriksaan kualitas",
    }
    if status in labels:
        return labels[status]
    return status.replace("_", " ").capitalize() if status else "Belum diperiksa"


def market_reference_status_display(row):
    status = str(row.get("market_reference_status") or "").casefold()
    labels = {
        "not_checked": "Belum diperiksa",
        "not_comparable": "Tidak dapat dibandingkan",
        "no_market_reference": "Harga pembanding belum ditemukan",
        "single_source_only": "Baru satu sumber; belum cukup untuk menilai pasar",
        "below_market_median": "Lebih murah dari median harga pasar",
        "not_below_market_median": "Tidak lebih murah dari median harga pasar",
        "invalid_market_reference": "Referensi harga tidak valid",
    }
    return labels.get(status, status.replace("_", " ").capitalize() if status else "Belum diperiksa")


def product_validation_display(row):
    """Expose product identity/price validation separately from promo date status."""
    status = str(row.get("product_validation_status") or "review").strip().casefold()
    if status == "eligible":
        return "Valid"
    if status == "rejected":
        return "Ditolak"
    return "Perlu review"


def _product_price_gate_issue(row, field, kind):
    amount = as_int(row.get(field))
    if amount is None:
        return None
    if amount <= 0:
        return f"{field}_not_positive"

    evidence_field = f"{field}_evidence_text"
    evidence = str(row.get(evidence_field) or "").strip()
    offer = str(row.get("offer_text") or "").strip()
    raw_line = str(row.get("raw_line") or "").strip()
    matching = find_amount_evidence(evidence, amount, kind) if evidence else None
    if not matching:
        source_text = " ".join(part for part in (offer, raw_line) if part)
        matching = find_amount_evidence(source_text, amount, kind)
    if not matching:
        return f"{field}_missing_matching_source_evidence"

    # Numeric amounts associated with payment/cashback/transaction conditions are
    # not item prices unless the same snippet explicitly labels a product price.
    if NON_PRODUCT_AMOUNT_CONTEXT_RE.search(matching) and not EXPLICIT_PRODUCT_PRICE_LABEL_RE.search(matching):
        return f"{field}_looks_like_payment_or_purchase_condition"

    # Keep the evidence snippet on the row so the audit can show what passed.
    row[evidence_field] = matching
    return None


DISCOUNT_PRICE_MATH_TOLERANCE = Decimal("3.0")


def discount_price_consistency_issue(row):
    """Flag explicit product discount claims that materially disagree with price math.

    Percentage claims are compared only when the same row has positive normal/promo
    prices, the parser marks them as offer prices, and unit context is not already
    ambiguous. 'Hingga/up to' claims are only flagged when the calculated SKU discount
    exceeds the stated maximum; a smaller value is compatible with a maximum claim.
    """
    basis = str(row.get("discount_percent_basis") or "").strip().casefold()
    if basis not in {"explicit_offer", "explicit_max"}:
        return None
    if str(row.get("price_basis") or "").strip().casefold() != "offer_price":
        return None
    flags = {part.strip() for part in str(row.get("price_quality_flag") or "").split("|") if part.strip()}
    if {"price_unit_context_requires_review", "normal_price_ambiguous_unit", "promo_price_ambiguous_unit"} & flags:
        return None
    promo = as_int(row.get("promo_price"))
    normal = as_int(row.get("normal_price"))
    if promo is None or normal is None or promo <= 0 or normal <= promo:
        return None
    stated = as_decimal(row.get("discount_percent"))
    if stated is None:
        stated, parsed_basis = parse_explicit_discount(str(row.get("offer_text") or ""))
        stated = as_decimal(stated)
        if stated is None or parsed_basis not in {"explicit_offer", "explicit_max"}:
            return None
        basis = parsed_basis
    calculated = as_decimal(row.get("discount_percent_calculated"))
    if calculated is None:
        calculated = (Decimal(normal - promo) / Decimal(normal) * Decimal("100")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if basis == "explicit_max":
        return "maximum_discount_below_price_calculation" if calculated > stated + DISCOUNT_PRICE_MATH_TOLERANCE else None
    return "explicit_discount_price_math_disagreement" if abs(calculated - stated) > DISCOUNT_PRICE_MATH_TOLERANCE else None


def export_quality_gate_issues(row):
    """Return blockers for a row that is about to enter the user-facing active catalog."""
    issues = []
    validation_status = str(row.get("product_validation_status") or "").strip().casefold()
    if validation_status != "eligible":
        issues.append("product_validation_status_not_eligible")

    product = clean_product_name(str(row.get("product_text") or ""))
    if not product or not product.strip() or GENERIC_PRODUCT_NAME_RE.fullmatch(product.strip().strip(" .:-–—")):
        issues.append("product_identity_empty_or_generic")

    merchant = str(row.get("merchant_name") or "").strip()
    category, subcategory = classify_promo_category(row)
    if (not merchant or merchant.casefold() in {"unknown", "n/a", "none", "belum diketahui"}) and category != "Promo Umum / Multi-Kategori":
        issues.append("merchant_missing_or_unknown")
    if category == "Lainnya / Perlu Review":
        issues.append("category_requires_manual_review")
    if category not in CATEGORY_NAMES:
        issues.append("category_not_in_taxonomy")
    if not str(row.get("source_url") or "").strip():
        issues.append("source_url_missing")

    promo = as_int(row.get("promo_price"))
    normal = as_int(row.get("normal_price"))
    if promo is not None:
        issue = _product_price_gate_issue(row, "promo_price", "promo")
        if issue:
            issues.append(issue)
    if normal is not None:
        issue = _product_price_gate_issue(row, "normal_price", "normal")
        if issue:
            issues.append(issue)
    if promo is not None and normal is not None and promo > normal:
        issues.append("promo_price_above_normal_price")

    # Source/adapter quality flags must survive fuzzy merges as blockers when they
    # describe an unresolved amount, unit or cross-source price conflict. Benign
    # notes (e.g. evidence reconstructed from visible text or a labelled bundle
    # estimate) intentionally do not block a row by themselves.
    price_flags = {part.strip() for part in str(row.get("price_quality_flag") or "").split("|") if part.strip()}
    if promo is not None:
        if "price_inferred" in price_flags:
            issues.append("promo_price_inferred_needs_review")
        if "promo_price_above_normal_review" in price_flags:
            issues.append("promo_price_above_normal_review")
        if "promo_price_parse_failed_or_unverified" in price_flags or "promo_price_without_matching_offer_evidence" in price_flags:
            issues.append("promo_price_source_quality_unverified")
        if "additional_payment_amount_not_unit_price" in price_flags or "additional_payment_not_unit_price" in price_flags:
            issues.append("promo_price_is_additional_payment_not_item_price")
    if normal is not None:
        if "normal_price_ambiguous_unit" in price_flags:
            issues.append("normal_price_unit_is_ambiguous")
        if "normal_price_parse_failed_or_unverified" in price_flags or "normal_price_without_matching_offer_evidence" in price_flags:
            issues.append("normal_price_source_quality_unverified")
    if (promo is not None or normal is not None) and "price_unit_context_requires_review" in price_flags:
        issues.append("price_unit_context_requires_review")
    if (promo is not None or normal is not None) and "cross_source_price_disagreement" in price_flags:
        issues.append("cross_source_price_disagreement")
    if "payment_benefit_amount_not_product_price" in price_flags and promo is not None:
        issues.append("promo_price_is_payment_benefit_not_item_price")

    discount = as_decimal(row.get("discount_percent"))
    if discount is not None and (discount < 0 or discount > 100):
        issues.append("product_discount_out_of_range")
    if str(row.get("discount_percent_basis") or "").casefold() == "calculated_from_price_reference":
        ref_price = as_int(row.get("reference_normal_price"))
        promo_price = as_int(row.get("promo_price"))
        ref_evidence = str(row.get("reference_price_evidence_text") or "").strip()
        age = as_int(row.get("reference_price_age_days"))
        user_estimate = row.get("reference_price_record_origin") == "user_supplied_estimate"
        calculated_statuses = {"matched_and_discount_calculated_from_reference", "matched_cross_merchant_and_discount_calculated_from_reference"}
        if user_estimate:
            low = as_int(row.get("reference_price_low"))
            high = as_int(row.get("reference_price_high"))
            ref_valid = (
                ref_price is not None and low is not None and high is not None and low <= ref_price <= high
                and promo_price is not None and 0 < promo_price < ref_price
                and bool(row.get("reference_price_source_name"))
                and "rentang acuan pengguna" in ref_evidence.casefold()
                and row.get("reference_price_match_status") in calculated_statuses
            )
        else:
            ref_valid = (
                ref_price is not None and ref_price > 0 and promo_price is not None and 0 < promo_price < ref_price
                and bool(row.get("reference_price_source_url"))
                and bool(EXPLICIT_NORMAL_PRICE_LABEL_RE.search(ref_evidence))
                and bool(find_amount_evidence(ref_evidence, ref_price, "normal"))
                and row.get("reference_price_match_status") == "matched_and_discount_calculated_from_reference"
                and age is not None and 0 <= age <= PRICE_REFERENCE_MAX_AGE_DAYS
            )
        if not ref_valid:
            issues.append("reference_discount_without_valid_reference")
        elif discount is not None:
            expected = round((ref_price - promo_price) / ref_price * 100, 1)
            if abs(float(discount) - expected) > 0.11:
                issues.append("reference_discount_math_mismatch")
    if row.get("discount_percent_basis") == "cross_sku_bundle_review" or "cross_sku_gift_not_product_discount" in str(row.get("price_quality_flag") or ""):
        issues.append("cross_sku_gift_bundle_requires_review")
    if discount is not None and "discount_without_matching_offer_evidence" in price_flags:
        issues.append("product_discount_missing_matching_offer_evidence")
    consistency_issue = discount_price_consistency_issue(row)
    if consistency_issue:
        issues.append(consistency_issue)
    return list(OrderedDict.fromkeys(issues))


def _quarantine_for_export_gate(row, issues):
    """Keep the source record for review while removing unsupported product money fields."""
    issues = list(OrderedDict.fromkeys(issues))
    row["export_gate_flags"] = " | ".join(issues)
    row["product_validation_status"] = "review"
    row["product_review_reason"] = " | ".join(pipe_unique([
        row.get("product_review_reason") or "",
        "Gerbang kualitas ekspor: " + ", ".join(issues),
    ]))
    if "explicit_discount_price_math_disagreement" in issues or "maximum_discount_below_price_calculation" in issues:
        source_claim = as_decimal(row.get("discount_percent"))
        if source_claim is not None:
            row["source_discount_claim_percent"] = float(source_claim)
        calculated = as_decimal(row.get("discount_percent_calculated"))
        claim = as_decimal(row.get("source_discount_claim_percent"))
        row["discount_percent"] = None
        row["discount_savings_rp"] = None
        row["discount_evidence_text"] = row.get("discount_evidence_text") or row.get("offer_text")
        row["discount_percent_basis"] = "discount_price_mismatch_review"
        row["discount_percent_confidence"] = "unknown"
        row["discount_mismatch_note"] = (
            f"Klaim sumber {claim:g}% berbeda dengan hitungan harga {calculated:g}%; periksa varian/satuan"
            if claim is not None and calculated is not None else
            "Klaim diskon sumber tidak cocok dengan hitungan harga; perlu review"
        )
    if "product_identity_empty_or_generic" in issues:
        for field in (
            "promo_price", "normal_price", "promo_price_evidence_text", "normal_price_evidence_text",
            "discount_evidence_text", "discount_percent", "discount_percent_calculated",
            "discount_savings_rp", "estimated_bundle_discount_percent", "reference_normal_price",
            "reference_price_source_name", "reference_price_source_url", "reference_price_evidence_text",
            "reference_price_observed_at", "reference_price_age_days",
        ):
            row[field] = None
        row["reference_price_match_status"] = "identity_not_eligible"
        row["discount_percent_basis"] = "not_available"
        row["discount_percent_confidence"] = "unknown"
        row["price_basis"] = None
        row["price_parse_method"] = "export_gate_cleared_unidentified_product"
        row["product_category"] = "Lainnya / Perlu Review"
        row["product_subcategory"] = "Produk belum teridentifikasi"
    else:
        clear_promo = any(issue.startswith("promo_price_") for issue in issues)
        clear_normal = any(issue.startswith("normal_price_") for issue in issues)
        if clear_promo:
            for field in ("promo_price", "promo_price_evidence_text"):
                row[field] = None
        if clear_normal:
            for field in ("normal_price", "normal_price_evidence_text"):
                row[field] = None
        if (clear_promo or clear_normal) and row.get("discount_percent_basis") in {"calculated_from_unit_prices", "calculated_from_price_reference"}:
            row["discount_percent"] = None
            row["discount_percent_calculated"] = None
            row["discount_savings_rp"] = None
            row["discount_evidence_text"] = None
            row["discount_percent_basis"] = "not_available"
            row["discount_percent_confidence"] = "unknown"
        if "reference_discount_without_valid_reference" in issues or "reference_discount_math_mismatch" in issues:
            row["discount_percent"] = None
            row["discount_percent_calculated"] = None
            row["discount_savings_rp"] = None
            row["discount_evidence_text"] = None
            row["discount_percent_basis"] = "not_available"
            row["discount_percent_confidence"] = "unknown"
            row["reference_price_match_status"] = "reference_discount_failed_quality_gate"
        if "product_discount_out_of_range" in issues or "product_discount_missing_matching_offer_evidence" in issues:
            row["discount_percent"] = None
            row["discount_percent_calculated"] = None
            row["discount_savings_rp"] = None
            row["discount_evidence_text"] = None
            row["discount_percent_basis"] = "not_available"
            row["discount_percent_confidence"] = "unknown"
        if "cross_sku_gift_bundle_requires_review" in issues:
            row["discount_percent"] = None
            row["discount_percent_calculated"] = None
            row["discount_savings_rp"] = None
            row["estimated_bundle_discount_percent"] = None
            row["discount_percent_basis"] = "cross_sku_bundle_review"
            row["discount_percent_confidence"] = "unknown"
        if "promo_price_above_normal_price" in issues and row.get("discount_percent_basis") in {"calculated_from_unit_prices", "calculated_from_price_reference"}:
            row["discount_percent"] = None
            row["discount_percent_calculated"] = None
            row["discount_savings_rp"] = None
            row["discount_evidence_text"] = None
            row["discount_percent_basis"] = "not_available"
            row["discount_percent_confidence"] = "unknown"
    row["data_quality_flags"] = " | ".join(pipe_unique([
        row.get("data_quality_flags") or "",
        "export_quality_gate_review",
        *["export_quality_gate:" + issue for issue in issues],
    ])) or None
    return row


def apply_export_quality_gate(consolidated_rows):
    """Partition active candidates into clean catalog vs review, with audit details."""
    current = []
    quarantined = []
    issue_counts = Counter()
    candidates = [
        row for row in consolidated_rows
        if row.get("promo_status") in {"active", "active_estimated"}
        and row.get("product_validation_status") != "review"
        and not has_material_period_conflict(row)
    ]
    candidate_ids = {id(row) for row in candidates}
    for row in candidates:
        issues = export_quality_gate_issues(row)
        if issues:
            _quarantine_for_export_gate(row, issues)
            quarantined.append({
                "product_text": row.get("product_text") or "",
                "merchant_name": row.get("merchant_name") or "",
                "campaign_title": row.get("campaign_title") or "",
                "source_url": row.get("source_url") or "",
                "source_discount_claim_percent": row.get("source_discount_claim_percent"),
                "discount_percent_calculated": row.get("discount_percent_calculated"),
                "issues": issues,
            })
            issue_counts.update(issues)
        else:
            current.append(row)

    # Existing review / non-active records also need their source discount claim
    # separated from the validated discount field. They are not new active-gate
    # candidates, but should not continue showing a contradictory number as valid.
    already_reviewed_discount_conflicts = []
    for row in consolidated_rows:
        if id(row) in candidate_ids or str(row.get("product_validation_status") or "").casefold() == "rejected":
            continue
        issue = discount_price_consistency_issue(row)
        if not issue:
            continue
        _quarantine_for_export_gate(row, [issue])
        already_reviewed_discount_conflicts.append({
            "product_text": row.get("product_text") or "",
            "merchant_name": row.get("merchant_name") or "",
            "campaign_title": row.get("campaign_title") or "",
            "source_url": row.get("source_url") or "",
            "source_discount_claim_percent": row.get("source_discount_claim_percent"),
            "discount_percent_calculated": row.get("discount_percent_calculated"),
            "issues": [issue],
        })
        issue_counts.update([issue])

    remaining = []
    for row in current:
        issues = export_quality_gate_issues(row)
        if issues:
            remaining.append({
                "product_text": row.get("product_text") or "",
                "merchant_name": row.get("merchant_name") or "",
                "source_url": row.get("source_url") or "",
                "issues": issues,
            })
    report = {
        "status": "passed" if not remaining else "failed",
        "candidate_active_rows": len(candidates),
        "accepted_active_rows": len(current),
        "routed_to_review_rows": len(quarantined),
        "additional_existing_review_discount_rows": len(already_reviewed_discount_conflicts),
        "issue_counts": dict(issue_counts),
        "routed_rows": quarantined,
        "discount_mismatches_already_in_review": already_reviewed_discount_conflicts,
        "blocking_rows_remaining": remaining,
    }
    return current, report


# Main catalog uses a compact, user-friendly column order; technical fields remain in OUT_FULL.
CLEAN_COLUMNS = [
    ("Nama Produk", "product_text"),
    ("Kategori Produk", "product_category"),
    ("Merchant", "merchant_name"),
    ("Harga Asli (Rp)", "display_normal_price"),
    ("Harga Promo (Rp)", "promo_price"),
    ("Diskon (%)", "discount_percent"),
    ("Basis Harga Asli", "display_normal_price_basis"),
    ("Harga Asli dari Sumber (Rp)", "normal_price"),
    ("Harga Normal Referensi (Rp)", "reference_normal_price"),
    ("Rentang Referensi Harga (Rp)", "reference_price_range_display"),
    ("Dasar Diskon", "discount_basis_label"),
    ("Status Promo", "display_status"),
    ("Mulai Berlaku", "valid_from"),
    ("Berakhir", "valid_until"),
    ("Kepastian Periode", "period_certainty"),
    ("Status Validasi Produk", "product_quality_label"),
    ("Verifikasi Sumber", "source_verification_label"),
    ("Status Referensi Harga", "reference_price_status_display"),
    ("Merchant Acuan Referensi", "reference_price_merchant_name"),
    ("Umur Referensi (hari)", "reference_price_age_days"),
    ("Tanggal Observasi Referensi", "reference_price_observed_at"),
    ("Sumber Referensi Harga", "reference_price_source_name"),
    ("URL Referensi Harga", "reference_price_source_url"),
    ("Median Harga Pasar (Rp)", "market_reference_price"),
    ("Rentang Harga Pasar (Rp)", "market_reference_range_display"),
    ("Lebih Murah dari Median Pasar (%)", "market_reference_below_median_percent"),
    ("Selisih terhadap Median Pasar (Rp)", "market_reference_savings"),
    ("Jumlah Domain Pembanding", "market_reference_source_count"),
    ("Status Pembanding Pasar", "market_reference_status_display"),
    ("Sumber Pembanding Pasar", "market_reference_sources"),
    ("URL Pembanding Pasar", "market_reference_source_urls"),
    ("Observasi Pembanding Pasar", "market_reference_observed_at"),
    ("Keyakinan Pembanding Pasar", "market_reference_confidence"),
    ("Hemat / Selisih Referensi (Rp)", "discount_savings_rp"),
    ("Subkategori", "product_subcategory"),
    ("Kesegaran Data", "freshness_display"),
    ("Waktu Scrape", "scraped_at"),
    ("Klaim Diskon Sumber (Review) (%)", "source_discount_claim_percent"),
    ("Jenis Promo", "display_deal_type"),
    ("Detail Penawaran", "offer_text"),
    ("Kampanye", "campaign_title"),
    ("Catatan", "display_notes"),
    ("Sumber URL", "source_url"),
]


def display_status(row):
    status = row.get("promo_status")
    if status == "active_estimated": return "Aktif (periode estimasi)"
    if status == "active": return "Aktif"
    if status == "upcoming": return "Mendatang"
    if status == "expired": return "Kedaluwarsa"
    return "Perlu verifikasi"


def display_notes(row):
    notes = []
    basis = row.get("discount_percent_basis")
    if basis == "calculated_from_unit_prices":
        notes.append("Diskon dihitung dari harga normal yang tercantum pada penawaran dan harga promo")
    elif basis == "calculated_from_price_reference":
        notes.append("Diskon dihitung terhadap harga normal yang pernah diamati dari sumber; bukan harga normal yang tercantum pada promo saat ini")
    elif basis == "explicit_offer":
        notes.append("Persentase diskon tertulis pada penawaran")
    elif basis == "bundle_estimate_same_item_value":
        notes.append("Estimasi bundling; diasumsikan nilai tiap barang sama")
    elif basis == "unit_context_review":
        notes.append("Diskon tidak dihitung: satuan harga perlu disamakan")
    else:
        notes.append("Diskon belum dapat dipastikan dari data harga")
    if row.get("price_basis") in {"bundle_offer_amount", "package_price", "non_numeric_bundle_offer"}:
        notes.append("Harga dapat merujuk ke paket, bukan harga satuan")
    if row.get("promo_status") == "active_estimated":
        notes.append("Periode promo masih estimasi; cek sumber")
    if has_material_period_conflict(row):
        notes.append("Konflik tanggal; dipindahkan ke file review")
    elif "period_taken_from_lead" in str(row.get("period_flags") or ""):
        notes.append("Tanggal diambil dari paragraf pembuka")
    if row.get("merchant_resolution_method") == "cross_source_corroboration_unverified":
        notes.append("Merchant dicocokkan dari penawaran lintas sumber; belum diverifikasi retailer resmi")
    if row.get("reference_normal_price") not in (None, ""):
        notes.append("Harga referensi bukan verifikasi resmi retailer; periksa tanggal, sumber, dan URL referensi")
        if row.get("reference_price_match_scope") == "cross_merchant":
            notes.append("Harga acuan berasal dari merchant lain karena referensi merchant promo tidak tersedia; diskon hanya estimasi pembanding")
    if row.get("price_quality_flag"):
        notes.append("Harga/nominal perlu diperiksa")
    if row.get("discount_mismatch_note"):
        notes.append(str(row.get("discount_mismatch_note")))
    if truthy(row.get("retained_from_previous_run")):
        notes.append("Data dipertahankan dari scraping sebelumnya karena sumber tidak menghasilkan produk baru pada run ini")
    campaign_claim = row.get("campaign_discount_percent")
    if campaign_claim not in (None, "") and basis in (None, "", "not_available", "unit_context_review"):
        try:
            claim = float(campaign_claim)
            notes.append(f"Kampanye menyebut diskon {claim:g}%; bukan diskon pasti produk ini")
        except (ValueError, TypeError):
            notes.append("Klaim diskon kampanye belum dipastikan untuk produk ini")
    return " | ".join(pipe_unique(notes))


def clean_sort_key(row):
    value = row.get("Diskon (%)")
    try: discount = float(value) if value not in (None, "") else -1.0
    except (ValueError, TypeError): discount = -1.0
    return (-discount, (row.get("Merchant") or "").casefold(), (row.get("Nama Produk") or "").casefold())


def make_clean_row(row, review=False):
    item = dict(row)
    item["display_status"] = display_status(row)
    item["product_quality_label"] = product_validation_display(row)
    item["display_deal_type"] = row.get("deal_type") or row.get("campaign_type") or ""
    item["product_category"], item["product_subcategory"] = classify_promo_category(row)
    item["display_notes"] = display_notes(row)
    item["discount_basis_label"] = discount_basis_display(row)
    item["reference_price_status_display"] = reference_price_status_display(row)
    item["freshness_display"] = freshness_display_label(row.get("freshness_status"))
    actual_normal = as_int(row.get("normal_price"))
    reference_normal = as_int(row.get("reference_normal_price"))
    if actual_normal is not None:
        item["display_normal_price"] = actual_normal
        item["display_normal_price_basis"] = (
            "Harga normal pada poster promo (OCR ditinjau manual)"
            if row.get("normal_price_provenance") == "poster_ocr_reviewed"
            else "Harga asli yang tercantum pada sumber promo"
        )
    elif reference_normal is not None and row.get("reference_primary_display_allowed"):
        item["display_normal_price"] = reference_normal
        if row.get("reference_price_record_origin") == "user_supplied_estimate":
            item["display_normal_price_basis"] = "Estimasi dari titik tengah rentang referensi pengguna"
        elif row.get("reference_price_match_scope") == "cross_merchant":
            item["display_normal_price_basis"] = "Estimasi dari harga referensi merchant lain"
        else:
            item["display_normal_price_basis"] = "Referensi harga historis; bukan harga asli pada promo saat ini"
    else:
        item["display_normal_price"] = None
        item["display_normal_price_basis"] = "Belum ada harga asli/referensi yang layak digunakan"
    low = as_int(row.get("reference_price_low"))
    high = as_int(row.get("reference_price_high"))
    if low is not None and high is not None:
        item["reference_price_range_display"] = f"Rp{low:,}–Rp{high:,}".replace(",", ".")
    else:
        item["reference_price_range_display"] = None
    market_low = as_int(row.get("market_reference_low"))
    market_high = as_int(row.get("market_reference_high"))
    if market_low is not None and market_high is not None:
        item["market_reference_range_display"] = f"Rp{market_low:,}–Rp{market_high:,}".replace(",", ".")
    else:
        item["market_reference_range_display"] = None
    item["market_reference_status_display"] = market_reference_status_display(row)
    out = {label: item.get(key) for label, key in CLEAN_COLUMNS}
    if review and not str(out.get("Nama Produk") or "").strip():
        # The review sheet should be readable, while the full audit retains the
        # original cleaned-empty value plus original_product_text/raw_line.
        out["Nama Produk"] = "Produk belum teridentifikasi"
    if review:
        reasons = []
        if row.get("promo_status") == "active_estimated": reasons.append("Periode promo estimasi")
        if has_material_period_conflict(row): reasons.append("Konflik tanggal judul/sumber")
        elif "period_taken_from_lead" in str(row.get("period_flags") or ""): reasons.append("Tanggal diambil dari paragraf pembuka")
        if row.get("price_quality_flag"): reasons.append("Harga perlu diperiksa")
        if row.get("product_validation_status") == "review" and row.get("product_review_reason"):
            reasons.append("Identitas produk perlu diverifikasi: " + str(row.get("product_review_reason")))
        if row.get("discount_percent_confidence") == "low": reasons.append("Persentase diskon estimasi")
        if row.get("overlap_period_flag"): reasons.append("Penawaran serupa muncul pada beberapa artikel")
        if row.get("discount_mismatch_note"): reasons.append(str(row.get("discount_mismatch_note")))
        if "explicit_discount_price_math_disagreement" in str(row.get("export_gate_flags") or ""):
            reasons.append("Klaim diskon tertulis berbeda signifikan dari hitungan harga")
        if "maximum_discount_below_price_calculation" in str(row.get("export_gate_flags") or ""):
            reasons.append("Diskon hitungan melampaui klaim diskon maksimum sumber")
        if not row.get("merchant_name"): reasons.append("Merchant belum diketahui")
        if row.get("merchant_resolution_method") == "cross_source_corroboration_unverified": reasons.append("Merchant dicocokkan lintas sumber; belum terverifikasi resmi")
        if not row.get("source_url"): reasons.append("Sumber URL tidak tersedia")
        out["Alasan Review"] = " | ".join(pipe_unique(reasons)) or "Pemeriksaan kualitas umum"
    return out


def write_clean_rows(path, rows, review=False):
    out_rows = [make_clean_row(row, review=review) for row in rows]
    out_rows.sort(key=clean_sort_key)
    headers = [label for label, _ in CLEAN_COLUMNS]
    if review: headers.append("Alasan Review")
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(out_rows)


def write_audit_rows(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)



POSSIBLE_DUPLICATE_FIELDS = [
    "Prioritas Review", "Kemiripan Nama (%)", "Produk A", "Produk B", "Merchant A", "Merchant B",
    "Harga Promo A (Rp)", "Harga Promo B (Rp)", "Harga Normal A (Rp)", "Harga Normal B (Rp)",
    "Periode A", "Periode B", "Penawaran A", "Penawaran B", "Sumber A", "Sumber B",
    "Alasan", "Tindakan",
]


def _source_url_primary(row):
    # Prefer item-level URL for Hemat cards sharing one listing page; otherwise
    # use article/page URL as the source identity.
    return str(row.get("source_item_url") or row.get("source_url") or row.get("source_page_url") or "").strip()


def _date_overlap_for_candidate(a, b):
    da, db = _row_dates(a), _row_dates(b)
    if da and db:
        return da[0] <= db[1] and db[0] <= da[1]
    return True  # incomplete dates are candidates for review, not auto-merge proof


def _candidate_reason(a, b, score):
    reasons = []
    merchant_a, merchant_b = norm_key(a.get("merchant_name")), norm_key(b.get("merchant_name"))
    if not merchant_a or not merchant_b:
        reasons.append("Merchant belum pasti pada salah satu sumber")
    for field, label in (("promo_price", "Harga promo berbeda"), ("normal_price", "Harga normal berbeda")):
        va, vb = as_int(a.get(field)), as_int(b.get(field))
        if va is not None and vb is not None and va != vb:
            reasons.append(label)
    if not _compatible_prices(a, b):
        reasons.append("Bukti harga/diskon tidak cukup kompatibel")
    if not _compatible_offers(a, b):
        reasons.append("Jenis atau syarat penawaran berbeda")
    if has_material_period_conflict(a) or has_material_period_conflict(b):
        reasons.append("Ada konflik periode dari sumber")
    if not _row_dates(a) or not _row_dates(b):
        reasons.append("Periode salah satu sumber belum lengkap")
    size_a = product_name_size_signature(a.get("product_text"))
    size_b = product_name_size_signature(b.get("product_text"))
    if bool(size_a) != bool(size_b):
        reasons.append("Ukuran/kemasan tidak tercantum pada salah satu nama")
    variant_a = product_name_variant_signature(a.get("product_text"))
    variant_b = product_name_variant_signature(b.get("product_text"))
    if variant_a and variant_b and variant_a != variant_b:
        reasons.append("Varian rasa/aroma/formula perlu dicek")
    if score < 0.93:
        reasons.append("Nama produk hanya mirip, bukan identik")
    if not reasons:
        reasons.append("Kandidat produk sama, tetapi gagal syarat penggabungan otomatis")
    return " | ".join(pipe_unique(reasons))


def _duplicate_review_priority(a, b, score):
    """Rank review urgency without treating different offers/variants as mergeable."""
    variant_a = product_name_variant_signature(a.get("product_text"))
    variant_b = product_name_variant_signature(b.get("product_text"))
    variants_conflict = bool(variant_a and variant_b and variant_a != variant_b)
    qty_a, qty_b = _purchase_quantity_signature(a), _purchase_quantity_signature(b)
    quantity_tier_conflict = qty_a is not None and qty_b is not None and qty_a != qty_b
    gift_a, gift_b = _gift_item_signature(a), _gift_item_signature(b)
    gift_conflict = (gift_a is not None or gift_b is not None) and gift_a != gift_b
    offer_conflict = not _compatible_offers(a, b)
    price_conflict = not _compatible_prices(a, b)
    # High means same-looking SKU plus compatible terms, but insufficient evidence
    # (e.g. date/source proof) blocked automatic consolidation.
    if score >= 0.93 and not any((variants_conflict, quantity_tier_conflict, gift_conflict, offer_conflict, price_conflict)):
        return "Tinggi"
    if score >= 0.88:
        return "Sedang"
    return "Rendah"


def _duplicate_review_action(a, b):
    qty_a, qty_b = _purchase_quantity_signature(a), _purchase_quantity_signature(b)
    if qty_a is not None and qty_b is not None and qty_a != qty_b:
        return f"Pertahankan terpisah; tingkat pembelian berbeda (Beli {qty_a} vs Beli {qty_b})"
    gift_a, gift_b = _gift_item_signature(a), _gift_item_signature(b)
    if (gift_a is not None or gift_b is not None) and gift_a != gift_b:
        return "Pertahankan terpisah; SKU/barang hadiah berbeda"
    variant_a = product_name_variant_signature(a.get("product_text"))
    variant_b = product_name_variant_signature(b.get("product_text"))
    if variant_a and variant_b and variant_a != variant_b:
        return "Jangan gabungkan otomatis; varian perlu dikonfirmasi"
    if not _compatible_offers(a, b):
        return "Pertahankan terpisah; jenis atau syarat penawaran berbeda"
    if not _compatible_prices(a, b):
        return "Jangan gabungkan otomatis; harga/diskon berbeda"
    return "Review manual; periode dan bukti sumber harus cocok sebelum penggabungan"


def build_possible_duplicate_rows(rows, max_pairs=500):
    """Find similar cross-source product rows not auto-merged; never deletes data."""
    blocks = defaultdict(list)
    for row in rows:
        name = row.get("product_text") or ""
        block = product_name_block_key(name)
        if block:
            blocks[block].append(row)

    candidates = []
    seen_pairs = set()
    for block_rows in blocks.values():
        if len(block_rows) < 2:
            continue
        for i in range(len(block_rows)):
            a = block_rows[i]
            url_a = _source_url_primary(a)
            for j in range(i + 1, len(block_rows)):
                b = block_rows[j]
                url_b = _source_url_primary(b)
                if url_a and url_b and url_a == url_b:
                    continue
                merchant_a, merchant_b = norm_key(a.get("merchant_name")), norm_key(b.get("merchant_name"))
                # Same product sold by two known different retailers is not a duplicate.
                if merchant_a and merchant_b and merchant_a != merchant_b:
                    continue
                size_a, size_b = product_name_size_signature(a.get("product_text")), product_name_size_signature(b.get("product_text"))
                if size_a and size_b and size_a != size_b:
                    continue
                # Different explicit variants are not auto-merged, but a close
                # name can still be surfaced as a review candidate with that warning.
                score = product_name_similarity(a.get("product_text"), b.get("product_text"))
                if score < 0.84:
                    continue
                if not _date_overlap_for_candidate(a, b):
                    continue
                # Exact compatible offers should already have been merged upstream.
                if _equivalent_offer_rows(a, b):
                    continue
                pair_key = tuple(sorted((
                    f"{url_a}|{norm_key(a.get('product_text'))}|{a.get('promo_price')}|{a.get('valid_from')}|{a.get('valid_until')}",
                    f"{url_b}|{norm_key(b.get('product_text'))}|{b.get('promo_price')}|{b.get('valid_from')}|{b.get('valid_until')}",
                )))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                priority = _duplicate_review_priority(a, b, score)
                candidates.append({
                    "Prioritas Review": priority,
                    "Kemiripan Nama (%)": round(score * 100, 1),
                    "Produk A": a.get("product_text"),
                    "Produk B": b.get("product_text"),
                    "Merchant A": a.get("merchant_name") or "Belum diketahui",
                    "Merchant B": b.get("merchant_name") or "Belum diketahui",
                    "Harga Promo A (Rp)": as_int(a.get("promo_price")),
                    "Harga Promo B (Rp)": as_int(b.get("promo_price")),
                    "Harga Normal A (Rp)": as_int(a.get("normal_price")),
                    "Harga Normal B (Rp)": as_int(b.get("normal_price")),
                    "Periode A": f"{a.get('valid_from') or '?'} s.d. {a.get('valid_until') or '?'}",
                    "Periode B": f"{b.get('valid_from') or '?'} s.d. {b.get('valid_until') or '?'}",
                    "Penawaran A": a.get("offer_text") or a.get("deal_type") or "",
                    "Penawaran B": b.get("offer_text") or b.get("deal_type") or "",
                    "Sumber A": " | ".join(pipe_unique([a.get("source_name") or "", a.get("source_url") or "", a.get("source_item_url") or ""])),
                    "Sumber B": " | ".join(pipe_unique([b.get("source_name") or "", b.get("source_url") or "", b.get("source_item_url") or ""])),
                    "Alasan": _candidate_reason(a, b, score),
                    "Tindakan": _duplicate_review_action(a, b),
                })
    priority_rank = {"Tinggi": 0, "Sedang": 1, "Rendah": 2}
    candidates.sort(key=lambda row: (priority_rank.get(row.get("Prioritas Review"), 9), -float(row["Kemiripan Nama (%)"]), str(row.get("Merchant A") or "").casefold(), str(row.get("Produk A") or "").casefold()))
    return candidates[:max_pairs]


def write_possible_duplicate_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=POSSIBLE_DUPLICATE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

def build_name_variant_rows(rows):
    """Group alternate spellings across offers without deleting distinct promotions."""
    grouped = defaultdict(list)
    for row in rows:
        key = (norm_key(row.get("merchant_name")), norm_key(row.get("product_text")))
        if key[1]:
            grouped[key].append(row)

    output = []
    for (_, _), group in grouped.items():
        standard_name = group[0].get("product_text") or ""
        merchant = group[0].get("merchant_name") or "Belum diketahui"
        variants = []
        for row in group:
            variants.extend(name_variants(row.get("product_name_variants")))
            variants.extend(name_variants(row.get("original_product_text")))
        variants = list(OrderedDict.fromkeys(v for v in variants if v))
        # Ignore any values that are not spelling/pack notation variants of this canonical name.
        source_spellings = [v for v in variants if clean_product_name(v) == standard_name]
        if len(source_spellings) < 2:
            continue
        offer_signatures = {
            (str(r.get("normal_price") or ""), str(r.get("promo_price") or ""),
             norm_key(r.get("offer_text")), str(r.get("valid_from") or ""),
             str(r.get("valid_until") or ""))
            for r in group
        }
        prices = list(OrderedDict.fromkeys(str(r.get("promo_price")) for r in group if r.get("promo_price") not in (None, "")))
        starts = list(OrderedDict.fromkeys(str(r.get("valid_from")) for r in group if r.get("valid_from")))
        ends = list(OrderedDict.fromkeys(str(r.get("valid_until")) for r in group if r.get("valid_until")))
        urls = pipe_unique([r.get("source_url") for r in group] + [r.get("source_urls") for r in group])
        note = (
            "Nama produk sama, tetapi harga/penawaran/periode berbeda; jangan gabungkan otomatis."
            if len(offer_signatures) > 1 else
            "Variasi penulisan dinormalisasi ke satu nama standar; nama asal tetap tersimpan di audit."
        )
        output.append({
            "Nama Standar": standard_name,
            "Variasi Nama Asal": " | ".join(source_spellings),
            "Merchant": merchant,
            "Jumlah Variasi": len(source_spellings),
            "Jumlah Baris Promo": len(group),
            "Harga Promo (Rp)": " | ".join(prices),
            "Mulai Berlaku": " | ".join(starts),
            "Berakhir": " | ".join(ends),
            "Sumber": " | ".join(urls),
            "Catatan": note,
        })
    output.sort(key=lambda x: ((x.get("Merchant") or "").casefold(), (x.get("Nama Standar") or "").casefold()))
    return output



def _price_as_integer(value):
    return as_int(value)



# Simplified everyday workbook: keep the operational sheet short, with
# "Masa Berlaku" immediately after "Diskon (%)". The detailed audit sheets
# remain available as hidden tabs for troubleshooting and regression tests.
SIMPLE_EXCEL_COLUMNS = [
    "Nama Produk",
    "Kategori",
    "Merchant",
    "Harga Normal/Acuan (Rp)",
    "Harga Promo (Rp)",
    "Hemat (Rp)",
    "Diskon (%)",
    "Masa Berlaku",
    "Status Diskon",
    "Penawaran",
    "Status Promo",
    "Verifikasi Sumber",
    "Sumber URL",
]


def _simple_rupiah_matches(text):
    return list(re.finditer(r"\brp\.?\s*([0-9][0-9.,]*)", str(text or ""), re.I))


def _simple_parse_amount(raw):
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        try:
            return int(round(float(raw)))
        except (ValueError, TypeError, OverflowError):
            return None
    digits = re.sub(r"\D", "", str(raw))
    return int(digits) if digits else None


def _simple_explicit_price_pair(row):
    """Recover a comparable promo/normal pair only when both prices are explicit."""
    text = " ".join(str(row.get(k) or "") for k in (
        "offer_text", "raw_line", "promo_price_evidence_text", "normal_price_evidence_text"
    ))
    if not text.strip():
        return None

    normal_match = re.search(
        r"\bharga\s+(?:normal|asli|reguler)(?:nya)?\b.{0,45}?\brp\.?\s*([0-9][0-9.,]*)",
        text, re.I
    )
    promo_match = re.search(
        r"\bharga\s+(?:promo|spesial|setelah\s+diskon)\b.{0,45}?\brp\.?\s*([0-9][0-9.,]*)",
        text, re.I
    )
    if normal_match and promo_match:
        promo = _simple_parse_amount(promo_match.group(1))
        normal = _simple_parse_amount(normal_match.group(1))
        if promo is not None and normal is not None and promo > 0 and normal > 0:
            return promo, normal

    # Common aggregator phrasing: "Rp10.900 dari harga normalnya Rp12.700".
    pair_match = re.search(
        r"\brp\.?\s*([0-9][0-9.,]*)\s+dari\s+harga\s+(?:normal|asli|reguler)(?:nya)?\b.{0,20}?\brp\.?\s*([0-9][0-9.,]*)",
        text, re.I
    )
    if pair_match:
        promo = _simple_parse_amount(pair_match.group(1))
        normal = _simple_parse_amount(pair_match.group(2))
        if promo is not None and normal is not None and promo > 0 and normal > 0:
            return promo, normal

    return None


def _simple_explicit_savings(row):
    # Prefer the dedicated offer field; raw_line often repeats the same offer,
    # which must not make one explicit nominal saving look like two amounts.
    offer = str(row.get("offer_text") or row.get("raw_line") or "")
    stored = _simple_parse_amount(row.get("discount_savings_rp"))
    basis = str(row.get("discount_percent_basis") or row.get("discount_basis") or "").casefold()
    if basis == "explicit_amount_discount" and stored is not None:
        return stored
    # Legacy repair: prior collectors sometimes put "Diskon RpX" in promo_price.
    pair = _simple_explicit_price_pair(row)
    if pair is None and len(_simple_rupiah_matches(offer)) == 1:
        match = re.search(
            r"\b(?:diskon|potongan(?:\s+harga)?|hemat)\s*(?:nominal\s*)?rp\.?\s*([0-9][0-9.,]*)",
            offer, re.I
        )
        if match:
            return _simple_parse_amount(match.group(1))
    return None


def _simple_quantity(row, text):
    all_text = " ".join(str(row.get(k) or "") for k in (
        "purchase_condition", "offer_text", "raw_line", "product_text"
    ))
    # "/pck", "/bdd", "/karton", etc. means the stated price is per package;
    # do not reinterpret "Beli 2" as one total package amount in that case.
    per_package_price = bool(re.search(
        r"(?:/\s*(?:pck|pack|bdd|karton|dus|set|btl|botol|box)|\bper\s+(?:pack|pak|karton|dus|botol|box)\b)",
        text, re.I
    ))
    quantity = None
    match = re.search(r"\bbeli\s*(\d+)\s*(?:pcs?|pc|potong|pack|item|bungkus|buah)?\b", all_text, re.I)
    if match:
        quantity = int(match.group(1))
    else:
        match = re.search(r"\bdapat\s*(\d+)\s*(?:produk|pcs?|item|bungkus|buah)\b", all_text, re.I)
        if match:
            quantity = int(match.group(1))
    if per_package_price:
        return None
    return quantity if quantity and quantity > 0 else None


def _simple_reference_value(row, field):
    value = _simple_parse_amount(row.get(field))
    if value is not None and value > 0:
        return value
    return None


def _simple_reference_unit_compatible(row, text):
    unit = str(row.get("reference_price_unit") or "").casefold()
    condition = str(row.get("purchase_condition") or "").casefold()
    source = str(text or "").casefold()
    if not unit:
        return False
    if "potong" in unit:
        return bool(re.search(r"\b(?:pcs?|pc|potong)\b", condition + " " + source))
    if "porsi" in unit:
        # "pcs" and "porsi" are not assumed to be equivalent.
        return bool(re.search(r"\bporsi\b", condition + " " + source))
    if "bungkus" in unit:
        return bool(re.search(r"\bbungkus\b", condition + " " + source)) or "beli" in condition
    if any(term in unit for term in ("pack", "bottle", "btl", "botol", "sachet", "saset", "bag", "karton", "dus", "roll", "box")):
        return True
    return False


def _simple_format_period(start_value, end_value):
    def parsed(value):
        try:
            return date.fromisoformat(str(value)[:10]) if value else None
        except (TypeError, ValueError):
            return None

    start = parsed(start_value)
    end = parsed(end_value)
    months = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
    if start and end:
        if start == end:
            return f"{start.day} {months[start.month - 1]} {start.year}"
        if start.year == end.year and start.month == end.month:
            return f"{start.day}–{end.day} {months[end.month - 1]} {end.year}"
        if start.year == end.year:
            return f"{start.day} {months[start.month - 1]}–{end.day} {months[end.month - 1]} {end.year}"
        return f"{start.day} {months[start.month - 1]} {start.year}–{end.day} {months[end.month - 1]} {end.year}"
    if start:
        return f"Mulai {start.day} {months[start.month - 1]} {start.year}"
    if end:
        return f"s.d. {end.day} {months[end.month - 1]} {end.year}"
    return "Belum terkonfirmasi"


def make_simple_excel_row(row):
    """Return a concise, nonblank-discount display row without inventing prices."""
    clean = make_clean_row(row, review=False)
    product = str(row.get("product_text") or clean.get("Nama Produk") or "").strip()
    category, _subcategory = classify_promo_category(row)
    merchant = str(row.get("merchant_name") or "").strip() or "Belum diketahui"
    offer = str(row.get("offer_text") or row.get("raw_line") or "").strip()
    text = " ".join(str(row.get(k) or "") for k in (
        "offer_text", "raw_line", "purchase_condition", "product_text"
    ))

    explicit_savings = _simple_explicit_savings(row)
    pair = _simple_explicit_price_pair(row)

    source_promo = _simple_parse_amount(row.get("promo_price"))
    source_normal = _simple_parse_amount(row.get("normal_price"))
    reference_origin = str(row.get("reference_price_record_origin") or "").casefold()
    # Old user/AI guess-only references remain in legacy backups for audit only.
    # They must never become a display price or drive a discount calculation.
    if reference_origin == "user_supplied_estimate":
        ref_normal = ref_low = ref_high = None
        reference_unit = ""
    else:
        ref_normal = _simple_reference_value(row, "reference_normal_price")
        ref_low = _simple_reference_value(row, "reference_price_low")
        ref_high = _simple_reference_value(row, "reference_price_high")
        reference_unit = str(row.get("reference_price_unit") or "").casefold()
    quality_flags = str(row.get("price_quality_flag") or "")
    existing_discount = row.get("discount_percent")
    existing_basis = str(row.get("discount_percent_basis") or "").casefold()
    existing_confidence = str(row.get("discount_percent_confidence") or "").casefold()

    promo_display = source_promo
    normal_display = source_normal if source_normal is not None else ref_normal
    savings_display = _simple_parse_amount(row.get("discount_savings_rp"))
    discount_value = None
    discount_status = ""
    calculation_basis = ""

    # Price pair explicitly stated in the same source offer outranks estimates.
    if pair:
        pair_promo, pair_normal = pair
        if pair_promo <= pair_normal and pair_normal > 0:
            promo_display = pair_promo
            normal_display = pair_normal
            savings_display = pair_normal - pair_promo
            discount_value = round((pair_normal - pair_promo) / pair_normal * 100, 1)
            calculation_basis = "direct_source_pair"
            discount_status = "Hitung dari harga promo dan normal yang tertulis"
        else:
            discount_status = "Harga promo tidak lebih rendah dari harga normal sumber"

    elif explicit_savings is not None:
        # A nominal saving isn't a product selling price.
        promo_display = None
        savings_display = explicit_savings
        quantity = _simple_quantity(row, text)
        unit_compatible = _simple_reference_unit_compatible(row, text)
        if (
            quantity
            and unit_compatible
            and ref_normal
            and reference_origin == "user_supplied_estimate"
            and ref_normal > 0
        ):
            normal_total = ref_normal * quantity
            discount_value = round(explicit_savings / normal_total * 100, 1)
            normal_display = normal_total
            calculation_basis = "estimate_nominal_saving_against_user_reference"
            discount_status = "Estimasi: potongan nominal dibanding nilai acuan per unit"
        else:
            discount_status = "Potongan nominal tertulis; persentase belum bisa dihitung dari harga pembanding"

    elif (
        source_promo is not None
        and source_normal is not None
        and source_normal > source_promo > 0
        and not row.get("purchase_condition")
        and str(row.get("price_basis") or "").casefold() == "offer_price"
    ):
        savings_display = source_normal - source_promo
        normal_display = source_normal
        discount_value = round(savings_display / source_normal * 100, 1)
        calculation_basis = "direct_source_pair"
        discount_status = "Hitung dari harga promo dan normal sumber"

    elif (
        existing_discount not in (None, "")
        and isinstance(existing_discount, (int, float, str))
        and str(existing_discount).strip() not in {"", "None"}
    ):
        try:
            discount_value = round(float(existing_discount), 1)
        except (ValueError, TypeError):
            discount_value = None
        if discount_value is not None:
            if existing_basis == "calculated_from_price_reference" or reference_origin == "user_supplied_estimate":
                discount_status = "Estimasi dari rentang referensi pengguna" if reference_origin == "user_supplied_estimate" else "Hitung dari referensi harga historis"
            elif existing_basis in {"explicit_offer", "explicit_max"}:
                discount_status = "Klaim diskon tertulis pada penawaran"
            elif existing_basis == "calculated_from_unit_prices":
                discount_status = "Hitung dari dua harga dalam penawaran"
            elif existing_basis in {"bundle_estimate_same_item_value", "bundle_estimate"}:
                discount_status = "Estimasi bundling; nilai tiap produk diasumsikan sama"
            else:
                discount_status = "Diskon tersedia dalam hasil validasi"
            if savings_display is None and normal_display is not None and source_promo is not None:
                # If normal is a per-unit reference and this row is not a bundle,
                # estimate the difference against the midpoint shown.
                if not row.get("purchase_condition") and normal_display > source_promo:
                    savings_display = normal_display - source_promo

    # Estimate a bundle's percentage only when the source gives a bundle total and
    # the user reference is for the same unit. Anomalous offers are sent to review.
    if (
        discount_value is None
        and explicit_savings is None
        and pair is None
        and source_promo is not None
        and ref_normal is not None
        and ref_low is not None
        and reference_origin == "user_supplied_estimate"
    ):
        quantity = _simple_quantity(row, text)
        has_bundle_offer = bool(
            quantity
            and re.search(r"\b(?:beli|dapat)\s*\d+\b", text, re.I)
            and not re.search(
                r"(?:/\s*(?:pck|pack|bdd|karton|dus|set|btl|botol|box)|\bper\s+(?:pack|pak|karton|dus|botol|box)\b)",
                text,
                re.I,
            )
        )
        unit_ok = _simple_reference_unit_compatible(row, text)
        if has_bundle_offer and unit_ok:
            expected_low = ref_low * quantity
            expected_mid = ref_normal * quantity
            ratio = source_promo / expected_mid if expected_mid else 0
            if source_promo < expected_low and 0.60 <= ratio < 1.0:
                normal_display = expected_mid
                promo_display = source_promo
                savings_display = expected_mid - source_promo
                discount_value = round(savings_display / expected_mid * 100, 1)
                calculation_basis = "estimated_bundle_from_user_range"
                discount_status = "Estimasi paket dari rentang harga pengguna"
            elif source_promo < expected_low and ratio < 0.60:
                discount_status = "Perlu review: harga paket jauh di bawah rentang acuan"

    # Direct user-range references for non-bundle offers: only produce an
    # additional estimate if the promo is below the stated low end, not merely
    # below the midpoint. This avoids claiming a discount when the promo lies
    # inside the user's broad approximate range.
    if (
        discount_value is None
        and explicit_savings is None
        and pair is None
        and source_promo is not None
        and source_normal is None
        and ref_normal is not None
        and ref_low is not None
        and reference_origin == "user_supplied_estimate"
        and not row.get("purchase_condition")
    ):
        if source_promo < ref_low:
            normal_display = ref_normal
            savings_display = ref_normal - source_promo
            discount_value = round(savings_display / ref_normal * 100, 1)
            calculation_basis = "estimate_below_user_range"
            discount_status = "Estimasi dari rentang pengguna; promo di bawah batas bawah acuan"

    # Always avoid an empty Diskon cell.
    if discount_value is not None and 0 <= discount_value <= 100:
        discount_cell = discount_value
    else:
        discount_cell = "Belum bisa dihitung"
        if not discount_status:
            if explicit_savings is not None:
                discount_status = "Potongan nominal tertulis; pembanding tidak memadai"
            elif quality_flags:
                discount_status = "Perlu review harga/satuan"
            elif source_promo is None:
                discount_status = "Harga promo tidak tersedia"
            elif ref_normal is not None and source_promo >= ref_normal:
                discount_status = "Harga promo tidak lebih rendah dari acuan"
            else:
                discount_status = "Belum ada pembanding harga yang sebanding"

    # If a source/conditional price is member-only or region-specific, make the
    # limitation visible rather than implying a universal price.
    if re.search(r"khusus member|member|pulau jawa|bali|sumatera|wilayah|minimum|min\.?\s*belanja", text, re.I):
        if not discount_status.startswith("Estimasi"):
            discount_status = "Ada syarat wilayah/member; periksa penawaran"

    if promo_display is None:
        promo_cell = "—"
    else:
        promo_cell = promo_display

    if normal_display is None:
        normal_cell = "—"
    else:
        normal_cell = normal_display

    if savings_display is None:
        savings_cell = "—"
    else:
        savings_cell = savings_display

    return {
        "Nama Produk": product or "Produk belum teridentifikasi",
        "Kategori": category,
        "Merchant": merchant,
        "Harga Normal/Acuan (Rp)": normal_cell,
        "Harga Promo (Rp)": promo_cell,
        "Hemat (Rp)": savings_cell,
        "Diskon (%)": discount_cell,
        "Masa Berlaku": _simple_format_period(row.get("valid_from"), row.get("valid_until")),
        "Status Diskon": discount_status or "Belum bisa dihitung",
        "Penawaran": offer or "—",
        "Status Promo": display_status(row),
        "Verifikasi Sumber": row.get("source_verification_label") or source_verification_label(row.get("verification_status")),
        "Sumber URL": row.get("source_url") or "",
        "_discount_numeric": isinstance(discount_cell, (int, float)),
        "_discount_basis": calculation_basis or existing_basis,
        "_review_required": (
            not isinstance(discount_cell, (int, float))
            or bool(row.get("price_quality_flag"))
            or row.get("product_validation_status") == "review"
            or row.get("promo_status") in {"active_estimated", "upcoming_estimated", "unknown"}
            or bool(row.get("period_flags"))
            or bool(row.get("overlap_period_flag"))
        ),
        "_source_row": row,
    }


def _write_simple_worksheet(wb, name, rows, header_fill, title_fill=None):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    ws = wb.create_sheet(name)
    ws.append(SIMPLE_EXCEL_COLUMNS)
    for row in rows:
        ws.append([row.get(header, "—") for header in SIMPLE_EXCEL_COLUMNS])

    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    widths = {
        "Nama Produk": 42,
        "Kategori": 30,
        "Merchant": 20,
        "Harga Normal/Acuan (Rp)": 21,
        "Harga Promo (Rp)": 17,
        "Hemat (Rp)": 15,
        "Diskon (%)": 18,
        "Masa Berlaku": 23,
        "Status Diskon": 48,
        "Penawaran": 48,
        "Status Promo": 22,
        "Verifikasi Sumber": 35,
        "Sumber URL": 62,
    }
    for col_idx, header in enumerate(SIMPLE_EXCEL_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = widths.get(header, 20)
    for row_idx in range(2, ws.max_row + 1):
        for col_idx, header in enumerate(SIMPLE_EXCEL_COLUMNS, start=1):
            cell = ws.cell(row_idx, col_idx)
            cell.alignment = Alignment(
                vertical="top",
                wrap_text=header in {
                    "Nama Produk", "Kategori", "Status Diskon",
                    "Penawaran", "Verifikasi Sumber", "Sumber URL",
                },
            )
            if header in {"Harga Normal/Acuan (Rp)", "Harga Promo (Rp)", "Hemat (Rp)"}:
                if isinstance(cell.value, (int, float)):
                    cell.number_format = '"Rp" #,##0'
            elif header == "Diskon (%)" and isinstance(cell.value, (int, float)):
                cell.number_format = '0.0"%"'
    ws.freeze_panes = "A2"
    # Do not set ws.auto_filter.ref on a range that is also an Excel Table.
    # The Table owns its own AutoFilter; writing both can make Excel repair the
    # workbook by removing the table/AutoFilter on open.
    ws.sheet_view.showGridLines = False
    ws.row_dimensions[1].height = 34
    try:
        from openpyxl.worksheet.table import Table, TableStyleInfo
        if ws.max_row >= 2:
            table_name = re.sub(r"[^A-Za-z0-9]", "", name) + "Table"
            table = Table(displayName=table_name, ref=ws.dimensions)
            table.tableStyleInfo = TableStyleInfo(
                name="TableStyleMedium2",
                showFirstColumn=False,
                showLastColumn=False,
                showRowStripes=True,
                showColumnStripes=False,
            )
            ws.add_table(table)
    except Exception:
        pass
    return ws


def build_simple_catalog_rows(rows):
    """Build the exact public-facing 13-column representation shared by Excel and CSV."""
    return [make_simple_excel_row(row) for row in rows]


def write_simple_catalog_csv(path, rows):
    """Write a CSV whose columns and display values mirror the Excel Promo sheet."""
    simple_rows = build_simple_catalog_rows(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SIMPLE_EXCEL_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({header: row.get(header, "—") for header in SIMPLE_EXCEL_COLUMNS} for row in simple_rows)
    return len(simple_rows)


def _build_simple_workbook_sheets(wb, current_rows, review_rows, header_fill, title_fill):
    from openpyxl.styles import Alignment, Font

    # The same transformation is used by write_simple_catalog_csv so numeric
    # calculations and human-readable fallback labels cannot drift by format.
    simple_current = build_simple_catalog_rows(current_rows)
    simple_review = build_simple_catalog_rows(review_rows)

    active_keys = {
        (
            str(row.get("Nama Produk", "")).casefold(),
            str(row.get("Merchant", "")).casefold(),
            str(row.get("Masa Berlaku", "")),
            str(row.get("Penawaran", "")),
            str(row.get("Sumber URL", "")),
        )
        for row in simple_current
    }

    # The review tab includes explicit review rows plus current rows that still
    # have an uncomputable/estimated discount or a data-quality flag.
    combined_review = []
    seen = set()
    for row in simple_review + [item for item in simple_current if item.get("_review_required")]:
        key = (
            str(row.get("Nama Produk", "")).casefold(),
            str(row.get("Merchant", "")).casefold(),
            str(row.get("Masa Berlaku", "")),
            str(row.get("Penawaran", "")),
            str(row.get("Sumber URL", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        combined_review.append(row)

    _write_simple_worksheet(wb, "Promo", simple_current, header_fill, title_fill)
    _write_simple_worksheet(wb, "Perlu Diperiksa", combined_review, header_fill, title_fill)

    # Separate market-price opportunities from official discount claims.
    market_rows = [
        row for row in current_rows
        if as_int(row.get("market_reference_source_count")) is not None
        and as_int(row.get("market_reference_source_count")) > 0
    ]
    market_rows.sort(
        key=lambda row: (
            as_decimal(row.get("market_reference_below_median_percent")) is not None,
            float(as_decimal(row.get("market_reference_below_median_percent")) or 0),
            as_int(row.get("market_reference_source_count")) or 0,
        ),
        reverse=True,
    )
    market_sheet = wb.create_sheet("Peluang Harga")
    market_headers = [
        "Nama Produk", "Merchant Promo", "Harga Promo (Rp)", "Median Harga Pasar (Rp)",
        "Rentang Harga Pasar (Rp)", "Lebih Murah dari Median (%)", "Potensi Selisih (Rp)",
        "Jumlah Domain Pembanding", "Status Pembanding", "Harga per Domain / Jenis Harga", "URL Bukti Harga",
        "Observasi Terbaru", "Tingkat Keyakinan",
    ]
    market_sheet.append(market_headers)
    for row in market_rows:
        low = as_int(row.get("market_reference_low"))
        high = as_int(row.get("market_reference_high"))
        range_text = f"Rp{low:,}–Rp{high:,}".replace(",", ".") if low is not None and high is not None else "—"
        market_sheet.append([
            row.get("product_text") or "Produk belum teridentifikasi", row.get("merchant_name") or "—",
            as_int(row.get("promo_price")), as_int(row.get("market_reference_price")), range_text,
            as_decimal(row.get("market_reference_below_median_percent")),
            as_int(row.get("market_reference_savings")), as_int(row.get("market_reference_source_count")) or 0,
            market_reference_status_display(row), row.get("market_reference_sources") or "—",
            row.get("market_reference_source_urls") or "—", row.get("market_reference_observed_at") or "—",
            row.get("market_reference_confidence") or "—",
        ])
    from openpyxl.utils import get_column_letter
    for cell in market_sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    market_widths = [42, 22, 18, 23, 24, 23, 20, 16, 42, 52, 72, 24, 20]
    for col_idx, width in enumerate(market_widths, start=1):
        market_sheet.column_dimensions[get_column_letter(col_idx)].width = width
    for row_idx in range(2, market_sheet.max_row + 1):
        for col_idx, header in enumerate(market_headers, start=1):
            cell = market_sheet.cell(row_idx, col_idx)
            cell.alignment = Alignment(vertical="top", wrap_text=header in {"Nama Produk", "Status Pembanding", "Harga per Domain / Jenis Harga", "URL Bukti Harga"})
            if header in {"Harga Promo (Rp)", "Median Harga Pasar (Rp)", "Potensi Selisih (Rp)"} and isinstance(cell.value, (int, float)):
                cell.number_format = '"Rp" #,##0'
            elif header == "Lebih Murah dari Median (%)" and isinstance(cell.value, (int, float)):
                cell.number_format = '0.0"%"'
    if not market_rows:
        market_sheet.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(market_headers))
        note = market_sheet.cell(2, 1)
        note.value = (
            "Belum ada pembanding pasar terverifikasi. Harga normal dan diskon sebaiknya diambil dari teks sumber promo "
            "atau poster promo yang sudah ditinjau manual melalui OCR. Untuk membandingkan dengan pasar, impor harga "
            "produk yang sama dari minimal dua domain independen melalui Excel dengan URL halaman harga dan bukti harga terlihat."
        )
        note.alignment = Alignment(vertical="top", wrap_text=True)
        note.font = Font(italic=True, color="7F6000")
        market_sheet.row_dimensions[2].height = 54
        market_sheet.freeze_panes = "A3"
        market_sheet.auto_filter.ref = f"A1:M1"
    else:
        market_sheet.freeze_panes = "A2"
        market_sheet.auto_filter.ref = market_sheet.dimensions
    market_sheet.sheet_view.showGridLines = False
    market_sheet.row_dimensions[1].height = 38

    # Unverified search leads are useful for discovering possible bargains but
    # must stay separate from verified market medians and official discount data.
    candidate_sheet = wb.create_sheet("Kandidat Harga")
    candidate_headers = [
        "Produk Dicari", "Merchant Promo", "Harga Promo (Rp)",
        "Harga dari Cuplikan (Rp) - BELUM TERVERIFIKASI",
        "Selisih Indikatif vs Promo (Rp) - BUKAN DISKON RESMI",
        "Judul Kandidat", "Domain", "URL untuk Diperiksa", "Status Bukti", "Catatan Validasi", "Tanggal Dicari",
    ]
    candidate_sheet.append(candidate_headers)
    candidate_records = []
    if MARKET_CANDIDATES_CSV.exists():
        try:
            with MARKET_CANDIDATES_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
                candidate_records = list(csv.DictReader(handle))
        except (OSError, csv.Error):
            candidate_records = []
    candidate_records.sort(key=lambda item: (item.get("observed_at", ""), item.get("product_text", "")), reverse=True)
    for item in candidate_records[:1000]:
        promo_price = as_int(item.get("promo_price"))
        candidate_price = as_int(item.get("candidate_price_hint"))
        indicative_delta = candidate_price - promo_price if candidate_price is not None and promo_price is not None else None
        candidate_sheet.append([
            item.get("product_text") or "—", item.get("merchant_name") or "—", promo_price,
            candidate_price, indicative_delta, item.get("candidate_title") or "—",
            item.get("candidate_domain") or "—", item.get("candidate_url") or "—",
            item.get("candidate_status") or "Belum diketahui", item.get("rejection_reason") or "—",
            item.get("observed_at") or "—",
        ])
    for cell in candidate_sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    candidate_widths = [42, 22, 18, 34, 38, 48, 28, 64, 28, 72, 16]
    for col_idx, width in enumerate(candidate_widths, start=1):
        candidate_sheet.column_dimensions[get_column_letter(col_idx)].width = width
    for row_idx in range(2, candidate_sheet.max_row + 1):
        for col_idx, header in enumerate(candidate_headers, start=1):
            cell = candidate_sheet.cell(row_idx, col_idx)
            cell.alignment = Alignment(vertical="top", wrap_text=header in {
                "Produk Dicari", "Judul Kandidat", "URL untuk Diperiksa", "Status Bukti", "Catatan Validasi",
            })
            if header in {"Harga Promo (Rp)", "Harga dari Cuplikan (Rp) - BELUM TERVERIFIKASI", "Selisih Indikatif vs Promo (Rp) - BUKAN DISKON RESMI"} and isinstance(cell.value, (int, float)):
                cell.number_format = '"Rp" #,##0;[Red]-"Rp" #,##0'
        url_cell = candidate_sheet.cell(row_idx, 8)
        if isinstance(url_cell.value, str) and url_cell.value.startswith(("https://", "http://")):
            url_cell.hyperlink = url_cell.value
            url_cell.style = "Hyperlink"
    if not candidate_records:
        candidate_sheet.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(candidate_headers))
        note = candidate_sheet.cell(2, 1)
        note.value = (
            "Belum ada kandidat harga hasil pencarian yang menunggu validasi. Harga dari OCR poster hanya dipakai setelah ditinjau dan disetujui; kandidat pembanding pasar perlu URL serta bukti harga yang terlihat."
        )
        note.alignment = Alignment(vertical="top", wrap_text=True)
        note.font = Font(italic=True, color="666666")
        candidate_sheet.row_dimensions[2].height = 40
        candidate_sheet.freeze_panes = "A3"
        candidate_sheet.auto_filter.ref = "A1:K1"
    else:
        candidate_sheet.freeze_panes = "A2"
        candidate_sheet.auto_filter.ref = candidate_sheet.dimensions
    candidate_sheet.sheet_view.showGridLines = False
    candidate_sheet.row_dimensions[1].height = 48

    # Simple summary as the initial tab.
    summary = wb.create_sheet("Ringkasan", 0)
    today_text = TODAY.isoformat()
    numeric_discounts = [r["Diskon (%)"] for r in simple_current if isinstance(r.get("Diskon (%)"), (int, float))]
    not_calculated = sum(not isinstance(r.get("Diskon (%)"), (int, float)) for r in simple_current)
    estimated_bundles = sum(r.get("estimated_bundle_discount_percent") not in (None, "") for r in current_rows)
    market_reference_count = sum((as_int(r.get("market_reference_source_count")) or 0) > 0 for r in current_rows)
    below_market_count = sum(r.get("market_reference_status") == "below_market_median" for r in current_rows)
    single_market_source_count = sum(r.get("market_reference_status") == "single_source_only" for r in current_rows)
    summary_rows = [
        ["KATALOG PROMO", ""],
        ["Tanggal referensi", today_text],
        ["Promo pada daftar utama", len(simple_current)],
        ["Diskon bisa dihitung", len(numeric_discounts)],
        ["Diskon belum bisa dihitung", not_calculated],
        ["Estimasi bundling (bukan diskon terkonfirmasi)", estimated_bundles],
        ["Harga normal diambil dari poster OCR yang ditinjau", sum(r.get("normal_price_provenance") == "poster_ocr_reviewed" for r in current_rows)],
        ["Produk dengan pembanding harga pasar", market_reference_count],
        ["Promo di bawah median harga pasar", below_market_count],
        ["Pembanding hanya satu domain (belum kuat)", single_market_source_count],
        ["Kandidat harga belum terverifikasi", len(candidate_records)],
        ["Baris perlu diperiksa", len(combined_review)],
        ["Kolom utama", "Nama Produk · Kategori · Merchant · Harga Normal/Acuan · Harga Promo · Hemat · Diskon · Masa Berlaku"],
        ["Cara membaca", "Harga normal dari poster hanya berlaku setelah ditinjau dan disetujui manual. Diskon dihitung dari harga normal dan promo yang cocok untuk SKU yang sama. Referensi pasar terpisah dari harga yang tercetak pada poster; tab Peluang Harga membutuhkan minimal dua domain independen."],
        ["Verifikasi", "Sumber agregator bukan verifikasi resmi retailer. Periksa sumber URL sebelum menganggap harga berlaku di semua toko/wilayah."],
        ["Riwayat", "Sheet audit teknis disimpan tersembunyi agar file utama sederhana. Data mentah dan log tetap ada di folder _internal."],
    ]
    for row in summary_rows:
        summary.append(row)
    summary.merge_cells("A1:B1")
    summary["A1"].font = Font(bold=True, size=16, color="FFFFFF")
    summary["A1"].fill = header_fill
    summary["A1"].alignment = Alignment(vertical="center")
    summary.row_dimensions[1].height = 30
    for i in range(2, len(summary_rows) + 1):
        summary.cell(i, 1).font = Font(bold=True, color="1F4E78")
        summary.cell(i, 1).alignment = Alignment(vertical="top", wrap_text=True)
        summary.cell(i, 2).alignment = Alignment(vertical="top", wrap_text=True)
    summary.column_dimensions["A"].width = 32
    summary.column_dimensions["B"].width = 110
    summary.sheet_view.showGridLines = False

    # Keep only useful daily tabs visible. Detailed audit sheets still exist for
    # project diagnostics and tests but do not clutter normal spreadsheet use.
    visible = {"Ringkasan", "Promo", "Perlu Diperiksa", "Ringkasan Kategori", "Peluang Harga", "Kandidat Harga"}
    for ws in wb.worksheets:
        ws.sheet_state = "visible" if ws.title in visible else "hidden"
    wb.active = wb.sheetnames.index("Promo")
    return simple_current, combined_review




def write_excel_catalog(path, current_rows, confirmed_rows, review_rows, name_variant_rows, possible_duplicate_rows, rejected_rows=None, export_gate_report=None):
    """User-facing workbook: clean catalogs plus separate review/duplicate/rejected audit sheets."""
    rejected_rows = rejected_rows or []
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        print("openpyxl belum terpasang; file Excel dilewati. Install: pip install openpyxl")
        return False

    wb = Workbook()
    wb.remove(wb.active)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    title_fill = PatternFill("solid", fgColor="DCE6F1")

    # Ringkasan first, so users know where to start.
    ws = wb.create_sheet("Mulai Di Sini")
    summary = [
        ["KATALOG PROMO MULTI-SUMBER", ""],
        ["Tanggal acuan", TODAY.isoformat()],
        ["Promo aktif (estimasi boleh)", len(current_rows)],
        ["Promo aktif dengan periode pasti (bukan verifikasi retailer)", len(confirmed_rows)],
        ["Baris perlu review", len(review_rows)],
        ["Kandidat non-produk ditolak", len(rejected_rows)],
        ["Kelompok variasi nama produk", len(name_variant_rows)],
        ["Kandidat duplikat untuk diperiksa", len(possible_duplicate_rows)],
        ["Kategori utama", len(CATEGORY_TAXONOMY)],
        ["Gerbang kualitas ekspor", f"{(export_gate_report or {}).get('status', 'not_reported')} — {(export_gate_report or {}).get('routed_to_review_rows', 0)} baris dialihkan ke Perlu Review oleh validasi akhir."],
        ["Diskon vs harga", f"{sum((export_gate_report or {}).get('issue_counts', {}).get(k, 0) for k in ('explicit_discount_price_math_disagreement', 'maximum_discount_below_price_calculation'))} temuan klaim diskon yang tidak cocok dengan hitungan harga; klaim sumber dipisah dari Diskon (%) dan baris perlu review."],
        ["Kesegaran Data", "Status per baris: segar ≤7 hari, katalog lama dipertahankan, usang, atau historis/timestamp tidak tersedia. Ini berbeda dari Verifikasi Sumber."],
        ["Status Validasi Produk", "Kolom ini terpisah dari Status Promo. 'Valid' berarti identitas produk dan pemeriksaan harga/merchant lolos gerbang; 'Perlu review' berarti perlu pemeriksaan, meskipun tanggal promo masih berstatus Aktif."],
        ["Sumber produk", "Hemat.id, Katalogpromosi.com, dan Scanharga.com"],
        ["Catatan", "Kepastian periode dipisahkan dari verifikasi sumber; agregator dan artikel publik bukan verifikasi resmi retailer."],
        ["Petunjuk", f"Buka sheet Promo untuk daftar promo sehari-hari. Gunakan filter Kategori Produk dan Subkategori untuk mencari jenis barang. Ringkasan Kategori menunjukkan jumlah promo aktif per kategori; Referensi Kategori berisi daftar {len(CATEGORY_TAXONOMY)} kategori. Daftar Filter Produk mengulang daftar contoh klasifikasi yang disimpan di input/category_review_list.csv."],
        ["File teknis", "Data mentah dan log tersimpan di folder _internal; laporan gerbang ekspor ada di promo_export_quality_gate_report.json."],
    ]
    for row in summary:
        ws.append(row)
    ws.merge_cells("A1:B1")
    ws["A1"].fill = title_fill
    ws["A1"].font = Font(bold=True, size=16, color="1F1F1F")
    ws["A1"].alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 30
    for row_idx in range(2, len(summary) + 1):
        ws.cell(row_idx, 1).font = Font(bold=True, color="1F4E78")
        ws.cell(row_idx, 1).alignment = Alignment(vertical="top", wrap_text=True)
        ws.cell(row_idx, 2).alignment = Alignment(vertical="top", wrap_text=True)
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 90
    ws.sheet_view.showGridLines = False

    # Summary counts make the taxonomy useful immediately, without changing any
    # promo eligibility/status rules. Counts are from the current user-facing sheet.
    category_totals = {name: 0 for name in CATEGORY_NAMES}
    subcategory_totals = Counter()
    for category_row in current_rows:
        category_name, subcategory_name = classify_promo_category(category_row)
        category_totals[category_name] = category_totals.get(category_name, 0) + 1
        subcategory_totals[(category_name, subcategory_name)] += 1
    total_current = len(current_rows)
    ws = wb.create_sheet("Ringkasan Kategori")
    ws.append(["Kategori Produk", "Jumlah Promo Aktif", "Proporsi"] )
    for category_name in CATEGORY_NAMES:
        count = category_totals.get(category_name, 0)
        ws.append([category_name, count, (count / total_current) if total_current else 0])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 14
    for row_idx in range(2, ws.max_row + 1):
        ws.cell(row_idx, 3).number_format = '0.0%'
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False

    ws = wb.create_sheet("Referensi Kategori")
    ws.append(["No.", "Kategori Utama", "Contoh Subkategori", "Panduan"] )
    for idx, (category_name, examples) in enumerate(CATEGORY_TAXONOMY, start=1):
        if category_name == "Lainnya / Perlu Review":
            guidance = "Pakai hanya jika identitas barang/layanan tidak cukup jelas untuk kategori lain."
        elif category_name == "Promo Umum / Multi-Kategori":
            guidance = "Untuk rangkuman promo, kampanye umum atau promo lintas merchant yang bukan SKU individual."
        else:
            guidance = "Klasifikasikan dari nama barang/layanan; detail penawaran dan kampanye hanya menjadi konteks pendukung."
        ws.append([idx, category_name, examples, guidance])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for column, width in {"A": 8, "B": 34, "C": 64, "D": 78}.items():
        ws.column_dimensions[column].width = width
    for row_cells in ws.iter_rows(min_row=2):
        for cell in row_cells:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False

    # Rebuild this QA sheet from a checked-in CSV instead of relying on a
    # manually edited workbook sheet that would disappear at the next export.
    ws = wb.create_sheet("Daftar Filter Produk")
    ws.append(["No.", "Nama Produk dari Daftar", "Kategori Hasil", "Subkategori Hasil", "Status Klasifikasi", "Catatan"])
    review_names = []
    if CATEGORY_REVIEW_LIST_CSV.is_file():
        try:
            with CATEGORY_REVIEW_LIST_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
                review_names = [
                    (row.get("product_text") or row.get("Nama Produk") or "").strip()
                    for row in csv.DictReader(handle)
                ]
        except (OSError, csv.Error) as exc:
            print(f"Peringatan: daftar filter kategori tidak dapat dibaca: {exc}")
    for idx, product_name in enumerate((name for name in review_names if name), start=1):
        category, subcategory = classify_promo_category({"product_text": product_name})
        needs_review = category == "Lainnya / Perlu Review"
        ws.append([
            idx, product_name, category, subcategory,
            "Perlu review manual" if needs_review else "Terkategori otomatis — cek sampel",
            "Identitas nama belum cukup jelas; tidak ditebak." if needs_review else
            "Kategori ditentukan dari jenis produk; audit sampel tetap disarankan.",
        ])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for column, width in {"A": 8, "B": 52, "C": 34, "D": 34, "E": 32, "F": 68}.items():
        ws.column_dimensions[column].width = width
    for row_cells in ws.iter_rows(min_row=2):
        for cell in row_cells:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False

    sheets = [
        ("Aktif", current_rows, False),
        ("Periode Pasti", confirmed_rows, False),
        ("Perlu Review", review_rows, True),
    ]
    for sheet_name, rows, is_review in sheets:
        ws = wb.create_sheet(sheet_name)
        headers = [label for label, _ in CLEAN_COLUMNS] + (["Alasan Review"] if is_review else [])
        ws.append(headers)
        clean_rows = [make_clean_row(r, review=is_review) for r in rows]
        clean_rows.sort(key=clean_sort_key)
        for row in clean_rows:
            ws.append([row.get(h) for h in headers])
        width_by_header = {
            "Nama Produk": 38, "Kategori Produk": 31, "Subkategori": 28,
            "Harga Asli (Rp)": 16, "Basis Harga Asli": 40, "Harga Asli dari Sumber (Rp)": 18, "Harga Normal Referensi (Rp)": 20, "Rentang Referensi Harga (Rp)": 24, "Harga Promo (Rp)": 15,
            "Diskon (%)": 12, "Dasar Diskon": 38, "Status Referensi Harga": 42, "Merchant": 22, "Merchant Acuan Referensi": 24,
            "Median Harga Pasar (Rp)": 20, "Rentang Harga Pasar (Rp)": 24, "Lebih Murah dari Median Pasar (%)": 22,
            "Selisih terhadap Median Pasar (Rp)": 22, "Jumlah Domain Pembanding": 18, "Status Pembanding Pasar": 42,
            "Sumber Pembanding Pasar": 44, "URL Pembanding Pasar": 60, "Observasi Pembanding Pasar": 24, "Keyakinan Pembanding Pasar": 20,
            "Hemat / Selisih Referensi (Rp)": 22, "Umur Referensi (hari)": 16,
            "Tanggal Observasi Referensi": 24, "Sumber Referensi Harga": 24, "URL Referensi Harga": 52,
            "Status Promo": 22, "Kepastian Periode": 20, "Verifikasi Sumber": 34, "Kesegaran Data": 34,
            "Waktu Scrape": 28, "Klaim Diskon Sumber (Review) (%)": 29,
            "Mulai Berlaku": 17, "Berakhir": 17, "Jenis Promo": 22,
            "Detail Penawaran": 52, "Kampanye": 48, "Catatan": 68, "Sumber URL": 52,
            "Alasan Review": 50,
        }
        widths = [width_by_header.get(header, 24) for header in headers]
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for idx, width in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(idx)].width = width
            field = headers[idx - 1]
            for col_cells in ws.iter_rows(min_row=2, min_col=idx, max_col=idx):
                cell = col_cells[0]
                cell.alignment = Alignment(vertical="top", wrap_text=field in {"Nama Produk", "Kategori Produk", "Subkategori", "Kesegaran Data", "Klaim Diskon Sumber (Review) (%)", "Detail Penawaran", "Kampanye", "Catatan", "Sumber URL", "URL Referensi Harga", "URL Pembanding Pasar", "Sumber Pembanding Pasar", "Dasar Diskon", "Sumber Referensi Harga", "Alasan Review"})
                if field in {"Harga Asli (Rp)", "Harga Asli dari Sumber (Rp)", "Harga Normal Referensi (Rp)", "Harga Promo (Rp)", "Hemat / Selisih Referensi (Rp)"}:
                    cell.number_format = '#,##0'
                elif field == "Diskon (%)":
                    cell.number_format = '0.0"%"'
                elif field == "Umur Referensi (hari)":
                    cell.number_format = '0'
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = ws.dimensions
        ws.sheet_view.showGridLines = False

    # Rejected article headings / non-SKU candidates are visible for diagnosis,
    # but deliberately isolated from the active catalog sheets.
    ws = wb.create_sheet("Ditolak (Audit)")
    rejected_headers = [label for label, _ in CLEAN_COLUMNS] + ["Alasan Penolakan"]
    ws.append(rejected_headers)
    rejected_clean_rows = []
    for row in rejected_rows:
        clean = make_clean_row(row, review=False)
        clean["Alasan Penolakan"] = row.get("product_rejection_reason") or row.get("rejection_reason") or "Kandidat tidak lolos validasi produk"
        rejected_clean_rows.append(clean)
    rejected_clean_rows.sort(key=lambda r: ((r.get("Merchant") or "").casefold(), (r.get("Nama Produk") or "").casefold()))
    for row in rejected_clean_rows:
        ws.append([row.get(h) for h in rejected_headers])
    rejected_width_by_header = {
        "Nama Produk": 38, "Kategori Produk": 31, "Subkategori": 28, "Harga Asli dari Sumber (Rp)": 18,
        "Harga Normal Referensi (Rp)": 20, "Harga Promo (Rp)": 15, "Diskon (%)": 12, "Dasar Diskon": 34,
        "Status Referensi Harga": 42,
        "Merchant": 22, "Hemat / Selisih Referensi (Rp)": 22, "Umur Referensi (hari)": 16,
        "Tanggal Observasi Referensi": 24, "Sumber Referensi Harga": 24, "URL Referensi Harga": 52, "Status Promo": 22,
        "Kepastian Periode": 20, "Verifikasi Sumber": 34, "Kesegaran Data": 34,
        "Waktu Scrape": 28, "Klaim Diskon Sumber (Review) (%)": 29,
        "Mulai Berlaku": 17, "Berakhir": 17, "Jenis Promo": 22,
        "Detail Penawaran": 52, "Kampanye": 48, "Catatan": 56,
        "Sumber URL": 52, "Alasan Penolakan": 48,
    }
    rejected_widths = [rejected_width_by_header.get(header, 24) for header in rejected_headers]
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="7F6000")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for idx, width in enumerate(rejected_widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
        field = rejected_headers[idx - 1]
        for col_cells in ws.iter_rows(min_row=2, min_col=idx, max_col=idx):
            cell = col_cells[0]
            cell.alignment = Alignment(vertical="top", wrap_text=field in {"Nama Produk", "Kategori Produk", "Subkategori", "Kesegaran Data", "Klaim Diskon Sumber (Review) (%)", "Detail Penawaran", "Kampanye", "Catatan", "Sumber URL", "Alasan Penolakan"})
            if field in {"Harga Asli (Rp)", "Harga Promo (Rp)", "Hemat (Rp)"}:
                cell.number_format = '#,##0'
            elif field == "Diskon (%)":
                cell.number_format = '0.0"%"'
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False

    # Rebuild a category-change audit on every export. Prior categories are only
    # taken from stored source rows; missing historical labels are not invented.
    ws = wb.create_sheet("Audit Kategori")
    audit_headers = [
        "Sheet Sumber", "Baris Excel", "Nama Produk", "Kategori Sebelumnya",
        "Subkategori Sebelumnya", "Kategori Baru", "Subkategori Baru",
        "Kampanye", "URL Sumber", "Alasan Perubahan",
    ]
    ws.append(audit_headers)
    audit_sheets = [
        ("Aktif", current_rows, False, False),
        ("Periode Pasti", confirmed_rows, False, False),
        ("Perlu Review", review_rows, True, False),
        ("Ditolak (Audit)", rejected_rows, False, True),
    ]
    for audit_sheet_name, audit_source_rows, is_review_sheet, is_rejected_sheet in audit_sheets:
        pairs = [(row, make_clean_row(row, review=is_review_sheet)) for row in audit_source_rows]
        if is_rejected_sheet:
            pairs.sort(key=lambda pair: ((pair[1].get("Merchant") or "").casefold(), (pair[1].get("Nama Produk") or "").casefold()))
        else:
            pairs.sort(key=lambda pair: clean_sort_key(pair[1]))
        for excel_row, (source_row, clean_row) in enumerate(pairs, start=2):
            old_category = str(source_row.get("previous_product_category") or "").strip()
            old_subcategory = str(source_row.get("previous_product_subcategory") or "").strip()
            if not old_category and not old_subcategory:
                continue
            new_category = clean_row.get("Kategori Produk") or ""
            new_subcategory = clean_row.get("Subkategori") or ""
            if old_category == new_category and old_subcategory == new_subcategory:
                continue
            ws.append([
                audit_sheet_name, excel_row, clean_row.get("Nama Produk"), old_category or "(kosong)",
                old_subcategory or "(kosong)", new_category, new_subcategory,
                clean_row.get("Kampanye"), clean_row.get("Sumber URL"),
                "Klasifikasi dihitung ulang dari jenis produk oleh classifier saat ini.",
            ])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="8064A2")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for column, width in {"A": 20, "B": 14, "C": 42, "D": 32, "E": 32, "F": 34, "G": 34, "H": 48, "I": 58, "J": 68}.items():
        ws.column_dimensions[column].width = width
    for row_cells in ws.iter_rows(min_row=2):
        for cell in row_cells:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False

    ws = wb.create_sheet("Kemungkinan Duplikat")
    ws.append(POSSIBLE_DUPLICATE_FIELDS)
    for row in possible_duplicate_rows:
        ws.append([row.get(h) for h in POSSIBLE_DUPLICATE_FIELDS])
    candidate_widths = [16, 16, 38, 38, 18, 18, 16, 16, 16, 16, 24, 24, 44, 44, 60, 60, 56, 48]
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="9C6500")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for idx, width in enumerate(candidate_widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
        for col_cells in ws.iter_rows(min_row=2, min_col=idx, max_col=idx):
            cell = col_cells[0]
            cell.alignment = Alignment(vertical="top", wrap_text=idx not in {2, 7, 8, 9, 10})
            if idx in {2}:
                cell.number_format = '0.0"%"'
            elif idx in {7, 8, 9, 10}:
                cell.number_format = '#,##0'
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False

    ws = wb.create_sheet("Variasi Nama")
    variant_headers = ["Nama Standar", "Variasi Nama Asal", "Merchant", "Jumlah Variasi", "Jumlah Baris Promo", "Harga Promo (Rp)", "Mulai Berlaku", "Berakhir", "Sumber", "Catatan"]
    ws.append(variant_headers)
    for row in name_variant_rows:
        ws.append([row.get(h) for h in variant_headers])
    variant_widths = [38, 72, 18, 15, 18, 30, 28, 28, 54, 62]
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for idx, width in enumerate(variant_widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
        for col_cells in ws.iter_rows(min_row=2, min_col=idx, max_col=idx):
            cell = col_cells[0]
            cell.alignment = Alignment(vertical="top", wrap_text=idx in {1, 2, 8, 9})
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False

    # A visible, auditable view of the persistent reference-price database,
    # including user-provided ranges and the provenance of every estimate.
    reference_records = load_reference_database(PRICE_REFERENCE_CSV)
    ws = wb.create_sheet("Referensi Harga")
    reference_headers = [
        "Merchant Acuan", "Nama Produk Referensi / Alias", "Harga Normal Referensi (Rp)",
        "Harga Minimum (Rp)", "Harga Maksimum (Rp)", "Satuan / Basis Harga", "Asal Rekaman",
        "Status Verifikasi", "Tanggal Observasi/Referensi", "Sumber / Provenans", "URL Sumber",
        "Bukti / Rationale", "Kampanye / Catatan Sumber", "Catatan Referensi", "Kanal / Wilayah", "Kunci Produk",
    ]
    ws.append(reference_headers)
    for record in sorted(reference_records, key=lambda r: (
        str(r.get("merchant_name") or "").casefold(),
        str(r.get("product_text") or "").casefold(),
        str(r.get("record_origin") or ""),
        str(r.get("observed_at") or ""),
    )):
        midpoint = _price_as_integer(record.get("normal_price"))
        low = _price_as_integer(record.get("normal_price_low")) or midpoint
        high = _price_as_integer(record.get("normal_price_high")) or midpoint
        ws.append([
            record.get("merchant_name"), record.get("product_text"), midpoint, low, high,
            record.get("price_unit"), record.get("record_origin"), record.get("verification_status"),
            record.get("observed_at"), record.get("source_name"), record.get("source_url"),
            record.get("evidence_text"), record.get("campaign_title"), record.get("reference_note"),
            record.get("region_or_channel"), record.get("product_key"),
        ])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for col, width in {"A": 22, "B": 42, "C": 20, "D": 18, "E": 18, "F": 18, "G": 22, "H": 25, "I": 28, "J": 48, "K": 54, "L": 60, "M": 54, "N": 50, "O": 20, "P": 45}.items():
        ws.column_dimensions[col].width = width
    for row_cells in ws.iter_rows(min_row=2):
        for cell in row_cells:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for col in ("C", "D", "E"):
        for cell in ws[col][1:]:
            cell.number_format = '#,##0'
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False

    # Canonical user-provided ranges remain separate from retailer/source observations.
    range_path = ROOT_DIR / "data" / "user_product_price_ranges.csv"
    ws = wb.create_sheet("Rentang Referensi Pengguna")
    user_range_headers = [
        "Kategori", "Produk / SKU Acuan", "Harga Minimum (Rp)", "Harga Maksimum (Rp)",
        "Titik Tengah Estimasi (Rp)", "Satuan / Basis Harga", "Merchant Prioritas",
        "Sumber / Provenans", "Tanggal Input", "Catatan",
    ]
    ws.append(user_range_headers)
    if range_path.exists():
        with range_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for item in csv.DictReader(handle):
                ws.append([
                    item.get("category"), item.get("product_text"),
                    _price_as_integer(item.get("normal_price_low")),
                    _price_as_integer(item.get("normal_price_high")),
                    _price_as_integer(item.get("normal_price")), item.get("price_unit"),
                    item.get("merchant_name"), item.get("reference_source"),
                    item.get("observed_at"), item.get("note"),
                ])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for col, width in {"A": 40, "B": 60, "C": 19, "D": 19, "E": 22, "F": 18, "G": 22, "H": 56, "I": 16, "J": 62}.items():
        ws.column_dimensions[col].width = width
    for row_cells in ws.iter_rows(min_row=2):
        for cell in row_cells:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for col in ("C", "D", "E"):
        for cell in ws[col][1:]:
            cell.number_format = '#,##0'
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False

    # Technical market references stay auditable separately from retailer normal-price records.
    market_records = load_market_reference_database(MARKET_REFERENCE_CSV)
    ws = wb.create_sheet("Referensi Harga Pasar")
    market_headers = [
        "Produk Kunci", "Nama Produk Query", "Harga Pembanding (Rp)", "Tanggal Observasi",
        "Nama Sumber", "Domain", "URL Sumber", "Judul Halaman", "Bukti Harga yang Diverifikasi",
        "Jenis Harga", "Jenis Sumber", "Metode Verifikasi", "Keyakinan", "Kunci Referensi",
    ]
    ws.append(market_headers)
    for record in sorted(market_records, key=lambda r: (r.get("product_key", ""), r.get("source_domain", ""), r.get("observed_at", ""))):
        ws.append([
            record.get("product_key"), record.get("product_text"), _price_as_integer(record.get("market_price")),
            record.get("observed_at"), record.get("source_name"), record.get("source_domain"),
            record.get("source_url"), record.get("page_title"), record.get("evidence_text"),
            record.get("price_kind"), record.get("source_kind"), record.get("verification_method"),
            record.get("confidence"), record.get("reference_key"),
        ])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for col, width in {"A": 42, "B": 48, "C": 20, "D": 22, "E": 28, "F": 28, "G": 60, "H": 48, "I": 70, "J": 16, "K": 18, "L": 24, "M": 18, "N": 28}.items():
        ws.column_dimensions[col].width = width
    for row_cells in ws.iter_rows(min_row=2):
        for cell in row_cells:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for cell in ws["C"][1:]:
        cell.number_format = '#,##0'
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False

    _build_simple_workbook_sheets(wb, current_rows, review_rows, header_fill, title_fill)
    wb.save(path)
    return True


def build_source_freshness_report(all_loaded_rows, now_dt=None, source_run_metadata=None):
    """Summarize row freshness without labelling mixed/historical data fully fresh."""
    from collections import defaultdict
    now_dt = now_dt or datetime.now().astimezone()
    per_source = defaultdict(list)

    def split_values(*values):
        result = []
        for value in values:
            for part in str(value or "").split(" | "):
                part = part.strip()
                if part and part not in result:
                    result.append(part)
        return result

    for row in all_loaded_rows:
        # scraped_at belongs to the row's primary source_name. Do not copy its
        # freshness timestamp to every name in a multi-source provenance string.
        primary_name = str(row.get("source_name") or "").strip()
        provenance_names = split_values(row.get("source_names"))
        if primary_name:
            names = [primary_name]
        elif len(provenance_names) == 1:
            names = provenance_names
        elif len(provenance_names) > 1:
            names = ["Multi-source provenance; freshness not attributable"]
        else:
            dataset = str(row.get("source_dataset") or "").strip()
            if "multi" in dataset.casefold():
                names = ["Multi-source aggregator"]
            else:
                names = ["Source tidak diketahui"]
        for name in names:
            per_source[name].append(row)

    source_run_metadata = source_run_metadata or {}
    for name in source_run_metadata:
        per_source.setdefault(name, [])

    output = {}
    component_order = ["fresh", "preserved_previous", "stale", "historical_or_unverified", "invalid_timestamp"]
    base_status = {
        "fresh": "fresh",
        "preserved_previous": "preserved_previous",
        "stale": "stale",
        "historical_or_unverified": "historical_or_unverified",
        "invalid_timestamp": "invalid_timestamp",
        "no_rows": "no_rows",
    }
    for name, rows in sorted(per_source.items(), key=lambda item: item[0].casefold()):
        counts = Counter()
        timestamped = 0
        for row in rows:
            status = classify_row_freshness(
                row.get("scraped_at"), row.get("retained_from_previous_run"), now_dt
            )
            counts[status] += 1
            if str(row.get("scraped_at") or "").strip():
                try:
                    datetime.fromisoformat(str(row.get("scraped_at")).strip().replace("Z", "+00:00"))
                    timestamped += 1
                except (TypeError, ValueError, OverflowError):
                    pass
        components = [component for component in component_order if counts[component] > 0]
        if not rows:
            status = "no_rows"
        elif len(components) == 1:
            status = base_status[components[0]]
        elif components:
            status = "mixed_" + "_and_".join(components)
        else:
            status = "historical_or_unverified"

        item = {
            "status": status,
            "row_count": len(rows),
            "fresh_rows": counts["fresh"],
            "preserved_previous_rows": counts["preserved_previous"],
            "stale_rows_over_7_days": counts["stale"],
            "rows_missing_or_invalid_timestamp": counts["historical_or_unverified"] + counts["invalid_timestamp"],
            "rows_with_parseable_timestamp": timestamped,
            "invalid_or_future_timestamp_rows": counts["invalid_timestamp"],
            "all_rows_fresh": bool(rows) and counts["fresh"] == len(rows),
            "fresh_row_percent": round(100 * counts["fresh"] / len(rows), 1) if rows else None,
            "freshness_components": components,
        }
        run_meta = source_run_metadata.get(name)
        if run_meta:
            run_meta = dict(run_meta)
            output_status = str(run_meta.get("output_status") or "unknown")
            validation = run_meta.get("validation")
            run_date_current = run_meta.get("run_log_is_current_date")
            run_meta["previous_catalog_preserved"] = output_status == "preserved_previous_catalog"
            # Source health and output status are not content validation.
            # Missing validation payload must be unknown, not False.
            if isinstance(validation, dict) and isinstance(validation.get("valid"), bool):
                run_meta["validation_passed"] = validation["valid"]
                run_meta["validation_status"] = "passed" if validation["valid"] else "failed"
            else:
                run_meta["validation_passed"] = None
                run_meta["validation_status"] = "not_reported"
            if output_status == "preserved_previous_catalog":
                run_meta["freshness_note"] = (
                    "Run log terakhir menunjukkan percobaan tidak menggantikan katalog stabil; "
                    "baris kandidat baru hanya menunjukkan percobaan ekstraksi, bukan pembaruan katalog penuh."
                )
            elif output_status == "updated":
                if run_meta.get("validation_status") == "not_reported":
                    run_meta["freshness_note"] = (
                        "Run log menunjukkan output sumber diperbarui; kesegaran dihitung per baris. "
                        "Status validasi konten per sumber tidak dilaporkan oleh collector dan tidak boleh "
                        "disimpulkan hanya dari status fresh/updated."
                    )
                elif run_meta.get("validation_status") == "failed":
                    run_meta["freshness_note"] = (
                        "Output sumber diperbarui, tetapi validasi sumber secara eksplisit gagal; "
                        "status segar tidak membuat data tersebut otomatis layak dipublikasikan."
                    )
                else:
                    run_meta["freshness_note"] = (
                        "Output sumber diperbarui dan run log melaporkan validasi sumber lulus; "
                        "kesegaran tetap dihitung per baris dan bukan bukti verifikasi retailer."
                    )
            elif output_status == "mixed_fresh_and_preserved":
                run_meta["freshness_note"] = "Sumber menghasilkan baris baru dan mempertahankan sebagian baris lama; jangan menganggap seluruh sumber segar."
            elif output_status == "request_failed":
                run_meta["freshness_note"] = "Request sumber gagal; baris yang ada mungkin merupakan data lama yang dipertahankan."
            else:
                run_meta["freshness_note"] = "Status run belum cukup untuk menyatakan seluruh data segar."
            if run_date_current is False:
                run_meta["freshness_note"] = (
                    f"Run log berasal dari {str(run_meta.get('checked_at') or 'tanggal tidak diketahui')[:10]}, "
                    "bukan tanggal referensi saat ini; status tersebut bukan bukti scraping hari ini. "
                    + run_meta["freshness_note"]
                )
            item["current_run"] = run_meta
        if counts["fresh"] and (counts["historical_or_unverified"] or counts["stale"] or counts["preserved_previous"] or counts["invalid_timestamp"]):
            item["freshness_note"] = "Campuran data baru dan data lama/tidak terverifikasi; label sumber tidak boleh dibaca sebagai seluruh baris segar."
        output[name] = item
    return output


def current_source_run_metadata():
    """Read generic health metadata for Hemat.id, Katalogpromosi.com, and Scanharga.com."""
    run_log_path = MULTISOURCE_RUN_LOG
    if not run_log_path.exists():
        return {}
    try:
        log = json.loads(run_log_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}

    checked_at = str(log.get("built_at") or "")
    reference_date = str(log.get("reference_date") or "")
    is_current = bool(checked_at[:10] == TODAY.isoformat())
    results = {}
    health = log.get("source_health") or {}
    for source_name, stats in health.items():
        stats = dict(stats or {})
        status = str(stats.get("status") or "unknown")
        preserved = as_int(stats.get("preserved_previous_rows")) or 0
        fresh = as_int(stats.get("fresh_product_rows")) or 0
        if status == "preserved_previous" or (preserved > 0 and fresh == 0):
            output_status = "preserved_previous_catalog"
        elif status == "fresh" and preserved > 0:
            output_status = "mixed_fresh_and_preserved"
        elif status == "fresh":
            output_status = "updated"
        elif status in {"request_failed", "blocked_or_limited"}:
            output_status = "request_failed"
        else:
            output_status = status
        results[source_name] = {
            **stats,
            "checked_at": checked_at or None,
            "reference_date": reference_date or None,
            "output_status": output_status,
            "source_health_status": status,
            "run_log_is_current_date": is_current,
        }
    return results

def archive_legacy_price_estimates():
    # Preserve old guess-only price rows before the active database is rewritten.
    if not PRICE_REFERENCE_CSV.exists():
        return 0
    try:
        with PRICE_REFERENCE_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
            raw_rows = list(csv.DictReader(handle))
    except (OSError, csv.Error):
        return 0
    legacy = [row for row in raw_rows if str(row.get("record_origin") or "").casefold() == "user_supplied_estimate"]
    if not legacy:
        return 0
    archive_path = INTERNAL_DIR / "legacy_price_estimates_disabled.csv"
    previous = {}
    if archive_path.exists():
        try:
            with archive_path.open("r", encoding="utf-8-sig", newline="") as handle:
                previous = {str(row.get("reference_key") or ""): row for row in csv.DictReader(handle)}
        except (OSError, csv.Error):
            previous = {}
    for row in legacy:
        key = str(row.get("reference_key") or "") or "|".join(str(row.get(k) or "") for k in ("product_key", "normal_price", "observed_at"))
        previous[key] = row
    fields = list(dict.fromkeys([*REFERENCE_FIELDS, *[key for row in legacy for key in row.keys()]]))
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(previous.values())
    return len(legacy)


def main():
    INTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not MULTISOURCE_CSV.exists():
        raise FileNotFoundError(
            f"Data collector belum ada: {MULTISOURCE_CSV.relative_to(ROOT_DIR)}. "
            "Jalankan Jalankan_Promo.bat agar tiga sumber dikumpulkan terlebih dahulu."
        )
    multisource_rows = load_source(MULTISOURCE_CSV, MULTISOURCE_CSV.name)
    poster_ocr_stats = apply_reviewed_poster_prices(multisource_rows, DEFAULT_OVERRIDES_CSV)
    if not multisource_rows:
        raise RuntimeError(
            "Collector menghasilkan 0 baris. Ekspor final dibatalkan agar katalog lama tidak ditimpa. "
            "Periksa promo_multisource_runlog.json."
        )
    print("Baris dari tiga sumber agregator:", len(multisource_rows))
    print("Harga poster OCR yang disetujui cocok:", poster_ocr_stats.get("rows_updated", 0))
    print("Konflik harga poster OCR (tidak diterapkan):", poster_ocr_stats.get("rows_conflicted", 0))
    print("Koreksi poster OCR belum cocok dengan produk/sumber:", poster_ocr_stats.get("rows_unmatched", 0))
    all_loaded_rows = multisource_rows
    archived_estimates = archive_legacy_price_estimates()
    if archived_estimates:
        print("Estimasi lama yang baru dipindahkan ke arsip:", archived_estimates)
    archive_path = INTERNAL_DIR / "legacy_price_estimates_disabled.csv"
    try:
        with archive_path.open("r", encoding="utf-8-sig", newline="") as handle:
            archived_legacy_count = sum(1 for _ in csv.DictReader(handle))
    except (OSError, csv.Error):
        archived_legacy_count = 0
    price_reference_update = update_reference_database(PRICE_REFERENCE_CSV, all_loaded_rows, TODAY)
    all_price_reference_records = load_reference_database(PRICE_REFERENCE_CSV)
    price_reference_records = [r for r in all_price_reference_records if r.get("record_origin") != "user_supplied_estimate"]
    market_reference_records = load_market_reference_database(MARKET_REFERENCE_CSV)
    print("Observasi harga normal baru tersimpan:", price_reference_update.get("observations_added", 0))
    print("Referensi harga normal bersumber tersedia:", len(price_reference_records))
    print("Estimasi AI/rentang lama hanya di arsip audit:", archived_legacy_count)
    print("Estimasi AI/rentang yang dipakai aktif:", sum(1 for r in all_price_reference_records if r.get("record_origin") == "user_supplied_estimate"))
    print("Referensi harga pasar tervalidasi tersedia:", len(market_reference_records))
    rejected_product_rows = [r for r in all_loaded_rows if r.get("product_validation_status") == "rejected"]
    input_rows = [r for r in all_loaded_rows if r.get("product_validation_status") != "rejected"]
    # Fuzzy matching belongs here, after all source outputs are loaded and bad
    # non-product/article-headline rows are quarantined, but before final export.
    merged_raw = merge_rows(input_rows)
    merged_raw = mark_overlapping_offers(merged_raw)
    consolidated, consolidated_groups, consolidated_rows = consolidate_overlapping_offers(merged_raw)
    consolidated, equivalent_offer_groups, equivalent_offer_rows_absorbed = merge_equivalent_cross_source_offers(consolidated)
    price_reference_apply_summary = apply_price_reference(consolidated, price_reference_records, today=TODAY)
    market_price_apply_summary = compare_market_prices(consolidated, market_reference_records, today=TODAY)

    # Quality gate: a row can have an active period and still be unfit for the
    # public-facing catalog. Quarantine identity, merchant, category, source-link,
    # and unsupported-price problems before any user-facing file is overwritten.
    current, export_gate_report = apply_export_quality_gate(consolidated)
    OUT_GATE_REPORT.write_text(json.dumps(export_gate_report, ensure_ascii=False, indent=2), encoding="utf-8")
    if export_gate_report.get("status") != "passed":
        raise RuntimeError(
            "Gerbang kualitas ekspor gagal; katalog sebelumnya tidak ditimpa. "
            f"Lihat {OUT_GATE_REPORT.relative_to(ROOT_DIR)}"
        )

    confirmed = [
        r for r in current
        if r.get("promo_status") == "active"
        and r.get("period_certainty") == "confirmed"
        and not has_material_period_conflict(r)
    ]
    review = [r for r in consolidated if (
        r.get("product_validation_status") == "review"
        or r.get("promo_status") in {"active_estimated", "upcoming_estimated", "unknown"}
        or r.get("period_flags")
        or r.get("overlap_period_flag")
        or r.get("price_quality_flag")
        or r.get("discount_percent_confidence") == "low"
    )]

    name_variant_rows = build_name_variant_rows(consolidated)
    possible_duplicate_rows = build_possible_duplicate_rows(current)
    write_clean_rows(OUT_ACTIVE, current)
    write_clean_rows(OUT_CONFIRMED, confirmed)
    write_clean_rows(OUT_REVIEW, review, review=True)
    write_audit_rows(OUT_FULL, merged_raw)
    write_audit_rows(OUT_REJECTED_PRODUCTS, rejected_product_rows)
    write_possible_duplicate_csv(OUT_POSSIBLE_DUPES, possible_duplicate_rows)
    write_simple_catalog_csv(OUT_SIMPLE_CSV, current)
    write_excel_catalog(OUT_XLSX, current, confirmed, review, name_variant_rows, possible_duplicate_rows, rejected_product_rows, export_gate_report)

    discount_basis = Counter(r.get("discount_percent_basis") or "not_available" for r in current)
    merchant_counts = Counter(r.get("merchant_name") or "UNKNOWN" for r in current)
    source_run_metadata = current_source_run_metadata()
    source_freshness = build_source_freshness_report(
        all_loaded_rows, source_run_metadata=source_run_metadata
    )
    report = {
        "built_at": timestamp(),
        "reference_date": TODAY.isoformat(),
        "input_files": {MULTISOURCE_CSV.name: len(multisource_rows)},
        "price_reference_database": {
            **price_reference_update,
            "path": PRICE_REFERENCE_CSV.relative_to(ROOT_DIR).as_posix(),
            "records_available_for_matching": len(price_reference_records),
            "max_match_age_days": PRICE_REFERENCE_MAX_AGE_DAYS,
            "policy": "Exact normalized product + merchant + region/channel match. Historical normal-price references are not official retailer verification; stale, conflicting, or non-comparable references are not used to infer discounts.",
        },
        "price_reference_matching": price_reference_apply_summary,
        "input_row_count": len(all_loaded_rows),
        "eligible_product_input_rows": len(input_rows),
        "rejected_non_product_rows": len(rejected_product_rows),
        "rejected_non_product_reasons": dict(Counter(r.get("product_rejection_reason") or "unknown" for r in rejected_product_rows)),
        "product_identity_review_input_rows": sum(r.get("product_validation_status") == "review" for r in all_loaded_rows),
        "product_identity_review_reasons": dict(Counter(r.get("product_review_reason") or "unknown" for r in all_loaded_rows if r.get("product_validation_status") == "review")),
        "price_provenance_review_rows": sum("price_without_matching_offer_evidence" in str(r.get("price_quality_flag") or "") or "without_matching_offer" in str(r.get("price_quality_flag") or "") for r in all_loaded_rows),
        "rows_missing_period_evidence": sum("period_evidence_missing_unverified" in str(r.get("period_flags") or "") for r in all_loaded_rows),
        "period_certainty_counts_current": dict(Counter(r.get("period_certainty") or "unknown" for r in current)),
        "source_verification_counts_current": dict(Counter(r.get("source_verification_label") or "Belum diverifikasi" for r in current)),
        "category_counts_current": dict(Counter(classify_promo_category(r)[0] for r in current)),
        "subcategory_counts_current": dict(Counter(f"{classify_promo_category(r)[0]} > {classify_promo_category(r)[1]}" for r in current)),
        "source_freshness": source_freshness,
        "source_run_status": source_run_metadata,
        "exact_deduplicated_row_count": len(merged_raw),
        "exact_duplicate_rows_collapsed": len(input_rows) - len(merged_raw),
        "overlapping_offer_groups_consolidated": consolidated_groups,
        "overlapping_offer_rows_absorbed": consolidated_rows,
        "equivalent_cross_source_offer_groups_merged": equivalent_offer_groups,
        "equivalent_cross_source_offer_rows_absorbed": equivalent_offer_rows_absorbed,
        "consolidated_row_count": len(consolidated),
        "current_active_including_estimates": len(current),
        "discount_price_mismatch_review_rows": sum(
            any(issue in str(row.get("export_gate_flags") or "") for issue in (
                "explicit_discount_price_math_disagreement", "maximum_discount_below_price_calculation"
            )) for row in consolidated
        ),
        "export_quality_gate": {
            "status": export_gate_report.get("status"),
            "status_meaning": (
                "passed berarti tidak ada isu pemblokir tersisa pada baris yang diterima; "
                "kandidat yang bermasalah tetap dipisahkan ke review dan tidak dianggap valid."
            ),
            "candidate_active_rows": export_gate_report.get("candidate_active_rows", 0),
            "accepted_active_rows": export_gate_report.get("accepted_active_rows", 0),
            "routed_to_review_rows": export_gate_report.get("routed_to_review_rows", 0),
            "blocking_rows_remaining": export_gate_report.get("blocking_rows_remaining", []),
            "issue_counts": export_gate_report.get("issue_counts", {}),
        },
        "source_validation_reporting_policy": (
            "validation_passed hanya boolean jika run log sumber memuat hasil validation.valid secara eksplisit; "
            "jika tidak, nilainya null dan validation_status=not_reported. source_health_status dan output_status "
            "mengukur request/freshness/output, bukan validasi identitas semua produk."
        ),
        "active_period_conflicts_routed_to_review": sum(
            r.get("promo_status") in {"active", "active_estimated"} and has_material_period_conflict(r)
            for r in consolidated
        ),
        "confirmed_active_rows": len(confirmed),
        "review_rows": len(review),
        "rows_with_product_discount": sum(r.get("discount_percent") is not None for r in current),
        "rows_with_price_calculated_discount": sum(r.get("discount_percent_calculated") is not None for r in current),
        "rows_with_reference_calculated_discount": sum(r.get("discount_percent_basis") == "calculated_from_price_reference" for r in current),
        "rows_with_bundle_discount_estimate": sum(r.get("estimated_bundle_discount_percent") is not None for r in current),
        "rows_with_campaign_level_discount_claim": sum(r.get("campaign_discount_percent") is not None for r in current),
        "rows_with_period_conflict": sum("title_lead_period_differs" in (r.get("period_flags") or "") for r in merged_raw),
        "rows_with_overlapping_offer_periods_before_consolidation": sum(bool(r.get("overlap_period_flag")) for r in merged_raw),
        "cross_dataset_duplicate_groups_or_rows": sum(bool(r.get("cross_dataset_duplicate")) for r in merged_raw),
        "product_name_variant_groups": len(name_variant_rows),
        "possible_duplicate_candidate_pairs": len(possible_duplicate_rows),
        "possible_duplicate_high_similarity_pairs": sum(float(r.get("Kemiripan Nama (%)") or 0) >= 93 for r in possible_duplicate_rows),
        "possible_duplicate_review_priority_counts": dict(Counter(r.get("Prioritas Review") or "Tidak diketahui" for r in possible_duplicate_rows)),
        "merchant_resolution_method_counts_current": dict(Counter(r.get("merchant_resolution_method") or "tidak tercatat" for r in current)),
        "cross_sku_gift_rows_in_review": sum(
            r.get("discount_percent_basis") == "cross_sku_bundle_review"
            or "cross_sku_gift_not_product_discount" in str(r.get("price_quality_flag") or "")
            for r in review
        ),
        "discount_basis_counts_current": dict(discount_basis),
        "merchant_counts_current": dict(merchant_counts),
        "market_price_comparison": market_price_apply_summary,
        "market_price_comparison_policy": (
            "Harga pembanding pasar berasal dari URL publik yang harganya telah diverifikasi pada halaman. "
            "Median dihitung dari domain berbeda yang cocok identitas/ukuran/varian dan berumur maksimal 14 hari. "
            "Minimal dua domain diperlukan untuk menampilkan persentase lebih murah; hasilnya bukan diskon resmi retailer."
        ),
        "user_facing_output_files": [OUT_ACTIVE.relative_to(ROOT_DIR).as_posix(), OUT_SIMPLE_CSV.relative_to(ROOT_DIR).as_posix(), OUT_XLSX.relative_to(ROOT_DIR).as_posix()],
        "output_contracts": {
            OUT_ACTIVE.name: "Katalog bersih dengan kolom provenance lebih lengkap dan nilai numerik kosong bila tidak tersedia; cocok untuk pengolahan data.",
            OUT_SIMPLE_CSV.name: "Kolom dan nilai tampilan identik dengan sheet Promo pada workbook Excel, termasuk label Belum bisa dihitung.",
            OUT_XLSX.name: "Workbook ringkas untuk penggunaan harian serta sheet audit teknis tersembunyi.",
        },
        "internal_audit_files": [OUT_CONFIRMED.relative_to(ROOT_DIR).as_posix(), OUT_REVIEW.relative_to(ROOT_DIR).as_posix(), OUT_FULL.relative_to(ROOT_DIR).as_posix(), OUT_REJECTED_PRODUCTS.relative_to(ROOT_DIR).as_posix(), OUT_GATE_REPORT.relative_to(ROOT_DIR).as_posix(), OUT_REPORT.relative_to(ROOT_DIR).as_posix()],
        "clean_catalog_columns": [label for label, _ in CLEAN_COLUMNS],
        "full_audit_file": OUT_FULL.name,
        "discount_policy": {
            "explicit_product_offer_percent": "only displayed as a validated product discount when it does not materially contradict comparable product-level prices; mismatches are routed to review and the source claim is preserved separately",
            "discount_price_math_tolerance_percentage_points": str(DISCOUNT_PRICE_MATH_TOLERANCE),
            "price_based_percent": "calculated only when normal and promo prices are positive, unit-comparable, and price_basis is offer_price; Indonesian thousands separators are parsed safely",
            "period_confirmation": "Periode Pasti requires exact range/single_day plus visible text evidence and no unresolved material conflict; it is not retailer verification",
            "price_provenance": "Current offer prices need visible matching offer evidence. A missing normal price may be compared to a recent exact product + merchant reference, falling back to an exact other-merchant reference only when a recent same-merchant reference is unavailable. The reference price is displayed separately and is never represented as a current source price.",
            "price_reference": f"Only explicit normal-price evidence is recorded from fresh, eligible source rows. Matching prefers exact normalized SKU/size/variant for the same merchant and region/channel; if no recent same-merchant reference exists, an exact fresh reference from another merchant may be used and is labelled as an estimate. Reference age <= {PRICE_REFERENCE_MAX_AGE_DAYS} days. Bundles/conditions, stale references, and fuzzy-only matches are not used for calculated discounts.",
            "source_verification": "aggregator_only is distinct from retailer_verified; never imply official retailer verification",
            "buy_x_free_y": "separate low-confidence estimate assuming the free item has the same value; not a confirmed discount",
            "cross_sku_gift": "if named gift item differs from the SKU on the row, bundle estimate is cleared as product discount and row is routed to review",
            "campaign_title_percent": "stored separately and never copied to every product row",
        },
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("DONE")
    print("input rows:", len(input_rows))
    print("multi-aggregator input rows:", len(multisource_rows))
    print("exact deduplicated rows:", len(merged_raw))
    print("consolidated rows:", len(consolidated))
    print("overlapping offer rows absorbed:", consolidated_rows)
    print("equivalent cross-source offer groups merged:", equivalent_offer_groups)
    print("equivalent cross-source offer rows absorbed:", equivalent_offer_rows_absorbed)
    print("Promo periode aktif (termasuk periode estimasi):", len(current))
    print("confirmed active:", len(confirmed))
    print("review rows:", len(review))
    print("rows with product discount:", report["rows_with_product_discount"])
    print("recent price references matched:", price_reference_apply_summary["exact_recent_references_found"])
    print("discounts calculated from references:", price_reference_apply_summary["reference_calculated_discounts"])
    print("market references attached:", market_price_apply_summary["rows_with_market_reference"])
    print("promos below market median:", market_price_apply_summary["below_market_median"])
    print("market reference database:", MARKET_REFERENCE_CSV.relative_to(ROOT_DIR))
    print("price reference database:", PRICE_REFERENCE_CSV.relative_to(ROOT_DIR))
    print("clean catalog columns:", ", ".join(label for label, _ in CLEAN_COLUMNS))
    print("KATALOG UTAMA:", OUT_XLSX.relative_to(ROOT_DIR))
    print("CSV UTAMA:", OUT_ACTIVE.relative_to(ROOT_DIR))
    print("Data teknis/audit disimpan di folder:", INTERNAL_DIR.name)
    print("Kelompok variasi nama produk:", len(name_variant_rows))
    print("Kandidat duplikat untuk diperiksa:", len(possible_duplicate_rows))
    print("CSV kandidat duplikat:", OUT_POSSIBLE_DUPES.relative_to(ROOT_DIR))


if __name__ == "__main__":
    main()
