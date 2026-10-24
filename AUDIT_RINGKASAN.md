# Hasil Audit Kode — Merchant Promo Catalog Engine

Tanggal audit: 24 Oktober 2026  
Basis audit: paket ZIP yang diunggah pengguna. Pemeriksaan ini bersifat offline; **tidak ada scraping live yang dijalankan saat audit akhir**.

## Hasil yang dipertahankan

- Tiga collector tetap dipertahankan untuk Hemat.id, Katalogpromosi.com, dan Scanharga.com; adapter Momsmoney/Kontan tetap menjadi sumber sekunder.
- CSV input historis, cache `_internal/`, dan workbook hasil lama tidak dihapus karena berisi baseline dan data *last-good* yang dapat dipakai saat sumber dibatasi. Menghapusnya akan menghilangkan kemampuan fallback dan konteks audit.
- Validasi produk, bukti harga/periode, pemeriksaan akses sumber, deduplikasi konservatif, serta sheet kualitas dan duplikat tetap dipertahankan.

## Perbaikan kode

1. **Redirect listing Momsmoney/Kontan:** respons yang berakhir di domain lain sekarang ditolak sebelum tautan diproses.
2. **Redirect artikel Momsmoney/Kontan:** redirect keluar domain ditandai sebagai `redirected_offsite`; loop artikel berhenti dan review lama tidak ditimpa hasil parsial.
3. **Timeout/kegagalan request artikel:** `requests.RequestException` sekarang dicatat sebagai `request_error`, bukan `error` generik. Status redirect, URL sumber tidak valid, request gagal, dan error tak terduga menghentikan loop serta mempertahankan output review sebelumnya.
4. **Kategorisasi:** taksonomi dikembalikan menjadi **29 kategori utama**, memisahkan `Snack & Makanan Kemasan` dari `Restoran & Makanan Siap Saji`. Produk yang tidak jelas tetap masuk `Lainnya / Perlu Review`; rangkuman kampanye tetap bisa masuk `Promo Umum / Multi-Kategori`.
5. **Reproduksibilitas sheet Excel:** `Daftar Filter Produk` kini dibuat dari `input/category_review_list.csv`, sedangkan `Audit Kategori` dibangun ulang dari kategori sumber yang tersimpan. Sheet tersebut tidak lagi bergantung pada workbook yang diedit manual, yang sebelumnya akan hilang saat ekspor Excel berikutnya.
6. **Tes duplikasi:** pemeriksaan deduplikasi lintas sumber di `test_duplicate_fixes.py` kini dikumpulkan oleh `pytest`; pemanggilan terpisah dari BAT dan CI dihapus agar tidak menjalankan tes yang sama dua kali.
7. **Kode mati/import tidak terpakai:** menghapus import `product_name_identity_warnings`, `date` dari `promo_evidence.py`, dan `parse_qs` dari collector, serta fungsi internal yang tidak dipanggil `_row_source_values` dan `_dynamic_merchant_candidate_from_title`.
8. **Daftar contoh kategori:** diekstrak dari sheet lama ke `input/category_review_list.csv`. Satu entri `Min. Belanja` yang duplikat dihapus; tersisa **126 nama unik**. Ini membuat worksheet QA dapat diproduksi ulang.

## Validasi yang dijalankan

- `python -m compileall -q engine`: lulus.
- `python -m pytest -q`: **66 passed** (sesudah gerbang kualitas ekspor ditambahkan).
- `python engine/promo_names.py`: self-test lulus.
- `python engine/promo_multisource_collector.py --self-test`: self-test lulus.
- `python engine/test_duplicate_fixes.py`: self-test lulus (opsional untuk menjalankan skrip langsung; tesnya juga sudah dikumpulkan oleh pytest).
- Workbook kategori dites untuk 29 kategori, sheet audit, sheet daftar filter, serta perubahan kategori contoh.
- ZIP arsip akan diperiksa kembali setelah packaging.

## Batasan dan catatan data

- Klasifikasi otomatis masih berbasis aturan, bukan model semantik penuh. Nama pendek/ambigu tetap harus diperiksa; tidak semua label yang berubah berarti label lama pasti salah.
- Sheet kategori dan workbook final akan berisi data dari CSV/cache lokal yang tersedia. Itu **bukan klaim bahwa sumber promo live berhasil diperbarui pada tanggal audit**.
- Source health dan label `fresh` menjelaskan status ekstraksi internal, bukan konfirmasi harga dari retailer resmi.
- File cache yang termasuk dalam paket dipertahankan secara sengaja untuk menjaga fallback *last-good*. Untuk penggunaan rutin, jangan menghapus `_internal/` sebelum mempunyai cadangan.

## Cara verifikasi lokal

Dari root proyek Windows:

```powershell
python -m compileall -q engine
python -m pytest -q
python engine/enrich_existing_promo_catalog_multisource.py
```

Untuk menjalankan scraping live dan ekspor lengkap, gunakan `Jalankan_Promo.bat`. Jika ada HTTP 401/403/429, timeout, server error, atau redirect keluar domain, pipeline harus berhenti dengan aman dan mempertahankan data *last-good* yang sesuai.


## Tindak lanjut dari sampel workbook pengguna

Audit lanjutan menemukan beberapa kebocoran kandidat non-produk yang belum tercakup pada pemeriksaan sebelumnya. Temuan dan perbaikannya:

1. `Gimana Mantapp Kan Promo Alfamart ...` dapat lolos karena frasa `Gimana` belum dikenali sebagai awalan editorial. Awalan tersebut kini ditambahkan ke validator dan ada tes regresi dengan nominal harga yang ikut terbaca.
2. Baris persis `Min. Belanja`/`Minimum Pembelanjaan`/`Minimum Transaksi`/`Minimal Pembelian` sebelumnya bisa masuk ke tab aktif dengan nominal syarat transaksi pada kolom harga promo. Baris tersebut kini ditolak dari katalog produk dan tetap dapat ditelusuri di `Ditolak (Audit)`.
3. `Extra` dengan detail `Rp 2.000 Dengan QRIS BCA myBCA & BCA mobile` sebelumnya diperlakukan sebagai produk berharga Rp2.000. Nama pendek yang terikat ke konteks pembayaran kini diarahkan ke review; nilai yang tidak terbukti sebagai harga produk dikosongkan dari kolom harga/diskon, sementara bukti teks tetap disimpan di detail penawaran.
4. `Pasti Hemat 1000 sheet Facial Tissue ...` pernah dipotong menjadi `Pasti` karena kata `Hemat 1000` dikira label penghematan. Aturan normalisasi sekarang mengenali kuantitas `sheet`, dan nama asal dengan atribut SKU yang eksplisit dapat dipulihkan jika nama normalisasi terlalu pendek. Pada hasil ekspor yang dibuat ulang, produk ini tampil sebagai `Pasti Hemat Facial Tissue 1000 sheet` dalam kategori `Tisu & Produk Kertas`.

Verifikasi sesudah perbaikan: `compileall`, self-test nama, self-test parser/guardrail, dan **57 tes regresi lulus**. Excel dibangun ulang dari input dan cache lokal yang ada. Tidak ada scraping live dijalankan, jadi ini membuktikan perbaikan jalur validasi/ekspor pada data lokal dan tes, bukan pembaruan sumber live.


## Tindak lanjut: nama produk kosong setelah normalisasi

Pemeriksaan hasil Excel menemukan dua baris yang tampil tanpa nama produk meskipun berstatus aktif: teks sumber `Potongan harga` (Alfamart) dan `Harga spesial` (Foodhall). `clean_product_name()` menghapus kedua frasa generik tersebut, tetapi validasi sebelumnya tetap menganggap row eligible karena nominal ada di teks penawaran.

Perbaikan:

1. Baris yang nama produknya menjadi kosong setelah normalisasi dipaksa ke `product_validation_status=review`, sehingga tidak masuk CSV/sheet `Aktif` atau `Periode Pasti`.
2. Harga promo, harga normal, diskon produk, dan bukti harga terkait dikosongkan karena nominal pada baris generik itu tidak dapat dipastikan sebagai harga SKU tertentu. Teks penawaran, kampanye, merchant, URL, dan nama asal tetap disimpan untuk review/audit.
3. Di sheet `Perlu Review`, nama kosong ditampilkan sebagai `Produk belum teridentifikasi` agar baris dapat dibaca; full audit tetap mempertahankan nilai hasil normalisasi yang kosong beserta `original_product_text`.
4. Tes regresi baru menguji kedua pola (`Potongan harga` dan `Harga spesial`), pastikan status review, nominal produk dikosongkan, dan label display tidak blank.

Hasil build lokal setelah perbaikan: tidak ada nama produk kosong pada CSV utama, sheet `Aktif`, atau sheet `Periode Pasti`; dua kasus yang ditemukan hanya tampil pada `Perlu Review` dengan harga kosong. Validasi: compileall dan self-test lulus; **57 tes lulus**. Tidak ada scraping live yang dijalankan.


## Fase 1: gerbang kualitas data sebelum ekspor

Diterapkan pada build 24 Oktober 2026, di atas paket false-positive fix.

1. Menambahkan kolom **`Status Validasi Produk`** ke CSV dan seluruh sheet katalog. Kolom ini terpisah dari `Status Promo`; promo yang masih aktif tanggalnya tetap dapat ditandai `Perlu review` jika identitas/merchant/harga produk belum aman.
2. Menambahkan `apply_export_quality_gate()` sebelum output final ditulis. Baris aktif dicek atas identitas produk kosong/generik, merchant tidak diketahui, kategori yang masih `Lainnya / Perlu Review`, URL sumber yang hilang, harga tanpa bukti yang cocok, nominal pembayaran/QRIS yang keliru dianggap harga produk, harga inferred/konflik unit, konflik harga lintas sumber, dan diskon di luar rentang 0–100%.
3. Baris yang gagal tidak dibuang diam-diam: statusnya menjadi `review`, alasan dan flag disimpan, sementara nominal yang tidak dapat dipastikan sebagai harga produk dikosongkan dari kolom harga/diskon terkait. Bukti teks, judul, merchant, dan URL tetap ada untuk audit.
4. Gerbang melakukan pemeriksaan kedua terhadap semua baris yang akan dipublikasikan. Bila masih ditemukan blocker, enhancer menulis `_internal/promo_export_quality_gate_report.json` dan berhenti sebelum menimpa katalog final sebelumnya.
5. Workbook `Mulai Di Sini` menjelaskan perbedaan `Status Promo` dan `Status Validasi Produk`, serta menampilkan ringkasan hasil gate.
6. Tes regresi baru menguji pemisahan status, merchant tidak diketahui, kategori belum terklasifikasi, nominal QRIS/cashback, nama generik, harga inferred, konflik harga lintas sumber, diskon di atas 100%, serta produk valid yang tetap lolos.

### Hasil build lokal setelah penerapan gate

- Kandidat aktif sebelum gate: **970**.
- Diterima di katalog aktif setelah gate: **935**.
- Dialihkan ke `Perlu Review`: **35** (26 merchant tidak diketahui, 9 kategori perlu pemeriksaan; sebagian baris memiliki lebih dari satu flag terkait harga).
- Katalog aktif: tidak ada nama kosong dan tidak ada merchant kosong/`UNKNOWN`.
- Kolom `Status Validasi Produk` pada katalog aktif: seluruh baris berstatus `Valid`.
- Excel mempunyai sheet `Aktif`, `Periode Pasti`, `Perlu Review`, `Ditolak (Audit)`, `Audit Kategori`, `Kemungkinan Duplikat`, `Variasi Nama`, serta ringkasan dan referensi kategori.
- Verifikasi: `compileall`, semua self-test, **66/66 tes lulus**, ekspor Excel/CSV lulus.

**Batas verifikasi:** build Excel memakai CSV/cache lokal. Log Momsmoney/Kontan yang tersedia bertanggal 23 Oktober 2026 sehingga tidak dipakai sebagai scrape segar pada build 24 Oktober. Tidak ada scraping live yang dijalankan saat tahap audit ini.


## Quality Gate v2 — 24 Oktober 2026

Perubahan lanjutan setelah audit log run live pengguna:

1. **Kesegaran sumber tidak lagi diringkas secara keliru menjadi `fresh`.** Laporan sekarang menghitung kesegaran per baris dan dapat menyatakan campuran baris segar, hasil katalog sebelumnya yang dipertahankan, baris usang, timestamp tidak valid/masa depan, serta data historis/tanpa timestamp. Timestamp pada satu record multi-sumber tidak otomatis diberikan ke semua sumber dalam provenance.
2. **Status run Momsmoney/Kontan ditampilkan terpisah dari kesegaran row-level.** Laporan menyertakan `output_status`, tanggal run, jumlah produk percobaan terbaru, validasi, dan keterangan bila `preserved_previous_catalog`. Status tersebut berarti katalog stabil tidak diganti oleh percobaan terbaru; tidak berarti seluruh dataset lama telah di-scrape ulang.
3. **Diskon tertulis dibandingkan terhadap harga yang sebanding.** Jika `normal_price` dan `promo_price` merupakan harga penawaran yang mempunyai konteks SKU dan tidak memiliki flag satuan ambigu, klaim diskon eksplisit yang berbeda lebih dari 3 poin persentase dari hasil hitung dipindahkan ke review. Klaim `hingga` ditandai hanya saat hasil hitungan melampaui batas maksimum yang disebutkan.
4. **Nilai tidak dihapus dari jejak audit.** Di baris mismatch, diskon display dikosongkan agar tidak dianggap valid; klaim sumber dipertahankan pada `Klaim Diskon Sumber (Review) (%)`, hasil hitung tetap tersedia untuk audit, dan catatan mismatch ditampilkan. Baris yang sudah berada di review juga dibersihkan dari diskon display yang bertentangan.
5. **Penawaran kuantitas berbeda dipisahkan.** Fuzzy merge lintas sumber tidak menggabungkan tawaran dengan kuantitas pembelian eksplisit berbeda, seperti beli 5 vs beli 10, meski nama produk sama.

### Verifikasi Quality Gate v2

- `python -m compileall -q engine`: lulus.
- `python -m pytest -q engine`: **72 passed**.
- `python engine/promo_names.py`: self-test lulus.
- `python engine/promo_multisource_collector.py --self-test`: self-test lulus.
- `python engine/test_duplicate_fixes.py`: self-test lulus.
- Uji ekspor lokal menghasilkan kolom `Kesegaran Data`, `Waktu Scrape`, dan `Klaim Diskon Sumber (Review) (%)`.

**Batas verifikasi:** tidak ada scraping live setelah perubahan ini. Pengujian menggunakan kode, tes, dan cache lokal; data live perlu divalidasi setelah pengguna menjalankan paket Quality Gate v2 di Windows. Output runtime lama tidak disertakan dalam ZIP final agar tidak disalahartikan sebagai data live baru.


## Quality Gate v3 — merchant, hadiah lintas-SKU, dan review duplikat

Perbaikan diterapkan setelah audit `diagnostic-v2.zip` pengguna.

1. **Merchant generik dibereskan secara konservatif.** Nilai `Lainnya`/`UNKNOWN`/kosong tidak lagi disimpan sebagai merchant seakan-akan sudah teridentifikasi. Enhancer memeriksa alias merchant yang jelas di judul, slug URL, dan judul sumber terkait. Jika satu merchant konsisten disebut, ia direkonstruksi dan `merchant_resolution_method` mencatat metode. Jika konteks menyebut lebih dari satu merchant yang tidak dapat diselesaikan, merchant tetap kosong agar quality gate mengarahkan row ke review. Inferensi nama merchant baru dari judul promo tetap didukung.
2. **Hadiah lintas-SKU tidak disajikan sebagai diskon produk.** Jika penawaran mengandung nama barang hadiah yang token SKU-nya berbeda material dari nama produk pada row, estimasi matematika `Beli X Gratis Y` ditandai `cross_sku_bundle_review`; kolom diskon produk dan estimasi bundle dikosongkan, bukti penawaran tetap dipertahankan, dan row diarahkan ke review. Mekanisme generik `Beli 1 Gratis 1` tanpa nama SKU hadiah eksplisit masih dapat tercatat sebagai estimasi berkeyakinan rendah. Klaim diskon eksplisit yang berdiri sendiri tidak otomatis dihapus hanya karena hadiah juga disebut.
3. **Deduplikasi melindungi identitas hadiah.** Jika satu sumber menyebut hadiah SKU berbeda dari sumber lain, atau hanya salah satu sumber menjelaskan SKU hadiah, penawaran tidak di-merge secara otomatis.
4. **Antrean kandidat duplikat mendapat prioritas.** Kolom `Prioritas Review` ditambahkan ke sheet `Kemungkinan Duplikat` dan CSV kandidat: `Tinggi` untuk kemiripan nama ≥93%, `Sedang` untuk ≥88% dan <93%, dan `Rendah` untuk nilai di bawah 88% sampai ambang kandidat 84%. Urutan tidak mengubah data maupun melakukan merge otomatis.
5. **Laporan quality gate diperluas** untuk mencatat jumlah per metode resolusi merchant, hitungan hadiah lintas-SKU pada review, dan distribusi prioritas kandidat duplikat.

### Verifikasi v3

- `python -m compileall -q engine`: lulus.
- `python -m pytest -q engine`: **79 passed**.
- Self-test normalisasi nama, parser/guardrails, dan deduplikasi: lulus.
- Ekspor lokal sudah dijalankan dari kode v3: workbook valid dengan 11 sheet; quality gate `passed`; CSV tidak memiliki nama produk kosong atau merchant kosong/`UNKNOWN` dalam katalog aktif.

**Bukti pada data lokal:** baris `Duo Bakso A/B` yang sebelumnya bermerchant `Lainnya` sekarang ter-resolve menjadi `Es Teler 77` dari judul dan URL yang konsisten. Skenario hadiah silang `Indomaret Facial Tissue Non Perfumed 400 g` vs `INDOMARET Travel Pack Tissue 50’s` ditambahkan sebagai regresi dan estimasi diskon bundle tidak lagi diterbitkan sebagai diskon SKU utama. Pada baseline lokal yang diuji, tidak terdapat baris live tersebut yang tersimpan sebagai data terbaru sehingga hitungan baris hadiah lintas-SKU pada report baseline tetap 0; kasusnya diverifikasi melalui tes khusus.

**Batas verifikasi:** perubahan ini belum diuji dengan scraping live baru; pengujian memakai regresi dan data lokal. Merchant yang tidak dapat ditentukan secara eksplisit tetap perlu review—tidak ada allow-list baru yang menolak merchant baru.
