"""Evidence-validated market price references and comparison helpers.

This module contains only deterministic market-reference functionality. It does
not call any AI API and never creates estimated prices without a source.
"""
from __future__ import annotations

import csv
import hashlib
import re
import statistics
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from promo_names import clean_product_name, product_name_size_signature, product_name_variant_signature
from promo_price_reference import identity_key

ROOT_DIR = Path(__file__).resolve().parent.parent
MARKET_REFERENCE_CSV = ROOT_DIR / "data" / "product_market_price_reference.csv"
DEFAULT_MAX_AGE_DAYS = 14
MARKET_REFERENCE_FIELDS = [
    "product_text", "product_key", "size_signature", "variant_signature",
    "market_price", "observed_at", "source_name", "source_domain", "source_url",
    "evidence_text", "page_title", "price_kind", "source_kind", "verification_method",
    "confidence", "reference_key",
]


def parse_idr_price(value: object) -> int | None:
    """Parse Indonesian Rupiah strings such as ``Rp 22.900``."""
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
        separator = "." if "." in text else ","
        parts = text.split(separator)
        if len(parts) > 1 and len(parts[-1]) in (1, 2):
            text = "".join(parts[:-1]) + "." + parts[-1]
        else:
            text = "".join(parts)
    try:
        result = int(round(float(text)))
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result > 0 else None


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
    row = {field: str(record.get(field) or "").strip() for field in MARKET_REFERENCE_FIELDS}
    row["product_key"] = row["product_key"] or identity_key(row["product_text"])
    row["size_signature"] = row["size_signature"] or "|".join(product_name_size_signature(row["product_text"]))
    row["variant_signature"] = row["variant_signature"] or "|".join(sorted(product_name_variant_signature(row["product_text"])))
    row["source_domain"] = row["source_domain"] or (urlparse(row["source_url"]).hostname or "").lower()
    if not row["reference_key"]:
        material = "|".join((row["product_key"], row["source_url"], row["observed_at"], row["market_price"]))
        row["reference_key"] = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return row


def _ensure_csv(path: Path, fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            csv.DictWriter(handle, fieldnames=fields).writeheader()


def load_market_reference_database(path: str | Path = MARKET_REFERENCE_CSV) -> list[dict]:
    """Load only market prices with a URL, evidence, timestamp and verified-page marker."""
    path = Path(path)
    _ensure_csv(path, MARKET_REFERENCE_FIELDS)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            raw_rows = list(csv.DictReader(handle))
    except (OSError, csv.Error):
        return []
    rows = []
    for raw in raw_rows:
        row = _normalize_record(dict(raw))
        price = parse_idr_price(row.get("market_price"))
        observed = _parse_date(row.get("observed_at"))
        parsed = urlparse(row.get("source_url", ""))
        if (
            not row.get("product_key") or not row.get("product_text")
            or price is None or price < 100 or price > 100_000_000
            or not observed or not row.get("evidence_text")
            or parsed.scheme not in {"http", "https"} or not parsed.hostname
            or not row.get("source_domain")
            or row.get("verification_method") != "page_price_verified"
        ):
            continue
        row["market_price"] = str(price)
        row["source_domain"] = parsed.hostname.lower().removeprefix("www.")
        rows.append(row)
    return rows


def upsert_market_reference_database(path: str | Path, rows: list[dict]) -> dict:
    """Insert independently verified records, deduplicating by product page URL."""
    path = Path(path)
    existing = load_market_reference_database(path)
    # A collection/category page can list many different SKUs, so URL alone is
    # not a unique key. Keep a separate reference for each product on that page.
    by_url = {
        (row.get("product_key", ""), row.get("source_url", ""), row.get("price_kind", "market")): row
        for row in existing if row.get("source_url")
    }
    added = updated = 0
    for raw in rows:
        row = _normalize_record(raw)
        price = parse_idr_price(row.get("market_price"))
        parsed = urlparse(row.get("source_url", ""))
        if (
            not row.get("product_key") or price is None or not parsed.hostname
            or parsed.scheme not in {"http", "https"} or not row.get("evidence_text")
            or row.get("verification_method") != "page_price_verified"
        ):
            continue
        row["market_price"] = str(price)
        row["source_domain"] = parsed.hostname.lower().removeprefix("www.")
        key = (row.get("product_key", ""), row["source_url"], row.get("price_kind", "market"))
        old = by_url.get(key)
        if old is None:
            added += 1
        elif any(old.get(k) != row.get(k) for k in ("market_price", "observed_at", "evidence_text", "product_key")):
            updated += 1
        by_url[key] = row
    ordered = sorted(by_url.values(), key=lambda r: (r.get("product_key", ""), r.get("source_domain", ""), r.get("observed_at", "")))
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MARKET_REFERENCE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ordered)
    temp.replace(path)
    return {"records_before": len(existing), "records_added": added, "records_updated": updated, "records_after": len(ordered), "database_path": str(path)}


def compare_market_prices(rows: list[dict], references: list[dict], today: date | None = None, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> dict:
    """Attach market median only when same exact product/size/variant evidence matches."""
    today = today or date.today()
    by_product: dict[str, list[dict]] = {}
    for raw in references:
        row = _normalize_record(raw)
        observed = _parse_date(row.get("observed_at"))
        price = parse_idr_price(row.get("market_price"))
        if not observed or observed > today or (today - observed).days > max_age_days or price is None:
            continue
        by_product.setdefault(row.get("product_key", ""), []).append(row)

    summary = {"rows_checked": 0, "rows_with_market_reference": 0, "single_source_only": 0, "below_market_median": 0, "not_below_market_median": 0, "no_market_reference": 0, "stale_or_unmatched": 0, "max_age_days": max_age_days}
    for row in rows:
        for key in (
            "market_reference_price", "market_reference_low", "market_reference_high",
            "market_reference_source_count", "market_reference_sources", "market_reference_source_urls",
            "market_reference_observed_at", "market_reference_status", "market_reference_below_median_percent",
            "market_reference_savings", "market_reference_confidence", "market_reference_note",
        ):
            row.setdefault(key, None)
        promo = parse_idr_price(row.get("promo_price"))
        product = clean_product_name(row.get("product_text"))
        pkey = identity_key(product)
        if not pkey or promo is None or row.get("product_validation_status") != "eligible":
            row["market_reference_status"] = "not_comparable"
            continue
        summary["rows_checked"] += 1
        qsize = "|".join(product_name_size_signature(product))
        qvariant = "|".join(sorted(product_name_variant_signature(product)))
        candidates = [item for item in by_product.get(pkey, []) if item.get("size_signature", "") == qsize and item.get("variant_signature", "") == qvariant]
        latest_by_domain: dict[str, dict] = {}
        for item in sorted(candidates, key=lambda r: (r.get("observed_at", ""), r.get("source_url", ""))):
            domain = str(item.get("source_domain") or "").lower().removeprefix("www.")
            if domain:
                latest_by_domain[domain] = item
        refs = list(latest_by_domain.values())
        if not refs:
            row["market_reference_status"] = "no_market_reference"
            summary["no_market_reference"] += 1
            if any(item.get("product_key") == pkey for item in references):
                summary["stale_or_unmatched"] += 1
            continue
        prices = [parse_idr_price(item.get("market_price")) for item in refs]
        prices = [price for price in prices if price is not None]
        if not prices:
            row["market_reference_status"] = "invalid_market_reference"
            continue
        median_price = int(round(statistics.median(prices)))
        row["market_reference_price"] = median_price
        row["market_reference_low"] = min(prices)
        row["market_reference_high"] = max(prices)
        row["market_reference_source_count"] = len(refs)
        row["market_reference_sources"] = " | ".join(
            f"{item.get('source_domain')} (Rp{parse_idr_price(item.get('market_price')):,}; {item.get('price_kind') or 'unknown'})".replace(",", ".")
            for item in sorted(refs, key=lambda r: r.get("source_domain", ""))
        )
        row["market_reference_source_urls"] = " | ".join(item.get("source_url", "") for item in sorted(refs, key=lambda r: r.get("source_domain", "")))
        row["market_reference_observed_at"] = max(item.get("observed_at", "") for item in refs)
        row["market_reference_note"] = "Pembanding pasar dari halaman publik yang harganya cocok diverifikasi; bukan harga normal resmi merchant promo. Harga promo/regular pada sumber pembanding dapat berbeda."
        summary["rows_with_market_reference"] += 1
        if len(refs) < 2:
            row["market_reference_status"] = "single_source_only"
            row["market_reference_confidence"] = "insufficient"
            summary["single_source_only"] += 1
            continue
        row["market_reference_confidence"] = "medium" if len(refs) >= 3 else "low"
        if median_price > promo:
            row["market_reference_below_median_percent"] = round((median_price - promo) / median_price * 100, 1)
            row["market_reference_savings"] = median_price - promo
            row["market_reference_status"] = "below_market_median"
            summary["below_market_median"] += 1
        else:
            row["market_reference_status"] = "not_below_market_median"
            summary["not_below_market_median"] += 1
    return summary
