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
echo KATALOG PROMO - PIPELINE RINGKAS V4
echo Sumber: Hemat.id, Katalogpromosi.com, Scanharga.com
echo =====================================================
echo.
echo [1/4] Uji normalisasi nama produk...
%PYTHON% "%~dp0engine\promo_names.py"
if errorlevel 1 goto :error

echo.
echo [2/4] Uji konsolidasi produk lintas sumber...
%PYTHON% "%~dp0engine\test_duplicate_fixes.py"
if errorlevel 1 goto :error

echo.
echo [3/4] Uji parser lalu scraping situs publik...
%PYTHON% "%~dp0engine\promo_multisource_collector.py" --self-test
if errorlevel 1 goto :error
%PYTHON% "%~dp0engine\promo_multisource_collector.py"
if errorlevel 1 goto :error

echo.
echo [4/4] Membersihkan data, menggabungkan penawaran identik, dan membuat katalog...
%PYTHON% "%~dp0engine\enrich_existing_promo_catalog_multisource.py"
if errorlevel 1 goto :error

echo.
echo SELESAI. File utama ada di folder hasil\
echo Buka: hasil\Katalog_Promo_Final.xlsx
if exist "%~dp0hasil\Katalog_Promo_Final.xlsx" start "" "%~dp0hasil\Katalog_Promo_Final.xlsx"
echo File mentah dan log teknis ada di _internal\.
pause
exit /b 0

:error
echo.
echo Pipeline berhenti karena error. Periksa pesan di atas atau log di _internal\ jika ada.
pause
exit /b 1
