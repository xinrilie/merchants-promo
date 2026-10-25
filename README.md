# Merchants Promo

Pipeline Python untuk mengumpulkan promo produk dari tiga sumber agregator publik, memvalidasi data, mendeteksi duplikat, mengategorikan produk, menyimpan database harga referensi, lalu mengekspor katalog ke Excel dan CSV.

## Sumber aktif

- **Hemat.id** — katalog/kartu promo.
- **Katalogpromosi.com** — artikel katalog, teks penawaran, dan referensi brosur.
- **Scanharga.com** — artikel promo dan referensi brosur.

Pipeline hanya menggunakan tiga sumber tersebut; Momsmoney tidak digunakan sebagai sumber promo.

## Menjalankan di Windows (langsung dari CMD)

1. Ekstrak proyek dan buka CMD di folder utama proyek.
2. Pastikan Python 3.10 atau lebih baru tersedia di PATH.
3. Pasang dependensi dan jalankan pemeriksaan offline:

   ```cmd
   python -m pip install -r requirements-dev.txt
   python -m compileall -q engine
   python engine\promo_names.py
   python engine\promo_multisource_collector.py --self-test
   python -m pytest -q engine
   ```

4. Sebelum scraping, sinkronkan rentang referensi manual dengan database. Jalankan pipeline:

   ```cmd
   python engine\seed_user_price_references.py
   python engine\promo_multisource_collector.py
   python engine\enrich_existing_promo_catalog_multisource.py
   ```

5. Buka `hasil/Katalog_Promo_Final.xlsx` atau `hasil/Katalog_Promo_Final.csv`.

   - `hasil/Katalog_Promo_Final.xlsx` — workbook ringkas untuk pemakaian harian dan sheet audit.
   - `hasil/Katalog_Promo_Final_Ringkas.csv` — kolom dan nilai tampilan sama dengan sheet `Promo` di Excel; cocok untuk dibagikan atau dipakai aplikasi yang membutuhkan tampilan ringkas.
   - `hasil/Katalog_Promo_Final.csv` — CSV yang lebih rinci dan tetap menyimpan kolom provenance/referensi; nilai numerik yang belum tersedia dibiarkan kosong agar tetap mudah diolah sebagai data.

Jangan menjalankan enhancer sebelum collector selesai. Enhancer berhenti jika hasil collector tidak ada atau kosong agar katalog lama tidak tertimpa oleh ekspor kosong.
Jika salah satu URL artikel mengembalikan HTTP 404/410, URL itu dicatat sebagai tidak ditemukan/dihapus dan URL lain tetap diproses. HTTP 401/403/429, error server 5xx, timeout/kegagalan request, atau redirect keluar domain tetap menjadi kondisi penghentian sumber untuk menjaga keamanan dan kualitas data.

## Struktur proyek

```text
merchants-promo-revisi-harga/
├── engine/
│   ├── promo_multisource_collector.py
│   ├── enrich_existing_promo_catalog_multisource.py
│   ├── promo_price_reference.py
│   ├── seed_user_price_references.py
│   └── test_*.py
├── input/
│   └── category_review_list.csv
├── data/
│   ├── product_price_reference.csv
│   ├── user_product_price_ranges.csv
│   └── user_product_price_reference_aliases.csv
├── hasil/       # workbook dan CSV final
└── _internal/   # log, data antara, laporan kualitas dan audit
```

## Tampilan Excel untuk pemakaian sehari-hari

Sheet yang ditampilkan di awal workbook disederhanakan menjadi:

- `Ringkasan` — jumlah promo, jumlah diskon yang bisa dihitung, baris estimasi, serta baris yang perlu diperiksa.
- `Promo` — katalog utama dengan 13 kolom yang ringkas.
- `Perlu Diperiksa` — daftar promo yang membutuhkan pemeriksaan harga, satuan, diskon, atau masa berlaku.
- `Ringkasan Kategori` — jumlah promo per kategori.

Di sheet `Promo`, urutan kolom harga adalah `Harga Normal/Acuan (Rp)` → `Harga Promo (Rp)` → `Hemat (Rp)` → `Diskon (%)` → `Masa Berlaku`. Jadi, `Masa Berlaku` langsung mengikuti `Diskon (%)`.

Sel `Diskon (%)` pada sheet `Promo` selalu diisi angka jika dasar hitungnya memadai, atau teks `Belum bisa dihitung` bila bukti harga/satuan tidak cukup. Sel tidak dibiarkan kosong dan sistem tidak mengarang persentase. Estimasi dari rentang harga pengguna atau bundling diberi penanda di `Status Diskon`. File `Katalog_Promo_Final_Ringkas.csv` menggunakan transformasi baris yang sama agar hasil tampilannya identik dengan sheet tersebut. CSV utama `Katalog_Promo_Final.csv` tetap mempertahankan nilai numerik kosong untuk field yang tidak tersedia dan kolom provenance yang lebih lengkap.

Sheet teknis seperti `Aktif`, `Periode Pasti`, `Perlu Review`, `Referensi Harga`, `Kemungkinan Duplikat`, dan audit lainnya tetap tersedia di workbook dalam keadaan tersembunyi untuk pemeriksaan mendalam; tampilan harian tidak dipenuhi sheet teknis.

## Urutan kolom pada sheet audit

Di sheet audit `Aktif`, `Periode Pasti`, dan `Perlu Review`, kolom harga rinci diawali dengan urutan:

1. `Harga Asli (Rp)`
2. `Harga Promo (Rp)`
3. `Diskon (%)`

Lalu ada `Basis Harga Asli`, `Harga Asli dari Sumber (Rp)`, `Harga Normal Referensi (Rp)`, `Rentang Referensi Harga (Rp)`, dan `Dasar Diskon`.

- `Harga Asli (Rp)` memakai harga asli yang disebut sumber promo jika tersedia. Jika tidak ada, kolom dapat memakai titik tengah dari rentang referensi **hanya jika** identitas/SKU dan satuan cocok serta nilai promo masuk akal terhadap rentang.
- `Harga Asli dari Sumber (Rp)` hanya menandai nominal asli yang memang tercantum atau berhasil diekstrak dari teks detail penawaran; kolom ini tetap kosong untuk nilai estimasi referensi.
- `Harga Normal Referensi (Rp)` berisi titik tengah estimasi. `Rentang Referensi Harga (Rp)` mempertahankan batas minimum–maksimum agar titik tengah tidak dianggap angka pasti.
- `Basis Harga Asli`, `Status Referensi Harga`, `Merchant Acuan Referensi`, dan `Dasar Diskon` menandai apakah nilai berasal dari sumber promo atau hanya perbandingan estimasi.
- Klaim diskon dari sumber yang berbeda cukup jauh dari hitungan harga dipindah ke `Klaim Diskon Sumber (Review) (%)`; nilainya tidak dibuang.
- Penawaran bersyarat seperti beli beberapa item, promo member/pembayaran, bundling, harga di luar rentang, rentang terlalu lebar, atau indikasi beda SKU/satuan tidak dipaksakan menjadi diskon per item.

## Database harga referensi

### File sumber referensi manual

- `data/user_product_price_ranges.csv` berisi **102 rentang harga** yang dikumpulkan pengguna. Harga ini adalah acuan perkiraan dan tidak diklaim sebagai harga pasar yang telah diverifikasi.
- `data/user_product_price_reference_aliases.csv` berisi variasi nama katalog yang telah diaudit agar dapat dicocokkan secara presisi. Perubahan/penghapusan alias tersinkron saat skrip seeder dijalankan.
- `engine/seed_user_price_references.py` memasukkan rentang manual dan alias yang aktif ke `data/product_price_reference.csv`. Skrip ini membangun ulang rekaman `user_supplied_estimate` dari file sumber, sekaligus mempertahankan rekaman hasil pengamatan sumber yang berdiri sendiri.

### Aturan pemilihan dan pembaruan

- **Merchant yang sama diprioritaskan.** Jika tidak ada referensi baru yang layak pada merchant tersebut, sistem dapat menggunakan referensi dari merchant lain atau acuan umum, dan menandainya sebagai fallback.
- Harga normal eksplisit terbaru dari sumber yang memenuhi validasi menggantikan harga sebelumnya untuk kombinasi produk/ukuran/varian + merchant + kanal + asal rekaman yang sama. Harga naik maupun turun akan diterapkan; database bukan arsip harga harian.
- Referensi scrape terbaru yang memenuhi syarat diprioritaskan dibanding rentang manual. Rentang manual hanya menjadi fallback dan titik tengahnya digunakan sebagai estimasi.
- Harga hasil agregator berstatus `aggregator_only`, bukan bukti harga resmi retailer. Rentang yang diberikan pengguna berstatus `user_supplied_estimate`.
- Harga referensi sumber otomatis maksimal berumur 30 hari untuk perhitungan. Harga lama, konflik nominal pada waktu yang sama, transaksi bersyarat, bundling, hadiah lintas-SKU, atau perbedaan ukuran/satuan tidak digunakan untuk menghitung diskon normal per unit.
- Detail penawaran yang secara eksplisit menyebut “harga normal/asli/reguler” dapat dipakai memulihkan harga asli ketika parser awal melewatkannya, kecuali konteksnya mengindikasikan bundling atau syarat transaksi.

## Sheet workbook

Sheet yang terlihat di awal file: `Ringkasan`, `Promo`, `Perlu Diperiksa`, dan `Ringkasan Kategori`. Sheet rinci `Mulai Di Sini`, `Referensi Kategori`, `Daftar Filter Produk`, `Aktif`, `Periode Pasti`, `Perlu Review`, `Ditolak (Audit)`, `Audit Kategori` (jika ada perubahan klasifikasi), `Kemungkinan Duplikat`, `Variasi Nama`, `Referensi Harga`, dan `Rentang Referensi Pengguna` tetap disimpan tetapi disembunyikan agar workbook harian ringkas.

`Referensi Harga` memuat rekaman database beserta rentang, satuan, asal rekaman, sumber, tanggal, bukti, dan URL bila tersedia. `Rentang Referensi Pengguna` menampilkan daftar 102 acuan awal secara terpisah dari pengamatan sumber.

## File teknis utama

- `engine/promo_multisource_collector.py` mengumpulkan data, mencatat bukti harga/diskon/periode, log request, dan status kesehatan sumber.
- `engine/enrich_existing_promo_catalog_multisource.py` menggabungkan data, memvalidasi harga/periode, menjalankan quality gate, mengupdate database referensi, dan menghasilkan Excel/CSV serta audit.
- `engine/promo_price_reference.py` menangani penyimpanan harga terbaru, prioritas merchant, batas umur referensi, pencocokan, dan penandaan asal referensi.
- `engine/seed_user_price_references.py` menyinkronkan 102 rentang manual dan alias katalog ke database sebelum proses.

## Prinsip validasi dan batasan

- Status `Aktif` bukan bukti resmi retailer; status periode, validasi produk, dan verifikasi sumber dipisahkan.
- Harga promo hanya diambil jika dapat ditelusuri ke teks penawaran yang relevan. Nominal QRIS/cashback, cashback pembayaran, syarat minimum belanja, judul editorial, dan nama produk kosong tidak dianggap harga SKU.
- Diskon kampanye tidak otomatis diwariskan ke semua produk; harga referensi hanya alat pembanding dan tetap diberi label estimasi.
- Bundling beli-gratis dan hadiah lintas-SKU tidak disamakan dengan diskon produk tunggal. Periode yang diinferensikan tetap berstatus estimasi.
- Situs dapat mengubah struktur, membatasi request, atau mengembalikan data tidak lengkap. Verifikasi resmi langsung pada retailer dan OCR brosur tidak dijalankan otomatis.

## Pengujian

```cmd
python -m compileall -q engine
python engine\promo_names.py
python engine\promo_multisource_collector.py --self-test
python -m pytest -q engine
```
