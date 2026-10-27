"""Regression tests for product-title and offer-context separation."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import promo_poster_ocr as ocr


def test_tebus_murah_suffix_does_not_pollute_product_title():
    assert ocr._clean_local_product_line(
        "ALUMINUM20CM TEBUS MURAH dengon 5 DIGITAL STIKER"
    ) == "ALUMINUM20CM"
    assert ocr._clean_local_product_line(
        "SAUCEPAN TEBUS MURAH dengan 5 DIGITALSTIKER"
    ) == "SAUCEPAN"
    assert ocr._clean_local_product_line(
        "GLASS FOOD CONTAINER 1050ML WITH GLASS LID AND STRAINER TEBUS MURAH dengan 5 DIGITAL STIKER"
    ) == "GLASS FOOD CONTAINER 1050ML WITH GLASS LID AND STRAINER"


def test_tebus_murah_prefix_can_preserve_real_product_name():
    assert ocr._clean_local_product_line("Tebus Murah Telur Ayam Negeri isi 10's") == "Telur Ayam Negeri isi 10's"
    assert ocr._clean_local_product_line(
        "dapat tambahan potongan Rp 3.000 Tebus Murah Telur Ayam Negeri isi 10's"
    ) == "Telur Ayam Negeri isi 10's"


def test_generic_tebus_murah_heading_is_not_a_product_name():
    assert ocr._clean_local_product_line("Bisa Tebus Murah Produk dibawah ini") == ""
    assert ocr._clean_local_product_line(
        "TEBUS MURAH Peralatan Dapur Praktis SegarnyaJuara Dipilih Pakai Hati"
    ) == ""


def test_tebus_murah_is_written_as_purchase_condition():
    result = ocr.classify_promo_context(
        "CASSEROLECAST ALUMINUM20CM | TEBUS MURAH | dengon | 5 DIGITALSTIKER | RP249.900"
    )
    assert "tebus murah" in result["purchase_condition"].casefold()
    assert "5 digital stiker" in result["purchase_condition"].casefold()


def test_regional_list_p_jowo_and_kec_batam_is_not_a_product_title():
    text = "P.Jowo,Bali,Sumatera (kec. Batam)"
    assert ocr._is_regional_pricing_segment(text)
    assert ocr._clean_local_product_line(text) == ""
    result = ocr.classify_promo_context(text)
    assert "P.Jowo" in result["regional_scope"]
    assert "Batam" in result["regional_price_context"]
    assert result["member_offer_classification"] == "TIDAK_TERDETEKSI"


def test_regional_prefix_is_removed_but_real_sku_title_is_kept():
    text = "Kupang Ternate Jayapura Abepura Wafer double ckt matcha ASTOR"
    assert ocr._clean_local_product_line(text) == "Wafer double ckt matcha ASTOR"
    assert ocr._clean_local_product_line("Lombok Kalimantan") == ""


def test_explicit_member_price_can_attach_to_one_unique_nearby_amount():
    first = ocr.classify_promo_context("172.900 | KHUSUS MEMBER | Rp")
    second = ocr.classify_promo_context("KHUSUS MEMBER | Rp | 37.500 | Indomart")
    assert first["member_price_candidate"] == 172900
    assert second["member_price_candidate"] == 37500
    assert first["member_offer_classification"] == "HARGA_MEMBER"
    assert second["member_offer_classification"] == "HARGA_MEMBER"


def test_member_discount_minimum_spend_and_regional_prices_are_not_flattened():
    discount = ocr.classify_promo_context("Khusus Member Potongan Rp 500")
    minimum = ocr.classify_promo_context("Member Alfamidi minimal Rp100.000")
    regional = ocr.classify_promo_context("Member: Sumatera Rp39.500, Kalimantan Rp41.000")
    assert discount["member_discount_candidate"] == 500
    assert discount["member_price_candidate"] == ""
    assert minimum["minimum_spend_candidate"] == 100000
    assert minimum["member_price_candidate"] == ""
    assert regional["member_offer_classification"] == "HARGA_REGIONAL"
    assert regional["member_price_candidate"] == ""


def test_member_price_is_not_guessed_when_nearby_prices_conflict():
    result = ocr.classify_promo_context("109.000 | 99.000 | KHUSUS MEMBER | Rp")
    assert result["member_price_candidate"] == ""
    assert result["member_offer_classification"] == "KETENTUAN_MEMBER"


def test_metrics_separate_hard_quarantine_from_raw_name_withheld():
    rows = [
        {"local_product_name_suggestion_status": "DIKARANTINA — TIDAK ADA NAMA PRODUK AMAN", "local_product_name_suggestion_raw": "GoPay Bayar Pakai"},
        {"local_product_name_suggestion_status": "DIPISAHKAN DARI TEKS PROMOSI/KETENTUAN", "local_product_name_suggestion_raw": "MINIMAL BELANJA"},
        {"local_product_name_suggestion_status": "SARAN OCR LOKAL — PERLU VERIFIKASI", "local_product_name_suggestion_raw": ""},
    ]
    metrics = ocr._candidate_metrics(rows)
    assert metrics["candidate_product_name_quarantined_count"] == 1
    assert metrics["candidate_raw_name_withheld_count"] == 2


def test_candidate_version_is_a_pipeline_marker_not_a_behavior_contract():
    assert ocr.CANDIDATE_PIPELINE_VERSION.startswith("poster-ocr-")


def test_image_summary_reports_raw_names_separately_from_hard_quarantine(monkeypatch):
    monkeypatch.setattr(ocr, "_is_current_ocr_cache", lambda row: True)
    monkeypatch.setattr(ocr, "_line_records", lambda row: [{"text": "OCR line"}])
    image = {"image_url": "https://example.test/poster.jpg", "source_name": "Test"}
    cache = {
        image["image_url"]: {
            "ocr_status": "ok", "ocr_backend": "rapidocr",
            "ocr_pipeline_version": ocr.OCR_PIPELINE_VERSION, "processed_at": "2026-10-27T00:00:00+07:00",
        }
    }
    candidates = [
        {
            "image_url": image["image_url"],
            "local_product_name_suggestion_status": "DIKARANTINA — TIDAK ADA NAMA PRODUK AMAN",
            "local_product_name_suggestion_raw": "GoPay Bayar Pakai",
            "member_price_candidate": 37500,
            "regional_price_context": "P.Jowo Bali Sumatera (kec. Batam)",
            "purchase_condition": "Tebus murah dengan 5 digital stiker",
        },
        {
            "image_url": image["image_url"],
            "local_product_name_suggestion_status": "DIPISAHKAN DARI TEKS PROMOSI/KETENTUAN",
            "local_product_name_suggestion_raw": "MINIMAL BELANJA",
            "member_offer_classification": "MINIMUM_BELANJA",
        },
    ]
    row = ocr._image_summary_rows([image], cache, candidates)[0]
    assert set(ocr.IMAGE_SUMMARY_FIELDS) == set(row)
    assert row["candidate_product_name_quarantined_count"] == 1
    assert row["candidate_raw_name_withheld_count"] == 2
    assert row["candidate_member_price_count"] == 1
    assert row["candidate_regional_price_context_count"] == 1
    assert row["candidate_tebus_murah_count"] == 1


def test_member_only_labels_are_not_product_name_suggestions():
    assert ocr._clean_local_product_line("KHUSUS MEMBER") == ""
    assert ocr._clean_local_product_line("Khusus Member 750gRp") == ""
    assert ocr._clean_local_product_line("KHUSUS MEMBER MY SUPER INDO") == ""


def test_member_label_suffix_preserves_a_real_product_title():
    assert ocr._clean_local_product_line("Product 350g Khusus Member") == "Product 350g"
    assert ocr._clean_local_product_line("Sunlight 750 ml Khusus Member") == "Sunlight 750 ml"


def test_paylater_limit_copy_is_quarantined_but_product_prefix_is_preserved():
    standalone = "Khusus Indodana PayLater Limit x/user/periode"
    assert ocr._contains_payment_campaign_cue(standalone)
    assert ocr._clean_local_product_line(standalone) == ""
    assert ocr._contains_payment_campaign_cue("• / Later Limit 1x/user")
    assert ocr._clean_local_product_line("• / Later Limit 1x/user") == ""
    mixed = "AQUA Air Mnrl PET 600ml Aplikasi GoPay Bayar Pakai Pay"
    assert ocr._clean_local_product_line(mixed) == "AQUA Air Mnrl PET 600ml"


def test_bare_or_generic_tebus_murah_is_not_broadcast_as_product_condition():
    assert not ocr.classify_promo_context("Bisa Tebus Murah Produk dibawah ini")["purchase_condition"]
    assert not ocr.classify_promo_context("TEBUS MURAH")["purchase_condition"]
    result = ocr.classify_promo_context("TEBUS MURAH | dengon | 5 DIGITALSTIKER")
    assert result["purchase_condition"] == "Tebus murah dengan 5 digital stiker"
