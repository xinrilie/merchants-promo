from __future__ import annotations

import json
from datetime import date

import pytest
from bs4 import BeautifulSoup

import promo_gemini_price_finder as gemini


class FakeResponse:
    def __init__(self, body, status_code=200, text=""):
        self._body = body
        self.status_code = status_code
        self.text = text or json.dumps(body, ensure_ascii=False)

    def json(self):
        return self._body


class FakeApiSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def _grounding_response(product="Sunlight Sabun Cuci Piring Lime 910 g", price=12500, evidence=None):
    source_url = "https://shop.example/products/sunlight-lime-910g"
    evidence = evidence or f"{product} dijual dengan harga Rp12.500."
    answer = {
        "references": [{
            "title": product,
            "url": source_url,
            "product_name": product,
            "price_idr": price,
            "currency": "IDR",
            "price_type": "regular",
            "evidence": evidence,
            "seller": "shop.example",
        }]
    }
    return {
        "candidates": [{
            "content": {"parts": [{"text": json.dumps(answer, ensure_ascii=False)}]},
            "groundingMetadata": {
                "groundingChunks": [{"web": {"uri": source_url, "title": product}}],
                "groundingSupports": [],
            },
        }]
    }


def test_gemini_dry_run_does_not_need_api_key_or_network(tmp_path, monkeypatch):
    products = tmp_path / "products.csv"
    products.write_text(
        "product_text,promo_price,product_validation_status,price_basis,offer_text\n"
        "Sunlight Sabun Cuci Piring Lime 910 g,12500,eligible,offer_price,Harga promo\n",
        encoding="utf-8-sig",
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = gemini.run_gemini_price_finder(
        input_path=str(products), database_path=str(tmp_path / "market.csv"),
        cache_path=str(tmp_path / "cache.csv"), limit=5, dry_run=True,
    )
    assert result["api_called"] is False
    assert result["selected_count"] == 1


def test_call_gemini_uses_google_search_tool_and_api_key_header(monkeypatch):
    monkeypatch.setattr(gemini.shared, "_is_safe_public_url", lambda url: True)
    response = _grounding_response()
    session = FakeApiSession(FakeResponse(response))
    result = gemini._call_gemini_search(
        "Sunlight Sabun Cuci Piring Lime 910 g", merchant_name="Yogya", promo_price=12500,
        api_key="test-key", session=session,
    )
    url, request = session.calls[0]
    assert url.endswith("/gemini-2.5-flash:generateContent")
    assert request["headers"]["x-goog-api-key"] == "test-key"
    assert request["json"]["tools"] == [{"google_search": {}}]
    assert result["sources"][0]["url"] == "https://shop.example/products/sunlight-lime-910g"


def test_prepare_candidates_only_accepts_urls_in_google_grounding_citations(monkeypatch):
    monkeypatch.setattr(gemini.shared, "_is_safe_public_url", lambda url: True)
    grounded = _grounding_response()
    result = gemini._call_gemini_search(
        "Sunlight Sabun Cuci Piring Lime 910 g", api_key="test-key",
        session=FakeApiSession(FakeResponse(grounded)),
    )
    parsed_refs = result["parsed"]["references"]
    parsed_refs.append({
        "title": "Fabricated source", "url": "https://fake.example/product",
        "product_name": "Sunlight Sabun Cuci Piring Lime 910 g", "price_idr": 999,
        "currency": "IDR", "evidence": "Harga Rp999",
    })
    candidates = gemini._prepare_grounded_candidates(
        "Sunlight Sabun Cuci Piring Lime 910 g", "sunlight-lime-910g", "Yogya", 12500,
        result, date.today().isoformat(),
    )
    assert len(candidates) == 1
    assert candidates[0]["candidate_url"] == "https://shop.example/products/sunlight-lime-910g"
    assert candidates[0]["candidate_price_hint"] == "12500"


def test_candidate_price_hint_requires_matching_currency_evidence(monkeypatch):
    monkeypatch.setattr(gemini.shared, "_is_safe_public_url", lambda url: True)
    response = _grounding_response(price=12900, evidence="Sunlight Sabun Cuci Piring Lime 910 g; harga sekitar belasan ribu.")
    parsed = gemini._call_gemini_search(
        "Sunlight Sabun Cuci Piring Lime 910 g", api_key="test-key",
        session=FakeApiSession(FakeResponse(response)),
    )
    candidates = gemini._prepare_grounded_candidates(
        "Sunlight Sabun Cuci Piring Lime 910 g", "sunlight-lime-910g", "Yogya", 12500,
        parsed, date.today().isoformat(),
    )
    assert candidates[0]["candidate_price_hint"] == ""
    assert candidates[0]["candidate_status"] == "gemini_grounded_needs_page_check"


def test_gemini_cannot_promote_price_without_page_validation(monkeypatch):
    monkeypatch.setattr(gemini.shared, "_is_safe_public_url", lambda url: True)
    response = _grounding_response()
    result = gemini._call_gemini_search(
        "Sunlight Sabun Cuci Piring Lime 910 g", api_key="test-key",
        session=FakeApiSession(FakeResponse(response)),
    )
    candidates = gemini._prepare_grounded_candidates(
        "Sunlight Sabun Cuci Piring Lime 910 g", "sunlight-lime-910g", "Yogya", 12500,
        result, date.today().isoformat(),
    )
    assert candidates[0]["candidate_price_hint"] == "12500"
    assert candidates[0]["candidate_status"] == "gemini_grounded_needs_page_check"
    # The preparation stage only yields candidates. Verification and database
    # promotion are a separate step that requires an actual source page.


def test_verify_page_requires_exact_product_and_matching_price(monkeypatch):
    html = """<html><head><title>Sunlight Sabun Cuci Piring Lime 910 g</title></head>
    <body><h1>Sunlight Sabun Cuci Piring Lime 910 g</h1><p>Harga Rp12.500</p></body></html>"""
    soup = BeautifulSoup(html, "html.parser")
    monkeypatch.setattr(gemini.shared, "_is_safe_public_url", lambda url: True)
    monkeypatch.setattr(gemini.free_finder, "_fetch_candidate_page", lambda url, session, timeout: (url, soup))
    candidate = {
        "candidate_url": "https://shop.example/products/sunlight-lime-910g",
        "candidate_price_hint": "12500",
    }
    reference = gemini._verify_candidate_page("Sunlight Sabun Cuci Piring Lime 910 g", candidate, 5)
    assert reference is not None
    assert reference["market_price"] == "12500"
    assert reference["verification_method"] == "page_price_verified"


def test_gemini_api_quota_error_stops_with_clear_message():
    session = FakeApiSession(FakeResponse({"error": {"message": "quota exceeded"}}, status_code=429, text="quota exceeded"))
    with pytest.raises(gemini.GeminiPriceSearchError, match="Kuota/rate limit Gemini"):
        gemini._call_gemini_search("Sunlight Sabun Cuci Piring Lime 910 g", api_key="test-key", session=session)


def test_invalid_url_from_model_is_never_added_without_grounding_chunk(monkeypatch):
    monkeypatch.setattr(gemini.shared, "_is_safe_public_url", lambda url: True)
    response = _grounding_response()
    parsed = gemini._call_gemini_search(
        "Sunlight Sabun Cuci Piring Lime 910 g", api_key="test-key",
        session=FakeApiSession(FakeResponse(response)),
    )
    source_url = parsed["sources"][0]["url"]
    # Even an added model URL will not create a new candidate without Google grounding.
    parsed["parsed"]["references"].append({
        "title": "Other SKU", "url": "https://elsewhere.example/wrong", 
        "product_name": "Sunlight Lemon 910 g", "price_idr": 17000,
        "currency": "IDR", "evidence": "Sunlight Lemon 910 g Rp17.000",
    })
    candidates = gemini._prepare_grounded_candidates(
        "Sunlight Sabun Cuci Piring Lime 910 g", "sunlight-lime-910g", "Yogya", 12500,
        parsed, date.today().isoformat(),
    )
    assert [row["candidate_url"] for row in candidates] == [source_url]
