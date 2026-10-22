KATALOG PROMO MULTI-SUMBER v7
============================

TUJUAN
Mengumpulkan data promo dari Hemat.id, Katalogpromosi.com, dan Scanharga.com,
memadukannya dengan dua CSV katalog lama, membersihkan data, mendeteksi produk
bukan-SKU seperti judul artikel, melakukan fuzzy matching konservatif, lalu
membuat katalog final Excel/CSV.

CARA MENJALANKAN
1. Ekstrak seluruh ZIP ke satu folder baru.
2. Pertama kali, jalankan Install_Dependency.bat (atau langsung Jalankan_Promo.bat,
   yang akan mengecek dependensi dan memasangnya bila belum ada).
3. Jalankan Jalankan_Promo.bat.
4. Buka hasil\Katalog_Promo_Final.xlsx. Untuk penggunaan sehari-hari, sheet Aktif
   adalah katalog utama; sheet Perlu Review dan Kemungkinan Duplikat untuk audit.

APA BEDANYA COLLECTOR DAN ENHANCER?
- engine\promo_multisource_collector.py: mengakses halaman sumber dan mengekstrak
  kandidat produk. Ia memakai validasi awal agar paragraf/judul artikel seperti
  "Apa Benefit ... Diskon 50%" tidak dianggap sebagai SKU. Ia mengekspor data
  scraping mentah/terstruktur ke _internal\promo_multisource_products_all.csv.
- engine\enrich_existing_promo_catalog_multisource.py: membaca CSV lama dan hasil
  collector. Di sinilah fuzzy matching lintas sumber dilakukan, karena semua data
  sudah berada dalam satu tabel. Sebelum matching, ia memvalidasi ulang seluruh
  data dan mengarantina baris non-produk. Setelah matching, ia memeriksa harga,
  varian, merchant, periode dan bentuk penawaran sebelum menggabungkan baris.
- engine\promo_product_validation.py: aturan generik untuk menolak kandidat yang
  terlihat seperti judul editorial/teks kampanye atau tidak memiliki bukti penawaran
  produk. Aturan dipakai di collector DAN enhancer (defense-in-depth).
- engine\promo_names.py: normalisasi nama, ukuran/kemasan dan pencocokan kemiripan.
- engine\promo_catalog_all_merchants_v2.py: modul helper parsing harga, tanggal,
  diskon dan merchant yang digunakan enhancer agar aturan parsing konsisten.
- engine\test_pipeline_v7.py: uji regresi untuk memastikan judul editorial ditolak,
  produk valid tetap lolos, fuzzy match menggabungkan penawaran cocok lintas sumber,
  dan varian/harga berbeda tidak ikut tergabung.

KENAPA FUZZY MATCH ADA DI ENHANCER?
Collector bekerja satu sumber pada satu waktu; ia belum memiliki konteks dari
sumber lain. Fuzzy match membutuhkan perbandingan lintas-sumber, sehingga tempat
utamanya di enhancer. Collector tetap menjalankan normalisasi dasar dan validasi
kandidat, sedangkan enhancer melakukan validasi ulang sebelum finalisasi. Dengan
begitu, data buruk bisa dicegah lebih awal sekaligus ada perlindungan kedua sebelum
masuk katalog.

ATURAN PENTING
- Klaim diskon di judul kampanye tidak otomatis menjadi diskon tiap produk.
- Baris tanpa bukti harga/penawaran produk atau yang terlihat sebagai judul editorial
  masuk file audit penolakan dan tidak tampil di sheet Aktif.
- Produk dengan ukuran/rasa/varian berbeda tidak digabung otomatis.
- Promo dengan harga berbeda, syarat berbeda, tanggal konflik, atau merchant ambigu
  tetap dipisahkan atau dimasukkan ke review; fuzzy matching bukan berarti asal merge.
- Hemat.id, Katalogpromosi.com, dan Scanharga.com adalah sumber agregator/konten.
  Data belum otomatis terverifikasi oleh retailer resmi.
- Scanharga dapat menghasilkan URL gambar katalog. Pipeline ini menyimpan metadata
  gambarnya untuk tindak lanjut; tidak mengarang produk/harga jika informasi tidak
  terbaca sebagai teks. OCR katalog gambar belum menjadi bagian dari versi ini.

FILE UTAMA
- hasil\Katalog_Promo_Final.xlsx
- hasil\Katalog_Promo_Final.csv

FILE TEKNIS
- _internal\promo_multisource_products_all.csv: hasil kandidat produk dari collector.
- _internal\promo_multisource_rejected_candidates.csv: kandidat collector yang ditolak.
- _internal\promo_product_rows_rejected.csv: baris CSV lama/scrape yang ditolak oleh enhancer.
- _internal\promo_catalog_multisource_full_audit.csv: semua baris eligible sebelum fuzzy akhir.
- _internal\promo_possible_duplicate_candidates.csv: kandidat mirip yang belum aman digabung.
- _internal\promo_multisource_runlog.json dan file quality report: ringkasan eksekusi.
Folder _internal dibuat otomatis. Tidak perlu membuka file-file ini untuk penggunaan harian,
tetapi jangan hapus jika ingin mempertahankan audit dan kemampuan fallback last-good.

PERSYARATAN
- Python 3.10 atau lebih baru.
- Internet diperlukan untuk mengambil halaman situs dan menginstal library pertama kali.

BATAS PENGUJIAN
Self-test dan uji regresi lokal menguji parser serta aturan normalisasi/fuzzy. Scraping
live tetap bergantung pada ketersediaan situs, DNS, perubahan HTML dan pembatasan akses.
Tidak ada perlindungan situs yang dicoba untuk dilewati. Periksa run log untuk tahu sumber
mana yang dapat diakses saat menjalankan pipeline.
