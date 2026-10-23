"""Compatibility import; implementation lives in video_analyzer.gameplay.recommendations."""
import sys
from video_analyzer.gameplay import recommendations as _implementation

sys.modules[__name__] = _implementation
