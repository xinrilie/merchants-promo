KATALOG PROMO - PAKET RINGKAS V6 (FUZZY MATCHING + CROSS-SOURCE MERCHANT CORROBORATION)
============================

CARA PALING MUDAH
1. Ekstrak semua isi ZIP ke satu folder baru.
2. Klik Install_Dependency.bat satu kali (cukup sekali di komputer yang sama).
3. Klik dua kali Jalankan_Promo.bat.
4. Buka hasil\Katalog_Promo_Final.xlsx. Ini file utama untuk penggunaan sehari-hari.

STRUKTUR FOLDER (TIDAK PERLU MEMBUKA SEMUA FILE)
- hasil\      : katalog final Excel dan CSV yang perlu kamu gunakan.
- input\      : dua CSV katalog lama sebagai sumber awal.
- engine\     : script mesin scraper/enhancer; jangan pindah-pindah file di dalamnya.
- _internal\  : dibuat otomatis; menyimpan data mentah, CSV perantara, audit, dan log.

Jadi tidak, kamu tidak perlu mengelola semua file satu per satu. Cukup jalankan
Jalankan_Promo.bat dan buka file di folder hasil\. File perantara disimpan di
_internal\ agar tidak memenuhi folder utama.

NORMALISASI, FUZZY MATCHING, DAN DEDUPLIKASI LINTAS SUMBER (V6)
Nama dari situs bisa berbeda karena kapitalisasi, ukuran ditulis dua kali,
abreviasi kemasan (pck/btl/klg), atau label harga yang salah terbaca sebagai
nama produk. Mesin membersihkan label seperti "Harga Normal", "Harga Promo",
"Diskon 67%" dari nama, tetapi tetap mempertahankan ukuran produk.

Penawaran setara dari Hemat.id, Momsmoney/Kontan, atau sumber lain digabung jika
merchant, nama/ukuran produk, periode promo, jenis penawaran, dan harga yang diketahui
saling cocok. Sumber-sumber asal tetap dicatat di audit. Jika harga berbeda,
periode bertentangan, atau syarat promo tidak sama, baris tidak digabung diam-diam;
data tetap terpisah untuk pemeriksaan.

Mesin menormalisasi variasi yang jelas, misalnya:
- "BENG-BENG Chocolate Drink 10x27g pck"
  dan "Beng-beng per 10 pcs 27 gr Chocolate Drink 27 gr"
  menjadi "Beng-Beng Chocolate Drink 10 x 27 g".
- "My Baby 100 ml Body Cologne Sweet Floral 100 ml Sweet Floral"
  dan "MY BABY Body Cologne Sweet Floral 100ml btl"
  menjadi "My Baby Body Cologne Sweet Floral 100 ml".
- "LASEGAR Twist Jeruk-Lemon 320ml klg" menjadi
  "Lasegar Twist Jeruk-Lemon 320 ml".

Nama asal tetap dicatat di audit. Sheet "Variasi Nama" menunjukkan variasi sumber
untuk nama standar yang sama. Produk dengan ukuran berbeda TIDAK otomatis
digabung. Penawaran dengan harga, syarat, atau periode berbeda juga tetap dipisah;
lihat catatan pada sheet variasi jika nama produknya sama tetapi detail promonya beda.

FUZZY MATCHING (DINAMIS) DAN KORELASI MERCHANT LINTAS SUMBER
- Nama produk yang mirip dinilai memakai algoritma teks standar Python; tidak perlu menambah semua nama baru ke daftar manual. Nama yang hanya mirip dan punya konflik tetap ditampilkan sebagai kandidat, bukan digabung paksa.
- Penggabungan otomatis hanya dilakukan jika merchant diketahui sama, ukuran/varian cocok, periode bertumpang tindih, serta penawaran dan harga kompatibel.
- Baris mirip yang tidak aman digabung tidak dibuang; lihat sheet "Kemungkinan Duplikat" dan file _internal\promo_possible_duplicate_candidates.csv.
- Produk beda ukuran atau varian rasa/formula tidak digabung hanya karena namanya mirip.
- URL Hemat yang menyebut beberapa retailer sekaligus tidak lagi otomatis dianggap merchant pertama yang cocok.

HASIL YANG DIBUAT
- hasil\Katalog_Promo_Final.xlsx : sheet Mulai Di Sini, Aktif, Terkonfirmasi,
  Perlu Review, Kemungkinan Duplikat, dan Variasi Nama.
- hasil\Katalog_Promo_Final.csv  : tabel promo ringkas.
- _internal\                   : data mentah scraper, log, dan file audit teknis.
  Jangan hapus folder ini jika ingin mekanisme mempertahankan data lama saat situs
  gagal diakses berfungsi pada run berikutnya.

CATATAN KUALITAS
- Hemat.id, Katalogpromosi.com, dan Scanharga.com adalah agregator; hasilnya tidak
  otomatis terverifikasi oleh retailer resmi.
- Brosur gambar dari Scanharga dicatat sebagai referensi; versi ini tidak menebak
  harga dari gambar dan belum menjalankan OCR.
- Jika situs membatasi akses atau mengubah struktur HTML, cek log di _internal\.

PENGUJIAN DUPLIKASI
Sebelum scraping, Jalankan_Promo.bat kini menjalankan test yang meniru dua kasus:
- Aice Mochi: label "Harga Normal" tidak lagi menjadi bagian nama produk; harga dan
  periode eksplisit diprioritaskan ketika penawaran dari dua sumber setara.
- My Baby Beli 1 Gratis 1: variasi penulisan lintas sumber menjadi satu baris katalog.
Jika dua sumber mempunyai harga promo eksplisit yang berbeda, penawaran tetap dipisah
dan tidak digabung secara otomatis.

PERBAIKAN V6 UNTUK DUPLIKAT DARI HALAMAN HEMAT MULTI-RETAILER
- Jika kartu Hemat tidak menunjukkan merchant individual, sistem boleh mencocokkan ke
  sumber lain yang menyebut satu retailer secara eksplisit hanya jika URL listing Hemat
  juga menyebut retailer tersebut dan bukti produk, harga/penawaran, serta periode cocok.
- Merchant hasil korelasi diberi merchant_resolution_method=
  cross_source_corroboration_unverified dan catatan bahwa ini belum diverifikasi resmi.
- Jika dua retailer cocok, harga/penawaran/varian bertentangan, atau bukti tidak cukup,
  sistem tidak menebak merchant dan data tetap pada daftar review.
- Menambah regression test khusus untuk contoh Aice dan My Baby pada pola sumber ini.

PENJELASAN DUPLIKAT LINTAS SUMBER (V6)
Baris Aice/My Baby dari URL Hemat yang mengandung beberapa retailer bisa dipadukan dengan
sumber lain hanya jika nama produk, ukuran/varian, periode, penawaran, dan harga saling cocok,
URL Hemat memang menyebut retailer tersebut, serta sumber pembanding menyebut retailer itu
secara eksplisit. Merchant hasil korelasi diberi tanda belum terverifikasi resmi. Jika ada lebih
dari satu retailer kandidat atau harga/penawaran bertentangan, baris tetap terpisah untuk review.
Karena itu, sheet Review bisa saja menampilkan satu baris yang juga ada di Aktif untuk masalah
kualitas data; periksa kolom alasan review. Ini bukan selalu duplikat penawaran yang sama.
