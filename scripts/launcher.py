from __future__ import annotations
import sys, time, shutil, subprocess, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'backend'
HEALTH = 'http://127.0.0.1:8000/health'

def wait_for_backend(proc: subprocess.Popen, seconds: int = 30) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(HEALTH, timeout=1.5) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False

def main() -> int:
    print('Starting Constituency Manager backend...')
    backend = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'main:app', '--host', '0.0.0.0', '--port', '8000'],
        cwd=str(BACKEND),
    )
    try:
        if not wait_for_backend(backend):
            print('\nERROR: Backend did not become healthy. Check the backend error shown above.')
            return 1
        print('Backend ready: http://127.0.0.1:8000')
        npx = shutil.which('npx') or shutil.which('npx.cmd')
        if not npx:
            print('ERROR: npx was not found. Install Node.js LTS, then run setup again.')
            return 1
        print('Starting web app...')
        frontend = subprocess.Popen([npx, 'expo', 'start', '--web', '-c'], cwd=str(ROOT))
        return frontend.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        if backend.poll() is None:
            backend.terminate()
            try:
                backend.wait(timeout=5)
            except subprocess.TimeoutExpired:
                backend.kill()

if __name__ == '__main__':
    raise SystemExit(main())
