"""Make the source-layout package importable from either checkout entry point."""
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
