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

def test_import_ai_json_estimates_need_only_product_and_price(tmp_path, monkeypatch):
    import json
    from promo_price_reference import load_reference_database
    json_file = tmp_path / "estimates.json"
    db = tmp_path / "product_price_reference.csv"
    json_file.write_text(json.dumps({"estimates": [{
        "product_name": "Sunlight Lime 910 ml",
        "estimated_price": 16900,
    }]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(importer, "JSON_IN", json_file)
    monkeypatch.setattr(importer, "PRICE_DB", db)
    importer.main([])
    rows = list(csv.DictReader(db.open(encoding="utf-8-sig")))
    assert len(rows) == 1
    assert rows[0]["normal_price"] == "16900"
    assert rows[0]["record_origin"] == "user_supplied_estimate"
    assert rows[0]["source_name"] == "Estimasi AI (tanpa sumber)"
    assert rows[0]["source_url"] == ""
    assert int(rows[0]["normal_price_low"]) < 16900 < int(rows[0]["normal_price_high"])
    loaded = load_reference_database(db)
    assert len(loaded) == 1
    assert loaded[0]["record_origin"] == "user_supplied_estimate"


def test_legacy_ai_references_with_markdown_urls_become_estimates(tmp_path, monkeypatch):
    import json
    json_file = tmp_path / "references.json"
    db = tmp_path / "product_price_reference.csv"
    json_file.write_text(json.dumps({"references": [{
        "product_name": "Merries Pants Good Skin M32 32 pcs",
        "reference_price": 61500,
        "source_name": "Raja Susu ID",
        "source_url": "[https://rajasusu.com/collections/merries](https://rajasusu.com/collections/merries)",
        "evidence_text": "Merries Pants Good Skin 32 pcs Rp61.500",
        "checked_date": "2026-10-25",
        "reference_type": "pasar",
    }]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(importer, "JSON_IN", json_file)
    monkeypatch.setattr(importer, "PRICE_DB", db)
    importer.main([])
    rows = list(csv.DictReader(db.open(encoding="utf-8-sig")))
    assert len(rows) == 1
    assert rows[0]["normal_price"] == "61500"
    assert rows[0]["record_origin"] == "user_supplied_estimate"
    assert rows[0]["source_url"] == ""
    assert "bukan harga terverifikasi" in rows[0]["evidence_text"].lower()


def test_existing_user_range_is_not_overwritten_by_ai_estimate(tmp_path, monkeypatch):
    import json
    from promo_price_reference import REFERENCE_FIELDS, _normalize_record
    json_file = tmp_path / "estimates.json"
    db = tmp_path / "product_price_reference.csv"
    original = _normalize_record({
        "merchant_name": "Referensi Umum",
        "product_text": "Product X 500 g",
        "normal_price": "20000",
        "observed_at": "2026-10-25",
        "source_name": "Rentang referensi yang diberikan pengguna",
        "source_url": "",
        "evidence_text": "Rentang acuan pengguna Rp15.000–Rp25.000; titik tengah Rp20.000.",
        "record_origin": "user_supplied_estimate",
        "verification_status": "user_supplied_estimate",
        "normal_price_low": "15000",
        "normal_price_high": "25000",
    })
    with db.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REFERENCE_FIELDS); w.writeheader(); w.writerow(original)
    json_file.write_text(json.dumps({"estimates": [{
        "product_name": "Product X 500 g", "estimated_price": 22000,
    }]}), encoding="utf-8")
    monkeypatch.setattr(importer, "JSON_IN", json_file)
    monkeypatch.setattr(importer, "PRICE_DB", db)
    importer.main([])
    rows = list(csv.DictReader(db.open(encoding="utf-8-sig")))
    assert len(rows) == 1
    assert rows[0]["normal_price"] == "20000"


def test_json_estimate_without_price_is_rejected(tmp_path):
    import json
    json_file = tmp_path / "estimates.json"
    json_file.write_text(json.dumps({"estimates": [{"product_name": "Product X"}]}), encoding="utf-8")
    entries, errors, total = importer._read_json_estimates(json_file)
    assert total == 1
    assert entries == []
    assert len(errors) == 1 and "harga estimasi tidak valid" in errors[0]
