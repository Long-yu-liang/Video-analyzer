"""Compatibility import; implementation lives in video_analyzer.gameplay.legacy_design."""
import sys
from video_analyzer.gameplay import legacy_design as _implementation

sys.modules[__name__] = _implementation
