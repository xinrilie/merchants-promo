from __future__ import annotations

import sys
from pathlib import Path

import requests

ENGINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_DIR))

import promo_multisource_collector as collector


class FakeResponse:
    def __init__(self, url: str, status_code: int, final_url: str | None = None):
        self.url = final_url or url
        self.status_code = status_code
        self.headers = {"Content-Type": "text/html"}
        self.text = "<html><body>test</body></html>"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, status_code: int = 200, final_url: str | None = None):
        self.status_code = status_code
        self.final_url = final_url
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return FakeResponse(url, self.status_code, self.final_url)


class TimeoutSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        raise requests.Timeout("simulated timeout")


def test_collector_stops_source_after_http_429(monkeypatch):
    monkeypatch.setattr(collector.time, "sleep", lambda _seconds: None)
    session = FakeSession(429)
    products, articles, images, request_log = collector.run_source(session, "Hemat.id")
    assert len(session.calls) == 1
    assert products == []
    assert images == []
    assert request_log[0]["status"] == "blocked_or_limited"


def test_collector_stops_after_request_timeout(monkeypatch):
    monkeypatch.setattr(collector.time, "sleep", lambda _seconds: None)
    session = TimeoutSession()
    products, articles, images, request_log = collector.run_source(session, "Hemat.id")
    assert len(session.calls) == 1
    assert products == []
    assert images == []
    assert request_log[0]["status"] == "request_error"


def test_collector_rejects_cross_site_redirect(monkeypatch):
    monkeypatch.setattr(collector.time, "sleep", lambda _seconds: None)
    session = FakeSession(200, "https://unrelated.example.net/redirect")
    products, articles, images, request_log = collector.run_source(session, "Hemat.id")
    assert len(session.calls) == 1
    assert products == []
    assert request_log[0]["status"] == "redirected_offsite"



def test_article_404_is_url_level_failure_and_other_articles_continue(monkeypatch):
    from bs4 import BeautifulSoup

    listing_url = "https://katalogpromosi.com/"
    dead_url = "https://katalogpromosi.com/promo-deleted/"
    good_url = "https://katalogpromosi.com/promo-live/"

    class RouteResponse:
        def __init__(self, url, status_code, body=""):
            self.url = url
            self.status_code = status_code
            self.headers = {"Content-Type": "text/html; charset=utf-8"}
            self.text = body

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"HTTP {self.status_code}")

    class RouterSession:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            if url == listing_url:
                body = (
                    '<html><body><a href="/promo-deleted/">Promo yang sudah dihapus</a>'
                    '<a href="/promo-live/">Promo aktif 23-25 Oktober 2026</a></body></html>'
                )
                return RouteResponse(url, 200, body)
            if url == dead_url:
                return RouteResponse(url, 404, "")
            if url == good_url:
                return RouteResponse(url, 200, "<html><body><h1>Promo Produk 23-25 Oktober 2026</h1><p>Penawaran</p></body></html>")
            raise AssertionError(f"Unexpected request: {url}")

    monkeypatch.setattr(collector.time, "sleep", lambda _seconds: None)
    monkeypatch.setitem(collector.SOURCE_SEEDS, "Katalogpromosi.com", [listing_url])
    monkeypatch.setattr(
        collector,
        "is_article_candidate",
        lambda _source, target, _title: target in {dead_url, good_url},
    )
    monkeypatch.setattr(collector, "title_from_page", lambda _soup, fallback="": fallback or "Promo Produk 23-25 Oktober 2026")
    monkeypatch.setattr(collector, "find_article_container", lambda soup: soup)
    monkeypatch.setattr(collector, "published_from_page", lambda _soup: None)
    monkeypatch.setattr(collector, "infer_merchant_dynamic", lambda *_args, **_kwargs: "Indomaret")
    monkeypatch.setattr(
        collector,
        "extract_article_rows",
        lambda _container, source_name, _title, final_url, _period, published_at=None: (
            [{"source_name": source_name, "product_text": "Contoh Produk 100 g", "source_url": final_url}]
            if final_url == good_url else []
        ),
    )
    monkeypatch.setattr(collector, "image_rows_from_article", lambda *_args, **_kwargs: [])

    products, articles, images, request_log = collector.run_source(RouterSession(), "Katalogpromosi.com")

    assert [row["source_url"] for row in products] == [good_url]
    assert images == []
    assert len(articles) == 2
    assert any(item.get("url") == dead_url and item.get("status") == "not_found" for item in request_log)
    assert any(item.get("url") == good_url and item.get("status") == "ok" for item in request_log)


def test_fetch_404_is_not_misclassified_as_source_outage():
    session = FakeSession(404)
    soup, info = collector.fetch_page(session, "https://www.hemat.id/article-deleted/", "Hemat.id")
    assert soup is None
    assert info["status"] == "not_found"
    assert "URL tidak ditemukan" in info["error"]
