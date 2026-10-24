# Merchants Promo

Pipeline Python untuk mengumpulkan promo produk dari tiga sumber agregator publik, memvalidasi data, mendeteksi duplikat, mengategorikan produk, lalu mengekspor katalog ke Excel dan CSV.

## Sumber aktif

- **Hemat.id** — katalog/kartu promo.
- **Katalogpromosi.com** — artikel katalog, teks penawaran, dan referensi brosur.
- **Scanharga.com** — artikel promo dan referensi brosur.

Pipeline ini hanya menggunakan tiga sumber tersebut. Data historis yang tidak berasal dari tiga sumber aktif tidak dimasukkan otomatis ke katalog final.

## Menjalankan di Windows

1. Ekstrak seluruh proyek ke satu folder dan buka CMD di folder tersebut.
2. Pastikan Python 3.10 atau lebih baru tersedia di PATH.
3. Pasang dependensi dan jalankan pemeriksaan offline langsung dari CMD:

   ```cmd
   python -m pip install -r requirements-dev.txt
   python -m compileall -q engine
   python engine\promo_names.py
   python engine\promo_multisource_collector.py --self-test
   python -m pytest -q engine
   ```

4. Jika tes lulus, jalankan scraping dan ekspor:

   ```cmd
   python engine\promo_multisource_collector.py
   python engine\enrich_existing_promo_catalog_multisource.py
   ```

5. Buka `hasil/Katalog_Promo_Final.xlsx` atau `hasil/Katalog_Promo_Final.csv`.

Jangan menjalankan enhancer ekspor sebelum collector selesai. Enhancer sengaja berhenti jika file hasil collector tidak ada atau kosong agar katalog final sebelumnya tidak tertimpa oleh ekspor kosong.

## Struktur proyek

```text
merchants-promo/
├── engine/
│   ├── promo_multisource_collector.py
│   ├── enrich_existing_promo_catalog_multisource.py
│   ├── promo_campaign_utils.py
│   ├── promo_categories.py
│   ├── promo_evidence.py
│   ├── promo_merchant_utils.py
│   ├── promo_names.py
│   ├── promo_product_validation.py
│   └── test_*.py
├── input/
│   └── category_review_list.csv
├── data/
│   └── product_price_reference.csv  # referensi harga normal terkini
├── hasil/       # workbook dan CSV final
├── _internal/   # log, data antara, laporan kualitas dan audit
├── Install_Dependency.bat
├── Jalankan_Promo.bat
└── Jalankan_Tes.bat
```

## File utama

- `engine/promo_multisource_collector.py` mengumpulkan data tiga sumber, menyimpan bukti teks untuk harga/diskon/periode, mencatat request log dan status kesehatan sumber, serta mempertahankan baris sumber sebelumnya jika sumber gagal menghasilkan data baru.
- `engine/enrich_existing_promo_catalog_multisource.py` menggabungkan baris antar-sumber, menormalisasi nama, memeriksa periode/harga/diskon, mengidentifikasi merchant, menjalankan quality gate, dan membuat laporan Excel/CSV.
- `engine/promo_campaign_utils.py` berisi utilitas umum untuk normalisasi teks, tipe kampanye, parsing periode, status promo, serta aturan diskon. Modul ini tidak melakukan request web.
- `engine/promo_categories.py` berisi taksonomi 29 kategori produk.
- `engine/promo_product_validation.py` menolak kandidat non-produk dan menandai data ambigu.
- `engine/promo_price_reference.py` menyimpan harga acuan terbaru yang punya bukti eksplisit. Harga baru menggantikan nilai sebelumnya untuk kombinasi produk, merchant, kanal, dan asal rekaman yang sama; database bukan lagi arsip historis harian.

## Hasil dan laporan

Workbook final memiliki sheet `Mulai Di Sini`, `Ringkasan Kategori`, `Referensi Kategori`, `Daftar Filter Produk`, `Aktif`, `Periode Pasti`, `Perlu Review`, `Ditolak (Audit)`, `Kemungkinan Duplikat`, `Variasi Nama`, dan sheet audit kategori apabila perubahan kategori terdeteksi.

Database referensi harga:

- `data/product_price_reference.csv` adalah basis data referensi harga normal terkini. Harga normal eksplisit, valid, dan segar dari hasil scrape memperbarui harga sebelumnya untuk produk + ukuran/varian + merchant + kanal yang sama. Database menyimpan nilai terbaru, bukan riwayat harga harian; observasi dengan waktu sama tetapi harga konflik tetap ditahan agar tidak dipilih secara sembarang.
- Sumber otomatis yang direkam tetap berstatus `aggregator_only`. Harga referensi historis **bukan** bukti bahwa retailer resmi menetapkan harga itu saat ini.
- Sistem lebih dahulu memakai referensi produk + ukuran/varian dari merchant yang sama, dengan kanal yang sama dan usia maksimal 30 hari. Jika referensi terbaru yang layak untuk merchant tersebut tidak tersedia, sistem boleh memakai produk persis dari merchant lain pada kanal yang sama. Kolom `Merchant Acuan Referensi`, `Status Referensi Harga`, dan `Dasar Diskon` menandai fallback tersebut secara eksplisit.
- Jika harga promo saat ini tidak menyertakan harga normal dan diskon, tetapi ada referensi yang cocok serta lebih tinggi dari harga promo, Excel menampilkan `Harga Normal Referensi (Rp)` dan diskon hasil hitung dengan `Dasar Diskon = Hitungan dari referensi harga historis`. Jika acuan berasal dari merchant lain, label secara eksplisit menyebut merchant lain dan nama toko acuan juga ditampilkan. Kolom `Harga Asli dari Sumber (Rp)` tetap kosong supaya tidak terkesan harga itu tertulis pada promo sekarang.
- Harga baru hanya dipelajari dari baris yang lolos validasi produk, mempunyai `Harga Normal` eksplisit dalam bukti teks, sumber URL, timestamp segar, merchant dan produk jelas, serta bukan katalog lama yang dipertahankan. Harga scrape yang lebih baru langsung memperbarui nilai lama sehingga harga naik maupun turun dapat diikuti.
- Database awal sudah disemai dengan observasi eksplisit dari run live terakhir yang tersedia. Saat pindah ke folder proyek baru untuk upgrade mendatang, salin `data/product_price_reference.csv` agar nilai acuan terbaru yang telah terkumpul tetap tersedia.
- Opsional: harga manual dapat ditambahkan ke CSV dengan `record_origin=manual_verified`, `verification_status=retailer_official`, URL halaman retailer, tanggal observasi, harga, serta potongan bukti yang secara eksplisit mencantumkan harga normal. Rekaman manual yang tidak memenuhi syarat ini tidak dipakai.

File teknis di `_internal/` meliputi:

- `promo_multisource_products_all.csv` — hasil collector yang menjadi masukan enhancer.
- `promo_multisource_products_active.csv` — kandidat aktif dari tahap collector.
- `promo_multisource_products_review.csv` — kandidat yang memerlukan pemeriksaan.
- `promo_multisource_articles.csv` — halaman/artikel yang diperiksa.
- `promo_multisource_catalog_images.csv` — referensi gambar katalog; OCR tidak dijalankan otomatis.
- `promo_multisource_rejected_candidates.csv` — kandidat mentah yang ditolak.
- `promo_multisource_runlog.json` — request log dan kesehatan per sumber.
- `promo_catalog_multisource_quality_report.json` — kesegaran data, validasi, duplikasi, dan hasil quality gate ekspor.
- `promo_export_quality_gate_report.json` — ringkasan keputusan gerbang kualitas.
- `promo_possible_duplicate_candidates.csv` — pasangan duplikat yang belum aman digabung otomatis.
- `promo_product_rows_rejected.csv` — baris non-produk yang ditolak enhancer.

## Prinsip validasi

- Status `Aktif` bukan bukti resmi retailer; status periode, status validasi produk, dan status verifikasi sumber dipisahkan.
- Harga promo diambil hanya jika dapat ditelusuri ke teks penawaran yang relevan. Harga normal yang hilang dari promo saat ini tidak diisi ke kolom harga sumber; bila cocok, referensi historis ditampilkan terpisah. Nominal QRIS/cashback, syarat minimum belanja, teks judul editorial, serta nama produk kosong tidak diperlakukan sebagai harga SKU.
- Diskon yang tidak cocok dengan harga sebanding dipindahkan ke review; diskon kampanye tidak otomatis diwariskan ke setiap produk. Diskon yang dihitung dari referensi historis diberi dasar tersendiri serta sumber/tanggal observasi.
- Bundling beli-gratis hanya merupakan estimasi jika nilai barang gratis tidak diketahui. Hadiah lintas-SKU tidak disamakan dengan diskon harga barang utama.
- Periode bulan atau tanggal yang diinferensikan berstatus estimasi, bukan periode pasti.
- Duplikasi persis/fuzzy tidak boleh menyatukan variasi ukuran, rasa, syarat kuantitas, periode, atau harga yang berbeda.
- Baris sumber yang dipertahankan setelah kegagalan ditandai sebagai data lama; timestamp kosong tidak dihitung sebagai kesegaran baru.
- Jika log sumber tidak menyertakan hasil validasi konten secara eksplisit, `validation_passed` ditulis `null` dengan `validation_status=not_reported`, bukan `false`. Status `fresh` atau `updated` hanya menyatakan kondisi pengambilan/output, bukan validasi produk.
- Status Quality Gate `passed` berarti tidak ada isu pemblokir yang tersisa pada baris yang diterima; kandidat bermasalah tetap dialihkan ke `Perlu Review`.
- Judul editorial seperti “Jangan Sampai Kelewatan Promo ...” ditolak sebagai SKU meskipun teksnya berisi nominal harga.
- Prioritas kandidat duplikat memperhitungkan kecocokan penawaran. Tingkat beli berbeda, varian berbeda, atau barang hadiah berbeda diberi instruksi untuk tetap dipisahkan dan tidak digabung otomatis.
- Situs yang membalas 401/403/429 tidak dicoba untuk dilewati.

## Tes lokal

```powershell
python -m pip install -r requirements-dev.txt
python -m compileall -q engine
python engine/promo_names.py
python engine/promo_multisource_collector.py --self-test
python -m pytest -q engine
```

CI juga menjalankan kompilasi dan suite tes regresi.

## Batasan

Situs dapat mengubah struktur, membatasi request, atau mengembalikan data yang tidak lengkap. OCR gambar dan verifikasi langsung otomatis ke situs resmi retailer belum termasuk. Database harga referensi bersifat self-learning dari bukti harga normal yang terlihat pada sumber aktif, jadi tidak akan mengisi semua produk sejak run pertama. Kandidat review dan diskon dari referensi historis tetap perlu dicek bila akan digunakan untuk publikasi atau keputusan pembelian.
