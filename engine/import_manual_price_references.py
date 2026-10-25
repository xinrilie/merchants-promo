from __future__ import annotations
import csv, hashlib, re, sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse
from openpyxl import load_workbook
import promo_ai_price_finder as price_utils
from promo_names import product_name_size_signature, product_name_variant_signature

ROOT=Path(__file__).resolve().parent.parent
XLSX=ROOT/"hasil"/"Daftar_Cari_Harga_Manual.xlsx"
DB=ROOT/"data"/"product_market_price_reference.csv"
FIELDS=["product_text","product_key","size_signature","variant_signature","market_price","observed_at","source_name","source_domain","source_url","evidence_text","page_title","price_kind","source_kind","verification_method","confidence","reference_key"]

def price_int(value):
    n=price_utils.parse_idr_price(value)
    return n if n is not None and 100 <= n <= 100_000_000 else None

def main():
    if not XLSX.exists():
        raise SystemExit(f"File tidak ditemukan: {XLSX}. Jalankan export_manual_price_search.py dahulu.")
    wb=load_workbook(XLSX,read_only=True,data_only=True)
    if "Cari Harga" not in wb.sheetnames: raise SystemExit("Sheet 'Cari Harga' tidak ditemukan.")
    ws=wb["Cari Harga"]
    headers=[str(c or "").strip() for c in next(ws.iter_rows(min_row=1,max_row=1,values_only=True))]
    entries=[]; skipped=0; errors=[]
    for idx, vals in enumerate(ws.iter_rows(min_row=2,values_only=True),2):
        row=dict(zip(headers,vals))
        name=str(row.get("Nama Produk") or "").strip()
        price=price_int(row.get("Harga Referensi (Rp) - isi manual"))
        source=str(row.get("Nama Sumber") or "").strip()
        url=str(row.get("URL Halaman Harga") or "").strip()
        evidence=str(row.get("Bukti Harga pada Halaman") or "").strip()
        observed=str(row.get("Tanggal Cek (YYYY-MM-DD)") or "").strip()[:10]
        kind=str(row.get("Jenis Referensi (pasar/normal)") or "pasar").strip().lower()
        if not any([price,source,url,evidence]): continue
        parsed=urlparse(url)
        if not name or price is None or not source or parsed.scheme not in {"http","https"} or not parsed.hostname or not evidence:
            skipped+=1; errors.append(f"Baris {idx}: isian belum lengkap/URL tidak valid"); continue
        try: date.fromisoformat(observed)
        except ValueError:
            skipped+=1; errors.append(f"Baris {idx}: tanggal harus YYYY-MM-DD"); continue
        key=price_utils.identity_key(name)
        size="|".join(product_name_size_signature(name))
        variant="|".join(sorted(product_name_variant_signature(name)))
        domain=parsed.hostname.lower().removeprefix("www.")
        canonical=f"{key}|{price}|{observed}|{url}"
        refkey=hashlib.sha256(canonical.encode()).hexdigest()[:24]
        entries.append({
          "product_text":name,"product_key":key,"size_signature":size,"variant_signature":variant,
          "market_price":str(price),"observed_at":observed,"source_name":source,"source_domain":domain,
          "source_url":url,"evidence_text":evidence,"page_title":"","price_kind":"normal" if kind=="normal" else "market",
          "source_kind":"manual_user_checked","verification_method":"page_price_verified","confidence":"manual_checked",
          "reference_key":refkey})
    wb.close()
    DB.parent.mkdir(parents=True,exist_ok=True)
    existing=[]
    if DB.exists():
        with DB.open("r",encoding="utf-8-sig",newline="") as f: existing=list(csv.DictReader(f))
    known={r.get("reference_key") or hashlib.sha256("|".join([r.get("product_key",""),r.get("source_url",""),r.get("observed_at",""),r.get("market_price","")]).encode()).hexdigest()[:24] for r in existing}
    added=0
    for r in entries:
        if r["reference_key"] not in known:
            existing.append(r); known.add(r["reference_key"]); added+=1
    with DB.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS); writer.writeheader()
        for r in existing: writer.writerow({k:r.get(k,"") for k in FIELDS})
    print(f"Referensi manual baru diimpor: {added}")
    print(f"Referensi manual dilewati karena isian tidak lengkap/tidak valid: {skipped}")
    for e in errors[:20]: print(" - "+e)
    print(f"Database: {DB}")
    print("Langkah berikutnya: python engine\\enrich_existing_promo_catalog_multisource.py")
    if not added and not skipped: print("Belum ada baris referensi baru yang lengkap untuk diimpor.")

if __name__=="__main__": main()
