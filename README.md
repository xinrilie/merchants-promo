# 🛒 Merchant Promo Catalog Engine

**Python Version:** 3.10+ | **Platform:** Windows 10/11

---

Mesin otomasi untuk mengumpulkan, membersihkan, memvalidasi, dan menggabungkan katalog promo retail multi-sumber. Engine mendukung scraping halaman publik, normalisasi nama produk, *cross-source fuzzy matching*, pemeriksaan harga/periode, dan ekspor katalog ke Excel/CSV.

> **Penting:** data Hemat.id, Katalogpromosi.com, dan Scanharga.com merupakan data agregator/konten. Data tidak otomatis dikonfirmasi oleh retailer resmi. Gunakan kolom sumber dan status verifikasi sebelum menjadikan harga sebagai referensi final.

> **Paket build diperbarui (24 Oktober 2026):** klasifikasi kategori dan cache regex diperbarui; validasi sumber independen diperketat; collector menghentikan permintaan setelah rate limit, timeout, server error, atau redirect lintas domain; serta ditambahkan `engine/test_access_limits.py`. Jalankan `Jalankan_Promo.bat --test-only` untuk pemeriksaan offline sebelum scraping live. Tes offline build ini: 44 passed.

---

## 🛰️ Sumber Aktif

`Jalankan_Promo.bat` menjalankan **4 adapter sumber live**: Momsmoney/Kontan (kategori Hemat), Hemat.id, Katalogpromosi.com, dan Scanharga.com. Dua CSV pada `input/` tetap dipakai sebagai baseline historis. Momsmoney menyimpan katalog terakhir yang lolos validasi terpisah dari `promo_catalog_all_merchants_latest_attempt_products.csv`. Jika validasi katalog gagal, hanya kandidat dari percobaan hari ini yang boleh masuk enhancer; CSV katalog lama tidak dipakai sebagai scrape baru. Produk dengan periode estimasi tetap ditandai dan diarahkan ke review, tidak disamarkan sebagai periode pasti.

## ⚙️ Arsitektur Sistem & Alur Kerja

Pipeline memisahkan pengumpulan data dan pemrosesan lintas sumber agar produk dari beberapa situs bisa dibandingkan setelah semua data terkumpul.

1. **Collector — pencari data:** `promo_catalog_all_merchants.py` mengambil artikel Momsmoney/Kontan dari kategori Hemat; setelah itu `promo_multisource_collector.py` mengambil katalog dari Hemat.id, Katalogpromosi.com, dan Scanharga.com; mengekstrak kartu/teks penawaran; menyimpan URL sumber; dan melakukan validasi awal untuk menolak kandidat yang terlihat seperti judul artikel, syarat umum, atau klaim diskon kampanye.
2. **Enhancer — normalisasi & validasi akhir:** membaca hasil collector terbaru yang lolos freshness gate dan CSV katalog baseline, menormalkan nama/harga/tanggal, mengulang validasi non-produk, melakukan deduplikasi persis serta konsolidasi periode yang aman, kemudian menjalankan *fuzzy matching* lintas sumber sebelum ekspor.
3. **Product validation:** modul bersama yang dipakai oleh collector dan enhancer. Teks yang jelas merupakan editorial ditolak; kandidat yang ambigu tetapi mungkin produk diarahkan ke review, bukan dibuang berdasarkan nama.
4. **Merchant discovery:** alias merchant hanya dipakai untuk normalisasi nama yang sudah dikenal. Nama merchant baru juga dicoba diambil dari judul promo, slug URL, dan label singkat pada konteks kartu. Jadi merchant baru seperti `All Fruit`, `All Sayur`, atau `Fresh Basket` tidak perlu masuk daftar manual terlebih dahulu. Judul yang terlalu ambigu tetap perlu ditinjau.
5. **Product name engine:** menyamakan spasi, ejaan, satuan, dan format kemasan. *Fuzzy matching* memberi kandidat kemiripan, tetapi tidak boleh menggabungkan produk jika ukuran/varian atau detail penawaran bertentangan.
6. **Regression tests:** menguji parsing, baris editorial, produk valid, merchant baru yang tidak ada di daftar alias, contoh produk mirip, dan perlindungan terhadap harga/varian yang berbeda.

### Kenapa fuzzy matching dilakukan di enhancer?

Collector memproses satu halaman/sumber pada satu waktu sehingga belum memiliki seluruh kandidat dari situs lain. Enhancer baru membandingkan seluruh hasil collector dan CSV lama dalam satu dataset. Karena itu, *cross-source fuzzy matching* utama dikerjakan di enhancer; collector tetap melakukan normalisasi dan penolakan kandidat buruk lebih awal. Validasi dijalankan dua kali untuk mencegah kandidat artikel lolos hanya karena berasal dari CSV lama.

---


## 🛠️ Guardrail validasi produk

- Baris lama yang kehilangan `campaign_title` kini tetap divalidasi; judul seperti `Promo Paris Baguette BNI Wonderful Weekend` tidak bisa lolos hanya karena kolom judul kosong.
- Fragmen generik satu kata seperti `Akhirnya` ditolak jika satu-satunya bukti penawaran adalah klaim persentase diskon dan tidak ada bukti SKU.
- Baris promo bundling tanpa harga satuan tetap dapat lolos jika syarat seperti `Beli 1 Gratis 1` eksplisit.
- Nama merchant/produk tidak ditolak hanya karena tidak ada di daftar alias. Alias merchant hanya untuk kanonisasi; ekstraksi dinamis memakai struktur judul promo, label konteks, atribut item, harga yang terkait langsung, dan bukti sumber.
- Nama pendek/ambigu yang mungkin merupakan produk atau label merchant tidak di-blacklist; bila bukti item belum cukup, baris diarahkan ke `Perlu Review`.
- Produk bernama jelas seperti `My Baby Body Cologne Sweet Floral 100 ml` tetap lolos validasi discount-only.
- Tes regresi menguji kasus `campaign_title` kosong agar bug tidak kembali.

## ✨ Fitur Utama

- **Penyaringan editorial dua lapis:** menolak baris seperti “Berapa Minimum Transaksi…”, “Memanfaatkan Akhir Pekan…”, “Cek Info Promo…”, atau “Promo Paris Baguette BNI Wonderful Weekend” ketika kalimat tersebut sebenarnya judul/klaim kampanye, bukan SKU.
- **Klaim diskon kampanye dipisahkan:** angka seperti “Diskon 50%” pada judul artikel tidak otomatis dijadikan diskon produk.
- **Cross-source fuzzy matching:** membandingkan kemiripan nama dengan konteks merchant, ukuran/varian, harga, syarat penawaran, dan periode. Nama mirip saja tidak cukup untuk penggabungan otomatis.
- **Parser angka Indonesia:** menangani pemisah ribuan rupiah dan persentase desimal dengan aturan yang berbeda.
- **Validasi periode:** periode estimasi dan konflik tanggal ditandai; baris yang tidak aman masuk review.
- **Audit transparan:** kandidat yang ditolak, kemungkinan duplikat, sumber artikel, dan metrik kualitas disimpan terpisah.
- **Perlindungan data lama:** hasil sebelumnya tidak seharusnya diganti dengan katalog kosong hanya karena satu sumber gagal diakses. Periksa run log untuk status sumber dan fallback.
- **Scanharga berbasis gambar:** URL katalog gambar dicatat untuk pemrosesan lanjutan. Engine tidak mengarang data produk/harga jika informasi gambar belum diekstrak; OCR otomatis penuh belum termasuk.

---

## 📂 Struktur Folder

```text
promo-katalog-engine/
├── README.md
├── .gitignore
├── .github/workflows/ci.yml
├── requirements.txt
├── Install_Dependency.bat
├── Jalankan_Promo.bat
├── Jalankan_Tes.bat
├── input/
│   ├── momskonten_promo_active.csv
│   └── promo_catalog_all_merchants.csv
├── engine/
│   ├── promo_multisource_collector.py
│   ├── enrich_existing_promo_catalog_multisource.py
│   ├── promo_product_validation.py
│   ├── promo_merchant_utils.py
│   ├── promo_names.py
│   ├── promo_catalog_all_merchants.py
│   ├── test_pipeline.py
│   └── test_dynamic_validation.py
├── hasil/       # dibuat otomatis
└── _internal/   # data antara, log, dan audit; dibuat otomatis
```

CSV lama yang berada pada folder `input/` digunakan sebagai data pembanding/seed. Ganti atau perbarui file tersebut bila katalog lama berubah. Jangan mengganti file input dengan file output yang dihasilkan pipeline.

---

## 📦 Hasil Output & Laporan Internal

Setelah menjalankan `Jalankan_Promo.bat`, output utama ditulis ke folder `hasil/`:

- **`hasil/Katalog_Promo_Final.xlsx`** — workbook katalog; berisi sheet Mulai Di Sini, Ringkasan Kategori, Referensi Kategori, Aktif, Periode Pasti, Perlu Review, Ditolak (Audit), Kemungkinan Duplikat, dan Variasi Nama.
- **`hasil/Katalog_Promo_Final.csv`** — katalog ringkas untuk analisis/ingest ke sistem lain.

File teknis tersimpan di `_internal/`:

- **`promo_multisource_products_all.csv`** — kandidat produk dari Hemat.id, Katalogpromosi.com, dan Scanharga.com.
- **`promo_catalog_all_merchants.csv`** — hasil scrape terbaru Momsmoney/Kontan; dipakai hanya jika run log menyatakan scrape berhasil diperbarui pada tanggal berjalan.
- **`promo_catalog_all_merchants_latest_attempt_products.csv`** — kandidat produk periode pasti dan estimasi dari percobaan Momsmoney terbaru. File ini hanya dipakai bila run log berasal dari hari berjalan dan menunjukkan artikel berhasil dibaca; file ini tidak menggantikan katalog terakhir yang lolos validasi.
- **`promo_catalog_all_merchants_review.csv`** — kandidat periode estimasi atau penawaran yang perlu ditinjau dari collector Momsmoney/Kontan.
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
3. Pertama kali, jalankan `Install_Dependency.bat`. Script memeriksa Python 3.10+, memasang runtime dependencies (`requests`, `beautifulsoup4`, `openpyxl`) serta `pytest`, lalu memverifikasi instalasi.
4. Untuk pemeriksaan tanpa akses internet, jalankan `Jalankan_Promo.bat --test-only` atau klik `Jalankan_Tes.bat`. Mode ini memeriksa sintaks, self-test, dan tes regresi tanpa scraping live.
5. Untuk mengambil data terbaru dan membangun katalog, jalankan `Jalankan_Promo.bat`. Script menjalankan tes dahulu, baru menjalankan empat sumber live, validasi, konsolidasi/fuzzy matching, dan ekspor final.
6. Buka `hasil/Katalog_Promo_Final.xlsx` setelah proses selesai. Tutup workbook dan CSV di Excel sebelum menjalankan pipeline lagi agar file keluaran tidak terkunci.

Atau instal dependensi lewat terminal:

```powershell
python -m pip install -r requirements.txt
```

Lalu jalankan pipeline:

```powershell
python engine/promo_names.py
python engine/promo_multisource_collector.py --self-test
python engine/test_pipeline.py
python engine/promo_catalog_all_merchants.py
python engine/promo_multisource_collector.py
python engine/enrich_existing_promo_catalog_multisource.py
```

---

## 🧪 Pengujian regresi

- Baris editorial/kampanye ditolak walaupun `campaign_title` kosong pada CSV lama.
- Kalimat teaser seperti `Kalau Kamu Lagi Cari Promo...` ditolak meski mewarisi promo `Diskon 50%` atau `Beli 1 Gratis 1` dari kartu; produk SKU nyata tetap diuji agar tidak ikut terbuang.
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
- Pemilihan artikel agregator memprioritaskan periode yang terbaca pada judul. Tahun di permalink tidak otomatis dianggap sebagai tanggal konten, sebab beberapa URL lama dapat memiliki isi promo yang diperbarui. Status promo dihitung dari teks periode yang berhasil diekstrak.

---

## 📄 Lisensi & Penggunaan

Gunakan hasil scraping sesuai ketentuan situs sumber, hak atas konten, dan aturan yang berlaku. Jika dipublikasikan di GitHub, hindari mengunggah token, kredensial, atau data pribadi. Periksa ketentuan penggunaan masing-masing sumber sebelum menjalankan scraping rutin.



### Kategori Produk dan Subkategori

- Katalog final memiliki kolom **Kategori Produk** dan **Subkategori** pada sheet Aktif, Periode Pasti, Perlu Review, dan Ditolak (Audit), serta pada CSV katalog ringkas. Keduanya bisa dipakai dengan filter Excel.
- Taksonomi terdiri dari 29 kategori utama, mulai dari makanan/bahan pokok, minuman, perawatan tubuh, cuci pakaian, kebutuhan rumah tangga, restoran, elektronik, voucher/cashback, transportasi, hiburan, hingga layanan. Daftar lengkap dan contoh subkategori ada di sheet **Referensi Kategori** dan modul `engine/promo_categories.py`.
- Sheet **Ringkasan Kategori** menghitung jumlah baris promo aktif per kategori. Angka ini menghitung baris promo, bukan jumlah SKU unik sepanjang waktu.
- Nama produk/SKU diprioritaskan daripada klaim kampanye. Kampanye/rangkuman umum dikelompokkan ke **Promo Umum / Multi-Kategori**; teks yang tidak cukup jelas ditempatkan di **Lainnya / Perlu Review**. Kategori tidak mengubah status aktif, penilaian harga, bukti periode, atau validasi retailer.
- Kategorisasi bersifat otomatis berbasis aturan dan dapat salah pada nama produk yang tidak deskriptif; nilai `Lainnya / Perlu Review` tersedia agar sistem tidak menebak. Tes regresi kategori ikut dijalankan sebelum scraping.

### Bukti dan verifikasi

- Kolom audit `promo_price_evidence_text`, `normal_price_evidence_text`, `discount_evidence_text`, dan `period_evidence_text` menyimpan kutipan teks sumber yang mendukung nilai hasil parsing. Nilai harga yang tidak dapat dicocokkan ke teks penawaran dibersihkan dan diarahkan ke review.
- `Kepastian Periode` terpisah dari `Verifikasi Sumber`. `Periode Pasti` berarti rentang tanggal eksplisit dapat ditelusuri; ini **bukan** bukti bahwa retailer resmi telah mengonfirmasi promo.
- `promo_multisource_runlog.json` menyediakan `source_health` untuk tiap sumber: request sukses/gagal, jumlah baris baru versus fallback, dan coverage bukti harga/diskon/periode. `promo_catalog_multisource_quality_report.json` memiliki `source_freshness` yang membedakan baris fresh, stale (>7 hari), dipertahankan dari run sebelumnya, dan historis/tanpa timestamp. Timestamp yang hilang tidak pernah dihitung sebagai data live baru.
- Momsmoney/Kontan adalah artikel publik sekunder. Jika validasi katalog gagal, hasil percobaan terbaru tetap masuk jalur audit/review tanpa menimpa katalog last-good.
