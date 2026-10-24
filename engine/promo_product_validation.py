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
    r"bagaimana(?:\s+cara)?\b|gimana(?:\s+cara)?\b|mengapa\b|kenapa\b|apakah\b|"
    r"memanfaatkan\b|manfaatkan\b|menikmati\b|mendapatkan\b|memperoleh\b|"
    r"menggunakan\b|memaksimalkan\b|maksimalkan\b|"
    r"belanja(?:\s+(?:kebutuhan|lebih|hemat|sehari-hari))?\b|"
    r"jangan\s+sampai\s+(?:kelewatan|ketinggalan|terlewat|lupa)\b|"
    r"buruan\s+(?:ke|cek|lihat|kunjungi|beli)\b|"
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

# These labels describe purchase thresholds, not a product/SKU. Keeping them as
# product rows causes the threshold amount to be mistaken for a promo price.
PURCHASE_CONDITION_ONLY_RE = re.compile(
    r"^(?:(?:syarat\s+)?(?:minimum|minimal)\s+(?:belanja|pembelanjaan|transaksi|pembelian)|"
    r"min\.?\s*(?:belanja|pembelanjaan|transaksi|pembelian))$",
    re.I,
)

# Payment benefit amounts can sit next to an unrelated/fragmented product name
# in article text. They are not price evidence for that product.
PAYMENT_PROMO_CONTEXT_RE = re.compile(
    r"\b(?:qris|bca\s+mobile|mybca|kartu\s+(?:kredit|debit)|paylater|cashback|cash\s*back|"
    r"promo\s+bank|pembayaran|transaksi\s+bank|rekening|uang\s+kembali)\b",
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

    # Purchase thresholds belong in purchase_condition, never in the product-name
    # or promo-price fields. Match the whole label to avoid rejecting real products
    # whose names merely contain the word "belanja" or "transaksi".
    if PURCHASE_CONDITION_ONLY_RE.fullmatch(clean_name):
        return "purchase_condition_misparsed_as_product"

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
    article_mode = any(x in mode for x in ("article", "katalogpromosi", "scanharga"))
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


# Product identity is assessed from evidence and row structure, not a list of
# retailer/brand names. Ambiguous names are routed to review instead of rejected.
PRODUCT_SPEC_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:ml|l|g|kg|mg|mcg|pcs?|pack|sachet|tablet|kaplet|botol|kaleng|gr|gram|liter|meter|cm|mm|inch|inci|sheets?|lembar|rolls?)\b|"
    r"\b\d+\s*[x×]\s*\d+\b|\b(?:model|sku|kode)\s*[:#-]?\s*[a-z0-9-]+",
    re.I,
)
PRODUCT_CODE_RE = re.compile(r"\b(?=[a-z0-9./-]{4,}\b)(?=[a-z0-9./-]*[a-z])(?=[a-z0-9./-]*\d)[a-z0-9./-]+\b", re.I)


def product_candidate_review_reason(row: dict, source_mode: str = "") -> str | None:
    """Return a review reason for plausible but weakly identified candidates.

    This is deliberately not a merchant/product-name deny-list. Generic-looking
    names are preserved for review unless strong editorial syntax already rejects
    them. Item-level specs, source links, direct price lines, and cross-source
    corroboration help establish product identity dynamically.
    """
    if product_candidate_rejection_reason(row, source_mode=source_mode):
        return None  # high-confidence non-products already go to rejected audit

    name = _text(row.get("original_product_text") or row.get("product_text") or row.get("Nama Produk"))
    merchant = _text(row.get("merchant_name") or row.get("merchant"))
    offer = _text(row.get("offer_text") or row.get("detail") or row.get("raw_line"))
    raw_line = _text(row.get("raw_line"))
    source_url = _text(row.get("source_url") or row.get("source_page_url"))
    item_url = _text(row.get("source_item_url"))
    mode = (source_mode or row.get("source_mode") or row.get("source_name") or "").casefold()
    tokens = _tokens(name)
    if not name or not tokens:
        return "product_identity_missing"

    has_spec = bool(PRODUCT_SPEC_RE.search(name) or PRODUCT_CODE_RE.search(name))
    normalized_name = " ".join(tokens)
    normalized_merchant = " ".join(_tokens(merchant))
    same_as_merchant = bool(normalized_merchant and normalized_name == normalized_merchant)
    has_cross_source_support = len(pipe_sources_for_validation(row)) >= 2
    has_item_link = bool(item_url and source_url and item_url.rstrip("/") != source_url.rstrip("/"))
    has_direct_price_line = bool(
        raw_line
        and normalized_name
        and normalized_name in " ".join(_tokens(raw_line))
        and PRICE_RE.search(raw_line)
        and _has_numeric_price(row, offer)
    )
    has_price = _has_numeric_price(row, offer)
    has_bundle = _has_bundle(offer, row)
    has_discount = bool(DISCOUNT_RE.search(offer))
    has_item_offer = has_price or has_bundle or has_discount

    # A short fragment paired with payment language (e.g. "Extra Diskon" plus
    # "Rp 2.000 dengan QRIS BCA") is not adequately linked to a product price.
    # Put it in review before a direct-price or cross-source shortcut can bless it.
    payment_context = " ".join([
        offer, _text(row.get("campaign_title") or row.get("title")),
        _text(row.get("deal_type")), _text(row.get("purchase_condition")),
    ])
    if len(tokens) <= 2 and not has_spec and PAYMENT_PROMO_CONTEXT_RE.search(payment_context):
        return "short_name_with_payment_offer_needs_review"

    # An item name with size/model/variant-like evidence is specific enough even
    # when the merchant is new and absent from any canonical alias table.
    if has_spec:
        return None
    if has_cross_source_support:
        return None
    if has_direct_price_line:
        return None
    if has_item_link and has_price:
        return None

    # A row that is literally just the merchant label is not automatically
    # discarded: it remains visible in review while product-level evidence is
    # collected. Thus a future merchant such as 'All Fruit' is not hard-blocked.
    if same_as_merchant:
        return "ambiguous_candidate_matches_merchant_label"

    # Very short names are inherently ambiguous without SKU attributes or item-
    # specific provenance. Keep them for review rather than hard-rejecting them.
    # This intentionally applies to unknown/new names too, without special cases.
    if len(tokens) <= 2 and not (has_item_link and has_item_offer):
        if has_item_offer:
            return "ambiguous_short_name_needs_item_level_evidence"
        return "short_name_without_item_level_evidence"

    # If a row has only a campaign-level percentage/bundle claim and no item-level
    # link/price evidence, keep it out of confirmed products until reviewed.
    if has_item_offer and not has_price and not has_bundle and not has_direct_price_line:
        return "offer_evidence_may_be_campaign_level"

    return None


def pipe_sources_for_validation(row: dict) -> list[str]:
    """Count distinct publisher/source labels, never count a CSV filename as a source."""
    # source_names is populated with multiple publishers after cross-source merge;
    # source_dataset is a file name and must not count as corroboration.
    # Only explicit publisher labels count as corroboration. A dataset/file
    # name is provenance of storage, not an independent publication.
    raw = _text(row.get("source_names") or row.get("source_name"))
    values = []
    for value in re.split(r"\s*[|;,]\s*", raw):
        value = value.strip()
        if value and value not in values:
            values.append(value)
    return values
