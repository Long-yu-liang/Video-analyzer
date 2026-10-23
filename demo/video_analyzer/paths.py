"""Stable workspace paths, independent of launch directory."""
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1]
ROOT = DEMO.parent
MAX_VIDEO = 12 * 1024 * 1024
