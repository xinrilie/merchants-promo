"""Regression coverage for poster campaign text that must not become SKU names."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import promo_poster_ocr as ocr


def _line(text: str, x0: int, y0: int, x1: int, y1: int, confidence: float = 0.97) -> dict:
    return {"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "confidence": confidence}


def test_digital_sticker_condition_is_removed_even_when_ocr_splits_the_line():
    assert ocr._clean_local_product_line("CASSEROLECAST ALUMINUM20CM dengon 5 DIGITAL STIKER") == "CASSEROLECAST ALUMINUM20CM"
    assert ocr._clean_local_product_line("DUAL COHPARTMENT FOOD THERMAL B0OML 5 DIGITAL STIKER") == "DUAL COHPARTMENT FOOD THERMAL B0OML"
    assert ocr._clean_local_product_line("5 DIGITAL STIKER") == ""
    assert ocr._clean_local_product_line("dengon") == ""


def test_campaign_cta_is_not_a_product_name():
    assert ocr._clean_local_product_line("DAPATKAN mulai dari 5 DIGITAL STIKER") == ""
    assert ocr._clean_local_product_line("Dapatkan") == ""
    assert ocr._clean_local_product_line("mulai dari") == ""
    assert ocr._clean_local_product_line("Dua Warna SEJUTA RASA") == ""


def test_split_ocr_title_is_cleaned_again_after_assembly():
    lines = [
        _line("GLASS FOOD CONTAINER 1050ML WITH GLASS LID AND STRAINER", 260, 94, 470, 104),
        _line("TEBUS MURAH", 270, 104, 350, 113),
        _line("dengon", 270, 113, 310, 122),
        _line("5 DIGITAL STIKER", 270, 122, 390, 131),
    ]
    name = ocr._local_product_name_suggestion(
        lines, center_x=360, center_y=150, width=862, height=278,
        max_dx=0.17, max_dy=0.25,
    )
    assert name == "GLASS FOOD CONTAINER 1050ML WITH GLASS LID AND STRAINER"
    assert "stiker" not in name.casefold()
    assert "tebus murah" not in name.casefold()


def test_known_poster_corner_mark_is_not_a_sku_but_is_recognized_as_campaign_artifact():
    # This exact short string appears as a tiny corner mark on the supplied
    # Super Indo poster. We do not infer/correct what the mark is meant to say.
    text = "Carl Schrin Seho"
    assert ocr._clean_local_product_line(text) == ""
    assert ocr._is_campaign_legal_line(text)


def test_tebus_murah_purchase_condition_is_still_retained_for_review():
    context = "CASSEROLECAST | ALUMINUM20CM | TEBUS MURAH | dengon | 5 DIGITAL STIKER | RP249.900"
    result = ocr.classify_promo_context(context)
    assert "tebus murah" in result["purchase_condition"].casefold()
    assert "5 digital stiker" in result["purchase_condition"].casefold()
