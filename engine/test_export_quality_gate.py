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


def test_explicit_discount_conflicting_with_same_row_prices_routes_to_review():
    row = _candidate(
        normal_price=17900,
        promo_price=10900,
        discount_percent=35.0,
        discount_percent_basis="explicit_offer",
        discount_percent_calculated=39.1,
        discount_savings_rp=7000,
        offer_text="Diskon: Hemat 35% | Harga Normal: Rp17.900 | Harga Promo: Rp10.900",
        raw_line="Cadbury Dairy Milk Roast Almond 52 g - Hemat 35%, normal Rp17.900, promo Rp10.900",
        discount_evidence_text="Hemat 35%",
    )
    current, report = enhancer.apply_export_quality_gate([row])
    assert current == []
    assert report["status"] == "passed"
    assert "explicit_discount_price_math_disagreement" in report["routed_rows"][0]["issues"]
    assert row["source_discount_claim_percent"] == 35.0
    assert row["discount_percent"] is None
    assert row["discount_percent_basis"] == "discount_price_mismatch_review"
    assert row["discount_percent_calculated"] == 39.1
    cleaned = enhancer.make_clean_row(row, review=True)
    assert cleaned["Diskon (%)"] is None
    assert cleaned["Klaim Diskon Sumber (Review) (%)"] == 35.0
    assert "39.1%" in cleaned["Catatan"]


def test_maximum_discount_claim_only_flags_when_price_math_exceeds_claim():
    acceptable = _candidate(
        normal_price=20000, promo_price=15000,
        discount_percent=30, discount_percent_basis="explicit_max",
        discount_percent_calculated=25.0,
        offer_text="Diskon hingga 30%, harga normal Rp20.000, harga promo Rp15.000",
    )
    assert enhancer.discount_price_consistency_issue(acceptable) is None
    excessive = dict(acceptable, discount_percent_calculated=36.0)
    assert enhancer.discount_price_consistency_issue(excessive) == "maximum_discount_below_price_calculation"


def test_row_freshness_is_visible_and_not_conflated_with_verification():
    recent = enhancer.classify_row_freshness("2026-10-24T10:00:00+07:00", now_dt=__import__("datetime").datetime.fromisoformat("2026-10-24T12:00:00+07:00"))
    historic = enhancer.classify_row_freshness("")
    assert recent == "fresh"
    assert historic == "historical_or_unverified"
    assert enhancer.classify_row_freshness("bukan-timestamp") == "invalid_timestamp"
    future = enhancer.classify_row_freshness(
        "2026-10-25T00:00:00+07:00",
        now_dt=__import__("datetime").datetime.fromisoformat("2026-10-24T12:00:00+07:00"),
    )
    assert future == "invalid_timestamp"
    row = _candidate(freshness_status="historical_or_unverified", scraped_at=None)
    cleaned = enhancer.make_clean_row(row)
    assert cleaned["Kesegaran Data"] == "Historis / timestamp tidak tersedia"
    assert cleaned["Verifikasi Sumber"] != cleaned["Kesegaran Data"]


def test_discount_conflict_in_existing_review_row_is_sanitized_too():
    row = _candidate(
        product_validation_status="review",
        normal_price=17900,
        promo_price=10900,
        discount_percent=35.0,
        discount_percent_basis="explicit_offer",
        discount_percent_calculated=39.1,
        offer_text="Hemat 35%, Harga Normal Rp17.900, Harga Promo Rp10.900",
        discount_evidence_text="Hemat 35%",
    )
    _, report = enhancer.apply_export_quality_gate([row])
    assert report["additional_existing_review_discount_rows"] == 1
    assert "explicit_discount_price_math_disagreement" in row["export_gate_flags"]
    assert row["source_discount_claim_percent"] == 35.0
    assert row["discount_percent"] is None
