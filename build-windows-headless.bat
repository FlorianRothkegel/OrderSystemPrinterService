@echo off
setlocal

cd /d "%~dp0\.."

if not defined PYTHON_CMD (
  set "PYTHON_CMD="
  where py >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_CMD=py -3.12"
  )
)
if not defined PYTHON_CMD (
  where python >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_CMD=python"
  )
)
if not defined PYTHON_CMD (
  echo Python 3.12 was not found on PATH. Install it for all users or expose it to the NSSM service account.
  exit /b 1
)

%PYTHON_CMD% -m pip install -r printerService\requirements.txt
if errorlevel 1 exit /b %errorlevel%

%PYTHON_CMD% -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --name OrderSystemPrinterServiceHeadless ^
  printerService\headless.py
if errorlevel 1 exit /b %errorlevel%

echo Build complete: %CD%\dist\OrderSystemPrinterServiceHeadless.exe
