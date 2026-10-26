# Catatan Perubahan — Prioritas OCR

## Yang berubah

1. Menghapus `engine/promo_ai_price_finder.py` dan tes khusus fitur pencarian/prediksi harga AI. JSON antrean/prompt/hasil AI tidak lagi disertakan di `hasil/`.
2. Mengganti `engine/seed_user_price_references.py` dengan perintah aman yang tidak menulis database estimasi.
3. Mengarsipkan 257 estimasi lama dan dua file sumber rentang/alias di `_internal/`; database `data/product_price_reference.csv` yang aktif hanya menyimpan rekaman harga bersumber.
4. Mempertahankan `data/product_market_price_reference.csv` sebagai database referensi pasar yang tetap memerlukan bukti sumber; importer hanya menerima workbook Excel bersumber, bukan JSON estimasi.
5. Menambahkan/menyempurnakan `engine/promo_poster_ocr.py`: OCR lokal RapidOCR/ONNX Runtime, fallback Tesseract, cache gambar, ekstraksi teks dan koordinat, deteksi nominal Rupiah, pasangan harga normal/promo, klaim diskon, kandidat per SKU, dan workbook review.
6. Menambahkan hyperlink ke URL artikel serta poster di workbook review agar reviewer dapat memeriksa materi sumber langsung.
7. Memperbaiki normalisasi URL agar poster cocok dengan baris produk walau domain memakai `www` atau URL berakhiran garis miring.
8. OCR menggabungkan kandidat dari jendela teks yang bertumpang tindih pada harga yang sama, tetapi tetap memisahkan jika nama SKU yang cocok berbeda.
9. Jika OCR gagal mengunduh gambar, percobaan berikutnya akan mencoba ulang. Jika semua gambar telah berada di cache, OCR tetap mengimpor keputusan review yang pengguna ubah.
10. Waktu observasi harga poster yang disetujui memakai waktu persetujuan review, bukan waktu scrape halaman.
11. Memperbaiki tes exporter pasar agar keputusan poster yang sudah diterima tidak dimasukkan lagi ke daftar pencarian harga normal.
12. Memperjelas ringkasan katalog bahwa estimasi bundling bukan diskon terkonfirmasi dan bahwa “periode estimasi” mengacu pada kepastian masa promo, bukan estimasi harga AI.

## Pemakaian

1. Jalankan `Install_Dependency.bat` sekali untuk memasang dependensi.
2. Jalankan `Jalankan_Promo.bat`. Launcher melakukan tes offline, scrape tiga sumber, OCR gambar baru/cache, konsolidasi dan ekspor.
3. Tinjau `hasil/Review_Harga_Poster_OCR.xlsx`. Pastikan produk, ukuran, harga normal, harga promo, dan klaim diskon cocok dengan poster. Pilih `TERIMA`, `TOLAK`, atau biarkan `CEK`; koreksi nama dan harga bila OCR salah.
4. Simpan dan tutup Excel. Jalankan `python engine\promo_poster_ocr.py --import-reviewed-only`, lalu `python engine\enrich_existing_promo_catalog_multisource.py`.
5. Untuk perbandingan harga toko lain, gunakan jalur Excel manual yang terpisah: `python engine\export_manual_price_search.py`, lalu importer dengan path workbook eksplisit.

## Pemeriksaan

124 tes lulus; compileall, self-test normalisasi, dan self-test collector lulus. Smoke test OCR lokal berhasil membaca poster sintetis dan membedakan klaim diskon 44% dengan perhitungan 44,4%. OCR terhadap URL poster publik belum dijalankan massal selama proses penyusunan paket.
