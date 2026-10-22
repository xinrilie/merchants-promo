# 🛒 Merchant Promo Catalog Engine (v10)

**Python Version:** 3.10+ | **Platform:** Windows 10/11

---

Mesin otomasi untuk mengumpulkan, membersihkan, memvalidasi, dan menggabungkan katalog promo retail multi-sumber. Engine mendukung scraping halaman publik, normalisasi nama produk, *cross-source fuzzy matching*, pemeriksaan harga/periode, dan ekspor katalog ke Excel/CSV.

> **Penting:** data Hemat.id, Katalogpromosi.com, dan Scanharga.com merupakan data agregator/konten. Data tidak otomatis dikonfirmasi oleh retailer resmi. Gunakan kolom sumber dan status verifikasi sebelum menjadikan harga sebagai referensi final.

---

## 🛰️ Sumber Aktif

`Jalankan_Promo.bat` menjalankan **4 adapter sumber live**: Momsmoney/Kontan (kategori Hemat), Hemat.id, Katalogpromosi.com, dan Scanharga.com. Dua CSV pada `input/` tetap dipakai sebagai baseline historis. CSV Momsmoney terbaru hanya digabungkan jika run log menunjukkan `output_status=updated` dan tanggal eksekusi sama dengan hari ini; CSV lama yang tersisa setelah validasi gagal tidak dihitung sebagai hasil scrape baru.

## ⚙️ Arsitektur Sistem & Alur Kerja

Pipeline memisahkan pengumpulan data dan pemrosesan lintas sumber agar produk dari beberapa situs bisa dibandingkan setelah semua data terkumpul.

1. **Collector — pencari data:** `promo_catalog_all_merchants_v2.py` mengambil artikel Momsmoney/Kontan dari kategori Hemat; setelah itu `promo_multisource_collector.py` mengambil katalog dari Hemat.id, Katalogpromosi.com, dan Scanharga.com; mengekstrak kartu/teks penawaran; menyimpan URL sumber; dan melakukan validasi awal untuk menolak kandidat yang terlihat seperti judul artikel, syarat umum, atau klaim diskon kampanye.
2. **Enhancer — normalisasi & validasi akhir:** membaca hasil collector dan CSV katalog lama, menormalkan nama/harga/tanggal, mengulang validasi non-produk, melakukan deduplikasi persis serta konsolidasi periode yang aman, kemudian menjalankan *fuzzy matching* lintas sumber sebelum ekspor.
3. **Product validation:** modul bersama yang dipakai oleh collector dan enhancer. Ini adalah lapisan pertahanan ganda agar judul artikel tidak masuk kembali ke katalog dari sumber lama atau hasil scraping sebelumnya.
4. **Product name engine:** menyamakan spasi, ejaan, satuan, dan format kemasan. *Fuzzy matching* memberi kandidat kemiripan, tetapi tidak boleh menggabungkan produk jika ukuran/varian atau detail penawaran bertentangan.
5. **Regression tests:** menguji parsing, baris editorial, produk valid, contoh produk mirip, dan perlindungan terhadap harga/varian yang berbeda.

### Kenapa fuzzy matching dilakukan di enhancer?

Collector memproses satu halaman/sumber pada satu waktu sehingga belum memiliki seluruh kandidat dari situs lain. Enhancer baru membandingkan seluruh hasil collector dan CSV lama dalam satu dataset. Karena itu, *cross-source fuzzy matching* utama dikerjakan di enhancer; collector tetap melakukan normalisasi dan penolakan kandidat buruk lebih awal. Validasi dijalankan dua kali untuk mencegah kandidat artikel lolos hanya karena berasal dari CSV lama.

---


## 🛠️ Perbaikan v10 — Validasi klaim diskon tanpa konteks judul

- Baris lama yang kehilangan `campaign_title` kini tetap divalidasi; judul seperti `Promo Paris Baguette BNI Wonderful Weekend` tidak bisa lolos hanya karena kolom judul kosong.
- Fragmen generik satu kata seperti `Akhirnya` ditolak jika satu-satunya bukti penawaran adalah klaim persentase diskon dan tidak ada bukti SKU.
- Baris promo bundling tanpa harga satuan tetap dapat lolos jika syarat seperti `Beli 1 Gratis 1` eksplisit.
- Produk bernama jelas seperti `My Baby Body Cologne Sweet Floral 100 ml` tetap lolos validasi discount-only.
- Regression tests v10 menguji kasus `campaign_title` kosong agar bug tidak kembali.

## ✨ Fitur Utama (v10)

- **Penyaringan editorial dua lapis:** menolak baris seperti “Berapa Minimum Transaksi…”, “Memanfaatkan Akhir Pekan…”, “Cek Info Promo…”, atau “Promo Paris Baguette BNI Wonderful Weekend” ketika kalimat tersebut sebenarnya judul/klaim kampanye, bukan SKU.
- **Klaim diskon kampanye dipisahkan:** angka seperti “Diskon 50%” pada judul artikel tidak otomatis dijadikan diskon produk.
- **Cross-source fuzzy matching:** membandingkan kemiripan nama dengan konteks merchant, ukuran/varian, harga, syarat penawaran, dan periode. Nama mirip saja tidak cukup untuk penggabungan otomatis.
- **Parser angka Indonesia:** menangani pemisah ribuan rupiah dan persentase desimal dengan aturan yang berbeda.
- **Validasi periode:** periode estimasi dan konflik tanggal ditandai; baris yang tidak aman masuk review.
- **Audit transparan:** kandidat yang ditolak, kemungkinan duplikat, sumber artikel, dan metrik kualitas disimpan terpisah.
- **Perlindungan data lama:** hasil sebelumnya tidak seharusnya diganti dengan katalog kosong hanya karena satu sumber gagal diakses. Periksa run log untuk status sumber dan fallback.
- **Scanharga berbasis gambar:** URL katalog gambar dicatat untuk pemrosesan lanjutan. Versi ini tidak mengarang data produk/harga jika informasi gambar belum diekstrak; OCR otomatis penuh belum termasuk.

---

## 📂 Struktur Folder

```text
promo-katalog-engine/
├── README.md
├── requirements.txt
├── Install_Dependency.bat
├── Jalankan_Promo.bat
├── input/
│   ├── momskonten_promo_active.csv
│   └── promo_catalog_all_merchants.csv
├── engine/
│   ├── promo_multisource_collector.py
│   ├── enrich_existing_promo_catalog_multisource.py
│   ├── promo_product_validation.py
│   ├── promo_names.py
│   ├── promo_catalog_all_merchants_v2.py
│   └── test_pipeline_v10.py
├── hasil/       # dibuat otomatis
└── _internal/   # data antara, log, dan audit; dibuat otomatis
```

CSV lama yang berada pada folder `input/` digunakan sebagai data pembanding/seed. Ganti atau perbarui file tersebut bila katalog lama berubah. Jangan mengganti file input dengan file output yang dihasilkan pipeline.

---

## 📦 Hasil Output & Laporan Internal

Setelah menjalankan `Jalankan_Promo.bat`, output utama ditulis ke folder `hasil/`:

- **`hasil/Katalog_Promo_Final.xlsx`** — workbook katalog; berisi sheet Aktif, Terkonfirmasi, Perlu Review, Ditolak (Audit), Variasi Nama, dan Kemungkinan Duplikat.
- **`hasil/Katalog_Promo_Final.csv`** — katalog ringkas untuk analisis/ingest ke sistem lain.

File teknis tersimpan di `_internal/`:

- **`promo_multisource_products_all.csv`** — kandidat produk dari Hemat.id, Katalogpromosi.com, dan Scanharga.com.
- **`promo_catalog_all_merchants_v2.csv`** — hasil scrape terbaru Momsmoney/Kontan; dipakai hanya jika run log menyatakan scrape berhasil diperbarui pada tanggal berjalan.
- **`promo_multisource_articles.csv`** — log artikel/halaman yang diperiksa.
- **`promo_multisource_catalog_images.csv`** — URL gambar katalog yang ditemukan.
- **`promo_multisource_rejected_candidates.csv`** — kandidat yang ditolak oleh collector.
- **`promo_product_rows_rejected.csv`** — baris dari CSV lama/hasil collector yang ditolak oleh enhancer.
- **`promo_possible_duplicate_candidates.csv`** — nama yang mirip tetapi belum aman digabung.
- **`promo_multisource_runlog.json`** dan **`promo_multisource_quality_report.json`** — ringkasan status sumber, jumlah baris, alasan penolakan, duplikasi, dan kualitas.

File `_internal/` digunakan untuk audit dan diagnosis. Untuk pemakaian sehari-hari, buka workbook atau CSV dari `hasil/`.

---

## 🚀 Cara Menjalankan di Windows

1. Ekstrak seluruh ZIP ke folder baru.
2. Pastikan Python **3.10 atau lebih baru** sudah terpasang dan opsi *Add Python to PATH* aktif.
3. Pertama kali, jalankan `Install_Dependency.bat`. Dependensi yang diperlukan adalah `requests`, `beautifulsoup4`, dan `openpyxl`.
4. Jalankan `Jalankan_Promo.bat`. Script melakukan self-test, uji regresi, collector, enhancer/fuzzy matching, lalu ekspor final.
5. Buka `hasil/Katalog_Promo_Final.xlsx`.

Atau instal dependensi lewat terminal:

```powershell
python -m pip install -r requirements.txt
```

Lalu jalankan pipeline:

```powershell
python engine/promo_names.py
python engine/promo_multisource_collector.py --self-test
python engine/test_pipeline_v10.py
python engine/promo_catalog_all_merchants_v2.py
python engine/promo_multisource_collector.py
python engine/enrich_existing_promo_catalog_multisource.py
```

---

## 🧪 Pengujian regresi v10

- Baris editorial/kampanye ditolak walaupun `campaign_title` kosong pada CSV lama.
- Fragmen satu kata seperti `Akhirnya` tidak diterima sebagai SKU hanya karena disertai klaim diskon.
- Produk nyata dengan atribut SKU (misalnya ukuran) dan penawaran bundling eksplisit tetap dipertahankan.
- Fuzzy matching tetap menjaga varian, harga, dan syarat promo yang bertentangan tetap terpisah.

---

## ⚠️ Batasan & Akurasi

- Scraping memerlukan koneksi internet; situs dapat berubah struktur, membatasi request, atau tidak tersedia.
- Status “aktif” menunjukkan hasil perhitungan periode yang ditemukan, bukan konfirmasi langsung dari retailer.
- Harga dan diskon dari agregator perlu verifikasi terhadap syarat, ukuran, wilayah, membership, kanal pembelian, dan tanggal berlaku.
- *Fuzzy matching* adalah alat bantu deteksi, bukan jaminan bahwa dua nama mengacu pada SKU yang sama. Kandidat ambigu harus diperiksa.
- Pipeline menghormati status blokir/limit situs dan tidak mencoba melewati CAPTCHA atau proteksi akses.

---

## 📄 Lisensi & Penggunaan

Gunakan hasil scraping sesuai ketentuan situs sumber, hak atas konten, dan aturan yang berlaku. Jika dipublikasikan di GitHub, hindari mengunggah token, kredensial, atau data pribadi. Periksa ketentuan penggunaan masing-masing sumber sebelum menjalankan scraping rutin.
