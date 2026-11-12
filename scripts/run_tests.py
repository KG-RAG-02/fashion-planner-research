"""Deterministic offline fixture restoration and three explicit discovery roots."""
from pathlib import Path
import subprocess
import sys
from restore_test_fixtures import main
if __name__ == '__main__':
    main()
    root=Path(__file__).resolve().parents[1]
    failed=False
    for suite in ['tests','tests/sales','tests/market']:
        failed |= bool(subprocess.run([sys.executable,'-m','unittest','discover','-s',suite,'-v'],cwd=root).returncode)
    raise SystemExit(int(failed))
