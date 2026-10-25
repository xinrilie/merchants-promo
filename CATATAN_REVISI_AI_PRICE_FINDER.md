# Catatan Revisi — Price Reference Finder Gratis dan AI Opsional

## Tujuan

Menambah cara mencari pembanding harga pasar ketika halaman promo hanya mencantumkan harga promo tanpa harga normal, tanpa mewajibkan API berbayar. Hasil pembanding tidak pernah otomatis dianggap harga normal resmi retailer atau diskon resmi.

## Perubahan

- Menambahkan `engine/promo_free_price_finder.py` sebagai jalur gratis: memakai pencarian web publik dan validasi lokal HTML/JSON-LD, tanpa `OPENAI_API_KEY`.
- `engine/promo_ai_price_finder.py` tetap tersedia sebagai opsi terpisah jika pengguna memang mau membayar OpenAI API.
- Kandidat hasil pencarian wajib lolos pencocokan nama/varian/ukuran, lalu URL dibuka kembali dan nominal harga diverifikasi dari HTML atau JSON-LD Product/Offer.
- Harga yang ditemukan di halaman harus terkait dengan konteks SKU, bukan hanya nominal yang kebetulan muncul di tempat lain pada halaman.
- Jenis sumber diturunkan dari domain yang diverifikasi untuk domain yang dikenali; label yang diklaim AI tidak dipercaya sebagai bukti.
- Mencatat bukti, URL, domain, jenis harga, metode verifikasi, tanggal observasi, dan cache pencarian.
- Menggunakan database terpisah `data/product_market_price_reference.csv`; cache mode gratis disimpan di `data/free_price_search_cache.csv`, sedangkan cache mode berbayar disimpan di `data/ai_price_search_cache.csv`.
- Menambahkan perhitungan median pasar berdasarkan harga yang masih segar dari domain berbeda. Minimal dua domain diperlukan agar persentase “Lebih Murah dari Median Pasar” tampil.
- Menambahkan tab Excel `Peluang Harga`; kolom diskon resmi pada sheet `Promo` tetap memakai alur sebelumnya.
- Memperbarui dokumentasi untuk penggunaan opsional, batas maksimum 20 produk per run, cache 7 hari, dan umur pembanding 14 hari.
- Pencarian gratis tidak memerlukan API key. Mesin pencari publik bisa membatasi request atau mengubah markup; kegagalan search tidak disimpan sebagai hasil “tidak ada harga”.

## Validasi

- `python -m compileall -q engine` berhasil.
- `python engine/promo_names.py` berhasil.
- `python engine/promo_multisource_collector.py --self-test` berhasil.
- `python -m pytest -q engine`: **119 tes lulus** setelah menambah 4 tes mode gratis.
- `python engine\promo_free_price_finder.py --dry-run --limit 5` menampilkan antrean tanpa mengakses jaringan.
- Tes mode gratis menggunakan respons HTTP tiruan untuk memastikan harga harus cocok dengan nama/varian dan harga terlihat pada halaman. Tidak ada pencarian live dilakukan dalam audit.
- Pencarian public-web real tetap perlu diuji dari komputer pengguna; beberapa search engine dapat membatasi request.
- Integrasi ekspor tetap menggunakan database harga pasar yang sama dan tidak mengubah kolom diskon resmi.

## Menjalankan mode gratis

Setelah collector selesai dan sebelum enhancer:

```cmd
python engine\promo_free_price_finder.py --dry-run --limit 5
python engine\promo_free_price_finder.py --limit 5
python engine\enrich_existing_promo_catalog_multisource.py
```

Mode gratis tidak memakai OpenAI API. `--dry-run` tidak mengakses internet. Mulai dari limit kecil; pencarian publik dapat dibatasi atau berubah. Jika ingin memakai mode AI berbayar secara sengaja, jalankan `engine\promo_ai_price_finder.py` dan siapkan `OPENAI_API_KEY` secara terpisah. Jangan commit API key ke GitHub.
