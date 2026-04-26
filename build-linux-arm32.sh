#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="$ROOT_DIR/.venv-printer-build-arm32"

ARCH="$("$PYTHON_BIN" -c 'import platform; print(platform.machine())')"
POINTER_BITS="$("$PYTHON_BIN" -c 'import struct; print(struct.calcsize("P") * 8)')"

if ! "$PYTHON_BIN" -m venv --help >/dev/null 2>&1; then
  echo "python3-venv is required. Install it with: sudo apt install python3-venv"
  exit 1
fi

case "$ARCH" in
  armv7l|armv8l|aarch64|armhf|arm)
    ARCH_OK=1
    ;;
  *)
    ARCH_OK=0
    ;;
esac

if [ "$ARCH_OK" != "1" ] || [ "$POINTER_BITS" != "32" ]; then
  echo "This script requires a 32-bit ARM Python environment."
  echo "Detected architecture: $ARCH"
  echo "Detected Python pointer width: ${POINTER_BITS}-bit"
  echo "Use printerService/build-linux.sh for native 64-bit builds,"
  echo "or run this script inside a 32-bit Raspberry Pi OS / 32-bit userspace."
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r printerService/requirements.txt

"$VENV_DIR/bin/python" -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --windowed \
  --name OrderSystemPrinterService \
  printerService/main.py

echo "Build complete: $ROOT_DIR/dist/OrderSystemPrinterService"
echo "Python userspace: ${POINTER_BITS}-bit"
echo "Kernel-reported architecture: $ARCH"
