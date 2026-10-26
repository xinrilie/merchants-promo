# Audit Ringkas — Upgrade OCR Poster Promo

Tanggal paket: 26 Oktober 2026.

## Keputusan desain

- Estimasi harga AI dan seeding rentang perkiraan dinonaktifkan dari jalur katalog.
- Sebanyak 257 rekaman `user_supplied_estimate` dipindahkan ke `_internal/legacy_price_estimates_disabled.csv`; database aktif sekarang hanya memuat referensi bersumber.
- File sumber rentang dan alias lama dipindahkan ke `_internal/legacy_user_product_price_ranges_disabled.csv` serta `_internal/legacy_user_product_price_reference_aliases_disabled.csv`.
- Harga normal yang hanya ada pada poster diproses oleh OCR lokal. OCR hanya menghasilkan kandidat; harga tidak digunakan sampai ada keputusan `TERIMA` di workbook review.
- Referensi harga pasar tetap memakai workflow Excel manual yang terpisah dan membutuhkan URL halaman langsung serta bukti harga.

## Snapshot data sesudah regenerasi lokal

- Baris input dari tiga agregator: 497.
- Baris terkonsolidasi: 492.
- Promo pada katalog utama per 26 Oktober 2026: 306.
- Promo terkonfirmasi aktif: 163.
- Promo aktif dengan periode estimasi: 143.
- Baris katalog dengan diskon terisi: 192.
- Estimasi bundling yang tetap ditandai non-konfirmasi: 46.
- Referensi harga normal bersumber yang tersedia: 228.
- Referensi estimasi AI/rentang yang digunakan aktif: 0.
- Estimasi lama yang disimpan di arsip audit: 257.
- Referensi pembanding pasar yang terverifikasi: 0.
- Harga normal yang diterapkan dari poster OCR yang sudah ditinjau: 0 (belum ada keputusan review pengguna).

Angka katalog berubah dari snapshot tanggal 25 Oktober ke tanggal 26 Oktober karena status periode promo diperhitungkan ulang; bukan semua perubahan disebabkan oleh penghapusan estimasi. Promo yang sudah berakhir tidak harus tetap ditampilkan sebagai aktif.

## Cakupan gambar dan OCR

- Indeks poster dalam snapshot: 412 catatan gambar, 391 URL gambar unik.
- Mesin OCR: RapidOCR + ONNX Runtime sebagai backend utama; Tesseract hanya fallback jika binary lokal tersedia.
- OCR menyimpan cache, baris teks/koordinat, kandidat harga, dan workbook review yang URL artikel serta gambar poster-nya dapat diklik.
- Harga normal/promo berlabel dipasangkan terlebih dahulu. Dua harga tanpa label hanya diusulkan jika hitungan persentasenya cocok dengan klaim diskon dalam toleransi; pasangan ambigu dibiarkan untuk pemeriksaan.
- Kandidat berdekatan yang membaca harga sama digabung untuk mengurangi duplikasi, tetapi dua nama SKU berbeda yang sama-sama cocok tetap dipisah.
- Foto dari URL sumber yang sebenarnya tidak diunduh/diproses massal selama pembangunan paket ini. Smoke test OCR dilakukan pada poster sintetis lokal: backend Tesseract mengenali harga normal Rp31.500, harga promo Rp17.500, serta klaim diskon 44%; hitungan independen menjadi 44,4%. Ini memverifikasi jalur OCR/parser offline, bukan akurasi terhadap seluruh poster publik.

## Verifikasi otomatis

- `python -m compileall -q engine`: lulus.
- `python engine/promo_names.py`: lulus.
- `python engine/promo_multisource_collector.py --self-test`: lulus.
- `python -m pytest -q engine`: 124 passed.
- Regenerasi katalog lokal tanpa scraping live: sukses.
- Regenerasi daftar harga pasar manual: sukses (134 produk yang belum memiliki harga normal bersumber, setelah menyaring di luar katalog, status review, dan nama generik).

## Batasan yang tersisa

- Pembacaan angka dari poster buram, kecil, atau tata letaknya padat perlu peninjauan manual.
- OCR belum dapat membuktikan secara visual bahwa garis coret benar-benar menandai harga asli; konteks label/klaim membantu, keputusan akhir tetap pada reviewer.
- Harga OCR hanya diterapkan jika sumber, URL artikel, dan identitas produk cocok serta nilai tidak bertentangan dengan harga sumber yang sudah ada.
- Database pembanding harga pasar kosong, sehingga klaim “lebih murah dari pasar” belum dapat dihitung.
- Sumber aktif pada paket tetap Hemat.id, Katalogpromosi.com, dan Scanharga.com; Momsmoney/Kontan belum aktif.
