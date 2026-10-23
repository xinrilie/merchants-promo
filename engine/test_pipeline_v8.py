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
from promo_product_validation import product_candidate_rejection_reason


def test_article_headlines_are_not_products():
    cases = [
        "Apa Benefit yang Didapatkan Promo Paris Baguette BNI Wonderful Weekend",
        "Apa Saja Penawaran Dalam Promo Paris Baguette BNI Wonderful Weekend",
        "Bagaimana Cara Mendapatkan Promo Dan Mekanisme Promo Paris Baguette BNI Wonderful Weekend",
        "Berapa Minimum Transaksi Untuk Mendapatkan",
        "Memanfaatkan Akhir Pekan Ini Untuk Menikmati Pilihan Menu Paris Baguette Dengan Lebih Hemat Bersama Promo",
        "Promo Paris Baguette Bni Wonderful Weekend",
        "☞ Cek Info Promo Paris Baguette Bni Wonderful Weekend",
    ]
    for name in cases:
        row = {
            "product_text": name,
            "offer_text": "Diskon 50%",
            "campaign_title": "Promo Paris Baguette BNI Wonderful Weekend 23-24 Oktober 2026",
            "normal_price": None,
            "promo_price": None,
            "discount_percent": 50,
            "source_name": "Katalogpromosi.com",
        }
        reason = product_candidate_rejection_reason(row, "Katalogpromosi.com article")
        assert reason in {
            "editorial_heading_misparsed_as_product",
            "campaign_discount_claim_misparsed_as_product",
            "long_narrative_with_discount_claim_not_product_sku",
        }, (name, reason)


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

def main():
    test_article_headlines_are_not_products()
    test_real_products_stay_eligible()
    test_name_fuzzy_guardrails()
    test_collector_article_extraction_regression()
    test_enhancer_defense_in_depth()
    test_cross_source_fuzzy_merge_and_guardrails()
    print("V8 REGRESSION TESTS PASSED")
    print("- editorial/headline fragments from the screenshot are rejected")
    print("- valid SKU price and bundle examples are kept")
    print("- fuzzy name matching preserves size/flavour distinctions")
    print("- collector filters screenshot editorial false positives before writing scrape output")
    print("- enhancer independently filters stale/old false product rows before fuzzy merge")
    print("- fuzzy merge combines corroborated cross-source offers but preserves variant/price conflicts")


if __name__ == "__main__":
    main()
