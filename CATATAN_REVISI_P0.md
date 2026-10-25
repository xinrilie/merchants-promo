# Catatan Revisi P0 — Merchants Promo

## Perubahan yang diterapkan

1. **HTTP 404/410 ditangani pada tingkat URL.** URL yang tidak ditemukan atau telah dihapus dicatat dan dilewati; artikel lain pada sumber yang sama tetap dicoba. HTTP 401/403/429, 5xx, timeout/kegagalan request, dan redirect keluar domain tetap menghentikan sumber untuk run tersebut.
2. **Status request lebih transparan.** Laporan kesehatan sumber kini memisahkan jumlah request `not_found`, `gone`, dan `client_error` dari pemblokiran sumber.
3. **CSV ringkas disamakan dengan tampilan Excel.** File `hasil/Katalog_Promo_Final_Ringkas.csv` memakai fungsi pembentuk baris yang sama dengan sheet `Promo`, termasuk status `Belum bisa dihitung`. File `hasil/Katalog_Promo_Final.csv` dipertahankan sebagai ekspor lebih rinci untuk pengolahan data.
4. **Regresi harga tanpa bukti ditambahkan.** Harga yang hanya disebut pada judul kampanye, tanpa bukti nominal yang cocok pada penawaran/baris sumber, harus dikosongkan dan dialihkan ke review.
5. **Tes regresi fallback URL ditambahkan.** Tes membuktikan bahwa artikel 404 tidak menghentikan proses ekstraksi artikel lain pada sumber yang sama.

## Verifikasi yang dilakukan

- `python -m compileall -q engine` — lulus.
- `python engine/promo_names.py` — self-test lulus.
- `python engine/promo_multisource_collector.py --self-test` — self-test lulus.
- `python -m pytest -q engine --disable-warnings` — **101 passed**.
- `hasil/Katalog_Promo_Final_Ringkas.csv` diverifikasi memiliki 455 baris data dan 13 kolom yang sama dengan sheet `Promo` pada workbook yang disertakan; pengecekan nilai per sel tidak menemukan mismatch.

## Batas verifikasi

Scraping live tidak dijalankan pada revisi ini. Tes lulus membuktikan perilaku kode pada kasus offline yang diuji, bukan status akses terkini atau kelengkapan data dari situs sumber.
