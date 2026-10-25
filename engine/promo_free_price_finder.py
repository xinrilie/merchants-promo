"""Find market-price references without a paid AI/search API.

Uses public DuckDuckGo HTML search results as a best-effort discovery layer,
then parses candidate pages and records only prices that are visibly tied to
an exact product/variant/size. Search providers may throttle or change markup;
this script never fabricates a price when no evidence is found.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date
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
SEARCH_CACHE_CSV = DATA_DIR / "free_price_search_cache.csv"
SEARCH_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
DEFAULT_DELAY_SECONDS = 1.0
DEFAULT_MAX_PAGES_PER_PRODUCT = 6


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


def _search_result_pages(
    product_text: str,
    session: requests.Session,
    timeout: int,
    max_pages: int,
    delay_seconds: float,
) -> list[str]:
    queries = [f'"{product_text}" harga', f'"{product_text}" Rp']
    found: list[str] = []
    seen: set[str] = set()
    successful_searches = 0
    errors: list[str] = []
    for query in queries:
        try:
            response = session.get(
                SEARCH_URL,
                params={"q": query, "kl": "id-id"},
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
            )
        except requests.RequestException as exc:
            errors.append(type(exc).__name__)
            continue
        if response.status_code in {403, 429, 503}:
            errors.append(f"HTTP {response.status_code}")
            continue
        if response.status_code != 200:
            errors.append(f"HTTP {response.status_code}")
            continue
        successful_searches += 1
        for url in parse_search_result_urls(response.text, limit=max_pages):
            if url not in seen:
                found.append(url)
                seen.add(url)
            if len(found) >= max_pages:
                break
        if len(found) >= max_pages:
            break
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    if successful_searches == 0:
        raise FreePriceSearchError(
            "Pencarian web gratis tidak dapat diakses (" + ", ".join(errors or ["tidak ada hasil HTTP 200"]) + "). "
            "Coba lagi nanti; hasil kosong tidak disimpan ke cache."
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
    timeout: int = 15,
    max_pages: int = DEFAULT_MAX_PAGES_PER_PRODUCT,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    session: requests.Session | None = None,
) -> dict:
    """Discover public pages without a paid AI API; verify product+price locally."""
    product_text = shared.clean_product_name(product_text)
    if not product_text:
        raise FreePriceSearchError("Nama produk kosong")
    client = session or requests.Session()
    urls = _search_result_pages(product_text, client, timeout, max_pages, delay_seconds)
    references: list[dict] = []
    rejected = 0
    visited_domains: set[str] = set()
    for url in urls:
        page = _fetch_candidate_page(url, client, timeout)
        if page is None:
            rejected += 1
            continue
        final_url, soup = page
        domain = (urlparse(final_url).hostname or "").lower().removeprefix("www.")
        if not domain or domain in visited_domains:
            continue
        candidates = _price_candidates_on_page(product_text, soup)
        if not candidates:
            rejected += 1
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
    return {
        "references": references,
        "search_note": (
            f"Pencarian web gratis: {len(urls)} halaman kandidat; "
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
        status = "verified_sources_found" if found else "no_verified_sources"
        cache[pkey] = {
            "product_key": pkey, "product_text": product, "searched_at": today.isoformat(),
            "search_status": status, "verified_source_count": str(len(found)),
            "note": result.get("search_note") or f"{result.get('candidates_rejected_by_validation', 0)} kandidat ditolak validator halaman",
        }
        results.append({
            "product_text": product, "status": status, "verified_source_count": len(found),
            "rejected_candidate_count": result.get("candidates_rejected_by_validation", 0),
            "note": result.get("search_note", ""),
        })
        print(f"  Terverifikasi: {len(found)} sumber", flush=True)
        shared.save_search_cache(cache, cache_path)
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    db_summary = shared.upsert_market_reference_database(database_path, all_found)
    shared.save_search_cache(cache, cache_path)
    summary = {
        "mode": "free_public_web_search_no_paid_ai_api",
        "input_path": str(source_path), "candidates_found": len(candidates), "searched": len(selected),
        "verified_references_found": len(all_found), "search_errors": search_errors,
        "database": db_summary, "results": results,
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
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="Jeda antar request (detik; default 1.0)")
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
