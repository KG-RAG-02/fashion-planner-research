"""Restore offline fixtures, then discover root, sales and market tests once."""
from pathlib import Path
import subprocess
import sys
from restore_test_fixtures import main
if __name__ == '__main__':
    main()
    root=Path(__file__).resolve().parents[1]
    # sales/ and market/ are now packages, so root discovery includes both.
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover',
                             '-s', 'tests', '-v'], cwd=root)
    raise SystemExit(result.returncode)
