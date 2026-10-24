from __future__ import annotations

import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_DIR))

import enrich_existing_promo_catalog_multisource as enhancer


def _candidate(**overrides):
    row = {
        "product_text": "Sunlight Jeruk 755 ml",
        "original_product_text": "Sunlight Jeruk 755 ml",
        "merchant_name": "Super Indo",
        "campaign_title": "Promo Super Indo 23-25 Oktober 2026",
        "promo_status": "active",
        "period_certainty": "confirmed",
        "period_precision": "range",
        "valid_from": "2026-10-23",
        "valid_until": "2026-10-25",
        "promo_price": 16900,
        "normal_price": 22900,
        "promo_price_evidence_text": "Harga Promo Rp16.900",
        "normal_price_evidence_text": "Harga Normal Rp22.900",
        "discount_percent": 26.2,
        "discount_percent_basis": "calculated_from_unit_prices",
        "discount_percent_confidence": "high",
        "price_basis": "offer_price",
        "offer_text": "Harga Normal Rp22.900, Harga Promo Rp16.900",
        "raw_line": "Sunlight Jeruk 755 ml: Harga Normal Rp22.900, Harga Promo Rp16.900",
        "source_url": "https://example.test/promo-sunlight",
        "source_name": "Katalogpromosi.com",
        "product_validation_status": "eligible",
        "product_review_reason": None,
        "product_rejection_reason": None,
        "price_quality_flag": None,
        "data_quality_flags": None,
    }
    row.update(overrides)
    return row


def test_product_validation_is_displayed_separately_from_promo_status():
    row = _candidate(promo_status="active_estimated", period_certainty="estimated")
    row["display_status"] = enhancer.display_status(row)
    cleaned = enhancer.make_clean_row(row, review=False)
    assert cleaned["Status Promo"] == "Aktif (periode estimasi)"
    assert cleaned["Status Validasi Produk"] == "Valid"

    row["product_validation_status"] = "review"
    cleaned = enhancer.make_clean_row(row, review=True)
    assert cleaned["Status Promo"] == "Aktif (periode estimasi)"
    assert cleaned["Status Validasi Produk"] == "Perlu review"


def test_quality_gate_routes_unknown_merchant_to_review():
    row = _candidate(merchant_name="UNKNOWN")
    current, report = enhancer.apply_export_quality_gate([row])
    assert current == []
    assert report["status"] == "passed"
    assert report["routed_to_review_rows"] == 1
    assert "merchant_missing_or_unknown" in report["routed_rows"][0]["issues"]
    assert row["product_validation_status"] == "review"


def test_quality_gate_routes_unclassified_category_to_review():
    row = _candidate(product_text="Novel Zeta Volume 1")
    category, _ = enhancer.classify_promo_category(row)
    if category != "Lainnya / Perlu Review":
        # Use a category which the current taxonomy deliberately leaves unresolved.
        row["product_text"] = "Barang Xyz Blorpt 1 unit"
        category, _ = enhancer.classify_promo_category(row)
    assert category == "Lainnya / Perlu Review"
    current, report = enhancer.apply_export_quality_gate([row])
    assert current == []
    assert report["status"] == "passed"
    assert "category_requires_manual_review" in report["routed_rows"][0]["issues"]


def test_quality_gate_clears_qris_cashback_amount_from_product_price():
    row = _candidate(
        promo_price=2000,
        promo_price_evidence_text="Pembayaran QRIS cashback Rp2.000",
        normal_price=None,
        normal_price_evidence_text=None,
        offer_text="Pembayaran QRIS cashback Rp2.000",
        raw_line="Sunlight Jeruk 755 ml — pembayaran QRIS cashback Rp2.000",
        discount_percent=None,
        discount_percent_basis="not_available",
    )
    current, report = enhancer.apply_export_quality_gate([row])
    assert current == []
    assert report["status"] == "passed"
    assert "promo_price_looks_like_payment_or_purchase_condition" in report["routed_rows"][0]["issues"]
    assert row["product_validation_status"] == "review"
    assert row["promo_price"] is None


def test_quality_gate_quarantines_empty_or_generic_product_name_and_clears_amounts():
    row = _candidate(product_text="Potongan harga", original_product_text="Potongan harga")
    current, report = enhancer.apply_export_quality_gate([row])
    assert current == []
    assert report["status"] == "passed"
    assert "product_identity_empty_or_generic" in report["routed_rows"][0]["issues"]
    assert row["product_validation_status"] == "review"
    assert row["promo_price"] is None
    assert row["normal_price"] is None


def test_valid_product_price_evidence_passes_gate():
    row = _candidate()
    current, report = enhancer.apply_export_quality_gate([row])
    assert len(current) == 1
    assert report["status"] == "passed"
    assert report["routed_to_review_rows"] == 0
    assert enhancer.export_quality_gate_issues(current[0]) == []


def test_discount_over_100_percent_is_quarantined_and_not_displayed_as_discount():
    row = _candidate(discount_percent=150, discount_percent_basis="explicit_offer")
    current, report = enhancer.apply_export_quality_gate([row])
    assert current == []
    assert report["status"] == "passed"
    assert "product_discount_out_of_range" in report["routed_rows"][0]["issues"]
    assert row["product_validation_status"] == "review"
    assert row["discount_percent"] is None


def test_gate_does_not_publish_inferred_price_even_when_amount_text_matches():
    row = _candidate(price_quality_flag="price_inferred")
    current, report = enhancer.apply_export_quality_gate([row])
    assert current == []
    assert report["status"] == "passed"
    assert "promo_price_inferred_needs_review" in report["routed_rows"][0]["issues"]
    assert row["promo_price"] is None
    assert row["product_validation_status"] == "review"


def test_gate_routes_cross_source_price_conflict_to_review():
    row = _candidate(price_quality_flag="cross_source_price_disagreement")
    current, report = enhancer.apply_export_quality_gate([row])
    assert current == []
    assert report["status"] == "passed"
    assert "cross_source_price_disagreement" in report["routed_rows"][0]["issues"]
    assert row["product_validation_status"] == "review"
