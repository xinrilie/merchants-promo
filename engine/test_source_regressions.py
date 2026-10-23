from __future__ import annotations

import csv
import sys
import calendar
import tempfile
from datetime import timedelta
from pathlib import Path

from bs4 import BeautifulSoup

ENGINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_DIR))

import promo_catalog_all_merchants as momsmoney
import promo_multisource_collector as multi
import enrich_existing_promo_catalog_multisource as enhancer


def test_article_source_adapters_keep_offer_and_period_evidence():
    cases = [
        (
            "Katalogpromosi.com",
            "Promo JSM Indomaret 23-25 Oktober 2026",
            "Indomie Goreng 5 pcs: Harga Promo Rp 15.000",
            "Indomie Goreng 5",
            15000,
        ),
        (
            "Scanharga.com",
            "Katalog Promo Superindo 23-25 Oktober 2026",
            "Sunlight Jeruk 755 ml: Harga Normal Rp 22.900, Harga Promo Rp 16.900",
            "Sunlight Jeruk 755 ml",
            16900,
        ),
    ]
    for source_name, title, line, expected_product, expected_price in cases:
        container = BeautifulSoup(f"<div class='entry-content'><p>{line}</p></div>", "html.parser")
        meta = multi.parse_promo_metadata(title, container.get_text(" ", strip=True))
        rows = multi.extract_article_rows(
            container, source_name, title, f"https://{source_name.lower()}/promo-test/", meta
        )
        assert len(rows) == 1, [(row.get("product_text"), row.get("offer_text")) for row in rows]
        row = rows[0]
        assert row["product_text"] == expected_product
        assert row["promo_price"] == expected_price
        assert row["price_evidence_text"] == row["offer_text"]
        assert row["period_evidence_source"] == "campaign_title"
        assert row["period_evidence_text"] == "23-25 Oktober 2026"


def test_hemat_card_rejects_inherited_offer_on_editorial_text_and_keeps_evidence_on_real_sku():
    html = """
    <html><head><title>Katalog Promo Alfamart 23-25 Oktober 2026</title></head><body>
      <div class="product-card">
        <a href="/product/soap">Susu UHT Full Cream 1 L Harga Normal Rp 20.000 Harga Promo Rp 15.000</a>
        <a href="/katalog-alfamart/">Alfamart</a>
      </div>
      <div class="product-card">
        <a href="/promo">Kalau Kamu Lagi Cari Promo Belanja Hemat, Ini Jawabannya Rp 50.000 Diskon 50%, Beli 1 Gratis 1</a>
        <a href="/katalog-alfamart/">Alfamart</a>
      </div>
    </body></html>
    """
    multi.REJECTED_PRODUCT_CANDIDATES.clear()
    rows = multi.extract_hemat_cards(
        BeautifulSoup(html, "html.parser"), "https://www.hemat.id/katalog-alfamart/"
    )
    names = [row["product_text"] for row in rows]
    assert any("susu uht full cream 1 l" in name.casefold() for name in names), names
    assert not any("Kalau Kamu Lagi Cari" in name for name in names), names
    good = next(row for row in rows if "susu uht full cream" in row["product_text"].casefold())
    assert good["promo_price"] == 15000
    assert "Harga Promo Rp 15.000" in good["price_evidence_text"]
    assert good["period_evidence_source"] in {"campaign_title", "card_context", "article_body", "unknown"}
    assert any(
        item.get("rejection_reason") in {
            "editorial_sentence_misparsed_as_product",
            "long_editorial_sentence_misparsed_as_product",
        }
        for item in multi.REJECTED_PRODUCT_CANDIDATES
    )


def test_momsmoney_line_prices_only_come_from_the_offer_segment():
    row = momsmoney.parse_product_line(
        "My Baby Body Cologne Sweet Floral 100 ml – Harga Normal Rp 22.900 | Harga Promo Rp 11.450",
        None,
        "https://momsmoney.kontan.co.id/news/promo-test",
    )
    assert row is not None
    assert row["normal_price"] == 22900
    assert row["promo_price"] == 11450
    assert row["price_evidence_text"] == row["offer_text"]
    assert row["discount_evidence_text"] is not None


def test_price_is_not_borrowed_from_the_product_name():
    parsed = multi.parse_offer_text(
        "Campaign headline mentioning Harga Promo Rp 99.000",
        "Diskon 50%",
        "article",
    )
    assert parsed["promo_price"] is None
    assert parsed["normal_price"] is None
    assert parsed["discount_percent"] == 50.0


def test_conflicting_title_and_body_period_is_downgraded_to_estimate():
    today = multi.TODAY
    month_names = {}
    for key, value in multi.MONTHS.items():
        if len(key) > len(month_names.get(value, "")):
            month_names[value] = key.title()
    month = month_names[today.month]
    last_day = calendar.monthrange(today.year, today.month)[1]
    title = f"Promo Alfamart 1-{last_day} {month} {today.year}"
    body = f"Periode promo 2-{last_day} {month} {today.year}"
    meta = multi.parse_promo_metadata(title, body)
    assert "title_visible_period_differs" in (meta["period_flags"] or "")
    assert meta["promo_status"] == "active_estimated"


def test_campaign_month_period_is_explicitly_estimated_with_provenance():
    meta = multi.parse_promo_metadata("Promo Alfamidi Oktober 2026", "")
    assert meta["period_precision"] == "month"
    assert meta["period_evidence_source"] == "campaign_title"
    assert meta["period_evidence_text"] == "Oktober 2026"
    assert meta["promo_status"] in {"active_estimated", "expired", "upcoming_estimated"}


def test_enhancer_downgrades_dates_without_evidence_and_drops_detached_prices():
    with tempfile.TemporaryDirectory() as directory:
        _test_detached_price_evidence(Path(directory) / "source.csv")


def _test_detached_price_evidence(path: Path):
    today = enhancer.TODAY.isoformat()
    fields = [
        "product_text", "merchant_name", "campaign_title", "promo_status", "valid_from",
        "valid_until", "period_precision", "promo_price", "normal_price", "price_basis",
        "offer_text", "source_url", "source_name",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({
            "product_text": "Susu UHT Full Cream 1 L",
            "merchant_name": "Indomaret",
            "campaign_title": "Promo Susu UHT",
            "promo_status": "active",
            "valid_from": today,
            "valid_until": today,
            "period_precision": "range",
            "promo_price": "15000",
            "normal_price": "20000",
            "price_basis": "offer_price",
            "offer_text": "Diskon 50%",
            "source_url": "https://example.test/promo",
            "source_name": "Legacy source",
        })
    rows = enhancer.load_source(path, path.name)
    assert len(rows) == 1
    row = rows[0]
    assert row["promo_price"] is None and row["normal_price"] is None
    assert "price_without_offer_evidence_routed_to_review" in (row["price_quality_flag"] or "")
    assert row["period_precision"] == "unverified"
    assert row["promo_status"] == "active_estimated"
    assert "period_precision_missing_source_evidence" in (row["period_flags"] or "")


def main():
    test_article_source_adapters_keep_offer_and_period_evidence()
    test_hemat_card_rejects_inherited_offer_on_editorial_text_and_keeps_evidence_on_real_sku()
    test_momsmoney_line_prices_only_come_from_the_offer_segment()
    test_price_is_not_borrowed_from_the_product_name()
    test_conflicting_title_and_body_period_is_downgraded_to_estimate()
    test_campaign_month_period_is_explicitly_estimated_with_provenance()
    test_enhancer_downgrades_dates_without_evidence_and_drops_detached_prices()
    print("SOURCE REGRESSION TESTS PASSED: Hemat.id, Katalogpromosi.com, Scanharga.com, Momsmoney/Kontan, price evidence, and period provenance.")


if __name__ == "__main__":
    main()
