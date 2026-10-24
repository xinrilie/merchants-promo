# Ringkasan Audit dan Refactor

## Keputusan arsitektur

Pipeline produksi sekarang hanya memakai Hemat.id, Katalogpromosi.com, dan Scanharga.com. Integrasi sumber artikel berita umum dan seluruh jalur katalog historis khusus sumber tersebut telah dikeluarkan dari paket.

## Perubahan utama

- Menghapus adapter scraper sumber berita yang tidak fokus pada katalog produk.
- Menghapus CSV input historis khusus sumber tersebut agar tidak masuk kembali ke katalog melalui jalur baseline.
- Memindahkan utilitas parsing tanggal, kampanye, dan diskon yang sebelumnya tertanam di adapter sumber ke `promo_campaign_utils.py` supaya enhancer tetap mandiri dan tidak bergantung pada scraper yang sudah dihapus.
- Menghapus logic pemilihan CSV percobaan/last-good yang khusus untuk sumber berita, beserta metadata run-nya.
- Mengganti metadata operasional sumber menjadi pembacaan `promo_multisource_runlog.json`, termasuk status segar, sebagian mempertahankan data sebelumnya, tidak mendapat produk, atau request gagal.
- Enhancer hanya membaca hasil collector tiga sumber. Jika file tidak tersedia atau kosong, ekspor dibatalkan agar katalog sebelumnya tidak tergantikan oleh output kosong.
- Menambah alias merchant Papaya Fresh Gallery untuk mendukung resolusi dari judul/URL.
- Menghapus tes khusus adapter yang tidak lagi ada, mempertahankan regresi generik untuk akses, validasi, kategori, bukti harga/periode, hadiah lintas-SKU, dan deduplikasi.
- Mengganti nama file tes yang memakai akhiran versi agar nama berorientasi fungsi.
- Menulis ulang README agar instruksi, struktur file, dan daftar sumber sesuai implementasi.

## File yang dihapus

- `engine/promo_catalog_all_merchants.py`
- `input/momskonten_promo_active.csv`
- `input/promo_catalog_all_merchants.csv`

Kedua CSV input tersebut memuat baris dengan URL sumber berita yang dikeluarkan; menghapusnya mencegah data lama terimpor otomatis.

## Data yang tetap dipertahankan

- `input/category_review_list.csv`, karena digunakan untuk meregenerasi sheet referensi/review klasifikasi.
- Modul validasi, kategori, bukti, merchant, normalisasi nama, collector tiga sumber, enhancer ekspor, dan tes regresi yang masih berkontribusi terhadap pipeline aktif.
- `_internal/` dan `hasil/` hanya berisi `.gitkeep` di paket bersih; log/data runtime tidak disertakan agar tidak tertukar dengan hasil scraping terbaru.

## Validasi

Hasil kompilasi, tes, ekspor lokal, dan struktur arsip dicatat setelah paket selesai dibangun. Scraping live tidak dijalankan saat audit/refactor ini; kesehatan situs harus diuji dengan menjalankan launcher di mesin pengguna.
