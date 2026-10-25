# Ringkasan Revisi — Referensi Harga & Kolom Excel

## Perubahan

- Memasukkan **102 rentang harga referensi manual** dari daftar yang diberikan pengguna, termasuk batas minimum, maksimum, titik tengah estimasi, satuan, kategori, asal referensi, tanggal input, dan catatan.
- Menambahkan **89 alias katalog terpilih** untuk variasi nama di CSV promo agar dapat dicocokkan tanpa fuzzy matching saat runtime. Alias penawaran kondisional “Nestle … Beli 2 Lebih” dikeluarkan agar tidak diperlakukan sebagai identitas SKU biasa.
- Database `data/product_price_reference.csv` kini berisi **394 rekaman**: 228 observasi scrape dan 166 estimasi dari referensi pengguna/alias. Seeder membangun ulang slot `user_supplied_estimate` dari daftar rentang dan alias aktif, tetapi mempertahankan observasi sumber.
- Referensi pada merchant yang sama diprioritaskan. Jika tidak tersedia, acuan umum/merchant lain dapat menjadi fallback yang ditandai di Excel.
- Harga promo dengan harga asli kosong kini dapat memakai titik tengah rentang sebagai `Harga Asli (Rp)` jika SKU/satuan cocok, transaksi tidak bersyarat, nilai promo masih dalam rentang yang masuk akal, dan rentang tidak terlalu lebar. Harga asli dari sumber tetap terpisah dan tidak dipalsukan oleh estimasi.
- Diskon dihitung dari harga asli sumber atau dari titik tengah referensi saat layak. Klaim diskon sumber yang berbeda dari hitungan disimpan dalam `Klaim Diskon Sumber (Review) (%)`; promo paket/beli banyak dan harga tidak sebanding tidak dihitung otomatis.
- Harga normal yang secara eksplisit disebut dalam detail penawaran dapat dipulihkan bila parser awal melewatkannya, kecuali penawaran tampak seperti bundling/syarat transaksi.
- Urutan kolom utama diubah menjadi `Harga Asli (Rp)` → `Harga Promo (Rp)` → `Diskon (%)`, kemudian basis harga asli, nilai asli dari sumber, referensi tengah/rentang, dan dasar diskon.
- Workbook menampilkan sheet `Referensi Harga` untuk seluruh database dan `Rentang Referensi Pengguna` untuk 102 rentang kanonis.

## Hasil pengolahan CSV/XLSX yang dilampirkan

- 457 baris katalog promo diproses ulang.
- 98 baris memiliki kecocokan ke salah satu rentang/alias referensi.
- 54 baris diisi `Harga Asli (Rp)` dengan titik tengah referensi pengguna.
- 51 diskon dihitung dari titik tengah rentang referensi.
- 27 harga normal yang sudah tertulis eksplisit di detail penawaran dipulihkan ke kolom harga asli sumber.
- 14 penawaran bersyarat/paket dan 9 harga di luar rentang tidak dipaksakan menjadi diskon estimasi; rentang lebar juga ditahan dari perhitungan otomatis.

Angka di atas berlaku untuk katalog yang diunggah pada sesi ini, bukan jaminan jumlah pada scraping berikutnya. Rentang harga adalah acuan perkiraan dari pengguna, bukan verifikasi harga retailer.

## Perubahan file utama

- `engine/promo_price_reference.py`
- `engine/enrich_existing_promo_catalog_multisource.py`
- `engine/seed_user_price_references.py`
- `engine/test_price_reference.py`
- `data/product_price_reference.csv`
- `data/user_product_price_ranges.csv`
- `data/user_product_price_reference_aliases.csv`

## Verifikasi

- `python -m compileall -q engine`: lulus.
- `python -m pytest -q engine`: **91 tes lulus**.

## Batasan

Harga dari daftar rentang dan hasil fallback tidak dinyatakan sebagai harga resmi retailer. Referensi tidak digunakan untuk menghitung diskon ketika transaksi bersyarat, harga promo di luar rentang, satuan tidak sebanding, atau rentang terlalu lebar. Cek kolom `Basis Harga Asli`, `Merchant Acuan Referensi`, `Status Referensi Harga`, `Dasar Diskon`, dan `Catatan` sebelum mempublikasikan diskon estimasi.
