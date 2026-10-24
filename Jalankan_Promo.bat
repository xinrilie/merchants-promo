@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul

REM =====================================================
REM KATALOG PROMO MULTI-SUMBER - LAUNCHER WINDOWS
REM Default: tes + scraping live + konsolidasi + ekspor
REM Offline checks only: Jalankan_Promo.bat --test-only
REM =====================================================

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
) else (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD goto :no_python

%PYTHON_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 goto :python_version_error

if not exist "%~dp0engine\promo_catalog_all_merchants.py" goto :bad_project
if not exist "%~dp0engine\promo_multisource_collector.py" goto :bad_project
if not exist "%~dp0engine\enrich_existing_promo_catalog_multisource.py" goto :bad_project

if not exist "%~dp0_internal" mkdir "%~dp0_internal"
if not exist "%~dp0hasil" mkdir "%~dp0hasil"

%PYTHON_CMD% -c "import requests, bs4, openpyxl, pytest" >nul 2>nul
if errorlevel 1 (
    echo Dependensi belum lengkap. Memasang requirements-dev.txt...
    %PYTHON_CMD% -m pip install -r "%~dp0requirements-dev.txt"
    if errorlevel 1 goto :dependency_error
)

%PYTHON_CMD% -c "import requests, bs4, openpyxl, pytest" >nul 2>nul
if errorlevel 1 goto :dependency_error

if /I "%~1"=="--help" goto :help

set "MODE=full"
if /I "%~1"=="--test-only" set "MODE=test"

echo.
echo =====================================================
echo KATALOG PROMO MULTI-SUMBER
echo Folder: %CD%
echo Python runtime: %PYTHON_CMD%
echo Mode: %MODE%
echo =====================================================
echo.

echo [PRA-TEST 1/4] Memeriksa sintaks seluruh engine...
%PYTHON_CMD% -m compileall -q "%~dp0engine"
if errorlevel 1 goto :error

echo.
echo [PRA-TEST 2/4] Self-test normalisasi nama produk...
%PYTHON_CMD% -u "%~dp0engine\promo_names.py"
if errorlevel 1 goto :error

echo.
echo [PRA-TEST 3/4] Self-test parser dan guardrails collector...
%PYTHON_CMD% -u "%~dp0engine\promo_multisource_collector.py" --self-test
if errorlevel 1 goto :error

echo.
echo [PRA-TEST 4/4] Menjalankan tes regresi...
%PYTHON_CMD% -m pytest -q "%~dp0engine"
if errorlevel 1 goto :error

if /I "%MODE%"=="test" goto :test_success

echo.
echo =====================================================
echo TES LULUS. MULAI SCRAPING LIVE
echo Sumber: Momsmoney/Kontan, Hemat.id, Katalogpromosi.com,
echo         Scanharga.com
echo =====================================================
echo.

echo [LIVE 1/3] Mengambil artikel promo Momsmoney/Kontan...
%PYTHON_CMD% -u "%~dp0engine\promo_catalog_all_merchants.py"
if errorlevel 1 goto :error

echo.
echo [LIVE 2/3] Mengambil promo agregator multi-sumber...
%PYTHON_CMD% -u "%~dp0engine\promo_multisource_collector.py"
if errorlevel 1 goto :error

echo.
echo [LIVE 3/3] Menggabungkan sumber, memvalidasi, fuzzy matching, dan ekspor...
%PYTHON_CMD% -u "%~dp0engine\enrich_existing_promo_catalog_multisource.py"
if errorlevel 1 goto :error

echo.
echo Verifikasi file hasil...
%PYTHON_CMD% -c "from pathlib import Path; from openpyxl import load_workbook; x=Path(r'%~dp0hasil\Katalog_Promo_Final.xlsx'); c=Path(r'%~dp0hasil\Katalog_Promo_Final.csv'); assert x.is_file() and x.stat().st_size>0, 'Workbook tidak ditemukan/kosong'; assert c.is_file() and c.stat().st_size>0, 'CSV tidak ditemukan/kosong'; w=load_workbook(x, read_only=True, data_only=True); print('Workbook OK:', x.name); print('Sheet:', ', '.join(w.sheetnames)); w.close(); print('CSV OK:', c.name, '(' + str(c.stat().st_size) + ' bytes)')"
if errorlevel 1 goto :error

echo.
echo =====================================================
echo SELESAI: KATALOG BERHASIL DIBUAT
echo Excel: hasil\Katalog_Promo_Final.xlsx
echo CSV  : hasil\Katalog_Promo_Final.csv
echo Audit: _internal\
echo Tutup workbook/CSV di Excel sebelum menjalankan ulang pipeline.
echo =====================================================
pause
exit /b 0

:test_success
echo.
echo =====================================================
echo TES OFFLINE BERHASIL
echo Tidak ada scraping live yang dijalankan.
echo Untuk pipeline lengkap, jalankan Jalankan_Promo.bat tanpa argumen.
echo =====================================================
pause
exit /b 0

:no_python
echo ERROR: Python tidak ditemukan.
echo Pasang Python 3.10+ dari python.org, aktifkan Add Python to PATH,
echo lalu buka terminal baru dan jalankan file ini kembali.
pause
exit /b 1

:python_version_error
echo ERROR: Python 3.10 atau lebih baru diperlukan.
%PYTHON_CMD% --version
pause
exit /b 1

:bad_project
echo ERROR: Struktur proyek tidak lengkap.
echo Ekstrak semua isi ZIP ke satu folder. Jangan pindahkan file BAT sendirian.
pause
exit /b 1

:dependency_error
echo ERROR: Dependensi belum berhasil dipasang/diverifikasi.
echo Coba jalankan Install_Dependency.bat, lalu ulangi.
pause
exit /b 1

:error
echo.
echo =====================================================
echo PIPELINE BERHENTI KARENA ERROR
echo Baca pesan di atas dan periksa file log di _internal.
echo Katalog hasil terakhir yang valid tidak perlu dihapus manual.
echo =====================================================
pause
exit /b 1

:help
echo Pemakaian:
echo   Jalankan_Promo.bat             Tes lalu jalankan scraping live dan ekspor
echo   Jalankan_Promo.bat --test-only Tes lokal tanpa mengakses sumber live
echo   Jalankan_Promo.bat --help      Tampilkan bantuan
exit /b 0
