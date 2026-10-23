"""Compatibility import; implementation lives in video_analyzer.contracts.output."""
import sys
from video_analyzer.contracts import output as _implementation

sys.modules[__name__] = _implementation
