#!/bin/bash
set -e
cd "$(dirname "$0")/.."
echo "Installing frontend packages..."
npm install
if ! command -v python3 >/dev/null 2>&1; then echo "Python 3 is required"; exit 1; fi
if ! command -v brew >/dev/null 2>&1; then echo "Homebrew is recommended for Tesseract: https://brew.sh"; else brew list tesseract >/dev/null 2>&1 || brew install tesseract tesseract-lang; fi
python3 -m venv backend/.venv
source backend/.venv/bin/activate
python3 -m pip install -U pip
python3 -m pip install -r backend/requirements.txt
echo "Setup complete. Run ./scripts/run-mac.sh"
