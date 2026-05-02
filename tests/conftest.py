import sys
from pathlib import Path

# Make repo root importable so `from services...` works in tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
