KATALOG PROMO - PAKET RINGKAS
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

NORMALISASI NAMA PRODUK
Nama dari situs bisa berbeda karena kapitalisasi, ukuran ditulis dua kali,
abreviasi kemasan (pck/btl/klg), atau teks berulang dari HTML/OCR. Mesin kini
menormalisasi variasi yang jelas, misalnya:
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

HASIL YANG DIBUAT
- hasil\Katalog_Promo_Final.xlsx : sheet Mulai Di Sini, Aktif, Terkonfirmasi,
  Perlu Review, dan Variasi Nama.
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
