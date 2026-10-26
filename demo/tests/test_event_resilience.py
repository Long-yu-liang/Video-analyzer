"""Auxiliary defaults and partial segment results; all API responses are mocked."""
import io
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from test_support import MockModelTestCase


def answer(**changes):
    data = {'summary': 'fixture event', 'events': [{'start_s': 1, 'end_s': 2, 'observation_s': 1.5,
            'description': 'visible preparation', 'evidence': 'object in hand', 'uncertainty': 'none'}]}
    return {**data, **changes}


def response(data):
    raw = json.dumps(data, ensure_ascii=False)
    return io.BytesIO(json.dumps({'choices': [{'message': {'content': raw}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15,
                  'prompt_tokens_details': {'video_tokens': 8, 'text_tokens': 2}}}).encode())


class EventResilienceTests(MockModelTestCase):
    def test_missing_auxiliary_defaults_but_core_stays_strict(self):
        original = json.dumps(answer())
        result = app.validate_stage_events(original, 20)
        self.assertEqual(result['limitations'], [])
        self.assertEqual(result['validation_warnings'][0]['action'], 'default_empty_array')
        self.assertNotIn('limitations', json.loads(original))
        for invalid in ({'events': None}, {'summary': None}, {'limitations': 'bad'},
                        {'events': [{**answer()['events'][0], 'observation_s': 30}]},
                        {'events': [{**answer()['events'][0], 'evidence': ''}]}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                app.validate_stage_events(json.dumps(answer(**invalid)), 20)

    def run_segments(self, replies):
        metadata = {'duration_s': 58., 'width': 320, 'height': 240, 'has_audio': False}
        def fake_clip(source, target, start, end):
            target.write_bytes(b'fixture-video')
            return target
        with patch.object(app, 'probe', side_effect=[metadata, {**metadata, 'duration_s': 20},
                                                  {**metadata, 'duration_s': 21}, {**metadata, 'duration_s': 19}]), \
                patch.object(app, 'clip_video', side_effect=fake_clip), \
                patch.object(app, 'urlopen', side_effect=replies) as call, \
                patch.object(app, 'save_record', side_effect=lambda r: r.update(record_file='fixture.json')):
            result = app.analyze_events(b'fixture', 'fixture.mp4', 'v14')
        return result, call.call_count

    def test_missing_limitations_succeeds_and_keeps_raw(self):
        result, count = self.run_segments([response(answer()) for _ in range(3)])
        self.assertEqual(count, 3)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(len(result['validation_warnings']), 3)
        self.assertEqual(result['usage']['total_tokens'], 45)
        for segment in result['segments']:
            self.assertNotIn('limitations', json.loads(segment['raw_text']))
            self.assertNotIn('limitations', json.loads(segment['raw_response']['choices'][0]['message']['content']))
            self.assertEqual(segment['analysis']['limitations'], [])

    def test_later_core_failure_preserves_partial_and_all_usage(self):
        bad = answer(events=[{**answer()['events'][0], 'observation_s': 999}])
        result, count = self.run_segments([response(answer()), response(bad)])
        self.assertEqual(count, 2, 'no extra paid retries or remaining calls')
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('analysis', result)
        self.assertEqual(len(result['partial_analysis']['events']), 1)
        self.assertEqual(result['partial_analysis']['events'][0]['observation_s'], 1.5)
        self.assertEqual([r['status'] for r in result['segment_coverage']['ranges']], ['success', 'failed', 'unprocessed'])
        self.assertEqual(result['usage']['total_tokens'], 30, 'include usage of rejected model output')
        self.assertEqual(result['segments'][1]['validation_error']['path'], 'events[0].observation_s')

    def test_http_failure_keeps_known_subtotal_not_false_zero(self):
        error = HTTPError('https://fixture.invalid', 503, 'fixture failure', {}, io.BytesIO(b'fixture error detail'))
        result, count = self.run_segments([response(answer()), error])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(count, 2)
        self.assertEqual(result['usage'], {})
        self.assertEqual(result['usage_reported_subtotal']['total_tokens'], 15)
        self.assertTrue(result['usage_incomplete'])
        self.assertEqual(result['segments'][1]['api_error_detail'], 'fixture error detail')
        self.assertEqual(len(result['partial_analysis']['events']), 1)

    def test_first_segment_failure_does_not_invent_events(self):
        result, count = self.run_segments([response(answer(events=None))])
        self.assertEqual(count, 1)
        self.assertNotIn('partial_analysis', result)
        self.assertEqual(result['usage']['total_tokens'], 15)
        self.assertFalse(result['segment_coverage']['complete'])
