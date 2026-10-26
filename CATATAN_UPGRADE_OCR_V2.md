# Catatan Upgrade OCR Poster v2

## Cakupan

Upgrade diterapkan langsung ke `engine/promo_poster_ocr.py` pada proyek Merchants Promo. Perubahan ini mempertahankan alur sumber, cache, workbook review, importer harga, dan gerbang persetujuan manusia yang sudah ada.

## Perubahan inti

- Menambah deteksi label harga merah/kuning menggunakan OpenCV dan OCR lokal pada crop label sebagai jalur rescue ketika OCR halaman penuh melewatkan harga.
- Menambah `recognition_method` pada rekaman baris OCR agar setiap teks dapat ditelusuri apakah berasal dari OCR halaman penuh atau crop warna.
- Menambah `ocr_pipeline_version` pada cache. Cache lama ditandai kedaluwarsa dan diproses ulang dengan v2.0; migrasi dapat dilakukan bertahap menggunakan `--limit 10` atau `--limit 25`.
- Mencegah persentase kemasan seperti `30% LEBIH BANYAK SEREAL` dianggap otomatis sebagai klaim diskon harga. Jika klaim diskon eksplisit saling bertentangan, hasil ditandai ambigu.
- Menambahkan metadata review untuk metode pasangan harga, konteks promo, syarat pembelian, cakupan wilayah, harga member, persentase kemasan yang dikecualikan, dan konteks klaim diskon.
- Memperbarui README dan setup Windows agar menggunakan stack lokal RapidOCR/ONNX Runtime dengan Tesseract opsional, tanpa memasang PaddlePaddle/PaddleOCR, EasyOCR, atau layanan OCR cloud.
- Mempertahankan `TERIMA` sebagai satu-satunya keputusan yang dapat mengaktifkan override; heuristik baru tidak menyetujui harga secara otomatis.

## Pengujian

- `python -m compileall -q engine`: lulus.
- `python -m pytest -q engine`: **163 passed**.
- Uji poster nyata, halaman 3 Promo Gantung Alfamart: Tesseract lokal menghasilkan 74 baris OCR; jalur crop label merah menangkap `20.900`, OCR halaman penuh menangkap `28.900`, lalu parser menghasilkan pasangan `[28900, 20900]` melalui `INFERENSI_GEOMETRI_SEJAJAR`. Confidence crop Tesseract pada baris `20.900` terbaca `0.0` pada uji tersebut, sehingga pemulihan angka tidak dianggap sebagai kepastian; kandidat tetap membutuhkan review manusia.
- Uji ini bukan klaim bahwa semua 16 halaman sudah menjalani OCR end-to-end. OCR penuh seluruh poster belum dijalankan sebagai satu batch pada verifikasi ini.

## Catatan operasional

1. Tutup workbook Excel sebelum OCR/import dijalankan ulang.
2. Sebelum migrasi semua cache, backup folder `_internal/` dan `hasil/`.
3. Jalankan `Jalankan_Promo.bat --test-only` untuk pemeriksaan offline.
4. Migrasikan cache secara bertahap, misalnya `python engine\promo_poster_ocr.py --limit 10`, lalu periksa workbook uji dengan `--rebuild-review-only --limit 10`.
5. Setelah hasil review memadai, jalankan OCR penuh. Jangan mengubah keputusan kandidat menjadi `TERIMA` hanya berdasarkan skor OCR atau metode pairing.

## Batasan

- Masker warna merah/kuning dioptimalkan untuk pola poster Promo Gantung yang diuji; warna desain poster lain bisa luput atau menghasilkan crop tak relevan.
- OpenCV membantu memilih crop, tetapi tetap bukan pembukti bahwa angka adalah harga. OCR crop yang ambigu atau rendah keyakinannya perlu ditinjau dari URL gambar asli.
- Integrasi teknis diuji melalui test suite lokal dan importer yang ada. Data poster yang seluruhnya berbeda desain perlu ditambahkan sebagai kumpulan regresi sebelum penggunaan otomatis berskala besar.


## Pembaruan kinerja v2.1 (26 Oktober 2026)

Log dari uji pengguna menunjukkan crop rescue menjalankan OCR untuk setiap kandidat label warna, termasuk label yang harganya sudah terbaca dari OCR halaman penuh. Pada 16 poster acuan, detektor mengusulkan 12–23 kotak warna per halaman, sehingga menimbulkan inferensi ONNX tambahan dan peringatan `The text detection result is empty` pada crop yang tidak berisi teks yang dapat dibaca.

Perbaikan: crop rescue sekarang dilewati jika OCR halaman penuh sudah menemukan harga di area label; jumlah crop OCR tambahan dibatasi maksimal 6 per gambar; cache diberi versi `poster-ocr-v2.1-fast-crop` agar hasil versi sebelumnya diproses ulang dengan jalur baru; output progres ditampilkan setelah setiap gambar beserta durasi. Peringatan deteksi kosong yang tersisa dapat muncul pada crop yang memang masih dicoba dan bukan dengan sendirinya error fatal.

Verifikasi setelah perubahan kinerja: `python -m pytest -q engine` — 165 passed. OCR end-to-end untuk semua poster tetap perlu dijalankan di mesin pengguna dengan memantau durasi per gambar.
