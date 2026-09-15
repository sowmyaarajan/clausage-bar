"""Console-less launcher for the big floating indicator.

Put a shortcut to this in shell:startup alongside clausage_bar.lnk to have it
appear at logon, or launch it from the tray menu.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from clausage_bar.widget import main

if __name__ == "__main__":
    raise SystemExit(main())
