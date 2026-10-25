# Perbaikan pencarian harga gratis — hasil kosong dan lambat

## Masalah yang diamati

Run sebelumnya menemukan hingga enam halaman kandidat per produk, namun seluruh halaman ditolak validator otomatis. Proses tersebut tetap memeriksa terlalu banyak halaman secara berurutan sehingga lambat dan tidak meninggalkan petunjuk yang bisa diperiksa pengguna.

## Perubahan

- Memisahkan **referensi harga terverifikasi** dari **kandidat hasil pencarian yang belum terverifikasi**.
- Membatasi halaman yang diambil dari hasil pencarian menjadi empat dan halaman yang di-fetch/diverifikasi menjadi maksimal dua per produk.
- Menggunakan timeout request default 5 detik dan mengambil dua halaman kandidat secara paralel dengan sesi HTTP terpisah.
- Mempertahankan judul, domain, URL, snippet, petunjuk nominal (jika nama/varian/ukuran tampak cocok), status, dan alasan penolakan dalam `data/product_market_price_candidates.csv`.
- Menghasilkan sheet terlihat **Kandidat Harga** saat enhancer dijalankan. Petunjuk harga diberi label jelas sebagai belum terverifikasi; tidak digunakan untuk median pasar atau diskon resmi.
- Tetap memakai `data/product_market_price_reference.csv` hanya untuk halaman yang lolos validasi harga + identitas.
- Memindahkan cache pencarian gratis ke `data/free_price_search_cache_v2.csv` agar cache negatif sebelumnya tidak melewati pencarian setelah perbaikan.
- Menambahkan tes parsing RSS/snippet, pemisahan database, dan ekspor worksheet kandidat.

## Langkah CMD di Windows

Tutup Excel, kemudian dari root proyek jalankan:

```cmd
python -m compileall -q engine
python -m pytest -q engine
python engine\promo_free_price_finder.py --limit 5 --force
python engine\enrich_existing_promo_catalog_multisource.py
start "" "hasil\Katalog_Promo_Final.xlsx"
```

Periksa sheet `Kandidat Harga` untuk tautan/cuplikan yang masih butuh pemeriksaan. Sheet `Peluang Harga` tetap khusus harga pasar terverifikasi; sheet itu dapat kosong jika belum ada dua domain berbeda yang valid.

## Batasan

Mesin pencari publik gratis dapat membatasi akses atau tidak menampilkan snippet nominal. Dalam kasus tersebut kandidat dapat hanya berisi judul dan URL. Tidak ada jaminan tiap produk menghasilkan harga. Sistem sengaja tidak mengubah petunjuk hasil pencarian menjadi harga pasar terverifikasi.
