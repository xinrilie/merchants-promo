from __future__ import annotations

"""Shared guardrails to keep article/editorial text out of product catalogs.

Collector uses this before exporting scraped product rows. Enhancer repeats the
same validation across every input dataset as a final defense-in-depth filter.
"""

import re
from difflib import SequenceMatcher

SPACE_RE = re.compile(r"\s+")
TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
PRICE_RE = re.compile(r"\b(?:rp\.?\s*\d|harga\s+(?:promo|normal|spesial)\s*[:\-]?\s*\d)", re.I)
BUNDLE_RE = re.compile(
    r"\b(?:beli\s*\d+(?:\s*(?:pcs?|pack|item))?\s*(?:gratis\s*\d+)?|buy\s*\d+\s*get\s*\d+|tebus\s+murah|pwp)\b",
    re.I,
)
DISCOUNT_RE = re.compile(r"\b(?:diskon|potongan(?:\s+harga)?|hemat)\s*(?:hingga\s*)?\d{1,3}(?:[.,]\d+)?\s*%", re.I)
OFFER_RE = re.compile(
    r"\b(?:diskon|potongan(?:\s+harga)?|hemat\s+rp|gratis|beli\s+\d+|buy\s*\d+\s*get\s*\d+|"
    r"tebus\s+murah|pwp|cashback|voucher|kupon|harga\s+(?:promo|normal|spesial)|rp\.?\s*\d)\b",
    re.I,
)

# Structural/article-language patterns, rather than a retailer/product blacklist.
EDITORIAL_PREFIX_RE = re.compile(
    r"^(?:"
    # Sentence fragments copied from PWP explanatory paragraphs, not product SKUs.
    r"karena\b|sebab\b|"
    r"hanya\s+dengan\s+belanja(?:\s+minimal)?\b|"
    r"mulai\s+dari\s+tanggal\b|bunda\s+semua\b|"
    # Article/question headings commonly misread as a product name.
    r"apa(?:\s+saja|\s+benefit|\s+manfaat|\s+yang|\s+keuntungan)?\b|"
    r"berapa(?:\s+(?:minimum|minimal|maksimum|maximal|besar|banyak|harga|biaya|transaksi))?\b|"
    r"bagaimana(?:\s+cara)?\b|mengapa\b|kenapa\b|apakah\b|"
    r"memanfaatkan\b|manfaatkan\b|menikmati\b|mendapatkan\b|memperoleh\b|"
    r"menggunakan\b|memaksimalkan\b|maksimalkan\b|"
    r"belanja(?:\s+(?:kebutuhan|lebih|hemat|sehari-hari))?\b|"
    r"cek\s+(?:gambar|info|informasi|promo|syarat|ketentuan)\b|"
    r"klik\b|lihat\s+(?:gambar|promo|selengkapnya)\b|"
    r"jangan\s+lewatkan\b|hadir(?:kan|nya)?\b|kembali\s+hadir\b|"
    r"cara\s+(?:mendapatkan|mengikuti|membeli|menukarkan|menggunakan)\b|"
    r"info\s+(?:promo|lengkap)\b|informasi\s+(?:promo|lengkap)\b|"
    r"dengan\s+lebih\s+hemat\b|untuk\s+(?:mendapatkan|menikmati|mengikuti)\b|"
    r"tips\b|manfaat\b|benefit\b|keuntungan\b|akhirnya\b|ternyata\b|"
    r"penawaran\b|mekanisme\b|syarat\b|ketentuan\b|"
    r"daftar\b|simak\b|ketahui\b|berikut\b|inilah\b|ini\s+dia\b|"
    r"begini\b|panduan\b|bocoran\b|intip\b|mengenal\b|"
    r"alasan\b|serba-serbi\b|faq\b|"
    r"(?:indomaret\s*[–—-]\s*)?pwp(?:\s*\(|\s+min\b|\s+minimum\b)|"
    r"purchase\s+with\s+purchase\b"
    r")",
    re.I,
)
EDITORIAL_PHRASE_RE = re.compile(
    r"\b(?:benefit\s+yang\s+didapatkan|apa\s+saja\s+penawaran|"
    r"penawaran\s+dalam\s+promo|mekanisme\s+promo|"
    r"bagaimana\s+cara\s+mendapatkan|cara\s+mendapatkan\s+promo|"
    r"syarat\s+dan\s+ketentuan|promo\s+dan\s+mekanisme|"
    r"yang\s+perlu\s+diketahui|minimum\s+transaksi|minimal\s+transaksi|"
    r"akhir\s+pekan\s+ini\s+untuk\s+menikmati|"
    r"pilihan\s+menu\s+.*\s+dengan\s+lebih\s+hemat|"
    r"bersama\s+promo)\b",
    re.I,
)

# High-confidence conversational/editorial language that can leak from a listing
# card's teaser text. Apply before offer/bundle checks: a campaign paragraph must
# not become a SKU simply because it inherits "Diskon 50%" or "Beli 1 Gratis 1".
CONVERSATIONAL_EDITORIAL_PREFIX_RE = re.compile(
    r"^(?:kalau|jika|bila|ketika|saat)\s+(?:kamu|anda|kalian)\b|"
    r"^(?:buat|bagi)\s+(?:kamu|anda)\b",
    re.I,
)
EDITORIAL_SENTENCE_CUE_RE = re.compile(
    r"\b(?:adalah\s+jawabannya|"
    r"(?:kamu|anda|kalian)\s+(?:bisa|dapat|akan)\s+(?:nikmati|menikmati|"
    r"memilih|menemukan|mendapatkan|menghemat)|"
    r"berbagai\s+penawaran(?:\s+menarik)?|penawaran\s+menarik|"
    r"kalau\s+(?:kamu|anda)\s+lagi\s+cari)\b",
    re.I,
)
EDITORIAL_LONG_PROSE_CUE_RE = re.compile(
    r"\b(?:kamu|anda|kalian|jawabannya|menikmati|penawaran|kebutuhan)\b",
    re.I,
)

# These words are strong evidence that a discount-only line is a campaign/article
# heading rather than a product SKU. They are not used to reject normal priced SKUs.
CAMPAIGN_HEADING_PREFIX_RE = re.compile(
    r"^(?:promo(?:si)?|diskon|katalog|program(?:\s+promo)?|weekend|weekday|"
    r"campaign|kampanye|cek\s+(?:gambar|info|informasi|promo)|info\s+promo|"
    r"lihat\s+promo|jangan\s+lewatkan)\b",
    re.I,
)

STOP_TITLE_TOKENS = {
    "promo", "promosi", "diskon", "katalog", "harga", "murah", "hemat",
    "resmi", "periode", "oktober", "november", "desember", "januari", "februari",
    "maret", "april", "mei", "juni", "juli", "agustus", "september",
    "2024", "2025", "2026", "2027", "dan", "yang", "untuk", "dalam", "apa",
    "saja", "bagaimana", "cara", "benefit", "manfaat", "didapatkan", "mekanisme",
    "penawaran", "weekend", "weekday", "jsm", "psm", "hingga", "sampai", "periode",
}


def _text(value) -> str:
    return SPACE_RE.sub(" ", str(value or "")).strip()


def _tokens(value) -> list[str]:
    return [t.casefold() for t in TOKEN_RE.findall(_text(value))]


def _strip_leading_decorators(value: str) -> str:
    """Remove list bullets/emojis before testing the first editorial word."""
    value = _text(value)
    return re.sub(r"^[^\w]+", "", value, flags=re.UNICODE).strip()


def _has_numeric_price(row: dict, offer: str) -> bool:
    for field in ("normal_price", "promo_price"):
        value = row.get(field)
        if value not in (None, "", 0, "0"):
            try:
                if float(str(value).replace(",", ".")) > 0:
                    return True
            except (TypeError, ValueError):
                return True  # keep unparsed but non-empty source prices for audit
    return bool(PRICE_RE.search(offer))


def _has_bundle(offer: str, row: dict) -> bool:
    return bool(row.get("purchase_condition") or BUNDLE_RE.search(offer))


def _campaign_overlap(name: str, title: str) -> tuple[float, float]:
    name_tokens = set(_tokens(name)) - STOP_TITLE_TOKENS
    title_tokens = set(_tokens(title)) - STOP_TITLE_TOKENS
    if not name_tokens or not title_tokens:
        return 0.0, 0.0
    overlap = len(name_tokens & title_tokens) / max(1, len(name_tokens))
    name_clean = " ".join(sorted(name_tokens))
    title_clean = " ".join(sorted(title_tokens))
    sequence = SequenceMatcher(None, name_clean, title_clean, autojunk=False).ratio()
    return overlap, sequence


def product_candidate_rejection_reason(row: dict, source_mode: str = "") -> str | None:
    """Return a reason when a row is not safe to present as an individual product.

    A rejection is for catalog eligibility only. The original row should be kept
    in an audit file; it is not silently deleted from diagnostics.
    """
    name = _text(row.get("original_product_text") or row.get("product_text") or row.get("Nama Produk"))
    offer = _text(row.get("offer_text") or row.get("detail") or row.get("raw_line"))
    title = _text(row.get("campaign_title") or row.get("title") or row.get("Judul Kampanye"))
    if not name:
        return "missing_product_name"

    clean_name = _strip_leading_decorators(name.strip(" •|:-–—"))
    tokens = _tokens(clean_name)
    if len(clean_name) < 3 or not any(ch.isalpha() for ch in clean_name):
        return "product_name_empty_or_invalid"

    # Reject article/question/imperative fragments even when a discount percentage
    # was split from the same paragraph. The leading-symbol cleanup matters for
    # source lines prefixed by emoji such as "☞ Cek Info Promo ...".
    if EDITORIAL_PREFIX_RE.search(clean_name) or EDITORIAL_PHRASE_RE.search(clean_name):
        return "editorial_heading_misparsed_as_product"

    # Campaign titles and call-to-action headings are never individual SKUs, even
    # when a parser also extracted a price from the surrounding article paragraph.
    if CAMPAIGN_HEADING_PREFIX_RE.search(clean_name):
        return "campaign_or_article_heading_misparsed_as_product"

    # Reject sentence-like campaign teasers independent of source type and offer
    # evidence. Structured cards can accidentally expose the whole teaser anchor,
    # and their generic bundle discount must not make that paragraph product-like.
    if CONVERSATIONAL_EDITORIAL_PREFIX_RE.search(clean_name) or EDITORIAL_SENTENCE_CUE_RE.search(clean_name):
        return "editorial_sentence_misparsed_as_product"
    if len(tokens) >= 16 and EDITORIAL_LONG_PROSE_CUE_RE.search(clean_name) and re.search(r"[,!?;]", clean_name):
        return "long_editorial_sentence_misparsed_as_product"

    # A repeated pair of leading numbers is a known symptom of a malformed card
    # extraction (e.g. "2 2 Zespri Kiwi Sungold 2"), not reliable SKU identity.
    if re.match(r"^\d+\s+\d+\s+[A-Za-z]", clean_name):
        return "leading_numeric_noise_in_product_name"

    has_price = _has_numeric_price(row, offer)
    has_bundle = _has_bundle(offer, row)
    has_discount = bool(DISCOUNT_RE.search(offer))
    has_offer = bool(OFFER_RE.search(offer)) or has_price or has_bundle

    if not has_offer:
        return "no_product_level_offer_evidence"

    # A line containing only a campaign-level percentage is not sufficient proof
    # of an individual SKU. This rule deliberately does not depend on campaign_title:
    # older CSV rows often have no title populated.
    if has_discount and not has_price and not has_bundle:
        overlap, sequence = _campaign_overlap(clean_name, title)
        name_words = len(tokens)
        name_cf = clean_name.casefold()
        headline_terms = bool(re.search(
            r"\b(?:promo|promosi|weekend|weekday|campaign|kampanye|benefit|penawaran|mekanisme|cara|apa|bagaimana|cek\s+info)\b",
            name_cf,
        ))
        mode = (source_mode or row.get("source_mode") or row.get("source_name") or "").casefold()
        merchant = _text(row.get("merchant_name") or row.get("merchant"))
        structured_product_card = "hemat_card" in mode and bool(merchant)

        # If the alleged product starts with a campaign label, the string itself is
        # a campaign heading. Do not require title overlap: legacy rows may omit it.
        if CAMPAIGN_HEADING_PREFIX_RE.search(clean_name):
            return "campaign_discount_claim_misparsed_as_product"

        # Very short prose fragments (e.g. 'Akhirnya') cannot identify an SKU when
        # the only offer is a percentage claim. Structured cards with a known
        # merchant are the narrow exception because their DOM context is product-level.
        has_sku_attribute = bool(re.search(
            r"\b\d+(?:[.,]\d+)?\s*(?:ml|l|g|kg|mg|pcs?|pack|sachet|tablet|kaplet|botol|kaleng|gr|gram)\b|\b\d+\s*[x×]\s*\d+",
            clean_name,
            re.I,
        ))
        if name_words <= 1 and not has_sku_attribute and not structured_product_card:
            return "discount_claim_without_product_identity"

        # A discount-only line that is mostly editorial prose is rejected even when
        # the listing/article title is missing or does not match the fragment.
        if headline_terms and (overlap >= 0.35 or sequence >= 0.45 or name_words <= 3):
            return "campaign_discount_claim_misparsed_as_product"
        if EDITORIAL_PHRASE_RE.search(clean_name):
            return "editorial_heading_misparsed_as_product"
        if name_words >= 8 or len(clean_name) > 80:
            if overlap >= 0.35 or sequence >= 0.72:
                return "long_narrative_with_discount_claim_not_product_sku"

    # Product names extracted from article paragraphs should look like SKU labels,
    # not entire editorial sentences. Structured card sources can have longer labels,
    # so the extra length guard is applied to article-like records only.
    mode = (source_mode or row.get("source_mode") or row.get("source_name") or "").casefold()
    article_mode = any(x in mode for x in ("article", "katalogpromosi", "scanharga", "momsmoney", "kontan"))
    if article_mode and len(tokens) >= 16:
        overlap, sequence = _campaign_overlap(clean_name, title)
        # Long names are acceptable for grouped variants (e.g. several flavours/weights).
        # Reject only when long text also strongly mirrors its article/campaign title.
        if overlap >= 0.45 or sequence >= 0.72:
            return "article_paragraph_too_long_for_product_name"

    # A candidate should include a recognisable offer, not simply be a product name
    # accidentally captured from page chrome. No-price bundle offers are allowed.
    if not has_price and not has_bundle and not has_discount:
        if not re.search(r"\b(?:cashback|voucher|kupon|tebus\s+murah|gratis)\b", offer, re.I):
            return "no_product_level_offer_evidence"

    return None
