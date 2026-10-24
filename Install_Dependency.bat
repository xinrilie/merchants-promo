@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul

echo =====================================================
echo INSTALASI DEPENDENSI KATALOG PROMO
echo =====================================================

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

%PYTHON_CMD% -m pip install -r "%~dp0requirements-dev.txt"
if errorlevel 1 goto :error

%PYTHON_CMD% -c "import sys, requests, bs4, openpyxl, pytest; print('Python:', sys.version.split()[0]); print('requests:', requests.__version__); print('beautifulsoup4: OK'); print('openpyxl:', openpyxl.__version__); print('pytest:', pytest.__version__)"
if errorlevel 1 goto :error

echo.
echo Dependensi berhasil dipasang.
echo Selanjutnya jalankan Jalankan_Promo.bat --test-only untuk memeriksa proyek.
pause
exit /b 0

:no_python
echo ERROR: Python tidak ditemukan.
echo Pasang Python 3.10+ dari python.org dan aktifkan Add Python to PATH.
pause
exit /b 1

:python_version_error
echo ERROR: Python 3.10 atau lebih baru diperlukan.
%PYTHON_CMD% --version
pause
exit /b 1

:error
echo.
echo Instalasi gagal. Pastikan internet dan pip tersedia.
pause
exit /b 1
