"""Regression checks for the local poster OCR v2 upgrade."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw
from openpyxl import load_workbook

import promo_poster_ocr as ocr


def test_packaging_percentage_is_not_a_discount_claim():
    text = "ENERGEN COKELAT | 30% LEBIH BANYAK SEREAL | Rp28.900 | Rp20.900"
    result = ocr.analyze_discount_claim(text)
    assert result["claim"] is None
    assert result["source"] == "PERSENTASE_KEMASAN_DIKECUALIKAN"
    assert "30%" in result["ignored"]

    parsed = ocr.interpret_price_pair(
        "Harga normal Rp28.900 | Harga promo Rp20.900 | 30% LEBIH BANYAK SEREAL"
    )
    assert parsed["normal_price_candidate"] == 28900
    assert parsed["promo_price_candidate"] == 20900
    assert parsed["discount_claim_percent"] == ""
    assert parsed["discount_claim_source"] == "PERSENTASE_KEMASAN_DIKECUALIKAN"


def test_packaging_percentage_split_over_ocr_lines_is_excluded():
    result = ocr.analyze_discount_claim(
        "ENERGEN | 30% | LEBIH BANYAK SEREAL | Rp28.900 | Rp20.900"
    )
    assert result["claim"] is None
    assert "30%" in result["ignored"]


def test_explicit_discount_is_kept_and_conflicting_claims_are_ambiguous():
    result = ocr.analyze_discount_claim(
        "Diskon 30% | Harga normal Rp28.900 | Harga promo Rp20.900"
    )
    assert result["claim"] == 30.0
    assert result["source"] == "KATA_DISKON"

    result = ocr.analyze_discount_claim("Diskon 20% | Promo diskon 30% | Rp20.000")
    assert result["claim"] is None
    assert result["source"] == "KLAIM_DISKON_AMBIGU"


def test_context_does_not_mistake_packaging_extra_for_retailer_multibuy():
    context = ocr.classify_promo_context("KODOMO | BUY 1 EXTRA 1 | Rp12.900")
    assert not context["purchase_condition"]
    assert "TEKS TAMBAHAN/KLAIM KEMASAN TERDETEKSI" in context["promo_context_tags"]
    assert "PROMO MULTI-ITEM TERDETEKSI" not in context["promo_context_tags"]

    context = ocr.classify_promo_context("Beli 2 Gratis 1 | Member Rp15.000 | Harga spesial")
    assert context["purchase_condition"].casefold() == "beli 2 gratis 1"
    assert context["member_price_candidate"] == 15000
    assert "LABEL HARGA SPESIAL TERDETEKSI" in context["promo_context_tags"]


def test_stacked_price_pair_has_explicit_method_and_needs_review():
    lines = [
        {"text": "Rp28.900", "confidence": 0.9, "x0": 450, "x1": 560, "y0": 790, "y1": 820},
        {"text": "Rp20.900", "confidence": 0.9, "x0": 450, "x1": 560, "y0": 850, "y1": 880},
    ]
    parsed = ocr.interpret_price_pair("Rp28.900 | Rp20.900")
    pair = ocr._infer_stacked_price_pair(parsed, lines, 1200, 1500)
    assert pair["normal_price_candidate"] == 28900
    assert pair["promo_price_candidate"] == 20900
    assert pair["price_pair_method"] == "INFERENSI_GEOMETRI_SEJAJAR"


def test_color_price_tag_detector_finds_local_red_and_yellow_labels():
    image = Image.new("RGB", (1200, 1500), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((200, 510, 430, 555), fill=(255, 210, 0))  # yellow normal price
    draw.rectangle((200, 610, 455, 660), fill=(230, 0, 20))  # red promo price
    boxes = ocr._detect_color_price_tag_boxes(image)
    colors = {box[4] for box in boxes}
    assert "YELLOW" in colors
    assert "RED" in colors


def test_review_workbook_adds_audit_columns_without_changing_decision_gate(tmp_path: Path):
    candidate = {
        "candidate_id": "TEST-CANDIDATE", "source_name": "Poster lokal", "campaign_title": "Kampanye uji",
        "article_url": "https://example.test/article", "image_url": "https://example.test/poster.jpg",
        "ocr_snippet": "Rp28.900 | Rp20.900", "price_values_json": "[28900, 20900]",
        "normal_price_candidate": 28900, "promo_price_candidate": 20900,
        "discount_calculated_percent": 27.7, "discount_validation_status": "TIDAK ADA KLAIM DISKON",
        "discount_difference_pp": "", "price_pair_method": "INFERENSI_GEOMETRI_SEJAJAR",
        "promo_context_tags": "LABEL HARGA NORMAL/PROMO TERDETEKSI", "purchase_condition": "",
        "regional_scope": "", "member_price_candidate": "",
        "ignored_packaging_percentages": "30% — 30% LEBIH BANYAK SEREAL",
        "discount_claim_source": "PERSENTASE_KEMASAN_DIKECUALIKAN",
        "discount_claim_context": "30% | LEBIH BANYAK SEREAL",
    }
    out = tmp_path / "review.xlsx"
    ocr.make_review_workbook([candidate], out, {})
    wb = load_workbook(out, data_only=False)
    try:
        ws = wb["Kandidat OCR"]
        assert ws.max_row == 2
        assert ws.max_column == len(ocr.REVIEW_HEADERS) == 35
        assert ws.cell(2, 2).value == "CEK"
        assert ws.cell(2, 28).value == "INFERENSI_GEOMETRI_SEJAJAR"
        assert "30%" in ws.cell(2, 33).value
        assert ws.cell(2, 34).value == "PERSENTASE_KEMASAN_DIKECUALIKAN"
        assert "TERIMA" in " ".join(str(dv.formula1 or "") for dv in ws.data_validations.dataValidation)
    finally:
        wb.close()


def test_cache_and_line_schemas_version_the_pipeline_and_preserve_recognition_method():
    assert "ocr_pipeline_version" in ocr.CACHE_FIELDS
    assert ocr.OCR_PIPELINE_VERSION == "poster-ocr-v2.1-fast-crop"
    assert "recognition_method" in ocr.LINE_FIELDS
    saved = {"lines_json": '[{"text":"20.900","recognition_method":"COLOR_PRICE_TAG_CROP_RED"}]'}
    lines = ocr._line_records(saved)
    assert lines[0]["recognition_method"] == "COLOR_PRICE_TAG_CROP_RED"


def test_color_crop_rescue_skips_tag_when_full_page_ocr_already_found_price(monkeypatch):
    image = Image.new("RGB", (1200, 1500), "white")
    boxes = [
        (200, 510, 430, 555, "YELLOW"),
        (600, 610, 830, 655, "RED"),
    ]
    monkeypatch.setattr(ocr, "_detect_color_price_tag_boxes", lambda _image: boxes)
    calls = []
    def fake_crop(_crop, _engine, _backend):
        calls.append(True)
        return "Rp20.900", 0.9
    monkeypatch.setattr(ocr, "_crop_ocr_price_text", fake_crop)
    lines = [{
        "text": "Rp28.900", "confidence": 0.9,
        "x0": 230, "y0": 520, "x1": 390, "y1": 545,
        "recognition_method": "FULL_PAGE",
    }]
    augmented = ocr._augment_with_color_price_tag_crops(image, lines, object(), "rapidocr")
    # The first tag is already covered by a full-page price. Only the missing
    # second tag receives a crop inference and is added as evidence.
    assert len(calls) == 1
    assert len(augmented) == 2
    assert any(line.get("recognition_method") == "COLOR_PRICE_TAG_CROP_RED" for line in augmented)


def test_color_crop_rescue_has_a_per_image_upper_bound(monkeypatch):
    image = Image.new("RGB", (1200, 1500), "white")
    boxes = [(100 + 100*i, 500 + 50*i, 180 + 100*i, 530 + 50*i, "RED") for i in range(12)]
    monkeypatch.setattr(ocr, "_detect_color_price_tag_boxes", lambda _image: boxes)
    calls = []
    def fake_crop(_crop, _engine, _backend):
        calls.append(True)
        return "not a price", 0.0
    monkeypatch.setattr(ocr, "_crop_ocr_price_text", fake_crop)
    ocr._augment_with_color_price_tag_crops(image, [], object(), "rapidocr")
    assert len(calls) == ocr.MAX_COLOR_PRICE_CROPS_PER_IMAGE == 6
