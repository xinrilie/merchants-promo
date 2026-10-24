from __future__ import annotations

import sys

import requests
from pathlib import Path
from types import SimpleNamespace

ENGINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_DIR))

import promo_catalog_all_merchants as momsmoney
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
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return FakeResponse(url, self.status_code)


def test_multisource_collector_stops_source_after_http_429(monkeypatch):
    monkeypatch.setattr(collector.time, "sleep", lambda _seconds: None)
    session = FakeSession(429)

    products, articles, images, request_log = collector.run_source(
        session, "Hemat.id"
    )

    assert len(session.calls) == 1
    assert products == []
    assert images == []
    assert request_log[0]["status"] == "blocked_or_limited"


def test_momsmoney_listing_stops_after_http_429():
    session = FakeSession(429)

    candidates, listing_log = momsmoney.discover_articles(session)

    assert len(session.calls) == 1
    assert candidates == []
    assert listing_log[0]["status"] == "blocked_or_limited"


def test_momsmoney_article_loop_stops_after_first_block(monkeypatch):
    calls = []

    def fake_fetch_article(_session, candidate):
        calls.append(candidate["url"])
        return {
            "requested_url": candidate["url"],
            "status": "blocked_or_limited",
            "note": "HTTP 429",
            "merchant_name": None,
            "product_count": 0,
            "valid_from": None,
            "valid_until": None,
            "promo_status": "unknown",
        }

    monkeypatch.setattr(momsmoney, "fetch_article", fake_fetch_article)
    monkeypatch.setattr(momsmoney.time, "sleep", lambda _seconds: None)

    selected = [
        {"url": "https://example.test/a"},
        {"url": "https://example.test/b"},
        {"url": "https://example.test/c"},
    ]

    checked, stopped = momsmoney.fetch_selected_articles(FakeSession(200), selected)

    assert len(checked) == 1
    assert calls == ["https://example.test/a"]
    assert stopped is True


def test_momsmoney_does_not_open_articles_after_listing_was_blocked(monkeypatch):
    fetch_calls = []

    def fake_fetch_article(_session, candidate):
        fetch_calls.append(candidate["url"])
        return {"status": "ok", "note": None}

    monkeypatch.setattr(momsmoney, "fetch_article", fake_fetch_article)

    selected = [{"url": "https://example.test/manual-candidate"}]
    listing_log = [{"status": "blocked_or_limited", "error": "HTTP 429"}]

    checked, stopped = momsmoney.fetch_selected_articles(
        FakeSession(200), selected, listing_log=listing_log
    )

    assert checked == []
    assert stopped is True
    assert fetch_calls == []


def test_incomplete_momsmoney_scan_preserves_last_good_review_file():
    blocked_listing = [{"status": "blocked_or_limited"}]
    assert momsmoney.should_refresh_review_output(blocked_listing, [], True) is False

    failed_article = [{"status": "ok"}, {"status": "request_error"}]
    assert momsmoney.should_refresh_review_output(
        [{"status": "ok"}], failed_article, False
    ) is False

    complete_scan = [{"status": "ok"}, {"status": "irrelevant_article"}]
    assert momsmoney.should_refresh_review_output(
        [{"status": "ok"}], complete_scan, False
    ) is True


class TimeoutSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        raise requests.Timeout("simulated timeout")


def test_multisource_collector_stops_after_request_timeout(monkeypatch):
    monkeypatch.setattr(collector.time, "sleep", lambda _seconds: None)
    session = TimeoutSession()

    products, articles, images, request_log = collector.run_source(
        session, "Hemat.id"
    )

    assert len(session.calls) == 1
    assert products == []
    assert images == []
    assert request_log[0]["status"] == "request_error"


def test_momsmoney_article_loop_stops_after_request_timeout(monkeypatch):
    calls = []

    def fake_fetch_article(_session, candidate):
        calls.append(candidate["url"])
        return {
            "requested_url": candidate["url"],
            "status": "request_error",
            "note": "timeout",
            "merchant_name": None,
            "product_count": 0,
            "valid_from": None,
            "valid_until": None,
            "promo_status": "unknown",
        }

    monkeypatch.setattr(momsmoney, "fetch_article", fake_fetch_article)
    selected = [
        {"url": "https://example.test/a"},
        {"url": "https://example.test/b"},
    ]

    checked, stopped = momsmoney.fetch_selected_articles(
        FakeSession(200), selected
    )

    assert len(checked) == 1
    assert calls == ["https://example.test/a"]
    assert stopped is True


class RedirectSession:
    def __init__(self, final_url: str):
        self.final_url = final_url
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return FakeResponse(url, 200, final_url=self.final_url)


def test_multisource_collector_rejects_cross_site_redirect(monkeypatch):
    monkeypatch.setattr(collector.time, "sleep", lambda _seconds: None)
    session = RedirectSession("https://unrelated.example.com/promo")

    products, articles, images, request_log = collector.run_source(
        session, "Hemat.id"
    )

    assert len(session.calls) == 1
    assert products == []
    assert images == []
    assert request_log[0]["status"] == "redirected_offsite"
