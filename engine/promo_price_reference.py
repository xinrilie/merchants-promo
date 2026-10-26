"""Persistent, evidence-based current normal-price references for promo comparison.

Only explicit normal-price labels from fresh, eligible source rows are learned
automatically. A newer accepted price replaces the previous value for that exact
product/merchant/channel slot. Matching prefers the same merchant, and falls back
to another merchant only when a recent exact same-merchant reference is unavailable.
Aggregator observations are never presented as verified official retailer prices.
"""
from __future__ import annotations

import csv
import hashlib
import re
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from promo_names import clean_product_name, product_name_size_signature, product_name_variant_signature
from promo_evidence import find_amount_evidence
from promo_campaign_utils import normalize_text

PRICE_REFERENCE_MAX_AGE_DAYS = 30
PRICE_REFERENCE_CAPTURE_MAX_AGE_DAYS = 7
REFERENCE_FIELDS = [
    "merchant_name", "merchant_key", "product_text", "product_key",
    "size_signature", "variant_signature", "normal_price", "observed_at",
    "source_name", "source_url", "evidence_text", "record_origin",
    "verification_status", "region_or_channel", "campaign_title", "reference_key",
    "normal_price_low", "normal_price_high", "price_unit", "reference_note",
]
ALLOWED_ORIGINS = {"observed_scrape", "manual_verified", "reviewed_poster_ocr"}
EXPLICIT_NORMAL_PRICE_LABEL_RE = re.compile(
    r"\b(?:harga\s+(?:normal|asli|reguler|sebelum\s+promo)|normal\s+price|regular\s+price)\b",
    re.I,
)


def identity_key(value: object) -> str:
    """Exact normalized identity key; intentionally does not fuzzy-match SKUs."""
    cleaned = clean_product_name(value)
    return " ".join(re.findall(r"[a-z0-9]+", normalize_text(cleaned)))


def merchant_key(value: object) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", normalize_text(value)))


def _price_int(value: object) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    text = str(value).strip()
    text = re.sub(r"(?i)\b(?:rp|idr)\b", "", text)
    text = text.replace(" ", "")
    text = re.sub(r"[^0-9.,+\-]", "", text)
    if not text or not re.search(r"\d", text):
        return None
    if "." in text and "," in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "." in text or "," in text:
        sep = "." if "." in text else ","
        parts = text.split(sep)
        if len(parts) > 1 and len(parts[-1]) in (1, 2):
            text = "".join(parts[:-1]) + "." + parts[-1]
        else:
            text = "".join(parts)
    try:
        value_num = float(text)
        return int(round(value_num))
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except (TypeError, ValueError, OverflowError):
        try:
            return date.fromisoformat(text[:10])
        except (TypeError, ValueError):
            return None


def _normalize_record(record: dict) -> dict:
    item = {field: str(record.get(field) or "").strip() for field in REFERENCE_FIELDS}
    item["merchant_key"] = item["merchant_key"] or merchant_key(item["merchant_name"])
    item["product_key"] = item["product_key"] or identity_key(item["product_text"])
    if not item["size_signature"]:
        item["size_signature"] = "|".join(product_name_size_signature(item["product_text"]))
    if not item["variant_signature"]:
        item["variant_signature"] = "|".join(sorted(product_name_variant_signature(item["product_text"])))
    if not item["reference_key"]:
        seed = "|".join([
            item["merchant_key"], item["product_key"], item["normal_price"],
            item["observed_at"][:10], item["source_url"], item["record_origin"],
        ])
        item["reference_key"] = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return item


def _ensure_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            csv.DictWriter(handle, fieldnames=REFERENCE_FIELDS).writeheader()


def load_reference_database(path: str | Path) -> list[dict]:
    path = Path(path)
    _ensure_database(path)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            loaded = list(csv.DictReader(handle))
    except (OSError, csv.Error):
        return []
    result = []
    for raw in loaded:
        row = _normalize_record(dict(raw))
        price = _price_int(row.get("normal_price"))
        observed = _parse_date(row.get("observed_at"))
        origin = row.get("record_origin", "").casefold()
        if (
            not row.get("merchant_key") or not row.get("product_key") or price is None or price <= 0
            or not observed or origin not in ALLOWED_ORIGINS or not row.get("evidence_text")
        ):
            continue
        if origin == "user_supplied_estimate":
            low = _price_int(row.get("normal_price_low"))
            high = _price_int(row.get("normal_price_high"))
            # This is a user-provided approximate range, not a source-verified price.
            # No fabricated URL or official-retailer claim is required.
            if low is None or high is None or low <= 0 or high < low or not (low <= price <= high):
                continue
            row["normal_price_low"] = str(low)
            row["normal_price_high"] = str(high)
            result.append(row)
            continue
        if not row.get("source_url"):
            continue
        if origin == "manual_verified" and row.get("verification_status", "").casefold() != "retailer_official":
            continue
        if not EXPLICIT_NORMAL_PRICE_LABEL_RE.search(row["evidence_text"]):
            continue
        evidence = find_amount_evidence(row["evidence_text"], price, "normal")
        if not evidence:
            continue
        row["normal_price_low"] = str(price)
        row["normal_price_high"] = str(price)
        row["normal_price"] = str(price)
        row["evidence_text"] = evidence
        result.append(row)
    return result


def _fresh_timestamp(stamp: object, today: date, max_age_days: int) -> bool:
    observed = _parse_date(stamp)
    if not observed:
        return False
    age = (today - observed).days
    return 0 <= age <= max_age_days


def _timestamp_sort_key(value: object) -> tuple:
    """Comparable timestamp key; invalid timestamps sort before valid ones."""
    text = str(value or "").strip()
    if not text:
        return (0, "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return (1, parsed.isoformat(timespec="microseconds"))
    except (TypeError, ValueError, OverflowError):
        parsed_date = _parse_date(text)
        return (1, parsed_date.isoformat() if parsed_date else text)


def _reference_slot_key(row: dict) -> tuple[str, str, str, str]:
    """One current value per product/merchant/channel/origin, not a price history."""
    normalized = _normalize_record(row)
    return (
        normalized.get("merchant_key", ""),
        normalized.get("product_key", ""),
        merchant_key(normalized.get("region_or_channel", "")),
        normalized.get("record_origin", "observed_scrape"),
    )


def _latest_records_by_slot(records: Iterable[dict]) -> dict[tuple, list[dict]]:
    """Keep only the newest record per slot; identical-time price conflicts stay visible."""
    grouped: dict[tuple, list[dict]] = {}
    for raw in records:
        row = _normalize_record(raw)
        slot = _reference_slot_key(row)
        current = grouped.get(slot, [])
        row_time = _timestamp_sort_key(row.get("observed_at"))
        if not current:
            grouped[slot] = [row]
            continue
        current_time = max(_timestamp_sort_key(item.get("observed_at")) for item in current)
        if row_time > current_time:
            grouped[slot] = [row]
        elif row_time == current_time:
            same_price = any(_price_int(item.get("normal_price")) == _price_int(row.get("normal_price")) for item in current)
            if not same_price and all(
                _price_int(item.get("normal_price")) != _price_int(row.get("normal_price"))
                or item.get("source_url") != row.get("source_url")
                for item in current
            ):
                current.append(row)
            elif same_price and all(item.get("source_url") != row.get("source_url") for item in current):
                # Preserve independent equal-price evidence at the same latest timestamp.
                current.append(row)
    return grouped


def update_reference_database(
    path: str | Path,
    rows: Iterable[dict],
    today: date,
    capture_max_age_days: int = PRICE_REFERENCE_CAPTURE_MAX_AGE_DAYS,
) -> dict:
    """Upsert fresh, explicit normal-price evidence; newer prices replace older values.

    The database stores the latest usable value per product/merchant/channel. It is
    not a growing day-by-day price history. If two sources report different prices
    at the exact same latest timestamp, both are retained so matching can flag the
    ambiguity instead of arbitrarily choosing one.
    """
    path = Path(path)
    existing = load_reference_database(path)
    before = len(existing)
    slots = _latest_records_by_slot(existing)
    rejected = 0
    added_slots: set[tuple] = set()
    updated_slots: set[tuple] = set()
    incoming: list[dict] = []
    for source_row in rows:
        price = _price_int(source_row.get("normal_price"))
        merchant = str(source_row.get("merchant_name") or "").strip()
        product = str(source_row.get("product_text") or "").strip()
        evidence = str(source_row.get("normal_price_evidence_text") or "").strip()
        source_url = str(source_row.get("source_url") or source_row.get("source_page_url") or "").strip()
        source_name = str(source_row.get("source_name") or "").strip()
        if str(source_row.get("price_parse_method") or "") == "poster_ocr_reviewed_manual":
            observed_at = str(source_row.get("poster_ocr_reviewed_at") or source_row.get("scraped_at") or "").strip()
        else:
            observed_at = str(source_row.get("scraped_at") or "").strip()
        source_status = str(source_row.get("freshness_status") or "").casefold()
        origin_type = str(source_row.get("source_type") or "aggregator").casefold()
        flags = {piece.strip() for piece in str(source_row.get("price_quality_flag") or "").split("|") if piece.strip()}
        blocked_flags = {
            "normal_price_ambiguous_unit", "normal_price_parse_failed_or_unverified",
            "normal_price_without_matching_offer_evidence", "price_unit_context_requires_review",
            "cross_source_price_disagreement", "promo_price_above_normal_review",
        }
        promo_price = _price_int(source_row.get("promo_price"))
        if (
            source_row.get("product_validation_status") != "eligible"
            or not merchant or merchant.casefold() in {"unknown", "n/a", "none", "lainnya", "belum diketahui"}
            or not product or not identity_key(product) or price is None or price <= 0
            or str(source_row.get("price_basis") or "").casefold() != "offer_price"
            or not evidence or not EXPLICIT_NORMAL_PRICE_LABEL_RE.search(evidence)
            or not find_amount_evidence(evidence, price, "normal")
            or not source_url or not source_name
            or source_status != "fresh" or source_row.get("retained_from_previous_run") in (True, "true", "1", "yes", "ya")
            or not ("aggregator" in origin_type or origin_type == "secondary_public_articles")
            or source_name not in {"Hemat.id", "Katalogpromosi.com", "Scanharga.com"}
            or not _fresh_timestamp(observed_at, today, capture_max_age_days)
            or blocked_flags.intersection(flags)
            or (promo_price is not None and promo_price > price)
        ):
            if price is not None:
                rejected += 1
            continue
        incoming.append(_normalize_record({
            "merchant_name": merchant,
            "product_text": product,
            "normal_price": price,
            "observed_at": observed_at,
            "source_name": source_name,
            "source_url": source_url,
            "evidence_text": evidence,
            "record_origin": "reviewed_poster_ocr" if source_row.get("price_parse_method") == "poster_ocr_reviewed_manual" else "observed_scrape",
            "verification_status": "poster_ocr_reviewed" if source_row.get("price_parse_method") == "poster_ocr_reviewed_manual" else str(source_row.get("verification_status") or "aggregator_only"),
            "region_or_channel": source_row.get("region_or_channel") or "",
            "campaign_title": source_row.get("campaign_title") or "",
        }))

    # Process chronological order so the newest observation wins even if collectors
    # return pages out of order. Exact-time price conflicts are preserved.
    incoming.sort(key=lambda r: _timestamp_sort_key(r.get("observed_at")))
    for row in incoming:
        slot = _reference_slot_key(row)
        current = slots.get(slot, [])
        if not current:
            slots[slot] = [row]
            added_slots.add(slot)
            continue
        old_time = max(_timestamp_sort_key(item.get("observed_at")) for item in current)
        new_time = _timestamp_sort_key(row.get("observed_at"))
        if new_time > old_time:
            slots[slot] = [row]
            if slot not in added_slots:
                updated_slots.add(slot)
            continue
        if new_time == old_time:
            row_price = _price_int(row.get("normal_price"))
            old_prices = {_price_int(item.get("normal_price")) for item in current}
            if row_price not in old_prices:
                current.append(row)
            elif not any(item.get("source_url") == row.get("source_url") for item in current):
                # Same price at the same timestamp from another source: keep one
                # latest value but retain that independent evidence in the source
                # metadata of the chosen observation only when it is newer.
                pass

    ordered = []
    for slot_records in slots.values():
        ordered.extend(slot_records)
    # Keep output deterministic and ready for review.
    ordered.sort(key=lambda r: (
        r.get("merchant_key", ""), r.get("product_key", ""),
        r.get("region_or_channel", ""), r.get("record_origin", ""),
        r.get("observed_at", ""), r.get("source_name", ""),
    ))
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REFERENCE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ordered)
    temp_path.replace(path)
    updated_final = len(updated_slots)
    added_final = len(added_slots)
    return {
        "database_path": path.as_posix(),
        "records_before": before,
        "observations_added": added_final,
        "observations_updated": updated_final,
        "records_superseded": max(0, before + added_final - len(ordered)),
        "records_after": len(ordered),
        "rejected_current_rows": rejected,
        "capture_max_age_days": capture_max_age_days,
        "update_policy": "latest_price_replaces_previous_per_product_merchant_channel",
    }

def find_reference_price(
    records: list[dict],
    product_text: object,
    merchant_name: object,
    today: date,
    max_age_days: int = PRICE_REFERENCE_MAX_AGE_DAYS,
    region_or_channel: object = None,
) -> dict:
    """Prefer a recent exact same-merchant match; otherwise use another merchant.

    Cross-merchant matching is still exact on product, size, variant and channel.
    It is explicitly identified in the returned ``match_scope`` so downstream
    reports never imply that the reference price belongs to the current merchant.
    """
    pkey = identity_key(product_text)
    mkey = merchant_key(merchant_name)
    size = "|".join(product_name_size_signature(product_text))
    variant = "|".join(sorted(product_name_variant_signature(product_text)))
    if not pkey:
        return {"status": "identity_unavailable"}
    channel_key = merchant_key(region_or_channel)
    exact_product = []
    for record in records:
        row = _normalize_record(record)
        if row.get("product_key") != pkey:
            continue
        if row.get("size_signature", "") != size or row.get("variant_signature", "") != variant:
            continue
        if merchant_key(row.get("region_or_channel")) != channel_key:
            continue
        observed = _parse_date(row.get("observed_at"))
        if not observed or observed > today:
            continue
        exact_product.append(row)

    if not exact_product:
        return {"status": "no_exact_match", "max_age_days": max_age_days}

    same_merchant = [r for r in exact_product if merchant_key(r.get("merchant_name")) == mkey]
    cross_merchant = [r for r in exact_product if merchant_key(r.get("merchant_name")) != mkey]

    def choose(candidates: list[dict], scope: str) -> dict | None:
        if not candidates:
            return None
        # Only source-backed observations and human-reviewed poster OCR records
        # are loadable here. Guess-only legacy estimates are excluded by the loader.
        fresh_candidates = [
            row for row in candidates
            if (today - _parse_date(row.get("observed_at"))).days <= max_age_days
        ]
        if fresh_candidates:
            candidates = fresh_candidates
        latest_sort = max(_timestamp_sort_key(row.get("observed_at")) for row in candidates)
        latest = [row for row in candidates if _timestamp_sort_key(row.get("observed_at")) == latest_sort]
        latest_date = max(_parse_date(row.get("observed_at")) for row in latest if _parse_date(row.get("observed_at")))
        age = (today - latest_date).days
        if age > max_age_days:
            return {"status": "reference_too_old", "age_days": age, "max_age_days": max_age_days, "observed_at": latest_date.isoformat(), "match_scope": scope}
        # A verified retailer price wins an exact-time tie against aggregator data.
        manual = [row for row in latest if row.get("record_origin", "").casefold() == "manual_verified" and row.get("verification_status", "").casefold() == "retailer_official"]
        if manual:
            latest = manual
        prices = {_price_int(row.get("normal_price")) for row in latest}
        prices.discard(None)
        if len(prices) != 1:
            return {"status": "conflicting_prices_same_day", "age_days": age, "observed_at": latest_date.isoformat(), "match_scope": scope}
        chosen_price = next(iter(prices))
        chosen = sorted(latest, key=lambda row: (
            row.get("record_origin") != "manual_verified",
            row.get("source_name", "").casefold(),
            row.get("source_url", ""),
        ))[0]
        return {
            "status": "matched_recent_exact" if scope == "same_merchant" else "matched_recent_cross_merchant",
            "match_scope": scope,
            "normal_price": chosen_price,
            "merchant_name": chosen.get("merchant_name"),
            "observed_at": chosen.get("observed_at") or latest_date.isoformat(),
            "age_days": age,
            "source_name": chosen.get("source_name"),
            "source_url": chosen.get("source_url"),
            "evidence_text": chosen.get("evidence_text"),
            "verification_status": chosen.get("verification_status"),
            "record_origin": chosen.get("record_origin"),
            "normal_price_low": _price_int(chosen.get("normal_price_low")) or chosen_price,
            "normal_price_high": _price_int(chosen.get("normal_price_high")) or chosen_price,
            "price_unit": chosen.get("price_unit", ""),
            "reference_note": chosen.get("reference_note", ""),
            "max_age_days": max_age_days,
        }

    own_match = choose(same_merchant, "same_merchant")
    if own_match and own_match.get("status") == "matched_recent_exact":
        return own_match
    if own_match and own_match.get("status") == "conflicting_prices_same_day":
        # Do not hide a conflict in the primary merchant's data behind another store's price.
        return own_match

    other_match = choose(cross_merchant, "cross_merchant")
    if other_match and other_match.get("status") == "matched_recent_cross_merchant":
        other_match["fallback_reason"] = "no_recent_exact_reference_for_current_merchant"
        return other_match
    if own_match and own_match.get("status") == "reference_too_old":
        if other_match and other_match.get("status") == "conflicting_prices_same_day":
            return other_match
        if other_match and other_match.get("status") == "reference_too_old":
            # Prefer the freshest known date for diagnostic clarity.
            if other_match.get("age_days", 10**9) < own_match.get("age_days", 10**9):
                return other_match
        return own_match
    if other_match:
        return other_match
    return {"status": "no_exact_match", "max_age_days": max_age_days}

