@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  [HATA] .venv bulunamadi -- kurulum henuz yapilmamis.
    echo.
    echo  Bu makinede ilk kez calistiriyorsan, once PowerShell'de sirayla su komutlari calistir:
    echo.
    echo    python -m venv .venv
    echo    .venv\Scripts\pip install opencv-contrib-python numpy Pillow
    echo.
    echo  Detayli kurulum adimlari icin README.md dosyasina bak.
    echo.
    pause
    exit /b 1
)

.venv\Scripts\python.exe cnc_prep_gui.py
if errorlevel 1 (
    echo.
    echo  [HATA] Program bir hatayla kapandi -- yukaridaki mesaji README.md'nin
    echo  "Sorun Giderme" bolumuyle karsilastir.
    echo.
)
pause
