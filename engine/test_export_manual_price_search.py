import csv
import export_manual_price_search as exporter


def _write_csv(path, rows):
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_market_search_excludes_generic_review_and_non_catalog_rows_but_keeps_new_categories(tmp_path, monkeypatch):
    source = tmp_path / "products.csv"
    catalog = tmp_path / "Katalog_Promo_Final_Ringkas.csv"
    _write_csv(source, [
        {"product_text": "Sunlight Lime 910 ml", "promo_price": "16900", "normal_price": "", "merchant_name": "Shop A", "source_name": "Shop A", "product_validation_status": "eligible", "source_url": "https://example.test/promo"},
        {"product_text": "All Fruit", "promo_price": "12000", "normal_price": "", "merchant_name": "Shop B", "product_validation_status": "eligible", "source_url": "https://example.test/promo2"},
        {"product_text": "Harga spesial", "promo_price": "50000", "normal_price": "", "merchant_name": "Foodhall", "product_validation_status": "eligible", "source_url": "https://example.test/promo3"},
        {"product_text": "All Sayur", "promo_price": "9000", "normal_price": "", "merchant_name": "Shop C", "product_validation_status": "review", "source_url": "https://example.test/promo4"},
        {"product_text": "Produk tidak ada di katalog", "promo_price": "10000", "normal_price": "", "merchant_name": "Shop D", "product_validation_status": "eligible", "source_url": "https://example.test/promo5"},
    ])
    _write_csv(catalog, [
        {"Nama Produk": "Sunlight Lime 910 ml"},
        {"Nama Produk": "All Fruit"},
        {"Nama Produk": "Harga spesial"},
        {"Nama Produk": "All Sayur"},
    ])
    monkeypatch.setattr(exporter, "SOURCE", source)
    monkeypatch.setattr(exporter, "CATALOG_CANDIDATES", (catalog,))

    products, stats = exporter.read_products()

    assert {row["Nama Produk"] for row in products} == {"Sunlight Lime 910 ml", "All Fruit"}
    assert stats["excluded_generic"] == 1
    assert stats["excluded_not_eligible"] == 1
    assert stats["excluded_not_in_catalog"] == 1
    assert "All Fruit" in {row["Nama Produk"] for row in products}


def test_reviewed_poster_price_is_not_queued_for_market_search(tmp_path, monkeypatch):
    source = tmp_path / "products.csv"
    catalog = tmp_path / "Katalog_Promo_Final_Ringkas.csv"
    _write_csv(source, [
        {"product_text": "Sunlight Lime 910 ml", "promo_price": "16900", "normal_price": "", "merchant_name": "Shop A", "source_name": "Shop A", "product_validation_status": "eligible", "source_url": "https://example.test/promo"},
    ])
    _write_csv(catalog, [{"Nama Produk": "Sunlight Lime 910 ml"}])
    override_file = tmp_path / "overrides.csv"
    with override_file.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["candidate_id", "decision", "product_text", "normal_price", "promo_price", "source_name", "article_url", "image_url", "ocr_snippet", "reviewed_at"])
        writer.writeheader()
        writer.writerow({"candidate_id": "ABC123", "decision": "TERIMA", "product_text": "Sunlight Lime 910 ml", "normal_price": "20000", "promo_price": "16900", "source_name": "Shop A", "article_url": "https://example.test/promo", "image_url": "https://example.test/poster.jpg", "ocr_snippet": "Harga Normal Rp20.000 Harga Promo Rp16.900", "reviewed_at": "2026-10-25T10:00:00+07:00"})
    monkeypatch.setattr(exporter, "SOURCE", source)
    monkeypatch.setattr(exporter, "CATALOG_CANDIDATES", (catalog,))
    monkeypatch.setattr(exporter, "DEFAULT_OVERRIDES_CSV", override_file)
    products, stats = exporter.read_products()
    assert products == []
    assert stats["excluded_with_normal_price"] == 1
