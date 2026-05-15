"""Standalone CLI tools that import from the app's src/ tree.

Importing this package also wires `src/` onto sys.path so the scripts work
when invoked as `python -m src.tools.X` (workflow style) or directly as
`python src/tools/X.py` (manual). Each script just does `import tools` (or
relies on relative imports from `src.tools.X`); no per-script bootstrap.
"""
import sys
from pathlib import Path

_REPO_SRC = Path(__file__).resolve().parents[1]
if str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))
