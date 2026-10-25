"""Find market-price references without a paid AI/search API.

Uses public search results as a best-effort discovery layer. Only prices that
are visibly tied to the exact product/variant/size are promoted to the verified
market database. Search-result snippets and pages that fail strict validation
are retained separately as unverified leads so the user can inspect them in Excel.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

import promo_ai_price_finder as shared

ENGINE_DIR = shared.ENGINE_DIR
ROOT_DIR = shared.ROOT_DIR
DATA_DIR = shared.DATA_DIR
INPUT_PRODUCTS_CSV = shared.INPUT_PRODUCTS_CSV
FALLBACK_PRODUCTS_CSV = shared.FALLBACK_PRODUCTS_CSV
MARKET_REFERENCE_CSV = shared.MARKET_REFERENCE_CSV
# New cache file deliberately ignores earlier runs that cached zero verified
# sources before the candidate-review workflow existed.
SEARCH_CACHE_CSV = DATA_DIR / "free_price_search_cache_v2.csv"
MARKET_CANDIDATES_CSV = DATA_DIR / "product_market_price_candidates.csv"
SEARCH_URL = "https://html.duckduckgo.com/html/"
# Bing RSS is an unofficial, no-key fallback. DuckDuckGo may return HTTP 202
# anti-bot pages to ordinary requests clients even when a browser works.
BING_RSS_URL = "https://www.bing.com/search"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
# The free endpoints are best-effort. Search fewer candidates and verify only a
# small number of pages so one product cannot stall a long catalog run.
DEFAULT_DELAY_SECONDS = 0.25
DEFAULT_MAX_PAGES_PER_PRODUCT = 4
DEFAULT_MAX_PAGES_TO_VERIFY = 2
DEFAULT_REQUEST_TIMEOUT_SECONDS = 5

CANDIDATE_FIELDS = [
    "product_text", "product_key", "merchant_name", "promo_price",
    "candidate_title", "candidate_url", "candidate_domain", "candidate_snippet",
    "candidate_price_hint", "observed_at", "search_provider", "candidate_status",
    "rejection_reason",
]


class FreePriceSearchError(RuntimeError):
    """Public search engine failed so the result should not be cached as empty."""


def _unwrap_search_url(href: str) -> str:
    href = str(href or "").strip()
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if (parsed.hostname or "").lower().endswith("duckduckgo.com"):
        query = parse_qs(parsed.query)
        wrapped = query.get("uddg", [""])[0]
        if wrapped:
            href = unquote(wrapped)
    return href


def parse_search_result_urls(html: str, limit: int = DEFAULT_MAX_PAGES_PER_PRODUCT) -> list[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for anchor in soup.select("a.result__a, a[data-testid='result-title-a']"):
        url = _unwrap_search_url(anchor.get("href", ""))
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        if not shared._is_safe_public_url(url):
            continue
        # Normalize fragments and common tracking parameters so the same page
        # isn't crawled several times from different snippets.
        normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, ""))
        if normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)
        if len(urls) >= max(0, int(limit)):
            break
    return urls


def parse_duckduckgo_candidates(html_text: str, limit: int = DEFAULT_MAX_PAGES_PER_PRODUCT) -> list[dict]:
    """Extract result titles/snippets as review leads, not as verified prices."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    results: list[dict] = []
    seen: set[str] = set()
    for anchor in soup.select("a.result__a, a[data-testid='result-title-a']"):
        url = _unwrap_search_url(anchor.get("href", ""))
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not shared._is_safe_public_url(url):
            continue
        normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, ""))
        if normalized in seen:
            continue
        seen.add(normalized)
        result_container = anchor.find_parent(class_=re.compile(r"result"))
        snippet_node = result_container.select_one(".result__snippet, [data-testid='result-snippet']") if result_container else None
        results.append({
            "url": normalized,
            "title": anchor.get_text(" ", strip=True),
            "snippet": snippet_node.get_text(" ", strip=True) if snippet_node else "",
            "provider": "duckduckgo",
        })
        if len(results) >= max(0, int(limit)):
            break
    return results


def parse_bing_rss_result_urls(xml_text: str, limit: int = DEFAULT_MAX_PAGES_PER_PRODUCT) -> list[str]:
    """Extract public page URLs from Bing's RSS-formatted web search response."""
    urls: list[str] = []
    seen: set[str] = set()
    try:
        root = ET.fromstring(xml_text or "")
    except ET.ParseError:
        return urls
    for item in root.findall(".//item"):
        link = (item.findtext("link") or "").strip()
        url = _unwrap_search_url(link)
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        if not shared._is_safe_public_url(url):
            continue
        normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, ""))
        if normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)
        if len(urls) >= max(0, int(limit)):
            break
    return urls


def parse_bing_rss_candidates(xml_text: str, limit: int = DEFAULT_MAX_PAGES_PER_PRODUCT) -> list[dict]:
    """Keep Bing RSS titles/snippets; they remain unverified until page check."""
    results: list[dict] = []
    seen: set[str] = set()
    try:
        root = ET.fromstring(xml_text or "")
    except ET.ParseError:
        return results
    for item in root.findall(".//item"):
        url = _unwrap_search_url(item.findtext("link") or "")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not shared._is_safe_public_url(url):
            continue
        normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, ""))
        if normalized in seen:
            continue
        seen.add(normalized)
        description_raw = item.findtext("description") or ""
        snippet = BeautifulSoup(html.unescape(description_raw), "html.parser").get_text(" ", strip=True)
        results.append({
            "url": normalized,
            "title": BeautifulSoup(item.findtext("title") or "", "html.parser").get_text(" ", strip=True),
            "snippet": snippet,
            "provider": "bing-rss",
        })
        if len(results) >= max(0, int(limit)):
            break
    return results


def _try_search_provider(
    provider: str,
    query: str,
    session: requests.Session,
    timeout: int,
    max_pages: int,
) -> tuple[list[dict], bool, str | None]:
    """Return result leads, usability, and error. A 202 challenge isn't a result."""
    if provider == "duckduckgo":
        url = SEARCH_URL
        params = {"q": query, "kl": "id-id"}
        parser = parse_duckduckgo_candidates
    else:
        url = BING_RSS_URL
        params = {"q": query, "format": "rss", "mkt": "id-ID", "setlang": "id"}
        parser = parse_bing_rss_candidates
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.7,en;q=0.6",
    }
    try:
        response = session.get(url, params=params, timeout=timeout, headers=headers)
    except requests.RequestException as exc:
        return [], False, f"{provider}: {type(exc).__name__}"

    if response.status_code != 200:
        return [], False, f"{provider}: HTTP {response.status_code}"
    body = response.text or ""
    items = parser(body, limit=max_pages)
    if items:
        return items, True, None

    # DuckDuckGo frequently responds with HTTP 202 or an HTTP 200 challenge page.
    # Do not interpret those pages as a legitimate empty search result.
    if provider == "duckduckgo":
        lower_body = body.casefold()
        challenge_markers = ("challenge-form", "anomaly-modal", "bots use duckduckgo", "captcha")
        if any(marker in lower_body for marker in challenge_markers) or not body.strip():
            return [], False, "duckduckgo: halaman challenge/hasil kosong tanpa tautan"
        # A structurally valid page with no results can be a legitimate search miss.
        soup = BeautifulSoup(body, "html.parser")
        has_results_container = bool(soup.select(".result, .results, #links, .no-results"))
        return [], has_results_container, None if has_results_container else "duckduckgo: format hasil tidak dikenali"

    # Empty but valid RSS can mean that the query yielded no public results.
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return [], False, "bing-rss: respons bukan XML/RSS yang valid"
    if root.tag.lower().endswith("rss") or root.find("channel") is not None:
        return [], True, None
    return [], False, "bing-rss: struktur RSS tidak dikenali"


def _search_result_pages(
    product_text: str,
    session: requests.Session,
    timeout: int,
    max_pages: int,
    delay_seconds: float,
) -> list[str]:
    # Keep the query set small; prefer DuckDuckGo, and fall back to Bing RSS if
    # DDG returns a challenge (HTTP 202) or unparseable HTML. Both need no API key.
    return [item["url"] for item in _search_result_candidates(
        product_text, session, timeout, max_pages, delay_seconds,
    )]


def _search_query_name(product_text: str) -> str:
    # Input rows may hold equivalent size spellings separated by semicolons.
    # Search the first canonical name instead of quoting both alternatives.
    return re.split(r"\s*;\s*", str(product_text or ""), maxsplit=1)[0].strip()


def _search_result_candidates(
    product_text: str,
    session: requests.Session,
    timeout: int,
    max_pages: int,
    delay_seconds: float,
) -> list[dict]:
    """Search once, use provider fallback, and retain titles/snippets as leads."""
    clean_query_name = _search_query_name(product_text)
    # One useful query is faster than two near-duplicate quoted queries. A
    # second query is attempted only when both providers return no usable links.
    queries = [f'"{clean_query_name}" harga', f'{clean_query_name} harga Indonesia']
    found: list[dict] = []
    seen: set[str] = set()
    usable_searches = 0
    errors: list[str] = []
    for query in queries:
        for provider in ("duckduckgo", "bing-rss"):
            items, usable, error = _try_search_provider(
                provider, query, session, timeout, max_pages,
            )
            if error:
                errors.append(error)
            if usable:
                usable_searches += 1
            for item in items:
                url = item["url"]
                if url not in seen:
                    found.append(item)
                    seen.add(url)
                if len(found) >= max_pages:
                    break
            if len(found) >= max_pages:
                break
            # If a provider returned any candidates, don't burn time on the
            # second provider/query just to collect redundant pages.
            if usable and items:
                break
            if delay_seconds > 0:
                time.sleep(delay_seconds)
        if len(found) >= max_pages:
            break
        if found:
            break
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    if usable_searches == 0:
        raise FreePriceSearchError(
            "Pencarian web gratis tidak dapat diakses (" + "; ".join(errors or ["tidak ada hasil yang dapat dibaca"]) + "). "
            "DuckDuckGo bisa mengembalikan HTTP 202 karena pembatasan bot; fallback Bing RSS juga tidak tersedia. "
            "Coba lagi nanti. Tidak ada hasil kosong yang disimpan ke cache."
        )
    return found


def _fetch_candidate_page(url: str, session: requests.Session, timeout: int) -> tuple[str, BeautifulSoup] | None:
    current_url = url
    response = None
    for _ in range(4):
        if not shared._is_safe_public_url(current_url):
            return None
        try:
            response = session.get(
                current_url,
                timeout=timeout,
                allow_redirects=False,
                headers={"User-Agent": USER_AGENT},
            )
        except requests.RequestException:
            return None
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location", "")
            if not location:
                return None
            from urllib.parse import urljoin
            current_url = urljoin(current_url, location)
            continue
        break
    if response is None or response.status_code != 200:
        return None
    content_type = str(response.headers.get("Content-Type") or "").lower()
    if content_type and not any(x in content_type for x in ("text/html", "application/xhtml+xml", "application/ld+json")):
        return None
    try:
        soup = BeautifulSoup(response.content, "html.parser")
    except Exception:
        return None
    return current_url, soup


def _price_hint_from_search_result(product_text: str, title: str, snippet: str) -> int | None:
    """Return a search-snippet price hint, never a verified price."""
    combined = " ".join(part for part in (title, snippet) if part).strip()
    if not combined or not shared._product_mentioned_in_page(product_text, combined):
        return None
    for match in re.finditer(r"(?i)\b(?:Rp\.?|IDR)\s*([0-9][0-9.,\s]{0,18})", combined):
        price = shared.parse_idr_price(match.group(1))
        if price is not None and 100 <= price <= 100_000_000:
            return price
    return None


def _candidate_record(
    product_text: str, product_key: str, merchant_name: str, promo_price: int | None,
    item: dict, observed_at: str, status: str, reason: str,
    page_title: str = "", page_snippet: str = "", price_hint: int | None = None,
) -> dict:
    url = str(item.get("url") or "").strip()
    title = str(page_title or item.get("title") or "").strip()
    search_snippet = str(item.get("snippet") or "").strip()
    page_excerpt = str(page_snippet or "").strip()
    if search_snippet and page_excerpt and search_snippet.casefold() != page_excerpt.casefold():
        snippet = f"Cuplikan pencarian: {search_snippet} | Cuplikan halaman: {page_excerpt}"
    else:
        snippet = page_excerpt or search_snippet
    hint = price_hint if price_hint is not None else _price_hint_from_search_result(product_text, title, snippet)
    return {
        "product_text": product_text, "product_key": product_key,
        "merchant_name": merchant_name, "promo_price": str(promo_price or ""),
        "candidate_title": title, "candidate_url": url,
        "candidate_domain": (urlparse(url).hostname or "").lower().removeprefix("www."),
        "candidate_snippet": snippet[:1000], "candidate_price_hint": str(hint or ""),
        "observed_at": observed_at, "search_provider": str(item.get("provider") or "public_web_search"),
        "candidate_status": status, "rejection_reason": reason,
    }


def load_candidate_database(path: str | Path = MARKET_CANDIDATES_CSV) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except (OSError, csv.Error):
        return []


def upsert_candidate_database(path: str | Path, rows: Iterable[dict]) -> dict:
    """Persist public search leads separately; they never feed market medians."""
    path = Path(path)
    old_rows = load_candidate_database(path)
    by_key: dict[tuple[str, str], dict] = {}
    for row in old_rows:
        key = (str(row.get("product_key") or ""), str(row.get("candidate_url") or ""))
        if all(key):
            by_key[key] = {field: str(row.get(field) or "") for field in CANDIDATE_FIELDS}
    added = updated = 0
    for raw in rows:
        row = {field: str(raw.get(field) or "") for field in CANDIDATE_FIELDS}
        if not row["product_key"] or not row["candidate_url"]:
            continue
        key = (row["product_key"], row["candidate_url"])
        if key not in by_key:
            added += 1
        elif by_key[key] != row:
            updated += 1
        by_key[key] = row
    ordered = sorted(by_key.values(), key=lambda row: (row.get("observed_at", ""), row.get("product_text", ""), row.get("candidate_domain", "")))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANDIDATE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ordered)
    tmp.replace(path)
    return {"records_before": len(old_rows), "records_added": added, "records_updated": updated, "records_after": len(ordered), "database_path": str(path)}


def _price_candidates_on_page(product_text: str, soup: BeautifulSoup) -> list[tuple[int, str, str]]:
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    meta_names: list[str] = []
    for attrs in (
        {"property": "og:title"}, {"name": "twitter:title"}, {"itemprop": "name"},
        {"property": "og:description"}, {"name": "description"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            meta_names.append(str(tag["content"]))
    visible_text = soup.get_text(" ", strip=True)
    candidates: list[tuple[int, str, str]] = []

    # Structured product offers are the strongest extraction source.
    for price, evidence in shared._extract_jsonld_prices(soup, product_text):
        if 100 <= price <= 100_000_000:
            candidates.append((price, evidence, title))

    # Otherwise only use currency amounts with nearby product identity evidence.
    for match in re.finditer(r"(?i)\b(?:Rp\.?|IDR)\s*([0-9][0-9.,\s]{0,18})", visible_text):
        price = shared.parse_idr_price(match.group(1))
        if price is None or not 100 <= price <= 100_000_000:
            continue
        start = max(0, match.start() - 320)
        end = min(len(visible_text), match.end() + 320)
        evidence = visible_text[start:end].strip()
        if shared._product_mentioned_in_page(product_text, evidence):
            candidates.append((price, evidence, title))

    # Deduplicate price+evidence. Avoid accepting prices from a page where
    # product identity is only visible in a broad page title or unrelated text.
    output: list[tuple[int, str, str]] = []
    seen: set[tuple[int, str]] = set()
    for price, evidence, page_title in candidates:
        key = (price, re.sub(r"\s+", " ", evidence).casefold()[:260])
        if key in seen:
            continue
        seen.add(key)
        output.append((price, evidence, page_title or (meta_names[0] if meta_names else "")))
    return output


def search_free_web_for_product(
    product_text: str,
    merchant_name: str = "",
    promo_price: int | None = None,
    timeout: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    max_pages: int = DEFAULT_MAX_PAGES_PER_PRODUCT,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    session: requests.Session | None = None,
) -> dict:
    """Discover public pages without a paid AI API; verify product+price locally."""
    product_text = shared.clean_product_name(product_text)
    if not product_text:
        raise FreePriceSearchError("Nama produk kosong")
    supplied_session = session
    client = session or requests.Session()
    result_items = _search_result_candidates(product_text, client, timeout, max_pages, delay_seconds)
    references: list[dict] = []
    candidate_records: list[dict] = []
    rejected = 0
    visited_domains: set[str] = set()
    product_key = shared.identity_key(product_text)
    verify_items = result_items[:DEFAULT_MAX_PAGES_TO_VERIFY]
    def fetch_item(item: dict):
        page_url = str(item.get("url") or "")
        request_timeout = min(max(2, int(timeout)), DEFAULT_REQUEST_TIMEOUT_SECONDS)
        if supplied_session is not None:
            # Injected sessions are typically test doubles and may not be thread-safe.
            page = _fetch_candidate_page(page_url, client, request_timeout)
        else:
            with requests.Session() as page_client:
                page = _fetch_candidate_page(page_url, page_client, request_timeout)
        return page_url, page

    verification_by_url: dict[str, tuple[str, BeautifulSoup] | None] = {}
    # The two candidate pages are independent; fetch them concurrently to avoid
    # serially waiting on slow/blocked retail sites.
    if verify_items:
        with ThreadPoolExecutor(max_workers=min(2, len(verify_items))) as pool:
            for url, page in pool.map(fetch_item, verify_items):
                verification_by_url[url] = page

    for item in result_items:
        url = str(item.get("url") or "")
        if url not in verification_by_url:
            candidate_records.append(_candidate_record(
                product_text, product_key, merchant_name, promo_price, item, date.today().isoformat(),
                "not_fetched_for_speed", "Halaman kandidat disimpan untuk review; verifikasi otomatis dibatasi agar pencarian lebih cepat.",
            ))
            continue
        page = verification_by_url[url]
        if page is None:
            rejected += 1
            candidate_records.append(_candidate_record(
                product_text, product_key, merchant_name, promo_price, item, date.today().isoformat(),
                "page_unavailable_or_blocked", "Halaman sumber tidak dapat dibaca otomatis; URL dan cuplikan pencarian disimpan untuk pemeriksaan manual.",
            ))
            continue
        final_url, soup = page
        domain = (urlparse(final_url).hostname or "").lower().removeprefix("www.")
        if not domain or domain in visited_domains:
            candidate_records.append(_candidate_record(
                product_text, product_key, merchant_name, promo_price, item, date.today().isoformat(),
                "duplicate_domain", "Domain sudah terwakili; tidak dihitung sebagai sumber independen kedua.",
                page_title=soup.title.get_text(" ", strip=True) if soup.title else "",
            ))
            continue
        candidates = _price_candidates_on_page(product_text, soup)
        if not candidates:
            rejected += 1
            page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
            visible = soup.get_text(" ", strip=True)
            candidate_records.append(_candidate_record(
                product_text, product_key, merchant_name, promo_price, item, date.today().isoformat(),
                "needs_manual_review", "Validator belum dapat mengikat harga ke SKU/varian/ukuran dengan cukup kuat. Jangan gunakan sebagai harga terverifikasi.",
                page_title=page_title, page_snippet=visible[:500],
            ))
            continue
        # One page may contain multiple offers. Store at most one distinct price
        # per domain to keep the market median from over-weighting a single site.
        price, evidence, page_title = candidates[0]
        price_kind = shared._classify_price_kind_from_evidence(evidence)
        row = shared._normalize_record({
            "product_text": product_text,
            "product_key": shared.identity_key(product_text),
            "size_signature": "|".join(shared.product_name_size_signature(product_text)),
            "variant_signature": "|".join(sorted(shared.product_name_variant_signature(product_text))),
            "market_price": price,
            "observed_at": date.today().isoformat(),
            "source_name": domain,
            "source_domain": domain,
            "source_url": final_url,
            "evidence_text": evidence,
            "page_title": page_title or domain,
            "price_kind": price_kind,
            "source_kind": shared._classify_source_kind_from_url(final_url),
            "verification_method": "page_price_verified",
            "confidence": "low_to_medium",
        })
        references.append(row)
        visited_domains.add(domain)
        candidate_records.append(_candidate_record(
            product_text, product_key, merchant_name, promo_price, item, date.today().isoformat(),
            "verified_price_source", "Identitas produk dan nominal harga ditemukan pada halaman sumber.",
            page_title=page_title or domain, page_snippet=evidence, price_hint=price,
        ))
    return {
        "references": references,
        "candidate_records": candidate_records,
        "candidate_page_count": len(result_items),
        "candidate_price_hint_count": sum(bool(row.get("candidate_price_hint")) for row in candidate_records),
        "search_note": (
            f"Pencarian web gratis: {len(result_items)} halaman kandidat; "
            f"{len(references)} referensi harga terverifikasi. "
            "Cocokkan kembali sumber dan tanggal sebelum mengambil keputusan belanja."
        ),
        "candidates_rejected_by_validation": rejected,
    }


def run_free_price_finder(
    input_path: str = str(INPUT_PRODUCTS_CSV),
    database_path: str = str(MARKET_REFERENCE_CSV),
    cache_path: str = str(SEARCH_CACHE_CSV),
    limit: int = 5,
    force: bool = False,
    dry_run: bool = False,
    today: date | None = None,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
) -> dict:
    today = today or date.today()
    source_path = __import__("pathlib").Path(input_path)
    if not source_path.exists() and source_path == INPUT_PRODUCTS_CSV:
        source_path = FALLBACK_PRODUCTS_CSV
    products = shared.load_product_rows(source_path)
    refs = shared.load_market_reference_database(database_path)
    normal_refs = shared.load_reference_database(shared.NORMAL_PRICE_REFERENCE_CSV)
    cache = shared.load_search_cache(cache_path)
    candidates = shared.collect_search_candidates(
        products, cache, refs, today, force=force, normal_price_references=normal_refs,
    )
    selected = candidates[:max(0, int(limit))]
    if dry_run:
        return {
            "mode": "free_public_web_search_no_paid_ai_api",
            "input_path": str(source_path), "eligible_unique_products": len(candidates),
            "selected_count": len(selected), "products": [
                {"product_text": row["product_text"], "merchant_name": row.get("merchant_name", ""), "promo_price": row["promo_price"]}
                for row in selected
            ],
        }

    all_found: list[dict] = []
    all_candidate_rows: list[dict] = []
    search_errors = 0
    results: list[dict] = []
    client = requests.Session()
    for index, row in enumerate(selected, start=1):
        product = row["product_text"]
        pkey = row["product_key"]
        print(f"[{index}/{len(selected)}] Cari harga (web gratis): {product}", flush=True)
        try:
            result = search_free_web_for_product(
                product, merchant_name=str(row.get("merchant_name") or ""),
                promo_price=row.get("promo_price"), delay_seconds=delay_seconds, session=client,
            )
        except FreePriceSearchError as exc:
            search_errors += 1
            results.append({"product_text": product, "status": "search_error", "note": str(exc)})
            print(f"  PERINGATAN: {exc}", flush=True)
            # Do not cache provider failures: next run may retry.
            continue
        found = result.get("references") or []
        all_found.extend(found)
        all_candidate_rows.extend(result.get("candidate_records") or [])
        status = "verified_sources_found" if found else "no_verified_sources"
        cache[pkey] = {
            "product_key": pkey, "product_text": product, "searched_at": today.isoformat(),
            "search_status": status, "verified_source_count": str(len(found)),
            "note": result.get("search_note") or f"{result.get('candidates_rejected_by_validation', 0)} kandidat ditolak validator halaman",
        }
        results.append({
            "product_text": product, "status": status, "verified_source_count": len(found),
            "rejected_candidate_count": result.get("candidates_rejected_by_validation", 0),
            "candidate_page_count": result.get("candidate_page_count", 0),
            "candidate_price_hint_count": result.get("candidate_price_hint_count", 0),
            "note": result.get("search_note", ""),
        })
        print(
            f"  Terverifikasi: {len(found)} sumber; kandidat untuk review: "
            f"{result.get('candidate_page_count', 0)} halaman; petunjuk harga: "
            f"{result.get('candidate_price_hint_count', 0)}",
            flush=True,
        )
        shared.save_search_cache(cache, cache_path)
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    db_summary = shared.upsert_market_reference_database(database_path, all_found)
    candidate_summary = upsert_candidate_database(MARKET_CANDIDATES_CSV, all_candidate_rows)
    shared.save_search_cache(cache, cache_path)
    summary = {
        "mode": "free_public_web_search_no_paid_ai_api",
        "input_path": str(source_path), "candidates_found": len(candidates), "searched": len(selected),
        "verified_references_found": len(all_found), "search_errors": search_errors,
        "database": db_summary, "candidate_database": candidate_summary,
        "candidate_pages_saved": len(all_candidate_rows),
        "candidate_price_hints_saved": sum(bool(row.get("candidate_price_hint")) for row in all_candidate_rows),
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def _load_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cari harga pembanding dengan pencarian web publik gratis; tanpa OpenAI API key."
    )
    parser.add_argument("--input", default=str(INPUT_PRODUCTS_CSV), help="CSV produk hasil collector atau katalog final")
    parser.add_argument("--database", default=str(MARKET_REFERENCE_CSV), help="CSV database harga pembanding")
    parser.add_argument("--cache", default=str(SEARCH_CACHE_CSV), help="CSV cache hasil pencarian")
    parser.add_argument("--limit", type=int, default=5, help="Maksimum produk per run (default: 5 untuk membatasi beban pencarian)")
    parser.add_argument("--force", action="store_true", help="Abaikan cache dan referensi pasar terbaru")
    parser.add_argument("--dry-run", action="store_true", help="Tampilkan antrean tanpa mengakses internet")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="Jeda antar request (detik; default 0.25)")
    return parser.parse_args()


def main() -> int:
    args = _load_cli()
    try:
        summary = run_free_price_finder(
            input_path=args.input, database_path=args.database, cache_path=args.cache,
            limit=args.limit, force=args.force, dry_run=args.dry_run, delay_seconds=max(0, args.delay),
        )
    except (FreePriceSearchError, OSError, ValueError) as exc:
        print(f"FREE PRICE FINDER BERHENTI: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
