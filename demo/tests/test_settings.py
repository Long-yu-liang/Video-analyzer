"""Local settings tests: no cloud requests or production configuration writes."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from video_analyzer import settings, application as app


class SettingsTests(unittest.TestCase):
    def test_private_http_api_addresses_are_supported_but_public_http_is_rejected(self):
        from video_analyzer.models.requests import build_request
        allowed=['http://192.168.6.122:8080/v1','http://10.1.2.3:8080/v1',
                 'http://172.16.0.1/v1','http://127.0.0.1:9000/v1','http://localhost:8080/v1',
                 'http://[::1]:8080/v1','http://[fd00::1]:8080/v1','https://example.com/v1']
        for value in allowed:
            with self.subTest(value=value):self.assertEqual(settings.api_base_url(value),value)
        denied=['http://example.com/v1','http://8.8.8.8/v1','http://172.32.0.1/v1',
                'http://192.0.2.1/v1','ftp://192.168.6.122/v1','http://user:secret@192.168.6.122/v1',
                'http://192.168.6.122:bad/v1','http://192.168.6.122:99999/v1',
                'http://192.168.6.122/v1?key=secret','http://192.168.6.122/v1#fragment']
        for value in denied:
            with self.subTest(value=value),self.assertRaises(ValueError) as caught:settings.api_base_url(value)
            self.assertNotIn('secret',str(caught.exception))
        with tempfile.TemporaryDirectory() as folder:
            store=settings.Store(folder)
            store.save({'revision':0,'values':{'BEEROUTE_BASE_URL':allowed[0],'BEEROUTE_MODELS':'Qwen3.8-Flash-Next'}})
            self.assertEqual(store.public()['values']['BEEROUTE_BASE_URL'],allowed[0])
            with settings.scope(folder):
                url,body,_=build_request('Qwen3.8-Flash-Next','frames',2,'',[],{'duration_s':2,'has_audio':False},'fixture',{'Qwen3.8-Flash-Next':'beeroute'})
            self.assertEqual(url,allowed[0]+'/chat/completions')
            self.assertEqual(body['model'],'Qwen3.8-Flash-Next')

    def test_no_dotenv_and_secret_redaction(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder)
            (root / '.env').write_text('BEEROUTE_API_KEY=do-not-read', encoding='utf-8')
            store = settings.Store(root)
            self.assertEqual(store.read()['values']['BEEROUTE_API_KEY'], '')
            result = store.save({'revision': 0, 'values': {'BEEROUTE_API_KEY': 'fixture-secret'}})
            self.assertNotIn('fixture-secret', json.dumps(result))
            self.assertTrue(result['configured']['BEEROUTE_API_KEY'])
            store.save({'revision': 1, 'values': {'BEEROUTE_API_KEY': ''}})
            self.assertEqual(store.read()['values']['BEEROUTE_API_KEY'], 'fixture-secret')
            self.assertNotIn('BEEROUTE_API_KEY', store.export()['values'])
            self.assertEqual(store.export(True)['values']['BEEROUTE_API_KEY'], 'fixture-secret')
            store.save({'revision': 2, 'values': {}, 'clear_keys': ['BEEROUTE_API_KEY']})
            self.assertFalse(store.public()['configured']['BEEROUTE_API_KEY'])

    def test_validation_conflict_and_atomic_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            store = settings.Store(folder)
            for changes in ({'unknown': ''}, {'MINIMAX_BASE_URL': 'http://example.com'},
                            {'BEEROUTE_MODELS': ''}, {'MINIMAX_MODEL': 'bad\nmodel'}):
                with self.assertRaises(ValueError):
                    store.save({'revision': 0, 'values': changes})
            store.save({'revision': 0, 'values': {}})
            before = store.path.read_bytes()
            with self.assertRaises(settings.SettingsConflict):
                store.save({'revision': 0, 'values': {}})
            with patch.object(settings.os, 'replace', side_effect=OSError('fixture')), self.assertRaises(OSError):
                store.save({'revision': 1, 'values': {'MINIMAX_MODEL': 'new'}})
            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(len(list(store.path.parent.iterdir())), 1)
            store.path.write_text('invalid-json', encoding='utf-8')
            with self.assertRaises(ValueError):
                store.save({'revision': 1, 'values': {}})
            self.assertEqual(store.path.read_text(), 'invalid-json')

    def test_environment_fallback_saved_precedence_and_snapshot(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'MINIMAX_MODEL': 'from-env'}):
            store = settings.Store(folder)
            self.assertEqual(store.read()['values']['MINIMAX_MODEL'], 'from-env')
            store.save({'revision': 0, 'values': {'MINIMAX_MODEL': 'first'}})
            with settings.scope(folder):
                self.assertEqual(settings.get('MINIMAX_MODEL'), 'first')
                worker = threading.Thread(target=lambda: store.save({'revision': 1, 'values': {'MINIMAX_MODEL': 'second'}}))
                worker.start(); worker.join()
                self.assertEqual(settings.get('MINIMAX_MODEL'), 'first')
            with settings.scope(folder):
                self.assertEqual(settings.get('MINIMAX_MODEL'), 'second')

    def test_tools_and_request_builder_use_saved_configuration(self):
        from video_analyzer.models.requests import build_request
        with tempfile.TemporaryDirectory() as folder:
            store = settings.Store(folder)
            store.save({'revision': 0, 'values': {'BEEROUTE_BASE_URL': 'https://fixture.example/v1',
                       'FFMPEG_BINARY': 'trusted-ffmpeg', 'FFPROBE_BINARY': 'trusted-ffprobe', 'NODE_BINARY': 'trusted-node'}})
            with settings.scope(folder):
                url, body, audio = build_request('qwen3-vl-plus', 'frames', 2, '', [],
                    {'duration_s': 2, 'has_audio': False}, 'fixture', {'qwen3-vl-plus': 'beeroute'})
            self.assertEqual(url, 'https://fixture.example/v1/chat/completions')
            self.assertEqual(body['max_tokens'], 16384)
            process = type('Process', (), {'returncode': 0, 'stdout': b'fixture version\n', 'stderr': b''})()
            with patch.object(settings.subprocess, 'run', return_value=process) as run:
                self.assertTrue(all(v['ok'] for v in settings.check_tools(folder).values()))
                self.assertEqual(run.call_args_list[0].args[0], ['trusted-ffmpeg', '-version'])

    def test_authenticated_http_and_live_model_list(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(app, 'ROOT', Path(folder)):
            server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
            base = f'http://127.0.0.1:{server.server_port}'
            def request(path, body=None, token=True):
                return urlopen(Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                    headers={'X-Local-Token': app.TOKEN if token else '', 'Content-Type': 'application/json'}), timeout=3)
            try:
                with self.assertRaises(HTTPError) as caught:
                    request('/settings', token=False)
                self.assertEqual(caught.exception.code, 403)
                with request('/settings', {'revision': 0, 'values': {'BEEROUTE_MODELS': 'fixture-vision', 'BEEROUTE_API_KEY': 'fixture-private',
                             'BEEROUTE_BASE_URL':'http://192.168.6.122:8080/v1'}}) as response:
                    self.assertNotIn('fixture-private', response.read().decode())
                with request('/config') as response:
                    data = json.load(response)
                    self.assertEqual(data['models'], {'fixture-vision': 'beeroute'})
                    self.assertTrue(data['configured']['beeroute'])
                with request('/settings/export') as response:
                    self.assertNotIn('fixture-private', response.read().decode())
                with self.assertRaises(HTTPError) as caught:
                    request('/settings', {'revision': 0, 'values': {}})
                self.assertEqual(caught.exception.code, 409)
            finally:
                server.shutdown(); server.server_close(); worker.join()

    def test_minimax_generation_uses_saved_key_endpoint_and_model(self):
        from video_analyzer.gameplay import generation
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prompt = root / 'prompts/game-generation/v1/generate.txt'
            prompt.parent.mkdir(parents=True)
            prompt.write_text('fixture prompt', encoding='utf-8')
            settings.Store(root).save({'revision': 0, 'values': {'MINIMAX_API_KEY': 'fixture-secret',
                'MINIMAX_MODEL': 'fixture-mini', 'MINIMAX_BASE_URL': 'http://192.168.6.122:8080/v1'}})
            project = {'project_id': 'a'*32, 'revision': 1}
            data = {'point_id': 'p1', 'video': {'name': 'fixture.mp4'}}
            output = {'design': {}, 'javascript': 'function mount(){}', 'css': ''}
            response = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(output)}}],
                        'usage': {'prompt_tokens': 5, 'completion_tokens': 10, 'total_tokens': 15}}
            def opener(request, **kwargs):
                self.assertEqual(request.full_url, 'http://192.168.6.122:8080/v1/chat/completions')
                self.assertEqual(request.get_header('Authorization'), 'Bearer fixture-secret')
                body = json.loads(request.data)
                self.assertEqual(body['model'], 'fixture-mini')
                self.assertEqual(body['max_completion_tokens'], 32768)
                self.assertEqual(body['reasoning_effort'], 'medium')
                return io.BytesIO(json.dumps(response).encode())
            with patch.object(generation, 'snapshot', return_value=(project, data, 'fixture-hash')), \
                    patch.object(generation, 'resolve_node', return_value='fixture-node'), \
                    patch.object(generation, 'validate_output'), patch.object(generation, 'write'):
                record = generation.generate(root, {'revision': 1, 'project_id': 'a'*32, 'point_id': 'p1'}, opener=opener)
            self.assertEqual(record['status'], 'checked')
            self.assertNotIn('fixture-secret', json.dumps(record))
