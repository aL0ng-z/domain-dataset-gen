import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "libs" / "parsing"))
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
