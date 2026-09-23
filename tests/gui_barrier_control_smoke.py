"""Manual smoke for the simplified UI; does not write to AutoCAD."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui_control_smoke import main


if __name__ == "__main__":
    main()
