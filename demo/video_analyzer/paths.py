"""Stable workspace paths, independent of launch directory."""
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1]
ROOT = DEMO.parent
MAX_VIDEO = 30 * 1024 * 1024
MAX_VIDEO_SECONDS = 120
MAX_MODEL_REQUEST = 48 * 1024 * 1024  # Base64 expands 30 MiB to 40 MiB, plus prompts and JSON.
