@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0Jalankan_Promo.bat" --test-only
exit /b %ERRORLEVEL%
