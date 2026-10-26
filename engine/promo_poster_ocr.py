"""Local OCR for promotion-catalog poster images.

No generative price guessing is used. OCR lines and candidate price pairs are
saved with image/page provenance; only rows explicitly approved in Excel are
eligible to fill missing promo/normal price values in the final catalog.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import ipaddress
import json
import re
import statistics
import sys
import time
from functools import lru_cache
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests
from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError
from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter

from promo_names import product_name_similarity, product_name_size_signature, product_name_variant_signature, product_name_identity_warnings
from promo_price_reference import identity_key
from promo_market_prices import parse_idr_price
from promo_poster_overrides import DEFAULT_REVIEW_XLSX, NO_MATCH_SKU_SENTINEL, import_reviewed_prices

ROOT = Path(__file__).resolve().parent.parent
IMAGE_INDEX = ROOT / "_internal" / "promo_multisource_catalog_images.csv"
PRODUCTS_CSV = ROOT / "_internal" / "promo_multisource_products_all.csv"
CACHE_CSV = ROOT / "_internal" / "promo_poster_ocr_cache.csv"
LINES_CSV = ROOT / "_internal" / "promo_poster_ocr_lines.csv"
CANDIDATES_CSV = ROOT / "_internal" / "promo_poster_price_candidates.csv"
REVIEW_XLSX = DEFAULT_REVIEW_XLSX
OVERRIDES_CSV = ROOT / "_internal" / "promo_poster_price_overrides.csv"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
REQUEST_TIMEOUT = (5, 15)
OCR_PIPELINE_VERSION = "poster-ocr-v2.1-fast-crop"
# Parser-only upgrades can rebuild price candidates from cached OCR boxes without re-running ONNX.
CANDIDATE_PIPELINE_VERSION = "poster-ocr-v2.2-spatial-context"
# Bound per-image crop inference time. Full-page OCR remains the primary pass;
# color crops are only a rescue path for labels whose price was not read.
MAX_COLOR_PRICE_CROPS_PER_IMAGE = 6
# Product cards on HAP/weekly posters can contain 2 normal/promo price boxes
# plus another product price just 4–8% of page height away. Keep price clusters
# pair-sized and use the local title region separately.
MAX_PRICE_CLUSTER_VERTICAL_SPAN_RATIO = 0.05
USER_AGENT = "Mozilla/5.0 (compatible; MerchantsPromoPosterOCR/1.0; local catalog audit)"

CACHE_FIELDS = [
    "image_url", "source_name", "campaign_title", "article_url", "image_alt", "period_status",
    "scraped_at", "image_sha256", "image_width", "image_height", "ocr_status", "ocr_backend",
    "ocr_pipeline_version", "ocr_error", "processed_at", "lines_json",
]
LINE_FIELDS = [
    "source_name", "campaign_title", "article_url", "image_url", "image_alt", "period_status",
    "line_no", "ocr_text", "ocr_confidence", "x0", "y0", "x1", "y1", "image_width", "image_height", "processed_at", "recognition_method",
]
CANDIDATE_FIELDS = [
    "candidate_id", "candidate_pipeline_version", "source_name", "campaign_title", "article_url", "image_url", "image_alt",
    "ocr_snippet", "price_values_json", "normal_price_candidate", "promo_price_candidate",
    "discount_claim_percent", "discount_calculated_percent", "ocr_confidence",
    "matched_product_name", "matched_product_score", "local_product_name_suggestion", "match_note", "candidate_note",
    "catalog_sku_candidate_1", "catalog_sku_candidate_2", "catalog_sku_candidate_3",
    "catalog_sku_score_1", "catalog_sku_score_2", "catalog_sku_score_3",
    "discount_validation_status", "discount_difference_pp",
    "price_pair_method", "promo_context_tags", "purchase_condition", "regional_scope",
    "member_price_candidate", "ignored_packaging_percentages", "discount_claim_source", "discount_claim_context",
]
REVIEW_HEADERS = [
    "ID Kandidat", "Keputusan", "Nama Produk untuk Review", "Saran Nama Produk (OCR Lokal)",
    "Harga Normal untuk Review (Rp)", "Harga Promo untuk Review (Rp)",
    "Harga Normal Kandidat (Rp)", "Harga Promo Kandidat (Rp)",
    "Klaim Diskon OCR (%)", "Diskon Hitung (%)", "Kecocokan Nama", "Keyakinan OCR",
    "Potongan Teks Poster OCR", "Sumber", "Kampanye", "URL Artikel", "URL Gambar", "Catatan Review",
    "Kandidat SKU 1", "Kandidat SKU 2", "Kandidat SKU 3", "Pilihan jika tidak ada SKU cocok",
    "Skor SKU 1", "Skor SKU 2", "Skor SKU 3",
    "Validasi Klaim Diskon", "Selisih Diskon (poin persentase)",
    "Metode Pasangan Harga", "Konteks Promo (aturan lokal)", "Syarat Pembelian Terdeteksi",
    "Cakupan Wilayah Terdeteksi", "Kandidat Harga Member (Rp)",
    "Persentase Kemasan Diabaikan", "Sumber Klaim Diskon", "Teks Klaim Diskon yang Dipakai",
]

PRICE_CUE_RE = re.compile(r"(?i)\b(?:rp\.?|idr|harga|price|promo|normal|asli|reguler|diskon|discount|hemat|off|potongan)\b")
LABEL_NORMAL_RE = re.compile(r"(?i)\b(?:harga\s*(?:normal|asli|reguler|sebelum\s*promo)|normal\s*price|regular\s*price)\b")
LABEL_PROMO_RE = re.compile(r"(?i)\b(?:harga\s*(?:promo|diskon|spesial)|promo\s*price|sale\s*price|price\s*after\s*discount)\b")
DISCOUNT_RE = re.compile(r"(?i)(?<!\d)(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
RP_AMOUNT_RE = re.compile(r"(?i)\b(?:rp\.?|idr)\s*(\d[\d.,]{0,12})")
OCR_MISSING_P_R_AMOUNT_RE = re.compile(r"(?i)(?<![\w])R(?!p)\s*(\d{1,3}(?:[.,:]\d{3})+)(?![\w])")
GROUPED_AMOUNT_RE = re.compile(r"(?<![\w])(\d{1,3}(?:[.,:]\d{3})+)(?![\w])")
PLAIN_AMOUNT_RE = re.compile(r"(?<![\w])([1-9]\d{3,7})(?![\w])")
UNIT_AFTER_RE = re.compile(r"(?i)^\s*(?:ml|g|gr|gram|kg|mg|l|liter|litre|pcs?|buah|lembar|sheets?|pack|sachet|tablet|capsule)\b")

# Percentage text on packaging is not automatically a price-discount claim.
# Example from the Oct 2026 Alfamart poster: "30% LEBIH BANYAK SEREAL" on Energen.
PACKAGING_PERCENT_CONTEXT_RE = re.compile(
    r"(?i)\b(?:lebih\s+banyak|lebih\s+besar|lebih\s+isi|isi\s+lebih|"
    r"isi\s+lebih\s+banyak|extra|ekstra|bonus|gram\s+lebih|ml\s+lebih|"
    r"netto|isi\s+bersih|buy\s+1\s+extra\s+1)\b"
)
DISCOUNT_WORDING_RE = re.compile(
    r"(?i)\b(?:diskon|discount|disc\.?|hemat|potongan(?:\s+harga)?|off|price\s*cut|harga\s+turun)\b"
)
MULTIBUY_OFFER_RE = re.compile(
    r"(?i)\b(?:beli\s*\d+\s*(?:pcs?\s*)?(?:gratis|free)\s*\d+|"
    r"buy\s*\d+\s*get\s*\d+|ambil\s*\d+\s*gratis\s*\d+|"
    r"beli\s*\d+\s*gratis\s*\d+)\b"
)
# Purchase quantity may be stated in the offer title ("BELI 5 INDOMIE") or
# attached to the price itself ("Rp15.300/5Pcs"). These are distinct from
# packaging claims such as "BUY 1 EXTRA 1", which must never become retailer terms.
BUY_QUANTITY_RE = re.compile(r"(?i)\b(?:beli|buy)\s*(\d{1,2})\b")
PRICE_QUANTITY_SUFFIX_RE = re.compile(
    r"(?i)(?:/|per\s*)(\d{1,2})\s*(?:p[ce]s?|buah|unit)\b"
)
ADD_QUANTITY_OFFER_RE = re.compile(
    r"(?i)\btambah\s+(?:rp\.?\s*[\d.,]+\s+)?(?:dapat|untuk)\s+(\d{1,2})\s*(?:pcs?|pc|buah|unit)\b"
)
PACKAGING_EXTRA_RE = re.compile(
    r"(?i)\b(?:buy\s*1\s*extra\s*1|extra\s*(?:isi|content)|"
    r"lebih\s+banyak|lebih\s+isi|isi\s+lebih\s+banyak|bonus\s+isi)\b"
)
MEMBER_CUE_RE = re.compile(r"(?i)\b(?:member|kartu\s+member|member\s+alfamart|alfamart\s+member)\b")
REGION_CUE_RE = re.compile(r"(?i)\b(?:luar\s+(?:pulau\s+)?jawa|non[- ]?jawa|jabodetabek|jawa\s+dan\s+luar\s+jawa|wilayah\s+(?:tertentu|berlaku))\b")
PAYMENT_PROMO_RE = re.compile(r"(?i)\b(?:shopeepay|spaylater|cashback|e[- ]?wallet|voucher\s+(?:pembayaran|transaksi)|minimal\s+belanja)\b")
CAMPAIGN_FOOTER_RE = re.compile(
    r"(?i)(?:diskon\s+aplikasi|shopeepay|spaylater|minimal\s+belanja|minimum\s+belanja|"
    r"1x\s*/?\s*pengguna|per\s*periode|kuota\s+(?:terbatas|berlaku)|"
    r"tidak\s+berlaku|promo\s+tidak\s+berlaku|tersedia\s+juga\s+di\s+alfagift)"
)
NON_SKU_PACKAGING_COPY_RE = re.compile(
    r"(?i)\b(?:sumber\s+serat|lebih\s+banyak\s+sereal|ayo\s+sarapan|"
    r"setiap\s+hari|kaya\s+(?:vitamin|kalsium|protein)|netto|isi\s+bersih|"
    r"diformulasi\s+untuk|diperkaya\s+(?:vitamin|mineral)|bonus\s+isi)\b"
)
SPECIAL_PRICE_RE = re.compile(r"(?i)\b(?:harga\s+spesial|special\s+price|harga\s+khusus)\b")
DISCOUNT_PAIR_MAX_DIFFERENCE_PP = 3.0
DISCOUNT_REVIEW_MATCH_TOLERANCE_PP = 1.0


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def _safe_image_url(value: str) -> bool:
    """Accept public HTTP(S) image URLs only; never fetch literal private IPs."""
    try:
        p = urlparse(str(value or "").strip())
        host = (p.hostname or "").rstrip(".").lower()
        if p.scheme not in {"http", "https"} or not host or p.username or p.password:
            return False
        if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
            return False
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global:
                return False
        except ValueError:
            # Hostname: only deny local/private suffixes. Public DNS resolution is
            # left to requests; this avoids blocking legitimate public image hosts.
            pass
        return True
    except ValueError:
        return False


def _canonical_article_url(value: object) -> str:
    """Normalize article URLs consistently for OCR-to-product association."""
    try:
        parsed = urlparse(str(value or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        host = parsed.hostname.lower().removeprefix("www.")
        path = parsed.path.rstrip("/") or "/"
        return urlunparse((parsed.scheme.lower(), host, path, "", parsed.query, ""))
    except ValueError:
        return ""


def _normalize_box(box) -> tuple[float, float, float, float]:
    """Normalize rectangle- or polygon-shaped OCR box outputs to xyxy."""
    try:
        points = list(box)
        # Some OCR APIs return [x0, y0, x1, y1]; others return polygon points.
        if len(points) == 4 and all(not isinstance(point, (list, tuple)) and getattr(point, "ndim", 0) == 0 for point in points):
            x0, y0, x1, y1 = (float(point) for point in points)
            return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        return min(xs), min(ys), max(xs), max(ys)
    except (TypeError, ValueError, IndexError):
        return 0.0, 0.0, 0.0, 0.0


def _as_list(value) -> list:
    """Convert Python/NumPy OCR outputs to a list without ambiguous truth tests."""
    if value is None:
        return []
    try:
        return list(value)
    except (TypeError, ValueError):
        return []


def _confidence(value) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return 0.0
    if n > 1.0:
        n /= 100.0
    return min(1.0, max(0.0, n))


def create_ocr_engine(preferred: str = "auto"):
    """Create a lightweight local OCR engine without a cloud API or Paddle runtime.

    The default prefers RapidOCR/ONNX Runtime. Tesseract is a fallback only when
    its executable is already installed locally; the pip package alone is not enough.
    """
    preferred = (preferred or "auto").strip().casefold()
    factories = {
        "rapidocr": ("RapidOCR",),
        "tesseract": ("Tesseract",),
        "auto": ("RapidOCR", "Tesseract"),
    }
    if preferred not in factories:
        raise ValueError(f"Backend OCR tidak dikenal: {preferred}")
    errors = []
    for name in factories[preferred]:
        try:
            if preferred == "auto":
                print(f"Inisialisasi backend OCR: {name}...", flush=True)
            if name == "RapidOCR":
                from rapidocr import RapidOCR
                return "rapidocr", RapidOCR()
            import pytesseract
            pytesseract.get_tesseract_version()
            return "tesseract", pytesseract
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    if preferred != "auto":
        raise RuntimeError(f"Backend OCR {preferred} tidak tersedia. " + " | ".join(errors))
    raise RuntimeError(
        "Mesin OCR lokal auto tidak tersedia. Jalankan Install_Dependency.bat "
        "untuk memasang RapidOCR/ONNX Runtime. Jika Tesseract lokal sudah dipasang, "
        "backend itu juga bisa dipilih. Backend yang dicoba: " + " | ".join(errors)
    )


def _prepare_image(image: Image.Image) -> Image.Image:
    """Upscale and improve local contrast before OCR without losing colour layout."""
    image = ImageOps.exif_transpose(image).convert("RGB")
    largest = max(image.size)
    if largest < 1800:
        scale = min(2.5, 1800 / max(1, largest))
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.Resampling.LANCZOS)
    image = ImageOps.autocontrast(image, cutoff=1)
    image = ImageEnhance.Contrast(image).enhance(1.12)
    image = ImageEnhance.Sharpness(image).enhance(1.2)
    # OpenCV CLAHE helps low-contrast small prices. It's optional so the Pillow
    # preprocessing above remains usable if cv2 is unavailable.
    try:
        import cv2
        import numpy as np
        rgb = np.asarray(image)
        lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
        light, a, b = cv2.split(lab)
        light = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(8, 8)).apply(light)
        adjusted = cv2.cvtColor(cv2.merge((light, a, b)), cv2.COLOR_LAB2RGB)
        image = Image.fromarray(adjusted)
    except Exception:
        pass
    return image


def _detect_color_price_tag_boxes(image: Image.Image) -> list[tuple[int, int, int, int, str]]:
    """Detect yellow normal-price and red promo-price labels with local OpenCV masks.

    This is a bounded crop proposal step, not price recognition: OCR must still
    read one plausible formatted IDR amount from each crop before a line is added.
    Boxes outside the product-card area (especially the footer) are ignored.
    """
    try:
        import cv2
        import numpy as np
    except Exception:
        return []

    rgb = np.asarray(image.convert("RGB"))
    h_img, w_img = rgb.shape[:2]
    if w_img < 300 or h_img < 400:
        return []
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    yellow = cv2.inRange(hsv, np.array([15, 65, 115]), np.array([43, 255, 255]))
    # Keep the two red hue bands separate during contour detection. Combining
    # them can connect a price badge to red packaging/footer regions and make
    # the resulting contour too large to qualify as a price tag.
    red_low = cv2.inRange(hsv, np.array([0, 70, 70]), np.array([12, 255, 255]))
    red_high = cv2.inRange(hsv, np.array([170, 70, 70]), np.array([180, 255, 255]))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3))
    candidates_by_color: dict[str, list[tuple[int, int, int, int, str]]] = {"YELLOW": [], "RED": []}
    for color, raw_mask in (("YELLOW", yellow), ("RED", red_low), ("RED", red_high)):
        mask = cv2.morphologyEx(raw_mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            aspect = w / max(1.0, float(h))
            center_y = (y + h / 2) / h_img
            if not (0.055 * w_img <= w <= 0.34 * w_img):
                continue
            if not (0.014 * h_img <= h <= 0.095 * h_img):
                continue
            if not (2.0 <= aspect <= 8.5):
                continue
            # The product-price cards occupy the middle of this poster family;
            # the campaign header/footer commonly contains red/yellow shapes too.
            if not (0.17 <= center_y <= 0.845):
                continue
            candidates_by_color[color].append((x, y, x + w, y + h, color))
    proposed = []
    for color, color_boxes in candidates_by_color.items():
        color_boxes.sort(key=lambda b: ((b[3] - b[1]) * (b[2] - b[0]), b[1], b[0]))
        accepted = []
        for box in color_boxes:
            x0, y0, x1, y1, _ = box
            duplicate = False
            for prev in accepted:
                px0, py0, px1, py1, _ = prev
                inter_w = max(0, min(x1, px1) - max(x0, px0))
                inter_h = max(0, min(y1, py1) - max(y0, py0))
                inter = inter_w * inter_h
                area_small = max(1, min((x1 - x0) * (y1 - y0), (px1 - px0) * (py1 - py0)))
                if inter / area_small >= 0.78:
                    duplicate = True
                    break
            if not duplicate:
                accepted.append(box)
        proposed.extend(accepted)
    # Highest y is fine; stable position order makes cache results reproducible.
    proposed.sort(key=lambda b: ((b[1] + b[3]) / 2, b[0], b[4]))
    return proposed


def _crop_ocr_price_text(crop: Image.Image, engine, backend: str) -> tuple[str, float]:
    """OCR a single coloured price-tag crop; returns raw text and confidence."""
    # Upscale only this small region. The model sees fewer background/text classes
    # than in the full poster and the crop remains local/on-device.
    scale = 3 if max(crop.size) < 700 else 2
    crop = crop.resize((max(1, crop.width * scale), max(1, crop.height * scale)), Image.Resampling.LANCZOS)
    if backend == "tesseract":
        import pytesseract
        from pytesseract import Output
        config = "--psm 8 -c tessedit_char_whitelist=0123456789.,:"
        data = pytesseract.image_to_data(crop, lang="eng", config=config, output_type=Output.DICT)
        tokens, scores = [], []
        for text, conf in zip(data.get("text", []), data.get("conf", [])):
            token = str(text or "").strip()
            if not token:
                continue
            tokens.append(token)
            try:
                numeric_conf = float(conf)
                if numeric_conf >= 0:
                    scores.append(numeric_conf / 100.0)
            except (TypeError, ValueError):
                pass
        raw = " ".join(tokens).strip()
        score = statistics.mean(scores) if scores else 0.0
        if not extract_prices(raw):
            # Some red-on-yellow chips binarize poorly. Retry with Otsu inversion,
            # still constraining recognition to price digits and separators.
            try:
                import cv2
                import numpy as np
                gray = cv2.cvtColor(np.asarray(crop), cv2.COLOR_RGB2GRAY)
                _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                retry_img = Image.fromarray(255 - binary)
                retry_data = pytesseract.image_to_data(retry_img, lang="eng", config=config, output_type=Output.DICT)
                retry_tokens, retry_scores = [], []
                for text, conf in zip(retry_data.get("text", []), retry_data.get("conf", [])):
                    token = str(text or "").strip()
                    if token:
                        retry_tokens.append(token)
                        try:
                            numeric_conf = float(conf)
                            if numeric_conf >= 0:
                                retry_scores.append(numeric_conf / 100.0)
                        except (TypeError, ValueError):
                            pass
                retry_raw = " ".join(retry_tokens).strip()
                if len({item["price"] for item in extract_prices(retry_raw)}) == 1:
                    raw = retry_raw
                    score = statistics.mean(retry_scores) if retry_scores else score
            except Exception:
                pass
        return raw, _confidence(score)

    if backend == "rapidocr":
        result = engine(crop)
        texts = _as_list(getattr(result, "txts", None))
        scores = _as_list(getattr(result, "scores", None))
        raw = " ".join(str(text).strip() for text in texts if str(text or "").strip())
        conf = [_confidence(score) for score in scores]
        return raw, statistics.mean(conf) if conf else 0.0

    return "", 0.0


def _augment_with_color_price_tag_crops(image: Image.Image, lines: list[dict], engine, backend: str) -> list[dict]:
    """Add locally OCR-read price-tag lines that a full-page pass missed."""
    if backend not in {"rapidocr", "tesseract"}:
        return lines
    boxes = _detect_color_price_tag_boxes(image)
    if not boxes:
        return lines
    width, height = image.size
    augmented = list(lines)
    crop_attempts = 0
    for x0, y0, x1, y1, color in boxes:
        box_w = max(1, x1 - x0)
        box_h = max(1, y1 - y0)

        # A color crop is a rescue pass, not a second OCR pass over every price.
        # If full-page OCR already recognized a price whose line sits inside the
        # tag (or its small margin), skip this crop entirely. The old behavior
        # OCRed every colored label and only discarded duplicates afterwards,
        # making one poster perform 12-23 extra ONNX inferences.
        tag_left = max(0.0, x0 - box_w * 0.12)
        tag_top = max(0.0, y0 - box_h * 0.35)
        tag_right = min(float(width), x1 + box_w * 0.12)
        tag_bottom = min(float(height), y1 + box_h * 0.35)
        existing_price_found = False
        for line in augmented:
            if not extract_prices(str(line.get("text") or "")):
                continue
            lx0 = float(line.get("x0", 0) or 0)
            ly0 = float(line.get("y0", 0) or 0)
            lx1 = float(line.get("x1", 0) or 0)
            ly1 = float(line.get("y1", 0) or 0)
            cx = (lx0 + lx1) / 2
            cy = (ly0 + ly1) / 2
            # Use either center-in-expanded-tag or geometric intersection. The
            # intersection branch handles OCR boxes whose baseline extends past
            # the colored rectangle while still representing its printed price.
            intersects = lx1 >= tag_left and lx0 <= tag_right and ly1 >= tag_top and ly0 <= tag_bottom
            center_inside = tag_left <= cx <= tag_right and tag_top <= cy <= tag_bottom
            if intersects or center_inside:
                existing_price_found = True
                break
        if existing_price_found:
            continue
        if crop_attempts >= MAX_COLOR_PRICE_CROPS_PER_IMAGE:
            break
        crop_attempts += 1

        pad_x = max(4, int(box_w * 0.05))
        # Include a narrow margin around the detected tag. Excessive vertical
        # padding can merge neighbouring labels into one OCR crop.
        pad_y = max(4, int((y1 - y0) * 0.12))
        left, top = max(0, x0 - pad_x), max(0, y0 - pad_y)
        right, bottom = min(width, x1 + pad_x), min(height, y1 + pad_y)
        crop = image.crop((left, top, right, bottom))
        raw, confidence = _crop_ocr_price_text(crop, engine, backend)
        prices = list(dict.fromkeys(item["price"] for item in extract_prices(raw)))
        # Never convert an unformatted 4-digit token or multiple amounts into a price.
        if len(prices) != 1:
            continue
        price = prices[0]
        if not (1_000 <= price <= 100_000_000):
            continue
        # A line is useful only if it contributes a new price at that same tag.
        cx, cy = (left + right) / 2, (top + bottom) / 2
        duplicate = False
        for line in augmented:
            line_prices = {item["price"] for item in extract_prices(str(line.get("text") or ""))}
            if price not in line_prices:
                continue
            lx, ly = _line_center(line)
            if abs(lx - cx) / max(1, width) <= 0.055 and abs(ly - cy) / max(1, height) <= 0.045:
                duplicate = True
                break
        if duplicate:
            continue
        augmented.append({
            "text": raw.strip(), "confidence": confidence,
            "x0": float(left), "y0": float(top), "x1": float(right), "y1": float(bottom),
            "recognition_method": f"COLOR_PRICE_TAG_CROP_{color}",
        })
    return _sort_lines(augmented)


def recognize_image(image_bytes: bytes, engine, backend: str) -> tuple[list[dict], int, int]:
    with Image.open(io.BytesIO(image_bytes)) as opened:
        # Keep an unenhanced colour copy for red/yellow price-tag detection while
        # OCR on the full page uses the contrast-enhanced image.
        color_source = ImageOps.exif_transpose(opened).convert("RGB")
        image = _prepare_image(opened)
    width, height = image.size
    if color_source.size != image.size:
        # BICUBIC preserves the saturated tag core more reliably than LANCZOS
        # for HSV thresholding on this poster family.
        color_source = color_source.resize(image.size, Image.Resampling.BICUBIC)
    if backend == "rapidocr":
        result = engine(image)
        texts = getattr(result, "txts", None)
        scores = getattr(result, "scores", None)
        boxes = getattr(result, "boxes", None)
        texts = [] if texts is None else list(texts)
        scores = [] if scores is None else list(scores)
        boxes = [] if boxes is None else list(boxes)
        lines = []
        for idx, text in enumerate(texts):
            if not str(text or "").strip():
                continue
            box = boxes[idx] if idx < len(boxes) else []
            score = scores[idx] if idx < len(scores) else 0.0
            x0, y0, x1, y1 = _normalize_box(box)
            lines.append({"text": str(text).strip(), "confidence": _confidence(score), "x0": x0, "y0": y0, "x1": x1, "y1": y1, "recognition_method": "FULL_PAGE"})
        lines = _augment_with_color_price_tag_crops(color_source, lines, engine, backend)
        return _sort_lines(lines), width, height

    # Local Tesseract fallback. It is not installed through pip; only used when
    # RapidOCR is absent but the Tesseract executable is already installed.
    from pytesseract import Output
    import pytesseract
    available_langs = set(pytesseract.get_languages(config=""))
    lang = "ind+eng" if {"ind", "eng"}.issubset(available_langs) else ("eng" if "eng" in available_langs else "osd")
    data = pytesseract.image_to_data(image, lang=lang, config="--psm 11", output_type=Output.DICT)
    grouped = {}
    for idx, text in enumerate(data.get("text", [])):
        text = str(text or "").strip()
        if not text:
            continue
        try:
            conf = max(0.0, float(data.get("conf", [0])[idx])) / 100.0
            x0, y0 = float(data["left"][idx]), float(data["top"][idx])
            x1, y1 = x0 + float(data["width"][idx]), y0 + float(data["height"][idx])
        except (TypeError, ValueError, IndexError):
            conf, x0, y0, x1, y1 = 0.0, 0.0, 0.0, 0.0, 0.0
        key = (data.get("block_num", [0])[idx], data.get("par_num", [0])[idx], data.get("line_num", [0])[idx])
        item = grouped.setdefault(key, {"parts": [], "scores": [], "x0": x0, "y0": y0, "x1": x1, "y1": y1})
        item["parts"].append(text)
        item["scores"].append(conf)
        item["x0"] = min(item["x0"], x0); item["y0"] = min(item["y0"], y0)
        item["x1"] = max(item["x1"], x1); item["y1"] = max(item["y1"], y1)
    lines = []
    for item in grouped.values():
        lines.append({"text": " ".join(item["parts"]), "confidence": statistics.mean(item["scores"]) if item["scores"] else 0.0, **{k: item[k] for k in ("x0", "y0", "x1", "y1")}, "recognition_method": "FULL_PAGE"})
    lines = _augment_with_color_price_tag_crops(color_source, lines, engine, backend)
    return _sort_lines(lines), width, height


def _sort_lines(lines: list[dict]) -> list[dict]:
    return sorted(lines, key=lambda line: ((float(line.get("y0", 0)) + float(line.get("y1", 0))) / 2, float(line.get("x0", 0))))


def _integer_amount(raw: str) -> int | None:
    return parse_idr_price(raw)


def extract_prices(text: str) -> list[dict]:
    """Extract plausible IDR amounts, preferring explicit Rp/IDR and grouped numbers."""
    text = str(text or "")
    matches = []
    occupied = []
    for match in RP_AMOUNT_RE.finditer(text):
        price = _integer_amount(match.group(1))
        if price is not None and 100 <= price <= 100_000_000:
            matches.append({"price": price, "start": match.start(), "end": match.end(), "raw": match.group(0), "explicit_currency": True})
            occupied.append((match.start(), match.end()))
    # OCR sometimes drops the P in Rp or reads the thousands separator as ':'.
    # Treat the single-letter R form as a candidate only (it is never auto-approved).
    for match in OCR_MISSING_P_R_AMOUNT_RE.finditer(text):
        if any(match.start() < end and match.end() > start for start, end in occupied):
            continue
        price = _integer_amount(match.group(1))
        if price is not None and 1_000 <= price <= 100_000_000:
            matches.append({"price": price, "start": match.start(), "end": match.end(), "raw": match.group(0), "explicit_currency": False})
            occupied.append((match.start(), match.end()))
    for match in GROUPED_AMOUNT_RE.finditer(text):
        if any(match.start() < end and match.end() > start for start, end in occupied):
            continue
        price = _integer_amount(match.group(1))
        if price is not None and 1_000 <= price <= 100_000_000:
            matches.append({"price": price, "start": match.start(), "end": match.end(), "raw": match.group(1), "explicit_currency": False})
    # Unformatted numbers are accepted only when a nearby currency/price cue exists.
    for match in PLAIN_AMOUNT_RE.finditer(text):
        if any(match.start() < end and match.end() > start for start, end in occupied):
            continue
        value = int(match.group(1))
        if 1_900 <= value <= 2_099:  # avoid campaign years mistaken for prices
            continue
        nearby = text[max(0, match.start() - 30):min(len(text), match.end() + 18)]
        if not re.search(r"(?i)\b(?:rp\.?|idr|harga|price|promo|normal|asli|reguler)\b", nearby):
            continue
        after = text[match.end():match.end() + 16]
        if UNIT_AFTER_RE.search(after):
            continue
        matches.append({"price": value, "start": match.start(), "end": match.end(), "raw": match.group(1), "explicit_currency": False})
    matches.sort(key=lambda item: item["start"])
    # Preserve repeated amounts at distinct locations, but suppress duplicate regex matches.
    out, seen_pos = [], set()
    for item in matches:
        pos = (item["start"], item["end"])
        if pos in seen_pos:
            continue
        seen_pos.add(pos); out.append(item)
    return out


def analyze_discount_claim(text: str) -> dict:
    """Extract plausible price-discount claims without confusing package copy.

    OCR sometimes splits a phrase across boxes, e.g. ``30%`` on one line and
    ``LEBIH BANYAK SEREAL`` on the next. The parser therefore checks the current
    OCR segment and its immediate neighbours. Explicit discount wording wins;
    conflicting claims stay ambiguous instead of silently selecting the first.
    """
    segments = [seg.strip() for seg in re.split(r"\s*(?:\||\r?\n)\s*", str(text or "")) if seg.strip()]
    valid = []
    ignored = []
    for index, segment in enumerate(segments):
        matches = list(DISCOUNT_RE.finditer(segment))
        if not matches:
            continue
        neighbour_segments = segments[max(0, index - 1):min(len(segments), index + 2)]
        local_context = " | ".join(neighbour_segments)
        current_discount_cue = bool(DISCOUNT_WORDING_RE.search(segment))
        current_packaging_cue = bool(PACKAGING_PERCENT_CONTEXT_RE.search(segment))
        nearest_discount_distance = min(
            (abs(other_index - index) for other_index, other in enumerate(segments)
             if DISCOUNT_WORDING_RE.search(other)),
            default=None,
        )
        nearest_packaging_distance = min(
            (abs(other_index - index) for other_index, other in enumerate(segments)
             if PACKAGING_PERCENT_CONTEXT_RE.search(other)),
            default=None,
        )
        # If the percentage's own line is packaging copy, only an explicit
        # discount cue on that same line can override the exclusion. When the
        # words were split into boxes, the closer adjacent cue wins; a tie goes
        # to the conservative packaging exclusion.
        is_packaging_copy = current_packaging_cue and not current_discount_cue
        if not current_packaging_cue and nearest_packaging_distance is not None:
            is_packaging_copy = (
                nearest_discount_distance is None
                or nearest_packaging_distance <= nearest_discount_distance
            )
        if is_packaging_copy:
            for match in matches:
                ignored.append(f"{match.group(0)} — {local_context[:120]}")
            continue
        has_discount_wording = current_discount_cue or (
            nearest_discount_distance is not None
            and (nearest_packaging_distance is None or nearest_discount_distance < nearest_packaging_distance)
            and nearest_discount_distance <= 1
        )
        for match in matches:
            try:
                value = float(match.group(1).replace(",", "."))
            except ValueError:
                continue
            if not (0 < value <= 100):
                continue
            # A discount keyword on an adjacent OCR line can label a bare percent
            # when the visual badge was segmented into two boxes.
            valid.append({
                "value": value,
                "segment": segment,
                "strength": 2 if has_discount_wording else 1,
                "source": "KATA_DISKON" if has_discount_wording else "PERSENTASE_TANPA_KATA_DISKON",
                "context": local_context,
            })

    ignored_text = "; ".join(dict.fromkeys(ignored))
    if not valid:
        return {
            "claim": None,
            "source": "PERSENTASE_KEMASAN_DIKECUALIKAN" if ignored else "TIDAK_TERDETEKSI",
            "context": "",
            "ignored": ignored_text,
            "note": "Persentase kemasan bukan klaim diskon harga." if ignored else "Klaim diskon tidak terdeteksi.",
        }

    strongest = max(item["strength"] for item in valid)
    strongest_items = [item for item in valid if item["strength"] == strongest]
    unique_values = sorted({item["value"] for item in strongest_items})
    if len(unique_values) != 1:
        return {
            "claim": None,
            "source": "KLAIM_DISKON_AMBIGU",
            "context": " | ".join(dict.fromkeys(item["context"] for item in strongest_items))[:240],
            "ignored": ignored_text,
            "note": "Ada beberapa persentase yang mungkin merupakan klaim diskon; tidak dipilih otomatis.",
        }
    picked = strongest_items[0]
    return {
        "claim": picked["value"],
        "source": picked["source"],
        "context": picked["context"][:240],
        "ignored": ignored_text,
        "note": "Klaim dipilih dari teks berlabel diskon." if picked["strength"] == 2 else "Persentase tanpa kata diskon diperlakukan sebagai petunjuk; perlu cek visual.",
    }


def extract_discount_claim(text: str) -> float | None:
    """Compatibility wrapper; does not classify packaging percentages as discounts."""
    return analyze_discount_claim(text).get("claim")


def _first_context_match(pattern: re.Pattern, segments: list[str]) -> str:
    for segment in segments:
        match = pattern.search(segment)
        if match:
            return match.group(0).strip()[:160]
    return ""


def _extract_purchase_condition(text: str) -> str:
    """Return a cautious, review-only purchase-quantity note from localized OCR text.

    Prefer explicit retail offer wording and the quantity attached to a price.
    Packaging copy (notably "BUY 1 EXTRA 1") is excluded. If several distinct
    quantities occur in the same region, preserve the ambiguity instead of
    choosing one silently.
    """
    segments = [seg.strip() for seg in re.split(r"\s*(?:\||\r?\n)\s*", str(text or "")) if seg.strip()]
    offer_phrases: list[str] = []
    suffix_quantities: list[int] = []
    for segment in segments:
        # Explicit multi-buy/gift terms are more specific than a bare quantity.
        multi = MULTIBUY_OFFER_RE.search(segment)
        if multi:
            offer_phrases.append(re.sub(r"\s+", " ", multi.group(0)).strip())
            continue
        if PACKAGING_EXTRA_RE.search(segment):
            continue
        if PACKAGING_PERCENT_CONTEXT_RE.search(segment) and not BUY_QUANTITY_RE.search(segment):
            continue
        buy = BUY_QUANTITY_RE.search(segment)
        if buy:
            # Exclude malformed OCR quantities and retain only explicit retail wording.
            quantity = int(buy.group(1))
            if 2 <= quantity <= 20:
                offer_phrases.append(f"Beli {quantity}")
        add = ADD_QUANTITY_OFFER_RE.search(segment)
        if add:
            quantity = int(add.group(1))
            if 2 <= quantity <= 20:
                offer_phrases.append(f"Tambah harga untuk {quantity} pcs")
        for match in PRICE_QUANTITY_SUFFIX_RE.finditer(segment):
            quantity = int(match.group(1))
            if 2 <= quantity <= 50:
                suffix_quantities.append(quantity)

    offer_phrases = list(dict.fromkeys(offer_phrases))
    suffix_quantities = list(dict.fromkeys(suffix_quantities))
    all_quantities = set(suffix_quantities)
    for phrase in offer_phrases:
        m = re.search(r"(?i)\b(?:beli|buy)\s*(\d{1,2})\b", phrase)
        if m:
            all_quantities.add(int(m.group(1)))
        m = re.search(r"(?i)\b(\d{1,2})\s*pcs\b", phrase)
        if m:
            all_quantities.add(int(m.group(1)))
    if len(all_quantities) > 1:
        return "AMBIGU: beberapa syarat jumlah terdeteksi (" + ", ".join(map(str, sorted(all_quantities))) + " pcs)"

    parts = offer_phrases[:]
    if suffix_quantities:
        quantity = suffix_quantities[0]
        suffix_note = f"harga tertera untuk {quantity} pcs"
        if not parts:
            parts.append(suffix_note.capitalize())
        elif quantity not in all_quantities or not any(re.search(rf"(?i)\b{quantity}\b", p) for p in parts):
            parts.append(suffix_note)
        elif not any("harga tertera" in p.casefold() for p in parts):
            parts.append(suffix_note)
    return "; ".join(parts)


def classify_promo_context(text: str) -> dict:
    """Classify review-only promo cues without assigning ambiguous prices to a SKU."""
    segments = [seg.strip() for seg in re.split(r"\s*(?:\||\r?\n)\s*", str(text or "")) if seg.strip()]
    joined = " | ".join(segments)
    tags = []
    if MEMBER_CUE_RE.search(joined):
        tags.append("HARGA/KETENTUAN MEMBER TERDETEKSI")
    if MULTIBUY_OFFER_RE.search(joined):
        tags.append("PROMO MULTI-ITEM TERDETEKSI")
    if REGION_CUE_RE.search(joined):
        tags.append("KETERANGAN WILAYAH TERDETEKSI")
    if PAYMENT_PROMO_RE.search(joined):
        tags.append("TEKS PROMO PEMBAYARAN/KAMPANYE — JANGAN LEKATKAN OTOMATIS KE SKU")
    if SPECIAL_PRICE_RE.search(joined):
        tags.append("LABEL HARGA SPESIAL TERDETEKSI")
    if PACKAGING_EXTRA_RE.search(joined) or PACKAGING_PERCENT_CONTEXT_RE.search(joined):
        tags.append("TEKS TAMBAHAN/KLAIM KEMASAN TERDETEKSI")

    # Only emit a member price when the same OCR segment contains both the cue
    # and exactly one amount. A nearby member word is not enough to infer price role.
    member_price = ""
    for segment in segments:
        if MEMBER_CUE_RE.search(segment):
            values = list(dict.fromkeys(item["price"] for item in extract_prices(segment)))
            if len(values) == 1:
                member_price = values[0]
                break
    purchase_condition = _extract_purchase_condition(joined)
    if purchase_condition:
        tags.append("SYARAT PEMBELIAN/JUMLAH TERDETEKSI — VERIFIKASI BASIS HARGA")
    if not tags and (LABEL_NORMAL_RE.search(joined) or LABEL_PROMO_RE.search(joined)):
        tags.append("LABEL HARGA NORMAL/PROMO TERDETEKSI")
    if not tags:
        tags.append("KONTEKS PROMO BELUM TERKLASIFIKASI")
    region = _first_context_match(REGION_CUE_RE, segments)
    return {
        "promo_context_tags": "; ".join(dict.fromkeys(tags)),
        "purchase_condition": purchase_condition,
        "regional_scope": region,
        "member_price_candidate": member_price,
    }


def _labelled_price(text: str, label_re: re.Pattern) -> int | None:
    label = label_re.search(text)
    if not label:
        return None
    after = text[label.end():label.end() + 80]
    prices = extract_prices(after)
    return prices[0]["price"] if prices else None


def interpret_price_pair(text: str, *, allow_unlabelled_pair: bool = False) -> dict:
    """Interpret price roles; spatial OCR can suggest unlabeled pairs for human review only."""
    prices = extract_prices(text)
    unique_prices = list(dict.fromkeys(item["price"] for item in prices))
    normal = _labelled_price(text, LABEL_NORMAL_RE)
    promo = _labelled_price(text, LABEL_PROMO_RE)
    claim_analysis = analyze_discount_claim(text)
    claim = claim_analysis.get("claim")
    calculated = None
    note = "Harga belum dapat ditentukan dari teks OCR; perlu dibaca pada poster."

    def plausible_pair(high: int, low: int) -> tuple[float | None, bool]:
        """Allow a missing price role only when a nearby discount claim corroborates it."""
        pct = round((high - low) / high * 100, 1) if high else None
        if pct is None or not (2.0 <= pct <= 80.0):
            return pct, False
        # A plausible difference alone is not evidence that two prices belong
        # to the same SKU. The labelled-label path above is the only path that
        # can make a pair without a discount claim.
        if claim is None or abs(pct - claim) > DISCOUNT_PAIR_MAX_DIFFERENCE_PP:
            return pct, False
        return pct, True

    if normal and promo:
        if normal > promo:
            calculated = round((normal - promo) / normal * 100, 1)
            if claim is None or abs(calculated - claim) <= DISCOUNT_PAIR_MAX_DIFFERENCE_PP:
                note = "Harga normal dan promo dikenali dari label OCR; tetap cocokkan dengan poster."
            else:
                note = "Label harga terbaca, tetapi hitungan tidak cocok dengan klaim diskon; periksa poster."
        else:
            normal = promo = None
            note = "Label/urutan harga konflik; periksa gambar sebelum menyetujui."
    elif len(unique_prices) == 2 and normal in unique_prices and promo is None:
        other = next((price for price in unique_prices if price != normal), None)
        if other is not None and other < normal:
            calculated, ok = plausible_pair(normal, other)
            if ok:
                promo = other
                note = "Harga normal berlabel; harga promo kandidat dari angka lebih rendah di tile yang sama. WAJIB cocokkan poster."
            else:
                calculated = None
                note = "Harga normal berlabel tetapi harga lain tidak cocok dengan klaim/aturan diskon; periksa poster."
        else:
            note = "Harga normal berlabel terdeteksi; harga promo belum jelas."
    elif len(unique_prices) == 2 and promo in unique_prices and normal is None:
        other = next((price for price in unique_prices if price != promo), None)
        if other is not None and other > promo:
            calculated, ok = plausible_pair(other, promo)
            if ok:
                normal = other
                note = "Harga promo berlabel; harga normal kandidat dari angka lebih tinggi di tile yang sama. WAJIB cocokkan poster."
            else:
                calculated = None
                note = "Harga promo berlabel tetapi harga lain tidak cocok dengan klaim/aturan diskon; periksa poster."
        else:
            note = "Harga promo berlabel terdeteksi; harga normal belum jelas."
    elif len(unique_prices) == 2:
        high, low = max(unique_prices), min(unique_prices)
        calculated, ok = plausible_pair(high, low)
        if claim is not None and ok:
            normal, promo = high, low
            note = "Dua harga tanpa label cocok secara matematis dengan klaim diskon pada area lokal; tetap konfirmasi visual."
        elif claim is not None:
            calculated = None
            note = "Dua harga OCR tidak cocok dengan klaim diskon; pasangan dibiarkan kosong."
        else:
            calculated = None
            note = "Dua harga terdeteksi tanpa label normal/promo atau klaim diskon pendukung; pasangan dibiarkan kosong."
    elif normal:
        note = "Harga normal berlabel terdeteksi; harga promo belum jelas."
    elif promo:
        note = "Harga promo berlabel terdeteksi; harga normal belum jelas."
    elif len(unique_prices) > 1:
        note = "Lebih dari dua harga dalam konteks; area mungkin mencakup SKU lain. Pasangan dibiarkan kosong."

    if normal and promo and normal > promo:
        calculated = round((normal - promo) / normal * 100, 1)
    elif normal and promo and promo >= normal:
        normal = promo = None
        calculated = None
        note = "Label/urutan harga konflik; periksa gambar sebelum menyetujui."
    return {
        "prices": unique_prices,
        "normal_price_candidate": normal or "",
        "promo_price_candidate": promo or "",
        "discount_claim_percent": claim if claim is not None else "",
        "discount_calculated_percent": calculated if calculated is not None else "",
        "discount_claim_source": claim_analysis.get("source", "TIDAK_TERDETEKSI"),
        "discount_claim_context": claim_analysis.get("context", ""),
        "ignored_packaging_percentages": claim_analysis.get("ignored", ""),
        "discount_claim_note": claim_analysis.get("note", ""),
        "candidate_note": note,
    }


def _line_center(line: dict) -> tuple[float, float]:
    return ((float(line.get("x0", 0)) + float(line.get("x1", 0))) / 2, (float(line.get("y0", 0)) + float(line.get("y1", 0))) / 2)


def _cluster_price_anchor_lines(lines: list[dict], width: int, height: int) -> list[list[dict]]:
    """Group paired price boxes without transitively swallowing the next SKU.

    A simple connected-component rule used to join three prices in one column
    (e.g. normal/promo for one product and a single price for the next product)
    because each adjacent pair was less than 5.5% of page height apart. This
    version also constrains the total vertical span and rejects implausible price
    ratios before joining two distinct boxes. Ambiguous price rows stay separate.
    """
    anchors = [line for line in _sort_lines(lines) if extract_prices(str(line.get("text") or ""))]
    if not anchors:
        return []
    centers = [_line_center(line) for line in anchors]
    values = [list(dict.fromkeys(item["price"] for item in extract_prices(str(line.get("text") or "")))) for line in anchors]
    parent = list(range(len(anchors)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def component_members(root: int) -> list[int]:
        return [idx for idx in range(len(anchors)) if find(idx) == root]

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        members = component_members(ra) + component_members(rb)
        ys = [centers[idx][1] for idx in members]
        if max(ys) - min(ys) > height * MAX_PRICE_CLUSTER_VERTICAL_SPAN_RATIO:
            return
        # Only pair one-price boxes when their values could plausibly represent
        # normal/promo amounts. Clearly different scales (e.g. 10,000 and 321,600)
        # should not merge just because OCR boxes are nearby. Explicitly labelled
        # normal/promo lines may still group even when the arithmetic is unusual.
        va, vb = values[a], values[b]
        if len(va) == 1 and len(vb) == 1:
            high, low = max(va[0], vb[0]), min(va[0], vb[0])
            diff = (high - low) / high * 100 if high else 0.0
            label_evidence = bool(
                (LABEL_NORMAL_RE.search(str(anchors[a].get("text") or "")) or LABEL_PROMO_RE.search(str(anchors[a].get("text") or "")))
                or (LABEL_NORMAL_RE.search(str(anchors[b].get("text") or "")) or LABEL_PROMO_RE.search(str(anchors[b].get("text") or "")))
            )
            if not label_evidence and not (2.0 <= diff <= 80.0):
                return
        parent[rb] = ra

    # The poster design usually stacks normal/promo price boxes tightly. The
    # total-span cap prevents a chain of adjacent boxes from joining successive
    # products; numeric plausibility disambiguates nearby unrelated prices.
    for i, (x1, y1) in enumerate(centers):
        for j in range(i + 1, len(anchors)):
            x2, y2 = centers[j]
            if abs(x2 - x1) / width <= 0.085 and abs(y2 - y1) / height <= 0.055:
                union(i, j)

    grouped: dict[int, list[dict]] = defaultdict(list)
    for index, line in enumerate(anchors):
        grouped[find(index)].append(line)
    return sorted(
        grouped.values(),
        key=lambda group: (_line_center(group[0])[1], _line_center(group[0])[0]),
    )


def _clean_local_product_line(text: str) -> str:
    """Remove price/promo labels from a single OCR line before treating it as a SKU suggestion."""
    cleaned = str(text or "").strip()
    cleaned = RP_AMOUNT_RE.sub(" ", cleaned)
    cleaned = OCR_MISSING_P_R_AMOUNT_RE.sub(" ", cleaned)
    cleaned = GROUPED_AMOUNT_RE.sub(" ", cleaned)
    cleaned = PLAIN_AMOUNT_RE.sub(" ", cleaned)
    cleaned = DISCOUNT_RE.sub(" ", cleaned)
    cleaned = re.sub(
        r"(?i)\b(?:rp\.?|idr|harga\s*(?:normal|promo|asli|reguler|diskon)|normal\s*price|regular\s*price|promo\s*price|sale\s*price|price\s*after\s*discount)\b",
        " ", cleaned,
    )
    cleaned = re.sub(
        r"(?i)\b(?:setiap\s+belanja(?:\s+produk)?|ada\s+yang\s+baru(?:\s+nih)?|buat\s+kamu|potongan\s+harga|promo\s+eksklusif|min(?:imum)?\s+belanja|berlaku\s+kelipatan)\b",
        " ", cleaned,
    )
    cleaned = re.sub(r"[|,:;_*~=+\-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-/")
    return cleaned


def _horizontal_overlap_ratio(a: dict, b: dict) -> float:
    ax0, ax1 = float(a.get("x0", 0)), float(a.get("x1", 0))
    bx0, bx1 = float(b.get("x0", 0)), float(b.get("x1", 0))
    overlap = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    denom = max(1.0, min(abs(ax1 - ax0), abs(bx1 - bx0)))
    return overlap / denom


def _infer_stacked_price_pair(parsed: dict, lines: list[dict], width: int, height: int) -> dict:
    """Infer a *review candidate* from the stacked old/new-price layout common in promo tiles.

    OCR often reads the smaller struck-through normal price and the larger promo
    price as two independent boxes, without the words "Harga Normal" / "Harga
    Promo" or a discount percentage. Geometry is useful evidence for this
    specific situation, but it is never an approval: the Excel decision remains
    CEK and the note explicitly records that the pair came from layout inference.
    """
    if parsed.get("normal_price_candidate") and parsed.get("promo_price_candidate"):
        return parsed
    values = list(dict.fromkeys(parsed.get("prices") or []))
    if len(values) != 2 or width <= 0 or height <= 0:
        return parsed
    # If the words are already labelling either role, retain the stricter label
    # parser rather than override it with a visual guess.
    context = " ".join(str(line.get("text") or "") for line in lines)
    if LABEL_NORMAL_RE.search(context) or LABEL_PROMO_RE.search(context):
        return parsed

    line_for_price: dict[int, list[dict]] = defaultdict(list)
    for line in lines:
        prices = list(dict.fromkeys(item["price"] for item in extract_prices(str(line.get("text") or ""))))
        # A box containing two prices has no per-price geometry and is therefore
        # not enough evidence to decide which one is the crossed-out price.
        if len(prices) == 1 and prices[0] in values:
            line_for_price[prices[0]].append(line)
    high, low = max(values), min(values)
    if not line_for_price.get(high) or not line_for_price.get(low):
        return parsed

    claim = parsed.get("discount_claim_percent")
    try:
        claim_value = float(claim) if claim not in (None, "") else None
    except (TypeError, ValueError):
        claim_value = None
    calculated = round((high - low) / high * 100, 1) if high else None
    if calculated is None or not 2.0 <= calculated <= 80.0:
        return parsed
    # A printed discount claim within this area is corroborating evidence. If it
    # contradicts the calculated difference, do not infer a price pair.
    if claim_value is not None and abs(calculated - claim_value) > DISCOUNT_PAIR_MAX_DIFFERENCE_PP:
        return parsed

    best = None
    for normal_line in line_for_price[high]:
        for promo_line in line_for_price[low]:
            nx, ny = _line_center(normal_line)
            px, py = _line_center(promo_line)
            y_gap = (py - ny) / height
            center_dx = abs(px - nx) / width
            x0_gap = abs(float(normal_line.get("x0", 0)) - float(promo_line.get("x0", 0))) / width
            overlap = _horizontal_overlap_ratio(normal_line, promo_line)
            # For this poster pattern, normal price is above promo price and
            # the two boxes align in one product card. Horizontal neighbours
            # must not become a normal/promo pair merely because values differ.
            if not (0.004 <= y_gap <= 0.105):
                continue
            # Large price boxes often have different centers despite being
            # clearly overlapping (e.g. 21.400 box vs Rp18.900 box). Trust strong
            # horizontal overlap, but still reject truly side-by-side columns.
            if (center_dx > 0.095 and overlap < 0.35) or (overlap < 0.20 and x0_gap > 0.04):
                continue
            conf = min(_confidence(normal_line.get("confidence")), _confidence(promo_line.get("confidence")))
            quality = overlap - center_dx - abs(y_gap - 0.045) * 0.3 + conf * 0.05
            if best is None or quality > best[0]:
                best = (quality, normal_line, promo_line)
    if best is None:
        return parsed

    parsed = dict(parsed)
    parsed["normal_price_candidate"] = high
    parsed["promo_price_candidate"] = low
    parsed["discount_calculated_percent"] = calculated
    parsed["price_pair_method"] = "INFERENSI_GEOMETRI_SEJAJAR"
    parsed["candidate_note"] = (
        "Pasangan harga kandidat dikenali dari tata letak poster: harga lebih tinggi berada di atas harga lebih rendah "
        "dan kedua kotak sejajar pada area produk yang sama. Bukan bukti pasti harga coret; cocokkan sebelum TERIMA."
        if claim_value is None else
        "Pasangan harga kandidat dikenali dari tata letak poster dan selisihnya cocok dengan klaim diskon. "
        "Tetap cocokkan harga coret pada gambar sebelum TERIMA."
    )
    return parsed


def _local_product_name_suggestion(
    lines: list[dict], center_x: float, center_y: float, width: int, height: int,
    *, max_dx: float = 0.09, max_dy: float = 0.11,
) -> str:
    """Suggest one local SKU name; never concatenate text from different-sized products."""
    generic_only_re = re.compile(
        r"(?i)\b(?:harga|normal|asli|reguler|promo|diskon|discount|hemat|off|"
        r"beli|gratis|voucher|periode|berlaku|katalog|cashback|special|spesial|"
        r"alfamart|alfamidi|indomaret|indogrosir|foodhall|weekend|mingguan|"
        r"oktober|november|desember|januari|februari|maret|april|mei|juni|juli|"
        r"agustus|september|202[0-9]|semua|varian|variant|variants)\b"
    )
    entries = []
    for line in lines:
        raw = str(line.get("text") or "").strip()
        if not raw or extract_prices(raw) or DISCOUNT_RE.search(raw) or NON_SKU_PACKAGING_COPY_RE.search(raw):
            continue
        lx, ly = _line_center(line)
        dx, dy = abs(lx - center_x) / max(1, width), abs(ly - center_y) / max(1, height)
        if dx > max_dx or dy > max_dy:
            continue
        cleaned = _clean_local_product_line(raw)
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9'&./-]*", cleaned)
        alpha_chars = sum(ch.isalpha() for ch in cleaned)
        # Single-token brand/size boxes (e.g. "KANZLER", "PAROTI", "65g")
        # are useful companions to an adjacent product-title line. They are
        # attachable, but only a multi-token line can be the primary anchor.
        attachable_single = len(tokens) == 1 and (
            (alpha_chars >= 3 and not re.search(r"[A-Za-z]\d+[A-Za-z]", cleaned))
            or bool(product_name_size_signature(cleaned))
        )
        if alpha_chars < 5 and not attachable_single:
            continue
        if len(tokens) < 1 or len(tokens) > 10:
            continue
        # A line that contains two distinct sizes often contains two SKU titles
        # merged by OCR. Do not invent a single combined product name.
        sizes = set(product_name_size_signature(cleaned))
        size_numbers = {re.match(r"\d+(?:[.,]\d+)?", value).group(0) for value in sizes if re.match(r"\d+(?:[.,]\d+)?", value)}
        # A single printed pack may appear as "380ml/380g". Treat equal numeric
        # values in alternate units as one equivalent pack size, not two SKUs.
        if len(size_numbers) > 1:
            continue
        if generic_only_re.search(cleaned):
            # Strip generic campaign tokens only when enough product-like terms remain.
            generic_tokens = {
                "harga", "normal", "asli", "reguler", "promo", "diskon", "discount",
                "hemat", "off", "beli", "gratis", "voucher", "periode", "berlaku",
                "katalog", "cashback", "special", "spesial", "alfamart", "alfamidi",
                "indomaret", "indogrosir", "foodhall", "weekend", "mingguan", "semua",
                "varian", "variant", "variants", "setiap", "belanja", "produk", "baru",
                "nih", "buat", "kamu", "potongan", "eksklusif", "minimum", "kelipatan",
            }
            kept = [token for token in tokens if token.casefold() not in generic_tokens]
            if len(kept) < 2:
                continue
            cleaned = " ".join(kept)
        size_bonus = 0.28 if sizes else 0.0
        above_bonus = 0.08 if ly <= center_y + height * 0.015 else 0.0
        score = (min(len(tokens), 7) * 0.13) + (alpha_chars / 90.0) + size_bonus + above_bonus - dx - (dy * 0.75)
        entries.append({"score": score, "text": cleaned, "line": line, "x": lx, "y": ly, "dx": dx, "dy": dy, "sizes": sizes})

    if not entries:
        return ""
    # Do not let a single generic word become the SKU anchor.
    anchor_entries = [item for item in entries if len(re.findall(r"[A-Za-z][A-Za-z0-9'&./-]*", item["text"])) >= 2]
    if not anchor_entries:
        return ""
    anchor_entries.sort(key=lambda item: (-item["score"], item["dy"], item["dx"], len(item["text"])))
    best = anchor_entries[0]
    selected = [best]
    # Product titles in retail posters are often split over 2–4 short OCR boxes
    # (brand, variant, pack size). Grow a spatially connected text block around
    # the strongest title line, but require alignment, tight vertical spacing,
    # non-conflicting sizes, and a bounded token count so neighboring SKUs do not
    # get concatenated into one suggestion.
    max_component_lines = 4
    max_title_tokens = 10
    while len(selected) < max_component_lines:
        possibilities = []
        for item in entries:
            if item in selected or identity_key(item["text"]) in {identity_key(x["text"]) for x in selected}:
                continue
            vertical_gaps = [abs(float(item["y"]) - float(chosen["y"])) / max(1, height) for chosen in selected]
            nearest_gap = min(vertical_gaps, default=1.0)
            if nearest_gap > 0.04:
                continue
            aligned = False
            overlaps = []
            for chosen in selected:
                center_gap = abs(float(item["x"]) - float(chosen["x"])) / max(1, width)
                overlap = _horizontal_overlap_ratio(item["line"], chosen["line"])
                overlaps.append(overlap)
                if center_gap <= 0.04 or overlap >= 0.30:
                    aligned = True
            if not aligned:
                continue
            combined_sizes = set(item["sizes"])
            for chosen in selected:
                combined_sizes |= set(chosen["sizes"])
            combined_size_numbers = {re.match(r"\d+(?:[.,]\d+)?", value).group(0) for value in combined_sizes if re.match(r"\d+(?:[.,]\d+)?", value)}
            if len(combined_size_numbers) > 1:
                continue
            combined_tokens = sum(len(re.findall(r"[A-Za-z][A-Za-z0-9'&./-]*", x["text"])) for x in selected) + len(re.findall(r"[A-Za-z][A-Za-z0-9'&./-]*", item["text"]))
            if combined_tokens > max_title_tokens:
                continue
            # Prefer a box that extends the current title block, not a distant
            # one with merely similar x coordinates.
            quality = max(overlaps, default=0.0) - nearest_gap + (0.05 if item["sizes"] else 0.0)
            possibilities.append((quality, item))
        if not possibilities:
            break
        possibilities.sort(key=lambda pair: (-pair[0], pair[1]["y"], pair[1]["x"]))
        selected.append(possibilities[0][1])
    selected.sort(key=lambda item: (item["y"], item["x"]))
    text_parts = []
    seen = set()
    for item in selected:
        key = identity_key(item["text"])
        if key and key not in seen:
            seen.add(key)
            text_parts.append(item["text"])
    return " ".join(text_parts)


def make_price_windows(lines: list[dict], width: int, height: int) -> list[dict]:
    windows = []
    if not lines or width <= 0 or height <= 0:
        return windows
    sorted_lines = _sort_lines(lines)
    anchor_groups = _cluster_price_anchor_lines(sorted_lines, width, height)
    if not anchor_groups:
        return windows

    tile_centers = []
    for group in anchor_groups:
        points = [_line_center(line) for line in group]
        tile_centers.append((statistics.mean(p[0] for p in points), statistics.mean(p[1] for p in points)))

    for index, group in enumerate(anchor_groups):
        cx, cy = tile_centers[index]
        # Start with a conservative tile around the anchor. If neighbouring
        # price clusters share a poster row/column, bisect the free space between
        # their centers so a window cannot spill into the next SKU card.
        left = cx - width * 0.11
        right = cx + width * 0.11
        # Keep the price-parser context tight: product titles and the next row's
        # quantity banner can be 100–200 px away and must not add extra prices.
        top = cy - height * 0.055
        bottom = cy + height * 0.055
        for other_index, (ox, oy) in enumerate(tile_centers):
            if other_index == index:
                continue
            dx, dy = ox - cx, oy - cy
            ndx, ndy = abs(dx) / width, abs(dy) / height
            if ndy <= 0.095 and ndx > 0.075:
                if dx < 0:
                    left = max(left, (ox + cx) / 2)
                else:
                    right = min(right, (ox + cx) / 2)
            if ndx <= 0.20 and ndy > 0.035:
                if dy < 0:
                    top = max(top, (oy + cy) / 2)
                else:
                    bottom = min(bottom, (oy + cy) / 2)

        neighborhood = []
        for line in sorted_lines:
            lx, ly = _line_center(line)
            if left <= lx <= right and top <= ly <= bottom:
                # Repeated campaign payment/legal footer text is not product metadata.
                if CAMPAIGN_FOOTER_RE.search(str(line.get("text") or "")):
                    continue
                neighborhood.append(line)
        # Include price-anchor lines from this cluster even if their bbox center
        # lands just outside a boundary after rounding/long OCR boxes.
        have = {str(row.get("text") or "") for row in neighborhood}
        for line in group:
            if str(line.get("text") or "") not in have:
                neighborhood.append(line)
        neighborhood = sorted(neighborhood, key=lambda row: (_line_center(row)[1], _line_center(row)[0]))
        context = " | ".join(str(line.get("text") or "").strip() for line in neighborhood if str(line.get("text") or "").strip())
        parsed = interpret_price_pair(context, allow_unlabelled_pair=True)
        # Promo Gantung-style tiles frequently show the crossed-out normal price
        # above the larger promo price without verbal labels. Infer only when
        # two separate OCR boxes are vertically stacked and horizontally aligned.
        parsed = _infer_stacked_price_pair(parsed, neighborhood, width, height)
        if not parsed["prices"]:
            continue
        line_conf = statistics.mean(_confidence(line.get("confidence")) for line in neighborhood) if neighborhood else 0.0
        bucket_x = int(cx / max(1, width * 0.08))
        bucket_y = int(cy / max(1, height * 0.045))

        # Product names are often further above the prices than normal/promo
        # price boxes are from one another. Use a separate text region: same
        # column, bounded by adjacent columns, and (for lower rows) starting
        # just after the previous row's price. This catches a product heading
        # without expanding the price-pair window into neighbouring SKUs.
        name_left = max(0.0, cx - width * 0.16)
        name_right = min(float(width), cx + width * 0.16)
        for other_index, (ox, oy) in enumerate(tile_centers):
            if other_index == index:
                continue
            dx, dy = ox - cx, oy - cy
            ndx, ndy = abs(dx) / width, abs(dy) / height
            if ndy <= 0.08 and ndx > 0.075:
                if dx < 0:
                    name_left = max(name_left, (ox + cx) / 2)
                else:
                    name_right = min(name_right, (ox + cx) / 2)
        name_top = max(0.0, cy - height * 0.19)
        above_same_column = [oy for ox, oy in tile_centers
                             if oy < cy and abs(ox - cx) / width <= 0.20
                             and (cy - oy) / height > 0.06]
        if above_same_column:
            # Start below the immediately preceding price row. A small gap is
            # enough to exclude that row's title while retaining the current SKU
            # name, which can sit noticeably above its own price.
            name_top = max(name_top, max(above_same_column) + height * 0.015)
        name_bottom = min(float(height), cy + height * 0.01)
        name_lines = []
        for line in sorted_lines:
            lx, ly = _line_center(line)
            if name_left <= lx <= name_right and name_top <= ly <= name_bottom:
                # The name-search region is separate from the price region. Do not
                # reintroduce a neighbouring price via product-title context (for
                # example Rp299.900 beside the Lotte Chocopie label).
                if extract_prices(str(line.get("text") or "")):
                    continue
                lx0, lx1 = float(line.get("x0", 0)), float(line.get("x1", 0))
                line_width = max(1.0, lx1 - lx0)
                horizontal_overlap = max(0.0, min(lx1, name_right) - max(lx0, name_left)) / line_width
                if horizontal_overlap < 0.65:
                    continue
                if CAMPAIGN_FOOTER_RE.search(str(line.get("text") or "")):
                    continue
                name_lines.append(line)
        local_name = _local_product_name_suggestion(
            name_lines, cx, cy, width, height, max_dx=0.17, max_dy=0.25
        )
        name_context = " | ".join(str(line.get("text") or "").strip() for line in name_lines if str(line.get("text") or "").strip())
        # Parse prices only from the tight price area. Context tags/conditions may
        # use the tightly bounded product-title area as additional evidence.
        promo_context = classify_promo_context(" | ".join(part for part in (context, name_context) if part))
        display_context = " | ".join(dict.fromkeys(part for part in (context, name_context) if part))
        signature = json.dumps([context.casefold(), bucket_x, bucket_y, parsed["prices"]], ensure_ascii=False, sort_keys=True)
        windows.append({
            # Keep the public context field inclusive for callers/tests/review;
            # price_context is the restricted area used only for amount parsing.
            "context": display_context,
            "price_context": context,
            "display_context": display_context,
            "product_context": name_context,
            "center_x": cx,
            "center_y": cy,
            "image_width": width,
            "image_height": height,
            "local_product_name_suggestion": local_name,
            "ocr_confidence": round(line_conf, 4),
            "price_values": parsed["prices"],
            **{k: parsed[k] for k in ("normal_price_candidate", "promo_price_candidate", "discount_claim_percent", "discount_calculated_percent", "candidate_note")},
            "discount_claim_source": parsed.get("discount_claim_source", "TIDAK_TERDETEKSI"),
            "discount_claim_context": parsed.get("discount_claim_context", ""),
            "ignored_packaging_percentages": parsed.get("ignored_packaging_percentages", ""),
            "discount_claim_note": parsed.get("discount_claim_note", ""),
            "price_pair_method": parsed.get("price_pair_method") or (
                "LABEL_HARGA" if (LABEL_NORMAL_RE.search(context) or LABEL_PROMO_RE.search(context)) and parsed.get("normal_price_candidate") and parsed.get("promo_price_candidate")
                else "HARGA_DAN_KLAIM_DISKON" if parsed.get("normal_price_candidate") and parsed.get("promo_price_candidate")
                else "BELUM_DAPAT_DIPASANGKAN"
            ),
            **promo_context,
            "lines_in_context": neighborhood,
            "lines_in_product_context": name_lines,
            "bucket_x": bucket_x,
            "bucket_y": bucket_y,
            "_signature": signature,
        })

    # One candidate per spatial price cluster. Collapse exact duplicate windows
    # only when they share most of their OCR lines; equal prices alone are not
    # enough evidence that two adjacent product tiles are the same SKU.
    unique = []
    for window in windows:
        this_lines = {str(line.get("text") or "").strip() for line in window.get("lines_in_context", []) if str(line.get("text") or "").strip()}
        duplicate_index = None
        for pos, previous in enumerate(unique):
            if window["price_values"] != previous["price_values"]:
                continue
            prev_lines = {str(line.get("text") or "").strip() for line in previous.get("lines_in_context", []) if str(line.get("text") or "").strip()}
            overlap = len(this_lines & prev_lines) / max(1, min(len(this_lines), len(prev_lines)))
            dx = abs(window["center_x"] - previous["center_x"]) / width
            dy = abs(window["center_y"] - previous["center_y"]) / height
            if overlap >= 0.60 and dx <= 0.055 and dy <= 0.045:
                duplicate_index = pos
                break
        if duplicate_index is None:
            unique.append(window)
        elif len(window.get("lines_in_context", [])) > len(unique[duplicate_index].get("lines_in_context", [])):
            unique[duplicate_index] = window
    return unique


# Terms that are common across unrelated SKUs. These words may describe a
# category, packaging, or campaign but must never establish a SKU match alone.
_SKU_GENERIC_TOKENS = {
    "harga", "normal", "asli", "reguler", "promo", "diskon", "discount", "sale",
    "hemat", "off", "beli", "buy", "get", "gratis", "voucher", "periode", "berlaku",
    "katalog", "cashback", "special", "spesial", "baru", "semua", "varian", "variant",
    "variants", "all", "setiap", "belanja", "produk", "pcs", "pc", "piece", "pieces",
    "sheets", "sheet", "lembar", "buah", "pack", "sachet", "ref", "refill", "isi",
    "ukuran", "size", "ml", "g", "gr", "gram", "kg", "mg", "l", "liter", "litre",
    "x", "dan", "dengan", "untuk", "the", "of", "and", "for", "new", "terbaru",
    "mingguan", "weekend", "online", "terdekat", "termurah", "murah", "pilihan",
    # Common descriptive/category words that cause unrelated names to look similar.
    "facial", "tissue", "tisu", "shampoo", "shampo", "conditioner", "pasta", "gigi",
    "toothpaste", "toothbrush", "whitening", "kerupuk", "crackers", "cracker", "biskuit",
    "biscuits", "biscuit", "wafer", "pants", "pant", "diaper", "diapers", "popok",
    "adult", "baby", "cologne", "body", "lotion", "sabun", "soap", "detergen", "detergent",
    "liquid", "minyak", "goreng", "saus", "sauce", "tomat", "susu", "milk", "drink",
    "drinks", "air", "water", "instant", "mie", "mi", "noodle", "noodles", "good",
    "skin", "premium", "comfort", "fit", "freshness", "family", "jumbo", "large", "small",
}
_SKU_CATEGORY_TOKENS = {
    "facial", "tissue", "tisu", "shampoo", "shampo", "conditioner", "pasta", "gigi",
    "toothpaste", "toothbrush", "kerupuk", "crackers", "cracker", "biskuit", "biscuits",
    "biscuit", "wafer", "pants", "pant", "diaper", "diapers", "popok", "adult", "baby",
    "cologne", "body", "lotion", "sabun", "soap", "detergen", "detergent", "liquid",
    "minyak", "goreng", "saus", "sauce", "tomat", "susu", "milk", "drink", "drinks",
    "air", "water", "instant", "mie", "mi", "noodle", "noodles",
}
_SKU_UNIT_TOKENS = {"ml", "g", "gr", "gram", "kg", "mg", "l", "liter", "litre", "pcs", "pc", "s"}


@lru_cache(maxsize=8192)
def _cached_identity_key(value: str) -> str:
    """Cache repeated catalog-name normalization across OCR windows."""
    return identity_key(value)


@lru_cache(maxsize=8192)
def _sku_match_features(value: str) -> tuple[set[str], set[str], set[str], set[str]]:
    """Extract distinctive SKU tokens while keeping category, variant and size evidence separate."""
    tokens = set(_cached_identity_key(value).split())
    # Quantities and size codes (e.g. M34, L30, 100, 375ml) cannot identify a brand/SKU.
    tokens = {t for t in tokens if not any(ch.isdigit() for ch in t) and len(t) > 1 and t not in _SKU_UNIT_TOKENS}
    categories = tokens & _SKU_CATEGORY_TOKENS
    core = tokens - _SKU_GENERIC_TOKENS - _SKU_CATEGORY_TOKENS
    variants = set(product_name_variant_signature(value))
    sizes = set(product_name_size_signature(value))
    return core, categories, variants, sizes


def _matched_core_tokens(left: set[str], right: set[str]) -> tuple[set[str], set[str]]:
    """Match exact SKU tokens first, then allow conservative OCR typos around them.

    To keep large fallback catalogs responsive, expensive character-level similarity
    is tested only for words with similar lengths and a shared two-character prefix.
    A fuzzy-only match can remain a manual hint but is capped below auto-match level.
    """
    matched_left = left & right
    matched_right = set(matched_left)
    from difflib import SequenceMatcher
    fuzzy_only = not matched_left
    for a in sorted(left - matched_left):
        if len(a) < 5:
            continue
        best = None
        best_ratio = 0.0
        for b in sorted(right - matched_right):
            if len(b) < 5 or a[:2] != b[:2]:
                continue
            if abs(len(a) - len(b)) > max(1, int(min(len(a), len(b)) * 0.12)):
                continue
            ratio = SequenceMatcher(None, a, b, autojunk=False).ratio()
            if ratio > best_ratio:
                best, best_ratio = b, ratio
        if best is not None and best_ratio >= 0.88:
            matched_left.add(a)
            matched_right.add(best)
    if fuzzy_only:
        # Leave the fuzzy pair visible to the caller so it can explain the
        # possible OCR typo, but _matching_score keeps it manual-only.
        return matched_left, matched_right
    return matched_left, matched_right


def _matching_score(product_name: str, context: str) -> tuple[float, str]:
    """Score only distinctive SKU evidence; common categories/sizes alone are insufficient."""
    name_key = _cached_identity_key(product_name)
    text_key = _cached_identity_key(context)
    if not name_key or not text_key:
        return 0.0, "Nama tidak terbaca cukup jelas."

    name_core, name_categories, name_variants, expected_sizes = _sku_match_features(product_name)
    text_core, text_categories, text_variants, seen_sizes = _sku_match_features(context)
    if not name_core or not text_core:
        return 0.0, "Tidak ada token inti produk yang cukup spesifik; SKU tidak direkomendasikan."

    exact_anchor = bool(text_core & name_core)
    shared_query, shared_name = _matched_core_tokens(text_core, name_core)
    if not shared_query:
        return 0.0, "Tidak ada token inti produk yang cocok; SKU tidak direkomendasikan."

    # Explicitly contradicting category, package size, or flavour/formula is a hard stop.
    if name_categories and text_categories and not (name_categories & text_categories):
        return 0.0, "Kategori produk saling bertentangan; SKU tidak direkomendasikan."
    if expected_sizes and seen_sizes and not (expected_sizes & seen_sizes):
        return 0.0, "Ukuran/kemasan SKU berbeda; kandidat tidak direkomendasikan."
    if name_variants and text_variants and not (name_variants & text_variants):
        return 0.0, "Varian rasa/aroma/formula bertentangan; SKU tidak direkomendasikan."

    # F1 penalizes either a candidate with many unmatched identity words or OCR context
    # that appears to contain another product title. Sizes/categories add corroboration,
    # never identity by themselves.
    precision = len(shared_name) / max(1, len(name_core))
    recall = len(shared_query) / max(1, len(text_core))
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    similarity = product_name_similarity(" ".join(sorted(name_core)), " ".join(sorted(text_core)))
    score = 0.70 * f1 + 0.30 * similarity
    note = "Token inti produk cocok."
    if name_categories and text_categories:
        category_overlap = len(name_categories & text_categories) / max(1, len(name_categories | text_categories))
        score += 0.06 * category_overlap
        note += " Kategori produk mendukung kecocokan."
    if expected_sizes and seen_sizes:
        score += 0.08
        note += " Ukuran/kemasan cocok."
    if name_variants and text_variants:
        score += 0.04
        note += " Token varian cocok."
    if not exact_anchor:
        score = min(score, 0.69)
        note += " Hanya cocok melalui koreksi ejaan tanpa token inti persis; petunjuk manual saja."
    if len(shared_query) == 1:
        # A single brand/token can make a useful manual hint, but is rarely enough
        # to auto-fill a catalog SKU unless category and size also corroborate it.
        if not (name_categories & text_categories) or not (expected_sizes & seen_sizes):
            score = min(score, 0.69)
            note += " Hanya satu token inti cocok; gunakan sebagai kandidat manual, bukan auto-match."
    if len(text_variants) > 1:
        score = min(score, 0.78)
        note += " Beberapa varian terdeteksi pada konteks OCR; tidak aman untuk auto-match."
    return round(max(0.0, min(1.0, score)), 4), note


def _rank_product_candidates(
    context: str, products: list[dict], *, limit: int = 10, minimum_score: float = 0.0,
) -> list[tuple[str, float, str]]:
    """Rank eligible catalog names for explicit human selection; never auto-approve a SKU."""
    scores_by_identity: dict[str, tuple[str, float, str]] = {}
    for product in products:
        if str(product.get("product_validation_status") or "eligible").casefold() not in {"eligible", "", "valid"}:
            continue
        name = str(product.get("product_text") or "").strip()
        key = _cached_identity_key(name)
        if not key:
            continue
        score, note = _matching_score(name, context)
        if score < minimum_score:
            continue
        prior = scores_by_identity.get(key)
        if prior is None or score > prior[1]:
            scores_by_identity[key] = (name, score, note)
    ranked = sorted(scores_by_identity.values(), key=lambda item: (-item[1], identity_key(item[0])))
    return ranked[:max(0, limit)]


def _suggest_product(context: str, page_products: list[dict]) -> tuple[str, float, str]:
    """Return a catalog match only when its score is strong and distinguishable."""
    ranked = _rank_product_candidates(context, page_products, limit=1000)
    if not ranked:
        return "", 0.0, "Tidak ada kecocokan katalog yang kuat; periksa saran nama OCR lokal dan poster asli."
    best_name, best_score, best_note = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = best_score - second_score
    if best_score < 0.82:
        return "", best_score, "Skor kecocokan katalog di bawah 0,82; nama SKU tidak diisi otomatis."
    if second_score >= 0.72 and margin < 0.12 and best_score < 0.96:
        return "", best_score, "Beberapa SKU katalog memiliki skor mirip; nama SKU dibiarkan kosong agar tidak salah cocok."
    if margin >= 0.12 or best_score >= 0.96 or len(ranked) == 1:
        return best_name, best_score, best_note + f" Skor {best_score:.2f}; margin ke kandidat kedua {margin:.2f}."
    return "", best_score, "Kecocokan katalog ambigu; perlu pilih nama produk setelah membuka poster."


def _discount_assessment(claim: object, calculated: object, *, tolerance_pp: float = DISCOUNT_REVIEW_MATCH_TOLERANCE_PP) -> tuple[str, float | str]:
    """Compare a discount claim to math; review match tolerance is 1 pp."""
    try:
        claim_value = float(str(claim).replace("%", "").replace(",", ".").strip()) if str(claim or "").strip() else None
    except (TypeError, ValueError):
        claim_value = None
    try:
        calculated_value = float(str(calculated).replace("%", "").replace(",", ".").strip()) if str(calculated or "").strip() else None
    except (TypeError, ValueError):
        calculated_value = None
    if claim_value is None:
        return ("TIDAK ADA KLAIM DISKON", "")
    if calculated_value is None:
        return ("BELUM BISA DIBANDINGKAN", "")
    difference = round(abs(claim_value - calculated_value), 1)
    if difference <= tolerance_pp:
        return (f"COCOK (selisih {difference:.1f} pp)", difference)
    return (f"TIDAK COCOK (selisih {difference:.1f} pp)", difference)


def _candidate_sku_choices(
    context: str, page_products: list[dict], fallback_products: list[dict] | None,
    matched_name: str = "", matched_score: float = 0.0, limit: int = 3,
) -> list[tuple[str, float]]:
    """Return a few plausible catalog SKU options for a dropdown, clearly unverified."""
    # Dropdown suggestions should be useful, not merely vaguely similar. Weak
    # candidates were producing category-only mislinks such as unrelated sauces
    # or baby-care products; users can still choose the explicit no-match option.
    ranked_page = _rank_product_candidates(context, page_products, limit=12, minimum_score=0.62)
    ranked_fallback = _rank_product_candidates(context, fallback_products or [], limit=12, minimum_score=0.68)
    merged: dict[str, tuple[str, float, int]] = {}
    for priority, ranked in ((0, ranked_page), (1, ranked_fallback)):
        for name, score, _note in ranked:
            key = identity_key(name)
            if not key:
                continue
            prior = merged.get(key)
            # Page-specific catalog entries are preferred on ties; better score wins.
            item = (name, score, priority)
            if prior is None or score > prior[1] or (score == prior[1] and priority < prior[2]):
                merged[key] = item
    ordered = sorted(merged.values(), key=lambda item: (-(item[1] + (0.025 if item[2] == 0 else 0.0)), identity_key(item[0])))
    if matched_name:
        key = identity_key(matched_name)
        found = merged.get(key)
        if found:
            ordered = [found] + [item for item in ordered if identity_key(item[0]) != key]
        else:
            ordered.insert(0, (matched_name, float(matched_score or 0.0), 0))
    selected = []
    seen = set()
    for name, score, _priority in ordered:
        key = identity_key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append((name, round(float(score), 3)))
        if len(selected) >= limit:
            break
    return selected


def _is_legacy_generated_review_note(note: str) -> bool:
    text = str(note or "").strip()
    if text.startswith("REVIEW OCR AUTO:"):
        return True
    legacy_prefixes = (
        "Kandidat lokal:", "Lebih dari dua harga dalam konteks", "Harga belum dapat ditentukan",
        "Dua harga terdeteksi", "Pasangan harga lokal cocok", "Dua harga OCR tidak cocok",
        "Harga promo berlabel", "Harga normal berlabel", "Harga normal dan promo dikenali",
        "Label harga terbaca", "Label/urutan harga konflik", "Harga normal berlabel tetapi",
        "Harga promo berlabel tetapi",
    )
    return text.startswith(legacy_prefixes)


def _build_review_note(candidate: dict, old_note: object = "") -> str:
    old = str(old_note or "").strip()
    # Keep user-authored notes but discard stale generated notes from earlier builds.
    marker = " | REVIEW OCR AUTO:"
    manual = old.split(marker, 1)[0].strip() if marker in old else old
    if _is_legacy_generated_review_note(manual):
        manual = ""
    base = str(candidate.get("candidate_note") or "Harga belum dapat ditentukan; buka poster asli.").strip()
    status, _difference = _discount_assessment(candidate.get("discount_claim_percent"), candidate.get("discount_calculated_percent"))
    claim = candidate.get("discount_claim_percent")
    calculated = candidate.get("discount_calculated_percent")
    if status.startswith("TIDAK COCOK"):
        # Override older, looser parser language such as "cocok" when the difference exceeds 1 pp.
        if "cocok" in base.casefold():
            base = "Pasangan harga kandidat perlu diperiksa; validasi diskon yang lebih ketat menemukan ketidaksesuaian."
        base += f" Klaim diskon {claim}% tetapi hasil hitung {calculated}%; {status.lower()}."
    elif status.startswith("COCOK"):
        base += f" Klaim diskon cocok dalam toleransi 1,0 poin persentase ({status.lower()}); tetap konfirmasi visual."
    elif status == "BELUM BISA DIBANDINGKAN":
        base += f" Klaim diskon {claim}% terbaca, tetapi pasangan harga belum lengkap; belum dapat dibandingkan."
    else:
        base += " Klaim diskon tidak terbaca; validasi matematis tidak tersedia."
    auto = "REVIEW OCR AUTO: " + base
    return f"{manual} | {auto}" if manual else auto


def build_candidates(
    image_record: dict,
    lines: list[dict],
    width: int,
    height: int,
    page_products: list[dict],
    fallback_products: list[dict] | None = None,
) -> list[dict]:
    """Create local price candidates; prefer same-article names over catalog suggestions."""
    raw_candidates = []
    for window in make_price_windows(lines, width, height):
        context = window["context"]
        local_suggestion = str(window.get("local_product_name_suggestion") or "").strip()
        # Catalog identity must be grounded in a distinctive local product title.
        # A broad price window may contain neighbouring SKUs (e.g. "Sauce 250g"
        # beside "MC LEWIS Sambal Terasi"). Falling back to that context produced
        # convincing but unrelated suggestions, so ambiguous/generic local titles
        # now stay unlinked and require human review.
        local_core = _sku_match_features(local_suggestion)[0] if local_suggestion else set()
        if local_core:
            match_context = local_suggestion
            suggested, score, match_note = _suggest_product(match_context, page_products)
            if not suggested and fallback_products:
                fallback_name, fallback_score, fallback_note = _suggest_product(match_context, fallback_products)
                if fallback_name and fallback_score >= 0.82:
                    suggested, score = fallback_name, fallback_score
                    match_note = "Kecocokan katalog lintas sumber berdasarkan nama lokal; wajib cek SKU/ukuran. " + fallback_note
            if not suggested:
                match_note = match_note + " Saran OCR lokal tersedia terpisah; itu bukan kecocokan SKU terverifikasi."
            sku_choices = _candidate_sku_choices(local_suggestion, page_products, fallback_products, suggested, score, limit=3)
        else:
            suggested, score = "", 0.0
            match_note = "Identitas produk lokal belum cukup spesifik; pencocokan SKU dinonaktifkan agar tidak mengambil nama produk tetangga. Periksa poster asli."
            sku_choices = []
        discount_status, discount_diff = _discount_assessment(window.get("discount_claim_percent"), window.get("discount_calculated_percent"))
        price_values = window["price_values"]
        seed = "|".join([
            str(image_record.get("image_url") or ""),
            str(window.get("bucket_x")), str(window.get("bucket_y")),
            identity_key(context), ",".join(map(str, price_values)),
        ])
        candidate_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:14].upper()
        raw_candidates.append({
            "candidate_id": candidate_id,
            "candidate_pipeline_version": CANDIDATE_PIPELINE_VERSION,
            "source_name": image_record.get("source_name", ""),
            "campaign_title": image_record.get("campaign_title", ""),
            "article_url": image_record.get("article_url", ""),
            "image_url": image_record.get("image_url", ""),
            "image_alt": image_record.get("image_alt", ""),
            "ocr_snippet": window.get("display_context") or context,
            "price_values_json": json.dumps(price_values, ensure_ascii=False),
            "normal_price_candidate": window["normal_price_candidate"],
            "promo_price_candidate": window["promo_price_candidate"],
            "discount_claim_percent": window["discount_claim_percent"],
            "discount_calculated_percent": window["discount_calculated_percent"],
            "ocr_confidence": window["ocr_confidence"],
            "matched_product_name": suggested,
            "matched_product_score": score,
            "local_product_name_suggestion": local_suggestion,
            "match_note": match_note,
            "candidate_note": window["candidate_note"],
            "catalog_sku_candidate_1": sku_choices[0][0] if len(sku_choices) > 0 else "",
            "catalog_sku_candidate_2": sku_choices[1][0] if len(sku_choices) > 1 else "",
            "catalog_sku_candidate_3": sku_choices[2][0] if len(sku_choices) > 2 else "",
            "catalog_sku_score_1": sku_choices[0][1] if len(sku_choices) > 0 else "",
            "catalog_sku_score_2": sku_choices[1][1] if len(sku_choices) > 1 else "",
            "catalog_sku_score_3": sku_choices[2][1] if len(sku_choices) > 2 else "",
            "discount_validation_status": discount_status,
            "discount_difference_pp": discount_diff,
            "price_pair_method": window.get("price_pair_method", "BELUM_DAPAT_DIPASANGKAN"),
            "promo_context_tags": window.get("promo_context_tags", ""),
            "purchase_condition": window.get("purchase_condition", ""),
            "regional_scope": window.get("regional_scope", ""),
            "member_price_candidate": window.get("member_price_candidate", ""),
            "ignored_packaging_percentages": window.get("ignored_packaging_percentages", ""),
            "discount_claim_source": window.get("discount_claim_source", "TIDAK_TERDETEKSI"),
            "discount_claim_context": window.get("discount_claim_context", ""),
            "_bucket_x": int(window.get("bucket_x", 0)),
            "_bucket_y": int(window.get("bucket_y", 0)),
            "_center_x": float(window.get("center_x", 0)),
            "_center_y": float(window.get("center_y", 0)),
            "_image_width": int(window.get("image_width", 0)),
            "_image_height": int(window.get("image_height", 0)),
            "_context_lines": {str(line.get("text") or "").strip() for line in (list(window.get("lines_in_context", [])) + list(window.get("lines_in_product_context", []))) if str(line.get("text") or "").strip()},
        })

    # OCR produces one window around each price line. Adjacent windows often
    # describe the same offer, e.g. one sees normal+promo and the next sees
    # normal+promo+discount. Merge only spatially adjacent windows with the same
    # price pair; if two distinct product names are confidently recognized, keep
    # them separate to avoid merging two separate SKUs sold at the same price.
    merged = []
    for candidate in raw_candidates:
        values = (candidate.get("price_values_json"), candidate.get("normal_price_candidate"), candidate.get("promo_price_candidate"))
        claim = candidate.get("discount_claim_percent")
        product_key = identity_key(candidate.get("matched_product_name"))
        match_index = None
        for idx, previous in enumerate(merged):
            previous_values = (previous.get("price_values_json"), previous.get("normal_price_candidate"), previous.get("promo_price_candidate"))
            if candidate.get("image_url") != previous.get("image_url") or values != previous_values:
                continue
            current_lines = candidate.get("_context_lines", set())
            previous_lines = previous.get("_context_lines", set())
            line_overlap = len(current_lines & previous_lines) / max(1, min(len(current_lines), len(previous_lines)))
            image_width = max(1, int(candidate.get("_image_width") or previous.get("_image_width") or 1))
            image_height = max(1, int(candidate.get("_image_height") or previous.get("_image_height") or 1))
            center_dx = abs(float(candidate.get("_center_x") or 0) - float(previous.get("_center_x") or 0)) / image_width
            center_dy = abs(float(candidate.get("_center_y") or 0) - float(previous.get("_center_y") or 0)) / image_height
            if line_overlap < 0.60 or center_dx > 0.055 or center_dy > 0.045:
                continue
            previous_product_key = identity_key(previous.get("matched_product_name"))
            if product_key and previous_product_key and product_key != previous_product_key:
                continue
            local_key = identity_key(candidate.get("local_product_name_suggestion"))
            previous_local_key = identity_key(previous.get("local_product_name_suggestion"))
            if local_key and previous_local_key and local_key != previous_local_key:
                continue
            previous_claim = previous.get("discount_claim_percent")
            if claim not in (None, "") and previous_claim not in (None, "") and str(claim) != str(previous_claim):
                continue
            match_index = idx
            break
        if match_index is None:
            merged.append(candidate)
            continue
        previous = merged[match_index]
        # Keep the strongest product match and OCR confidence, but retain the
        # richer context/discount clue from the other adjacent window.
        if (float(candidate.get("matched_product_score") or 0), float(candidate.get("ocr_confidence") or 0)) > (float(previous.get("matched_product_score") or 0), float(previous.get("ocr_confidence") or 0)):
            chosen, other = candidate, previous
        else:
            chosen, other = previous, candidate
        chosen = dict(chosen)
        combined_lines = chosen.get("_context_lines", set()) | other.get("_context_lines", set())
        chosen["ocr_snippet"] = " | ".join(sorted(combined_lines)) or chosen.get("ocr_snippet", "")
        chosen["_context_lines"] = combined_lines
        if chosen.get("discount_claim_percent") in (None, "") and other.get("discount_claim_percent") not in (None, ""):
            chosen["discount_claim_percent"] = other["discount_claim_percent"]
            chosen["discount_calculated_percent"] = other.get("discount_calculated_percent")
            chosen["discount_claim_source"] = other.get("discount_claim_source", chosen.get("discount_claim_source", ""))
            chosen["discount_claim_context"] = other.get("discount_claim_context", chosen.get("discount_claim_context", ""))
        if not chosen.get("ignored_packaging_percentages") and other.get("ignored_packaging_percentages"):
            chosen["ignored_packaging_percentages"] = other["ignored_packaging_percentages"]
        # Union classifier metadata from both overlapping OCR windows, but preserve
        # its role as review hints rather than activating any offer automatically.
        for field in ("promo_context_tags",):
            combined = list(dict.fromkeys(
                part.strip() for source in (chosen.get(field, ""), other.get(field, ""))
                for part in str(source or "").split("; ") if part.strip()
            ))
            if combined:
                chosen[field] = "; ".join(combined)
        for field in ("purchase_condition", "regional_scope", "member_price_candidate"):
            if chosen.get(field) in (None, "") and other.get(field) not in (None, ""):
                chosen[field] = other[field]
        if chosen.get("price_pair_method") in (None, "", "BELUM_DAPAT_DIPASANGKAN") and other.get("price_pair_method"):
            chosen["price_pair_method"] = other["price_pair_method"]
        if not chosen.get("local_product_name_suggestion") and other.get("local_product_name_suggestion"):
            chosen["local_product_name_suggestion"] = other["local_product_name_suggestion"]
        if chosen.get("candidate_note") and other.get("candidate_note") and chosen.get("candidate_note") != other.get("candidate_note"):
            chosen["candidate_note"] = chosen.get("candidate_note") + " Window OCR bertumpang tindih digabung; tetap periksa poster."
        # Preserve the union of SKU dropdown candidates across overlapping windows.
        options = []
        for source in (chosen, other):
            for n in range(1, 4):
                name = str(source.get(f"catalog_sku_candidate_{n}") or "").strip()
                score_value = source.get(f"catalog_sku_score_{n}")
                try:
                    score_value = float(score_value)
                except (TypeError, ValueError):
                    score_value = 0.0
                if name:
                    options.append((name, score_value))
        unique_options = {}
        for name, option_score in options:
            key = identity_key(name)
            if key and (key not in unique_options or option_score > unique_options[key][1]):
                unique_options[key] = (name, option_score)
        ranked_options = sorted(unique_options.values(), key=lambda x: (-x[1], identity_key(x[0])))[:3]
        for n in range(1, 4):
            chosen[f"catalog_sku_candidate_{n}"] = ranked_options[n-1][0] if len(ranked_options) >= n else ""
            chosen[f"catalog_sku_score_{n}"] = ranked_options[n-1][1] if len(ranked_options) >= n else ""
        discount_status, discount_diff = _discount_assessment(chosen.get("discount_claim_percent"), chosen.get("discount_calculated_percent"))
        chosen["discount_validation_status"] = discount_status
        chosen["discount_difference_pp"] = discount_diff
        merged[match_index] = chosen

    for candidate in merged:
        candidate.pop("_bucket_x", None)
        candidate.pop("_bucket_y", None)
        candidate.pop("_center_x", None)
        candidate.pop("_center_y", None)
        candidate.pop("_image_width", None)
        candidate.pop("_image_height", None)
        candidate.pop("_context_lines", None)
    return merged


def _download_image(session: requests.Session, image_url: str) -> tuple[bytes, str]:
    if not _safe_image_url(image_url):
        raise ValueError("URL gambar tidak aman/tidak valid")
    with session.get(image_url, timeout=REQUEST_TIMEOUT, stream=True, headers={"User-Agent": USER_AGENT}) as response:
        response.raise_for_status()
        ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype and not ctype.startswith("image/"):
            raise ValueError(f"URL tidak mengembalikan konten gambar ({ctype})")
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_IMAGE_BYTES:
                raise ValueError("Gambar melebihi batas 20 MB")
            chunks.append(chunk)
    body = b"".join(chunks)
    if not body:
        raise ValueError("Gambar kosong")
    return body, hashlib.sha256(body).hexdigest()


def _line_records(cache_row: dict) -> list[dict]:
    try:
        lines = json.loads(cache_row.get("lines_json") or "[]")
        return lines if isinstance(lines, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _is_current_ocr_cache(cache_row: dict) -> bool:
    """Only count a cache row as usable for a complete review if it is current and valid."""
    if not cache_row or str(cache_row.get("ocr_pipeline_version") or "") != OCR_PIPELINE_VERSION:
        return False
    status = str(cache_row.get("ocr_status") or "").strip().casefold()
    if status == "ok":
        return bool(_line_records(cache_row))
    if status == "no_text":
        # Empty OCR is still a valid result when the active pipeline recorded it explicitly.
        raw_lines = cache_row.get("lines_json")
        try:
            decoded = json.loads(raw_lines) if raw_lines not in (None, "") else None
        except (json.JSONDecodeError, TypeError):
            return False
        return isinstance(decoded, list) and not decoded
    return False


def _cache_coverage(images: list[dict], cache: dict[str, dict]) -> tuple[int, int]:
    """Return (current usable cached images, indexed images missing/stale) for this index."""
    usable = sum(
        _is_current_ocr_cache(cache.get(str(record.get("image_url") or ""), {}))
        for record in images
    )
    return usable, max(0, len(images) - usable)


def _load_old_review(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
        if "Kandidat OCR" not in wb.sheetnames:
            wb.close(); return {}
        ws = wb["Kandidat OCR"]
        headers = [str(c or "").strip() for c in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
        output = {}
        for values in ws.iter_rows(min_row=2, values_only=True):
            row = dict(zip(headers, values))
            cid = str(row.get("ID Kandidat") or "").strip()
            if cid:
                output[cid] = row
        wb.close()
        return output
    except Exception:
        return {}


def _review_price_key(value: object) -> str:
    """Normalize Excel int/float/string prices so legacy auto-fill can be recognized."""
    parsed = parse_idr_price(value)
    if parsed is not None:
        return str(parsed)
    return str(value or "").strip()


def _review_pair_fingerprint(row: dict) -> str:
    """Stable, conservative fallback key when context changes candidate IDs.

    The image URL and both prices must be present. Candidate rows with only one
    price, or repeated identical price pairs on the same poster, are not safe to
    migrate by fingerprint alone.
    """
    image_url = str(row.get("URL Gambar") or row.get("image_url") or "").strip()
    if not image_url:
        return ""
    normal = row.get("Harga Normal Kandidat (Rp)", row.get("normal_price_candidate"))
    promo = row.get("Harga Promo Kandidat (Rp)", row.get("promo_price_candidate"))
    normal_key, promo_key = _review_price_key(normal), _review_price_key(promo)
    if not normal_key or not promo_key:
        return ""
    return f"{image_url}|{normal_key}|{promo_key}"


def _review_product_context_compatible(old_row: dict, candidate: dict) -> bool:
    """Require product-name continuity before migrating a manual decision by fallback key.

    This deliberately blocks the dangerous case where a parser fix moves an old
    price pair from a neighbouring title (e.g. Sambal) to the true product (CHIL-KID).
    Exact candidate IDs are still the strongest match; this guard is only for the
    fallback used when IDs changed after a pipeline upgrade.
    """
    old_name = str(
        old_row.get("Saran Nama Produk (OCR Lokal)")
        or old_row.get("Nama Produk Saran")
        or ""
    ).strip()
    new_name = str(
        candidate.get("local_product_name_suggestion")
        or candidate.get("matched_product_name")
        or ""
    ).strip()
    if not old_name or not new_name:
        return False
    if identity_key(old_name) == identity_key(new_name):
        return True
    if product_name_identity_warnings(old_name, new_name):
        return False
    return product_name_similarity(old_name, new_name) >= 0.68


def make_review_workbook(candidates: list[dict], path: Path = REVIEW_XLSX, prior: dict[str, dict] | None = None) -> None:
    prior = prior or {}
    path.parent.mkdir(parents=True, exist_ok=True)
    # The candidate ID includes spatial/context information and can legitimately
    # change after a parser upgrade. Carry prior human decisions only when the
    # same image plus both price values identifies exactly one row in old and new
    # workbooks. Never carry by a one-price or duplicated-price fingerprint.
    old_fp_rows: dict[str, list[dict]] = {}
    for old_row in prior.values():
        fingerprint = _review_pair_fingerprint(old_row)
        if fingerprint:
            old_fp_rows.setdefault(fingerprint, []).append(old_row)
    new_fp_counts: dict[str, int] = {}
    for candidate in candidates:
        fingerprint = _review_pair_fingerprint(candidate)
        if fingerprint:
            new_fp_counts[fingerprint] = new_fp_counts.get(fingerprint, 0) + 1
    wb = Workbook()
    ws = wb.active
    ws.title = "Kandidat OCR"
    ws.append(REVIEW_HEADERS)
    for candidate in candidates:
        cid = candidate["candidate_id"]
        old = prior.get(cid, {})
        if not old:
            fingerprint = _review_pair_fingerprint(candidate)
            matches = old_fp_rows.get(fingerprint, []) if fingerprint else []
            if (fingerprint and len(matches) == 1 and new_fp_counts.get(fingerprint) == 1
                    and _review_product_context_compatible(matches[0], candidate)):
                old = matches[0]
        # Keep the catalog match and local OCR title suggestion separate.
        # Old unreviewed workbooks often copied the same OCR guess into both
        # fields; discard that duplicate auto-fill but preserve explicit edits
        # and all TERIMA/TOLAK decisions.
        catalog_suggestion = str(candidate.get("matched_product_name") or "").strip()
        local_suggestion = str(candidate.get("local_product_name_suggestion") or "").strip()
        if catalog_suggestion and local_suggestion and identity_key(catalog_suggestion) == identity_key(local_suggestion):
            local_suggestion = ""
        decision = str(old.get("Keputusan") or "CEK").strip().upper()
        if decision not in {"CEK", "TERIMA", "TOLAK"}:
            decision = "CEK"
        old_product = str(old.get("Nama Produk untuk Review") or old.get("Nama Produk Disetujui") or "").strip()
        old_suggestion = str(old.get("Saran Nama Produk (OCR Lokal)") or old.get("Nama Produk Saran") or "").strip()
        if decision == "CEK" and old_product and old_suggestion and identity_key(old_product) == identity_key(old_suggestion):
            # It was auto-filled by the previous version, not a deliberate correction.
            old_product = ""
        product_for_review = old_product or catalog_suggestion

        normal_for_review = old.get("Harga Normal untuk Review (Rp)")
        if normal_for_review in (None, ""):
            normal_for_review = old.get("Harga Normal Disetujui (Rp)")
        old_normal_candidate = old.get("Harga Normal Kandidat (Rp)")
        if decision == "CEK" and normal_for_review not in (None, "") and old_normal_candidate not in (None, "") and _review_price_key(normal_for_review) == _review_price_key(old_normal_candidate):
            # Previous build auto-copied its guess into review inputs. If the
            # stricter parser no longer proposes it, remove the stale guess.
            if candidate.get("normal_price_candidate") in (None, ""):
                normal_for_review = ""
        if normal_for_review in (None, ""):
            normal_for_review = candidate.get("normal_price_candidate") or ""

        promo_for_review = old.get("Harga Promo untuk Review (Rp)")
        if promo_for_review in (None, ""):
            promo_for_review = old.get("Harga Promo Disetujui (Rp)")
        old_promo_candidate = old.get("Harga Promo Kandidat (Rp)")
        if decision == "CEK" and promo_for_review not in (None, "") and old_promo_candidate not in (None, "") and _review_price_key(promo_for_review) == _review_price_key(old_promo_candidate):
            if candidate.get("promo_price_candidate") in (None, ""):
                promo_for_review = ""
        if promo_for_review in (None, ""):
            promo_for_review = candidate.get("promo_price_candidate") or ""
        discount_status, discount_difference = _discount_assessment(
            candidate.get("discount_claim_percent"), candidate.get("discount_calculated_percent")
        )
        provided_status = str(candidate.get("discount_validation_status") or "").strip()
        provided_difference = candidate.get("discount_difference_pp")
        if provided_status:
            discount_status = provided_status
        if provided_difference not in (None, ""):
            discount_difference = provided_difference
        row_values = [
            cid, decision, product_for_review, local_suggestion,
            normal_for_review, promo_for_review,
            candidate.get("normal_price_candidate") or "", candidate.get("promo_price_candidate") or "",
            candidate.get("discount_claim_percent") or "", candidate.get("discount_calculated_percent") or "",
            candidate.get("match_note", ""), candidate.get("ocr_confidence", ""), candidate.get("ocr_snippet", ""),
            candidate.get("source_name", ""), candidate.get("campaign_title", ""), candidate.get("article_url", ""),
            candidate.get("image_url", ""), _build_review_note(candidate, old.get("Catatan Review")),
            candidate.get("catalog_sku_candidate_1", ""), candidate.get("catalog_sku_candidate_2", ""), candidate.get("catalog_sku_candidate_3", ""), NO_MATCH_SKU_SENTINEL,
            candidate.get("catalog_sku_score_1", ""), candidate.get("catalog_sku_score_2", ""), candidate.get("catalog_sku_score_3", ""),
            discount_status, discount_difference,
            candidate.get("price_pair_method", ""), candidate.get("promo_context_tags", ""),
            candidate.get("purchase_condition", ""), candidate.get("regional_scope", ""),
            candidate.get("member_price_candidate") or "", candidate.get("ignored_packaging_percentages", ""),
            candidate.get("discount_claim_source", ""), candidate.get("discount_claim_context", ""),
        ]
        ws.append(row_values)
        # Make both provenance URLs clickable in Excel so reviewers can open the
        # original poster/article directly instead of copying long URLs manually.
        current_row = ws.max_row
        for column in (16, 17):
            cell = ws.cell(current_row, column)
            url = str(cell.value or "").strip()
            if url.startswith(("http://", "https://")):
                cell.hyperlink = url
                cell.style = "Hyperlink"
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False
    ws.row_dimensions[1].height = 42
    widths = [15, 13, 38, 38, 22, 22, 20, 20, 16, 16, 45, 14, 76, 22, 48, 60, 70, 88, 38, 38, 38, 32, 13, 13, 13, 32, 20, 28, 58, 34, 32, 22, 58, 30, 58]
    for idx, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="244062")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        for idx in (5, 6, 7, 8):
            row[idx - 1].number_format = '#,##0'
        for idx in (9, 10):
            row[idx - 1].number_format = '0.0"%"'
        for idx in (23, 24, 25):
            row[idx - 1].number_format = '0.000'
        row[26].number_format = '0.0'
        # Yellow = editable review input; grey = OCR-generated candidate only.
        for idx in (2, 3, 5, 6, 18):
            row[idx - 1].fill = PatternFill("solid", fgColor="FFF2CC")
        for idx in (4, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35):
            row[idx - 1].fill = PatternFill("solid", fgColor="E7E6E6")
        status_value = str(row[25].value or "")
        if status_value.startswith("TIDAK COCOK"):
            row[25].fill = PatternFill("solid", fgColor="F4CCCC")
        elif status_value.startswith("COCOK"):
            row[25].fill = PatternFill("solid", fgColor="D9EAD3")
        elif status_value == "BELUM BISA DIBANDINGKAN":
            row[25].fill = PatternFill("solid", fgColor="FCE5CD")
    ws["B1"].comment = Comment(
        "CEK = belum disetujui. Hanya TERIMA yang mengaktifkan nilai pada kolom review; TOLAK menutup kandidat.",
        "Merchants Promo",
    )
    ws["C1"].comment = Comment(
        "Nama ini diprefill hanya jika ditemukan kecocokan katalog yang cukup kuat dan tidak ambigu. Jika kosong, isi nama SKU yang benar setelah membuka poster asli.",
        "Merchants Promo",
    )
    ws["D1"].comment = Comment(
        "Saran dari teks OCR lokal pada area harga. Ini bukan kecocokan katalog terverifikasi; jangan mengandalkannya tanpa melihat poster.",
        "Merchants Promo",
    )
    ws["E1"].comment = Comment(
        "Nilai awal adalah kandidat OCR, bukan persetujuan. Pasangan tanpa label/klaim hanya diusulkan bila dua kotak harga terpisah bertumpuk dan sejajar secara spasial; tetap cek bukti poster sebelum TERIMA.",
        "Merchants Promo",
    )
    ws["F1"].comment = Comment(
        "Nilai awal disalin dari kandidat OCR untuk memudahkan review. Ini bukan persetujuan; cek poster dan pilih TERIMA setelah yakin.",
        "Merchants Promo",
    )
    ws["V1"].comment = Comment(
        "Pilihan eksplisit jika tiga kandidat SKU tidak cocok. Jika dipilih, keputusan TERIMA akan ditolak importer sampai nama produk diisi dengan SKU yang benar. Jangan memilih kandidat hanya karena kategori atau ukuran serupa.",
        "Merchants Promo",
    )
    decision_validation = DataValidation(type="list", formula1='"CEK,TERIMA,TOLAK"', allow_blank=False)
    decision_validation.error = "Pilih CEK, TERIMA, atau TOLAK."
    decision_validation.errorTitle = "Keputusan OCR tidak valid"
    ws.add_data_validation(decision_validation)
    if ws.max_row >= 2:
        decision_validation.add(f"B2:B{ws.max_row}")
    # The SKU field has a row-specific drop-down of the top three candidate names.
    # Information-style errors still allow a manually typed SKU when all choices are poor.
    sku_validation = DataValidation(type="list", formula1="=$S2:$V2", allow_blank=True)
    sku_validation.error = "Pilih kandidat SKU, pilih Tidak ada SKU yang cocok, atau masukkan nama SKU yang telah diverifikasi."
    sku_validation.errorTitle = "Pilihan SKU untuk review"
    sku_validation.prompt = "Jika semua opsi salah, pilih Tidak ada SKU yang cocok. Untuk manual, ketik nama SKU yang telah diperiksa pada poster."
    sku_validation.promptTitle = "Kecocokan SKU belum disetujui"
    sku_validation.showInputMessage = True
    sku_validation.showErrorMessage = True
    sku_validation.errorStyle = "information"
    ws.add_data_validation(sku_validation)
    if ws.max_row >= 2:
        sku_validation.add(f"C2:C{ws.max_row}")

    helpws = wb.create_sheet("Petunjuk")
    tips = [
        ["REVIEW HARGA DARI POSTER PROMO (OCR LOKAL)"],
        ["1. Buka URL Gambar untuk melihat poster asli; OCR dapat salah membaca digit atau menggabungkan dua SKU."],
        ["2. Nama Produk untuk Review hanya diprefill jika ada kecocokan katalog yang kuat dan tidak ambigu. Saran OCR lokal bukan SKU terverifikasi dan bukan persetujuan harga; cek merek, ukuran, dan varian."],
        ["3. Harga Normal/Promo untuk Review adalah kandidat yang boleh diedit, BUKAN persetujuan. Hanya keputusan TERIMA mengaktifkan nilai setelah validasi importer."],
        ["4. Metode Pasangan Harga menjelaskan apakah pasangan berasal dari label harga, konteks matematika, atau inferensi geometri. Semua pasangan yang diinferensikan tetap perlu dicek visual."],
        ["5. Validasi Klaim Diskon memakai toleransi COCOK maksimal 1,0 poin persentase. TIDAK COCOK atau KLAIM DISKON AMBIGU wajib ditinjau manual."],
        ["6. Persentase kemasan seperti '30% lebih banyak sereal' dikecualikan dari klaim diskon harga. Kolom Persentase Kemasan Diabaikan dan Teks Klaim Diskon yang Dipakai membantu audit alasan parser."],
        ["7. Crop OCR warna merah/kuning adalah jalur rescue lokal untuk label harga yang luput dari OCR halaman penuh; harga crop hanya masuk sebagai teks kandidat dan bukan persetujuan."],
        ["8. Konteks promo, syarat pembelian, wilayah, dan harga member adalah petunjuk hasil aturan lokal. Jangan menganggap teks footer kampanye berlaku untuk SKU tertentu tanpa bukti poster."],
        ["9. Syarat pembelian mendeteksi teks Beli N dan kuantitas pada harga seperti /2Pcs atau /5Pcs. Ini petunjuk basis paket, bukan persetujuan; pastikan SKU dan jumlahnya cocok secara visual."],
        ["10. Untuk promo gratis, harga member, harga spesial, atau cakupan wilayah, pastikan syarat dan basis harga (satuan/paket) dibaca benar sebelum TERIMA."],
        ["10. Jika harga normal dicoret dan harga promo tidak berlabel, pastikan kedua kotak harga benar-benar berada di area produk yang sama."],
        ["11. Setelah menyimpan dan menutup Excel, jalankan: python engine\\promo_poster_ocr.py --import-reviewed-only"],
        ["12. Lalu perbarui katalog: python engine\\enrich_existing_promo_catalog_multisource.py. Harga dari poster hanya dipakai setelah TERIMA."],
        ["13. Tanpa --force, gambar dengan cache versi pipeline terkini tidak diproses ulang. Upgrade ini mengubah versi OCR sehingga cache lama akan diproses ulang sekali; gunakan --limit 10/25 untuk menguji batch kecil lebih dahulu."],
    ]
    for tip in tips:
        helpws.append(tip)
    helpws.column_dimensions["A"].width = 150
    for row in helpws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if cell.row == 1:
                cell.font = Font(bold=True, size=13, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="244062")
    wb.save(path)


def _load_image_index(path: Path = IMAGE_INDEX) -> list[dict]:
    rows = _read_csv(path)
    deduped = {}
    for row in rows:
        url = str(row.get("image_url") or "").strip()
        if not url or not _safe_image_url(url):
            continue
        if url not in deduped:
            deduped[url] = row
        else:
            # Keep richer campaign metadata if duplicated image URL appears under another article.
            current = deduped[url]
            if not current.get("article_url") and row.get("article_url"):
                deduped[url] = row
    return list(deduped.values())


def _load_page_products(path: Path = PRODUCTS_CSV) -> dict[str, list[dict]]:
    by_url = defaultdict(list)
    for row in _read_csv(path):
        urls = {row.get("source_url"), row.get("source_page_url")}
        for url in urls:
            key = _canonical_article_url(url)
            if key:
                by_url[key].append(row)
    return by_url


def _load_catalog_products(path: Path = PRODUCTS_CSV) -> list[dict]:
    """Load eligible SKU names only; their prices are never treated as OCR evidence."""
    output = []
    for row in _read_csv(path):
        status = str(row.get("product_validation_status") or "eligible").casefold()
        product = str(row.get("product_text") or "").strip()
        if status not in {"eligible", "", "valid"} or not product:
            continue
        output.append({
            "product_text": product,
            "product_validation_status": status,
            "merchant_name": str(row.get("merchant_name") or ""),
            "source_name": str(row.get("source_name") or ""),
        })
    seen, unique = set(), []
    for row in output:
        key = identity_key(row["product_text"])
        if key and key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def _catalog_fallback_for_campaign(catalog: list[dict], campaign_title: str) -> list[dict]:
    """Return possible names from the retailer named in the campaign title."""
    campaign_key = identity_key(campaign_title)
    if not campaign_key:
        return []
    campaign_tokens = set(campaign_key.split())
    merchant_keys = {
        identity_key(row.get("merchant_name"))
        for row in catalog
        if identity_key(row.get("merchant_name"))
    }
    matched = {key for key in merchant_keys if key in campaign_tokens}
    if not matched:
        return []
    return [row for row in catalog if identity_key(row.get("merchant_name")) in matched]


def rebuild_review_from_cache(limit: int = 0) -> dict:
    """Rebuild review from current-version OCR caches without risking a partial main workbook."""
    all_images = _load_image_index(IMAGE_INDEX)
    if not all_images:
        raise FileNotFoundError(f"Daftar gambar poster tidak ditemukan/kosong: {IMAGE_INDEX}. Jalankan collector terlebih dahulu.")
    old_cache = {str(r.get("image_url") or ""): r for r in _read_csv(CACHE_CSV)}
    current_cache_count, missing_or_stale_count = _cache_coverage(all_images, old_cache)
    review_path = REVIEW_XLSX
    lines_path = LINES_CSV
    candidates_path = CANDIDATES_CSV

    # A limited rebuild is always a sandbox preview based only on valid current-version OCR caches.
    # A full rebuild is allowed only when every indexed image has a valid result from this OCR version.
    current_cached_images = [
        record for record in all_images
        if _is_current_ocr_cache(old_cache.get(str(record.get("image_url") or ""), {}))
    ]
    if limit > 0:
        def _processed_timestamp(record: dict) -> float:
            raw = str(old_cache.get(str(record.get("image_url") or ""), {}).get("processed_at") or "")
            try:
                value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if value.tzinfo is None:
                    value = value.astimezone()
                return value.timestamp()
            except (TypeError, ValueError, OverflowError):
                return 0.0

        images = sorted(current_cached_images, key=_processed_timestamp, reverse=True)[:limit]
        if not images:
            raise RuntimeError(
                "Belum ada cache OCR valid untuk versi " + OCR_PIPELINE_VERSION + ". "
                "Jalankan `python engine\\promo_poster_ocr.py --limit 10` terlebih dahulu."
            )
        review_path = REVIEW_XLSX.with_name("Review_Harga_Poster_OCR_UjiTerbaru.xlsx")
        lines_path = LINES_CSV.with_name("promo_poster_ocr_lines_review_latest.csv")
        candidates_path = CANDIDATES_CSV.with_name("promo_poster_price_candidates_review_latest.csv")
        print(
            f"Mode review uji: memakai {len(images)} cache OCR valid terbaru; "
            "tidak mengunduh/OCR ulang dan tidak menimpa review utama.", flush=True
        )
    else:
        if missing_or_stale_count:
            coverage_pct = 100.0 * current_cache_count / max(1, len(all_images))
            raise RuntimeError(
                f"Cache OCR belum lengkap: {current_cache_count}/{len(all_images)} gambar "
                f"({coverage_pct:.1f}%) memiliki hasil valid versi {OCR_PIPELINE_VERSION}; "
                f"{missing_or_stale_count} gambar belum ada cache aktif atau masih memakai versi lama/gagal. "
                "Review utama tidak diubah. Jalankan OCR inkremental bertahap `python engine\\promo_poster_ocr.py --limit 25` "
                "sampai cakupan cache lengkap, lalu ulangi `--rebuild-review-only`. Untuk meninjau sebagian cache, "
                "gunakan `--rebuild-review-only --limit 10`."
            )
        images = all_images
        print("Mode review saja: seluruh indeks memiliki cache OCR aktif; tidak mengunduh gambar atau menjalankan OCR.", flush=True)

    line_rows = []
    candidates = []
    page_products = _load_page_products(PRODUCTS_CSV)
    catalog_products = _load_catalog_products(PRODUCTS_CSV)
    for image_record in images:
        cache_row = old_cache[str(image_record.get("image_url") or "")]
        lines = _line_records(cache_row)
        try:
            width = int(cache_row.get("image_width") or 0)
            height = int(cache_row.get("image_height") or 0)
        except (TypeError, ValueError):
            width = height = 0
        stamp = cache_row.get("processed_at") or ""
        for n, line in enumerate(_sort_lines(lines), 1):
            line_rows.append({
                "source_name": image_record.get("source_name", ""), "campaign_title": image_record.get("campaign_title", ""),
                "article_url": image_record.get("article_url", ""), "image_url": image_record.get("image_url", ""),
                "image_alt": image_record.get("image_alt", ""), "period_status": image_record.get("period_status", ""),
                "line_no": n, "ocr_text": line.get("text", ""), "ocr_confidence": line.get("confidence", ""),
                "x0": line.get("x0", ""), "y0": line.get("y0", ""), "x1": line.get("x1", ""), "y1": line.get("y1", ""),
                "image_width": width, "image_height": height, "processed_at": stamp,
                "recognition_method": line.get("recognition_method", "FULL_PAGE"),
            })
        product_rows = page_products.get(_canonical_article_url(image_record.get("article_url")), [])
        fallback_rows = _catalog_fallback_for_campaign(catalog_products, str(image_record.get("campaign_title") or ""))
        candidates.extend(build_candidates(image_record, lines, width, height, product_rows, fallback_rows))

    _write_csv(lines_path, line_rows, LINE_FIELDS)
    candidate_map = {candidate["candidate_id"]: candidate for candidate in candidates}
    candidates = sorted(candidate_map.values(), key=lambda row: (row.get("source_name", ""), row.get("campaign_title", ""), row.get("candidate_id", "")))
    _write_csv(candidates_path, candidates, CANDIDATE_FIELDS)
    prior = _load_old_review(review_path)
    make_review_workbook(candidates, review_path, prior)
    if limit <= 0:
        import_stats = import_reviewed_prices(REVIEW_XLSX, OVERRIDES_CSV)
    else:
        import_stats = {"active_overrides": "tidak disentuh (mode uji terpisah)"}
    stats = {
        "images_indexed": len(all_images), "images_considered": len(images),
        "images_processed": 0, "images_cached": len(images),
        "cache_usable_images": current_cache_count,
        "cache_missing_or_stale_images": missing_or_stale_count,
        "cache_coverage_percent": round(100.0 * current_cache_count / max(1, len(all_images)), 1),
        "ocr_ok": 0, "ocr_failed_or_empty": 0, "ocr_backend": "cache-reuse",
        "line_count": len(line_rows), "candidate_count": len(candidates),
        "candidate_price_pair_count": sum(bool(r.get("normal_price_candidate")) and bool(r.get("promo_price_candidate")) for r in candidates),
        "candidate_product_suggestion_count": sum(bool(r.get("matched_product_name") or r.get("local_product_name_suggestion")) for r in candidates),
        "candidate_catalog_match_count": sum(bool(r.get("matched_product_name")) for r in candidates),
        "candidate_local_name_suggestion_count": sum(bool(r.get("local_product_name_suggestion")) for r in candidates),
        "cache_csv": str(CACHE_CSV), "lines_csv": str(lines_path), "candidate_csv": str(candidates_path),
        "review_workbook": str(review_path),
        "approved_override_count": import_stats.get("active_overrides", 0),
    }
    return stats


def run_ocr(limit: int = 0, force: bool = False, backend_preference: str = "auto") -> dict:
    images = _load_image_index()
    if not images:
        raise FileNotFoundError(f"Daftar gambar poster tidak ditemukan/kosong: {IMAGE_INDEX}. Jalankan collector terlebih dahulu.")
    old_cache = {str(r.get("image_url") or ""): r for r in _read_csv(CACHE_CSV)}
    starting_cache = dict(old_cache)
    if force:
        to_process = images[:limit] if limit > 0 else images
        scope = f"semua {len(to_process)} gambar" if limit <= 0 else f"{len(to_process)} gambar pertama"
        print(f"Mode OCR paksa: akan menjalankan ulang {scope}; penggunaan CPU/RAM meningkat selama inferensi.", flush=True)
    else:
        # Retry transient download/OCR failures next run. Successful results and
        # no-text images remain cached to avoid repeatedly processing the same poster.
        to_process = [
            record for record in images
            if not _is_current_ocr_cache(old_cache.get(str(record.get("image_url") or ""), {}))
        ]
        if limit > 0:
            to_process = to_process[:limit]
        print(f"Mode OCR inkremental: {len(to_process)} gambar baru/gagal akan diproses; cache sukses dipakai ulang.", flush=True)
    if not to_process:
        print("OCR: semua gambar pada indeks sudah memiliki cache; gunakan --force untuk memproses ulang.")
        # Even with no new images, the user may have edited the review workbook.
        # Import those decisions so the following enricher run sees the approval.
        if REVIEW_XLSX.exists():
            import_stats = import_reviewed_prices(REVIEW_XLSX, OVERRIDES_CSV)
            print(f"Koreksi poster disetujui/ditambahkan: {import_stats.get('accepted_or_updated', 0)}")
            print(f"Koreksi poster dinonaktifkan: {import_stats.get('deactivated', 0)}")
            print(f"Koreksi poster aktif: {import_stats.get('active_overrides', 0)}")
        else:
            import_stats = {"active_overrides": 0}
        cache_usable, cache_missing_or_stale = _cache_coverage(images, old_cache)
        return {
            "images_indexed": len(images), "images_processed": 0,
            "images_cached": cache_usable,
            "cache_usable_images": cache_usable,
            "cache_missing_or_stale_images": cache_missing_or_stale,
            "cache_coverage_percent": round(100.0 * cache_usable / max(1, len(images)), 1),
            "ocr_ok": 0, "ocr_failed": 0, "candidate_count": 0,
            "approved_override_count": import_stats.get("active_overrides", 0),
        }

    backend, engine = create_ocr_engine(backend_preference)
    session = requests.Session()
    processed = ok = failed = 0
    try:
        for idx, record in enumerate(to_process, 1):
            image_started_at = time.monotonic()
            url = str(record.get("image_url") or "")
            previous_cache = old_cache.get(url)
            cache_to_store = None
            cache_row = dict(record)
            cache_row.update({"image_sha256": "", "image_width": "", "image_height": "", "ocr_status": "failed", "ocr_backend": backend, "ocr_pipeline_version": OCR_PIPELINE_VERSION, "ocr_error": "", "processed_at": now_iso(), "lines_json": "[]"})
            try:
                body, image_hash = _download_image(session, url)
                lines, width, height = recognize_image(body, engine, backend)
                cache_row.update({
                    "image_sha256": image_hash, "image_width": width, "image_height": height,
                    "ocr_status": "ok" if lines else "no_text", "ocr_backend": backend,
                    "ocr_pipeline_version": OCR_PIPELINE_VERSION,
                    "processed_at": now_iso(), "lines_json": json.dumps(lines, ensure_ascii=False, separators=(",", ":")),
                })
                if lines:
                    ok += 1
                else:
                    failed += 1
            except Exception as exc:
                cache_row["ocr_error"] = f"{type(exc).__name__}: {exc}"[:500]
                cache_row["ocr_backend"] = backend
                cache_row["processed_at"] = now_iso()
                failed += 1
                # Do not destroy a previous successful OCR result if a forced
                # refresh temporarily fails (e.g. the poster host is down).
                if previous_cache and previous_cache.get("ocr_status") == "ok" and _line_records(previous_cache):
                    cache_to_store = dict(previous_cache)
                    cache_to_store["ocr_error"] = ("Refresh gagal; hasil OCR lama dipertahankan. " + cache_row["ocr_error"])[:500]
            if cache_to_store is None:
                cache_to_store = cache_row
            old_cache[url] = {field: str(cache_to_store.get(field) or "") for field in CACHE_FIELDS}
            processed += 1
            image_duration = time.monotonic() - image_started_at
            short_label = str(record.get("image_alt") or record.get("campaign_title") or record.get("source_name") or "poster").strip()
            if len(short_label) > 55:
                short_label = short_label[:52] + "..."
            print(
                f"OCR gambar {idx}/{len(to_process)} | {short_label} | "
                f"teks terbaca={ok} | gagal/kosong={failed} | durasi={image_duration:.1f}s",
                flush=True,
            )
            time.sleep(0.05)
    finally:
        session.close()

    # Write cache only for current images and previous cached entries; retaining prior
    # rows allows OCR to be incremental across runs even when an article disappears.
    _write_csv(CACHE_CSV, [old_cache[k] for k in sorted(old_cache)], CACHE_FIELDS)
    cache_rows = [old_cache.get(str(r.get("image_url") or ""), {}) for r in images]
    line_rows = []
    candidates = []
    page_products = _load_page_products()
    catalog_products = _load_catalog_products()
    for image_record, cache_row in zip(images, cache_rows):
        lines = _line_records(cache_row)
        width = int(cache_row.get("image_width") or 0)
        height = int(cache_row.get("image_height") or 0)
        stamp = cache_row.get("processed_at") or ""
        for n, line in enumerate(_sort_lines(lines), 1):
            line_rows.append({
                "source_name": image_record.get("source_name", ""), "campaign_title": image_record.get("campaign_title", ""),
                "article_url": image_record.get("article_url", ""), "image_url": image_record.get("image_url", ""),
                "image_alt": image_record.get("image_alt", ""), "period_status": image_record.get("period_status", ""),
                "line_no": n, "ocr_text": line.get("text", ""), "ocr_confidence": line.get("confidence", ""),
                "x0": line.get("x0", ""), "y0": line.get("y0", ""), "x1": line.get("x1", ""), "y1": line.get("y1", ""),
                "image_width": width, "image_height": height, "processed_at": stamp,
                "recognition_method": line.get("recognition_method", "FULL_PAGE"),
            })
        product_rows = page_products.get(_canonical_article_url(image_record.get("article_url")), [])
        fallback_rows = _catalog_fallback_for_campaign(catalog_products, str(image_record.get("campaign_title") or ""))
        candidates.extend(build_candidates(image_record, lines, width, height, product_rows, fallback_rows))

    _write_csv(LINES_CSV, line_rows, LINE_FIELDS)
    # De-duplicate candidates when one image has repeated identical price windows.
    candidate_map = {}
    for candidate in candidates:
        candidate_map[candidate["candidate_id"]] = candidate
    candidates = sorted(candidate_map.values(), key=lambda row: (row.get("source_name", ""), row.get("campaign_title", ""), row.get("candidate_id", "")))
    _write_csv(CANDIDATES_CSV, candidates, CANDIDATE_FIELDS)
    prior = _load_old_review(REVIEW_XLSX)
    make_review_workbook(candidates, REVIEW_XLSX, prior)
    import_stats = import_reviewed_prices(REVIEW_XLSX, OVERRIDES_CSV)
    cache_usable, cache_missing_or_stale = _cache_coverage(images, old_cache)
    to_process_urls = {str(record.get("image_url") or "") for record in to_process}
    reused_count = sum(
        str(record.get("image_url") or "") not in to_process_urls
        and _is_current_ocr_cache(starting_cache.get(str(record.get("image_url") or ""), {}))
        for record in images
    )
    return {
        "images_indexed": len(images), "images_processed": processed, "images_cached": reused_count,
        "cache_usable_images": cache_usable,
        "cache_missing_or_stale_images": cache_missing_or_stale,
        "cache_coverage_percent": round(100.0 * cache_usable / max(1, len(images)), 1),
        "ocr_ok": ok, "ocr_failed_or_empty": failed, "ocr_backend": backend,
        "line_count": len(line_rows), "candidate_count": len(candidates),
        "candidate_price_pair_count": sum(bool(r.get("normal_price_candidate")) and bool(r.get("promo_price_candidate")) for r in candidates),
        "candidate_product_suggestion_count": sum(bool(r.get("matched_product_name") or r.get("local_product_name_suggestion")) for r in candidates),
        "candidate_catalog_match_count": sum(bool(r.get("matched_product_name")) for r in candidates),
        "candidate_local_name_suggestion_count": sum(bool(r.get("local_product_name_suggestion")) for r in candidates),
        "approved_override_count": import_stats.get("active_overrides", 0),
        "cache_csv": str(CACHE_CSV), "lines_csv": str(LINES_CSV), "candidate_csv": str(CANDIDATES_CSV),
        "review_workbook": str(REVIEW_XLSX),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="OCR poster katalog promo secara lokal; tidak melakukan estimasi harga AI.")
    parser.add_argument("--limit", type=int, default=0, help="Maksimum gambar OCR baru; dengan --rebuild-review-only, buat workbook uji dari N cache terbaru.")
    parser.add_argument("--force", action="store_true", help="Paksa OCR ulang gambar yang sudah ada cache.")
    parser.add_argument("--backend", choices=("auto", "rapidocr", "tesseract"), default="auto", help="Mesin OCR lokal (default: RapidOCR/ONNX, lalu Tesseract).")
    parser.add_argument("--import-reviewed-only", action="store_true", help="Impor keputusan TERIMA/TOLAK dari workbook tanpa OCR ulang.")
    parser.add_argument("--rebuild-review-only", action="store_true", help="Bangun ulang workbook dari cache OCR tanpa mengunduh gambar atau menjalankan OCR.")
    args = parser.parse_args(argv)
    if args.limit < 0:
        parser.error("--limit harus 0 atau lebih")
    if args.rebuild_review_only:
        try:
            stats = rebuild_review_from_cache(limit=args.limit)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            print(f"ERROR rebuild review: {exc}", file=sys.stderr)
            return 2
        for key, value in stats.items():
            print(f"{key}: {value}")
        if args.limit > 0:
            print("Review uji dari cache terbaru selesai; review utama dan persetujuan harga tidak diubah.")
        else:
            print("Review selesai dibangun ulang dari cache; tidak ada OCR/download baru.")
        return 0
    if args.import_reviewed_only:
        stats = import_reviewed_prices(REVIEW_XLSX, OVERRIDES_CSV)
        print(f"Baris review dibaca: {stats['review_rows']}")
        print(f"Koreksi disetujui/ditambahkan: {stats['accepted_or_updated']}")
        print(f"Koreksi dinonaktifkan: {stats['deactivated']}")
        print(f"Baris TERIMA tidak valid: {stats['rejected_invalid']}")
        print(f"Koreksi aktif: {stats['active_overrides']}")
        print(f"Database koreksi: {OVERRIDES_CSV}")
        for err in stats.get("errors", [])[:20]:
            print(" - " + err)
        return 0 if not stats["rejected_invalid"] else 2
    try:
        stats = run_ocr(limit=args.limit, force=args.force, backend_preference=args.backend)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"ERROR OCR: {exc}", file=sys.stderr)
        return 2
    for key, value in stats.items():
        print(f"{key}: {value}")
    print("Harga OCR belum dipakai ke katalog sampai keputusan Excel = TERIMA.")
    print("Backend OCR: auto memprioritaskan RapidOCR/ONNX, lalu Tesseract. Crop warna label harga dipakai sebagai rescue lokal; tidak ada API OCR cloud.")
    print("Untuk impor manual setelah edit Excel: python engine\\promo_poster_ocr.py --import-reviewed-only")
    print("Setelah itu: python engine\\enrich_existing_promo_catalog_multisource.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
