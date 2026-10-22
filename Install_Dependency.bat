@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHON=python"
where py >nul 2>nul
if not errorlevel 1 set "PYTHON=py -3"
echo Memasang dependensi pipeline promo...
%PYTHON% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo.
  echo Instalasi gagal. Pastikan Python dan pip sudah terpasang.
  pause
  exit /b 1
)
echo.
echo Dependensi berhasil diperiksa/dipasang.
pause
