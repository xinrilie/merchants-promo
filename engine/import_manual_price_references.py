from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from openpyxl import load_workbook
import promo_ai_price_finder as price_utils
from promo_names import product_name_size_signature, product_name_variant_signature

ROOT = Path(__file__).resolve().parent.parent
XLSX = ROOT / "hasil" / "Daftar_Cari_Harga_Manual.xlsx"
JSON_IN = ROOT / "hasil" / "Referensi_Harga_AI.json"
DB = ROOT / "data" / "product_market_price_reference.csv"
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
    parser = argparse.ArgumentParser(description="Impor referensi harga dari JSON hasil AI atau Excel manual.")
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
            f"Buat {JSON_IN.name} dari hasil AI, atau jalankan export_manual_price_search.py untuk membuat Excel."
        )
    if input_path.suffix.lower() == ".json":
        entries, errors, total = _read_json(input_path)
    elif input_path.suffix.lower() in {".xlsx", ".xlsm"}:
        entries, errors, total = _read_xlsx(input_path)
    else:
        raise SystemExit("Format input belum didukung. Gunakan .json atau .xlsx.")

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
    added = 0
    duplicates = 0
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

    print(f"Input: {input_path}")
    print(f"Baris referensi dibaca: {total}")
    print(f"Referensi baru diimpor: {added}")
    print(f"Duplikat dilewati: {duplicates}")
    print(f"Baris ditolak karena tidak lengkap/tidak valid: {len(errors)}")
    for err in errors[:30]:
        print(" - " + err)
    if len(errors) > 30:
        print(f" - ... {len(errors) - 30} kesalahan lainnya")
    print(f"Database: {DB}")
    print("Langkah berikutnya: python engine\\enrich_existing_promo_catalog_multisource.py")
    if input_path.suffix.lower() == ".json":
        print("Catatan: sistem memeriksa format, URL, harga, dan bukti yang disediakan; kebenaran isi halaman tetap perlu diperiksa bila hasil AI meragukan.")
    if not added and not errors:
        print("Tidak ada referensi baru untuk diimpor.")


if __name__ == "__main__":
    main()
