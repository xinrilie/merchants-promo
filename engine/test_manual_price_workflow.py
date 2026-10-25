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
    importer.main([])
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
    importer.main([])
    rows=list(csv.DictReader(db.open(encoding="utf-8-sig")))
    assert len(rows)==0

def test_import_ai_json_reference(tmp_path, monkeypatch):
    import json
    json_file = tmp_path / "references.json"
    db = tmp_path / "market.csv"
    json_file.write_text(json.dumps({"references": [{
        "product_name": "Sunlight Lime 910 ml",
        "reference_price": 16900,
        "source_name": "Toko Contoh",
        "source_url": "https://shop.example/product/sunlight-lime",
        "evidence_text": "Sunlight Lime 910 ml harga Rp16.900",
        "checked_date": "2026-10-25",
        "reference_type": "pasar",
        "page_title": "Sunlight Lime 910 ml - Toko Contoh",
    }]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(importer, "JSON_IN", json_file)
    monkeypatch.setattr(importer, "DB", db)
    importer.main([])
    rows = list(csv.DictReader(db.open(encoding="utf-8-sig")))
    assert len(rows) == 1
    assert rows[0]["market_price"] == "16900"
    assert rows[0]["confidence"] == "ai_assisted_needs_review"
    assert rows[0]["source_kind"] == "ai_assisted_user_supplied"


def test_json_search_result_url_is_rejected(tmp_path):
    import json
    json_file = tmp_path / "references.json"
    json_file.write_text(json.dumps({"references": [{
        "product_name": "Product X", "reference_price": 15000,
        "source_name": "Google", "source_url": "https://www.google.com/search?q=product+x",
        "evidence_text": "Product X Rp15.000", "checked_date": "2026-10-25",
        "reference_type": "pasar",
    }]}), encoding="utf-8")
    entries, errors, total = importer._read_json(json_file)
    assert total == 1
    assert entries == []
    assert len(errors) == 1 and "URL hasil pencarian" in errors[0]
