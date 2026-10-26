"""Settings UI on a temporary root; all cloud calls are blocked."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from video_analyzer import application as app
from video_analyzer.gameplay import generation


def forbidden(*args, **kwargs):
    raise AssertionError('Cloud calls forbidden in settings fixture')


if __name__ == '__main__':
    for key in ('BEEROUTE_API_KEY', 'MINIMAX_API_KEY', 'DASHSCOPE_API_KEY', 'GEMINI_API_KEY'):
        os.environ[key] = ''
    with tempfile.TemporaryDirectory(prefix='video-settings-fixture-') as folder:
        root = Path(folder)
        shutil.copytree(app.ROOT / 'prompts', root / 'prompts')
        app.ROOT = root
        app.urlopen = generation.urlopen = forbidden
        server = ThreadingHTTPServer(('127.0.0.1', 8772), app.Handler)
        print('Settings fixture http://127.0.0.1:8772 (temporary data, no model calls)', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
