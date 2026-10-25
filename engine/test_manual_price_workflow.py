import csv
from pathlib import Path
from openpyxl import Workbook, load_workbook
import import_manual_price_references as importer

def test_price_parser_indonesian():
    assert importer.price_int("Rp 22.900") == 22900
    assert importer.price_int("16.900") == 16900
    assert importer.price_int("0") is None

def test_import_manual_reference(tmp_path, monkeypatch):
    from openpyxl import Workbook
    xlsx=tmp_path/"queue.xlsx"; db=tmp_path/"market.csv"
    wb=Workbook(); ws=wb.active; ws.title="Cari Harga"
    headers=["Nama Produk","Harga Referensi (Rp) - isi manual","Nama Sumber","URL Halaman Harga","Bukti Harga pada Halaman","Tanggal Cek (YYYY-MM-DD)","Jenis Referensi (pasar/normal)"]
    ws.append(headers)
    ws.append(["Sunlight Lime 910 ml","16900","Toko Contoh","https://shop.example/product","Sunlight Lime 910 ml Rp16.900","2026-10-25","pasar"])
    wb.save(xlsx)
    monkeypatch.setattr(importer,"XLSX",xlsx); monkeypatch.setattr(importer,"DB",db)
    importer.main()
    rows=list(csv.DictReader(db.open(encoding="utf-8-sig")))
    assert len(rows)==1
    assert rows[0]["market_price"]=="16900"
    assert rows[0]["verification_method"]=="page_price_verified"

def test_incomplete_rows_are_not_imported(tmp_path, monkeypatch):
    xlsx=tmp_path/"queue.xlsx"; db=tmp_path/"market.csv"
    wb=Workbook(); ws=wb.active; ws.title="Cari Harga"
    headers=["Nama Produk","Harga Referensi (Rp) - isi manual","Nama Sumber","URL Halaman Harga","Bukti Harga pada Halaman","Tanggal Cek (YYYY-MM-DD)","Jenis Referensi (pasar/normal)"]
    ws.append(headers); ws.append(["Product X","15000","Shop","","price 15000","2026-10-25","pasar"]); wb.save(xlsx)
    monkeypatch.setattr(importer,"XLSX",xlsx); monkeypatch.setattr(importer,"DB",db)
    importer.main()
    rows=list(csv.DictReader(db.open(encoding="utf-8-sig")))
    assert len(rows)==0
