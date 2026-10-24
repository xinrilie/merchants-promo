from __future__ import annotations

import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_DIR))

import promo_multisource_collector as collector
import promo_catalog_all_merchants as momsmoney
from promo_product_validation import product_candidate_rejection_reason, product_candidate_review_reason


def test_unknown_future_merchants_are_inferred_without_allowlist():
    # Names are intentionally not present in ALIAS_GROUPS. New merchants should
    # still be discoverable from promo/catalog titles and should not be rejected.
    assert collector.infer_merchant_dynamic("Promo All Fruit 23-25 Oktober 2026") == "All Fruit"
    assert collector.infer_merchant_dynamic("Katalog Promo All Sayur Minggu Ini 23-25 Oktober 2026") == "All Sayur"
    assert collector.infer_merchant_dynamic("Promo JSM Fresh Basket 23-25 Oktober 2026") == "Fresh Basket"
    # Known merchants still receive canonical names.
    assert collector.infer_merchant_dynamic("Promo JSM Alfamart 23-25 Oktober 2026") == "Alfamart"
    # The Momsmoney adapter uses the same fallback, not only the aggregator collector.
    assert momsmoney.infer_merchant("Promo All Fruit 23-25 Oktober 2026") == "All Fruit"
    assert momsmoney.infer_merchant("Promo Alfamart 16-31 Oktober 2026") == "Alfamart"


def test_future_merchant_name_is_not_a_product_denylist_entry():
    row = {
        "product_text": "All Fruit Apples Fuji 1 kg",
        "merchant_name": "All Fruit",
        "offer_text": "Harga Promo Rp12.900, Harga Normal Rp15.900",
        "normal_price": 15900,
        "promo_price": 12900,
        "raw_line": "All Fruit Apples Fuji 1 kg — Harga Promo Rp12.900, Harga Normal Rp15.900",
        "source_url": "https://example.test/promo-all-fruit/",
        "source_item_url": "https://example.test/products/apples-fuji-1kg/",
    }
    assert product_candidate_rejection_reason(row, "hemat_card") is None
    assert product_candidate_review_reason(row, "hemat_card") is None

    # A bare candidate matching the merchant is ambiguous, but kept for review,
    # not permanently discarded by a static deny-list.
    ambiguous = {
        "product_text": "All Sayur",
        "merchant_name": "All Sayur",
        "offer_text": "Beli 1 Gratis 1",
        "purchase_condition": "Beli 1 Gratis 1",
        "source_url": "https://example.test/promo-all-sayur/",
    }
    assert product_candidate_rejection_reason(ambiguous, "article") is None
    assert product_candidate_review_reason(ambiguous, "article") == "ambiguous_candidate_matches_merchant_label"


def test_unknown_merchant_can_be_resolved_from_card_context():
    from bs4 import BeautifulSoup

    html = """<div class="product-card">
      <a href="/products/apple-fuji-1kg">Apple Fuji 1 kg — Harga Promo Rp12.900</a>
      <a href="/merchant/all-fruit">All Fruit</a>
    </div>"""
    rows = collector.extract_hemat_cards(
        BeautifulSoup(html, "html.parser"),
        "https://www.hemat.id/katalog-all-fruit/",
    )
    assert rows, "structured product card should be extracted"
    assert rows[0]["merchant_name"] == "All Fruit", rows[0]


def test_directly_priced_short_name_can_be_kept_without_brand_allowlist():
    row = {
        "product_text": "All Fruit",
        "offer_text": "Harga Promo Rp12.900",
        "promo_price": 12900,
        "raw_line": "All Fruit → Harga Promo Rp12.900",
        "source_name": "Katalogpromosi.com",
    }
    assert product_candidate_rejection_reason(row, "article") is None
    assert product_candidate_review_reason(row, "article") is None


def main():
    test_unknown_future_merchants_are_inferred_without_allowlist()
    test_future_merchant_name_is_not_a_product_denylist_entry()
    test_unknown_merchant_can_be_resolved_from_card_context()
    test_directly_priced_short_name_can_be_kept_without_brand_allowlist()
    print("DYNAMIC VALIDATION TESTS PASSED: unseen merchants, item evidence, and review routing.")


if __name__ == "__main__":
    main()
