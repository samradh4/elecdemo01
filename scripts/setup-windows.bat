@echo off
cd /d %~dp0\..
echo Installing frontend packages...
call npm install
py -3 -m venv backend\.venv
call backend\.venv\Scripts\activate.bat
python -m pip install -U pip
python -m pip install -r backend\requirements.txt
echo.
echo IMPORTANT: Install Tesseract OCR for Windows and include English + Hindi language data.
echo Typical path: C:\Program Files\Tesseract-OCR\tesseract.exe
echo.
echo Setup complete. Run scripts\run-windows.bat
