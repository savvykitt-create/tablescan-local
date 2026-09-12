@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PIP_NO_CACHE_DIR=1
title TableScan Local - Slow mode setup
echo TableScan Local - optional offline slow mode
echo.
echo Requires Python 3.12 (64-bit) and internet during installation.
echo CPU: at least 16 GB RAM; 24 GB or more recommended; processing can take a long time.
echo NVIDIA: updated driver and sufficient VRAM; Auto can fall back to CPU.
echo Allow 15 GB free disk space for models and dependencies.
echo Documents are never uploaded.
echo Close TableScan before installing or repairing this module.
echo.
py -3.12 -c "import sys; assert sys.maxsize > 2**32" >nul 2>&1
if errorlevel 1 (
  echo Install 64-bit Python 3.12 from https://www.python.org/downloads/windows/
  echo Include the Python launcher, then run this shortcut again.
  pause
  exit /b 1
)
echo 1. Auto: NVIDIA if available, otherwise CPU
echo 2. CPU only: smaller dependency download
echo 3. NVIDIA CUDA only
choice /c 123 /n /m "Choose mode [1-3]: "
set "DEVICE=auto"
if errorlevel 3 (set "DEVICE=cuda") else if errorlevel 2 (set "DEVICE=cpu")
py -3.12 "%~dp0install_slow_mode.py" --backend transformers --device %DEVICE%
if errorlevel 1 (
  echo Installation failed. Details are shown above; you can rerun this setup.
  pause
  exit /b 1
)
echo.
echo Testing both models locally. This may take several minutes on CPU.
start "" /wait "%~dp0..\TableScanLocal.exe" --slow-mode-self-test
if errorlevel 1 (
  echo Models installed, but the test failed. Diagnostics:
  echo "%LOCALAPPDATA%\TableScan Local\slow-mode\self-test\result.json"
  pause
  exit /b 1
)
echo Slow mode is ready. Open TableScan and enable Slow mode in Alignment.
pause
