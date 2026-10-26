from datetime import date
import csv

from promo_market_prices import (
    MARKET_REFERENCE_FIELDS, _normalize_record, compare_market_prices,
    parse_idr_price, upsert_market_reference_database, load_market_reference_database,
)
from promo_names import product_name_size_signature, product_name_variant_signature
from promo_price_reference import identity_key


def _ref(name, price, domain, day="2026-10-24"):
    return _normalize_record({
        "product_text": name,
        "product_key": identity_key(name),
        "size_signature": "|".join(product_name_size_signature(name)),
        "variant_signature": "|".join(sorted(product_name_variant_signature(name))),
        "market_price": str(price),
        "observed_at": day,
        "source_name": domain,
        "source_domain": domain,
        "source_url": f"https://{domain}/product/sunlight",
        "evidence_text": f"Sunlight Lime 910 ml harga Rp{price}",
        "page_title": "Sunlight Lime 910 ml",
        "price_kind": "market",
        "source_kind": "manual_user_checked",
        "verification_method": "page_price_verified",
        "confidence": "manual_checked",
    })


def test_parse_idr_prices():
    assert parse_idr_price("Rp 31.500") == 31500
    assert parse_idr_price("16,900") == 16900
    assert parse_idr_price(0) is None


def test_market_db_keeps_multiple_products_on_same_category_url(tmp_path):
    path = tmp_path / "market.csv"
    one = _ref("Sunlight Lime 910 ml", 16900, "toko.example")
    two = _ref("Merries Pants Good Skin M32 32 pcs", 61500, "toko.example")
    shared_url = "https://toko.example/collections/promo"
    one["source_url"] = two["source_url"] = shared_url
    summary = upsert_market_reference_database(path, [one, two])
    loaded = load_market_reference_database(path)
    assert summary["records_added"] == 2
    assert len(loaded) == 2


def test_market_median_requires_two_independent_domains():
    name = "Sunlight Lime 910 ml"
    rows = [{"product_text": name, "promo_price": 15000, "product_validation_status": "eligible"}]
    refs = [_ref(name, 18000, "shop-one.example"), _ref(name, 20000, "shop-two.example")]
    summary = compare_market_prices(rows, refs, today=date(2026, 10, 25))
    assert summary["rows_with_market_reference"] == 1
    assert rows[0]["market_reference_price"] == 19000
    assert rows[0]["market_reference_source_count"] == 2
    assert rows[0]["market_reference_status"] == "below_market_median"
    assert rows[0]["market_reference_savings"] == 4000


def test_unverified_estimate_is_not_accepted_into_market_database(tmp_path):
    path = tmp_path / "market.csv"
    unverified = _ref("Sunlight Lime 910 ml", 16900, "shop.example")
    unverified["verification_method"] = "estimate_only"
    no_evidence = _ref("Merries Pants Good Skin M32 32 pcs", 61500, "shop.example")
    no_evidence["evidence_text"] = ""
    summary = upsert_market_reference_database(path, [unverified, no_evidence])
    assert summary["records_added"] == 0
    assert load_market_reference_database(path) == []


def test_market_median_needs_independent_domains_and_does_not_change_official_discount():
    name = "Sunlight Lime 910 ml"
    product = {"product_text": name, "merchant_name": "Indomaret", "promo_price": 15000, "normal_price": None, "discount_percent": None, "product_validation_status": "eligible"}
    refs = [_ref(name, 18000, "shop-one.example"), _ref(name, 20000, "shop-two.example"), _ref(name, 21000, "shop-one.example", day="2026-10-23")]
    compare_market_prices([product], refs, today=date(2026, 10, 25))
    assert product["market_reference_price"] == 19000
    assert product["market_reference_source_count"] == 2
    assert product["market_reference_savings"] == 4000
    assert product["discount_percent"] is None


def test_one_domain_is_displayed_but_not_used_to_claim_potential_savings():
    name = "Sunlight Lime 910 ml"
    product = {"product_text": name, "promo_price": 15000, "product_validation_status": "eligible"}
    summary = compare_market_prices([product], [_ref(name, 18000, "shop-one.example")], today=date(2026, 10, 25))
    assert summary["single_source_only"] == 1
    assert product["market_reference_status"] == "single_source_only"
    assert product.get("market_reference_below_median_percent") is None


def test_stale_market_references_are_not_used():
    name = "Sunlight Lime 910 ml"
    product = {"product_text": name, "promo_price": 15000, "product_validation_status": "eligible"}
    refs = [_ref(name, 18000, "shop-one.example", day="2026-09-01"), _ref(name, 20000, "shop-two.example", day="2026-09-01")]
    summary = compare_market_prices([product], refs, today=date(2026, 10, 25), max_age_days=14)
    assert summary["no_market_reference"] == 1
    assert product["market_reference_status"] == "no_market_reference"
