#!/bin/bash
set -e
cd "$(dirname "$0")/.."
if [ ! -x backend/.venv/bin/python ]; then
  echo "Environment not installed. Running setup first..."
  ./scripts/setup-mac.sh
fi
exec backend/.venv/bin/python scripts/launcher.py
