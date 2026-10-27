"""Regression tests for OCR campaign-context cleanup."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import promo_poster_ocr as ocr


def test_campaign_steps_and_slogans_are_not_sku_titles():
    assert ocr._clean_local_product_line(
        "1.DOWNLOAD Download Aplikasi MY SUPER INDO 2.BELANJA"
    ) == ""
    assert ocr._clean_local_product_line(
        "3.TUKARKAN Peralatan Dapur Praktis SegarnyaJuara Dipilih Pakai Hati"
    ) == ""
    assert ocr._clean_local_product_line("mudah dan") == ""
    assert ocr._clean_local_product_line(
        "3.TUKARKAN CASSEROLECAST ALUMINUM20CM SegarnyaJuara Dipilih Pakai Hati"
    ) == "CASSEROLECAST ALUMINUM20CM"


def test_tebus_murah_sticker_suffix_is_a_condition_not_a_product_name_suffix():
    assert ocr._clean_local_product_line(
        "CASSEROLECAST ALUMINUM20CM dengon 5 DIGITAL STIKER"
    ) == "CASSEROLECAST ALUMINUM20CM"
    result = ocr.classify_promo_context(
        "TEBUS MURAH | CASSEROLECAST ALUMINUM20CM dengon 5 DIGITALSTIKER | RP249.900"
    )
    assert "tebus murah" in result["purchase_condition"].casefold()
    assert "5 digital stiker" in result["purchase_condition"].casefold()


def test_ocr_corrupted_paylater_footer_is_detected_and_removed_only_from_name():
    samples = [
        "Later Ix/ /poriode",
        "di Aplikosi timit tx Tidak boriaku al Kilk",
        "Ix/usor/poriode Tidak barlaku di Klik",
    ]
    for sample in samples:
        assert ocr._contains_payment_campaign_cue(sample), sample
        assert ocr._clean_local_product_line(sample) == "", sample
    mixed = "NUTRIBRA Pro 900g di Aplikosi timit tx Tidak boriaku al Kilk"
    assert ocr._clean_local_product_line(mixed) == "NUTRIBRA Pro 900g"
