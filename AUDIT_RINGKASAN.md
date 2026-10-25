# Ringkasan Audit Final — Katalog Promo Multi-Sumber

Tanggal snapshot data: **25 Oktober 2026**. Paket dibangun dari file data collector yang sudah tersimpan; proses pemaketan ini tidak menjalankan scraping live.

## Status akhir

- **97 tes lulus**, termasuk regresi parser diskon nominal. `compileall`, self-test normalisasi nama, dan self-test collector juga lulus.
- `hasil/Katalog_Promo_Final.xlsx` berhasil dibuat ulang dan dibuka untuk verifikasi struktur.
- Sheet yang ditujukan untuk pemakaian harian: `Ringkasan`, `Promo`, `Perlu Diperiksa`, dan `Ringkasan Kategori`. Pada workbook lampiran perbaikan ini, semua 16 tab terlihat karena metadata hidden tidak dipertahankan saat ekspor perbaikan; seluruh data audit tetap ada. Pipeline proyek yang dijalankan ulang memakai kode yang telah diperbaiki menerapkan pengaturan tampilan normal.
- Sheet `Promo` memiliki **455 baris promo**, menggunakan 13 kolom. Urutan kolom `Diskon (%)` diikuti langsung oleh `Masa Berlaku`.
- Sel `Diskon (%)`: **380 nilai numerik**, **75 bertanda `Belum bisa dihitung`**, dan **0 sel kosong**.
- Pemeriksaan promo dengan teks potongan nominal menemukan nominal seperti `Diskon Rp10.000` ditampilkan di `Hemat (Rp)`, bukan dianggap sebagai `Harga Promo (Rp)`. Kasus yang dapat dibandingkan dengan rentang acuan satuan yang cocok ditandai jelas sebagai estimasi.

## Perbaikan parser

- Regex untuk label `diskon`, `potongan`, dan `hemat` diperbaiki agar benar-benar mengenali batas kata dan spasi.
- `parse_offer_text()` tidak lagi menganggap `Beli 5 pcs Diskon Rp. 10.000` sebagai produk dengan harga promo Rp10.000; hasilnya menempatkan nominal itu sebagai potongan nominal.
- Fungsi Excel juga memperbaiki baris legacy ketika `promo_price` sebelumnya keliru memuat nilai diskon. Teks `raw_line` yang mengulang `offer_text` tidak membuat nominal yang sama dihitung dua kali.
- Diskon dihitung hanya dari pasangan harga yang dapat dibandingkan atau dari acuan yang sesuai dengan satuan/kondisi; selain itu nilainya diberi status `Belum bisa dihitung`, bukan dibuat-buat.

## Data referensi dan batasan

- Daftar sumber referensi pengguna memuat **102 rentang harga** dan **89 alias katalog terpilih**. Database harga yang dibundel berisi 394 rekaman pada snapshot ini.
- Rentang yang disediakan pengguna adalah **estimasi acuan**, bukan harga resmi merchant. Data dari situs agregator juga bukan verifikasi resmi retailer.
- Harga/diskon berbasis perkiraan tetap diberi penanda pada `Status Diskon`; periksa `Sumber URL`, syarat transaksi, satuan, dan masa berlaku sebelum menganggap penawaran universal.

## File utama

- `hasil/Katalog_Promo_Final.xlsx` — workbook Excel ringkas dengan sheet audit tersembunyi.
- `hasil/Katalog_Promo_Final.csv` — data CSV utama.
- `_internal/` — data antara, log, laporan kualitas ekspor, kandidat review/duplikat, serta audit lengkap.
- `Jalankan_Promo.bat` — jalankan tes dan pipeline lengkap di Windows.
- `Jalankan_Promo.bat --test-only` — tes offline tanpa scraping live.

## Perbaikan kompatibilitas Excel — 25 Oktober 2026

- Penyebab yang diidentifikasi: sheet `Promo` dan `Perlu Diperiksa` menuliskan `AutoFilter` worksheet pada rentang yang sama dengan AutoFilter bawaan Excel Table. Ini membuat Excel menawarkan perbaikan dengan menghapus fitur AutoFilter/tabel saat dibuka.
- Kode pembuat workbook telah diperbaiki: untuk sheet yang menggunakan Excel Table, filter dikelola oleh tabel dan tidak lagi ditulis terpisah ke worksheet.
- Workbook lampiran dibangun ulang dengan dua objek tabel tanpa AutoFilter duplikat. Struktur XLSX tervalidasi, seluruh 16 sheet dan data kunci dapat dibaca kembali, serta arsip XLSX lolos tes integritas.
- Seluruh **97 tes lulus** setelah perubahan kode.



## Tindak lanjut P0 pada paket revisi ini

- HTTP 404/410 dan error HTTP 4xx URL-level kini dicatat terpisah dari pemblokiran sumber; artikel lain tetap diproses. Status 401/403/429, 5xx, request error, dan redirect keluar domain tetap memicu penghentian sumber.
- Ditambahkan `hasil/Katalog_Promo_Final_Ringkas.csv` dengan kolom dan nilai tampilan yang dihasilkan oleh transformasi yang sama dengan sheet Excel `Promo`. CSV utama tetap menjadi ekspor lebih rinci untuk pengolahan data.
- Ditambahkan tes regresi untuk 404 pada satu artikel, kesamaan transformasi CSV ringkas dengan tampilan Excel, serta penolakan harga yang hanya muncul pada judul kampanye tanpa bukti harga di penawaran.
- Verifikasi offline pada revisi: 101 tes otomatis lulus. Ini tidak menggantikan smoke test scraping live.
