from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

ENGINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_DIR))

import promo_free_price_finder as free_finder


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200, headers: dict | None = None):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}


class FakeSession:
    def __init__(self, search_html: str, page_html: str, search_status: int = 200):
        self.search_html = search_html
        self.page_html = page_html
        self.search_status = search_status
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url == free_finder.SEARCH_URL:
            return FakeResponse(self.search_html, self.search_status)
        return FakeResponse(self.page_html)


def _search_html(url="https://shop.example/sunlight-jeruk-755-ml"):
    return f'''<html><div class="result"><a class="result__a" href="{url}">Sunlight Jeruk 755 ml Harga</a></div></html>'''


def _page_html(product="Sunlight Jeruk 755 ml", price="Rp22.900"):
    return f'''<html><head><title>{product} | Toko Contoh</title></head>
    <body><h1>{product}</h1><div>Harga normal {price} untuk {product}. Stok tersedia.</div></body></html>'''


class FallbackSession:
    """Simulate DuckDuckGo's HTTP 202 challenge and a valid Bing RSS fallback."""
    def __init__(self, page_html: str):
        self.page_html = page_html
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url == free_finder.SEARCH_URL:
            return FakeResponse("<html><body>challenge page</body></html>", 202)
        if url == free_finder.BING_RSS_URL:
            return FakeResponse(
                "<?xml version='1.0'?><rss version='2.0'><channel>"
                "<item><title>Sunlight Jeruk 755 ml</title>"
                "<link>https://shop.example/sunlight-jeruk-755-ml</link>"
                "<description>Harga Sunlight Jeruk 755 ml</description></item>"
                "</channel></rss>", 200,
                headers={"Content-Type": "application/rss+xml"},
            )
        return FakeResponse(self.page_html)


def test_free_finder_falls_back_to_bing_rss_when_duckduckgo_returns_202(monkeypatch):
    monkeypatch.setattr(free_finder.shared, "_is_safe_public_url", lambda url: True)
    session = FallbackSession(_page_html())
    result = free_finder.search_free_web_for_product(
        "Sunlight Jeruk 755 ml", session=session, delay_seconds=0,
    )
    assert len(result["references"]) == 1
    assert result["references"][0]["market_price"] == "22900"
    assert any(url == free_finder.BING_RSS_URL for url, _ in session.calls)


def test_parse_bing_rss_result_urls_rejects_unsafe_or_malformed_urls(monkeypatch):
    monkeypatch.setattr(free_finder.shared, "_is_safe_public_url", lambda url: url.startswith("https://"))
    rss = """<rss version='2.0'><channel>
    <item><title>Safe</title><link>https://example.com/item</link></item>
    <item><title>Unsafe</title><link>javascript:alert(1)</link></item>
    </channel></rss>"""
    assert free_finder.parse_bing_rss_result_urls(rss) == ["https://example.com/item"]
    assert free_finder.parse_bing_rss_result_urls("not rss") == []


def test_parse_bing_rss_candidates_preserves_snippet_and_unverified_price_hint(monkeypatch):
    monkeypatch.setattr(free_finder.shared, "_is_safe_public_url", lambda url: url.startswith("https://"))
    rss = """<rss version='2.0'><channel>
    <item><title>Sunlight Jeruk 755 ml - Harga Rp22.900</title>
    <link>https://shop.example/sunlight</link>
    <description>Sunlight Jeruk 755 ml tersedia dengan harga Rp22.900.</description></item>
    </channel></rss>"""
    candidates = free_finder.parse_bing_rss_candidates(rss)
    assert len(candidates) == 1
    assert candidates[0]["provider"] == "bing-rss"
    assert "Sunlight Jeruk 755 ml" in candidates[0]["snippet"]
    assert free_finder._price_hint_from_search_result(
        "Sunlight Jeruk 755 ml", candidates[0]["title"], candidates[0]["snippet"],
    ) == 22900


def test_free_finder_verifies_product_price_without_openai_api(monkeypatch):
    monkeypatch.setattr(free_finder.shared, "_is_safe_public_url", lambda url: True)
    session = FakeSession(_search_html(), _page_html())
    result = free_finder.search_free_web_for_product(
        "Sunlight Jeruk 755 ml", session=session, delay_seconds=0,
    )
    assert len(result["references"]) == 1
    reference = result["references"][0]
    assert reference["market_price"] == "22900"
    assert reference["verification_method"] == "page_price_verified"
    assert reference["price_kind"] == "regular"
    assert "Sunlight Jeruk 755 ml" in reference["evidence_text"]
    assert not any("api.openai.com" in url for url, _ in session.calls)


def test_free_finder_rejects_wrong_product_variant(monkeypatch):
    monkeypatch.setattr(free_finder.shared, "_is_safe_public_url", lambda url: True)
    session = FakeSession(_search_html(), _page_html("Sunlight Lemon 755 ml", "Rp19.900"))
    result = free_finder.search_free_web_for_product(
        "Sunlight Jeruk 755 ml", session=session, delay_seconds=0,
    )
    assert result["references"] == []
    assert result["candidates_rejected_by_validation"] >= 1


def test_free_finder_keeps_unverified_candidates_without_promoting_them(monkeypatch):
    monkeypatch.setattr(free_finder.shared, "_is_safe_public_url", lambda url: True)
    rss = """<rss version='2.0'><channel>
    <item><title>Sunlight Jeruk 755 ml - harga Rp22.900</title>
    <link>https://shop.example/sunlight</link>
    <description>Sunlight Jeruk 755 ml harga Rp22.900.</description></item>
    <item><title>Info Sunlight Jeruk 755 ml</title>
    <link>https://catalog.example/sunlight</link>
    <description>Sunlight Jeruk 755 ml, lihat halaman untuk detail harga.</description></item>
    <item><title>Produk terkait</title><link>https://other.example/item</link>
    <description>Informasi produk terkait.</description></item>
    </channel></rss>"""

    class CandidateOnlySession:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            if url == free_finder.SEARCH_URL:
                return FakeResponse("<html>challenge captcha</html>", 202)
            if url == free_finder.BING_RSS_URL:
                return FakeResponse(rss, 200, headers={"Content-Type": "application/rss+xml"})
            # Page lacks a sufficiently matched price; it should remain a lead only.
            return FakeResponse("<html><head><title>Sunlight catalog</title></head><body>See products. Rp22.900</body></html>")

    result = free_finder.search_free_web_for_product(
        "Sunlight Jeruk 755 ml", session=CandidateOnlySession(), delay_seconds=0,
    )
    assert result["references"] == []
    assert result["candidate_page_count"] == 3
    assert len(result["candidate_records"]) == 3
    assert result["candidate_records"][0]["candidate_status"] == "needs_manual_review"
    assert result["candidate_records"][0]["candidate_price_hint"] == "22900"
    assert result["candidate_records"][2]["candidate_status"] == "not_fetched_for_speed"


def test_candidate_database_is_separate_from_verified_market_reference_database(tmp_path):
    candidate_db = tmp_path / "candidate.csv"
    verified_db = tmp_path / "market.csv"
    row = free_finder._candidate_record(
        "Sunlight Jeruk 755 ml", "sunlight-jeruk-755", "Yogya", 12500,
        {"url": "https://shop.example/item", "title": "Sunlight Jeruk 755 ml Rp22.900",
         "snippet": "Sunlight Jeruk 755 ml harga Rp22.900", "provider": "bing-rss"},
        "2026-10-25", "needs_manual_review", "test candidate",
    )
    summary = free_finder.upsert_candidate_database(candidate_db, [row])
    assert summary["records_after"] == 1
    loaded = free_finder.load_candidate_database(candidate_db)
    assert loaded[0]["candidate_price_hint"] == "22900"
    assert free_finder.shared.load_market_reference_database(verified_db) == []


def test_run_saves_unverified_leads_even_when_no_market_reference_is_verified(tmp_path, monkeypatch):
    input_csv = tmp_path / "products.csv"
    input_csv.write_text("product_text,promo_price\n", encoding="utf-8-sig")
    market_db = tmp_path / "market.csv"
    cache_db = tmp_path / "cache.csv"
    candidate_db = tmp_path / "candidates.csv"
    product_row = {
        "product_text": "Sunlight Jeruk 755 ml", "product_key": "sunlight-jeruk-755",
        "merchant_name": "Yogya", "promo_price": 12500,
    }
    candidate_row = free_finder._candidate_record(
        product_row["product_text"], product_row["product_key"], "Yogya", 12500,
        {"url": "https://shop.example/item", "title": "Sunlight Jeruk 755 ml Rp22.900",
         "snippet": "Sunlight Jeruk 755 ml harga Rp22.900", "provider": "bing-rss"},
        "2026-10-25", "needs_manual_review", "test candidate",
    )
    monkeypatch.setattr(free_finder, "MARKET_CANDIDATES_CSV", candidate_db)
    monkeypatch.setattr(free_finder.shared, "collect_search_candidates", lambda *a, **k: [product_row])
    monkeypatch.setattr(free_finder.shared, "load_search_cache", lambda *a, **k: {})
    monkeypatch.setattr(free_finder.shared, "save_search_cache", lambda *a, **k: None)
    monkeypatch.setattr(free_finder, "search_free_web_for_product", lambda *a, **k: {
        "references": [], "candidate_records": [candidate_row],
        "candidate_page_count": 1, "candidate_price_hint_count": 1,
        "candidates_rejected_by_validation": 1, "search_note": "candidate only",
    })
    summary = free_finder.run_free_price_finder(
        input_path=str(input_csv), database_path=str(market_db), cache_path=str(cache_db), limit=1,
    )
    assert summary["verified_references_found"] == 0
    assert summary["candidate_pages_saved"] == 1
    assert len(free_finder.load_candidate_database(candidate_db)) == 1
    assert free_finder.shared.load_market_reference_database(market_db) == []


def test_free_finder_does_not_cache_or_fake_result_when_search_is_blocked(monkeypatch):
    monkeypatch.setattr(free_finder.shared, "_is_safe_public_url", lambda url: True)
    session = FakeSession(_search_html(), _page_html(), search_status=429)
    with pytest.raises(free_finder.FreePriceSearchError, match="web gratis tidak dapat diakses"):
        free_finder.search_free_web_for_product(
            "Sunlight Jeruk 755 ml", session=session, delay_seconds=0,
        )


def test_free_finder_cli_dry_run_does_not_call_web_or_need_api(tmp_path, monkeypatch):
    input_csv = tmp_path / "products.csv"
    input_csv.write_text(
        "product_text,promo_price,product_validation_status,price_basis,offer_text\n"
        "Produk Uji Detergen Jeruk 900 ml,15000,eligible,offer_price,Harga promo\n",
        encoding="utf-8-sig",
    )
    db_csv = tmp_path / "market.csv"
    cache_csv = tmp_path / "cache.csv"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = free_finder.run_free_price_finder(
        input_path=str(input_csv), database_path=str(db_csv), cache_path=str(cache_csv),
        limit=5, dry_run=True,
    )
    assert result["mode"] == "free_public_web_search_no_paid_ai_api"
    assert result["selected_count"] == 1
    assert len(result["products"]) == 1
