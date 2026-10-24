from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_DIR))

import enrich_existing_promo_catalog_multisource as enhancer
import promo_multisource_collector as collector
from bs4 import BeautifulSoup
from promo_product_validation import pipe_sources_for_validation
from promo_evidence import (
    find_amount_evidence,
    find_discount_evidence,
    period_certainty,
    source_verification_label,
)


def _write_feed(path: Path, row: dict) -> None:
    fields = list(row)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(row)


def test_evidence_extractors_return_literal_source_text():
    offer = "Harga Promo Rp12.900, Harga Normal Rp15.900, Diskon 18,9%"
    promo = find_amount_evidence(offer, 12900, "promo")
    normal = find_amount_evidence(offer, 15900, "normal")
    discount = find_discount_evidence(offer, 18.9)
    assert promo and "Rp12.900" in promo and "Promo" in promo
    assert normal and "Rp15.900" in normal and "Normal" in normal
    assert discount and "18,9%" in discount
    assert find_amount_evidence("Hanya diskon produk tanpa nominal harga", 12900, "promo") is None


def test_load_source_reconstructs_evidence_and_keeps_new_merchant():
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "feed.csv"
        _write_feed(path, {
            "product_text": "All Fruit Fuji Apples 1 kg",
            "merchant_name": "All Fruit",
            "campaign_title": "Promo All Fruit 23-25 Oktober 2026",
            "offer_text": "Harga Promo Rp12.900, Harga Normal Rp15.900",
            "normal_price": "15900",
            "promo_price": "12900",
            "price_basis": "offer_price",
            "valid_from": "2026-10-23",
            "valid_until": "2026-10-25",
            "period_precision": "range",
            "source_name": "Hemat.id",
            "source_type": "aggregator_product_listing",
            "verification_status": "aggregator_only",
            "source_url": "https://example.test/all-fruit/promo",
            "raw_line": "All Fruit Fuji Apples 1 kg — Harga Promo Rp12.900, Harga Normal Rp15.900",
        })
        rows = enhancer.load_source(path, "promo_multisource_products_all.csv")

    assert len(rows) == 1
    row = rows[0]
    assert row["merchant_name"] == "All Fruit"
    assert "Rp12.900" in row["promo_price_evidence_text"]
    assert "Rp15.900" in row["normal_price_evidence_text"]
    assert row["discount_percent_basis"] == "calculated_from_unit_prices"
    assert row["discount_evidence_text"]
    assert row["period_evidence_text"]
    assert row["period_certainty"] == "confirmed"
    assert row["source_verification_label"] == "Agregator; belum diverifikasi retailer"


def test_unsubstantiated_price_is_cleared_and_sent_to_review():
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "feed.csv"
        _write_feed(path, {
            "product_text": "Snack Brand Potato Chips 200 g",
            "merchant_name": "Fresh Basket",
            "campaign_title": "Promo Fresh Basket Minggu Ini",
            "offer_text": "Diskon 20% untuk produk pilihan",
            "normal_price": "",
            "promo_price": "9999",
            "price_basis": "offer_price",
            "discount_percent": "20",
            "valid_from": "2026-10-01",
            "valid_until": "2026-10-31",
            "period_precision": "range",
            "source_name": "Katalogpromosi.com",
            "source_type": "aggregator_article",
            "verification_status": "aggregator_only",
            "source_url": "https://example.test/promo",
            "raw_line": "Snack Brand Potato Chips 200 g — Diskon 20% untuk produk pilihan",
        })
        rows = enhancer.load_source(path, "promo_multisource_products_all.csv")

    assert len(rows) == 1
    row = rows[0]
    assert row["promo_price"] is None
    assert "promo_price_without_matching_offer_evidence" in (row["price_quality_flag"] or "")
    assert row["product_validation_status"] == "review"
    assert "Harga promo tidak memiliki bukti teks" in (row["product_review_reason"] or "")
    assert row["period_precision"] == "unverified"
    assert row["promo_status"] == "active_estimated"
    assert row["period_certainty"] == "estimated"


def test_name_cleaned_to_empty_is_reviewed_and_never_published_as_product_price():
    with __import__("tempfile").TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "generic_price_labels.csv"
        fields = [
            "product_text", "merchant_name", "campaign_title", "promo_status",
            "valid_from", "valid_until", "period_precision", "promo_price",
            "price_basis", "offer_text", "source_url", "source_name",
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows([
                {
                    "product_text": "Potongan harga", "merchant_name": "Alfamart",
                    "campaign_title": "Promo JSM Alfamart 23-25 Oktober 2026",
                    "promo_status": "active", "valid_from": "2026-10-23",
                    "valid_until": "2026-10-25", "period_precision": "range",
                    "promo_price": "5000", "price_basis": "offer_price",
                    "offer_text": "Rp5.000.", "source_url": "https://example.test/alfamart",
                    "source_name": "Katalogpromosi.com",
                },
                {
                    "product_text": "Harga spesial", "merchant_name": "Foodhall",
                    "campaign_title": "Promo THEFOODHALL 23-29 Oktober 2026",
                    "promo_status": "active", "valid_from": "2026-10-23",
                    "valid_until": "2026-10-29", "period_precision": "range",
                    "promo_price": "50000", "price_basis": "offer_price",
                    "offer_text": "Rp 50.000 dan Rp 100.000 berlaku untuk produk tertentu",
                    "source_url": "https://example.test/foodhall",
                    "source_name": "Katalogpromosi.com",
                },
            ])
        rows = enhancer.load_source(path, path.name)

    assert len(rows) == 2
    for row in rows:
        assert row["product_text"] == ""
        assert row["product_validation_status"] == "review"
        assert row["promo_price"] is None
        assert row["normal_price"] is None
        assert row["discount_percent"] is None
        assert row["product_subcategory"] == "Produk belum teridentifikasi"
        display = enhancer.make_clean_row(row, review=True)
        assert display["Nama Produk"] == "Produk belum teridentifikasi"
        assert display["Harga Promo (Rp)"] is None
        assert display["Harga Asli (Rp)"] is None


def test_period_certainty_is_separate_from_retailer_verification():
    assert period_certainty("range", "active", None, "23-25 Oktober 2026") == "confirmed"
    assert period_certainty("month", "active_estimated", None, "Oktober 2026") == "estimated"
    assert period_certainty("unverified", "active_estimated", "period_evidence_missing_unverified", None) == "estimated"
    assert period_certainty("range", "active", "title_period_mismatch_with_catalog_dates", "23-25 Oktober 2026") == "conflict"
    assert source_verification_label("aggregator_only") == "Agregator; belum diverifikasi retailer"
    assert source_verification_label("unrecognized_source_type") == "Beberapa sumber; bukan verifikasi retailer"
    assert source_verification_label("retailer_verified") == "Retailer terverifikasi"
    assert source_verification_label("aggregator_only | historical_baseline_unknown") == "Beberapa sumber; sebagian asal verifikasi belum diketahui"


def test_each_live_adapter_emits_price_and_period_evidence():
    # Hemat.id structured-card path.
    hemat_meta = collector.parse_promo_metadata(
        "Promo All Fruit 23-25 Oktober 2026", "",
        "Berlaku 23-25 Oktober 2026"
    )
    hemat_row = collector.make_product_row(
        source_name="Hemat.id",
        product_text="All Fruit Fuji Apples 1 kg",
        offer_text="Harga Promo Rp12.900, Harga Normal Rp15.900",
        title="Promo All Fruit 23-25 Oktober 2026",
        merchant="All Fruit", meta=hemat_meta,
        source_url="https://hemat.example/promo",
        source_page_url="https://hemat.example/promo",
        source_item_url="https://hemat.example/item/apple",
        raw_line="All Fruit Fuji Apples 1 kg — Harga Promo Rp12.900, Harga Normal Rp15.900",
        source_mode="hemat",
    )
    assert "Rp12.900" in hemat_row["promo_price_evidence_text"]
    assert "Rp15.900" in hemat_row["normal_price_evidence_text"]
    assert hemat_row["period_evidence_text"]

    # Katalogpromosi/Scanharga share article-line extraction but retain their source labels.
    html = BeautifulSoup(
        "<article><ul><li>Fresh Basket Fuji Apples 1 kg → Harga Promo Rp12.900, Harga Normal Rp15.900</li></ul></article>",
        "html.parser",
    )
    title = "Promo JSM Fresh Basket 23-25 Oktober 2026"
    meta = collector.parse_promo_metadata(title, "23-25 Oktober 2026")
    article_rows = collector.extract_article_rows(
        html, "Katalogpromosi.com", title,
        "https://katalogpromosi.example/promo-fresh-basket", meta
    )
    assert article_rows, "Katalogpromosi article adapter should extract the fixture offer"
    assert article_rows[0]["promo_price_evidence_text"]
    assert article_rows[0]["normal_price_evidence_text"]
    assert article_rows[0]["period_evidence_text"]



def test_source_freshness_report_does_not_call_missing_timestamp_fresh():
    now = datetime.fromisoformat("2026-10-23T12:00:00+07:00")
    rows = [
        {"source_name": "Hemat.id", "scraped_at": "2026-10-23T11:50:00+07:00"},
        {"source_name": "Scanharga.com", "scraped_at": "2026-10-01T11:50:00+07:00"},
        {"source_name": "Katalogpromosi.com", "scraped_at": "2026-10-23T11:55:00+07:00", "retained_from_previous_run": "true"},
        {"source_dataset": "older_catalog_baseline.csv", "scraped_at": ""},
    ]
    report = enhancer.build_source_freshness_report(rows, now)
    assert report["Hemat.id"]["status"] == "fresh"
    assert report["Hemat.id"]["fresh_rows"] == 1
    assert report["Scanharga.com"]["status"] == "stale"
    assert report["Katalogpromosi.com"]["status"] == "preserved_previous"
    assert report["Source tidak diketahui"]["status"] == "historical_or_unverified"


def test_collector_source_health_distinguishes_failure_from_empty_source():
    products = [
        {
            "source_name": "Hemat.id", "promo_price": 12900, "promo_price_evidence_text": "Harga Promo Rp12.900",
            "discount_percent": 20, "discount_evidence_text": "Diskon 20%", "period_evidence_text": "23-25 Oktober 2026",
            "retained_from_previous_run": False, "promo_status": "active",
        },
        {"source_name": "Scanharga.com", "promo_price": 10000, "retained_from_previous_run": True},
    ]
    requests = [
        {"source_name": "Hemat.id", "status": "ok"},
        {"source_name": "Scanharga.com", "status": "ok"},
        {"source_name": "Katalogpromosi.com", "status": "ok"},
    ]
    report = collector.build_source_health_report(
        ["Hemat.id", "Scanharga.com", "Katalogpromosi.com"],
        products,
        requests,
    )
    assert report["Hemat.id"]["status"] == "fresh"
    assert report["Hemat.id"]["promo_price_evidence_coverage"] == 1.0
    assert report["Scanharga.com"]["status"] == "preserved_previous"
    assert report["Katalogpromosi.com"]["status"] == "reachable_no_products"


def test_unknown_verification_provenance_is_not_mislabeled_as_aggregator():
    assert source_verification_label("historical_baseline_unknown") == "Baseline historis; asal verifikasi belum diketahui"


def test_storage_dataset_name_never_counts_as_independent_source():
    assert pipe_sources_for_validation({"source_dataset": "promo_baseline.csv"}) == []
    assert pipe_sources_for_validation({"source_name": "Hemat.id", "source_dataset": "promo_baseline.csv"}) == ["Hemat.id"]
    assert pipe_sources_for_validation({"source_names": "Hemat.id | Scanharga.com", "source_dataset": "promo_baseline.csv"}) == ["Hemat.id", "Scanharga.com"]


def test_source_freshness_reports_mixed_fresh_and_historical_and_last_good_status():
    now = datetime.fromisoformat("2026-10-24T19:40:00+07:00")
    rows = [
        {"source_name": "Katalogpromosi.com", "scraped_at": "2026-10-24T19:36:00+07:00"},
        {"source_name": "Katalogpromosi.com", "scraped_at": ""},
        {"source_name": "Katalogpromosi.com", "scraped_at": None},
    ]
    report = enhancer.build_source_freshness_report(rows, now, {
        "Katalogpromosi.com": {
            "checked_at": "2026-10-24T19:36:21+07:00",
            "output_status": "preserved_previous_catalog",
            "preserved_previous_rows": 17,
            "fresh_product_rows": 0,
            "request_count": 10,
        }
    })
    source = report["Katalogpromosi.com"]
    assert source["status"] == "mixed_fresh_and_historical_or_unverified"
    assert source["fresh_rows"] == 1
    assert source["rows_missing_or_invalid_timestamp"] == 2
    assert source["all_rows_fresh"] is False
    assert source["fresh_row_percent"] == 33.3
    assert enhancer.freshness_display_label("mixed:fresh|historical_or_unverified") == "Campuran: Segar (≤7 hari) + Historis / timestamp tidak tersedia"
    assert source["current_run"]["previous_catalog_preserved"] is True
    assert source["current_run"]["previous_catalog_preserved"] is True
    assert "tidak menggantikan katalog stabil" in source["current_run"]["freshness_note"]


def test_multi_source_provenance_does_not_copy_one_timestamp_to_every_source():
    now = datetime.fromisoformat("2026-10-24T12:00:00+07:00")
    report = enhancer.build_source_freshness_report([
        {
            "source_name": "Katalogpromosi.com",
            "source_names": "Katalogpromosi.com | Scanharga.com",
            "scraped_at": "2026-10-24T11:55:00+07:00",
        }
    ], now)
    assert report["Katalogpromosi.com"]["fresh_rows"] == 1
    assert "Scanharga.com" not in report


def test_current_source_metadata_comes_from_three_source_runlog(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(enhancer, "INTERNAL_DIR", tmp_path)
    monkeypatch.setattr(enhancer, "MULTISOURCE_RUN_LOG", tmp_path / "promo_multisource_runlog.json")
    log = {
        "built_at": "2026-10-24T19:36:21+07:00",
        "reference_date": "2026-10-24",
        "source_health": {
            "Hemat.id": {"status": "fresh", "fresh_product_rows": 20, "preserved_previous_rows": 0, "request_count": 5},
            "Katalogpromosi.com": {"status": "preserved_previous", "fresh_product_rows": 0, "preserved_previous_rows": 12, "request_count": 8},
            "Scanharga.com": {"status": "request_failed", "fresh_product_rows": 0, "preserved_previous_rows": 0, "request_count": 2},
        },
    }
    (tmp_path / "promo_multisource_runlog.json").write_text(json.dumps(log), encoding="utf-8")
    metadata = enhancer.current_source_run_metadata()
    assert set(metadata) == {"Hemat.id", "Katalogpromosi.com", "Scanharga.com"}
    assert metadata["Hemat.id"]["output_status"] == "updated"
    assert metadata["Katalogpromosi.com"]["output_status"] == "preserved_previous_catalog"
    assert metadata["Scanharga.com"]["output_status"] == "request_failed"
