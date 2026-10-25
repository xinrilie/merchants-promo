# Catatan Revisi — AI Price Reference Finder

## Tujuan

Menambah cara mencari pembanding harga pasar ketika halaman promo hanya mencantumkan harga promo tanpa harga normal. Hasil pembanding tidak pernah otomatis dianggap harga normal resmi retailer atau diskon resmi.

## Perubahan

- Menambah `engine/promo_ai_price_finder.py` yang menggunakan OpenAI Responses API dengan tool `web_search`.
- Kandidat hasil pencarian wajib lolos pencocokan nama/varian/ukuran, lalu URL dibuka kembali dan nominal harga diverifikasi dari HTML atau JSON-LD Product/Offer.
- Harga yang ditemukan di halaman harus terkait dengan konteks SKU, bukan hanya nominal yang kebetulan muncul di tempat lain pada halaman.
- Jenis sumber diturunkan dari domain yang diverifikasi untuk domain yang dikenali; label yang diklaim AI tidak dipercaya sebagai bukti.
- Mencatat bukti, URL, domain, jenis harga, metode verifikasi, tanggal observasi, dan cache pencarian.
- Menggunakan database terpisah `data/product_market_price_reference.csv`; cache lokal disimpan di `data/ai_price_search_cache.csv`.
- Menambahkan perhitungan median pasar berdasarkan harga yang masih segar dari domain berbeda. Minimal dua domain diperlukan agar persentase “Lebih Murah dari Median Pasar” tampil.
- Menambahkan tab Excel `Peluang Harga`; kolom diskon resmi pada sheet `Promo` tetap memakai alur sebelumnya.
- Memperbarui dokumentasi untuk penggunaan opsional, batas maksimum 20 produk per run, cache 7 hari, dan umur pembanding 14 hari.
- Tidak menjalankan pencarian berbayar otomatis. `OPENAI_API_KEY` harus disetel eksplisit oleh pengguna.

## Validasi

- `python -m compileall -q engine` berhasil.
- `python engine/promo_names.py` berhasil.
- `python engine/promo_multisource_collector.py --self-test` berhasil.
- `python -m pytest -q engine`: **115 tes lulus**.
- `--dry-run --limit 5` berjalan tanpa panggilan API dan menemukan antrean produk yang layak dicari.
- Integrasi ekspor dicoba di salinan proyek terpisah dengan dua referensi uji sintetis; sheet `Peluang Harga` terisi dan hasil median/perbandingan sesuai harapan. Referensi uji tidak dimasukkan ke database rilis.
- Tes offline tidak melakukan panggilan ke OpenAI/web search.
- Pencarian AI live belum dijalankan karena API key tidak disediakan dalam lingkungan audit ini.

## Menjalankan

Dari CMD Windows, setelah collector selesai dan sebelum enhancer:

```cmd
python engine\promo_ai_price_finder.py --dry-run --limit 5
set OPENAI_API_KEY=ISI_API_KEY_MILIKMU
python engine\promo_ai_price_finder.py --limit 20
python engine\enrich_existing_promo_catalog_multisource.py
```

`--dry-run` hanya menampilkan antrean tanpa memanggil API. Untuk pencarian aktual, OpenAI API dapat mengenakan biaya sesuai penggunaan. Jangan commit API key ke GitHub. Pencarian live perlu diperiksa hasilnya; angka pasar bukan diskon resmi.
