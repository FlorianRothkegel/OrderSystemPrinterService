#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR="$ROOT_DIR/.venv-printer-build"

if ! python3 -m venv --help >/dev/null 2>&1; then
  echo "python3-venv is required. Install it with: sudo apt install python3-venv"
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
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
