from __future__ import annotations

"""Heuristics for extracting unknown merchant labels without a merchant allow-list.

Known aliases may still be used by source adapters to canonicalize familiar names.
This utility only supplies a conservative fallback from campaign titles/URL slugs;
ambiguous long titles return None rather than pretending to know the merchant.
"""

import re
from urllib.parse import urlparse

SPACE_RE = re.compile(r"\s+")
MONTHS = (
    "januari|februari|maret|april|mei|juni|juli|agustus|september|oktober|november|desember|"
    "jan|feb|mar|apr|jun|jul|agu|ags|agst|sep|sept|okt|nov|des"
)
TITLE_PREFIX_RE = re.compile(r"^\s*(?:(?:katalog\s+promo|promo(?:si)?|katalog|penawaran)\s+)", re.I)
TITLE_STOP_RE = re.compile(
    r"\b(?:jsm|psm|minggu\s+ini|minggu\s+depan|akhir\s+pekan|weekend|weekday|"
    r"terbaru|bulan\s+ini|bulan\s+depan|katalog(?:\s+belanja)?|promo(?:si)?|"
    r"diskon|cashback|voucher|kupon|tebus\s+murah|serba\s+gratis|harga(?:\s+spesial)?|"
    r"periode|berlaku|khusus|spesial|hingga|sampai|mulai|ada|hadir(?:kan)?|"
    r"beli\s+\d|buy\s*\d|gratis|potongan|tanggal|untuk|dengan|bersama|pakai|menggunakan|"
    r"sale|deals?|payday|anniversary|b1g1|flash|dari\s+tanggal|x|via|"
    r"januari|februari|maret|april|mei|juni|juli|agustus|september|oktober|november|desember|"
    r"jan(?:uari)?|feb(?:ruari)?|mar(?:et)?|apr(?:il)?|jun(?:i)?|jul(?:i)?|agu(?:stus)?|"
    r"sep(?:tember)?|okt(?:ober)?|nov(?:ember)?|des(?:ember)?|20\d{2})\b",
    re.I,
)
LABEL_NOISE_RE = re.compile(
    r"(?:\brp\.?\s*\d|\d{1,2}\s*[-–—]\s*\d{1,2}|%|[!?;]|"
    r"syarat|ketentuan|minimal belanja|tebus murah|cashback|voucher|kupon)",
    re.I,
)
GENERIC_ONLY = {"promo", "promosi", "katalog", "penawaran", "diskon", "belanja", "minggu", "terbaru", "spesial", "sale", "deal", "deals", "payday", "anniversary", "flash", "gratis", "b1g1", "b1g0", "buy", "get", "harga", "cashback", "voucher", "kupon", "jsm", "psm"}


def clean_text(value: object) -> str:
    return SPACE_RE.sub(" ", str(value or "")).strip()


def infer_merchant_candidate_from_title(title: object) -> str | None:
    """Extract a short candidate from a title with an explicit campaign prefix.

    Example: 'Promo All Fruit 23-25 Oktober 2026' -> 'All Fruit'.
    It deliberately avoids retailer-specific allow/deny lists.
    """
    text = clean_text(title)
    if not text or not re.match(r"^(?:promo(?:si)?|katalog|penawaran)\b", text, re.I):
        return None

    text = TITLE_PREFIX_RE.sub("", text, count=1).strip(" :|–—-")
    text = re.sub(r"^(?:jsm|psm)\s+", "", text, flags=re.I).strip()
    text = re.split(r"[:|]", text, maxsplit=1)[0].strip()

    date_match = re.search(rf"\b\d{{1,2}}\s*(?:[-–—]\s*\d{{1,2}})?\s+(?:{MONTHS})\b", text, re.I)
    if date_match:
        text = text[:date_match.start()].strip(" :|–—-")
    year_match = re.search(r"\b20\d{2}\b", text)
    if year_match:
        text = text[:year_match.start()].strip(" :|–—-")
    stop = TITLE_STOP_RE.search(text)
    if stop:
        text = text[:stop.start()].strip(" :|–—-")
    text = re.split(r"\s+[–—]\s+", text, maxsplit=1)[0].strip(" :|–—-")

    tokens = re.findall(r"[\w&'.!]+", text, flags=re.UNICODE)
    if not tokens or len(tokens) > 3 or LABEL_NOISE_RE.search(text):
        return None
    if not any(ch.isalpha() for ch in text) or all(token.casefold() in GENERIC_ONLY for token in tokens):
        return None
    return text


def infer_merchant_candidate_from_url(url: object) -> str | None:
    """Extract an unknown candidate from explicit promo/catalog URL slugs only."""
    parsed = urlparse(clean_text(url))
    if parsed.scheme not in {"http", "https"}:
        return None
    for segment in reversed([part for part in parsed.path.split("/") if part]):
        slug = re.sub(r"[-_]+", " ", segment)
        candidate = infer_merchant_candidate_from_title(slug.title())
        if candidate:
            return candidate
    return None


def is_dynamic_merchant_label(value: object) -> bool:
    label = clean_text(value)
    if not label or len(re.findall(r"[\w&'.!]+", label, flags=re.UNICODE)) > 3:
        return False
    if LABEL_NOISE_RE.search(label) or re.search(r"[,!?;]", label):
        return False
    if re.match(r"^(?:promo|katalog|diskon|syarat|ketentuan|harga)\b", label, re.I):
        return False
    return any(char.isalpha() for char in label)
