# Catatan Integrasi Gemini + Google Search

## Apa yang ditambahkan
- `engine/promo_gemini_price_finder.py`: pencarian berbasis model `gemini-2.5-flash` dan tool Google Search grounding melalui Gemini Developer API.
- `engine/test_gemini_price_finder.py`: tes untuk respons grounding, URL sitasi, petunjuk harga, validasi halaman, dan batas kuota.
- README diperbarui dengan langkah dan peringatan kuota.

## Cara menjalankan di Windows CMD
```cmd
python engine\promo_gemini_price_finder.py --dry-run --limit 5
set GEMINI_API_KEY=ISI_API_KEY_GOOGLE_AI_STUDIO
python engine\promo_gemini_price_finder.py --limit 5
python engine\enrich_existing_promo_catalog_multisource.py
```
Tutup workbook Excel sebelum enhancer dijalankan.

## Kontrak bukti
1. URL model harus sama dengan salah satu URL dari metadata Google Search grounding.
2. Harga dari output model hanya disimpan sebagai kandidat.
3. Database harga pasar hanya menerima nominal yang ikut muncul pada halaman sumber dan cocok dengan SKU/varian/ukuran.
4. Kandidat yang belum lolos disimpan di `data/product_market_price_candidates.csv` dan sheet `Kandidat Harga`.
5. Harga pembanding pasar tetap terpisah dari `Diskon (%)` resmi.

## Kuota dan privasi
Google mendokumentasikan Gemini 2.5 Flash + Google Search grounding hingga 500 grounded prompts/hari pada Free tier, berbagi batas dengan Flash-Lite. Ketersediaan aktual bergantung pada akun/project; cek https://ai.google.dev/gemini-api/docs/pricing sebelum memakai. Jangan mengaktifkan/menautkan billing bila tujuanmu adalah menghindari tagihan. Konten Free tier dapat digunakan Google untuk meningkatkan produk sesuai ketentuan mereka. API key hanya disimpan di environment variable; jangan dimasukkan ke kode, file `.env` yang ter-commit, atau ZIP diagnostik.

## Verifikasi lokal
Paket diuji dengan pytest offline. Panggilan Google Search live belum dijalankan karena membutuhkan API key pengguna; hasil online dan akses Free tier harus diverifikasi di mesin pengguna.
