"""Console-less launcher. This is what the Startup shortcut points at."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from clausage_bar.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
