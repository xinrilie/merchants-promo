# Hotfix: pencarian gratis menerima HTTP 202 dari DuckDuckGo

## Gejala
`promo_free_price_finder.py` menampilkan `HTTP 202, HTTP 202` untuk semua produk. Akibatnya `verified_references_found=0`, database referensi harga pasar tetap kosong, dan sheet `Peluang Harga` tidak berisi baris.

## Penyebab
DuckDuckGo dapat mengembalikan halaman challenge/anti-bot dengan status HTTP 202 untuk permintaan HTTP biasa. Respons tersebut bukan halaman hasil pencarian yang dapat diparse.

## Perubahan
- DuckDuckGo tetap dicoba terlebih dahulu.
- Jika DuckDuckGo mengembalikan status selain 200, halaman challenge, atau markup hasil yang tidak dikenali, modul mencoba fallback Bing RSS publik.
- Bing RSS tidak memerlukan API key, tetapi merupakan antarmuka publik yang tidak dijamin selalu tersedia.
- Jika kedua penyedia tidak dapat digunakan, produk dilaporkan sebagai `search_error` dan tidak disimpan sebagai hasil kosong ke cache.
- Validasi halaman produk (kecocokan produk/varian/ukuran serta bukti harga) tetap berlaku. Hasil pencarian saja tidak langsung diterima sebagai referensi harga.

## File yang berubah
- `engine/promo_free_price_finder.py`
- `engine/test_free_price_finder.py`
- `README.md`
- `CATATAN_MODE_GRATIS.md`

## Pengujian
`python -m pytest -q engine` — 121 tes lulus pada lingkungan audit. Tes fallback HTTP 202 memakai respons simulasi; ketersediaan provider secara live tetap harus diverifikasi dari komputer pengguna.

## Penerapan aman
Ekstrak hotfix ke folder root proyek yang sama untuk mengganti file yang disebutkan di atas. Hotfix ini tidak memuat atau menimpa database harga, cache, hasil katalog, atau workbook.
