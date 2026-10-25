# Mode gratis AI Price Finder

- `engine/promo_free_price_finder.py` menggunakan pencarian web publik tanpa OpenAI API.
- Pencarian publik adalah tahap penemuan. Harga hanya masuk database `product_market_price_reference.csv` jika nominal dan identitas produk ditemukan pada halaman sumber dengan validasi yang memadai.
- Judul, URL, cuplikan, dan petunjuk nominal hasil pencarian yang belum terverifikasi disimpan di `data/product_market_price_candidates.csv` dan diekspor ke sheet `Kandidat Harga`.
- Kandidat yang belum terverifikasi **tidak** digunakan untuk menghitung median harga pasar atau diskon resmi.
- Jalankan `python engine\promo_free_price_finder.py --dry-run --limit 5` untuk melihat antrean; `python engine\promo_free_price_finder.py --limit 5 --force` untuk mencari lagi tanpa memakai cache; kemudian jalankan enhancer.
- Cache pencarian mode gratis saat ini menggunakan `data/free_price_search_cache_v2.csv`.
- `engine/promo_ai_price_finder.py` masih merupakan opsi OpenAI API berbayar; tidak diperlukan untuk jalur gratis.
