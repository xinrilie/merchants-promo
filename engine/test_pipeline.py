from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_DIR))

import promo_multisource_collector as collector
import enrich_existing_promo_catalog_multisource as enhancer
from promo_names import clean_product_name, product_names_can_auto_merge
from promo_product_validation import product_candidate_rejection_reason, product_candidate_review_reason


def test_article_headlines_are_not_products():
    cases = [
        "Apa Benefit yang Didapatkan Promo Paris Baguette BNI Wonderful Weekend",
        "Apa Saja Penawaran Dalam Promo Paris Baguette BNI Wonderful Weekend",
        "Bagaimana Cara Mendapatkan Promo Dan Mekanisme Promo Paris Baguette BNI Wonderful Weekend",
        "Berapa Minimum Transaksi Untuk Mendapatkan",
        "Memanfaatkan Akhir Pekan Ini Untuk Menikmati Pilihan Menu Paris Baguette Dengan Lebih Hemat Bersama Promo",
        "Promo Paris Baguette Bni Wonderful Weekend",
        "☞ Cek Info Promo Paris Baguette Bni Wonderful Weekend",
        "Kalau Kamu Lagi Cari Promo Belanja Hemat, Promo Alfamidi Katalog Mingguan Adalah Jawabannya! Dari Tanggal 16-31 Oktober 2026, Kamu Bisa Nikmati Berbagai Penawaran Menarik Mulai Dari",
        # Regression: titles can be missing in legacy/stale CSV rows.
        "Akhirnya",
    ]
    for name in cases:
        title_values = ["Promo Paris Baguette BNI Wonderful Weekend 23-24 Oktober 2026"]
        if name in {"Akhirnya", "Promo Paris Baguette Bni Wonderful Weekend"}:
            title_values.append("")  # stale legacy rows often lose campaign_title
        for title in title_values:
            row = {
                "product_text": name,
                "offer_text": "Diskon 50%",
                "campaign_title": title,
                "normal_price": None,
                "promo_price": None,
                "discount_percent": 50,
                "source_name": "Katalogpromosi.com",
            }
            reason = product_candidate_rejection_reason(row, "Katalogpromosi.com article")
            assert reason in {
                "editorial_heading_misparsed_as_product",
                "campaign_discount_claim_misparsed_as_product",
                "campaign_or_article_heading_misparsed_as_product",
                "long_narrative_with_discount_claim_not_product_sku",
                "discount_claim_without_product_identity",
                "editorial_sentence_misparsed_as_product",
                "long_editorial_sentence_misparsed_as_product",
            }, (name, title, reason)


def test_editorial_teaser_rejected_even_with_structured_bundle_offer():
    # Exact leak shown in the user's workbook screenshot. The product-looking row
    # inherited a generic B1G1 / 50% offer from an Alfamidi promo card.
    row = {
        "product_text": (
            "Kalau Kamu Lagi Cari Promo Belanja Hemat, Promo Alfamidi Katalog Mingguan "
            "Adalah Jawabannya! Dari Tanggal 16-31 Oktober 2026, Kamu Bisa Nikmati "
            "Berbagai Penawaran Menarik Mulai Dari"
        ),
        "offer_text": "Beli 1 Gratis 1",
        "purchase_condition": "Beli 1 Gratis 1",
        "discount_percent": 50,
        "merchant_name": "Alfamidi",
        "source_name": "Hemat.id",
        "campaign_title": "Katalog Promo Alfamidi 16-31 Oktober 2026",
    }
    reason = product_candidate_rejection_reason(row, "hemat_card")
    assert reason in {
        "editorial_sentence_misparsed_as_product",
        "long_editorial_sentence_misparsed_as_product",
    }, (row["product_text"], reason)

    # Exercise the live collector path, not only the shared validator.
    from bs4 import BeautifulSoup
    import promo_multisource_collector as collector
    html = """<div class="product-card">
      <a href="/promo">{product_text} Diskon 50%, Beli 1 Gratis 1</a>
      <span>16-31 Oktober 2026</span><a href="/katalog-alfamidi/">Alfamidi</a>
    </div>""".format(product_text=row["product_text"])
    collector.REJECTED_PRODUCT_CANDIDATES.clear()
    extracted = collector.extract_hemat_cards(
        BeautifulSoup(html, "html.parser"), "https://www.hemat.id/katalog-alfamidi/"
    )
    assert extracted == [], [item.get("product_text") for item in extracted]
    assert any(
        item.get("rejection_reason") in {
            "editorial_sentence_misparsed_as_product",
            "long_editorial_sentence_misparsed_as_product",
        }
        for item in collector.REJECTED_PRODUCT_CANDIDATES
    ), collector.REJECTED_PRODUCT_CANDIDATES

    # Real structured-card products with the same B1G1 offer must remain eligible.
    valid_product = {
        "product_text": "Cultusia Hair Serum Joyfull Romance Morning Fresh 30 ml",
        "offer_text": "Beli 1 Gratis 1",
        "purchase_condition": "Beli 1 Gratis 1",
        "discount_percent": 50,
        "merchant_name": "Alfamidi",
        "source_name": "Hemat.id",
        "campaign_title": "Katalog Promo Alfamidi 16-31 Oktober 2026",
    }
    assert product_candidate_rejection_reason(valid_product, "hemat_card") is None



def test_real_products_stay_eligible():
    samples = [
        {
            "product_text": "Fres&Nat Body Wash 400 ml",
            "offer_text": "Harga Promo Rp12.900, Harga Normal Rp19.900",
            "campaign_title": "Promo akhir pekan 23-24 Oktober 2026",
            "normal_price": 19900, "promo_price": 12900,
            "source_name": "Katalogpromosi.com",
        },
        {
            "product_text": "OATSIDE Oat Bar All Variant 18 g",
            "offer_text": "Promo: Beli 1 Gratis 1",
            "purchase_condition": "Beli 1 Gratis 1",
            "campaign_title": "Promo Alfamidi 16-31 Oktober 2026",
            "source_name": "Momsmoney/Kontan",
        },
        {
            "product_text": "Koepoe Baking Powder, Perisa Vanilla, Soda Kue, SP Pengemulsi 45 g; 20 g; 81 g; 30 g",
            "offer_text": "Diskon 15%",
            "discount_percent": 15,
            "campaign_title": "Promo Hypermart Daily Fresh 16-22 Oktober 2026",
            "source_name": "Momsmoney/Kontan",
        },
        {
            "product_text": "My Baby Body Cologne Sweet Floral 100 ml",
            "offer_text": "Diskon 50%",
            "discount_percent": 50,
            "campaign_title": "Promo My Baby 23-25 Oktober 2026",
            "source_name": "Momsmoney/Kontan",
        },
    ]
    for row in samples:
        reason = product_candidate_rejection_reason(row, row.get("source_name"))
        assert reason is None, (row["product_text"], reason)


def test_discount_only_short_fragments_need_product_identity():
    bad_rows = [
        {"product_text": "Akhirnya", "offer_text": "Diskon 50%", "normal_price": None, "promo_price": None},
        {"product_text": "Promo Paris Baguette BNI Wonderful Weekend", "offer_text": "Diskon 50%", "normal_price": None, "promo_price": None},
    ]
    for row in bad_rows:
        # No campaign_title on purpose: validator must still reject stale rows.
        reason = product_candidate_rejection_reason(row, "momskonten_promo_active.csv")
        assert reason is not None, (row, reason)

    bundle_row = {
        "product_text": "Alfamart Fac Tissue Onepiece 100S",
        "offer_text": "Beli 1 Gratis 1", "purchase_condition": "Beli 1 Gratis 1",
        "merchant_name": "Alfamart", "normal_price": None, "promo_price": None,
    }
    assert product_candidate_rejection_reason(bundle_row, "momskonten_promo_active.csv") is None

    named_item = {
        "product_text": "My Baby Body Cologne Sweet Floral 100 ml",
        "offer_text": "Diskon 50%", "merchant_name": "Indomaret",
        "normal_price": None, "promo_price": None,
    }
    assert product_candidate_rejection_reason(named_item, "momskonten_promo_active.csv") is None


def test_enhancer_rejects_stale_false_rows_without_campaign_title():
    # Reproduce the exact failure mode in the screenshot: old rows have product
    # fragments and a discount claim, but the campaign_title field is empty.
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "stale_rows.csv"
        fields = [
            "product_text", "campaign_title", "promo_status", "valid_from", "valid_until",
            "normal_price", "promo_price", "discount_percent", "offer_text", "source_url",
            "merchant_name", "deal_type", "purchase_condition", "price_basis", "period_precision",
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            cases = [
                ("Akhirnya", "Diskon 50%", "", ""),
                ("Promo Paris Baguette BNI Wonderful Weekend", "Diskon 50%", "", ""),
                (
                    "Kalau Kamu Lagi Cari Promo Belanja Hemat, Promo Alfamidi Katalog Mingguan "
                    "Adalah Jawabannya! Dari Tanggal 16-31 Oktober 2026, Kamu Bisa Nikmati "
                    "Berbagai Penawaran Menarik Mulai Dari",
                    "Beli 1 Gratis 1", "Beli 1 Gratis 1", "Alfamidi",
                ),
            ]
            for name, offer, purchase_condition, merchant in cases:
                writer.writerow({
                    "product_text": name, "campaign_title": "", "promo_status": "active",
                    "valid_from": "2026-10-23", "valid_until": "2026-10-24",
                    "normal_price": "", "promo_price": "", "discount_percent": "50",
                    "offer_text": offer, "purchase_condition": purchase_condition,
                    "source_url": "https://hemat.id/test/", "merchant_name": merchant,
                    "deal_type": "Beli 1 Gratis 1" if purchase_condition else "Diskon Persentase",
                })
        rows = enhancer.load_source(path, path.name)
        assert len(rows) == 3
        assert all(r.get("product_validation_status") == "rejected" for r in rows), [
            (r.get("product_text"), r.get("product_validation_status"), r.get("product_rejection_reason")) for r in rows
        ]



def test_article_paragraphs_and_campaign_titles_are_rejected_even_with_prices():
    cases = [
        {
            "product_text": "Belanja Kebutuhan Sehari-Hari Jadi Lebih Hemat Dengan Promo Lawson Sms Serius Murah Seminggu! Program Spesial Ini Kembali Menghadirkan Berbagai Produk Pilihan Dengan Harga Menarik, Mulai Dari",
            "merchant_name": None, "promo_price": 3000,
            "offer_text": "Rp3.000 saja. Cocok buat kamu yang ingin stok minuman, camilan, hingga kebutuhan rumah tangga",
            "campaign_title": "Promo LAWSON SMS Serius Murah Seminggu 16-23 Oktober 2026",
        },
        {
            "product_text": "Promo Gopay Hanya Berlaku Untuk Pengguna Baru, Dengan Pembelian Minimal",
            "merchant_name": "Alfamart", "promo_price": 15000, "offer_text": "Rp15.000.",
            "campaign_title": "Promo JSM Alfamart 23-25 Oktober 2026",
        },
        {
            "product_text": "Cek Gambar Promo Lawson Sms Serius Murah Seminggu Mulai",
            "merchant_name": None, "promo_price": 3000, "offer_text": "Rp3.000! di bawah ini untuk lihat detail dan informasi selengkapnya",
            "campaign_title": "Promo LAWSON SMS Serius Murah Seminggu 16-23 Oktober 2026",
        },
        {
            "product_text": "Indomaret – PWP (Purchase With Purchase) Min. Belanja",
            "merchant_name": "Indomaret", "promo_price": 50000,
            "offer_text": "Rp. 50.000 Periode: 15-28 Oktober 2026",
            "campaign_title": "Promo JSM Indomaret 22-25 Oktober 2026",
        },
        {
            "product_text": "2 2 Zespri Kiwi Sungold 2", "merchant_name": "Yogya",
            "promo_price": None, "offer_text": "Diskon 65%, Pembelian Ke-2",
            "campaign_title": "Promo Unggulan Alfamart VS Yogya minggu ini",
        },
        # Live catalog leaks from PWP text that incorrectly inherited an offer price.
        {
            "product_text": "Hanya Dengan Belanja Minimal Mulai", "merchant_name": "Alfamidi",
            "promo_price": 50000,
            "offer_text": "Rp. 50.000 bisa tebus Murah 1 Produk TTM",
            "campaign_title": "Promo ALFAMIDI TTM Terbaru 16-31 Oktober 2026",
        },
        {
            "product_text": "Karena Mulai Dari Tanggal 15 Sampai Dengan 28 Oktober 2026, Bunda Semua Hanya Cukup Belanja Atau Transaksi Minimal",
            "merchant_name": "Indomaret", "promo_price": 50000,
            "offer_text": "Rp. 50.000 di semua toko Indomaret, otomatis bisa ikutan Tebus Murah",
            "campaign_title": "Promo PWP Indomaret Tebus Murah 15-28 Oktober 2026",
        },
    ]
    for row in cases:
        reason = product_candidate_rejection_reason(row, source_mode="article")
        assert reason, f"Editorial/noisy candidate passed validation: {row['product_text']}"


def test_pwp_explanatory_fragments_are_rejected_even_with_prices():
    """Protect against editorial snippets leaking as priced PWP products."""
    cases = [
        {
            "product_text": "Hanya Dengan Belanja Minimal Mulai",
            "offer_text": "Rp. 50.000 bisa tebus Murah 1 Produk TTM",
            "promo_price": "50000", "merchant_name": "Alfamidi",
            "campaign_title": "Promo ALFAMIDI TTM Terbaru 16-31 Oktober 2026",
            "source_name": "Scanharga.com",
        },
        {
            "product_text": "Karena Mulai Dari Tanggal 15 Sampai Dengan 28 Oktober 2026, Bunda Semua Hanya Cukup Belanja Atau Transaksi Minimal",
            "offer_text": "Rp. 50.000 di semua toko Indomaret, otomatis bisa ikutan Tebus Murah",
            "promo_price": "50000", "merchant_name": "Indomaret",
            "campaign_title": "Promo PWP Indomaret Tebus Murah 15-28 Oktober 2026",
            "source_name": "Scanharga.com",
        },
    ]
    for row in cases:
        reason = product_candidate_rejection_reason(row, source_mode="Scanharga.com article")
        assert reason == "editorial_heading_misparsed_as_product", (row["product_text"], reason)

    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "pwp_editorial_leaks.csv"
        fields = [
            "product_text", "campaign_title", "promo_status", "valid_from", "valid_until",
            "normal_price", "promo_price", "discount_percent", "offer_text", "source_url",
            "merchant_name", "deal_type", "purchase_condition", "price_basis", "period_precision",
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for row in cases:
                output_row = {
                    **row,
                    "promo_status": "active", "valid_from": "2026-10-23", "valid_until": "2026-10-31",
                    "normal_price": "", "discount_percent": "", "source_url": "https://www.scanharga.com/test",
                    "deal_type": "Tebus Murah / PWP", "purchase_condition": "", "price_basis": "offer_price",
                    "period_precision": "range",
                }
                writer.writerow({key: value for key, value in output_row.items() if key in fields})
        loaded = enhancer.load_source(path, path.name)
        assert len(loaded) == 2
        assert all(row.get("product_validation_status") == "rejected" for row in loaded), [
            (r.get("product_text"), r.get("product_validation_status"), r.get("product_rejection_reason"))
            for r in loaded
        ]


def test_name_fuzzy_guardrails():
    assert product_names_can_auto_merge("Aice Mochi Vanilla Chocolate 270g", "Aice Mochi Vanilla / Chocolate 270 g")
    assert not product_names_can_auto_merge("Aice Mochi Vanilla 270 g", "Aice Mochi Chocolate 270 g")
    assert not product_names_can_auto_merge("Sunlight Jeruk 755 ml", "Sunlight Jeruk 450 ml")


def test_collector_article_extraction_regression():
    collector.REJECTED_PRODUCT_CANDIDATES.clear()
    html = '''<article><div class="entry-content">
    <p>Apa Benefit yang Didapatkan Promo Paris Baguette BNI Wonderful Weekend: Diskon 50%</p>
    <p>Apa Saja Penawaran Dalam Promo Paris Baguette BNI Wonderful Weekend: Diskon 50%</p>
    <p>Bagaimana Cara Mendapatkan Promo Dan Mekanisme Promo Paris Baguette BNI Wonderful Weekend: Diskon 50%</p>
    <p>Berapa Minimum Transaksi Untuk Mendapatkan: Diskon 50%</p>
    <p>Memanfaatkan Akhir Pekan Ini Untuk Menikmati Pilihan Menu Paris Baguette Dengan Lebih Hemat Bersama Promo: Diskon 50%</p>
    <p>Promo Paris Baguette Bni Wonderful Weekend: Diskon 50%</p>
    <p>☞ Cek Info Promo Paris Baguette Bni Wonderful Weekend: Diskon 50%</p>
    <ul><li>FRES&amp;NAT Body Wash 400 ml → Harga Promo Rp12.900, Harga Normal Rp19.900</li></ul>
    </div></article>'''
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    container = collector.find_article_container(soup)
    title = "Promo Paris Baguette BNI Wonderful Weekend 23-24 Oktober 2026"
    meta = collector.parse_promo_metadata(title, collector.clean_text(container.get_text(" ", strip=True)))
    rows = collector.extract_article_rows(
        container, "Katalogpromosi.com", title,
        "https://katalogpromosi.com/promo-paris-baguette-test/", meta,
    )
    assert len(rows) == 1, [r.get("product_text") for r in rows]
    assert rows[0]["promo_price"] == 12900
    assert len(collector.REJECTED_PRODUCT_CANDIDATES) == 7, [r.get("product_text") for r in collector.REJECTED_PRODUCT_CANDIDATES]


def test_enhancer_defense_in_depth():
    # The enhancer must independently reject rows from old CSVs or stale collector data.
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "fake.csv"
        fieldnames = [
            "product_text", "campaign_title", "promo_status", "valid_from", "valid_until",
            "normal_price", "promo_price", "discount_percent", "offer_text", "source_url",
            "merchant_name", "deal_type", "purchase_condition", "price_basis", "period_precision",
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for name in [
                "Apa Benefit yang Didapatkan Promo Paris Baguette BNI Wonderful Weekend",
                "Apa Saja Penawaran Dalam Promo Paris Baguette BNI Wonderful Weekend",
                "Bagaimana Cara Mendapatkan Promo Dan Mekanisme Promo Paris Baguette BNI Wonderful Weekend",
                "Berapa Minimum Transaksi Untuk Mendapatkan",
                "Memanfaatkan Akhir Pekan Ini Untuk Menikmati Pilihan Menu Paris Baguette Dengan Lebih Hemat Bersama Promo",
                "Promo Paris Baguette Bni Wonderful Weekend",
                "☞ Cek Info Promo Paris Baguette Bni Wonderful Weekend",
            ]:
                writer.writerow({
                    "product_text": name,
                    "campaign_title": "Promo Paris Baguette BNI Wonderful Weekend 23-24 Oktober 2026",
                    "promo_status": "active", "valid_from": "2026-10-23", "valid_until": "2026-10-24",
                    "discount_percent": "50", "offer_text": "Diskon 50%",
                    "source_url": "https://katalogpromosi.com/promo-paris-test/",
                })
        loaded = enhancer.load_source(path, path.name)
        assert len(loaded) == 7
        assert all(row.get("product_validation_status") == "rejected" for row in loaded)



def test_cross_source_fuzzy_merge_and_guardrails():
    # Integration-level regression: fuzzy matching runs in the enhancer after
    # rows from multiple sources are loaded and product eligibility is checked.
    fields = [
        "product_text", "merchant_name", "campaign_title", "promo_status",
        "valid_from", "valid_until", "period_precision", "normal_price",
        "promo_price", "discount_percent", "offer_text", "source_url",
        "source_name", "source_page_url", "source_item_url", "deal_type",
        "purchase_condition", "price_basis", "price_parse_method", "period_flags",
    ]
    hemat_rows = [
        {
            "product_text": "Aice Mochi Vanilla Chocolate 270g", "merchant_name": "Alfamart",
            "campaign_title": "Promo Alfamart 23-25 Oktober 2026", "promo_status": "active",
            "valid_from": "2026-10-23", "valid_until": "2026-10-25", "period_precision": "range",
            "normal_price": "14900", "promo_price": "9900", "discount_percent": "33.6",
            "offer_text": "Harga Promo Rp9.900, Harga Normal Rp14.900",
            "source_url": "https://www.hemat.id/katalog-alfamart/item-aice", "source_name": "Hemat.id",
            "source_page_url": "https://www.hemat.id/katalog-alfamart/item-aice",
            "deal_type": "Diskon Persentase", "price_basis": "offer_price",
        },
        {
            "product_text": "My Baby Body Cologne Sweet Floral 100 ml", "merchant_name": "Indomaret",
            "campaign_title": "Promo Indomaret 23-25 Oktober 2026", "promo_status": "active",
            "valid_from": "2026-10-23", "valid_until": "2026-10-25", "period_precision": "range",
            "normal_price": "", "promo_price": "", "discount_percent": "50",
            "offer_text": "Beli 1 Gratis 1", "source_url": "https://www.hemat.id/katalog-indomaret/item-my-baby",
            "source_name": "Hemat.id", "source_page_url": "https://www.hemat.id/katalog-indomaret/item-my-baby",
            "deal_type": "Beli Gratis", "purchase_condition": "Beli 1 Gratis 1",
        },
        {
            "product_text": "Aice Mochi Chocolate 270 g", "merchant_name": "Alfamart",
            "campaign_title": "Promo Alfamart 23-25 Oktober 2026", "promo_status": "active",
            "valid_from": "2026-10-23", "valid_until": "2026-10-25", "period_precision": "range",
            "normal_price": "14900", "promo_price": "9900", "discount_percent": "33.6",
            "offer_text": "Harga Promo Rp9.900, Harga Normal Rp14.900",
            "source_url": "https://www.hemat.id/katalog-alfamart/item-aice-choc", "source_name": "Hemat.id",
            "source_page_url": "https://www.hemat.id/katalog-alfamart/item-aice-choc",
            "deal_type": "Diskon Persentase", "price_basis": "offer_price",
        },
    ]
    other_rows = [
        {
            "product_text": "Aice Mochi Vanilla / Chocolate 270 g", "merchant_name": "Alfamart",
            "campaign_title": "Promo Alfamart 23-25 Oktober 2026", "promo_status": "active",
            "valid_from": "2026-10-23", "valid_until": "2026-10-25", "period_precision": "range",
            "normal_price": "14900", "promo_price": "9900", "discount_percent": "33.6",
            "offer_text": "Harga Promo Rp9.900, Harga Normal Rp14.900",
            "source_url": "https://momsmoney.kontan.co.id/news/promo-aice", "source_name": "Momsmoney/Kontan",
            "source_page_url": "https://momsmoney.kontan.co.id/news/promo-aice",
            "deal_type": "Diskon Persentase", "price_basis": "offer_price",
        },
        {
            "product_text": "MY BABY Body Cologne Sweet Floral 100ml btl", "merchant_name": "Indomaret",
            "campaign_title": "Promo Indomaret 23-25 Oktober 2026", "promo_status": "active",
            "valid_from": "2026-10-23", "valid_until": "2026-10-25", "period_precision": "range",
            "normal_price": "", "promo_price": "", "discount_percent": "50",
            "offer_text": "Diskon 50%", "source_url": "https://momsmoney.kontan.co.id/news/promo-my-baby",
            "source_name": "Momsmoney/Kontan", "source_page_url": "https://momsmoney.kontan.co.id/news/promo-my-baby",
            "deal_type": "Diskon Persentase",
        },
        {
            "product_text": "Aice Mochi Vanilla Chocolate 270 g", "merchant_name": "Alfamart",
            "campaign_title": "Promo Alfamart 23-25 Oktober 2026", "promo_status": "active",
            "valid_from": "2026-10-23", "valid_until": "2026-10-25", "period_precision": "range",
            "normal_price": "14900", "promo_price": "11500", "discount_percent": "22.8",
            "offer_text": "Harga Promo Rp11.500, Harga Normal Rp14.900",
            "source_url": "https://momsmoney.kontan.co.id/news/promo-aice-different-price", "source_name": "Momsmoney/Kontan",
            "source_page_url": "https://momsmoney.kontan.co.id/news/promo-aice-different-price",
            "deal_type": "Diskon Persentase", "price_basis": "offer_price",
        },
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        hemat_path = Path(temp_dir) / "hemat.csv"
        other_path = Path(temp_dir) / "other.csv"
        for path, rows in ((hemat_path, hemat_rows), (other_path, other_rows)):
            with path.open("w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
        loaded = enhancer.load_source(hemat_path, "hemat.csv") + enhancer.load_source(other_path, "other.csv")
        eligible = [r for r in loaded if r.get("product_validation_status") != "rejected"]
        exact = enhancer.merge_rows(eligible)
        consolidated, _, _ = enhancer.consolidate_overlapping_offers(exact)
        fuzzy, groups, absorbed = enhancer.merge_equivalent_cross_source_offers(consolidated)

        assert len(loaded) == 6
        assert len(fuzzy) == 4, [(r.get("product_text"), r.get("promo_price"), r.get("source_names")) for r in fuzzy]
        cross_source_rows = [r for r in fuzzy if "Hemat.id" in str(r.get("source_names")) and "Momsmoney/Kontan" in str(r.get("source_names"))]
        assert len(cross_source_rows) == 2, [(r.get("product_text"), r.get("source_names")) for r in cross_source_rows]
        assert any("Aice Mochi Vanilla" in str(r.get("product_text")) for r in cross_source_rows)
        assert any("My Baby" in str(r.get("product_text")) for r in cross_source_rows)
        assert groups >= 1 and absorbed >= 1
        # The distinctly different flavour and conflicting price remain separate.
        assert sum("Aice Mochi Chocolate 270 g" in str(r.get("product_text")) for r in fuzzy) == 1
        same_name_aice = [r for r in fuzzy if "Aice Mochi Vanilla Chocolate" in str(r.get("product_text"))]
        assert len(same_name_aice) == 2
        assert {str(r.get("promo_price")) for r in same_name_aice} == {"9900", "11500"}

def test_momsmoney_feed_freshness_gate():
    from datetime import date
    with tempfile.TemporaryDirectory() as temp_dir:
        folder = Path(temp_dir)
        feed = folder / "promo_catalog_all_merchants.csv"
        log = folder / "promo_catalog_all_merchants_runlog.json"
        feed.write_text("product_text\nExample SKU\n", encoding="utf-8")
        old_feed_path, old_log_path = enhancer.MOMSMONEY_CSV, enhancer.MOMSMONEY_RUN_LOG
        try:
            enhancer.MOMSMONEY_CSV, enhancer.MOMSMONEY_RUN_LOG = feed, log
            log.write_text(__import__("json").dumps({
                "checked_at": enhancer.TODAY.isoformat() + "T10:00:00+07:00",
                "output_status": "updated",
            }), encoding="utf-8")
            fresh, _ = enhancer.momsmoney_output_is_fresh()
            assert fresh, "Successful Momsmoney feed for today should be loaded"
            log.write_text(__import__("json").dumps({
                "checked_at": enhancer.TODAY.isoformat() + "T10:00:00+07:00",
                "output_status": "preserved_previous_catalog",
            }), encoding="utf-8")
            fresh, _ = enhancer.momsmoney_output_is_fresh()
            assert not fresh, "Previous CSV must not be loaded after failed validation"
            log.write_text(__import__("json").dumps({
                "checked_at": "2001-01-01T10:00:00+07:00",
                "output_status": "updated",
            }), encoding="utf-8")
            fresh, _ = enhancer.momsmoney_output_is_fresh()
            assert not fresh, "Stale Momsmoney run must not be treated as fresh"
        finally:
            enhancer.MOMSMONEY_CSV, enhancer.MOMSMONEY_RUN_LOG = old_feed_path, old_log_path


def test_momsmoney_current_attempt_is_available_without_loading_stale_catalog():
    import json
    with tempfile.TemporaryDirectory() as temp_dir:
        folder = Path(temp_dir)
        stable_feed = folder / "promo_catalog_all_merchants.csv"
        attempt_feed = folder / "promo_catalog_all_merchants_latest_attempt_products.csv"
        log_path = folder / "promo_catalog_all_merchants_runlog.json"
        stable_feed.write_text("product_text\nSTALE PREVIOUS SKU\n", encoding="utf-8")
        attempt_feed.write_text("product_text,promo_status\nFresh SKU,active\nEstimated SKU,active_estimated\n", encoding="utf-8")

        old_paths = (enhancer.MOMSMONEY_CSV, enhancer.MOMSMONEY_ATTEMPT_CSV, enhancer.MOMSMONEY_RUN_LOG)
        try:
            enhancer.MOMSMONEY_CSV = stable_feed
            enhancer.MOMSMONEY_ATTEMPT_CSV = attempt_feed
            enhancer.MOMSMONEY_RUN_LOG = log_path
            log_path.write_text(json.dumps({
                "checked_at": enhancer.TODAY.isoformat() + "T06:00:00+07:00",
                "successful_articles": 60,
                "output_status": "preserved_previous_catalog",
                "latest_attempt_product_rows": 2,
            }), encoding="utf-8")

            path, note = enhancer.momsmoney_fresh_input()
            assert path == attempt_feed, (path, note)
            assert "preserved_previous_catalog" in note

            # A stale run log invalidates both files; last-good data is not
            # mistaken for a current scrape merely because the CSV exists.
            log_path.write_text(json.dumps({
                "checked_at": "2001-01-01T06:00:00+07:00",
                "successful_articles": 60,
                "output_status": "preserved_previous_catalog",
                "latest_attempt_product_rows": 2,
            }), encoding="utf-8")
            path, _ = enhancer.momsmoney_fresh_input()
            assert path is None
        finally:
            enhancer.MOMSMONEY_CSV, enhancer.MOMSMONEY_ATTEMPT_CSV, enhancer.MOMSMONEY_RUN_LOG = old_paths


def test_multisource_month_periods_are_estimates_and_rank_current_titles_first():
    canonical_months = {
        1: "januari", 2: "februari", 3: "maret", 4: "april", 5: "mei", 6: "juni",
        7: "juli", 8: "agustus", 9: "september", 10: "oktober", 11: "november", 12: "desember",
    }
    current_title = f"Promo KFC {collector.TODAY.day}-{collector.TODAY.day} "
    current_title += f"{canonical_months[collector.TODAY.month]} {collector.TODAY.year}"

    current_priority = collector.article_candidate_priority({"title_hint": current_title})
    expired_priority = collector.article_candidate_priority({"title_hint": "Promo Lama 1-2 Januari 2000"})
    assert current_priority > expired_priority, (current_priority, expired_priority)

    month_name = canonical_months[collector.TODAY.month]
    current_month = collector.parse_promo_metadata(f"Promo Restoran {month_name} {collector.TODAY.year}", "")
    assert current_month["period_precision"] == "month"
    assert current_month["promo_status"] == "active_estimated"

    end_only = collector.parse_promo_metadata("Promo Restoran", "Promo berlaku hingga 31 Desember 2099")
    assert end_only["valid_until"] == "2099-12-31"
    assert end_only["promo_status"] == "active_estimated"


def test_momsmoney_rows_get_source_metadata():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "feed.csv"
        fields = ["product_text", "offer_text", "purchase_condition", "campaign_title",
                  "valid_from", "valid_until", "promo_status", "source_url", "promo_price", "normal_price"]
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerow({
                "product_text": "My Baby Body Cologne Sweet Floral 100 ml",
                "offer_text": "Beli 1 Gratis 1", "purchase_condition": "Beli 1 Gratis 1",
                "campaign_title": "Promo Indomaret 23-25 Oktober 2026",
                "valid_from": "2026-10-23", "valid_until": "2026-10-25", "promo_status": "active",
                "source_url": "https://momsmoney.kontan.co.id/news/promo-test",
            })
        rows = enhancer.load_source(path, "promo_catalog_all_merchants.csv")
        assert len(rows) == 1
        assert rows[0]["source_name"] == "Momsmoney/Kontan"
        assert rows[0]["verification_status"] == "secondary_public_articles"


def main():
    test_article_headlines_are_not_products()
    test_editorial_teaser_rejected_even_with_structured_bundle_offer()
    test_real_products_stay_eligible()
    test_discount_only_short_fragments_need_product_identity()
    test_article_paragraphs_and_campaign_titles_are_rejected_even_with_prices()
    test_pwp_explanatory_fragments_are_rejected_even_with_prices()
    test_enhancer_rejects_stale_false_rows_without_campaign_title()
    test_name_fuzzy_guardrails()
    test_collector_article_extraction_regression()
    test_enhancer_defense_in_depth()
    test_cross_source_fuzzy_merge_and_guardrails()
    test_momsmoney_feed_freshness_gate()
    test_momsmoney_current_attempt_is_available_without_loading_stale_catalog()
    test_momsmoney_rows_get_source_metadata()
    test_multisource_month_periods_are_estimates_and_rank_current_titles_first()
    print("REGRESSION TESTS PASSED")
    print("- editorial/headline fragments from the screenshot are rejected")
    print("- conversational promo teaser is rejected even when a card has a B1G1/50% offer")
    print("- valid SKU price and bundle examples are kept")
    print("- fuzzy name matching preserves size/flavour distinctions")
    print("- collector filters screenshot editorial false positives before writing scrape output")
    print("- article paragraphs and campaign headings are rejected even when a price was extracted")
    print("- PWP explanatory sentence fragments are rejected even when they inherit a price")
    print("- enhancer independently filters stale/old false product rows even when campaign_title is missing")
    print("- fuzzy merge combines corroborated cross-source offers but preserves variant/price conflicts")
    print("- Momsmoney/Kontan uses only current-run data; stale last-good CSV is never loaded as fresh data")
    print("- fresh Momsmoney attempt rows can be reviewed without overwriting a stale last-good catalog")
    print("- multi-source article selection prioritizes current period evidence; month-only periods remain estimates")


if __name__ == "__main__":
    main()



def test_screenshot_campaign_headline_never_passes_as_a_product():
    row = {
        "product_text": (
            "Gimana Mantapp Kan Promo Alfamart Paling Murah Sejagat Terbaru Buat Minggu Ini. "
            "Yang Pastinya Serba Lengkap, Serba Hemat!! Produknya Bisa Bunda Dapatkan Mulai Dari Ceban"
        ),
        "offer_text": "Rp 10.000",
        "promo_price": 10000,
        "merchant_name": "Alfamart",
        "source_name": "Katalogpromosi.com",
        "campaign_title": "Promo Alfamart Terbaru Minggu Ini",
    }
    reason = product_candidate_rejection_reason(row, "Katalogpromosi.com article")
    assert reason == "editorial_heading_misparsed_as_product"


def test_standalone_minimum_purchase_threshold_is_not_a_product_or_price():
    for name in ["Min. Belanja", "Minimum Pembelanjaan", "Minimum Transaksi", "Minimal Pembelian"]:
        row = {
            "product_text": name,
            "offer_text": "Rp. 15.000",
            "promo_price": 15000,
            "merchant_name": "Indomaret",
            "source_name": "Scanharga.com",
            "campaign_title": "Promo PTW Indomaret Product Of The Week",
        }
        assert product_candidate_rejection_reason(row, "Scanharga.com article") == "purchase_condition_misparsed_as_product"


def test_payment_benefit_amount_on_short_name_is_review_not_product_price():
    row = {
        "product_text": "Extra",
        "original_product_text": "Extra Diskon",
        "offer_text": "Rp 2.000 Dengan QRIS BCA myBCA & BCA mobile",
        "promo_price": 2000,
        "merchant_name": "Indomaret",
        "source_url": "https://www.scanharga.com/2025/11/promo-pwp-indomaret-tebus-murah.html",
        "source_name": "Scanharga.com",
        "campaign_title": "Promo PWP Indomaret Tebus Murah 15-28 Oktober 2026",
        "deal_type": "Tebus Murah / PWP",
    }
    assert product_candidate_rejection_reason(row, "Scanharga.com article") is None
    assert product_candidate_review_reason(row, "Scanharga.com article") == "short_name_with_payment_offer_needs_review"


def test_truncated_tissue_name_is_recovered_from_original_label():
    fields = [
        "product_text", "original_product_text", "offer_text", "promo_price", "normal_price",
        "merchant_name", "campaign_title", "promo_status", "valid_from", "valid_until",
        "period_precision", "source_name", "source_url", "source_item_url",
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "truncated_tissue.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow({
                "product_text": "Pasti",
                "original_product_text": "Pasti Hemat 1000 sheet Facial Tissue 1000 sheet",
                "offer_text": "Harga Promo Rp29.800",
                "promo_price": 29800,
                "normal_price": "",
                "merchant_name": "Yogya",
                "campaign_title": "Promo Unggulan Yogya Minggu Ini",
                "promo_status": "active_estimated",
                "valid_from": "2026-10-23",
                "valid_until": "2026-10-25",
                "period_precision": "range",
                "source_name": "Hemat.id",
                "source_url": "https://www.hemat.id/katalog-alfamart,yogya/",
                "source_item_url": "https://www.hemat.id/harga-pasti-hemat-facial-tissue-1000-sheet-yogya/",
            })
        rows = enhancer.load_source(path, path.name)
    assert len(rows) == 1
    row = rows[0]
    assert "Facial Tissue" in row["product_text"], row["product_text"]
    assert "1000 sheet" in row["product_text"].casefold(), row["product_text"]
    assert row["product_category"] == "Tisu & Produk Kertas", row["product_category"]
    assert row["product_validation_status"] != "review", row["product_review_reason"]



def test_payment_benefit_amount_is_not_exported_as_product_price():
    fields = [
        "product_text", "original_product_text", "offer_text", "promo_price", "normal_price",
        "merchant_name", "campaign_title", "promo_status", "valid_from", "valid_until",
        "period_precision", "source_name", "source_url", "deal_type",
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "payment_offer.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow({
                "product_text": "Extra",
                "original_product_text": "Extra Diskon",
                "offer_text": "Rp 2.000 Dengan QRIS BCA myBCA & BCA mobile",
                "promo_price": 2000,
                "normal_price": "",
                "merchant_name": "Indomaret",
                "campaign_title": "Promo PWP Indomaret Tebus Murah 15-28 Oktober 2026",
                "promo_status": "active",
                "valid_from": "2026-10-15",
                "valid_until": "2026-10-28",
                "period_precision": "range",
                "source_name": "Scanharga.com",
                "source_url": "https://www.scanharga.com/2025/11/promo-pwp-indomaret-tebus-murah.html",
                "deal_type": "Tebus Murah / PWP",
            })
        rows = enhancer.load_source(path, path.name)
    assert len(rows) == 1
    row = rows[0]
    assert row["product_validation_status"] == "review"
    assert row["product_review_reason"] == "short_name_with_payment_offer_needs_review"
    assert row["promo_price"] is None
    assert row["normal_price"] is None
    assert row["discount_percent"] is None
    assert "Rp 2.000 Dengan QRIS" in row["offer_text"]
    assert "payment_benefit_amount_not_product_price" in row["price_quality_flag"]
