# Audit dan Perbaikan OCR Poster — 26 Oktober 2026

## Temuan dari Review_Harga_Poster_OCR.xlsx

Workbook yang diunggah memiliki 662 baris kandidat dari 22 URL gambar unik. Semua keputusan masih `CEK`; kolom normal/promo kandidat kosong. Banyak cuplikan mengandung nama dan harga dari beberapa produk pada satu halaman penuh. Confidence OCR tinggi pada baris contoh, jadi masalah bukan sekadar model OCR: pengelompokan posisi dan filter parser juga menyebabkan kandidat harga hilang.

## Perubahan

- PaddleOCR 3.x menjadi mesin OCR lokal utama dengan CPU; RapidOCR dan Tesseract tetap fallback.
- Parsing result mendukung `rec_texts`, `rec_scores`, `rec_boxes` dan box `xyxy` dari PaddleOCR.
- Preprocessing gambar memperbesar teks kecil, memperbaiki kontras dengan Pillow dan memakai OpenCV CLAHE bila tersedia.
- Jendela spasial disempitkan dari 19% x 15% menjadi 12% x 7,5% ukuran gambar agar harga dari tile SKU tetangga tidak mudah tercampur.
- Menghapus guard konteks yang salah menolak teks OCR seperti `Rp20.000` tanpa spasi; bentuk umum yang terbaca `R33.900` dan separator ribuan `31:900` juga dipulihkan sebagai kandidat.
- Memperbaiki pembacaan umum OCR `R33.900` dan pemisah ribuan `31:900` sebagai kandidat, bukan harga otomatis.
- Dua harga pada jendela lokal dapat diusulkan sebagai harga tinggi/rendah bila perbedaannya masuk akal; selalu perlu review visual dan default `CEK`.
- Saran nama produk bisa berasal dari produk katalog eligible milik retailer yang disebut dalam judul kampanye, namun harga produk tersebut tidak dipakai sebagai bukti poster.
- Statistik `candidate_price_pair_count` dan `candidate_product_suggestion_count` ditambahkan agar kualitas hasil awal lebih mudah dilihat.
- Windows launcher/installer memprioritaskan `D:\Python312\python.exe` dan memverifikasi dependensi OCR. Jika refresh paksa gagal mengunduh gambar, cache OCR sukses sebelumnya dipertahankan.

## Validasi

- `python -m compileall -q engine`: lulus.
- `python engine/promo_names.py`: self-test lulus.
- `python engine/promo_multisource_collector.py --self-test`: self-test lulus.
- `python -m pytest -q engine`: **130 passed**.
- Tes mencakup output PaddleOCR sintetis (termasuk box NumPy), dua tile produk yang berdampingan, parser harga berlabel/tanpa label, dan typo OCR umum.

## Belum diverifikasi

Model PaddleOCR belum dijalankan terhadap seluruh URL poster live dari lingkungan pengembangan ini. Instalasi memerlukan internet dan model kemungkinan diunduh pada penggunaan pertama. OCR tidak membuktikan harga yang dicoret; semua kandidat tetap perlu diverifikasi visual di Excel sebelum `TERIMA`.
