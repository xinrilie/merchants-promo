from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, OrderedDict, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import importlib.util
import math

from promo_names import (
    clean_product_name, name_variants, product_name_similarity,
    product_names_can_auto_merge, product_name_block_key,
    product_name_size_signature, product_name_variant_signature,
    product_name_identity_warnings,
)

ENGINE_DIR = Path(__file__).resolve().parent
ROOT_DIR = ENGINE_DIR.parent
INPUT_DIR = ROOT_DIR / "input"
INTERNAL_DIR = ROOT_DIR / "_internal"
OUTPUT_DIR = ROOT_DIR / "hasil"
OLD_CSV = INPUT_DIR / "momskonten_promo_active.csv"
NEW_CSV = INPUT_DIR / "promo_catalog_all_merchants.csv"
MULTISOURCE_CSV = INTERNAL_DIR / "promo_multisource_products_all.csv"  # dibuat oleh collector
OUT_ACTIVE = OUTPUT_DIR / "Katalog_Promo_Final.csv"
OUT_CONFIRMED = INTERNAL_DIR / "promo_catalog_multisource_confirmed.csv"
OUT_REVIEW = INTERNAL_DIR / "promo_catalog_multisource_review.csv"
OUT_FULL = INTERNAL_DIR / "promo_catalog_multisource_full_audit.csv"
OUT_REPORT = INTERNAL_DIR / "promo_catalog_multisource_quality_report.json"
OUT_POSSIBLE_DUPES = INTERNAL_DIR / "promo_possible_duplicate_candidates.csv"
OUT_XLSX = OUTPUT_DIR / "Katalog_Promo_Final.xlsx"

# Reuse the exact parsing/discount logic that is in the revised scraper.
spec = importlib.util.spec_from_file_location(
    "promo_scraper_v2", ENGINE_DIR / "promo_catalog_all_merchants_v2.py"
)
if spec is None or spec.loader is None:
    raise RuntimeError("Tidak dapat memuat promo_catalog_all_merchants_v2.py")
scraper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scraper)

REQUIRED_SCRAPER_FUNCTIONS = (
    "normalize_text", "infer_merchant", "campaign_type", "parse_period_details",
    "promo_status", "parse_explicit_discount", "discount_fields", "timestamp",
)
_missing_functions = [name for name in REQUIRED_SCRAPER_FUNCTIONS if not hasattr(scraper, name)]
if _missing_functions:
    raise RuntimeError(
        "Versi promo_catalog_all_merchants_v2.py tidak kompatibel. "
        "Fungsi yang belum tersedia: " + ", ".join(_missing_functions) + ". "
        "Gunakan file scraper v2 yang disertakan bersama enhancer ini."
    )

TODAY = scraper.TODAY

FIELDS = [
    "product_text", "original_product_text", "product_name_variants", "merchant_name", "campaign_title", "campaign_type",
    "promo_status", "valid_from", "valid_until", "period_precision",
    "period_flags", "matched_period_text", "published_at",
    "promo_price", "normal_price", "discount_percent", "discount_percent_basis",
    "discount_percent_confidence", "discount_percent_calculated", "discount_savings_rp",
    "estimated_bundle_discount_percent", "campaign_discount_percent",
    "campaign_discount_basis", "price_parse_method", "price_basis",
    "purchase_condition", "deal_type", "offer_text", "section",
    "source_url", "source_urls", "source_campaign_titles", "source_dataset",
    "source_name", "source_names", "source_type", "verification_status", "merchant_resolution_method",
    "source_page_url", "source_item_url", "region_or_channel", "scraped_at",
    "duplicate_count", "duplicate_group_id", "cross_dataset_duplicate", "overlapping_offer_count",
    "overlap_period_flag", "price_quality_flag", "data_quality_flags", "retained_from_previous_run", "raw_line",
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


def norm_key(value) -> str:
    value = scraper.normalize_text(value)
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


def canonical_merchant(value, title):
    value = (value or "").strip()
    inferred = scraper.infer_merchant(title or "")
    if not value or value.casefold() in {"lainnya", "unknown", "null", "none"}:
        return inferred or (value if value else None)
    canonical_map = {
        "superindo": "Super Indo",
        "super indo": "Super Indo",
        "mcdonalds": "McDonald's",
        "mcdonald's": "McDonald's",
        "jco": "J.CO",
        "j.co": "J.CO",
        "lotte mart": "Lotte Mart",
    }
    value = canonical_map.get(value.casefold(), value)
    # If the title explicitly identifies another more specific merchant, use it
    # for rows whose previous source had only a generic bucket.
    return value


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
    return scraper.campaign_type(title or "")


def derive_deal_type(offer_text, existing=None):
    if existing:
        return existing
    text = scraper.normalize_text(offer_text)
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

    title_meta = scraper.parse_period_details(title or "")
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
    status = scraper.promo_status(start, end, precision)
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
    value, basis = scraper.parse_explicit_discount(title or "")
    if value is not None:
        return value, f"title_{basis}"
    # Keep soft headline claims visible, but don't apply them to every SKU as
    # a product discount.
    m = re.search(r"diskon\s+hampir\s+(\d{1,2}(?:[.,]\d+)?)\s*%", scraper.normalize_text(title or ""))
    if m:
        return float(m.group(1).replace(",", ".")), "title_approximate_claim"
    return None, None


UNIT_CONTEXT_PATTERN = re.compile(
    r"(?:/\s*(?:\d+(?:[.,]\d+)?\s*)?(?:gr|gram|g|kg|ml|liter|litre|ltr|l)\b"
    r"|\bper\s+(?:\d+(?:[.,]\d+)?\s*)?(?:gr|gram|g|kg|ml|liter|litre|ltr|l)\b)",
    re.IGNORECASE,
)


def safe_discount_fields(offer_text, promo_price, normal_price, price_basis,
                         purchase_condition=None, deal_type=None, quality_flags=None):
    """Hitung diskon secara konservatif bila teks menunjukkan harga per satuan/berat."""
    flags = quality_flags if quality_flags is not None else []
    result = scraper.discount_fields(
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
    return result


def load_source(path, source_label):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    output = []
    for original in rows:
        raw = dict(original)
        title = (raw.get("campaign_title") or "").strip()
        merchant = canonical_merchant(raw.get("merchant_name") or raw.get("merchant"), title)
        original_product = (raw.get("original_product_text") or raw.get("product_text") or "").strip()
        product = clean_product_name(raw.get("product_text") or original_product)
        offer = (raw.get("offer_text") or raw.get("detail") or "").strip()
        raw_line = (raw.get("raw_line") or "").strip()
        if not offer:
            offer = raw_line
        promo = as_int(raw.get("promo_price"))
        normal = as_int(raw.get("normal_price"))
        basis = (raw.get("price_basis") or "").strip()
        purchase_condition = (raw.get("purchase_condition") or "").strip() or None
        deal_type = derive_deal_type(offer, raw.get("deal_type"))

        start, end, precision, period_flags, matched = period_meta(raw, title)
        status = current_status(start, end, precision, period_flags, raw.get("promo_status"))

        # Calculate product-level discount from the SKU's offer text only.
        quality_flags = pipe_unique([raw.get("price_quality_flag") or ""])
        if raw.get("promo_price") not in (None, "") and promo is None:
            quality_flags = pipe_unique([*quality_flags, "promo_price_parse_failed"])
        if raw.get("normal_price") not in (None, "") and normal is None:
            quality_flags = pipe_unique([*quality_flags, "normal_price_parse_failed"])
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
        )
        campaign_disc, campaign_disc_basis = campaign_discount(title)

        price_method = (raw.get("price_parse_method") or "").strip()
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
            "merchant_name": merchant,
            "campaign_title": title,
            "campaign_type": canonical_campaign_type(raw.get("campaign_type"), title),
            "promo_status": status,
            "valid_from": start,
            "valid_until": end,
            "period_precision": precision,
            "period_flags": period_flags,
            "matched_period_text": matched,
            "published_at": (raw.get("published_at") or "").strip() or None,
            "promo_price": promo,
            "normal_price": normal,
            **discount,
            "campaign_discount_percent": campaign_disc,
            "campaign_discount_basis": campaign_disc_basis,
            "price_parse_method": price_method,
            "price_basis": basis or None,
            "purchase_condition": purchase_condition,
            "deal_type": deal_type,
            "offer_text": offer or None,
            "section": (raw.get("section") or "").strip() or None,
            "source_url": (raw.get("source_url") or "").strip() or None,
            "source_urls": (raw.get("source_urls") or "").strip() or None,
            "source_campaign_titles": (raw.get("source_campaign_titles") or "").strip() or None,
            "source_dataset": source_label,
            "source_name": (raw.get("source_name") or "").strip() or None,
            "source_names": (raw.get("source_names") or "").strip() or None,
            "source_type": (raw.get("source_type") or "").strip() or None,
            "verification_status": (raw.get("verification_status") or "").strip() or None,
            "merchant_resolution_method": (raw.get("merchant_resolution_method") or "").strip() or None,
            "source_page_url": (raw.get("source_page_url") or "").strip() or None,
            "source_item_url": (raw.get("source_item_url") or "").strip() or None,
            "region_or_channel": (raw.get("region_or_channel") or "").strip() or None,
            "scraped_at": (raw.get("scraped_at") or "").strip() or None,
            "retained_from_previous_run": truthy(raw.get("retained_from_previous_run")),
            "duplicate_count": as_int(raw.get("duplicate_count")) or 1,
            "cross_dataset_duplicate": False,
            "overlapping_offer_count": 1,
            "overlap_period_flag": None,
            "price_quality_flag": " | ".join(pipe_unique(quality_flags)) or None,
            "data_quality_flags": " | ".join(pipe_unique([
                period_flags or "",
                "missing_merchant_name" if not merchant else "",
                "missing_product_text" if not product else "",
                "missing_source_url" if not raw.get("source_url") else "",
                "retained_from_previous_run" if truthy(raw.get("retained_from_previous_run")) else "",
                "discount_is_estimated" if discount["discount_percent_basis"] == "bundle_estimate_same_item_value" else "",
            ])) or None,
            "raw_line": raw_line or None,
        }
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
            )) + (2 if r.get("source_dataset") == OLD_CSV.name else 0)
        best = dict(max(group, key=score))
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


def _row_source_values(row, field_single, field_multi):
    return pipe_unique([row.get(field_single) or "", row.get(field_multi) or ""])


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
    text = scraper.normalize_text(" ".join(str(row.get(k) or "") for k in (
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


def _compatible_offers(a, b):
    fa, fb = _offer_family(a), _offer_family(b)
    if fa[0] == fb[0] == "bundle" and fa[1] != fb[1]:
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


# Main catalog uses a compact, user-friendly column order; technical fields remain in OUT_FULL.
CLEAN_COLUMNS = [
    ("Nama Produk", "product_text"),
    ("Harga Asli (Rp)", "normal_price"),
    ("Harga Promo (Rp)", "promo_price"),
    ("Diskon (%)", "discount_percent"),
    ("Merchant", "merchant_name"),
    ("Hemat (Rp)", "discount_savings_rp"),
    ("Status Promo", "display_status"),
    ("Mulai Berlaku", "valid_from"),
    ("Berakhir", "valid_until"),
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
        notes.append("Diskon dihitung dari harga asli dan harga promo")
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
    if row.get("price_quality_flag"):
        notes.append("Harga/nominal perlu diperiksa")
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
    item["display_deal_type"] = row.get("deal_type") or row.get("campaign_type") or ""
    item["display_notes"] = display_notes(row)
    out = {label: item.get(key) for label, key in CLEAN_COLUMNS}
    if review:
        reasons = []
        if row.get("promo_status") == "active_estimated": reasons.append("Periode promo estimasi")
        if has_material_period_conflict(row): reasons.append("Konflik tanggal judul/sumber")
        elif "period_taken_from_lead" in str(row.get("period_flags") or ""): reasons.append("Tanggal diambil dari paragraf pembuka")
        if row.get("price_quality_flag"): reasons.append("Harga perlu diperiksa")
        if row.get("discount_percent_confidence") == "low": reasons.append("Persentase diskon estimasi")
        if row.get("overlap_period_flag"): reasons.append("Penawaran serupa muncul pada beberapa artikel")
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
    "Kemiripan Nama (%)", "Produk A", "Produk B", "Merchant A", "Merchant B",
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
                candidates.append({
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
                    "Tindakan": "Jangan gabungkan otomatis; periksa sumber dan syarat promo",
                })
    candidates.sort(key=lambda row: (-float(row["Kemiripan Nama (%)"]), str(row.get("Merchant A") or "").casefold(), str(row.get("Produk A") or "").casefold()))
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


def write_excel_catalog(path, current_rows, confirmed_rows, review_rows, name_variant_rows, possible_duplicate_rows):
    """User-facing workbook: summary, clean catalog, confirmed rows, review and name variants."""
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
        ["Promo aktif terkonfirmasi", len(confirmed_rows)],
        ["Baris perlu review", len(review_rows)],
        ["Kelompok variasi nama produk", len(name_variant_rows)],
        ["Kandidat duplikat untuk diperiksa", len(possible_duplicate_rows)],
        ["Sumber produk", "Hemat.id, Katalogpromosi.com, Scanharga.com, dan katalog lama"],
        ["Catatan", "Promo agregator belum tentu diverifikasi retailer resmi."],
        ["Petunjuk", "Buka sheet Aktif untuk katalog sehari-hari. Sheet Kemungkinan Duplikat membantu memeriksa produk mirip yang belum aman digabung."],
        ["File teknis", "Data mentah dan log tersimpan di folder _internal; tidak perlu dibuka untuk pemakaian harian."],
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

    sheets = [
        ("Aktif", current_rows, False),
        ("Terkonfirmasi", confirmed_rows, False),
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
        widths = [38, 15, 15, 12, 18, 15, 22, 17, 17, 22, 52, 48, 56, 52] + ([50] if is_review else [])
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for idx, width in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(idx)].width = width
            field = headers[idx - 1]
            for col_cells in ws.iter_rows(min_row=2, min_col=idx, max_col=idx):
                cell = col_cells[0]
                cell.alignment = Alignment(vertical="top", wrap_text=field in {"Nama Produk", "Detail Penawaran", "Kampanye", "Catatan", "Sumber URL", "Alasan Review"})
                if field in {"Harga Asli (Rp)", "Harga Promo (Rp)", "Hemat (Rp)"}:
                    cell.number_format = '#,##0'
                elif field == "Diskon (%)":
                    cell.number_format = '0.0"%"'
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = ws.dimensions
        ws.sheet_view.showGridLines = False

    ws = wb.create_sheet("Kemungkinan Duplikat")
    ws.append(POSSIBLE_DUPLICATE_FIELDS)
    for row in possible_duplicate_rows:
        ws.append([row.get(h) for h in POSSIBLE_DUPLICATE_FIELDS])
    candidate_widths = [16, 38, 38, 18, 18, 16, 16, 16, 16, 24, 24, 44, 44, 60, 60, 56, 48]
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="9C6500")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for idx, width in enumerate(candidate_widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
        for col_cells in ws.iter_rows(min_row=2, min_col=idx, max_col=idx):
            cell = col_cells[0]
            cell.alignment = Alignment(vertical="top", wrap_text=idx not in {1, 6, 7, 8, 9})
            if idx in {1}:
                cell.number_format = '0.0"%"'
            elif idx in {6, 7, 8, 9}:
                cell.number_format = '#,##0'
    ws.freeze_panes = "B2"
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

    wb.save(path)
    return True


def main():
    INTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not OLD_CSV.exists() or not NEW_CSV.exists():
        raise FileNotFoundError(f"Dua CSV lama wajib ada: {OLD_CSV.name} dan {NEW_CSV.name}")

    old_rows = load_source(OLD_CSV, OLD_CSV.name)
    new_rows = load_source(NEW_CSV, NEW_CSV.name)
    multisource_rows = load_source(MULTISOURCE_CSV, MULTISOURCE_CSV.name) if MULTISOURCE_CSV.exists() else []
    input_rows = old_rows + new_rows + multisource_rows
    merged_raw = merge_rows(input_rows)
    merged_raw = mark_overlapping_offers(merged_raw)
    consolidated, consolidated_groups, consolidated_rows = consolidate_overlapping_offers(merged_raw)
    consolidated, equivalent_offer_groups, equivalent_offer_rows_absorbed = merge_equivalent_cross_source_offers(consolidated)

    # Main catalog excludes unresolved material date conflicts; those remain in Review.
    current = [
        r for r in consolidated
        if r.get("promo_status") in {"active", "active_estimated"}
        and not has_material_period_conflict(r)
    ]
    confirmed = [
        r for r in current
        if r.get("promo_status") == "active"
        and r.get("period_precision") in {"range", "single_day"}
    ]
    review = [r for r in consolidated if (
        r.get("promo_status") in {"active_estimated", "upcoming_estimated", "unknown"}
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
    write_possible_duplicate_csv(OUT_POSSIBLE_DUPES, possible_duplicate_rows)
    write_excel_catalog(OUT_XLSX, current, confirmed, review, name_variant_rows, possible_duplicate_rows)

    discount_basis = Counter(r.get("discount_percent_basis") or "not_available" for r in current)
    merchant_counts = Counter(r.get("merchant_name") or "UNKNOWN" for r in current)
    report = {
        "built_at": scraper.timestamp(),
        "reference_date": TODAY.isoformat(),
        "input_files": {OLD_CSV.name: len(old_rows), NEW_CSV.name: len(new_rows), **({MULTISOURCE_CSV.name: len(multisource_rows)} if MULTISOURCE_CSV.exists() else {})},
        "input_row_count": len(input_rows),
        "exact_deduplicated_row_count": len(merged_raw),
        "exact_duplicate_rows_collapsed": len(input_rows) - len(merged_raw),
        "overlapping_offer_groups_consolidated": consolidated_groups,
        "overlapping_offer_rows_absorbed": consolidated_rows,
        "equivalent_cross_source_offer_groups_merged": equivalent_offer_groups,
        "equivalent_cross_source_offer_rows_absorbed": equivalent_offer_rows_absorbed,
        "consolidated_row_count": len(consolidated),
        "current_active_including_estimates": len(current),
        "active_period_conflicts_routed_to_review": sum(
            r.get("promo_status") in {"active", "active_estimated"} and has_material_period_conflict(r)
            for r in consolidated
        ),
        "confirmed_active_rows": len(confirmed),
        "review_rows": len(review),
        "rows_with_product_discount": sum(r.get("discount_percent") is not None for r in current),
        "rows_with_price_calculated_discount": sum(r.get("discount_percent_calculated") is not None for r in current),
        "rows_with_bundle_discount_estimate": sum(r.get("estimated_bundle_discount_percent") is not None for r in current),
        "rows_with_campaign_level_discount_claim": sum(r.get("campaign_discount_percent") is not None for r in current),
        "rows_with_period_conflict": sum("title_lead_period_differs" in (r.get("period_flags") or "") for r in merged_raw),
        "rows_with_overlapping_offer_periods_before_consolidation": sum(bool(r.get("overlap_period_flag")) for r in merged_raw),
        "cross_dataset_duplicate_groups_or_rows": sum(bool(r.get("cross_dataset_duplicate")) for r in merged_raw),
        "product_name_variant_groups": len(name_variant_rows),
        "possible_duplicate_candidate_pairs": len(possible_duplicate_rows),
        "possible_duplicate_high_similarity_pairs": sum(float(r.get("Kemiripan Nama (%)") or 0) >= 93 for r in possible_duplicate_rows),
        "discount_basis_counts_current": dict(discount_basis),
        "merchant_counts_current": dict(merchant_counts),
        "user_facing_output_files": [OUT_ACTIVE.relative_to(ROOT_DIR).as_posix(), OUT_XLSX.relative_to(ROOT_DIR).as_posix()],
        "internal_audit_files": [OUT_CONFIRMED.relative_to(ROOT_DIR).as_posix(), OUT_REVIEW.relative_to(ROOT_DIR).as_posix(), OUT_FULL.relative_to(ROOT_DIR).as_posix(), OUT_REPORT.relative_to(ROOT_DIR).as_posix()],
        "clean_catalog_columns": [label for label, _ in CLEAN_COLUMNS],
        "full_audit_file": OUT_FULL.name,
        "discount_policy": {
            "explicit_product_offer_percent": "used as the displayed discount; explicit 'hingga' is marked as a maximum claim",
            "price_based_percent": "calculated only when normal and promo prices are positive, unit-comparable, and price_basis is offer_price; Indonesian thousands separators are parsed safely",
            "period_confirmation": "confirmed catalog only includes exact range/single_day periods without unresolved material title/source conflicts; incomplete provenance is estimated or review",
            "buy_x_free_y": "separate low-confidence estimate assuming the free item has the same value; not a confirmed discount",
            "campaign_title_percent": "stored separately and never copied to every product row",
        },
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("DONE")
    print("input rows:", len(input_rows))
    print("multisource input rows:", len(multisource_rows))
    print("exact deduplicated rows:", len(merged_raw))
    print("consolidated rows:", len(consolidated))
    print("overlapping offer rows absorbed:", consolidated_rows)
    print("equivalent cross-source offer groups merged:", equivalent_offer_groups)
    print("equivalent cross-source offer rows absorbed:", equivalent_offer_rows_absorbed)
    print("current active (including estimated):", len(current))
    print("confirmed active:", len(confirmed))
    print("review rows:", len(review))
    print("rows with product discount:", report["rows_with_product_discount"])
    print("clean catalog columns:", ", ".join(label for label, _ in CLEAN_COLUMNS))
    print("KATALOG UTAMA:", OUT_XLSX.relative_to(ROOT_DIR))
    print("CSV UTAMA:", OUT_ACTIVE.relative_to(ROOT_DIR))
    print("Data teknis/audit disimpan di folder:", INTERNAL_DIR.name)
    print("Kelompok variasi nama produk:", len(name_variant_rows))
    print("Kandidat duplikat untuk diperiksa:", len(possible_duplicate_rows))
    print("CSV kandidat duplikat:", OUT_POSSIBLE_DUPES.relative_to(ROOT_DIR))


if __name__ == "__main__":
    main()
