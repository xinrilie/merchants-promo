from __future__ import annotations
import csv, sys
from datetime import date
from pathlib import Path
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "_internal" / "promo_multisource_products_all.csv"
OUT = ROOT / "hasil" / "Daftar_Cari_Harga_Manual.xlsx"
HEADERS = [
    "ID Produk", "Nama Produk", "Merchant Promo", "Harga Promo (Rp)",
    "Kata Kunci Pencarian (copy)", "Harga Referensi (Rp) - isi manual",
    "Nama Sumber", "URL Halaman Harga", "Bukti Harga pada Halaman",
    "Tanggal Cek (YYYY-MM-DD)", "Jenis Referensi (pasar/normal)", "Status"
]

def read_products():
    if not SOURCE.exists():
        raise SystemExit(f"Data produk belum ada: {SOURCE}\nJalankan scraping terlebih dahulu.")
    with SOURCE.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out, seen = [], set()
    for r in rows:
        name = (r.get("product_text") or "").strip()
        promo = (r.get("promo_price") or "").strip()
        merchant = (r.get("merchant_name") or "").strip()
        if not name or not promo:
            continue
        key = (name.casefold(), merchant.casefold(), promo)
        if key in seen: continue
        seen.add(key)
        normal = (r.get("normal_price") or "").strip()
        # Queue products where normal price is missing, or market reference is not already recorded.
        if normal:
            continue
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
    return out

def main():
    products = read_products()
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
    print(f"Daftar pencarian dibuat: {OUT}")
    print(f"Produk tanpa harga normal: {len(merged)}")
    print("Isi referensi manual, simpan, tutup Excel, lalu jalankan import_manual_price_references.py")

if __name__ == "__main__": main()
