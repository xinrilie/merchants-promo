# Ringkasan Revisi Database Referensi Harga

## Perubahan

- Database `data/product_price_reference.csv` menyimpan nilai referensi terbaru untuk kombinasi produk/ukuran/varian, merchant, kanal, dan asal rekaman. Harga normal baru yang lebih mutakhir langsung menggantikan harga sebelumnya; kenaikan maupun penurunan harga diterapkan.
- Database tidak dimaksudkan sebagai arsip historis harian. Rekaman lama per slot diringkas ke observasi terbaru yang tersedia. Jika beberapa bukti memiliki timestamp yang sama persis tetapi nominal berbeda, keduanya dipertahankan agar pencocokan menandai konflik.
- Referensi untuk merchant promo yang sama selalu menjadi prioritas, dengan pencocokan nama/ukuran/varian persis dan kanal yang sama.
- Jika tidak ada referensi terbaru yang layak untuk merchant tersebut, sistem dapat memakai referensi produk persis dari merchant lain. Keluaran menampilkan `Merchant Acuan Referensi`, status fallback, dan dasar diskon yang menyebut bahwa acuannya dari merchant lain.
- Harga dari referensi tidak pernah ditulis ke kolom `Harga Asli dari Sumber (Rp)`. Nilainya tetap di `Harga Normal Referensi (Rp)` dan diskon hasilnya tetap ditandai sebagai estimasi pembanding.
- Urutan kolom utama workbook sekarang memprioritaskan nama produk, kategori, merchant, harga promo, harga asli dari sumber, harga acuan, diskon, status/periode promo, validasi, dan informasi sumber. Detail audit berada lebih ke kanan.

## Syarat penggunaan referensi

- Harga baru tetap harus berasal dari baris produk valid, sumber URL aktif, bukti teks yang jelas menyebut harga normal/asli/reguler, timestamp segar, serta tidak terkena flag harga yang perlu review.
- Pencocokan hanya exact identity; variasi rasa, ukuran, dan SKU yang tidak sama tidak dipaksakan cocok.
- Harga referensi maksimal berumur 30 hari untuk perhitungan diskon. Referensi tua, harga yang konflik pada timestamp yang sama, penawaran bundling, hadiah lintas-SKU, pembayaran, cashback, serta penawaran bersyarat tidak digunakan untuk menghitung diskon SKU biasa.
- Harga yang berasal dari agregator tetap `aggregator_only`, bukan bukti harga resmi retailer.

## Verifikasi

- `python -m compileall -q engine`: lulus.
- `python -m pytest -q engine`: 89 tes lulus setelah perubahan fallback merchant dan pembaruan harga.

## Batasan

Fallback lintas merchant adalah pembanding, bukan jaminan harga normal toko promo yang sedang berjalan. Sebelum dipakai untuk publikasi, periksa tanggal observasi, merchant acuan, URL, dan bukti harga yang ditampilkan.
