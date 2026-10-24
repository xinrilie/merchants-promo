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
