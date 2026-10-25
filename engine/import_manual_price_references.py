from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from promo_price_reference import REFERENCE_FIELDS, _normalize_record, identity_key, merchant_key

from openpyxl import load_workbook
import promo_ai_price_finder as price_utils
from promo_names import product_name_size_signature, product_name_variant_signature

ROOT = Path(__file__).resolve().parent.parent
XLSX = ROOT / "hasil" / "Daftar_Cari_Harga_Manual.xlsx"
JSON_IN = ROOT / "hasil" / "Referensi_Harga_AI.json"
DB = ROOT / "data" / "product_market_price_reference.csv"
PRICE_DB = ROOT / "data" / "product_price_reference.csv"
FIELDS = [
    "product_text", "product_key", "size_signature", "variant_signature", "market_price",
    "observed_at", "source_name", "source_domain", "source_url", "evidence_text",
    "page_title", "price_kind", "source_kind", "verification_method", "confidence", "reference_key",
]
SEARCH_HOSTS = {
    "google.com", "www.google.com", "bing.com", "www.bing.com",
    "duckduckgo.com", "www.duckduckgo.com", "search.yahoo.com",
}


def price_int(value):
    n = price_utils.parse_idr_price(value)
    return n if n is not None and 100 <= n <= 100_000_000 else None


def _first(row: dict, *keys, default=""):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def _normalize_input_row(row: dict, idx: int, input_type: str):
    """Map the Excel and AI JSON schemas into one validated reference row."""
    if input_type == "json":
        name = str(_first(row, "product_name", "nama_produk", "product_text")).strip()
        raw_price = _first(row, "reference_price", "price", "market_price", "harga_referensi")
        source = str(_first(row, "source_name", "source", "nama_sumber")).strip()
        url = str(_first(row, "source_url", "url", "url_halaman_harga")).strip()
        evidence = str(_first(row, "evidence_text", "price_evidence", "bukti_harga")).strip()
        observed = str(_first(row, "checked_date", "observed_at", "tanggal_cek", default=date.today().isoformat())).strip()[:10]
        kind = str(_first(row, "reference_type", "price_kind", "jenis_referensi", default="pasar")).strip().lower()
        title = str(_first(row, "page_title", "title", "judul_halaman")).strip()
    else:
        name = str(row.get("Nama Produk") or "").strip()
        raw_price = row.get("Harga Referensi (Rp) - isi manual")
        source = str(row.get("Nama Sumber") or "").strip()
        url = str(row.get("URL Halaman Harga") or "").strip()
        evidence = str(row.get("Bukti Harga pada Halaman") or "").strip()
        observed = str(row.get("Tanggal Cek (YYYY-MM-DD)") or "").strip()[:10]
        kind = str(row.get("Jenis Referensi (pasar/normal)") or "pasar").strip().lower()
        title = str(row.get("Judul Halaman") or "").strip()

    price = price_int(raw_price)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    full_host = (parsed.hostname or "").lower()
    issues = []
    if not name:
        issues.append("nama produk kosong")
    if price is None:
        issues.append("harga tidak valid (gunakan angka Rupiah, minimal Rp100)")
    if not source:
        issues.append("nama sumber kosong")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        issues.append("URL harus berupa URL halaman produk berawalan http(s)")
    elif full_host in SEARCH_HOSTS or any(part in url.lower() for part in ("/search?", "/search/", "google.com/search", "bing.com/search")):
        issues.append("URL hasil pencarian tidak boleh dipakai; gunakan halaman produk/harga langsung")
    if not evidence:
        issues.append("bukti harga pada halaman kosong")
    try:
        date.fromisoformat(observed)
    except ValueError:
        issues.append("tanggal harus YYYY-MM-DD")
    if issues:
        label = f"Baris {idx}" if input_type == "xlsx" else f"references[{idx}]"
        return None, f"{label}: " + "; ".join(issues)

    key = price_utils.identity_key(name)
    size = "|".join(product_name_size_signature(name))
    variant = "|".join(sorted(product_name_variant_signature(name)))
    canonical = f"{key}|{price}|{observed}|{url}"
    refkey = hashlib.sha256(canonical.encode()).hexdigest()[:24]
    normalized_kind = "normal" if kind in {"normal", "regular", "harga normal"} else "market"
    # JSON results are AI-assisted. The required direct URL and evidence make each
    # record auditable, but the importer cannot independently prove the page contents.
    ai_source = input_type == "json"
    return ({
        "product_text": name,
        "product_key": key,
        "size_signature": size,
        "variant_signature": variant,
        "market_price": str(price),
        "observed_at": observed,
        "source_name": source,
        "source_domain": host,
        "source_url": url,
        "evidence_text": evidence,
        "page_title": title,
        "price_kind": normalized_kind,
        "source_kind": "ai_assisted_user_supplied" if ai_source else "manual_user_checked",
        "verification_method": "page_price_verified",
        "confidence": "ai_assisted_needs_review" if ai_source else "manual_checked",
        "reference_key": refkey,
    }, None)


def _read_json(path: Path):
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"JSON tidak valid di {path.name}, baris {exc.lineno} kolom {exc.colno}: {exc.msg}")
    except OSError as exc:
        raise SystemExit(f"Gagal membaca {path}: {exc}")
    if isinstance(data, list):
        refs = data
    elif isinstance(data, dict):
        refs = data.get("references")
    else:
        refs = None
    if not isinstance(refs, list):
        raise SystemExit("Format JSON harus berupa array, atau objek dengan properti 'references' berisi array.")
    entries, errors = [], []
    for idx, item in enumerate(refs, 0):
        if not isinstance(item, dict):
            errors.append(f"references[{idx}]: item harus berupa objek JSON")
            continue
        row, err = _normalize_input_row(item, idx, "json")
        if err:
            errors.append(err)
        else:
            entries.append(row)
    return entries, errors, len(refs)



def _read_json_estimates(path: Path):
    """Read AI price predictions with a minimal schema; source metadata is optional and ignored."""
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"JSON tidak valid di {path.name}, baris {exc.lineno} kolom {exc.colno}: {exc.msg}")
    except OSError as exc:
        raise SystemExit(f"Gagal membaca {path}: {exc}")

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("estimates")
        if not isinstance(items, list):
            # Backward compatibility: user's earlier prompt produced "references"
            # with reference_price, source_url, etc. Those values now become estimates;
            # URLs and evidence are intentionally not required or treated as verified.
            items = data.get("references")
        if not isinstance(items, list):
            # Accept a product queue file too, but rows without a price are rejected.
            items = data.get("products")
    else:
        items = None
    if not isinstance(items, list):
        raise SystemExit("Format JSON harus berupa array atau objek dengan 'estimates' (atau 'references' dari format lama).")

    entries, errors = [], []
    today = date.today().isoformat()
    seen = set()
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"estimasi[{idx}]: item harus berupa objek JSON")
            continue
        name = str(_first(item, "product_name", "nama_produk", "product_text")).strip()
        raw_price = _first(item, "estimated_price", "predicted_price", "reference_price", "market_price", "price", "harga_estimasi", "harga_referensi")
        price = price_int(raw_price)
        if not name:
            errors.append(f"estimasi[{idx}]: nama produk kosong")
            continue
        if price is None:
            errors.append(f"estimasi[{idx}] ({name}): harga estimasi tidak valid/kosong")
            continue

        # The AI only needs to provide a single midpoint. The importer adds a broad
        # +/-20% uncertainty interval internally so the pipeline keeps this clearly
        # marked as an estimate rather than a verified normal/market price.
        low_raw = _first(item, "estimated_price_low", "price_low", "harga_minimum")
        high_raw = _first(item, "estimated_price_high", "price_high", "harga_maksimum")
        low, high = price_int(low_raw), price_int(high_raw)
        if low is None or high is None or low > price or high < price:
            low = max(100, int(round(price * 0.80 / 100) * 100))
            high = max(price, int(round(price * 1.20 / 100) * 100))

        observed = str(_first(item, "estimated_date", "checked_date", "observed_at", default=today)).strip()[:10]
        try:
            date.fromisoformat(observed)
        except ValueError:
            observed = today

        product_key = identity_key(name)
        size = "|".join(product_name_size_signature(name))
        variant = "|".join(sorted(product_name_variant_signature(name)))
        slot = (product_key, size, variant)
        if slot in seen:
            errors.append(f"estimasi[{idx}] ({name}): duplikat produk dalam satu JSON; entri pertama dipakai")
            continue
        seen.add(slot)
        evidence = (
            f"Prediksi AI; bukan harga terverifikasi dari toko. Titik estimasi Rp{price:,}; "
            f"rentang asumsi sistem Rp{low:,}–Rp{high:,} (±20%)."
        ).replace(",", ".")
        seed = "|".join(["referensi umum", product_key, size, variant, str(price), observed, "ai_estimate"])
        refkey = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
        record = _normalize_record({
            "merchant_name": "Referensi Umum",
            "product_text": name,
            "normal_price": str(price),
            "observed_at": observed,
            "source_name": "Estimasi AI (tanpa sumber)",
            "source_url": "",
            "evidence_text": evidence,
            "record_origin": "user_supplied_estimate",
            "verification_status": "user_supplied_estimate",
            "region_or_channel": "",
            "campaign_title": "Prediksi AI tanpa sumber; bukan harga terverifikasi",
            "reference_key": refkey,
            "normal_price_low": str(low),
            "normal_price_high": str(high),
            "price_unit": "pack",
            "reference_note": f"Estimasi AI tanpa sumber; bukan harga terverifikasi; rentang sistem ±20%: Rp{low:,}–Rp{high:,}".replace(",", "."),
        })
        entries.append(record)
    return entries, errors, len(items)


def _estimate_slot(row: dict):
    normalized = _normalize_record(row)
    return (
        normalized.get("merchant_key", merchant_key(normalized.get("merchant_name"))),
        normalized.get("product_key", identity_key(normalized.get("product_text"))),
        normalized.get("size_signature", ""),
        normalized.get("variant_signature", ""),
        normalized.get("region_or_channel", ""),
        normalized.get("record_origin", ""),
    )


def _import_ai_estimates(entries: list[dict], path: Path):
    """Upsert AI estimates while preserving existing curated/user-maintained ranges."""
    PRICE_DB.parent.mkdir(parents=True, exist_ok=True)
    if PRICE_DB.exists():
        with PRICE_DB.open("r", encoding="utf-8-sig", newline="") as f:
            existing = [_normalize_record(dict(r)) for r in csv.DictReader(f)]
    else:
        existing = []

    rows_by_slot = {}
    for idx, row in enumerate(existing):
        rows_by_slot.setdefault(_estimate_slot(row), []).append(idx)

    added = updated = protected = duplicates = 0
    protected_names = []
    rejected_keys = set()
    for incoming in entries:
        slot = _estimate_slot(incoming)
        if slot in rejected_keys:
            duplicates += 1
            continue
        rejected_keys.add(slot)
        matches = rows_by_slot.get(slot, [])
        if not matches:
            existing.append(incoming)
            rows_by_slot[slot] = [len(existing) - 1]
            added += 1
            continue

        # Never overwrite a curated/user-supplied range. Re-imports of this workflow's
        # own AI rows are updatable, so a newer estimate replaces the older AI estimate.
        ai_indices = [i for i in matches if existing[i].get("source_name") == "Estimasi AI (tanpa sumber)"]
        if not ai_indices:
            protected += 1
            protected_names.append(incoming.get("product_text", "Produk tanpa nama"))
            continue
        primary = ai_indices[0]
        existing[primary] = incoming
        for extra in reversed(ai_indices[1:]):
            existing.pop(extra)
        if len(ai_indices) > 1:
            # Rebuild slot indexes after removing legacy duplicate AI rows.
            rows_by_slot = {}
            for j, row in enumerate(existing):
                rows_by_slot.setdefault(_estimate_slot(row), []).append(j)
        updated += 1

    tmp = PRICE_DB.with_suffix(PRICE_DB.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REFERENCE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing)
    tmp.replace(PRICE_DB)
    return {"added": added, "updated": updated, "protected": protected, "protected_names": protected_names, "duplicates": duplicates}

def _read_xlsx(path: Path):
    if not path.exists():
        raise SystemExit(f"File tidak ditemukan: {path}. Jalankan engine\\export_manual_price_search.py dahulu.")
    wb = load_workbook(path, read_only=True, data_only=True)
    if "Cari Harga" not in wb.sheetnames:
        wb.close()
        raise SystemExit("Sheet 'Cari Harga' tidak ditemukan.")
    ws = wb["Cari Harga"]
    headers = [str(c or "").strip() for c in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
    entries, errors, total = [], [], 0
    for idx, vals in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        row = dict(zip(headers, vals))
        # Ignore completely empty rows in the manual workbook.
        if not any(str(row.get(k) or "").strip() for k in ["Harga Referensi (Rp) - isi manual", "Nama Sumber", "URL Halaman Harga", "Bukti Harga pada Halaman"]):
            continue
        total += 1
        normalized, err = _normalize_input_row(row, idx, "xlsx")
        if err:
            errors.append(err)
        else:
            entries.append(normalized)
    wb.close()
    return entries, errors, total


def main(argv=None):
    parser = argparse.ArgumentParser(description="Impor prediksi harga AI dari JSON sederhana atau referensi terverifikasi dari Excel.")
    parser.add_argument("input", nargs="?", help="Path file .json atau .xlsx (opsional).")
    args = parser.parse_args(argv)
    if args.input:
        input_path = Path(args.input)
        if not input_path.is_absolute():
            input_path = (Path.cwd() / input_path).resolve()
    elif JSON_IN.exists():
        input_path = JSON_IN
    else:
        input_path = XLSX
    if not input_path.exists():
        raise SystemExit(
            f"File input tidak ditemukan: {input_path}\n"
            f"Simpan jawaban prediksi AI sebagai {JSON_IN.name}, atau jalankan export_manual_price_search.py untuk membuat Excel."
        )

    print(f"Input: {input_path}")
    if input_path.suffix.lower() == ".json":
        entries, errors, total = _read_json_estimates(input_path)
        stats = _import_ai_estimates(entries, input_path)
        print(f"Baris prediksi dibaca: {total}")
        print(f"Estimasi baru diimpor: {stats['added']}")
        print(f"Estimasi AI diperbarui: {stats['updated']}")
        print(f"Estimasi lama yang dipertahankan: {stats['protected']}")
        for name in stats.get("protected_names", [])[:20]:
            print(f" - Sudah punya rentang tersimpan: {name}")
        if len(stats.get("protected_names", [])) > 20:
            print(f" - ... {len(stats['protected_names']) - 20} produk lain dipertahankan")
        print(f"Duplikat dilewati: {stats['duplicates']}")
        print(f"Baris ditolak: {len(errors)}")
        for err in errors[:30]:
            print(" - " + err)
        if len(errors) > 30:
            print(f" - ... {len(errors) - 30} kesalahan lainnya")
        print(f"Database estimasi: {PRICE_DB}")
        print("Catatan: harga ini estimasi AI, bukan harga yang diverifikasi dari toko. Rentang pembanding sistem dibuat otomatis ±20%.")
    elif input_path.suffix.lower() in {".xlsx", ".xlsm"}:
        entries, errors, total = _read_xlsx(input_path)
        DB.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if DB.exists():
            with DB.open("r", encoding="utf-8-sig", newline="") as f:
                existing = list(csv.DictReader(f))
        known = {
            r.get("reference_key") or hashlib.sha256("|".join([
                r.get("product_key", ""), r.get("source_url", ""), r.get("observed_at", ""), r.get("market_price", "")
            ]).encode()).hexdigest()[:24]
            for r in existing
        }
        added = duplicates = 0
        for row in entries:
            if row["reference_key"] not in known:
                existing.append(row)
                known.add(row["reference_key"])
                added += 1
            else:
                duplicates += 1
        with DB.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            for row in existing:
                writer.writerow({k: row.get(k, "") for k in FIELDS})
        print(f"Baris referensi dibaca: {total}")
        print(f"Referensi baru diimpor: {added}")
        print(f"Duplikat dilewati: {duplicates}")
        print(f"Baris ditolak karena tidak lengkap/tidak valid: {len(errors)}")
        for err in errors[:30]:
            print(" - " + err)
        if len(errors) > 30:
            print(f" - ... {len(errors) - 30} kesalahan lainnya")
        print(f"Database harga pasar teramati: {DB}")
    else:
        raise SystemExit("Format input belum didukung. Gunakan .json atau .xlsx.")

    print("Langkah berikutnya: python engine\\enrich_existing_promo_catalog_multisource.py")
    if input_path.suffix.lower() == ".xlsx":
        print("Catatan: Excel tetap mensyaratkan nama sumber, URL halaman produk langsung, dan bukti harga.")
    if input_path.suffix.lower() == ".json" and not entries and not errors:
        print("Tidak ada estimasi baru untuk diimpor.")


if __name__ == "__main__":
    main()
