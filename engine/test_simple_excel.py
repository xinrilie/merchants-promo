from __future__ import annotations

import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE))

from enrich_existing_promo_catalog_multisource import (  # noqa: E402
    SIMPLE_EXCEL_COLUMNS,
    make_simple_excel_row,
    write_simple_catalog_csv,
)
from promo_multisource_collector import parse_offer_text  # noqa: E402


def base_row(**overrides):
    row = {
        "product_text": "Contoh Produk 100 g",
        "original_product_text": "Contoh Produk 100 g",
        "merchant_name": "Indomaret",
        "normal_price": None,
        "promo_price": None,
        "discount_percent": None,
        "discount_percent_basis": "not_available",
        "discount_percent_confidence": "unknown",
        "discount_savings_rp": None,
        "reference_normal_price": None,
        "reference_price_low": None,
        "reference_price_high": None,
        "reference_price_unit": None,
        "reference_price_record_origin": None,
        "reference_primary_display_allowed": False,
        "price_quality_flag": None,
        "price_basis": "offer_price",
        "purchase_condition": None,
        "deal_type": None,
        "offer_text": "",
        "raw_line": "",
        "promo_status": "active",
        "valid_from": "2026-10-23",
        "valid_until": "2026-10-28",
        "period_precision": "range",
        "period_certainty": "confirmed",
        "product_validation_status": "eligible",
        "verification_status": "aggregator_only",
        "source_verification_label": "Agregator; belum diverifikasi retailer",
        "source_url": "https://example.invalid/promo",
        "source_name": "Test",
        "source_type": "aggregator",
        "freshness_status": "fresh",
        "campaign_title": "Promo contoh",
    }
    row.update(overrides)
    return row


def test_explicit_nominal_discount_is_not_misread_as_product_price():
    parsed = parse_offer_text(
        "Yummy Choice Fried Chicken",
        "Beli 5 pcs Diskon Rp. 10.000",
        "article",
    )
    assert parsed["promo_price"] is None
    assert parsed["normal_price"] is None
    assert parsed["discount_savings_rp"] == 10000
    assert parsed["discount_basis"] == "explicit_amount_discount"
    assert parsed["price_basis"] == "discount_amount_offer"


def test_simple_sheet_estimates_percent_from_nominal_savings_and_matching_unit():
    row = base_row(
        product_text="Yummy Choice Fried Chicken",
        original_product_text="Yummy Choice Fried Chicken",
        promo_price=10000,  # legacy bad parse; display helper must repair it
        price_basis="bundle_offer_amount",
        purchase_condition="Beli 5 pcs",
        offer_text="Beli 5 pcs Diskon Rp. 10.000",
        raw_line="Yummy Choice Fried Chicken -> Beli 5 pcs Diskon Rp. 10.000",
        reference_normal_price=15000,
        reference_price_low=12000,
        reference_price_high=18000,
        reference_price_unit="per potong",
        reference_price_record_origin="user_supplied_estimate",
    )
    simple = make_simple_excel_row(row)
    assert simple["Harga Promo (Rp)"] == "—"
    assert simple["Hemat (Rp)"] == 10000
    assert simple["Harga Normal/Acuan (Rp)"] == 75000
    assert simple["Diskon (%)"] == 13.3
    assert "Estimasi" in simple["Status Diskon"]


def test_simple_sheet_calculates_explicit_same_offer_price_pair():
    row = base_row(
        product_text="Sedaap Mie Instant 90 g",
        promo_price=7900,
        normal_price=None,
        price_basis="bundle_offer_amount",
        purchase_condition="Beli 3",
        offer_text="Beli 3 Lebih Hemat harga spesial Rp7.900 dari harga normalnya Rp.9.000",
        raw_line="Beli 3 Lebih Hemat harga spesial Rp7.900 dari harga normalnya Rp.9.000",
    )
    simple = make_simple_excel_row(row)
    assert simple["Harga Normal/Acuan (Rp)"] == 9000
    assert simple["Harga Promo (Rp)"] == 7900
    assert simple["Hemat (Rp)"] == 1100
    assert simple["Diskon (%)"] == 12.2
    assert "tertulis" in simple["Status Diskon"].casefold()


def test_simple_sheet_estimates_valid_bundle_discount_from_matching_reference():
    row = base_row(
        product_text="Frisian Flag Susu Cair UHT Cokelat 225 ml",
        promo_price=9900,
        price_basis="bundle_offer_amount",
        purchase_condition="Beli 2",
        offer_text="Beli 2 Rp. 9.900",
        raw_line="Beli 2 Rp. 9.900",
        reference_normal_price=6000,
        reference_price_low=5500,
        reference_price_high=6500,
        reference_price_unit="pack",
        reference_price_record_origin="user_supplied_estimate",
    )
    simple = make_simple_excel_row(row)
    assert simple["Harga Normal/Acuan (Rp)"] == 12000
    assert simple["Harga Promo (Rp)"] == 9900
    assert simple["Hemat (Rp)"] == 2100
    assert simple["Diskon (%)"] == 17.5
    assert "Estimasi" in simple["Status Diskon"]


def test_simple_sheet_does_not_calculate_implausibly_low_bundle_price():
    row = base_row(
        product_text="Indomaret Air Mineral 600 ml",
        promo_price=2500,
        price_basis="bundle_offer_amount",
        purchase_condition="Beli 2",
        offer_text="Beli 2 Rp. 2.500",
        raw_line="Beli 2 Rp. 2.500",
        reference_normal_price=4250,
        reference_price_low=3000,
        reference_price_high=5500,
        reference_price_unit="pack",
        reference_price_record_origin="user_supplied_estimate",
    )
    simple = make_simple_excel_row(row)
    assert simple["Diskon (%)"] == "Belum bisa dihitung"
    assert "review" in simple["Status Diskon"].casefold()


def test_every_simple_row_has_discount_cell_and_period_after_discount():
    row = base_row(
        promo_price=9000,
        offer_text="Rp9.000",
        raw_line="Rp9.000",
    )
    simple = make_simple_excel_row(row)
    assert simple["Diskon (%)"] not in (None, "")
    assert SIMPLE_EXCEL_COLUMNS.index("Masa Berlaku") == SIMPLE_EXCEL_COLUMNS.index("Diskon (%)") + 1
    assert simple["Masa Berlaku"] == "23–28 Okt 2026"



def test_simple_csv_uses_exact_same_values_as_excel_promo_sheet(tmp_path):
    import csv

    rows = [
        base_row(
            product_text="Sedaap Mie Instant 90 g",
            promo_price=7900,
            offer_text="Beli 3 Lebih Hemat harga spesial Rp7.900 dari harga normalnya Rp.9.000",
            raw_line="Beli 3 Lebih Hemat harga spesial Rp7.900 dari harga normalnya Rp.9.000",
        ),
        base_row(
            product_text="Contoh Produk Tanpa Harga",
            promo_price=None,
            normal_price=None,
            offer_text="Promo berlaku untuk produk pilihan",
            raw_line="Promo berlaku untuk produk pilihan",
        ),
    ]
    out = tmp_path / "Katalog_Promo_Final_Ringkas.csv"
    assert write_simple_catalog_csv(out, rows) == len(rows)
    with out.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        csv_rows = list(reader)
        assert reader.fieldnames == SIMPLE_EXCEL_COLUMNS

    expected = [make_simple_excel_row(row) for row in rows]
    for actual, exp in zip(csv_rows, expected):
        for field in SIMPLE_EXCEL_COLUMNS:
            assert actual[field] == str(exp.get(field, "—") if exp.get(field, "—") is not None else ""), (field, actual[field], exp.get(field))
    assert csv_rows[0]["Diskon (%)"] == "12.2"
    assert csv_rows[1]["Diskon (%)"] == "Belum bisa dihitung"
    assert csv_rows[0]["Masa Berlaku"] == "23–28 Okt 2026"
