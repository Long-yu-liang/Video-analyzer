"""Compatibility import; implementation lives in video_analyzer.projects.store."""
import sys
from video_analyzer.projects import store as _implementation

sys.modules[__name__] = _implementation
