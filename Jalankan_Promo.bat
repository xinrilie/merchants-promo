@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHON=python"
where py >nul 2>nul
if not errorlevel 1 set "PYTHON=py -3"

%PYTHON% -c "import requests, bs4, openpyxl" >nul 2>nul
if errorlevel 1 (
  echo Dependensi belum lengkap. Memasang otomatis...
  %PYTHON% -m pip install -r "%~dp0requirements.txt"
  if errorlevel 1 goto :error
)

echo =====================================================
echo KATALOG PROMO MULTI-SUMBER - v10
echo Tahap: Momsmoney + 3 agregator > validasi produk > fuzzy lintas sumber > final catalog
echo Sumber live: Momsmoney/Kontan, Hemat.id, Katalogpromosi.com, Scanharga.com
echo =====================================================
echo.
echo [1/6] Uji normalisasi nama produk...
%PYTHON% "%~dp0engine\promo_names.py"
if errorlevel 1 goto :error

echo.
echo [2/6] Uji parser dan guardrails collector...
%PYTHON% "%~dp0engine\promo_multisource_collector.py" --self-test
if errorlevel 1 goto :error

echo.
echo [3/6] Uji regresi pipeline, judul artikel, dan fuzzy matching...
%PYTHON% "%~dp0engine\test_pipeline_v10.py"
if errorlevel 1 goto :error

echo.
echo [4/6] Mengambil artikel promo Momsmoney/Kontan...
%PYTHON% "%~dp0engine\promo_catalog_all_merchants_v2.py"
if errorlevel 1 goto :error

echo.
echo [5/6] Mengambil promo Hemat.id, Katalogpromosi.com, dan Scanharga.com...
%PYTHON% "%~dp0engine\promo_multisource_collector.py"
if errorlevel 1 goto :error

echo.
echo [6/6] Menggabungkan 4 sumber live + katalog baseline, validasi, fuzzy-match, ekspor...
%PYTHON% "%~dp0engine\enrich_existing_promo_catalog_multisource.py"
if errorlevel 1 goto :error

echo.
echo SELESAI. Buka hasil\Katalog_Promo_Final.xlsx
echo CSV ringkas: hasil\Katalog_Promo_Final.csv
echo Log/audit: _internal\
if exist "%~dp0hasil\Katalog_Promo_Final.xlsx" start "" "%~dp0hasil\Katalog_Promo_Final.xlsx"
pause
exit /b 0

:error
echo.
echo Pipeline berhenti karena error. Baca pesan di atas dan periksa _internal\ untuk log.
pause
exit /b 1
