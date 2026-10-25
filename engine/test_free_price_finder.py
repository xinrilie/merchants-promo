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
