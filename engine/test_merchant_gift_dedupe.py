from __future__ import annotations

import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_DIR))

import enrich_existing_promo_catalog_multisource as enhancer


def _base_row(**updates):
    row = {
        "product_text": "Sunlight Jeruk 755 ml",
        "original_product_text": "Sunlight Jeruk 755 ml",
        "merchant_name": "Indomaret",
        "campaign_title": "Promo Indomaret 23-25 Oktober 2026",
        "promo_status": "active",
        "period_precision": "range",
        "valid_from": "2026-10-23",
        "valid_until": "2026-10-25",
        "promo_price": 16900,
        "normal_price": 22900,
        "discount_percent": 26.2,
        "discount_percent_basis": "calculated_from_unit_prices",
        "discount_percent_confidence": "high",
        "price_basis": "offer_price",
        "offer_text": "Harga Normal Rp22.900, Harga Promo Rp16.900",
        "purchase_condition": None,
        "deal_type": "Diskon Persentase",
        "source_url": "https://example.test/promo-a",
        "source_item_url": "https://example.test/item-a",
        "source_name": "Katalogpromosi.com",
        "source_dataset": "katalog.csv",
        "product_validation_status": "eligible",
        "price_quality_flag": None,
    }
    row.update(updates)
    return row


def test_generic_merchant_is_resolved_from_article_title_and_url():
    merchant, method = enhancer.canonical_merchant(
        "Lainnya",
        "Duo Bakso A: promo spesial Es Teler 77",
        "https://katalogpromosi.com/promo/promo-es-teler-77-duo-bakso-a",
    )
    assert merchant == "Es Teler 77"
    assert method in {"title_url_known_alias", "known_alias_from_title_or_url", "source_url_known_alias"}


def test_generic_merchant_is_not_kept_when_context_is_ambiguous():
    merchant, method = enhancer.canonical_merchant(
        "Lainnya",
        "Promo Es Teler 77 x McDonald's",
        "https://example.test/promo-es-teler-77-mcdonalds",
    )
    assert merchant is None
    assert method == "conflicting_merchant_context"


def test_dynamic_unknown_merchant_inference_remains_supported():
    merchant, method = enhancer.canonical_merchant(
        "Lainnya",
        "Promo All Fruit 23-25 Oktober 2026",
        "https://example.test/promo-all-fruit",
    )
    assert merchant == "All Fruit"
    assert method == "campaign_title_inference"


def test_cross_sku_gift_does_not_become_product_discount():
    # Reproduce the live-row leak: 400 g facial tissue with a different travel-pack SKU.
    offer = "BELI 1 GRATIS 2 pck INDOMARET Travel Pack Tissue 50’s"
    flags = []
    discount = enhancer.safe_discount_fields(
        offer_text=offer,
        promo_price=None,
        normal_price=None,
        price_basis="non_numeric_bundle_offer",
        purchase_condition="BELI 1 GRATIS 2 pck INDOMARET Travel Pack Tissue 50’s",
        deal_type="Beli Gratis",
        quality_flags=flags,
        product_text="Indomaret Facial Tissue Non Perfumed 400 g",
        raw_line=f"Indomaret Facial Tissue Non Perfumed 400 g: {offer}",
    )
    assert discount["discount_percent"] is None
    assert discount["estimated_bundle_discount_percent"] is None
    assert discount["discount_percent_basis"] == "cross_sku_bundle_review"
    assert "cross_sku_gift_not_product_discount" in flags

    row = _base_row(
        product_text="Indomaret Facial Tissue Non Perfumed 400 g",
        offer_text=offer,
        purchase_condition="BELI 1 GRATIS 2 pck INDOMARET Travel Pack Tissue 50’s",
        deal_type="Beli Gratis",
        discount_percent=None,
        discount_percent_basis="cross_sku_bundle_review",
        discount_percent_confidence="unknown",
        promo_price=None,
        normal_price=None,
        price_basis="non_numeric_bundle_offer",
        price_quality_flag="cross_sku_gift_not_product_discount",
    )
    _, gate_report = enhancer.apply_export_quality_gate([row])
    assert gate_report["routed_to_review_rows"] == 1
    assert "cross_sku_gift_bundle_requires_review" in gate_report["routed_rows"][0]["issues"]


def test_generic_buy_one_get_one_estimate_still_supported_without_named_gift_sku():
    flags = []
    discount = enhancer.safe_discount_fields(
        offer_text="Beli 1 Gratis 1",
        promo_price=None,
        normal_price=None,
        price_basis="non_numeric_bundle_offer",
        purchase_condition="Beli 1 Gratis 1",
        deal_type="Beli Gratis",
        quality_flags=flags,
        product_text="Indomie Goreng",
    )
    assert discount["discount_percent"] == 50.0
    assert discount["discount_percent_basis"] == "bundle_estimate_same_item_value"
    assert "cross_sku_gift_not_product_discount" not in flags


def test_dedupe_does_not_merge_different_named_gift_items():
    a = _base_row(
        product_text="Facial Tissue 400 g",
        offer_text="Beli 1 Gratis 1, hadiah Facial Tissue Travel Pack",
        purchase_condition="Beli 1 Gratis 1",
        deal_type="Beli Gratis",
    )
    b = dict(a)
    b.update({
        "offer_text": "Beli 1 Gratis 1, hadiah Wet Wipes Travel Pack",
        "source_url": "https://example.test/promo-b",
        "source_item_url": "https://example.test/item-b",
    })
    assert enhancer._gift_item_signature(a) != enhancer._gift_item_signature(b)
    assert not enhancer._compatible_offers(a, b)
    merged, groups, absorbed = enhancer.merge_equivalent_cross_source_offers([a, b])
    assert len(merged) == 2
    assert groups == 0 and absorbed == 0


def test_duplicate_review_rows_show_priority_and_sort_highest_first():
    # Same product and compatible offer, but date evidence is missing; this is a
    # legitimate high-priority review candidate rather than an offer conflict.
    exact_a = _base_row(
        product_text="Sunlight Jeruk 755 ml",
        offer_text="Harga Promo Rp16.900",
        promo_price=16900,
        valid_from=None,
        valid_until=None,
        source_url="https://example.test/a",
        source_item_url="https://example.test/item-a",
    )
    exact_b = dict(exact_a, source_url="https://example.test/b", source_item_url="https://example.test/item-b")
    similar = dict(
        exact_a,
        product_text="Sunlight Jeruk Lime 755 ml",
        source_url="https://example.test/c",
        source_item_url="https://example.test/item-c",
        promo_price=14900,
        offer_text="Harga Promo Rp14.900",
    )
    candidates = enhancer.build_possible_duplicate_rows([exact_a, exact_b, similar])
    assert candidates
    assert all(row.get("Prioritas Review") in {"Tinggi", "Sedang", "Rendah"} for row in candidates)
    priority_rank = {"Tinggi": 0, "Sedang": 1, "Rendah": 2}
    ranks = [priority_rank[row["Prioritas Review"]] for row in candidates]
    assert ranks == sorted(ranks)
    assert candidates[0]["Prioritas Review"] == "Tinggi"
    assert "Prioritas Review" in enhancer.POSSIBLE_DUPLICATE_FIELDS


def test_different_purchase_quantity_tiers_are_not_high_priority_duplicate_merges():
    buy_five = _base_row(
        product_text="Yummy Choice Fried Chicken",
        offer_text="Beli 5 diskon Rp10.000",
        purchase_condition="Beli 5 pcs diskon Rp10.000",
        deal_type="Beli Banyak Lebih Hemat",
        promo_price=None,
        normal_price=None,
        valid_from=None,
        valid_until=None,
        source_url="https://example.test/yummy-a",
        source_item_url="https://example.test/yummy-item-a",
    )
    buy_ten = dict(
        buy_five,
        offer_text="Beli 10 diskon Rp24.000",
        purchase_condition="Beli 10 pcs diskon Rp24.000",
        source_url="https://example.test/yummy-b",
        source_item_url="https://example.test/yummy-item-b",
    )
    candidates = enhancer.build_possible_duplicate_rows([buy_five, buy_ten])
    assert len(candidates) == 1
    pair = candidates[0]
    assert pair["Prioritas Review"] != "Tinggi"
    assert "Pertahankan terpisah" in pair["Tindakan"]
    assert "tingkat pembelian berbeda" in pair["Tindakan"]
