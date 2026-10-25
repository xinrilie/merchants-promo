"""Seed user-provided approximate price ranges into the persistent reference database.

Ranges are not retailer-verified. Their midpoint is used only as an estimated
comparison point until a fresh, accepted observed price exists for the same SKU.
"""
from __future__ import annotations
import csv, hashlib
from pathlib import Path
from datetime import date
from promo_price_reference import REFERENCE_FIELDS, _normalize_record, identity_key, merchant_key, load_reference_database

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RANGES_CSV = DATA / "user_product_price_ranges.csv"
ALIASES_CSV = DATA / "user_product_price_reference_aliases.csv"
DATABASE_CSV = DATA / "product_price_reference.csv"

def load_dicts(path: Path):
    if not path.exists(): return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def make_record(product_text, merchant, mid, low, high, unit, source_text, note, observed_at):
    evidence = f"Rentang acuan pengguna Rp{int(low):,}–Rp{int(high):,}; titik tengah Rp{int(mid):,} digunakan hanya sebagai estimasi, bukan harga retailer terverifikasi."
    seed = "|".join([merchant_key(merchant), identity_key(product_text), str(mid), str(observed_at), "user_supplied_estimate"])
    return _normalize_record({
        "merchant_name": merchant,
        "product_text": product_text,
        "normal_price": str(int(mid)),
        "observed_at": observed_at,
        "source_name": source_text,
        "source_url": "",
        "evidence_text": evidence,
        "record_origin": "user_supplied_estimate",
        "verification_status": "user_supplied_estimate",
        "region_or_channel": "",
        "campaign_title": "Rentang referensi non-verifikasi; gunakan midpoint untuk hitung estimasi",
        "reference_key": hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24],
        "normal_price_low": str(int(low)),
        "normal_price_high": str(int(high)),
        "price_unit": unit,
        "reference_note": note,
    })

def seed_user_price_references(today: date | None = None, database_path: Path = DATABASE_CSV) -> dict:
    today = today or date.today()
    ranges = load_dicts(RANGES_CSV)
    aliases = load_dicts(ALIASES_CSV)
    existing = []
    if database_path.exists():
        with database_path.open("r", encoding="utf-8-sig", newline="") as f:
            existing = [_normalize_record(r) for r in csv.DictReader(f)]
    before = len(existing)
    # A user range is maintained separately from observed merchant-price records.
    # User estimates can be overwritten by editing the source range CSV; actual
    # observed scrape values remain in their own origin slot and win when recent.
    incoming = []
    for row in ranges:
        try:
            low, high = int(row["normal_price_low"]), int(row["normal_price_high"])
            mid = int(row.get("normal_price") or round((low + high) / 2))
        except (KeyError, TypeError, ValueError):
            continue
        base = row.get("product_text", "").strip()
        if not base or not low <= mid <= high: continue
        merchants = [row.get("merchant_name", "Referensi Umum").strip() or "Referensi Umum"]
        # Store-brand items also have a general fallback for other merchants.
        if "indomaret" in base.casefold() and "Indomaret" not in merchants:
            merchants.append("Indomaret")
        if "indomaret" in base.casefold() and "Referensi Umum" not in merchants:
            merchants.append("Referensi Umum")
        for merchant in dict.fromkeys(merchants):
            incoming.append(make_record(base, merchant, mid, low, high, row.get("price_unit", "pack"), row.get("reference_source", "Referensi pengguna"), row.get("note", ""), row.get("observed_at", "2026-10-25")))
    # Alias rows use the exact source/catalog spelling; only rows pre-audited as
    # high-confidence matches are included, so the production matcher stays exact.
    for alias in aliases:
        ref_name = alias.get("reference_product_text", "").strip()
        catalog_name = alias.get("catalog_product_text", "").strip()
        try:
            low, high = int(alias["normal_price_low"]), int(alias["normal_price_high"])
            mid = int(alias.get("normal_price") or round((low + high) / 2))
        except (KeyError, TypeError, ValueError):
            continue
        if not ref_name or not catalog_name or not low <= mid <= high: continue
        unit = alias.get("price_unit", "pack")
        note = "Alias katalog: " + ref_name + "; " + (alias.get("note", "") or "")
        source_text = alias.get("reference_source", "Rentang referensi pengguna (AI)")
        observed_at = alias.get("observed_at", "2026-10-25")
        incoming.append(make_record(catalog_name, "Referensi Umum", mid, low, high, unit, source_text, note, observed_at))
        if alias.get("merchant_name") == "Indomaret":
            incoming.append(make_record(catalog_name, "Indomaret", mid, low, high, unit, source_text, note, observed_at))
    # Rebuild user-supplied estimates from the canonical range and audited alias
    # files each run. This removes stale aliases when an alias is corrected or
    # deleted, while preserving all independently observed/source-verified rows.
    merged = {
        (r.get("merchant_key", merchant_key(r.get("merchant_name"))),
         r.get("product_key", identity_key(r.get("product_text"))),
         r.get("region_or_channel", ""), r.get("record_origin", "observed_scrape")): r
        for r in existing if r.get("record_origin") != "user_supplied_estimate"
    }
    added = 0
    updated = 0
    for row in incoming:
        key = (row["merchant_key"], row["product_key"], row["region_or_channel"], row["record_origin"])
        old = merged.get(key)
        if old is None: added += 1
        elif old.get("normal_price") != row.get("normal_price") or old.get("normal_price_low") != row.get("normal_price_low") or old.get("normal_price_high") != row.get("normal_price_high") or old.get("reference_note") != row.get("reference_note"):
            updated += 1
        merged[key] = row
    result = sorted(merged.values(), key=lambda r: (r.get("merchant_key", ""), r.get("product_key", ""), r.get("region_or_channel", ""), r.get("record_origin", "")))
    database_path.parent.mkdir(parents=True, exist_ok=True)
    temp = database_path.with_suffix(database_path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REFERENCE_FIELDS, extrasaction="ignore")
        writer.writeheader(); writer.writerows(result)
    temp.replace(database_path)
    validated = load_reference_database(database_path)
    return {"records_before": before, "canonical_ranges": len(ranges), "high_confidence_aliases": len(aliases), "estimate_rows_added": added, "estimate_rows_updated": updated, "records_after": len(result), "loadable_records": len(validated), "database": str(database_path)}

if __name__ == "__main__":
    print(seed_user_price_references())
