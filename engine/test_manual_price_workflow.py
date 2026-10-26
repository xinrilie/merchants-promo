import csv
import pytest
from openpyxl import Workbook

import import_manual_price_references as importer


def _workbook(path, row):
    wb = Workbook()
    ws = wb.active
    ws.title = "Cari Harga"
    headers = ["Nama Produk", "Harga Referensi (Rp) - isi manual", "Nama Sumber", "URL Halaman Harga", "Bukti Harga pada Halaman", "Tanggal Cek (YYYY-MM-DD)", "Jenis Referensi (pasar/normal)", "Judul Halaman"]
    ws.append(headers)
    ws.append(row)
    wb.save(path)


def test_price_parser_indonesian():
    assert importer.price_int("Rp 22.900") == 22900
    assert importer.price_int("16.900") == 16900
    assert importer.price_int("0") is None


def test_import_manual_market_reference(tmp_path, monkeypatch):
    xlsx = tmp_path / "market.xlsx"
    db = tmp_path / "market.csv"
    _workbook(xlsx, ["Sunlight Lime 910 ml", "16900", "Toko Contoh", "https://shop.example/product", "Sunlight Lime 910 ml harga normal Rp16.900", "2026-10-25", "pasar", "Sunlight Lime 910 ml"])
    monkeypatch.setattr(importer, "XLSX", xlsx)
    monkeypatch.setattr(importer, "DB", db)
    importer.DB = db
    importer.main([str(xlsx)])
    rows = list(csv.DictReader(db.open(encoding="utf-8-sig")))
    assert len(rows) == 1
    assert rows[0]["market_price"] == "16900"
    assert rows[0]["source_kind"] == "manual_user_checked"
    assert rows[0]["verification_method"] == "page_price_verified"


def test_ai_json_is_explicitly_rejected(tmp_path):
    json_file = tmp_path / "old-estimates.json"
    json_file.write_text('{"estimates": [{"product_name": "Product X", "estimated_price": 15000}]}', encoding="utf-8")
    with pytest.raises(SystemExit, match="estimasi AI sudah dihapus"):
        importer.main([str(json_file)])


def test_incomplete_manual_market_reference_is_not_imported(tmp_path):
    xlsx = tmp_path / "market.xlsx"
    _workbook(xlsx, ["Product X 500 g", "15000", "Shop", "", "Harga 15000", "2026-10-25", "pasar", "Product X"])
    entries, errors, total = importer._read_xlsx(xlsx)
    assert total == 1
    assert entries == []
    assert any("URL harus berupa URL halaman" in error for error in errors)
