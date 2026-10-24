# Merchants Promo

Pipeline Python untuk mengumpulkan promo produk dari tiga sumber agregator publik, memvalidasi data, mendeteksi duplikat, mengategorikan produk, lalu mengekspor katalog ke Excel dan CSV.

## Sumber aktif

- **Hemat.id** — katalog/kartu promo.
- **Katalogpromosi.com** — artikel katalog, teks penawaran, dan referensi brosur.
- **Scanharga.com** — artikel promo dan referensi brosur.

Pipeline ini hanya menggunakan tiga sumber tersebut. Data historis yang tidak berasal dari tiga sumber aktif tidak dimasukkan otomatis ke katalog final.

## Menjalankan di Windows

1. Ekstrak seluruh ZIP ke satu folder baru.
2. Pastikan Python 3.10 atau lebih baru tersedia di PATH.
3. Jalankan `Install_Dependency.bat` sekali untuk memasang dependensi.
4. Jalankan `Jalankan_Promo.bat --test-only` untuk pemeriksaan offline.
5. Jalankan `Jalankan_Promo.bat` untuk scraping live, validasi, konsolidasi, dan ekspor.
6. Buka `hasil/Katalog_Promo_Final.xlsx` atau `hasil/Katalog_Promo_Final.csv`.

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

## Hasil dan laporan

Workbook final memiliki sheet `Mulai Di Sini`, `Ringkasan Kategori`, `Referensi Kategori`, `Daftar Filter Produk`, `Aktif`, `Periode Pasti`, `Perlu Review`, `Ditolak (Audit)`, `Kemungkinan Duplikat`, `Variasi Nama`, dan sheet audit kategori apabila perubahan kategori terdeteksi.

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
- Harga hanya dipublikasikan jika dapat ditelusuri ke teks penawaran yang relevan. Nominal QRIS/cashback, syarat minimum belanja, teks judul editorial, serta nama produk kosong tidak diperlakukan sebagai harga SKU.
- Diskon yang tidak cocok dengan harga sebanding dipindahkan ke review; diskon kampanye tidak otomatis diwariskan ke setiap produk.
- Bundling beli-gratis hanya merupakan estimasi jika nilai barang gratis tidak diketahui. Hadiah lintas-SKU tidak disamakan dengan diskon harga barang utama.
- Periode bulan atau tanggal yang diinferensikan berstatus estimasi, bukan periode pasti.
- Duplikasi persis/fuzzy tidak boleh menyatukan variasi ukuran, rasa, syarat kuantitas, periode, atau harga yang berbeda.
- Baris sumber yang dipertahankan setelah kegagalan ditandai sebagai data lama; timestamp kosong tidak dihitung sebagai kesegaran baru.
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

Situs dapat mengubah struktur, membatasi request, atau mengembalikan data yang tidak lengkap. OCR gambar, verifikasi langsung ke situs resmi retailer, dan pemantauan harga secara berkelanjutan belum termasuk. Kandidat review tetap perlu diverifikasi secara manual bila akan digunakan untuk publikasi atau keputusan pembelian.
