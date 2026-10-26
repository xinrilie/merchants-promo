from promo_poster_ocr import extract_prices, interpret_price_pair, make_price_windows, build_candidates


def test_reads_labelled_normal_promo_and_discount():
    result = interpret_price_pair("Harga Normal Rp31.500 | Harga Promo Rp17.500 | Diskon 44%")
    assert result["normal_price_candidate"] == 31500
    assert result["promo_price_candidate"] == 17500
    assert result["discount_claim_percent"] == 44.0
    assert result["discount_calculated_percent"] == 44.4


def test_does_not_pair_unlabelled_prices_without_matching_discount_claim():
    result = interpret_price_pair("Rp100.000 | Rp89.000")
    assert result["normal_price_candidate"] == ""
    assert result["promo_price_candidate"] == ""
    assert "tanpa label" in result["candidate_note"] or "tidak cocok" in result["candidate_note"]


def test_unlabelled_pair_can_be_suggested_only_inside_compact_poster_window():
    # Generic text parsing remains conservative; the spatial poster review may
    # suggest high/low only when exactly two plausible prices share one local tile.
    safe = interpret_price_pair("Rp100.000 | Rp89.000")
    local = interpret_price_pair("Sunsilk Shampoo 170 ml | Rp20.000 | Rp16.000", allow_unlabelled_pair=True)
    assert safe["normal_price_candidate"] == ""
    assert local["normal_price_candidate"] == 20000
    assert local["promo_price_candidate"] == 16000
    assert local["discount_calculated_percent"] == 20.0
    assert "WAJIB cocokkan poster" in local["candidate_note"]


def test_single_explicit_label_can_pair_the_other_price_in_local_tile():
    normal_label = interpret_price_pair("Harga Normal Rp30.000 | Rp21.000", allow_unlabelled_pair=True)
    promo_label = interpret_price_pair("Harga Promo Rp21.000 | Rp30.000", allow_unlabelled_pair=True)
    assert (normal_label["normal_price_candidate"], normal_label["promo_price_candidate"]) == (30000, 21000)
    assert (promo_label["normal_price_candidate"], promo_label["promo_price_candidate"]) == (30000, 21000)


def test_does_not_pair_when_discount_math_disagrees():
    result = interpret_price_pair("Rp100.000 | Rp80.000 | Diskon 10%")
    assert result["normal_price_candidate"] == ""
    assert result["promo_price_candidate"] == ""
    assert "tidak cocok" in result["candidate_note"]


def test_extract_prices_skips_units_and_campaign_years():
    prices = extract_prices("Susu 1 L tahun 2026; Harga Promo Rp15.900")
    assert [row["price"] for row in prices] == [15900]


def test_extract_prices_recovers_common_ocr_rp_typos_and_colon_separator():
    prices = extract_prices("R33.900 | Rp 15.900 | 31:900")
    assert [row["price"] for row in prices] == [33900, 15900, 31900]


def test_candidate_preserves_source_poster_and_suggests_product_from_article():
    lines = [
        {"text": "Sunlight Lime 910 ml", "confidence": 0.95, "x0": 100, "y0": 120, "x1": 470, "y1": 165},
        {"text": "Harga Normal Rp20.000", "confidence": 0.95, "x0": 100, "y0": 180, "x1": 470, "y1": 220},
        {"text": "Harga Promo Rp16.900", "confidence": 0.95, "x0": 100, "y0": 230, "x1": 470, "y1": 270},
        {"text": "Diskon 15%", "confidence": 0.95, "x0": 100, "y0": 280, "x1": 470, "y1": 320},
    ]
    image = {"source_name": "Katalogpromosi.com", "campaign_title": "Promo Mingguan", "article_url": "https://example.test/article", "image_url": "https://example.test/poster.jpg"}
    products = [{"product_text": "Sunlight Lime 910 ml", "product_validation_status": "eligible"}]
    candidates = build_candidates(image, lines, 800, 1000, products)
    assert candidates
    assert all(row["article_url"] == image["article_url"] for row in candidates)
    assert all(row["image_url"] == image["image_url"] for row in candidates)
    assert any(row["normal_price_candidate"] == 20000 and row["promo_price_candidate"] == 16900 for row in candidates)


def test_poster_price_windows_keep_two_adjacent_skus_separate():
    lines = [
        {"text": "Sunlight Lime 910 ml", "confidence": 0.96, "x0": 90, "y0": 100, "x1": 300, "y1": 130},
        {"text": "Rp20.000", "confidence": 0.96, "x0": 140, "y0": 145, "x1": 260, "y1": 175},
        {"text": "Rp16.000", "confidence": 0.96, "x0": 140, "y0": 180, "x1": 260, "y1": 210},
        {"text": "Dove Body Wash 400 ml", "confidence": 0.96, "x0": 610, "y0": 100, "x1": 850, "y1": 130},
        {"text": "Rp31.500", "confidence": 0.96, "x0": 660, "y0": 145, "x1": 790, "y1": 175},
        {"text": "Rp25.200", "confidence": 0.96, "x0": 660, "y0": 180, "x1": 790, "y1": 210},
    ]
    image = {
        "source_name": "Katalogpromosi.com", "campaign_title": "Promo Mingguan",
        "article_url": "https://example.test/article", "image_url": "https://example.test/poster.jpg",
    }
    products = [
        {"product_text": "Sunlight Lime 910 ml", "product_validation_status": "eligible"},
        {"product_text": "Dove Body Wash 400 ml", "product_validation_status": "eligible"},
    ]
    candidates = build_candidates(image, lines, 1000, 1000, products)
    pairs = {(r["normal_price_candidate"], r["promo_price_candidate"]) for r in candidates}
    assert (20000, 16000) in pairs
    assert (31500, 25200) in pairs
    assert all(len(__import__("json").loads(r["price_values_json"])) <= 2 for r in candidates)
    assert all("WAJIB cocokkan poster" in r["candidate_note"] for r in candidates if r["normal_price_candidate"])


def test_normalize_box_accepts_paddle_xyxy_and_rapid_polygon_boxes():
    from promo_poster_ocr import _normalize_box
    assert _normalize_box([10, 20, 90, 60]) == (10.0, 20.0, 90.0, 60.0)
    assert _normalize_box([[10, 20], [90, 22], [88, 60], [12, 58]]) == (10.0, 20.0, 90.0, 60.0)


def test_paddleocr_numpy_outputs_are_parsed_into_text_lines():
    import io
    import numpy as np
    from PIL import Image
    from promo_poster_ocr import recognize_image

    class FakePaddleOCR:
        def predict(self, image):
            assert image.ndim == 3 and image.shape[2] == 3
            return [{
                "rec_texts": ["Rp20.000"],
                "rec_scores": np.asarray([0.98]),
                "rec_boxes": np.asarray([[10, 20, 90, 60]]),
            }]

    buf = io.BytesIO()
    Image.new("RGB", (120, 80), "white").save(buf, format="PNG")
    lines, width, height = recognize_image(buf.getvalue(), FakePaddleOCR(), "paddleocr")
    assert width >= 120 and height >= 80
    assert lines[0]["text"] == "Rp20.000"
    assert lines[0]["confidence"] == 0.98
    assert (lines[0]["x0"], lines[0]["y0"], lines[0]["x1"], lines[0]["y1"]) == (10.0, 20.0, 90.0, 60.0)


def test_article_url_match_is_normalized_for_www_and_trailing_slash(tmp_path):
    import csv
    from promo_poster_ocr import _load_page_products, _canonical_article_url, _safe_image_url

    path = tmp_path / "products.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["product_text", "source_url", "source_page_url", "product_validation_status"])
        writer.writeheader()
        writer.writerow({"product_text": "Sunlight Lime 910 ml", "source_url": "https://www.example.test/article/", "source_page_url": "", "product_validation_status": "eligible"})
    products = _load_page_products(path)
    assert products[_canonical_article_url("https://example.test/article")][0]["product_text"] == "Sunlight Lime 910 ml"
    assert not _safe_image_url("http://127.0.0.1/private.jpg")
    assert not _safe_image_url("http://192.168.1.2/private.jpg")
    assert _safe_image_url("https://images.example.test/poster.jpg")


def test_review_workbook_links_to_article_and_original_poster(tmp_path):
    from openpyxl import load_workbook
    from promo_poster_ocr import make_review_workbook

    output = tmp_path / "review.xlsx"
    candidate = {
        "candidate_id": "AB12CD34", "source_name": "Katalogpromosi.com",
        "campaign_title": "Promo Mingguan", "article_url": "https://example.test/article",
        "image_url": "https://example.test/poster.jpg", "ocr_snippet": "Harga Normal Rp20.000 Harga Promo Rp15.000",
        "matched_product_name": "Sunlight Lime 910 ml", "matched_product_score": 0.95,
        "match_note": "Ukuran cocok", "candidate_note": "Harga ditinjau manual",
        "normal_price_candidate": 20000, "promo_price_candidate": 15000,
        "discount_claim_percent": 25, "discount_calculated_percent": 25,
        "ocr_confidence": 0.94,
    }
    make_review_workbook([candidate], output)
    wb = load_workbook(output, read_only=False, data_only=False)
    ws = wb["Kandidat OCR"]
    assert ws["P2"].hyperlink.target == "https://example.test/article"
    assert ws["Q2"].hyperlink.target == "https://example.test/poster.jpg"
    wb.close()
