"""Synthetic partial failure UI. No model calls or production records/settings writes."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from video_analyzer import application as app, settings

SOURCE = app.ROOT
SAMPLE = 'S07_篮球比赛_前58秒.mp4'


class Fixture(app.Handler):
    def do_POST(self):
        if urlparse(self.path).path != '/events':
            return self.respond(400, {'error': 'Fixture: no cloud calls allowed'})
        if self.headers.get('X-Local-Token') != app.TOKEN:
            return self.respond(403, {'error': 'fixture token required'})
        length = int(self.headers.get('Content-Length', '0'))
        self.rfile.read(length)
        return self.respond(200, {
            'stage': 'events', 'status': 'failed', 'record_file': 'LOCAL-FIXTURE-NOT-SAVED.json',
            'model': 'local-fixture', 'total_s': 0, 'error': '模拟：第二段核心时间越界（未调用模型）',
            'segment_coverage': {'complete': False, 'ranges': [
                {'start_s': 0, 'end_s': 20, 'status': 'success'},
                {'start_s': 20, 'end_s': 40, 'status': 'failed'},
                {'start_s': 40, 'end_s': 58, 'status': 'unprocessed'}]},
            'segments': [{'core_start_s': 0, 'core_end_s': 20, 'status': 'success',
                          'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}},
                         {'core_start_s': 20, 'core_end_s': 40, 'status': 'failed',
                          'error': '模拟：events[0].observation_s 超出片长',
                          'usage': {'prompt_tokens': 80, 'completion_tokens': 10, 'total_tokens': 90}}],
            'validation_warnings': [{'message': '模拟：缺少 limitations，兼容为空数组，原文未改。'}],
            'partial_analysis': {'summary': '本地模拟数据，仅展示已完成的 0–20 秒，不是模型评测。',
                'events': [{'observation_id': 'e1', 'start_s': 1, 'end_s': 2, 'observation_s': 1.5,
                            'description': '测试事件（非事实评估）', 'evidence': '夹具画面说明', 'uncertainty': '待回看'}],
                'limitations': ['全片未完成，不能用于第二步。']}})


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='video-partial-events-') as folder:
        app.ROOT = Path(folder)
        shutil.copytree(SOURCE / 'prompts', app.ROOT / 'prompts')
        settings.Store(app.ROOT).save({'revision': 0, 'values': {'BEEROUTE_API_KEY': 'fixture-not-real'}})
        app.list_samples = lambda: [SAMPLE]
        app.read_sample = lambda name: (SOURCE / 'mp4-example' / SAMPLE).read_bytes() if name == SAMPLE else b''
        server = ThreadingHTTPServer(('127.0.0.1', 8773), Fixture)
        print('Synthetic partial-events fixture http://127.0.0.1:8773', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
