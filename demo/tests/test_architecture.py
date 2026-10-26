"""Architecture/compatibility checks; no experiments, media changes or model calls."""
import importlib
import hashlib
import json
import os
import re
import socket
import shutil
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen
from urllib.error import HTTPError
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from video_analyzer import application
from video_analyzer.paths import DEMO, ROOT
from video_analyzer.web.assets import STATIC_ASSETS


from test_support import MockModelTestCase


class ArchitectureChecks(MockModelTestCase):
    def test_settings_panel_without_inference(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js not available')
        result = subprocess.run([node, str(DEMO/'tests/frontend/settings_panel.js')], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

    def test_token_usage_display_without_inference(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js not available')
        result = subprocess.run([node, str(DEMO/'tests/frontend/token_usage.js')],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

    def test_six_view_navigation_without_inference(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js not available')
        result = subprocess.run([node, str(DEMO/'tests/frontend/workbench_navigation.js')],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

    def test_model_payloads_match_preorganization_snapshots(self):
        expected=json.loads((Path(__file__).parent/'fixtures/request_payload_hashes.json').read_text(encoding='utf-8'))
        frames=[{'time_s':0.5,'data':'aW1hZ2U='},{'time_s':2.0,'data':'cGljdHVyZQ=='}]
        with patch.dict(os.environ,{'BEEROUTE_BASE_URL':'https://gateway.example/v1','DASHSCOPE_BASE_URL':'https://qwen.example/v1'}):
            for case,digest in expected.items():
                provider,model,mode=case.split('|')
                with self.subTest(case=case),patch.dict(app.MODELS,{model:provider},clear=True):
                    value=app.build_request(model,mode,2.0,'dmlkZW8=',frames,{'duration_s':3.125,'has_audio':True},'FORMAT CONTRACT')
                    actual=hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                    self.assertEqual(actual,digest)

    def test_both_launchers_work_outside_repository_without_model_calls(self):
        for entry in ('app.py','start.py'):
            with self.subTest(entry=entry),tempfile.TemporaryDirectory() as folder:
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                process=subprocess.Popen([sys.executable,str(DEMO/entry)],cwd=folder,
                    env={**os.environ,'PORT':str(port)},stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                try:
                    deadline=time.monotonic()+6
                    while time.monotonic()<deadline:
                        try:
                            with urlopen(f'http://127.0.0.1:{port}/assets/js/app.js',timeout=1) as response:
                                self.assertIn(b'point.time_s',response.read())
                            break
                        except OSError:
                            if process.poll() is not None:
                                self.fail(process.stderr.read().decode(errors='replace'))
                            time.sleep(0.05)
                    else:self.fail('Local launcher did not become ready')
                finally:
                    process.terminate();process.communicate(timeout=4)

    def test_tool_entrypoints_offer_help_without_running_experiments(self):
        paths=('evaluation/evaluate_v20_batch.py','evaluation/analyze_v20_batch.py',
               'evaluation/day3_evaluate.py','recovery/revalidate_opportunities.py')
        with tempfile.TemporaryDirectory() as folder:
            for relative in paths:
                with self.subTest(tool=relative):
                    result=subprocess.run([sys.executable,str(DEMO/'tools'/relative),'--help'],cwd=folder,
                        capture_output=True,timeout=8)
                    self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
                    self.assertIn(b'usage:',result.stdout)

    def test_compatibility_imports_share_state(self):
        self.assertIs(app, application)
        for old, new in (
            ('projects', 'video_analyzer.projects.store'),
            ('output_contract', 'video_analyzer.contracts.output'),
            ('gameplay_design', 'video_analyzer.gameplay.legacy_design'),
            ('gameplay_recommendations', 'video_analyzer.gameplay.recommendations'),
        ):
            self.assertIs(importlib.import_module(old), importlib.import_module(new))
        self.assertEqual(app.ROOT, ROOT)
        self.assertEqual(app.DEMO, DEMO)
        self.assertTrue((ROOT/'prompts/games/catalog.json').is_file())

    def test_frontend_paths_and_script_order(self):
        page=(DEMO/'web/index.html').read_text(encoding='utf-8')
        scripts=re.findall(r'<script src="([^"]+)"',page)
        self.assertEqual(scripts,[
            '/assets/js/token-usage.js',
            '/assets/js/settings-panel.js',
            '/assets/js/gameplay/legacy-design.js','/assets/js/gameplay/recommendations.js',
            '/assets/js/game-workbench.js','/assets/js/workbench.js','/assets/js/app.js'])
        self.assertNotIn('<script>',page)
        self.assertNotIn('<style>',page)
        for url in re.findall(r'(?:src|href)="(/assets/[^"]+)"',page):
            self.assertIn(url,STATIC_ASSETS)
            self.assertTrue((DEMO/'web'/STATIC_ASSETS[url][0]).is_file())

    def test_http_assets_and_legacy_urls_are_identical(self):
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            for url,(relative,mime) in STATIC_ASSETS.items():
                with self.subTest(url=url),urlopen(base+url,timeout=3) as response:
                    self.assertEqual(response.headers['Content-Type'],mime)
                    self.assertEqual(response.read(),(DEMO/'web'/relative).read_bytes())
            with self.assertRaises(HTTPError) as caught:urlopen(base+'/assets/../app.py',timeout=3)
            self.assertEqual(caught.exception.code,404)
            with urlopen(base+'/config',timeout=3) as response:
                config=json.load(response)
            self.assertIn('stage_prompts',config)
        finally:
            server.shutdown();server.server_close();worker.join()
