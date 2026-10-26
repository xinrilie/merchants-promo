"""Import and apply human-reviewed prices read from promotion posters."""
from __future__ import annotations

import csv
import hashlib
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from openpyxl import load_workbook
from promo_market_prices import parse_idr_price
from promo_price_reference import identity_key

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REVIEW_XLSX = ROOT / "hasil" / "Review_Harga_Poster_OCR.xlsx"
DEFAULT_OVERRIDES_CSV = ROOT / "_internal" / "promo_poster_price_overrides.csv"
NO_MATCH_SKU_SENTINEL = "Tidak ada SKU yang cocok"
OVERRIDE_FIELDS = [
    "candidate_id", "decision", "product_text", "normal_price", "promo_price",
    "source_name", "campaign_title", "article_url", "image_url", "ocr_snippet",
    "ocr_confidence", "discount_claim_percent", "reviewed_at", "review_method",
]


def _parse_price(value):
    n = parse_idr_price(value)
    return n if n is not None and 100 <= n <= 100_000_000 else None


def _normalize_url(value: object) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        host = parsed.hostname.lower().removeprefix("www.")
        path = parsed.path.rstrip("/") or "/"
        return urlunparse((parsed.scheme.lower(), host, path, "", parsed.query, ""))
    except ValueError:
        return ""


def import_reviewed_prices(review_xlsx: str | Path = DEFAULT_REVIEW_XLSX, overrides_csv: str | Path = DEFAULT_OVERRIDES_CSV) -> dict:
    """Upsert TERIMA decisions from review workbook, preserving an auditable CSV."""
    review_xlsx = Path(review_xlsx)
    overrides_csv = Path(overrides_csv)
    if not review_xlsx.exists():
        raise FileNotFoundError(f"Workbook review OCR tidak ditemukan: {review_xlsx}")

    existing = {}
    if overrides_csv.exists():
        with overrides_csv.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                cid = str(row.get("candidate_id") or "").strip()
                if cid:
                    existing[cid] = {k: str(row.get(k) or "") for k in OVERRIDE_FIELDS}

    wb = load_workbook(review_xlsx, read_only=True, data_only=True)
    try:
        if "Kandidat OCR" not in wb.sheetnames:
            raise ValueError("Sheet 'Kandidat OCR' tidak ditemukan di workbook review OCR.")
        ws = wb["Kandidat OCR"]
        header_values = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
        headers = [str(value or "").strip() for value in header_values]
        by_name = {h: i for i, h in enumerate(headers)}
        required = {"ID Kandidat", "Keputusan"}
        missing = required - set(by_name)
        product_field = "Nama Produk untuk Review" if "Nama Produk untuk Review" in by_name else "Nama Produk Disetujui"
        normal_field = "Harga Normal untuk Review (Rp)" if "Harga Normal untuk Review (Rp)" in by_name else "Harga Normal Disetujui (Rp)"
        promo_field = "Harga Promo untuk Review (Rp)" if "Harga Promo untuk Review (Rp)" in by_name else "Harga Promo Disetujui (Rp)"
        for field in (product_field, normal_field, promo_field):
            if field not in by_name:
                missing.add(field)
        if missing:
            raise ValueError("Kolom workbook tidak lengkap: " + ", ".join(sorted(missing)))

        accepted = rejected = deactivated = reviewed_rows = 0
        errors = []
        for excel_row, values in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            row = {name: values[idx] if idx < len(values) else None for name, idx in by_name.items()}
            candidate_id = str(row.get("ID Kandidat") or "").strip()
            decision = str(row.get("Keputusan") or "CEK").strip().upper()
            if not candidate_id:
                continue
            reviewed_rows += 1
            previous = existing.get(candidate_id)
            if decision != "TERIMA":
                # A user may explicitly retract a previously accepted correction.
                if previous and decision in {"TOLAK", "CEK", "PERIKSA"}:
                    if previous.get("decision") == "TERIMA":
                        deactivated += 1
                    previous["decision"] = "TOLAK" if decision == "TOLAK" else "CEK"
                    existing[candidate_id] = previous
                continue

            # Backward-compatible aliases allow existing review workbooks to be
            # imported. New workbooks use non-approval labels; `TERIMA` remains
            # the only condition that activates the submitted values.
            if product_field == "Nama Produk untuk Review":
                # New review schema requires an explicit SKU in the editable
                # product field. Never silently substitute the unverified OCR
                # suggestion column when the reviewer leaves the SKU blank.
                product = str(row.get(product_field) or "").strip()
            else:
                # Legacy workbooks used an approved column plus an optional
                # suggestion field; keep backward compatibility for those files.
                product = str(row.get(product_field) or row.get("Nama Produk Saran") or row.get("Nama Produk Disetujui") or "").strip()
            normal_price = _parse_price(row.get(normal_field))
            promo_price = _parse_price(row.get(promo_field))
            source_name = str(row.get("Sumber") or "").strip()
            campaign = str(row.get("Kampanye") or "").strip()
            article_url = str(row.get("URL Artikel") or "").strip()
            image_url = str(row.get("URL Gambar") or "").strip()
            snippet = str(row.get("Potongan Teks Poster OCR") or "").strip()
            confidence = str(row.get("Keyakinan OCR") or "").strip()
            claim = str(row.get("Klaim Diskon OCR (%)") or "").strip()
            issues = []
            if product.casefold() == NO_MATCH_SKU_SENTINEL.casefold():
                issues.append("pilihan 'Tidak ada SKU yang cocok' bukan nama produk; koreksi SKU atau ubah keputusan ke CEK/TOLAK")
            elif not product or not identity_key(product):
                issues.append("nama produk untuk review kosong")
            if normal_price is None and promo_price is None:
                issues.append("isi minimal harga normal atau harga promo")
            if normal_price is not None and promo_price is not None and promo_price >= normal_price:
                issues.append("harga promo harus lebih rendah daripada harga normal")
            if not source_name or not article_url or not image_url:
                issues.append("metadata sumber/poster tidak lengkap")
            if not snippet:
                issues.append("teks OCR/bukti poster kosong")
            if issues:
                rejected += 1
                errors.append(f"baris {excel_row} ({candidate_id}): " + "; ".join(issues))
                continue

            record = {
                "candidate_id": candidate_id,
                "decision": "TERIMA",
                "product_text": product,
                "normal_price": str(normal_price or ""),
                "promo_price": str(promo_price or ""),
                "source_name": source_name,
                "campaign_title": campaign,
                "article_url": article_url,
                "image_url": image_url,
                "ocr_snippet": snippet,
                "ocr_confidence": confidence,
                "discount_claim_percent": claim,
                "reviewed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "review_method": "user_approved_ocr_candidate",
            }
            existing[candidate_id] = record
            accepted += 1
    finally:
        wb.close()

    overrides_csv.parent.mkdir(parents=True, exist_ok=True)
    temp = overrides_csv.with_suffix(overrides_csv.suffix + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OVERRIDE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing[key] for key in sorted(existing))
    temp.replace(overrides_csv)
    return {
        "review_rows": reviewed_rows,
        "accepted_or_updated": accepted,
        "deactivated": deactivated,
        "rejected_invalid": rejected,
        "active_overrides": sum(row.get("decision") == "TERIMA" for row in existing.values()),
        "errors": errors,
        "database": str(overrides_csv),
    }


def apply_reviewed_poster_prices(rows: list[dict], overrides_csv: str | Path = DEFAULT_OVERRIDES_CSV) -> dict:
    """Apply only manually accepted poster prices to matching product rows.

    Matching requires same source + article URL + normalized SKU identity. Existing
    conflicting prices are never overwritten automatically.
    """
    path = Path(overrides_csv)
    if not path.exists():
        return {"overrides_available": 0, "rows_updated": 0, "rows_conflicted": 0, "rows_unmatched": 0, "rows_updated_names": []}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        overrides = [dict(row) for row in csv.DictReader(f) if str(row.get("decision") or "").upper() == "TERIMA"]

    index = {}
    for override in overrides:
        key = (
            str(override.get("source_name") or "").casefold(),
            _normalize_url(override.get("article_url")),
            identity_key(override.get("product_text")),
        )
        if all(key):
            index.setdefault(key, []).append(override)

    updated_names = []
    conflicts = 0
    matched_override_ids = set()
    for row in rows:
        source_name = str(row.get("source_name") or "").strip().casefold()
        urls = {_normalize_url(row.get(field)) for field in ("source_url", "source_page_url") if row.get(field)}
        name_key = identity_key(row.get("product_text"))
        if not source_name or not urls or not name_key:
            continue
        matched = []
        for url in urls:
            matched.extend(index.get((source_name, url, name_key), []))
        if len(matched) != 1:
            if len(matched) > 1:
                conflicts += 1
            continue
        override = matched[0]
        cid = override.get("candidate_id", "")
        matched_override_ids.add(cid)
        normal = _parse_price(override.get("normal_price"))
        promo = _parse_price(override.get("promo_price"))
        old_normal = _parse_price(row.get("normal_price"))
        old_promo = _parse_price(row.get("promo_price"))
        if (normal is not None and old_normal is not None and normal != old_normal) or (promo is not None and old_promo is not None and promo != old_promo):
            row["poster_ocr_override_conflict"] = f"Harga sumber berbeda dengan koreksi OCR yang disetujui: {cid}"
            conflicts += 1
            continue
        evidence = str(override.get("ocr_snippet") or "").strip()
        image_url = str(override.get("image_url") or "").strip()
        if normal is not None and old_normal is None:
            row["normal_price"] = str(normal)
            row["normal_price_evidence_text"] = (
                f"Harga Normal Rp{normal:,} (poster promo OCR, ditinjau manual); cuplikan OCR: {evidence}; gambar: {image_url}"
            ).replace(",", ".")
        if promo is not None and old_promo is None:
            row["promo_price"] = str(promo)
            row["promo_price_evidence_text"] = (
                f"Harga Promo Rp{promo:,} (poster promo OCR, ditinjau manual); cuplikan OCR: {evidence}; gambar: {image_url}"
            ).replace(",", ".")
        claim = str(override.get("discount_claim_percent") or "").strip()
        if claim and not row.get("source_discount_claim_percent"):
            row["source_discount_claim_percent"] = claim
        evidence_parts = ["Harga poster ditinjau manual setelah OCR"]
        if normal is not None:
            evidence_parts.append(f"Harga Normal Rp{normal:,}".replace(",", "."))
        if promo is not None:
            evidence_parts.append(f"Harga Promo Rp{promo:,}".replace(",", "."))
        if claim:
            evidence_parts.append(f"Klaim Diskon {claim}%")
        evidence_parts.append(f"Bukti OCR: {evidence}")
        evidence_parts.append(f"Gambar poster: {image_url}")
        marker = " | ".join(evidence_parts)
        old_offer = str(row.get("offer_text") or "").strip()
        if "Harga poster ditinjau manual setelah OCR" not in old_offer:
            row["offer_text"] = (old_offer + " | " + marker).strip(" |") if old_offer else marker
        row["price_parse_method"] = "poster_ocr_reviewed_manual"
        row["poster_ocr_reviewed_at"] = str(override.get("reviewed_at") or "")
        if normal is not None:
            row["normal_price_provenance"] = "poster_ocr_reviewed"
        approved_normal = _parse_price(row.get("normal_price"))
        approved_promo = _parse_price(row.get("promo_price"))
        if approved_normal is not None and approved_promo is not None and approved_promo < approved_normal:
            # The reviewer accepted a like-for-like normal/promo pair from one
            # poster, so the prices are comparable for product-level discount math.
            row["price_basis"] = "offer_price"
        row["poster_ocr_reviewed"] = "true"
        # A human has verified the SKU and displayed amount on the poster; remove
        # only the old parser warnings about that amount, not unrelated warnings.
        old_flags = [part.strip() for part in str(row.get("price_quality_flag") or "").split("|") if part.strip()]
        reviewed_flags = {
            "normal_price_parse_failed_or_unverified", "normal_price_without_matching_offer_evidence",
            "promo_price_parse_failed_or_unverified", "promo_price_without_matching_offer_evidence",
        }
        row["price_quality_flag"] = " | ".join(flag for flag in old_flags if flag not in reviewed_flags) or None
        old_sources = str(row.get("source_urls") or "").strip()
        if image_url and image_url not in old_sources:
            row["source_urls"] = (old_sources + " | " + image_url).strip(" |")
        updated_names.append(str(row.get("product_text") or ""))

    return {
        "overrides_available": len(overrides),
        "rows_updated": len(updated_names),
        "rows_conflicted": conflicts,
        "rows_unmatched": max(0, len(overrides) - len(matched_override_ids)),
        "rows_updated_names": updated_names,
    }
