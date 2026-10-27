# Catatan Upgrade OCR — Pemisahan Konteks Promo

Tanggal: 27 Oktober 2026  
Jenis: upgrade parser untuk eksplorasi prototipe; **bukan hasil evaluasi skripsi formal**.

## Tujuan

Menindaklanjuti audit preview parser sebelumnya. Perubahan berfokus pada pemisahan nama produk dari syarat promo, klasifikasi harga member dan konteks wilayah, serta statistik audit yang lebih konsisten. Mesin OCR dan cache OCR tidak diganti.

## Perubahan

1. Versi parser kandidat menjadi `poster-ocr-v2.6-offer-context`. `OCR_PIPELINE_VERSION` tetap `poster-ocr-v2.1-fast-crop` karena deteksi dan pengenalan teks tidak berubah.
2. Frasa `TEBUS MURAH` dipisahkan dari nama produk dan, jika terbaca, dicatat sebagai syarat penawaran. Parser menangani variasi OCR seperti `dengon` untuk `dengan` dan `DIGITALSTIKER` tanpa spasi.
3. Judul generik seperti `Bisa Tebus Murah Produk dibawah ini` dan slogan kampanye tidak diperlakukan sebagai nama SKU. Untuk pola `Tebus Murah Telur Ayam Negeri isi 10's`, nama produk yang mengikuti frasa penawaran tetap dipertahankan.
4. Daftar wilayah seperti `P.Jowo,Bali,Sumatera (kec. Batam)` tidak digunakan sebagai nama produk. Penanda wilayah dicatat di konteks regional, tetapi tidak otomatis diklasifikasikan sebagai harga regional bila tidak ada harga numerik regional.
5. Harga member hanya diisi dari label member eksplisit jika ada satu nominal terdekat yang tidak berkonflik dengan harga normal/promo, potongan, minimum belanja, atau daftar harga regional. Bila lebih dari satu harga sama-sama mungkin, kolom harga member dibiarkan kosong.
6. Statistik membedakan jumlah nama yang dikarantina keras dari jumlah raw name yang ditahan sebagai bukti review. Ringkasan per gambar juga menyertakan hitungan harga member, potongan member, minimum belanja, konteks regional, dan syarat tebus murah.
7. Nama file test baru didasarkan pada fungsi (`test_promo_poster_purchase_context.py`), bukan nomor versi.

## Pengujian

- `python -m compileall -q engine`: lulus.
- `python -m pytest -q engine`: **191 passed** pada salinan proyek yang digunakan untuk pengembangan di lingkungan ini.
- Uji fungsi mencakup judul produk sebelum/sesudah `TEBUS MURAH`, judul generik, syarat 5 stiker, daftar wilayah, penentuan harga member yang unik, pencegahan tebakan harga member bila ada harga yang saling bersaing, dan pemisahan metrik karantina.
- Smoke test parser dijalankan pada lima halaman terpilih dari snapshot OCR lokal: 88 kandidat dan 27 pasangan harga. Ini bukan rebuild seluruh 378 gambar.

## Batasan

- Salinan proyek snapshot yang tersedia di lingkungan pengembangan tidak memiliki cache aktif penuh yang sama dengan komputer Windows pengguna. Karena itu preview cache penuh harus dikonfirmasi di mesin pengguna setelah pemasangan.
- Hasil di atas adalah uji regresi dan smoke test parser, bukan ukuran akurasi formal. Belum ada ground truth beranotasi; tidak ada klaim precision/recall/F1 atau akurasi SKU.
- Semua kandidat tetap membutuhkan review manusia. Perubahan ini tidak menyetujui harga secara otomatis.

## Cara memasang

1. Cadangkan `hasil\Review_Harga_Poster_OCR.xlsx` dan file override bila ada keputusan manual.
2. Ekstrak overlay ke root proyek `D:\Coding\Py\merchants-promo`. Overlay hanya berisi source OCR, test baru, dan dokumentasi; tidak menyertakan `_internal` atau `hasil`.
3. Jalankan `python -m pytest -q engine`.
4. Buat preview dari cache yang ada: `python engine\promo_poster_ocr.py --rebuild-review-only --limit 25`.
5. Periksa nama produk, syarat tebus murah, konteks regional, dan harga member. Jangan jalankan importer dari preview.
6. Setelah preview diterima dan cache menunjukkan 100%, backup lagi lalu baru pertimbangkan rebuild review penuh.
