"""Two-stage contracts; model responses below are fixtures, not evaluation evidence."""
import base64
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


def completion(answer, finish='stop'):
    return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(answer, ensure_ascii=False)},
                                              'finish_reason': finish}],
                                  'usage': {'prompt_tokens_details': {'video_tokens': 100, 'text_tokens': 20}}}).encode())


from test_support import MockModelTestCase


class TwoStageChecks(MockModelTestCase):
    def event(self, **changes):
        item = {'start_s': 1, 'end_s': 2, 'observation_s': 1.5,
                'description': '人物取得球', 'evidence': '球在手中', 'uncertainty': '无'}
        item.update(changes)
        return item

    def test_stage_event_time_and_fact_contract(self):
        answer = {'summary': '传接球', 'events': [self.event()], 'limitations': []}
        result = app.validate_stage_events(json.dumps(answer), 3)
        self.assertEqual(result['events'][0]['observation_id'], 'e1')
        self.assertEqual(app.validate_stage_events(json.dumps({**answer, 'events': []}), 3)['events'], [])
        for invalid in (self.event(observation_s=2.5), self.event(observation_s=-1),
                        self.event(observation_s=True), self.event(evidence='')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                app.validate_stage_events(json.dumps({**answer, 'events': [invalid]}), 3)
        with self.assertRaises(ValueError):
            app.validate_stage_events(json.dumps({**answer, 'interaction_points': []}), 3)

    def test_opportunity_ids_and_server_owned_time(self):
        observations = [{'observation_id': 'e1', 'frame_time_s': 1.5}]
        text = json.dumps({'interaction_points': [
            {'observation_id': 'e1', 'time_s': 100, 'opportunity': '决定是否投篮', 'reason': '球在手中'},
            {'observation_id': 'e1', 'opportunity': '选择传球', 'reason': '画面可见队友'}]})
        points = app.validate_stage_opportunities(text, observations)['interaction_points']
        self.assertEqual([point['time_s'] for point in points], [1.5, 1.5])
        self.assertEqual(len(points), 2)
        self.assertEqual(app.validate_stage_opportunities('{"interaction_points":[]}', observations)['interaction_points'], [])
        with self.assertRaises(ValueError):
            app.validate_stage_opportunities('{"interaction_points":[{"observation_id":"e2","opportunity":"投篮","reason":"持球"}]}', observations)

    def test_first_call_is_visual_only_and_returns_reviewer_events(self):
        meta = {'duration_s': 3, 'width': 320, 'height': 240, 'has_audio': True}
        answer = {'summary': '传接球', 'events': [self.event()], 'limitations': []}
        with patch.object(app, 'probe', return_value=meta), \
             patch.object(app, 'strip_audio', side_effect=lambda path, output: path), \
             patch.object(app, 'urlopen', return_value=completion(answer)) as request, \
             patch.object(app, 'save_record', side_effect=lambda record: record.update(record_file='fixture.json')):
            result = app.analyze_events(b'fixture', 'clip.mp4')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['stage'], 'events')
        self.assertFalse(result['sent_audio'])
        self.assertEqual(result['analysis']['events'][0]['observation_id'], 'e1')
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(body['model'], 'qwen3-vl-plus')
        self.assertEqual(body['messages'][0]['content'][1]['fps'], 2.)
        self.assertNotIn('interaction_points', body['messages'][0]['content'][0]['text'])

    def test_event_step_uses_selected_model_sampling_and_frames(self):
        meta = {'duration_s': 3, 'width': 320, 'height': 240, 'has_audio': True}
        frames = [{'time_s': 0., 'data': base64.b64encode(b'frame').decode()}]
        answer = {'summary': '传接球', 'events': [self.event()], 'limitations': []}
        with patch.object(app, 'probe', return_value=meta), \
             patch.object(app, 'extract', return_value=frames) as extracted, \
             patch.object(app, 'strip_audio') as stripped, \
             patch.object(app, 'urlopen', return_value=completion(answer)) as request, \
             patch.object(app, 'save_record', side_effect=lambda record: record.update(record_file='fixture.json')):
            result = app.analyze_events(b'fixture', 'clip.mp4', 'v13', 'gemini-2.5-flash-lite', 'frames', 1.)
        stripped.assert_not_called()
        extracted.assert_called_once()
        self.assertEqual(result['model'], 'gemini-2.5-flash-lite')
        self.assertEqual(result['mode'], 'frames')
        self.assertEqual(result['fps'], 1.)
        self.assertFalse(result['sent_audio'])
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(body['model'], 'gemini-2.5-flash-lite')
        self.assertEqual(sum(part['type'] == 'image_url' for part in body['messages'][0]['content']), 1)

    def test_second_call_maps_nearest_frame_and_keeps_first_record(self):
        raw = b'fixture'
        parent = {'record_id': 'a' * 32, 'stage': 'events', 'status': 'success',
                  'video_sha256': app.hashlib.sha256(raw).hexdigest(), 'record_file': 'events.json',
                  'analysis': {'events': [
                      {'observation_id': 'e1', **self.event()},
                      {'observation_id': 'e2', **self.event(start_s=2, end_s=3, observation_s=2.5,
                                                             description='后来投篮', evidence='球离手')} ]}}
        frames = [{'time_s': 1., 'data': base64.b64encode(b'frame1').decode()},
                  {'time_s': 1.5, 'data': base64.b64encode(b'frame2').decode()}]
        answer = {'interaction_points': [{'observation_id': 'e1', 'opportunity': '决定投篮', 'reason': '球在手中'}]}
        with patch.object(app, 'stage_record', return_value=parent), \
             patch.object(app, 'probe', return_value={'duration_s': 3, 'width': 320, 'height': 240, 'has_audio': False}), \
             patch.object(app, 'extract', return_value=frames), \
             patch.object(app, 'urlopen', return_value=completion(answer)) as request, \
             patch.object(app, 'save_record', side_effect=lambda record: record.update(record_file='opportunities.json')):
            result = app.suggest_opportunities(raw, 'clip.mp4', 'a' * 32, ['e1'])
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['parent_record_file'], 'events.json')
        self.assertEqual(result['analysis']['interaction_points'][0]['time_s'], 1.5)
        self.assertEqual(result['observations'][0]['frame_sha256'], app.hashlib.sha256(b'frame2').hexdigest())
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(sum(part['type'] == 'image_url' for part in body['messages'][0]['content']), 1)
        self.assertNotIn('后来投篮', result['actual_prompt'])

    def test_empty_selection_and_wrong_video_never_call_model(self):
        with patch.object(app, 'urlopen') as request:
            with self.assertRaises(ValueError):
                app.suggest_opportunities(b'fixture', 'clip.mp4', 'a' * 32, [])
            request.assert_not_called()

    def test_v14_segment_windows_and_global_timestamps(self):
        self.assertEqual(app.segment_windows(24), [(0., 24, 0., 24)])
        self.assertEqual(app.segment_windows(58), [(0., 20., 0., 20.),
                                                    (20., 40., 19., 40.),
                                                    (40., 58, 39., 58)])
        raw = b'fixture'
        original = {'duration_s': 58., 'width': 1280, 'height': 720, 'has_audio': True}
        clips = [dict(duration_s=n, width=1280, height=720, has_audio=False) for n in (20., 21., 19.)]
        answers = [{'summary': '片段', 'events': [self.event(start_s=a, end_s=b, observation_s=c)],
                    'limitations': []} for a, b, c in ((0, 1, 0.5), (1, 2, 1.5), (17, 18, 17.5))]

        def fake_clip(_, output, __, ___):
            output.write_bytes(b'visual clip')
            return output

        with patch.object(app, 'probe', side_effect=[original, *clips]), \
             patch.object(app, 'clip_video', side_effect=fake_clip), \
             patch.object(app, 'urlopen', side_effect=[completion(answer) for answer in answers]) as request, \
             patch.object(app, 'save_record', side_effect=lambda record: record.update(record_file='fixture.json')):
            result = app.analyze_events(raw, 'long.mp4', 'v14')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(request.call_count, 3)
        self.assertEqual(result['prompt_file'], 'prompts/v14/events.txt')
        self.assertEqual([event['observation_s'] for event in result['analysis']['events']], [0.5, 20.5, 56.5])
        self.assertEqual([event['observation_id'] for event in result['analysis']['events']], ['e1', 'e2', 'e3'])
        self.assertTrue(all(segment['status'] == 'success' for segment in result['segments']))
        self.assertFalse(result['sent_audio'])

    def test_v14_short_video_keeps_one_request_and_can_retry_if_truncated(self):
        meta = {'duration_s': 16., 'width': 320, 'height': 240, 'has_audio': False}
        answer = {'summary': '持球', 'events': [self.event()], 'limitations': []}

        def fake_clip(_, output, __, ___):
            output.write_bytes(b'visual clip')
            return output

        with patch.object(app, 'probe', side_effect=[meta, meta]), \
             patch.object(app, 'clip_video', side_effect=fake_clip), \
             patch.object(app, 'urlopen', return_value=completion(answer)) as request, \
             patch.object(app, 'save_record', side_effect=lambda record: record.update(record_file='fixture.json')):
            result = app.analyze_events(b'fixture', 'short.mp4', 'v14')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(request.call_count, 1)
        self.assertEqual(len(result['segments']), 1)

    def test_v14_deduplicates_boundary_continuation_without_dropping_new_action(self):
        first = {'start_s': 38, 'end_s': 40, 'observation_s': 39,
                 'segment_core_start_s': 20, 'description': '黑队持续围圈听讲，教练翻动手中的纸张。'}
        repeated = {'start_s': 39, 'end_s': 41, 'observation_s': 40,
                    'segment_core_start_s': 40, 'description': '黑队球员围成半圈，注视教练手中的纸张。'}
        new_action = {'start_s': 39, 'end_s': 41, 'observation_s': 40.5,
                      'segment_core_start_s': 40, 'description': '红队8号取得篮球，面向防守者。'}
        kept, dropped = app.deduplicate_segment_events([first, repeated, new_action])
        self.assertEqual(kept, [first, new_action])
        self.assertEqual(dropped, [repeated])

    def test_v14_retries_truncated_segment_with_smaller_clips(self):
        parent = {'metadata': {'duration_s': 20.}, 'segments': []}
        answer = {'summary': '片段', 'events': [self.event(start_s=0, end_s=1, observation_s=0.5)],
                  'limitations': []}
        later = {'summary': '后段', 'events': [self.event(start_s=1, end_s=2, observation_s=1.5)],
                 'limitations': []}

        def fake_clip(_, output, __, ___):
            output.write_bytes(b'visual clip')
            return output

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / 'source.mp4'
            with patch.object(app, 'probe', side_effect=[
                {'duration_s': n, 'has_audio': False} for n in (20., 10., 11.)]), \
                 patch.object(app, 'clip_video', side_effect=fake_clip), \
                 patch.object(app, 'urlopen', side_effect=[completion(answer, 'length'),
                                                        completion(answer), completion(later)]) as request:
                events = app.analyze_segment(source, folder, 0., 20., 0., 20., 'prompt', 'key', parent)
        self.assertEqual(request.call_count, 3)
        self.assertEqual([item['status'] for item in parent['segments']], ['failed', 'success', 'success'])
        self.assertIn('截断', parent['segments'][0]['error'])
        self.assertIn('raw_response', parent['segments'][0])
        self.assertEqual([item['observation_s'] for item in events], [0.5, 10.5])

    def test_v14_retry_record_counts_failed_attempt_and_still_succeeds(self):
        meta = {'duration_s': 20., 'width': 320, 'height': 240, 'has_audio': False}
        early = {'summary': '前段', 'events': [self.event(start_s=0, end_s=1, observation_s=0.5)],
                 'limitations': []}
        later = {'summary': '后段', 'events': [self.event(start_s=1, end_s=2, observation_s=1.5)],
                 'limitations': []}

        def fake_clip(_, output, __, ___):
            output.write_bytes(b'visual clip')
            return output

        with patch.object(app, 'probe', side_effect=[meta, meta, {**meta, 'duration_s': 10.},
                                                    {**meta, 'duration_s': 11.}]), \
             patch.object(app, 'clip_video', side_effect=fake_clip), \
             patch.object(app, 'urlopen', side_effect=[completion(early, 'length'),
                                                    completion(early), completion(later)]), \
             patch.object(app, 'save_record', side_effect=lambda record: record.update(record_file='fixture.json')):
            result = app.analyze_events(b'fixture', 'short.mp4', 'v14')
        self.assertEqual(result['status'], 'success')
        self.assertEqual([part['status'] for part in result['segments']], ['failed', 'success', 'success'])
        self.assertNotIn('prompt_tokens', result['usage'], 'Missing reported usage is not zero')
        self.assertEqual(result['video_tokens'], 300)
        self.assertEqual([event['observation_s'] for event in result['analysis']['events']], [0.5, 10.5])
        parent = {'video_sha256': app.hashlib.sha256(b'correct').hexdigest()}
        with patch.object(app, 'stage_record', return_value=parent), patch.object(app, 'urlopen') as request:
            with self.assertRaises(ValueError):
                app.suggest_opportunities(b'wrong', 'clip.mp4', 'a' * 32, ['e1'])
            request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
