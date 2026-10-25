from __future__ import annotations
import csv, json, sys
from datetime import date, datetime
from pathlib import Path
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from promo_price_reference import identity_key, load_reference_database
from promo_names import product_name_size_signature, product_name_variant_signature

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "_internal" / "promo_multisource_products_all.csv"
OUT = ROOT / "hasil" / "Daftar_Cari_Harga_Manual.xlsx"
AI_QUEUE = ROOT / "hasil" / "Daftar_Produk_Untuk_AI.json"
AI_PROMPT = ROOT / "hasil" / "PROMPT_AI_CARI_HARGA.txt"
PRICE_DB = ROOT / "data" / "product_price_reference.csv"
HEADERS = [
    "ID Produk", "Nama Produk", "Merchant Promo", "Harga Promo (Rp)",
    "Kata Kunci Pencarian (copy)", "Harga Referensi (Rp) - isi manual",
    "Nama Sumber", "URL Halaman Harga", "Bukti Harga pada Halaman",
    "Tanggal Cek (YYYY-MM-DD)", "Jenis Referensi (pasar/normal)", "Status"
]

def _product_slot(name: str):
    """Stable exact identity used to avoid re-queueing products with saved AI estimates."""
    return (
        identity_key(name),
        "|".join(product_name_size_signature(name)),
        "|".join(sorted(product_name_variant_signature(name))),
    )


def _existing_estimate_slots():
    """Load valid saved AI/user estimates, including older estimate records."""
    if not PRICE_DB.exists():
        return set()
    try:
        refs = load_reference_database(PRICE_DB)
    except Exception:
        return set()
    return {
        _product_slot(str(row.get("product_text") or ""))
        for row in refs
        if row.get("record_origin") == "user_supplied_estimate" and row.get("product_text")
    }


def read_products():
    if not SOURCE.exists():
        raise SystemExit(f"Data produk belum ada: {SOURCE}\nJalankan scraping terlebih dahulu.")
    with SOURCE.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out, seen_source_rows, seen_products = [], set(), set()
    known_estimates = _existing_estimate_slots()
    stats = {"source_candidates": 0, "already_estimated": 0, "duplicate_products": 0}
    for r in rows:
        name = (r.get("product_text") or "").strip()
        promo = (r.get("promo_price") or "").strip()
        merchant = (r.get("merchant_name") or "").strip()
        if not name or not promo:
            continue
        key = (name.casefold(), merchant.casefold(), promo)
        if key in seen_source_rows:
            continue
        seen_source_rows.add(key)
        normal = (r.get("normal_price") or "").strip()
        # We only need an AI task if the promo row lacks its own normal price.
        if normal:
            continue
        stats["source_candidates"] += 1
        slot = _product_slot(name)
        # The previous exporter only checked normal_price in the scraped row. This
        # made the same product return to the queue forever after its estimate had
        # already been imported. Existing verified/curated entries are not altered;
        # this filter specifically avoids re-asking AI for a saved estimate.
        if slot in known_estimates:
            stats["already_estimated"] += 1
            continue
        # Product estimates are general product-level values; don't queue identical
        # product identities repeatedly just because two merchants have the offer.
        if slot in seen_products:
            stats["duplicate_products"] += 1
            continue
        seen_products.add(slot)
        q = f'"{name}" harga'
        out.append({
            "ID Produk": f"{len(out)+1:05d}",
            "Nama Produk": name,
            "Merchant Promo": merchant,
            "Harga Promo (Rp)": promo,
            "Kata Kunci Pencarian (copy)": q,
            "Harga Referensi (Rp) - isi manual": "",
            "Nama Sumber": "",
            "URL Halaman Harga": "",
            "Bukti Harga pada Halaman": "",
            "Tanggal Cek (YYYY-MM-DD)": "",
            "Jenis Referensi (pasar/normal)": "pasar",
            "Status": "BELUM DICARI",
        })
    return out, stats

def main():
    products, queue_stats = read_products()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # Preserve any manually filled rows from an existing workbook; refresh/add only empty queue rows.
    existing = {}
    if OUT.exists():
        try:
            old = load_workbook(OUT, read_only=True, data_only=True)
            if "Cari Harga" in old.sheetnames:
                ws = old["Cari Harga"]
                hdr = [c.value for c in ws[1]]
                for vals in ws.iter_rows(min_row=2, values_only=True):
                    d = dict(zip(hdr, vals))
                    k = (str(d.get("Nama Produk") or "").casefold(), str(d.get("Merchant Promo") or "").casefold(), str(d.get("Harga Promo (Rp)") or ""))
                    if k[0]: existing[k] = d
            old.close()
        except Exception: pass
    merged, seen = [], set()
    for row in products:
        key=(row["Nama Produk"].casefold(), row["Merchant Promo"].casefold(), str(row["Harga Promo (Rp)"]))
        if key in seen: continue
        seen.add(key)
        old=existing.get(key)
        if old and (old.get("Harga Referensi (Rp) - isi manual") or old.get("URL Halaman Harga")):
            row.update({h: old.get(h, row.get(h, "")) for h in HEADERS})
            row["Status"] = "SIAP DIIMPOR" if row.get("Harga Referensi (Rp) - isi manual") and row.get("URL Halaman Harga") and row.get("Bukti Harga pada Halaman") else "PERIKSA ISIAN"
        merged.append(row)
    wb=Workbook()
    ws=wb.active; ws.title="Cari Harga"
    ws.append(HEADERS)
    for row in merged: ws.append([row.get(h, "") for h in HEADERS])
    ws.freeze_panes="A2"; ws.auto_filter.ref=ws.dimensions
    ws.row_dimensions[1].height=32
    for c in ws[1]:
        c.font=Font(bold=True,color="FFFFFF"); c.fill=PatternFill("solid",fgColor="244062")
        c.alignment=Alignment(wrap_text=True,vertical="center")
    widths=[12,38,23,18,48,24,24,48,42,20,25,18]
    for i,w in enumerate(widths,1): ws.column_dimensions[get_column_letter(i)].width=w
    for row in ws.iter_rows(min_row=2):
        for c in row: c.alignment=Alignment(vertical="top",wrap_text=(c.column in [2,5,7,8,9]))
    helpws=wb.create_sheet("Petunjuk")
    helpws.append(["CARA PAKAI DAFTAR CARI HARGA"])
    helpws.append(["1. Salin kolom Kata Kunci Pencarian ke Google/Google AI Mode."])
    helpws.append(["2. Cari produk yang sama persis, termasuk varian dan ukuran."])
    helpws.append(["3. Isi Harga Referensi, Nama Sumber, URL HALAMAN PRODUK/HARGA, bukti teks yang terlihat, dan tanggal cek."])
    helpws.append(["4. Satu produk boleh dibuat beberapa baris untuk beberapa toko/domain berbeda. Salin baris produk lalu ganti sumber dan harga."])
    helpws.append(["5. Jenis 'normal' hanya jika sumber menunjukkan harga normal; selain itu pilih 'pasar'."])
    helpws.append(["6. Simpan file, tutup Excel, lalu jalankan: python engine\\import_manual_price_references.py"])
    helpws.append(["7. Hanya baris lengkap yang diimpor. Referensi pasar dipakai untuk perbandingan, bukan diskon resmi."])
    helpws.append(["8. Jangan isi harga tebakan. Jika tidak ketemu, biarkan kosong."])
    helpws.column_dimensions["A"].width=130
    for row in helpws.iter_rows():
        for c in row: c.alignment=Alignment(wrap_text=True,vertical="top")
    wb.save(OUT)

    # JSON queue is easier to upload to an AI tool than manually copying hundreds of rows.
    ai_products = [{
        "product_id": row.get("ID Produk", ""),
        "product_name": row.get("Nama Produk", ""),
        "promo_merchant": row.get("Merchant Promo", ""),
        "promo_price": row.get("Harga Promo (Rp)", ""),
        "search_query": row.get("Kata Kunci Pencarian (copy)", ""),
    } for row in merged]
    queue = {
        "schema_version": 2,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "instructions": "Buat estimasi harga normal/pasar yang masuk akal untuk setiap produk. Saat mengembalikan hasil, cukup gunakan product_name dan estimated_price. Jangan mengubah nama produk.",
        "products": ai_products,
    }
    AI_QUEUE.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    AI_PROMPT.write_text("""Tugas: buat PREDIKSI harga normal/pasar di Indonesia untuk setiap produk dalam file Daftar_Produk_Untuk_AI.json.

Saya tidak membutuhkan sumber, URL, bukti, tanggal, alasan, atau penjelasan. Gunakan pengetahuan yang kamu punya untuk memperkirakan harga retail biasa bagi produk dengan merek, varian, ukuran, dan isi yang paling cocok. Jangan sekadar menyalin Harga Promo (Rp) dari antrean sebagai harga normal. Jika produk langka atau informasi terbatas, tetap berikan tebakan terbaik yang masuk akal; jangan menghilangkan produk hanya karena tidak menemukan sumber web.

Kerjakan SEMUA produk dalam daftar. Jangan mengubah product_name. estimated_price harus berupa angka Rupiah bulat tanpa simbol Rp atau pemisah ribuan.

Balas HANYA JSON valid, tanpa markdown/backtick dan tanpa teks lain, dengan format sesederhana ini:
{
  "estimates": [
    {"product_name": "nama produk persis dari antrean", "estimated_price": 61500},
    {"product_name": "nama produk persis berikutnya", "estimated_price": 38900}
  ]
}

Keluarkan tepat satu estimasi untuk setiap produk pada file antrean. Tidak perlu menyertakan field lain.
""", encoding="utf-8")
    print(f"Daftar pencarian dibuat: {OUT}")
    print(f"Antrean produk untuk AI: {AI_QUEUE}")
    print(f"Prompt AI siap salin: {AI_PROMPT}")
    print(f"Produk yang masih perlu estimasi AI/referensi: {len(merged)}")
    print(f"Produk tanpa harga normal yang dilewati karena estimasi sudah tersimpan: {queue_stats['already_estimated']}")
    print(f"Produk duplikat antar-merchant yang digabung dari antrean: {queue_stats['duplicate_products']}")
    if merged:
        print("Unggah Daftar_Produk_Untuk_AI.json ke AI, gunakan prompt di PROMPT_AI_CARI_HARGA.txt, simpan jawaban JSON sebagai hasil\\Referensi_Harga_AI.json, lalu jalankan importer.")
    else:
        print("Antrean kosong: semua produk yang memenuhi syarat sudah punya estimasi tersimpan atau harga normal.")

if __name__ == "__main__": main()
