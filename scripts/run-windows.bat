@echo off
cd /d %~dp0\..
if not exist backend\.venv\Scripts\python.exe (
  echo Environment not installed. Running setup first...
  call scripts\setup-windows.bat
  if errorlevel 1 exit /b 1
)
backend\.venv\Scripts\python.exe scripts\launcher.py
