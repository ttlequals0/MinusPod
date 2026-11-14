"""Standalone CLI tools that import from the app's src/ tree.

Importing this package puts `src/` on sys.path so scripts work via
`python -m src.tools.X` or `python src/tools/X.py`.
"""
import sys
from pathlib import Path

_REPO_SRC = Path(__file__).resolve().parents[1]
if str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))
