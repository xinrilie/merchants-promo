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
- `python -m pytest -q`: **57 passed**.
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
