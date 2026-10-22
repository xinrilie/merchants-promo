# 🛒 Merchant Promo Catalog Engine (v5)

[![Python Version](https://shields.io)](https://python.org)
[![Platform](https://shields.io)](https://microsoft.com)

Aplikasi otomatisasi untuk melakukan *scraping*, pembersihan data, dan penggabungan katalog promo retail dari berbagai sumber agregator (seperti Hemat.id dan Momsmoney). Sistem ini dilengkapi dengan **Dynamic Fuzzy Matching** untuk mendeteksi variasi penulisan nama produk secara cerdas tanpa merusak validitas data harga.

---

## ✨ Fitur Utama (Update v5)

*   **Pencocokan Nama Dinamis (Fuzzy Matching):** Menghitung skor kemiripan teks untuk mendeteksi variasi penulisan nama produk baru secara otomatis tanpa daftar manual.
*   **Deduplikasi Konservatif:** Penggabungan baris promo lintas sumber hanya dilakukan jika komponen kritis (*merchant*, ukuran, varian, periode, harga, dan syarat) terbukti kompatibel.
*   **Lembar Review "Kemungkinan Duplikat":** Memisahkan produk yang mirip tetapi belum aman digabung ke *sheet* khusus, lengkap dengan skor kemiripan dan alasan pemeriksaan.
*   **Resolusi Merchant Akurat:** Penanganan cerdas untuk URL yang mencantumkan beberapa retailer sekaligus agar tidak salah klaim kepemilikan data.
*   **Pengujian Otomatis (Automated Tests):** Dilengkapi dengan unit pengujian lokal untuk validasi normalisasi, fuzzy matching, penawaran lintas sumber, dan perlindungan harga konflik sebelum proses *scraping* berjalan.

---

## 📂 Struktur Folder Proyek

```text
merchants-promo/
│
├── engine/                 # 🧠 Core logic / Mesin utama scraper & pemroses data
├── input/                  # 📥 File CSV data mentah / katalog lama dari sumber awal
├── hasil/                  # 📤 Output akhir (Excel & CSV Ringkas) untuk pengguna
├── _internal/              # ⚙️ Data mentah scraper, log teknis, dan file audit (Auto-generated)
│
├── Install_Dependency.bat  # 🛠️ Skrip satu-klik untuk instalasi library Python
├── Jalankan_Promo.bat      # 🚀 Skrip satu-klik untuk menjalankan seluruh sistem
├── requirements.txt        # 📄 Daftar dependensi library Python
└── README.md               # 📖 Dokumentasi proyek
```

---

## 🚀 Cara Menjalankan

Ikuti langkah mudah berikut untuk menjalankan aplikasi di komputer lokal Anda:

### 1. Persiapan Awal (Hanya untuk Penggunaan Pertama)
Pastikan komputer Anda sudah terinstal Python. Klik dua kali pada file:
```bash
Install_Dependency.bat
```
*Skrip ini akan menginstal semua library yang dibutuhkan di `requirements.txt` secara otomatis.*

### 2. Eksekusi Scraper & Pemrosesan Data
Untuk mulai mengambil dan memproses data katalog promo terbaru, klik dua kali pada file:
```bash
Jalankan_Promo.bat
```

### 3. Melihat Hasil
Setelah proses di terminal hitam selesai, buka folder **`hasil/`** dan gunakan file berikut:
*   **`Katalog_Promo_Final.xlsx`** (File Excel Utama):
    *   *Sheet* **Aktif / Terkonfirmasi:** Katalog bersih siap pakai.
    *   *Sheet* **Kemungkinan Duplikat:** Daftar produk yang butuh *review* manual Anda karena memiliki kemiripan teks tetapi harganya berbeda.
*   **`Katalog_Promo_Final.csv`** (Versi Ringkas): Sangat cocok jika Anda ingin mengimpor data ini ke database atau aplikasi lain.

---

## ⚠️ Catatan Penting Kualitas Data

1.  **Proteksi Harga:** Jika dua sumber memberikan harga promo eksplisit yang berbeda untuk produk yang sama, sistem **sengaja tidak menggabungkannya** agar informasi diskon yang valid tidak hilang.
2.  **Prioritas Periode:** Jika ada bentrokan rentang waktu, sistem otomatis memprioritaskan rentang tanggal eksplisit (misal: `10-15 Okt`) di atas estimasi bulan (`Okt 2026`).
3.  **Folder `_internal/`:** Jangan menghapus folder ini agar mekanisme pertahanan data tetap berjalan (mempertahankan data lama jika situs agregator mendadak gagal diakses/RTO pada *run* berikutnya).
