@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHON=python"
where py >nul 2>nul
if not errorlevel 1 set "PYTHON=py -3"
echo =====================================================
echo INSTALASI DEPENDENSI KATALOG PROMO v9
echo =====================================================
%PYTHON% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto :error
echo.
echo Dependensi terpasang.
pause
exit /b 0
:error
echo.
echo Instalasi gagal. Pastikan Python 3.10+ dan pip telah terpasang.
pause
exit /b 1
