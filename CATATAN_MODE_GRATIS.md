# Mode pencarian harga tanpa API berbayar

- Jalur utama gratis: `engine/promo_free_price_finder.py`. Jalur ini adalah pencarian web publik + validasi deterministik, bukan panggilan ke model AI generatif.
- Tidak memerlukan `OPENAI_API_KEY` atau pemanggilan model berbayar.
- Menggunakan halaman hasil pencarian publik sebagai penemuan URL dan memeriksa harga yang terlihat pada halaman produk/JSON-LD.
- Validasi harga tidak menjamin halaman tetap tersedia; mesin pencari publik dapat membatasi/memblokir permintaan atau mengubah markup. Jika search gagal, run dilaporkan gagal dan tidak menyimpan status negatif ke cache.
- Produk/ukuran/varian harus cocok; harga pembanding tidak menjadi harga normal resmi merchant dan tidak mengubah `Diskon (%)`.
- Perintah CMD: `python engine\promo_free_price_finder.py --dry-run --limit 5`, lalu `python engine\promo_free_price_finder.py --limit 5`, lalu jalankan enhancer.
- `engine/promo_ai_price_finder.py` tetap ada sebagai opsi OpenAI API berbayar, tetapi tidak diperlukan untuk jalur gratis.
