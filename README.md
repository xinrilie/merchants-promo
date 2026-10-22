# 🛒 Merchant Promo Catalog Engine (v6)

**Python Version:** 3.8+ | **Platform:** Windows

---

Aplikasi otomatisasi untuk *scraping*, pembersihan, dan penggabungan katalog promo retail dari berbagai sumber dengan **Dynamic Fuzzy Matching** serta **Merchant Inference Logic**.

---

## ✨ Fitur Utama (Update v6)

*   **Resolusi Merchant & Cross-Source Inference:** Menyimpulkan retailer yang hilang dengan memvalidasi harga dan periode antar sumber.
*   **Penyelarasan Tipe Promo:** Menggabungkan promo setara seperti "Beli 1 Gratis 1" dengan "Diskon 50%".
*   **Fuzzy Matching & Proteksi Data:** Menangani variasi nama produk secara dinamis serta mencegah penggabungan jika terjadi konflik harga atau ambiguitas merchant.
*   **Audit Kualitas:** Memisahkan data audit ke sheet "Perlu Review" untuk mencegah duplikasi pada sheet Aktif.

---

## 🚀 Cara Menjalankan

1. Ekstrak ZIP v6 ke folder baru.
2. Jalankan `Install_Dependency.bat` (untuk instalasi pertama).
3. Jalankan `Jalankan_Promo.bat` untuk memproses data.
4. Buka hasil keluaran di folder **`hasil/Katalog_Promo_Final.xlsx`**.

Detail lengkap struktur folder dan catatan kualitas data dapat ditemukan di dokumentasi internal proyek.
