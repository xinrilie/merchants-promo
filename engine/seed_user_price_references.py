"""Disabled legacy estimator seeder.

The project now uses only explicit source prices and manually approved poster-OCR
prices. Historical AI/user price estimates are preserved under _internal/ for audit
and must not be reseeded into the active reference database.
"""
from __future__ import annotations


def main() -> int:
    print("Penyemaian estimasi harga lama dinonaktifkan.")
    print("Harga aktif hanya berasal dari observasi sumber atau koreksi poster OCR yang disetujui.")
    print("Database lama disimpan di _internal/legacy_price_estimates_disabled.csv bila tersedia.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
