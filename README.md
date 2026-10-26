# Merchants Promo

Pipeline Python untuk mengumpulkan katalog promo dari tiga sumber publik, membaca bukti harga dari teks sumber dan poster promo, memvalidasi SKU/harga/periode, serta menghasilkan Excel dan CSV. **Tidak ada prediksi harga AI tanpa sumber**. Jika harga normal hanya terlihat pada gambar, pipeline memakai OCR lokal dan meminta persetujuan manusia sebelum harga dipakai.

## Sumber aktif

- **Hemat.id** — katalog/kartu promo.
- **Katalogpromosi.com** — artikel katalog dan poster.
- **Scanharga.com** — artikel promo dan poster.

Pipeline snapshot ini menggunakan tiga sumber tersebut; Momsmoney/Kontan belum menjadi sumber aktif.

## Menjalankan di Windows

Jalankan dari folder proyek di CMD. Untuk instalasi pertama:

```cmd
Install_Dependency.bat
```

Untuk pemeriksaan offline tanpa scraping:

```cmd
Jalankan_Promo.bat --test-only
```

Untuk menjalankan pipeline lengkap, termasuk kolektor, OCR poster, konsolidasi, dan ekspor:

```cmd
Jalankan_Promo.bat
```

Dependensi utama: Python 3.10+, requests, BeautifulSoup, openpyxl, RapidOCR, ONNX Runtime, Pillow, OpenCV headless, pytesseract (wrapper opsional untuk fallback), dan pytest. PaddlePaddle/PaddleOCR tidak dipasang oleh paket ini. Tesseract executable juga opsional dan harus dipasang terpisah jika fallback tersebut ingin digunakan.

## Alur harga: teks sumber dahulu, OCR poster berikutnya

1. Parser membaca harga normal/asli, harga promo, serta klaim diskon yang dinyatakan pada teks sumber.
2. `engine/promo_poster_ocr.py` memproses URL gambar poster yang sudah dikumpulkan. OCR berjalan lokal memakai RapidOCR + ONNX Runtime; Tesseract lokal dapat digunakan sebagai fallback jika executable-nya sudah tersedia. Tidak ada EasyOCR atau API OCR cloud.
3. OCR menyimpan teks dan kotak posisi ke `_internal/promo_poster_ocr_lines.csv`, kandidat ke `_internal/promo_poster_price_candidates.csv`, serta cache gambar ke `_internal/promo_poster_ocr_cache.csv`.
4. Pengelompokan kandidat memakai posisi kotak harga dan kolom produk. Untuk pola poster seperti Promo Gantung Alfamart, dua harga yang terbaca sebagai kotak terpisah boleh diusulkan sebagai pasangan jika harga lebih tinggi berada di atas harga lebih rendah dan keduanya sejajar pada area yang sama. Jalur rescue tambahan mendeteksi label harga kuning/merah melalui OpenCV lalu menjalankan OCR lokal pada crop label jika angka luput dari OCR halaman penuh. Ini hanya inferensi layout, bukan bukti final; keputusan tetap `CEK` sampai ditinjau.
5. Klaim persentase diperiksa menurut konteks. Teks kemasan seperti `30% lebih banyak sereal` dan `BUY 1 EXTRA 1` tidak otomatis dianggap diskon harga atau promo multi-item dari retailer. Metadata review juga menandai harga member, syarat pembelian, wilayah, harga spesial, dan ketentuan kampanye agar tidak otomatis ditempelkan ke SKU.
6. Area pencarian nama produk diperluas ke bagian atas tile dengan batas kolom dan batas dari harga baris sebelumnya. Ini mencegah judul SKU yang berada agak jauh di atas harga terpotong atau tertukar dengan judul dari kolom sebelah.
7. Pilihan SKU dropdown menggunakan ambang kemiripan yang lebih ketat. Jika tidak ada kandidat yang cukup kuat, nama katalog dibiarkan kosong dan pilihan "tidak ada SKU cocok" tetap tersedia.
8. Kandidat harga masuk ke `hasil/Review_Harga_Poster_OCR.xlsx`. Kolom `URL Gambar` tetap menjadi sumber bukti visual saat kasus memang ambigu.
9. Isi `Keputusan` dengan `TERIMA`, `TOLAK`, atau `CEK`. Koreksi nama produk, harga normal, dan harga promo sesuai poster. Jangan terima kandidat yang pasangannya ambigu atau ukuran/variannya berbeda.
10. Simpan dan tutup Excel. Jalankan:

   ```cmd
   python engine\promo_poster_ocr.py --import-reviewed-only
   python engine\enrich_existing_promo_catalog_multisource.py
   ```

   Harga yang disetujui ditautkan ke sumber artikel dan gambar poster. Harga normal yang disetujui bisa menjadi referensi normal dari sumber; harga promo mengisi kolom promo bila belum ada atau nilainya konsisten. Harga sumber yang berbeda tidak ditimpa diam-diam: dicatat sebagai konflik untuk ditinjau.

OCR tidak bisa menjamin pembacaan digit pada poster yang kecil/buram. Label teks, posisi, dan kecocokan perhitungan diskon hanya dipakai untuk mengusulkan pasangan; persetujuan di Excel tetap menjadi gerbang penggunaan harga.

### Mengelola OCR

```cmd
python engine\promo_poster_ocr.py --limit 25
```

Memproses paling banyak 25 gambar yang belum memiliki cache atau masih memakai versi pipeline lama. Jalankan tanpa `--limit` untuk memproses semua gambar yang membutuhkan OCR. Gunakan `--force` untuk memproses ulang seluruh gambar. Gambar yang sempat gagal diunduh/OCR dicoba ulang pada pelaksanaan berikutnya; gambar yang berhasil tapi tidak mengandung teks tetap di-cache agar tidak diproses terus-menerus. Upgrade `poster-ocr-v2.0` membuat cache lama dianggap perlu diproses ulang satu kali agar crop harga merah/kuning dan filter konteks persentase diterapkan. Untuk migrasi bertahap, jalankan `--limit 10` atau `--limit 25` beberapa kali sebelum menjalankan pipeline penuh.

Untuk menguji perubahan parser pada 25 gambar yang paling baru masuk cache tanpa menimpa workbook utama, jalankan:

```cmd
python engine\promo_poster_ocr.py --rebuild-review-only --limit 25
```

Output terpisah: `hasil\Review_Harga_Poster_OCR_UjiTerbaru.xlsx`. Mode ini memakai cache, tidak mengunduh atau menjalankan OCR ulang, dan tidak mengubah persetujuan/harga aktif. `--rebuild-review-only` tanpa `--limit` tetap membangun ulang workbook utama dari seluruh cache.

Setelah mengubah workbook review utama, gunakan `--import-reviewed-only` di atas. Perintah OCR pada launcher juga mengimpor keputusan review yang ada, termasuk ketika tidak ada gambar baru untuk diproses.

## Estimasi AI lama dinonaktifkan

Harga yang sebelumnya dihasilkan oleh AI atau rentang perkiraan pengguna **tidak lagi digunakan untuk mengisi harga normal atau menghitung diskon**. Sebelum paket ini dibangun, 257 rekaman estimasi lama dipindahkan ke `_internal/legacy_price_estimates_disabled.csv`; sumber rentang lama disimpan sebagai `legacy_user_product_price_ranges_disabled.csv` dan `legacy_user_product_price_reference_aliases_disabled.csv` di `_internal/`. Arsip ini hanya untuk audit/rujukan; pipeline aktif tidak memuatnya.

`engine/promo_ai_price_finder.py`, antrean/prompt/JSON prediksi AI di folder hasil, dan tes khusus pencarian AI telah dihapus. `engine/seed_user_price_references.py` kini menjadi perintah aman yang hanya memberi informasi bahwa seeding estimasi dinonaktifkan.

## Referensi harga pasar adalah fitur terpisah

Jika ingin mengetahui apakah harga promo lebih rendah dibanding toko lain, jalankan:

```cmd
python engine\export_manual_price_search.py
```

Isi `hasil\Daftar_Cari_Harga_Manual.xlsx` berdasarkan halaman harga langsung: `Harga Referensi (Rp) - isi manual`, `Nama Sumber`, `URL Halaman Harga`, `Bukti Harga pada Halaman`, `Tanggal Cek (YYYY-MM-DD)`, dan jenis referensi. Simpan dan tutup Excel, lalu impor dengan:

```cmd
python engine\import_manual_price_references.py hasil\Daftar_Cari_Harga_Manual.xlsx
python engine\enrich_existing_promo_catalog_multisource.py
```

Sumber pasar harus memiliki URL dan bukti harga yang bisa ditelusuri. Median harga pasar/persentase lebih murah baru dihitung jika ada minimal dua domain independen yang cocok dengan SKU/ukuran/varian. Ini berbeda dari diskon promo yang dihitung langsung dari harga normal dan promo pada sumber yang sama.

## Output utama

- `hasil/Katalog_Promo_Final.xlsx` — workbook katalog dan sheet audit.
- `hasil/Katalog_Promo_Final.csv` — CSV lengkap dengan provenance.
- `hasil/Katalog_Promo_Final_Ringkas.csv` — sesuai dengan sheet `Promo` yang ringkas.
- `hasil/Review_Harga_Poster_OCR.xlsx` — antrean review kandidat dari poster.
- `hasil/Daftar_Cari_Harga_Manual.xlsx` — antrean input harga pasar manual, jalur terpisah.
- `_internal/` — data antara, OCR, audit kualitas, konflik, dan arsip estimasi lama.

Sheet utama Excel menyederhanakan tampilan harian: `Ringkasan`, `Promo`, `Perlu Diperiksa`, `Ringkasan Kategori`, `Peluang Harga`, dan `Kandidat Harga`. Sheet teknis tetap tersedia dalam keadaan tersembunyi untuk audit.

Pada sheet `Promo`, urutan harga adalah `Harga Normal/Acuan (Rp)`, `Harga Promo (Rp)`, `Hemat (Rp)`, `Diskon (%)`, lalu `Masa Berlaku`. Harga normal tidak diisi dari prediksi AI. Jika sumber tidak memberi harga normal dan OCR belum disetujui, diskon resmi akan tetap belum bisa dihitung; itu lebih baik daripada menampilkan tebakan sebagai fakta.

## Pengujian

```cmd
python -m compileall -q engine
python engine\promo_names.py
python engine\promo_multisource_collector.py --self-test
python -m pytest -q engine
```

Upgrade OCR poster v2 menambahkan rescue OCR crop label merah/kuning, filter persentase kemasan, klasifikasi konteks promo, kolom audit pada workbook review, dan invalidasi cache berdasarkan versi pipeline. Seluruh temuan tetap berstatus review; harga hanya aktif melalui `TERIMA` yang lolos importer.
