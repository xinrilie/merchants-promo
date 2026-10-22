# 🛒 Merchant Promo Catalog Engine (v7)

**Python Version:** 3.8+ | **Platform:** Windows

---

Aplikasi otomatisasi tingkat lanjut untuk *scraping*, pembersihan data, dan penggabungan katalog promo retail multi-sumber. Menggunakan **Dynamic Fuzzy Matching** lintas sumber serta penyaringan berbasis aturan dua lapis untuk memisahkan konten editorial dari penawaran produk riil.

---

## ⚙️ Arsitektur Sistem & Alur Kerja

Sistem ini memisahkan tanggung jawab secara tegas menjadi dua modul utama untuk efisiensi pemrosesan data:

1.  **Collector (Pencari Data):** Melakukan *scraping* situs agregator, *parsing* kartu/artikel, normalisasi dasar, serta menolak mentah-mentah kandidat baris yang terdeteksi sebagai judul artikel/non-produk.
2.  **Enhancer (Pemroses & Validasi):** Membaca hasil *scrape* dan CSV lama, mengarantina baris tidak valid, melakukan **Cross-Source Fuzzy Matching** setelah seluruh data terkumpul, lalu menyusun katalog final.

---

## ✨ Fitur Utama (Update v7)

*   **Penyaringan Dua Lapis (Dual-Layer Filtering):** Mendeteksi dan membuang judul artikel editorial/kampanye tingkat tinggi (seperti teks mekanisme atau benefit bank) agar tidak keliru masuk sebagai baris produk.
*   **Fuzzy Matching Lintas Sumber:** Membandingkan nama produk, *merchant*, ukuran, varian, periode, harga, dan syarat secara aman setelah semua data dari berbagai situs terkumpul.
*   **Sistem Audit Data Transparan:** Menghasilkan laporan kualitas data otomatis untuk mempermudah peninjauan performa *engine*.
*   **Proteksi Konflik Data:** Tetap memisahkan varian rasa atau harga yang bertentangan secara sengaja untuk mencegah penggabungan keliru yang merusak informasi promo.

---

## 📂 Hasil Output & Laporan Internal

Setelah menjalankan `Jalankan_Promo.bat`, sistem akan langsung memutakhirkan file berikut:

*   **`hasil/Katalog_Promo_Final.xlsx`** – Katalog utama bersih (Sheet Aktif) dan data tinjauan.
*   **`hasil/Katalog_Promo_Final.csv`** – Katalog utama dalam format CSV ringkas.
*   **`_internal/promo_product_rows_rejected.csv`** – Daftar kandidat produk/artikel yang ditolak oleh sistem.
*   **`_internal/promo_possible_duplicate_candidates.csv`** – Produk mirip yang sengaja dipisahkan dan butuh *review*.
*   **`_internal/promo_multisource_quality_report.json`** – Ringkasan statistik kualitas data dan metrik penggabungan.

---

## 🚀 Cara Menjalankan

1. Ekstrak ZIP v7 ke folder baru di komputer lokal Anda.
2. Jalankan `Install_Dependency.bat` (jika dependensi belum tersedia).
3. Jalankan `Jalankan_Promo.bat` untuk memulai swauji (*self-test*), *scraping*, dan *enhancing*.
4. Periksa folder `hasil/` untuk melihat katalog final Anda.
