"""Local engineering checks only. These tests do not measure model understanding."""
import base64
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class PipelineChecks(unittest.TestCase):
    def result(self, **update):
        event = dict(start_s=1, end_s=2, description='test', evidence='frame 1', source='visual', uncertainty='between frames')
        event.update(update)
        return json.dumps(dict(summary='test fixture, not AI output', events=[event], limitations=[]))

    def day2_result(self, points):
        result = json.loads(self.result())
        result['interaction_points'] = points
        return json.dumps(result)

    def point(self, **update):
        point = dict(time_s=0.5, action_start_s=1.5, visible_cue='球在手中',
                     player_action='选择出手时机', reason='动作尚未开始',
                     evidence='0.5 秒画面可见持球', uncertainty='时间约有 0.25 秒误差')
        point.update(update)
        return point

    def test_timestamp_bounds_and_invalid_numbers(self):
        for value in (-1, 30, float('nan'), float('inf'), True, '1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                app.validate(self.result(start_s=value), 3, False)
        self.assertEqual(app.validate(self.result(), 3, False)['events'][0]['start_s'], 1)

    def test_audio_claims_require_audio(self):
        with self.assertRaises(ValueError):
            app.validate(self.result(source='audio'), 3, False)
        app.validate(self.result(source='audio'), 3, True)

    def test_fenced_json_is_accepted(self):
        fenced = '```json\n' + self.result() + '\n```'
        self.assertEqual(app.validate(app.extract_json(fenced), 3, False)['events'][0]['start_s'], 1)

    def test_media_tokens_detect_gemini_video_and_ignore_text_only(self):
        text_only = {'billing_usage': {'gemini_usage_metadata': {'promptTokensDetails': [{'modality': 'TEXT', 'tokenCount': 319}]}}}
        seen = {'billing_usage': {'gemini_usage_metadata': {'promptTokensDetails': [{'modality': 'VIDEO', 'tokenCount': 516}, {'modality': 'TEXT', 'tokenCount': 19}]}}}
        qwen = {'prompt_tokens_details': {'video_tokens': 5762, 'text_tokens': 321}}
        self.assertEqual(app.media_token_count(text_only), 0)
        self.assertEqual(app.media_token_count(seen), 516)
        self.assertEqual(app.media_token_count(qwen), 5762)
        duplicated = {'prompt_tokens_details': {'audio_tokens': 600},
                      'billing_usage': {'gemini_usage_metadata': {'promptTokensDetails': [
                          {'modality': 'VIDEO', 'tokenCount': 6192},
                          {'modality': 'AUDIO', 'tokenCount': 600}]}}}
        self.assertEqual(app.media_token_count(duplicated), 6792)
        self.assertEqual(app.modality_token_counts(duplicated)['audio'], 600)
        self.assertIsNone(app.media_token_count({'prompt_tokens': 7000}))

    def test_slight_overrun_is_clamped_to_duration(self):
        result = app.validate(self.result(start_s=8, end_s=11), 10.88, False)
        self.assertEqual(result['events'][0]['end_s'], 10.88)
        self.assertIn('11→10.88', result['limitations'][-1])
        with self.assertRaises(ValueError):
            app.validate(self.result(end_s=5), 3, False)

    def test_missing_uncertainty_is_marked_not_rejected(self):
        raw = self.result()
        event = json.loads(raw)['events'][0]
        del event['uncertainty']
        body = json.dumps(dict(summary='kept', events=[event], limitations=['采样稀疏']))
        result = app.validate(body, 3, False)
        self.assertEqual(result['events'][0]['uncertainty'], '模型未说明')
        self.assertIn('模型未说明', result['limitations'][-1])

    def test_empty_events_allowed_and_evidence_required(self):
        app.validate('{"summary":"", "events":[],"limitations":[]}', 3, False)
        with self.assertRaises(ValueError):
            app.validate(self.result(evidence=''), 3, False)

    def test_day2_points_validate_and_old_records_remain_readable(self):
        self.assertEqual(app.validate(self.result(), 3, False)['interaction_points'], [])
        self.assertEqual(app.validate(self.day2_result([]), 3, False, True)['interaction_points'], [])
        self.assertEqual(app.validate(self.day2_result([self.point()]), 3, False, True)['interaction_points'][0]['time_s'], 0.5)
        with self.assertRaises(ValueError):
            app.validate(self.result(), 3, False, True)
        invalid = [self.point(time_s=1.5), self.point(action_start_s=4),
                   self.point(time_s=-1), self.point(evidence='')]
        for point in invalid:
            with self.subTest(point=point), self.assertRaises(ValueError):
                app.validate(self.day2_result([point]), 3, False, True)
        with self.assertRaises(ValueError):
            app.validate(self.day2_result([self.point()] * 3), 3, False, True)

    def test_v6_point_source_requires_confirmed_audio(self):
        point = self.point(source='visual')
        self.assertEqual(app.validate(self.day2_result([point]), 3, False, True, True)['interaction_points'][0]['source'], 'visual')
        with self.assertRaises(ValueError):
            app.validate(self.day2_result([self.point()]), 3, True, True, True)
        with self.assertRaises(ValueError):
            app.validate(self.day2_result([self.point(source='audio')]), 3, True, True, True)
        with self.assertRaises(ValueError):
            app.validate(self.day2_result([self.point(source='both')]), 3, False, True, True)
        self.assertEqual(app.validate(self.day2_result([self.point(source='both')]), 3, True, True, True)['interaction_points'][0]['source'], 'both')
        with self.assertRaises(ValueError):
            app.validate(self.day2_result([point, point]), 3, True, True, True, max_points=1)

    def test_v9_observed_and_unobserved_actions(self):
        observed = self.point(source='visual', action_observed=True)
        result = app.validate(self.day2_result([observed]), 3, False, True, True,
                              max_points=1, require_action_observed=True)
        self.assertTrue(result['interaction_points'][0]['action_observed'])
        unseen = self.point(source='visual', action_observed=False, action_start_s=None,
                            evidence='0.5 秒画面可见准备；后续行动未拍到',
                            uncertainty='后续行动未拍到')
        result = app.validate(self.day2_result([unseen]), 3, False, True, True,
                              max_points=1, require_action_observed=True)
        self.assertIsNone(result['interaction_points'][0]['action_start_s'])
        self.assertEqual(app.validate(self.day2_result([]), 3, False, True, True,
                                      max_points=1, require_action_observed=True)['interaction_points'], [])
        invalid = [self.point(source='visual'),
                   self.point(source='visual', action_observed='false', action_start_s=None),
                   self.point(source='visual', action_observed=False),
                   self.point(source='visual', action_observed=False, action_start_s=1.5),
                   self.point(source='visual', action_observed=False, action_start_s=None),
                   self.point(source='visual', action_observed=True, action_start_s=None),
                   self.point(source='visual', action_observed=True, time_s=1.5),
                   self.point(source='visual', action_observed=False, action_start_s=None, time_s=4)]
        for point in invalid:
            with self.subTest(point=point), self.assertRaises(ValueError):
                app.validate(self.day2_result([point]), 3, False, True, True,
                             max_points=1, require_action_observed=True)
        with self.assertRaises(ValueError):
            app.validate(self.day2_result([unseen]), 3, False, True, True)
        # v1-v8 records lacked this flag and keep their original numeric time contract.
        self.assertEqual(app.validate(self.day2_result([self.point(source='visual')]), 3,
                                      False, True, True)['interaction_points'][0]['action_start_s'], 1.5)

    def test_v9_analysis_accepts_preparation_without_recorded_execution(self):
        meta = dict(duration_s=3, width=320, height=240, has_audio=False)
        point = self.point(source='visual', action_observed=False, action_start_s=None,
                           evidence='准备姿态可见；后续行动未拍到', uncertainty='后续行动未拍到')
        answer = self.day2_result([point])
        response = {'choices': [{'message': {'content': answer}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens_details': {'video_tokens': 100, 'text_tokens': 20}}}
        with patch.object(app, 'probe', return_value=meta), \
             patch.object(app, 'urlopen', return_value=io.BytesIO(json.dumps(response).encode())) as request, \
             patch.object(app, 'save_record', side_effect=lambda record: record.update(record_file='fixture.json')):
            result = app.analyze(b'fixture', 'qwen3.8-omni-flash', 'direct', 2., 'fixture.mp4', 'v9')
        self.assertEqual(result['status'], 'success')
        self.assertIsNone(result['analysis']['interaction_points'][0]['action_start_s'])
        body = json.loads(request.call_args.args[0].data)
        self.assertIn('action_observed', body['messages'][0]['content'][0]['text'])
        self.assertEqual(body['response_format'], {'type': 'json_object'})

    def test_v12_accepts_multiple_simple_points_without_old_evidence_fields(self):
        body = dict(summary='人物移动并拿起物体',
                    events=[dict(start_s=0, end_s=2, description='人物走向桌面')],
                    interaction_points=[dict(time_s=1.5, opportunity='观察物体', reason='此时物体出现在画面中'),
                                        dict(time_s=0.5, opportunity='选择路径', reason='人物经过岔路')])
        result = app.validate_open_interactions(json.dumps(body), 3)
        self.assertEqual([p['time_s'] for p in result['interaction_points']], [0.5, 1.5])
        self.assertEqual(result['limitations'], [])
        for point in (dict(time_s=-1, opportunity='选择路径', reason='岔路'),
                      dict(time_s=4, opportunity='选择路径', reason='岔路'),
                      dict(time_s=True, opportunity='选择路径', reason='岔路'),
                      dict(time_s=1, opportunity='', reason='岔路')):
            with self.subTest(point=point), self.assertRaises(ValueError):
                app.validate_open_interactions(json.dumps({**body, 'interaction_points': [point]}), 3)
        # Older prompt versions still retain their original one-point and evidence contract.
        with self.assertRaises(ValueError):
            app.validate(self.day2_result([self.point(source='visual')] * 2), 3,
                         False, True, True, max_points=1)

    def test_v12_analyze_uses_open_schema(self):
        meta = dict(duration_s=3, width=320, height=240, has_audio=False)
        answer = json.dumps(dict(summary='test', events=[dict(start_s=0, end_s=2, description='经过桌面')],
                                 interaction_points=[dict(time_s=0.5, opportunity='检查桌面', reason='经过此处'),
                                                     dict(time_s=1.5, opportunity='转向', reason='出现岔路')]))
        response = {'choices': [{'message': {'content': answer}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens_details': {'video_tokens': 100, 'text_tokens': 20}}}
        with patch.object(app, 'probe', return_value=meta), \
             patch.object(app, 'urlopen', return_value=io.BytesIO(json.dumps(response).encode())), \
             patch.object(app, 'save_record', side_effect=lambda record: record.update(record_file='fixture.json')):
            result = app.analyze(b'fixture', 'qwen3.8-omni-flash', 'direct', 2., 'fixture.mp4', 'v12')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(len(result['analysis']['interaction_points']), 2)

    def test_env_models_are_selectable(self):
        expected = ['qwen3-vl-plus', 'qwen3.8-flash', 'gemini-2.5-flash-lite', 'qwen3.8-omni-flash']
        self.assertEqual(list(app.MODELS), expected)
        self.assertTrue(all(provider == 'beeroute' for provider in app.MODELS.values()))

    def test_highest_numeric_prompt_version_is_selected(self):
        path, text = app.load_prompt()
        self.assertRegex(path.name, r'v[1-9]\d*\.txt')
        self.assertIn('summary', text)
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / 'prompts'
            folder.mkdir()
            for name in ('v2.txt', 'v10.txt', 'v9.txt'):
                (folder / name).write_text(name, encoding='utf-8')
            with patch.object(app, 'ROOT', Path(td)):
                self.assertEqual(app.load_prompt()[0].name, 'v10.txt')
                self.assertEqual(app.load_prompt('v2')[0].name, 'v2.txt')
                with self.assertRaises(ValueError):
                    app.load_prompt('v3')
                with self.assertRaises(ValueError):
                    app.load_prompt('../v2')

    def test_samples_come_from_mp4_example(self):
        names = app.list_samples()
        self.assertIn('S01_倒水.mp4', names)
        data = app.read_sample('S01_倒水.mp4')
        self.assertGreater(len(data), 1000)
        self.assertIn(b'ftyp', data[:32])
        with self.assertRaises(ValueError):
            app.read_sample('../.env')

    def test_request_modes_preserve_timestamps_and_audio_contract(self):
        meta = dict(duration_s=3, has_audio=True)
        frames = [dict(time_s=1.04, data='fixture')]
        prompt = 'prompt'
        for model in app.MODELS:
            for mode in ('direct', 'frames'):
                url, body, audio = app.build_request(model, mode, 1., 'fixture', frames, meta, prompt)
                self.assertTrue(url.startswith('https://'))
                expected_audio = mode == 'direct' and app.supports_video_audio(model)
                self.assertEqual(audio, expected_audio)
                text = json.dumps(body, ensure_ascii=False)
                if mode == 'frames':
                    self.assertIn('1.040000', text)
                    self.assertIn('可用音频：无', text)
                else:
                    self.assertIn('可用音频：可能有' if expected_audio else '可用音频：无', text)
                    if not expected_audio:
                        self.assertIn('只分析画面', text)
                    if model.startswith('gemini'):
                        self.assertIn('image_url', text)
                        self.assertIn('video/mp4', text)
                        self.assertNotIn('video_url', text)
                    if model.startswith('qwen'):
                        self.assertIn('video_url', text)
                    if model == 'qwen3.8-omni-flash':
                        self.assertEqual(body.get('response_format'), {'type': 'json_object'})
        silent = dict(duration_s=3, has_audio=False)
        _, body, audio = app.build_request(next(iter(app.MODELS)), 'direct', 1., 'fixture', frames, silent, prompt)
        self.assertFalse(audio)
        self.assertIn('可用音频：无', json.dumps(body, ensure_ascii=False))

    def test_audio_claims_require_supported_model_and_confirmed_audio_tokens(self):
        meta = dict(duration_s=3, width=320, height=240, has_audio=True)
        def call(model, usage, source):
            answer = json.loads(self.result(source=source))
            answer['interaction_points'] = []
            response = {'choices': [{'message': {'content': json.dumps(answer)}, 'finish_reason': 'stop'}],
                        'usage': usage}
            with patch.object(app, 'probe', return_value=meta), \
                 patch.object(app, 'strip_audio', side_effect=lambda path, output: path), \
                 patch.object(app, 'urlopen', return_value=io.BytesIO(json.dumps(response).encode())), \
                 patch.object(app, 'save_record', side_effect=lambda record: record.update(record_file='fixture.json')):
                return app.analyze(b'fixture', model, 'direct', 1., 'fixture.mp4', 'v6')
        video_only = {'prompt_tokens_details': {'video_tokens': 100, 'text_tokens': 20}}
        for model in ('qwen3-vl-plus', 'qwen3.8-flash', 'qwen3.8-omni-flash'):
            with self.subTest(model=model):
                result = call(model, video_only, 'audio')
                self.assertEqual(result['status'], 'failed')
                self.assertIn('未确认模型收到音频', result['error'])
        supported = call('qwen3.8-omni-flash', {'prompt_tokens_details': {'video_tokens': 100, 'audio_tokens': 5}}, 'audio')
        self.assertEqual(supported['status'], 'success')
        self.assertEqual(supported['audio_verification'], 'confirmed')
        self.assertEqual(supported['video_tokens'], 100)
        unreported = call('qwen3.8-flash', {'prompt_tokens': 120}, 'visual')
        self.assertEqual(unreported['status'], 'success')
        self.assertEqual(unreported['media_verification'], 'unreported')
        self.assertIn('无法确认视频是否进入模型', unreported['analysis']['limitations'][-1])
        text_only = call('qwen3.8-flash', {'prompt_tokens_details': {'text_tokens': 120}}, 'visual')
        self.assertEqual(text_only['status'], 'failed')
        audio_only = call('qwen3.8-omni-flash', {'prompt_tokens_details': {'audio_tokens': 20}}, 'visual')
        self.assertEqual(audio_only['status'], 'failed')

    def test_omni_json_mode_falls_back_only_for_explicit_format_rejection(self):
        meta = dict(duration_s=3, width=320, height=240, has_audio=True)
        answer = json.loads(self.day2_result([self.point(source='both')]))
        response = {'choices': [{'message': {'content': json.dumps(answer)}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens_details': {'video_tokens': 100, 'audio_tokens': 5}}}
        rejected = HTTPError('https://example.test', 400, 'bad request', {},
                             io.BytesIO(b'{"error":"response_format json_object unsupported"}'))
        with patch.object(app, 'probe', return_value=meta), \
             patch.object(app, 'urlopen', side_effect=[rejected, io.BytesIO(json.dumps(response).encode())]) as request, \
             patch.object(app, 'save_record', side_effect=lambda record: record.update(record_file='fixture.json')):
            result = app.analyze(b'fixture', 'qwen3.8-omni-flash', 'direct', 2., 'fixture.mp4', 'v6')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(request.call_count, 2)
        self.assertTrue(result['json_mode_fallback'])
        self.assertIsNone(result['request_options']['response_format'])
        self.assertEqual(result['audio_verification'], 'confirmed')

    def test_v6_both_point_rejected_when_usage_has_no_audio_tokens(self):
        meta = dict(duration_s=3, width=320, height=240, has_audio=True)
        answer = self.day2_result([self.point(source='both')])
        response = {'choices': [{'message': {'content': answer}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens_details': {'video_tokens': 100, 'audio_tokens': 0}}}
        with patch.object(app, 'probe', return_value=meta), \
             patch.object(app, 'urlopen', return_value=io.BytesIO(json.dumps(response).encode())), \
             patch.object(app, 'save_record', side_effect=lambda record: record.update(record_file='fixture.json')):
            result = app.analyze(b'fixture', 'qwen3.8-omni-flash', 'direct', 2., 'fixture.mp4', 'v6')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['audio_verification'], 'zero')
        self.assertIn('未确认模型收到音频', result['error'])

    def test_audio_is_removed_from_visual_only_request_copy(self):
        original = Path(__file__).resolve().parents[2] / 'mp4-example' / 'S02_人物交流.mp4'
        with tempfile.TemporaryDirectory() as td:
            stripped = app.strip_audio(original, Path(td) / 'visual-only.mp4')
            self.assertTrue(app.probe(original)['has_audio'])
            self.assertFalse(app.probe(stripped)['has_audio'])
            self.assertGreater(stripped.stat().st_size, 1000)

    def test_record_names_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as td, patch.object(app, 'ROOT', Path(td)):
            first = {'model': 'qwen3.8-flash', 'mode': 'direct', 'video_name': 'clip.mp4'}
            second = first.copy()
            app.save_record(first)
            app.save_record(second)
            self.assertNotEqual(first['record_file'], second['record_file'])
            self.assertEqual(len(list((Path(td) / 'results' / 'qwen3.8-flash').glob('*.json'))), 2)
            self.assertTrue(first['record_file'].startswith('results/qwen3.8-flash/'))

    def test_actual_ffmpeg_decode_and_pts(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            video = folder / 'fixture.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=320x240:r=25:d=3','-c:v','libx264','-y',str(video)], check=True)
            meta = app.probe(video)
            self.assertAlmostEqual(meta['duration_s'], 3, places=1)
            frames = app.extract(video, folder, 3, 1.)
            self.assertEqual([f['time_s'] for f in frames], [0., 1., 2.])
            self.assertTrue(base64.b64decode(frames[0]['data']).startswith(b'\xff\xd8'))

    def test_http_page_and_missing_credentials_no_fake_result(self):
        server = app.ThreadingHTTPServer(('127.0.0.1',0), app.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base) as response:
                page = response.read()
                self.assertIn('尚未调用模型'.encode(), page)
                self.assertIn('候选交互点'.encode(), page)
                self.assertIn(b'/assets/js/app.js', page)
            with urlopen(base+'/assets/js/app.js') as response:
                self.assertIn(b'point.time_s', response.read())
            with urlopen(base+'/config') as response:
                configuration = json.load(response)
                token = configuration['token']
                v13 = next(item for item in configuration['prompt_catalog'] if item['version'] == 'v13')
                self.assertEqual(v13['flow'], 'two_stage')
                self.assertEqual([item['path'] for item in v13['files']],
                                 ['prompts/v13/events.txt', 'prompts/v13/opportunities.txt'])
                self.assertIn('可观察事件', v13['files'][0]['content'])
                self.assertIn('参与机会', v13['files'][1]['content'])
                v14 = next(item for item in configuration['prompt_catalog'] if item['version'] == 'v14')
                self.assertEqual([item['path'] for item in v14['files']],
                                 ['prompts/v14/events.txt', 'prompts/v13/opportunities.txt'])
                self.assertNotIn('v15', [item['version'] for item in configuration['prompt_catalog']])
                self.assertEqual([item['id'] for item in configuration['games']['types']],
                                 ['tap_object', 'choice', 'timing', 'mash', 'drag', 'hold_release', 'swipe'])
            with patch.dict(os.environ, {'BEEROUTE_API_KEY': ''}):
                request = Request(base+f'/analyze?model={next(iter(app.MODELS))}&mode=frames&fps=1&name=clip.mp4', data=b'fixture', headers={'X-Local-Token':token})
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request)
                self.assertEqual(raised.exception.code, 400)
                result = json.load(raised.exception)
                self.assertIn('没有调用模型', result['error'])
                self.assertNotIn('analysis', result)
            with self.assertRaises(HTTPError) as raised:
                urlopen(Request(base+'/analyze', data=b'fixture'))
            self.assertEqual(raised.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()

    @unittest.skipUnless(shutil.which('node'), 'Node.js not installed')
    def test_ui_candidate_click_seeks_video_and_empty_state(self):
        script = Path(__file__).parent / 'frontend/ui_smoke.js'
        result = subprocess.run(['node', str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
