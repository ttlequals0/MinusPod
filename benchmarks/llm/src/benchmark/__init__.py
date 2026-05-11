"""MinusPod offline LLM ad-detection benchmark."""
import sys
from pathlib import Path

_minuspod_src = Path(__file__).resolve().parents[3].parent / "src"
if _minuspod_src.is_dir() and str(_minuspod_src) not in sys.path:
    sys.path.insert(0, str(_minuspod_src))

__version__ = "0.1.0"
