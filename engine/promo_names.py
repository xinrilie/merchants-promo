from __future__ import annotations

import re
from difflib import SequenceMatcher
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
        r"\b(\d+(?:[.,]\d+)?)\s*(ml|gr?\.?|grams?|kg|litre|liter|ltr|l|sheets?|lembar|pcs?|pieces|rolls?)\b",
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
        r"hemat\s+(?:rp\.?\s*)?\d[\d.,]*\b(?!\s*(?:sheets?|lembar|pcs?|pieces|pack|pak|roll|buah|botol|kaleng)\b)|"
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



# Tokens that usually signal a meaningful product variant (flavour, scent, or formula).
# This is a guardrail, not a complete product dictionary.
_VARIANT_TOKENS = {
    "vanilla", "cokelat", "chocolate", "strawberry", "stroberi", "mango", "mangga",
    "banana", "pisang", "coffee", "kopi", "mocha", "mocca", "matcha", "green",
    "tea", "teh", "original", "classic", "zero", "less", "sugar", "sugarfree",
    "jeruk", "lemon", "lime", "orange", "grape", "anggur", "apple", "apel",
    "durian", "melon", "lychee", "leci", "peach", "coconut", "kelapa", "milk",
    "susu", "mint", "peppermint", "floral", "sweet", "fresh", "aloe", "lavender",
    "rose", "pink", "blue", "red", "black", "white", "spicy", "pedas", "bbq",
    "barbeque", "cheese", "keju", "chicken", "ayam", "beef", "sapi", "seaweed",
    "rumput", "laut", "garlic", "bawang", "honey", "madu", "creamy", "crunchy",
}

_SIZE_PACK_RE = re.compile(
    r"\b(\d+)\s*x\s*(\d+(?:[.,]\d+)?)\s*(ml|g|kg|l)\b", re.IGNORECASE
)
_SIZE_SINGLE_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(ml|g|kg|l)\b", re.IGNORECASE)


def _fuzzy_text(value: object) -> str:
    """A punctuation-insensitive form used only for comparison, not display."""
    canonical = clean_product_name(value).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", canonical))


def product_name_size_signature(value: object) -> tuple[str, ...]:
    """Return package-size signature; 10 x 27 g remains distinct from 270 g."""
    text = clean_product_name(value).casefold()
    pack = _SIZE_PACK_RE.search(text)
    if pack:
        count, amount, unit = pack.groups()
        return (f"pack:{int(count)}x{_amount(amount)}:{_unit(unit)}",)
    measures = []
    for amount, unit in _SIZE_SINGLE_RE.findall(text):
        value = f"{_amount(amount)}:{_unit(unit)}"
        if value not in measures:
            measures.append(value)
    return tuple(measures)


def product_name_variant_signature(value: object) -> frozenset[str]:
    """Known variant markers used to avoid merging different flavours/formulas."""
    text = _fuzzy_text(value)
    tokens = set(text.split())
    # Multi-token variants that are meaningful but should not be mistaken for
    # unrelated token noise.
    if "sugar free" in text:
        tokens.add("sugarfree")
    return frozenset(tokens & _VARIANT_TOKENS)


def product_name_similarity(a: object, b: object) -> float:
    """Dynamic similarity score in [0, 1], using only Python's standard library."""
    left, right = _fuzzy_text(a), _fuzzy_text(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left_tokens, right_tokens = left.split(), right.split()
    left_set, right_set = set(left_tokens), set(right_tokens)
    denom = len(left_set) + len(right_set)
    dice = (2 * len(left_set & right_set) / denom) if denom else 0.0
    seq = SequenceMatcher(None, left, right, autojunk=False).ratio()
    sorted_seq = SequenceMatcher(
        None, " ".join(sorted(left_tokens)), " ".join(sorted(right_tokens)), autojunk=False
    ).ratio()
    return round(0.55 * seq + 0.25 * dice + 0.20 * sorted_seq, 4)


def product_name_block_key(value: object) -> str:
    """First normalized token used to reduce fuzzy-comparison work."""
    text = _fuzzy_text(value)
    return text.split()[0] if text else ""


def product_name_identity_warnings(a: object, b: object) -> list[str]:
    """Return explicit conflicts that should prevent auto-merging names."""
    warnings = []
    sa, sb = product_name_size_signature(a), product_name_size_signature(b)
    if sa and sb and sa != sb:
        warnings.append("ukuran/kemasan berbeda")
    va, vb = product_name_variant_signature(a), product_name_variant_signature(b)
    if va and vb and va != vb:
        warnings.append("varian rasa/aroma/formula berbeda")
    return warnings


def product_names_can_auto_merge(a: object, b: object, threshold: float = 0.93) -> bool:
    """Conservative dynamic matcher: only near-identical names with matching identity cues."""
    left, right = _fuzzy_text(a), _fuzzy_text(b)
    if not left or not right:
        return False
    if left == right:
        return True

    warnings = product_name_identity_warnings(a, b)
    if warnings:
        return False

    # Do not auto-merge if one side omits all explicit size or variant information.
    if bool(product_name_size_signature(a)) != bool(product_name_size_signature(b)):
        return False
    if bool(product_name_variant_signature(a)) != bool(product_name_variant_signature(b)):
        return False

    left_tokens, right_tokens = left.split(), right.split()
    if not left_tokens or not right_tokens or left_tokens[0] != right_tokens[0]:
        return False
    token_coverage = len(set(left_tokens) & set(right_tokens)) / max(1, min(len(set(left_tokens)), len(set(right_tokens))))
    return product_name_similarity(a, b) >= threshold and token_coverage >= 0.80

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
    assert product_name_similarity("Aice Mochi Vanilla Chocolate 270 g", "Aice Mochi Vanilla / Chocolate 270 g") >= 0.99
    assert product_names_can_auto_merge("Aice Mochi Vanilla Chocolate 270 g", "Aice Mochi Vanilla / Chocolate 270 g")
    assert not product_names_can_auto_merge("Aice Mochi Vanilla 270 g", "Aice Mochi Chocolate 270 g")
    assert not product_names_can_auto_merge("Sunlight Jeruk 755 ml", "Sunlight Jeruk 450 ml")
    print("SELF-TEST PASSED: normalisasi nama, ukuran, varian, dan fuzzy matching.")


if __name__ == "__main__":
    _self_test()
