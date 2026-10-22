from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


def _clean_space(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _amount(value: str) -> str:
    value = value.replace(",", ".")
    try:
        d = Decimal(value)
        if d == d.to_integral_value():
            return str(int(d))
        return format(d.normalize(), "f")
    except InvalidOperation:
        return value


def _unit(value: str) -> str:
    v = value.casefold().rstrip(".")
    if v in {"gr", "gram", "grams"}:
        return "g"
    if v in {"litre", "liter", "ltr"}:
        return "l"
    return v


def _collapse_repeated_suffix(tokens: list[str]) -> list[str]:
    """Collapse repeated phrases at the end, e.g. 'Sweet Floral Sweet Floral'."""
    tokens = list(tokens)
    while len(tokens) >= 2:
        changed = False
        max_n = min(5, len(tokens) // 2)
        for n in range(max_n, 0, -1):
            left = [re.sub(r"[^a-z0-9]", "", x.casefold()) for x in tokens[-2 * n:-n]]
            right = [re.sub(r"[^a-z0-9]", "", x.casefold()) for x in tokens[-n:]]
            if left == right and any(left):
                del tokens[-n:]
                changed = True
                break
        if not changed:
            break
    return tokens


def clean_product_name(value: object) -> str:
    """Normalize noisy product names while preserving pack size/quantity.

    Removes price/promotion annotations accidentally captured as part of a product
    name (e.g. "Aice ... - Harga Normal 270 g"), but first captures size/pack data
    so the actual product size is not lost. Different sizes remain different SKUs.
    """
    text = _clean_space(value)
    if not text:
        return ""

    text = text.replace("×", "x").replace("\u00a0", " ")
    text = re.sub(r"\bmybaby\b", "my baby", text, flags=re.IGNORECASE)
    text = re.sub(r"\bbengbeng\b", "beng-beng", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n|•-–—:")
    original_text = text

    # Capture size information from the full string before removing promotional
    # suffixes. Some source parsers mistakenly put "Harga Normal" before the size.
    pack_specs: list[str] = []
    pack_patterns = [
        re.compile(
            r"\b(?:per\s*)?(\d+)\s*(?:pcs?|pieces|buah)\s*(?:x|@)?\s*"
            r"(\d+(?:[.,]\d+)?)\s*(gr?\.?|grams?|ml|kg|litre|liter|ltr|l)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(\d+)\s*x\s*(\d+(?:[.,]\d+)?)\s*"
            r"(gr?\.?|grams?|ml|kg|litre|liter|ltr|l)\b",
            re.IGNORECASE,
        ),
    ]
    for pattern in pack_patterns:
        match = pattern.search(original_text)
        if match:
            count, amount, unit = match.groups()
            pack_specs.append(f"{count} x {_amount(amount)} {_unit(unit)}")
            break

    measure_pattern = re.compile(
        r"\b(\d+(?:[.,]\d+)?)\s*(ml|gr?\.?|grams?|kg|litre|liter|ltr|l)\b",
        re.IGNORECASE,
    )
    measures: list[str] = []
    for match in measure_pattern.finditer(original_text):
        amount, unit = match.groups()
        measure = f"{_amount(amount)} {_unit(unit)}"
        if measure not in measures:
            measures.append(measure)

    # Trim metadata accidentally captured in product_text. This is intentionally
    # limited to recognizable price/promo labels so product wording is preserved.
    annotation = re.compile(
        r"\s*(?:[-–—|,:]\s*)?(?:"
        r"harga\s+(?:normal|promo|spesial|setelah\s+diskon)\b|"
        r"diskon(?:\s+(?:hingga|sampai))?(?:\s*[:\-]?\s*\d+(?:[.,]\d+)?\s*%)?\b|"
        r"hemat\s+(?:rp\.?\s*)?\d|"
        r"potongan\s+(?:harga|\d)|"
        r"rp\.?\s*\d[\d.,]*\b|"
        r"promo\s*[:\-]\s*(?:rp\.?\s*\d|diskon|harga|beli|gratis)"
        r").*$",
        re.IGNORECASE,
    )
    text = annotation.sub("", text).strip(" -–—|,:;")

    # Remove measurement/pack notation from the body because it will be appended
    # once in canonical form below.
    for pattern in pack_patterns:
        text = pattern.sub(" ", text)
    text = measure_pattern.sub(" ", text)
    text = re.sub(
        r"\b(?:pck|pack|pak|btl|botol|klg|kaleng|pcs?|pieces|per)\b\.?",
        " ", text, flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+", " ", text).strip(" -–—:|,;")

    # Normalize separators consistently without deleting meaningful variants.
    text = re.sub(r"\s*/\s*", " / ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    tokens = _collapse_repeated_suffix(text.split())
    text = " ".join(tokens).strip()

    # Consistent display casing. Fix high-frequency brand spellings after title case.
    text = text.title()
    brand_fixes = [
        (r"\bBeng\s*[- ]\s*Beng\b", "Beng-Beng"),
        (r"\bMy Baby\b", "My Baby"),
        (r"\bLasegar\b", "Lasegar"),
        (r"\bJ\.co\b", "J.CO"),
        (r"\bKfc\b", "KFC"),
        (r"\bMcdonald'S\b", "McDonald's"),
        (r"\bAqua\b", "AQUA"),
        (r"\bSariwangi\b", "SariWangi"),
    ]
    for pattern, replacement in brand_fixes:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    size_parts = pack_specs if pack_specs else measures
    size_parts = list(dict.fromkeys(size_parts))
    if size_parts:
        text = f"{text} {'; '.join(size_parts)}".strip()
    return _clean_space(text)


def name_variants(value: object) -> list[str]:
    """Split variant fields (pipe-separated) into unique original names."""
    result = []
    for bit in str(value or "").split(" | "):
        bit = _clean_space(bit)
        if bit and bit not in result:
            result.append(bit)
    return result


def _self_test() -> None:
    samples = {
        "BENG-BENG Chocolate Drink 10x27g pck": "Beng-Beng Chocolate Drink 10 x 27 g",
        "Beng-beng per 10 pcs 27 gr Chocolate Drink 27 gr": "Beng-Beng Chocolate Drink 10 x 27 g",
        "My Baby 100 ml Body Cologne Sweet Floral 100 ml Sweet Floral": "My Baby Body Cologne Sweet Floral 100 ml",
        "MY BABY Body Cologne Sweet Floral 100ml btl": "My Baby Body Cologne Sweet Floral 100 ml",
        "LASEGAR Twist Jeruk-Lemon 320ml klg": "Lasegar Twist Jeruk-Lemon 320 ml",
        "Aice Mochi Vanilla/ Chocolate - Harga Normal 270 g": "Aice Mochi Vanilla / Chocolate 270 g",
        "Aice Mochi Vanilla/ Chocolate 270 g": "Aice Mochi Vanilla / Chocolate 270 g",
        "My Baby Body Cologne Sweet Floral 100 ml Harga Promo Rp 12.900": "My Baby Body Cologne Sweet Floral 100 ml",
    }
    for source, expected in samples.items():
        actual = clean_product_name(source)
        assert actual == expected, f"{source!r}: {actual!r} != {expected!r}"
    print("SELF-TEST PASSED: normalisasi nama produk dan ukuran kemasan.")


if __name__ == "__main__":
    _self_test()
