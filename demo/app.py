"""Stable launcher and compatibility facade for the reorganized backend."""
from pathlib import Path
import sys

_demo = str(Path(__file__).resolve().parent)
if _demo not in sys.path:
    sys.path.insert(0, _demo)
from video_analyzer import application as _application

if __name__ == '__main__':
    _application.main()
else:
    # Preserve existing imports and patch points; no duplicate runtime/configuration state.
    sys.modules[__name__] = _application
