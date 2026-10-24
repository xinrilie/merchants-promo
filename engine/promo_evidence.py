from __future__ import annotations

"""Small, deterministic helpers for tracing parsed promo values to visible text.

These helpers do not decide whether a retailer is trustworthy. They only keep
an auditable text snippet for values that were extracted from source content.
"""

import re

MONTH_PATTERN = (
    r"januari|jan|februari|feb|maret|mar|april|apr|mei|juni|jun|juli|jul|"
    r"agustus|agu|ags|agst|september|sept|sep|oktober|okt|november|nov|"
    r"desember|des"
)
SPACE_RE = re.compile(r"\s+")
CURRENCY_AMOUNT_RE = re.compile(r"(?i)Rp\.?\s*(?P<amount>\d[\d.,]*)")
DISCOUNT_RE = re.compile(
    r"(?i)(?:diskon|potongan(?:\s+harga)?|hemat)\s*(?:hingga|sampai|up\s+to)?\s*"
    r"(?P<pct>\d{1,3}(?:[.,]\d+)?)\s*%|"
    r"(?P<pct_after>\d{1,3}(?:[.,]\d+)?)\s*%\s*(?:off|diskon)"
)
PERIOD_PATTERNS = (
    re.compile(rf"(?i)\b\d{{1,2}}\s+({MONTH_PATTERN})\s*(?:-|–|—|sampai|hingga|s\.d\.|s/d|sd)\s*\d{{1,2}}\s+({MONTH_PATTERN})\s+\d{{4}}\b"),
    re.compile(rf"(?i)\b\d{{1,2}}\s*(?:-|–|—|sampai|hingga|s\.d\.|s/d|sd)\s*\d{{1,2}}\s+({MONTH_PATTERN})\s+\d{{4}}\b"),
    re.compile(rf"(?i)\b(?:tanggal|berlaku tanggal|promo tanggal)\s+\d{{1,2}}\s+({MONTH_PATTERN})\s+\d{{4}}\b"),
    re.compile(rf"(?i)\b(?:hingga|sampai|s\.d\.|s/d|sd)\s*\d{{1,2}}\s+({MONTH_PATTERN})\s+\d{{4}}\b"),
    re.compile(rf"(?i)\b({MONTH_PATTERN})\s+\d{{4}}\b"),
)


def clean(value: object) -> str:
    return SPACE_RE.sub(" ", str(value or "")).strip()


def amount_digits(value: object) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    text = re.sub(r"\D", "", str(value))
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def find_amount_evidence(text: object, amount: object, kind: str = "promo") -> str | None:
    """Return the closest literal Rp amount matching value, retaining label context."""
    source = clean(text)
    expected = amount_digits(amount)
    if not source or expected is None:
        return None
    matches = list(CURRENCY_AMOUNT_RE.finditer(source))
    if not matches:
        return None

    def rank(match):
        before = source[max(0, match.start() - 42):match.start()].casefold()
        label = 0
        if kind == "normal" and re.search(r"harga\s+normal|\bnormal\b", before):
            label = 3
        elif kind == "promo" and re.search(r"harga\s+(?:promo|spesial|setelah\s+diskon)|\bpromo\b", before):
            label = 3
        elif re.search(r"harga|promo|normal", before):
            label = 1
        value_match = amount_digits(match.group("amount")) == expected
        return (int(value_match), label, -match.start())

    exact = [m for m in matches if amount_digits(m.group("amount")) == expected]
    if not exact:
        return None
    exact.sort(key=rank, reverse=True)
    match = exact[0]
    start = max(0, match.start() - 42)
    end = min(len(source), match.end() + 16)
    return source[start:end].strip(" :-|;,")


def find_discount_evidence(text: object, discount: object) -> str | None:
    source = clean(text)
    if not source or discount in (None, ""):
        return None
    try:
        expected = round(float(str(discount).replace(",", ".")), 1)
    except (TypeError, ValueError):
        return None
    for match in DISCOUNT_RE.finditer(source):
        raw = match.group("pct") or match.group("pct_after")
        if raw is None:
            continue
        try:
            value = round(float(raw.replace(",", ".")), 1)
        except ValueError:
            continue
        if value == expected:
            return source[max(0, match.start() - 28):min(len(source), match.end() + 24)].strip(" :-|;,")
    return None


def extract_period_evidence(
    title: object,
    body: object,
    precision: str | None,
    matched_text: object = None,
) -> tuple[str | None, str | None]:
    """Return a visible phrase and whether it came from title or body/lead text."""
    explicit = clean(matched_text)
    if explicit:
        title_text = clean(title)
        body_text = clean(body)
        if explicit.casefold() in title_text.casefold():
            return explicit, "title"
        if explicit.casefold() in body_text.casefold():
            return explicit, "body"
        # Existing parsers sometimes normalize/lowercase the match. It remains useful
        # only if a date/month phrase with the same parsed precision is in visible text.

    precision = (precision or "unknown").casefold()
    patterns = PERIOD_PATTERNS
    if precision in {"range", "single_day"}:
        patterns = PERIOD_PATTERNS[:3]
    elif precision == "until":
        patterns = PERIOD_PATTERNS[3:4]
    elif precision == "month":
        patterns = PERIOD_PATTERNS[4:5]

    for label, text in (("title", clean(title)), ("body", clean(body))):
        if not text:
            continue
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                snippet = text[max(0, match.start() - 12):min(len(text), match.end() + 12)].strip(" :-|;,")
                return snippet, label
    return None, None


def period_certainty(precision: object, status: object, flags: object = None, evidence: object = None) -> str:
    precision = clean(precision).casefold()
    status = clean(status).casefold()
    flag_text = clean(flags).casefold()
    if any(marker in flag_text for marker in (
        "title_lead_period_differs", "title_period_mismatch_with_catalog_dates",
        "source_dates_differ_from_title_period", "period_evidence_missing",
    )):
        return "conflict" if "differs" in flag_text or "mismatch" in flag_text or "conflict" in flag_text else "estimated"
    if status == "unknown" or precision in {"unknown", ""}:
        return "unknown"
    if precision in {"range", "single_day"} and clean(evidence):
        return "confirmed"
    if precision in {"month", "until", "unverified", "conflict"} or status.endswith("estimated"):
        return "estimated"
    if precision in {"range", "single_day"}:
        return "estimated"
    return "unknown"


def source_verification_label(value: object) -> str:
    """Describe source provenance without overclaiming official verification."""
    values = {part.strip().casefold() for part in clean(value).split("|") if part.strip()}
    if "retailer_verified" in values:
        return "Retailer terverifikasi"
    if values == {"aggregator_only"}:
        return "Agregator; belum diverifikasi retailer"
    if values == {"historical_baseline_unknown"} or values == {"baseline_unknown"}:
        return "Baseline historis; asal verifikasi belum diketahui"
    if any("baseline_unknown" in value or "historical_baseline_unknown" in value for value in values):
        return "Beberapa sumber; sebagian asal verifikasi belum diketahui"
    if not values:
        return "Belum diverifikasi"
    return "Beberapa sumber; bukan verifikasi retailer"
