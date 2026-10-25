from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_DIR))

import promo_ai_price_finder as finder
from promo_price_reference import identity_key
from promo_names import product_name_size_signature, product_name_variant_signature

TODAY = date(2026, 10, 25)


def _market_record(product="Sunlight Jeruk 755 ml", domain="shop-a.example", price=22900, day="2026-10-25", **overrides):
    row = {
        "product_text": product,
        "product_key": identity_key(product),
        "size_signature": "|".join(product_name_size_signature(product)),
        "variant_signature": "|".join(sorted(product_name_variant_signature(product))),
        "market_price": str(price),
        "observed_at": day,
        "source_name": domain,
        "source_domain": domain,
        "source_url": f"https://{domain}/sunlight-jeruk-755-ml",
        "evidence_text": f"Harga Rp{price:,}".replace(",", "."),
        "page_title": product,
        "price_kind": "regular",
        "source_kind": "retailer",
        "verification_method": "page_price_verified",
        "confidence": "medium",
    }
    row.update(overrides)
    return row


def test_parse_idr_price_handles_indonesian_currency_format():
    assert finder.parse_idr_price("Rp22.900") == 22900
    assert finder.parse_idr_price("IDR 1.299.000") == 1299000
    assert finder.parse_idr_price("22,900") == 22900
    assert finder.parse_idr_price("gratis") is None
    assert finder.parse_idr_price(-100) is None


def test_same_product_match_requires_size_and_variant_to_match():
    assert finder.same_product_variant("Sunlight Jeruk 755 ml", "Sunlight Jeruk 755 ml")
    assert finder.same_product_variant("Sunlight Jeruk 755 ml", "Sunlight Jeruk 755ml")
    assert not finder.same_product_variant("Sunlight Jeruk 755 ml", "Sunlight Jeruk 650 ml")
    assert not finder.same_product_variant("Susu UHT Full Cream 1 L", "Susu UHT Cokelat 1 L")
    assert not finder.same_product_variant("Sunlight Jeruk 755 ml", "Sunlight Lemon 755 ml")
    assert not finder.same_product_variant("Sunlight Jeruk 755 ml", "Sunlight Jeruk Nipis 755 ml")
    assert finder.same_product_variant("Sovia Minyak Goreng 1800 ml; 1.8 l", "Sovia Minyak Goreng 1.8 L")
    assert finder.same_product_variant("Pasti Hemat Facial Tissue 1000 sheet", "Pasti Hemat Facial Tissue 1000 sheets")
    assert finder._has_multiple_size_variants("Merries Pants Good Skin M32, Xxl32, M30, S32, L30+2 32 pcs")
    assert finder._is_ambiguous_product_label("Biokul Greek Yogurt Cup Semua Varian 473 g")
    assert finder._is_ambiguous_product_label("Mallow Puff Rasa Stroberi / Rasa Cokelat 12.5 g")
    assert finder._is_ambiguous_product_label("So Klin Liquid Detergent 1 kg; 1.45 kg")
    assert not finder._is_ambiguous_product_label("Sovia Minyak Goreng 1800 ml; 1.8 l")


class _FakeResponse:
    def __init__(self, html: str, status=200, headers=None):
        self.content = html.encode("utf-8")
        self.status_code = status
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self.text = html


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_price_kind_is_classified_from_verified_page_evidence_only():
    assert finder._classify_price_kind_from_evidence("Harga Promo Rp16.900") == "promotional"
    assert finder._classify_price_kind_from_evidence("Harga Normal Rp22.900") == "regular"
    assert finder._classify_price_kind_from_evidence("Rp22.900") == "unknown"


def test_page_price_verification_requires_product_identity_and_price(monkeypatch):
    monkeypatch.setattr(finder, "_is_safe_public_url", lambda _url: True)
    html = "<html><head><title>Sunlight Jeruk 755 ml</title></head><body><h1>Sunlight Jeruk 755 ml</h1><p>Harga normal Rp22.900</p></body></html>"
    result = finder.verify_price_on_source_page(
        "https://shop-a.example/sunlight", "Sunlight Jeruk 755 ml", 22900,
        session=_FakeSession(_FakeResponse(html)),
    )
    assert result["ok"] is True
    assert result["source_domain"] == "shop-a.example"
    assert "22.900" in result["evidence_text"]

    wrong_price = finder.verify_price_on_source_page(
        "https://shop-a.example/sunlight", "Sunlight Jeruk 755 ml", 19900,
        session=_FakeSession(_FakeResponse(html)),
    )
    assert wrong_price["ok"] is False
    assert wrong_price["reason"] == "candidate_price_not_found_on_page"


def test_page_price_verification_rejects_wrong_size_product(monkeypatch):
    monkeypatch.setattr(finder, "_is_safe_public_url", lambda _url: True)
    html = "<html><head><title>Sunlight Jeruk 650 ml</title></head><body><h1>Sunlight Jeruk 650 ml</h1><p>Harga Rp19.900</p></body></html>"
    result = finder.verify_price_on_source_page(
        "https://shop-a.example/sunlight", "Sunlight Jeruk 755 ml", 19900,
        session=_FakeSession(_FakeResponse(html)),
    )
    assert result["ok"] is False
    assert result["reason"] == "product_identity_not_visible_on_page"


def test_market_comparison_uses_independent_domains_and_does_not_change_official_discount():
    product = "Sunlight Jeruk 755 ml"
    row = {
        "product_text": product,
        "merchant_name": "Indomaret",
        "promo_price": 16900,
        "normal_price": None,
        "discount_percent": None,
        "product_validation_status": "eligible",
    }
    refs = [
        _market_record(product, "shop-a.example", 22900),
        _market_record(product, "shop-b.example", 21500),
        # Same domain must not get an additional vote in the median.
        _market_record(product, "shop-a.example", 23500, day="2026-10-24", source_url="https://shop-a.example/another-listing"),
    ]
    summary = finder.compare_market_prices([row], refs, today=TODAY)
    assert summary["below_market_median"] == 1
    assert row["market_reference_source_count"] == 2
    assert row["market_reference_price"] == 22200
    assert row["market_reference_below_median_percent"] == 23.9
    assert row["market_reference_savings"] == 5300
    assert row["market_reference_status"] == "below_market_median"
    assert row["discount_percent"] is None


def test_one_market_domain_is_displayed_but_not_used_to_claim_potential_savings():
    row = {
        "product_text": "Sunlight Jeruk 755 ml", "merchant_name": "Indomaret",
        "promo_price": 16900, "normal_price": None, "discount_percent": None,
        "product_validation_status": "eligible",
    }
    summary = finder.compare_market_prices([row], [_market_record()], today=TODAY)
    assert summary["single_source_only"] == 1
    assert row["market_reference_price"] == 22900
    assert row["market_reference_below_median_percent"] is None
    assert row["market_reference_status"] == "single_source_only"


def test_stale_market_reference_is_not_used():
    row = {
        "product_text": "Sunlight Jeruk 755 ml", "merchant_name": "Indomaret",
        "promo_price": 16900, "normal_price": None, "product_validation_status": "eligible",
    }
    old = [
        _market_record("Sunlight Jeruk 755 ml", "shop-a.example", 22900, day="2026-09-01"),
        _market_record("Sunlight Jeruk 755 ml", "shop-b.example", 21500, day="2026-09-01"),
    ]
    summary = finder.compare_market_prices([row], old, today=TODAY, max_age_days=14)
    assert summary["no_market_reference"] == 1
    assert row["market_reference_status"] == "no_market_reference"


def test_market_reference_database_upsert_requires_verified_source_price(tmp_path):
    path = tmp_path / "product_market_price_reference.csv"
    good = _market_record()
    bad = _market_record(source_url="https://shop-a.example/sunlight-2", verification_method="ai_assertion_only")
    summary = finder.upsert_market_reference_database(path, [good, bad])
    loaded = finder.load_market_reference_database(path)
    assert summary["records_added"] == 1
    assert len(loaded) == 1
    assert loaded[0]["market_price"] == "22900"


def test_candidate_collection_only_picks_products_with_price_but_no_normal_price():
    rows = [
        {"product_text": "Sunlight Jeruk 755 ml", "promo_price": "16900", "normal_price": "", "product_validation_status": "eligible", "price_basis": "offer_price", "offer_text": "Harga Promo Rp16.900"},
        {"product_text": "Sunlight Lemon 755 ml", "promo_price": "16900", "normal_price": "22000", "product_validation_status": "eligible", "price_basis": "offer_price"},
        {"product_text": "Produk Bundle A dan B", "promo_price": "20000", "normal_price": "", "product_validation_status": "eligible", "price_basis": "offer_price", "offer_text": "Beli 2 gratis 1"},
        {"product_text": "Merries Pants Good Skin M32, Xxl32, M30, S32, L30+2 32 pcs", "promo_price": "58900", "normal_price": "", "product_validation_status": "eligible", "price_basis": "offer_price", "offer_text": "Harga Promo Rp58.900"},
    ]
    selected = finder.collect_search_candidates(rows, {}, [], TODAY)
    assert [row["product_text"] for row in selected] == ["Sunlight Jeruk 755 ml"]


def test_cache_avoids_repeating_recent_no_result_search(tmp_path):
    product = "Sunlight Jeruk 755 ml"
    cache = {
        identity_key(product): {
            "product_key": identity_key(product), "searched_at": TODAY.isoformat(),
            "search_status": "no_verified_sources", "verified_source_count": "0",
        }
    }
    row = {"product_text": product, "promo_price": "16900", "normal_price": "", "product_validation_status": "eligible", "price_basis": "offer_price"}
    assert finder.collect_search_candidates([row], cache, [], TODAY) == []
    assert len(finder.collect_search_candidates([row], cache, [], TODAY, force=True)) == 1


def test_page_price_verification_rejects_price_unrelated_to_product(monkeypatch):
    monkeypatch.setattr(finder, "_is_safe_public_url", lambda _url: True)
    filler = "Informasi umum toko dan rekomendasi produk. " * 20
    html = (
        "<html><head><title>Sunlight Jeruk 755 ml</title></head><body>"
        "<h1>Sunlight Jeruk 755 ml</h1><p>Deskripsi produk.</p>"
        f"<section>{filler}<h2>Produk lain</h2><p>Sabun cuci lain Harga Rp22.900</p></section>"
        "</body></html>"
    )
    result = finder.verify_price_on_source_page(
        "https://shop-a.example/sunlight", "Sunlight Jeruk 755 ml", 22900,
        session=_FakeSession(_FakeResponse(html)),
    )
    assert result["ok"] is False
    assert result["reason"] == "candidate_price_not_associated_with_product"


def test_jsonld_price_requires_explicit_idr_currency():
    html = '''<html><script type="application/ld+json">{
      "@type":"Product", "name":"Sunlight Jeruk 755 ml",
      "offers":{"@type":"Offer", "price":"22900"}
    }</script></html>'''
    soup = finder.BeautifulSoup(html, "html.parser")
    assert finder._extract_jsonld_prices(soup, "Sunlight Jeruk 755 ml") == []


def test_source_kind_is_derived_from_verified_domain_not_ai_label():
    assert finder._classify_source_kind_from_url("https://www.tokopedia.com/product") == "marketplace"
    assert finder._classify_source_kind_from_url("https://www.katalogpromosi.com/promo") == "aggregator"
    assert finder._classify_source_kind_from_url("https://www.klikindomaret.com/product") == "retailer"
    assert finder._classify_source_kind_from_url("https://unknown-shop.example/product") == "other"
