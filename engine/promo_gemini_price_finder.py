"""Find market-price references with Gemini + Google Search grounding.

Uses the Gemini Developer API's gemini-2.5-flash model and built-in Google
Search tool. A model-reported price is always stored as an unverified candidate
first; it enters the market-reference database only if the cited URL is also
readable and the exact product/price is independently visible on that page.

The script uses `requests` (already required by this project) and needs no
additional SDK package. Set GEMINI_API_KEY in the current shell. Keep the
Google AI Studio project on its Free tier and do not attach billing if you do
not want API charges. Current Google pricing documents up to 500 grounded
prompts/day for Gemini 2.5 Flash/Flash-Lite on the Free tier; actual access and
limits depend on the account/project.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from concurrent.futures import ThreadPoolExecutor

import requests

import promo_ai_price_finder as shared
import promo_free_price_finder as free_finder

ENGINE_DIR = shared.ENGINE_DIR
ROOT_DIR = shared.ROOT_DIR
DATA_DIR = shared.DATA_DIR
INPUT_PRODUCTS_CSV = shared.INPUT_PRODUCTS_CSV
FALLBACK_PRODUCTS_CSV = shared.FALLBACK_PRODUCTS_CSV
MARKET_REFERENCE_CSV = shared.MARKET_REFERENCE_CSV
NORMAL_PRICE_REFERENCE_CSV = shared.NORMAL_PRICE_REFERENCE_CSV
SEARCH_CACHE_CSV = DATA_DIR / "gemini_price_search_cache.csv"
CANDIDATE_DATABASE_CSV = DATA_DIR / "product_market_price_candidates.csv"
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_TIMEOUT_SECONDS = 45
DEFAULT_PAGE_TIMEOUT_SECONDS = 5
DEFAULT_MAX_CANDIDATES = 6
DEFAULT_MAX_PAGES_TO_VERIFY = 2
# Hard guardrails against accidentally triggering a large batch. Override limit
# deliberately only after confirming that the API project is on the Free tier.
MAX_PRODUCTS_PER_RUN = 20


class GeminiPriceSearchError(RuntimeError):
    """Gemini request/response could not safely be processed."""


def _canonical_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", ""))


def _extract_response_text(response_json: dict) -> str:
    chunks: list[str] = []
    for candidate in response_json.get("candidates", []) or []:
        content = candidate.get("content") or {}
        for part in content.get("parts", []) or []:
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
        if chunks:
            break
    return "\n".join(chunks)


def _extract_grounded_sources(response_json: dict, limit: int = DEFAULT_MAX_CANDIDATES) -> list[dict]:
    """Read source URLs only from Google Search grounding metadata."""
    sources: list[dict] = []
    seen: set[str] = set()
    for candidate in response_json.get("candidates", []) or []:
        metadata = candidate.get("groundingMetadata") or candidate.get("grounding_metadata") or {}
        chunks = metadata.get("groundingChunks") or metadata.get("grounding_chunks") or []
        for chunk in chunks:
            web = chunk.get("web") or chunk.get("retrievedContext") or {}
            url = str(web.get("uri") or web.get("url") or "").strip()
            title = str(web.get("title") or "").strip()
            if not url or not shared._is_safe_public_url(url):
                continue
            canonical = _canonical_url(url)
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            sources.append({"url": url, "canonical_url": canonical, "title": title, "provider": "gemini-google-search"})
            if len(sources) >= max(0, int(limit)):
                return sources
        if sources:
            break
    return sources


def _parse_model_json(text: str) -> dict:
    value = str(text or "").strip()
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I)
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start, end = value.find("{"), value.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(value[start:end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {"references": []}


def _make_prompt(product_text: str, merchant_name: str, promo_price: int | None, max_candidates: int) -> str:
    return f"""Cari referensi harga pasar terkini untuk produk Indonesia berikut dengan Google Search.
Produk target: {json.dumps(product_text, ensure_ascii=False)}
Merchant tempat promo ditemukan: {json.dumps(merchant_name or 'tidak diketahui', ensure_ascii=False)}
Harga promo yang diketahui (IDR): {promo_price if promo_price else 'tidak diketahui'}
Tanggal pencarian: {date.today().isoformat()}

Tugas:
- Cari maksimal {max_candidates} halaman sumber yang relevan untuk produk dengan merek, varian/rasa/formula, ukuran dan jumlah isi yang SAMA PERSIS.
- Prioritaskan halaman produk retailer resmi; marketplace boleh menjadi referensi pasar jika nama produk/varian/ukuran jelas.
- Ambil harga yang benar-benar didukung halaman hasil pencarian. Jangan menebak, menghitung dari harga promo, atau memakai pengetahuan internal model.
- Jangan menyebut harga toko lain sebagai harga normal resmi merchant promo asal.
- Setiap `url` HARUS sama dengan salah satu URL halaman yang kamu kutip/rujuk dari Google Search. Jangan buat URL sendiri.
- Jika harga tidak didukung bukti, isi `price_idr` dengan null dan jelaskan apa yang benar-benar terlihat.
- Kembalikan HANYA JSON valid dengan struktur:
{{"references":[{{"title":"judul halaman","url":"URL sumber","product_name":"nama produk persis yang terlihat","price_idr":22900,"currency":"IDR","price_type":"regular|promo|unclear","evidence":"kutipan/uraian singkat bukti harga dari sumber","seller":"nama toko atau domain"}}]}}
- `references` boleh kosong. Jangan memasukkan hasil yang tidak cocok hanya untuk memenuhi jumlah kandidat."""


def _call_gemini_search(
    product_text: str,
    merchant_name: str = "",
    promo_price: int | None = None,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    session: Any | None = None,
) -> dict:
    key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()
    if not key:
        raise GeminiPriceSearchError(
            "GEMINI_API_KEY belum disetel. Buat API key di Google AI Studio, pastikan project tetap Free tier dan tidak mengaktifkan billing."
        )
    product_text = shared.clean_product_name(product_text)
    if not product_text:
        raise GeminiPriceSearchError("Nama produk kosong")
    url = f"{GEMINI_API_BASE}/{model}:generateContent"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": _make_prompt(product_text, merchant_name, promo_price, max_candidates)}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1600},
    }
    client = session or requests.Session()
    try:
        response = client.post(
            url,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise GeminiPriceSearchError(f"Permintaan Gemini gagal: {type(exc).__name__}") from exc
    if response.status_code != 200:
        message = " ".join(str(getattr(response, "text", "") or "").split())[:350]
        if response.status_code in {401, 403}:
            raise GeminiPriceSearchError(
                f"Gemini menolak API key/akses (HTTP {response.status_code}). Periksa API key, model, region, dan status Free tier. {message}"
            )
        if response.status_code == 429:
            raise GeminiPriceSearchError(
                f"Kuota/rate limit Gemini tercapai (HTTP 429). Tidak ada retry otomatis agar permintaan tidak membengkak. {message}"
            )
        raise GeminiPriceSearchError(f"Gemini API mengembalikan HTTP {response.status_code}. {message}")
    try:
        body = response.json()
    except (ValueError, AttributeError) as exc:
        raise GeminiPriceSearchError("Respons Gemini bukan JSON yang valid") from exc
    text = _extract_response_text(body)
    if not text:
        raise GeminiPriceSearchError("Respons Gemini tidak berisi teks jawaban")
    sources = _extract_grounded_sources(body, limit=max_candidates)
    parsed = _parse_model_json(text)
    return {"text": text, "parsed": parsed, "sources": sources, "raw": body}


def _normalize_price(value: Any) -> int | None:
    price = shared.parse_idr_price(value)
    if price is None or price < 100 or price > 100_000_000:
        return None
    return price


def _currency_price_from_evidence(product_text: str, title: str, evidence: str) -> int | None:
    """Extract a concrete Rp/IDR amount from AI evidence, not arbitrary digits."""
    combined = " ".join(part for part in (title, evidence) if part).strip()
    if not combined or not shared._product_mentioned_in_page(product_text, combined):
        return None
    for match in re.finditer(r"(?i)\b(?:rp\.?|idr|rupiah)\s*([0-9][0-9.,\s]{0,18})", combined):
        amount = _normalize_price(match.group(1))
        if amount is not None:
            return amount
    return None


def _prepare_grounded_candidates(product_text: str, product_key: str, merchant_name: str,
                                  promo_price: int | None, search: dict, observed_at: str) -> list[dict]:
    sources = search.get("sources") or []
    model_rows = (search.get("parsed") or {}).get("references") or []
    if not isinstance(model_rows, list):
        model_rows = []
    source_by_url = {item["canonical_url"]: item for item in sources}
    model_by_url: dict[str, dict] = {}
    for item in model_rows:
        if not isinstance(item, dict):
            continue
        canonical = _canonical_url(str(item.get("url") or ""))
        # Critical anti-hallucination guard: the model cannot introduce a URL
        # unless Google Search grounding metadata returned that URL.
        if canonical and canonical in source_by_url:
            model_by_url[canonical] = item
    rows = []
    for source in sources:
        model_row = model_by_url.get(source["canonical_url"], {})
        title = str(model_row.get("title") or source.get("title") or "").strip()
        product_name = str(model_row.get("product_name") or "").strip()
        evidence = str(model_row.get("evidence") or "").strip()
        identity_text = " ".join(part for part in (product_name, title, evidence) if part)
        identity_ok = bool(identity_text) and (
            shared.same_product_variant(product_text, product_name)
            or shared._product_mentioned_in_page(product_text, identity_text)
        )
        price = _normalize_price(model_row.get("price_idr")) if identity_ok else None
        currency = str(model_row.get("currency") or "").strip().upper()
        if currency and currency not in {"IDR", "RP", "RUPIAH"}:
            price = None
        # Price mentions are hints only. The market DB gets updated exclusively
        # after the exact amount is found on the cited public page itself.
        hint_text = evidence
        hint_from_evidence = _currency_price_from_evidence(product_text, f"{product_name} {title}", evidence)
        # The AI's numeric field must agree with a currency amount in its cited
        # evidence before it can appear even as a candidate price hint.
        if price is None or hint_from_evidence != price:
            price = None
        candidate = free_finder._candidate_record(
            product_text, product_key, merchant_name, promo_price,
            {"url": source["url"], "title": title, "snippet": hint_text or search.get("text", "")[:850], "provider": "gemini-google-search"},
            observed_at,
            "gemini_grounded_needs_page_check",
            "Kandidat ditemukan lewat Gemini + Google Search; belum masuk database pasar sebelum harga dan SKU terbukti pada halaman sumber.",
            page_title=title,
            page_snippet=evidence or search.get("text", "")[:500],
            price_hint=price,
        )
        if not identity_ok:
            candidate["candidate_price_hint"] = ""
            candidate["rejection_reason"] += " Nama/ukuran pada jawaban AI belum cocok kuat dengan produk target."
        rows.append(candidate)
    return rows


def _verify_candidate_page(product_text: str, candidate: dict, timeout: int) -> dict | None:
    url = str(candidate.get("candidate_url") or "")
    with requests.Session() as client:
        page = free_finder._fetch_candidate_page(url, client, min(max(2, int(timeout)), DEFAULT_PAGE_TIMEOUT_SECONDS))
    if page is None:
        return None
    final_url, soup = page
    candidates = free_finder._price_candidates_on_page(product_text, soup)
    expected_hint = _normalize_price(candidate.get("candidate_price_hint"))
    chosen = next((item for item in candidates if expected_hint is not None and item[0] == expected_hint), None)
    if chosen is None and expected_hint is None and candidates:
        chosen = candidates[0]
    if not chosen:
        return None
    price, evidence, page_title = chosen
    domain = (urlparse(final_url).hostname or "").lower().removeprefix("www.")
    if not domain:
        return None
    return shared._normalize_record({
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
        "price_kind": shared._classify_price_kind_from_evidence(evidence),
        "source_kind": shared._classify_source_kind_from_url(final_url),
        "verification_method": "page_price_verified",
        "confidence": "low_to_medium",
    })


def search_gemini_for_product(product_text: str, merchant_name: str = "", promo_price: int | None = None,
                              api_key: str | None = None, model: str = DEFAULT_MODEL,
                              timeout: int = DEFAULT_TIMEOUT_SECONDS, max_candidates: int = DEFAULT_MAX_CANDIDATES,
                              max_pages_to_verify: int = DEFAULT_MAX_PAGES_TO_VERIFY, session: Any | None = None) -> dict:
    product_text = shared.clean_product_name(product_text)
    if not product_text:
        raise GeminiPriceSearchError("Nama produk kosong")
    search = _call_gemini_search(product_text, merchant_name, promo_price, api_key, model, timeout, max_candidates, session)
    product_key = shared.identity_key(product_text)
    candidate_records = _prepare_grounded_candidates(
        product_text, product_key, merchant_name, promo_price, search, date.today().isoformat(),
    )
    to_verify = candidate_records[:max(0, int(max_pages_to_verify))]
    verified: list[dict] = []
    by_domain: set[str] = set()
    if to_verify:
        with ThreadPoolExecutor(max_workers=min(2, len(to_verify))) as pool:
            futures = [pool.submit(_verify_candidate_page, product_text, row, timeout) for row in to_verify]
            for index, future in enumerate(futures):
                try:
                    reference = future.result()
                except Exception:
                    reference = None
                if not reference:
                    continue
                domain = str(reference.get("source_domain") or "").lower()
                if not domain or domain in by_domain:
                    continue
                by_domain.add(domain)
                verified.append(reference)
                match = next((row for row in candidate_records if _canonical_url(row.get("candidate_url", "")) == _canonical_url(reference.get("source_url", ""))), None)
                if match:
                    match["candidate_status"] = "verified_price_source"
                    match["candidate_price_hint"] = str(reference.get("market_price", ""))
                    match["rejection_reason"] = "Harga dan identitas produk terdeteksi ulang pada halaman sumber; masuk database pasar."
    return {
        "references": verified,
        "candidate_records": candidate_records,
        "candidate_page_count": len(candidate_records),
        "candidate_price_hint_count": sum(bool(row.get("candidate_price_hint")) for row in candidate_records),
        "grounded_source_count": len(search.get("sources") or []),
        "model_text": search.get("text", ""),
        "search_note": f"Gemini + Google Search: {len(candidate_records)} sumber bersitasi; {len(verified)} harga diverifikasi ulang pada halaman.",
    }


def run_gemini_price_finder(input_path: str = str(INPUT_PRODUCTS_CSV), database_path: str = str(MARKET_REFERENCE_CSV),
                            cache_path: str = str(SEARCH_CACHE_CSV), limit: int = 5, force: bool = False,
                            dry_run: bool = False, model: str = DEFAULT_MODEL, api_key: str | None = None,
                            timeout: int = DEFAULT_TIMEOUT_SECONDS, max_candidates: int = DEFAULT_MAX_CANDIDATES,
                            max_pages_to_verify: int = DEFAULT_MAX_PAGES_TO_VERIFY,
                            today: date | None = None) -> dict:
    today = today or date.today()
    source_path = Path(input_path)
    if not source_path.exists() and source_path == INPUT_PRODUCTS_CSV:
        source_path = FALLBACK_PRODUCTS_CSV
    products = shared.load_product_rows(source_path)
    refs = shared.load_market_reference_database(database_path)
    normal_refs = shared.load_reference_database(NORMAL_PRICE_REFERENCE_CSV)
    cache = shared.load_search_cache(cache_path)
    candidates = shared.collect_search_candidates(products, cache, refs, today, force=force, normal_price_references=normal_refs)
    selected = candidates[:min(MAX_PRODUCTS_PER_RUN, max(0, int(limit)))]
    if dry_run:
        return {
            "mode": "gemini_google_search_grounding",
            "model": model,
            "input_path": str(source_path),
            "eligible_unique_products": len(candidates),
            "selected_count": len(selected),
            "api_called": False,
            "products": [{"product_text": row["product_text"], "merchant_name": row.get("merchant_name", ""), "promo_price": row["promo_price"]} for row in selected],
        }
    key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()
    if selected and not key:
        raise GeminiPriceSearchError("GEMINI_API_KEY belum disetel. Gunakan --dry-run tanpa API, atau setel key dari Google AI Studio Free tier.")

    all_found: list[dict] = []
    all_candidate_rows: list[dict] = []
    results: list[dict] = []
    search_errors = 0
    for index, row in enumerate(selected, start=1):
        product = row["product_text"]
        pkey = row["product_key"]
        print(f"[{index}/{len(selected)}] Gemini + Google Search: {product}", flush=True)
        try:
            result = search_gemini_for_product(
                product, str(row.get("merchant_name") or ""), row.get("promo_price"),
                api_key=key, model=model, timeout=timeout, max_candidates=max_candidates,
                max_pages_to_verify=max_pages_to_verify,
            )
        except GeminiPriceSearchError as exc:
            search_errors += 1
            results.append({"product_text": product, "status": "search_error", "note": str(exc)})
            print(f"  PERINGATAN: {exc}", flush=True)
            # Stop on first API/permission/quota error to avoid repeated calls.
            break
        found = result.get("references") or []
        candidate_rows = result.get("candidate_records") or []
        all_found.extend(found)
        all_candidate_rows.extend(candidate_rows)
        status = "page_verified_sources_found" if found else ("grounded_candidates_found" if candidate_rows else "no_grounded_sources")
        cache[pkey] = {
            "product_key": pkey, "product_text": product, "searched_at": today.isoformat(),
            "search_status": status, "verified_source_count": str(len(found)),
            "note": result.get("search_note", ""),
        }
        results.append({
            "product_text": product, "status": status,
            "verified_source_count": len(found),
            "grounded_source_count": result.get("grounded_source_count", 0),
            "candidate_price_hint_count": result.get("candidate_price_hint_count", 0),
            "note": result.get("search_note", ""),
        })
        print(
            f"  Sumber Google: {result.get('grounded_source_count', 0)}; petunjuk harga: "
            f"{result.get('candidate_price_hint_count', 0)}; diverifikasi pada halaman: {len(found)}",
            flush=True,
        )
        shared.save_search_cache(cache, cache_path)
        time.sleep(0.15)
    db_summary = shared.upsert_market_reference_database(database_path, all_found)
    candidate_summary = free_finder.upsert_candidate_database(CANDIDATE_DATABASE_CSV, all_candidate_rows)
    shared.save_search_cache(cache, cache_path)
    summary = {
        "mode": "gemini_google_search_grounding", "model": model,
        "input_path": str(source_path), "eligible_products": len(candidates), "searched": len(results),
        "verified_references_found": len(all_found), "search_errors": search_errors,
        "database": db_summary, "candidate_database": candidate_summary,
        "candidate_pages_saved": len(all_candidate_rows),
        "candidate_price_hints_saved": sum(bool(row.get("candidate_price_hint")) for row in all_candidate_rows),
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def _load_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cari referensi harga dengan Gemini + Google Search grounding (API key Google).")
    parser.add_argument("--input", default=str(INPUT_PRODUCTS_CSV))
    parser.add_argument("--database", default=str(MARKET_REFERENCE_CSV))
    parser.add_argument("--cache", default=str(SEARCH_CACHE_CSV))
    parser.add_argument("--limit", type=int, default=5, help="Produk per proses; default 5, batas keras 20")
    parser.add_argument("--force", action="store_true", help="Abaikan cache referensi dan ulangi pencarian")
    parser.add_argument("--dry-run", action="store_true", help="Lihat antrean tanpa API/network")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES)
    parser.add_argument("--verify-pages", type=int, default=DEFAULT_MAX_PAGES_TO_VERIFY, help="Jumlah halaman yang diperiksa langsung per produk (default 2)")
    return parser.parse_args()


def main() -> int:
    args = _load_cli()
    try:
        summary = run_gemini_price_finder(
            input_path=args.input, database_path=args.database, cache_path=args.cache,
            limit=args.limit, force=args.force, dry_run=args.dry_run, model=args.model,
            timeout=max(10, args.timeout), max_candidates=max(1, min(10, args.max_candidates)),
            max_pages_to_verify=max(0, min(4, args.verify_pages)),
        )
    except (GeminiPriceSearchError, OSError, ValueError) as exc:
        print(f"GEMINI PRICE FINDER BERHENTI: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
