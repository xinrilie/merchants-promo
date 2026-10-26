@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul

echo =====================================================
echo INSTALASI DEPENDENSI KATALOG PROMO
echo =====================================================

set "PYTHON_CMD="

REM First, test the user's relocated Python directly. Do not trust `where py` alone:
REM the Python Launcher may still point at an uninstalled old directory.
if exist "D:\Python312\python.exe" (
    "D:\Python312\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD=D:\Python312\python.exe"
        goto :python_found
    )
)

REM Common explicit install locations, including paths with spaces.
if exist "C:\Python312\python.exe" (
    "C:\Python312\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD=C:\Python312\python.exe"
        goto :python_found
    )
)
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (
    "%LocalAppData%\Programs\Python\Python313\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD="%LocalAppData%\Programs\Python\Python313\python.exe""
        goto :python_found
    )
)
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    "%LocalAppData%\Programs\Python\Python312\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD="%LocalAppData%\Programs\Python\Python312\python.exe""
        goto :python_found
    )
)
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    "%LocalAppData%\Programs\Python\Python311\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD="%LocalAppData%\Programs\Python\Python311\python.exe""
        goto :python_found
    )
)
if exist "%ProgramFiles%\Python312\python.exe" (
    "%ProgramFiles%\Python312\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD="%ProgramFiles%\Python312\python.exe""
        goto :python_found
    )
)

REM Probe launcher/command by executing Python, not merely checking whether it exists.
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
    goto :python_found
)
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    goto :python_found
)

goto :no_python

:python_found
%PYTHON_CMD% -m pip install -r "%~dp0requirements-dev.txt"
if errorlevel 1 goto :error

%PYTHON_CMD% -c "import sys, requests, bs4, openpyxl, pytest, cv2, rapidocr, onnxruntime, PIL, pytesseract; print('Python:', sys.version.split()[0]); print('requests:', requests.__version__); print('beautifulsoup4: OK'); print('openpyxl:', openpyxl.__version__); print('pytest:', pytest.__version__); print('RapidOCR: OK'); print('ONNX Runtime:', onnxruntime.__version__); print('OpenCV:', cv2.__version__); print('Pillow:', PIL.__version__); print('pytesseract wrapper: OK; Tesseract executable is optional')"
if errorlevel 1 goto :error

echo.
echo Dependensi berhasil dipasang.
echo Selanjutnya jalankan Jalankan_Promo.bat --test-only untuk memeriksa proyek.
pause
exit /b 0

:no_python
echo ERROR: Python tidak ditemukan.
echo Python 3.10+ tidak ditemukan atau launcher masih menunjuk ke jalur lama.
echo Pastikan D:\Python312\python.exe bisa dijalankan, atau pasang Python 3.10+ dari python.org.
pause
exit /b 1

:error
echo.
echo Instalasi gagal. Pastikan internet dan pip tersedia.
pause
exit /b 1
