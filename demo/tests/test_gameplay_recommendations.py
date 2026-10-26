"""Recommendation-only checks. Model calls are mocked, not evaluation runs."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import app
import gameplay_recommendations as recommendations
import test_gameplay as fixture_module


from test_support import MockModelTestCase


class RecommendationChecks(MockModelTestCase):
    def setUp(self):
        self.catalog = app.load_game_catalog()
        self.point = {'point_id': 'p1', 'observation_id': 'e1', 'time_s': 2,
                      'opportunity': '参与物体操作', 'reason': '人物持有物体'}

    def option(self, **changes):
        item = {key: '具体的' + key for key in recommendations.FIELDS}
        item.update(point_id='p1', game_id='choice')
        item.update(changes)
        return item

    def validate(self, items):
        return recommendations.validate(json.dumps({'recommendations': items}), [self.point], self.catalog)

    def test_choice_drag_swipe_without_execution_fields_are_valid(self):
        for game in ('choice', 'drag', 'swipe', 'timing', 'hold_release'):
            output = self.validate([self.option(game_id=game)])['recommendations'][0]
            self.assertEqual(output['game_id'], game)
            self.assertEqual(output['option_id'], 'p1-o1')
            for key in ('parameters', 'anchors', 'resume', 'rules_text', 'ready_for_codegen'):
                self.assertNotIn(key, output)
        pair = self.validate([self.option(), self.option(game_id='drag')])['recommendations']
        self.assertEqual(len(pair), 2)

    def test_still_rejects_unknown_points_types_missing_description_and_duplicates(self):
        for items in ([self.option(point_id='unknown')], [self.option(game_id='unknown')],
                      [self.option(player_action='')], [self.option(), self.option()],
                      [self.option(), self.option(game_id='drag'), self.option(game_id='swipe')]):
            with self.subTest(items=items), self.assertRaises(ValueError):
                self.validate(items)
        self.assertEqual(self.validate([])['recommendations'], [])

    def test_save_binary_frame_and_read_historical_inline_without_rewriting(self):
        data = b'jpeg-test-fixture'
        record = {'model': 'qwen3-vl-plus', 'record_id': 'c'*32}
        with tempfile.TemporaryDirectory() as temp, patch.object(app, 'ROOT', Path(temp)):
            frame_file = app.save_gameplay_frame(record, 'p1', base64.b64encode(data).decode())
            self.assertEqual((Path(temp) / frame_file).read_bytes(), data)
            observation = {'point_id': 'p1', 'frame_file': frame_file,
                           'frame_sha256': hashlib.sha256(data).hexdigest()}
            with patch.object(app, 'load_stage_result', return_value={'observations': [observation]}):
                self.assertEqual(app.read_gameplay_frame('c'*32, 'p1'), data)
                observation['frame_file'] = '../outside.jpg'
                with self.assertRaises(ValueError):
                    app.read_gameplay_frame('c'*32, 'p1')
        old = {'observations': [{'point_id': 'p1', 'preview_jpeg_base64': base64.b64encode(data).decode(),
                                 'frame_sha256': hashlib.sha256(data).hexdigest()}]}
        before = copy.deepcopy(old)
        with patch.object(app, 'load_stage_result', return_value=old):
            self.assertEqual(app.read_gameplay_frame('c'*32, 'p1'), data)
        self.assertEqual(old, before)

    def test_v20_request_has_images_but_record_has_only_frame_references(self):
        fixture = fixture_module.GameplayChecks(); fixture.setUp()
        frame = {'time_s': 8, 'data': base64.b64encode(b'jpeg-fixture').decode()}
        answer = {'recommendations': [self.option(), self.option(game_id='drag')]}
        with patch.object(app, 'load_stage_result', return_value=fixture.opportunity), \
             patch.object(app, 'stage_record', return_value=fixture.events), \
             patch.object(app, 'probe', return_value=fixture.opportunity['metadata']), \
             patch.object(app, 'extract', return_value=[frame]), \
             patch.object(app, 'save_gameplay_frame', return_value='results/fixture/p1.jpg'), \
             patch.object(app, 'urlopen', return_value=fixture_module.completion(answer)) as request, \
             patch.object(app, 'save_record'):
            result = app.suggest_gameplay(fixture.raw, 'clip.mp4', 'b'*32, ['p1'], 'v20', times={'p1': 8})
        self.assertEqual(result['status'], 'success', result.get('error'))
        self.assertEqual(result['schema_version'], 3)
        self.assertNotIn('template_snapshot', result)
        self.assertNotIn('preview_jpeg_base64', json.dumps(result))
        self.assertNotIn(frame['data'], json.dumps(result))
        self.assertEqual(result['observations'][0]['frame_file'], 'results/fixture/p1.jpg')
        self.assertEqual(result['observations'][0]['frame_time_s'], 8)
        body = json.loads(request.call_args.args[0].data)
        self.assertIn('data:image/jpeg;base64,' + frame['data'], json.dumps(body))
        self.assertEqual(body['max_tokens'], 4096)
        self.assertEqual(body['response_format'], {'type': 'json_object'})
        self.assertEqual(result['schema_enforcement'], 'local_core_strict_auxiliary_warnings')
        self.assertEqual(result['output_schema']['properties']['recommendations']['items']['required'],
                         ['point_id', 'game_id', *recommendations.FIELDS])
        self.assertEqual(json.loads(result['raw_text']), answer)

    def test_actual_pipeline_keeps_core_options_and_preserves_bad_auxiliary_raw(self):
        fixture = fixture_module.GameplayChecks(); fixture.setUp()
        frame = {'time_s': 5.5, 'data': base64.b64encode(b'jpeg-fixture').decode()}
        answer = {'recommendations': [self.option()], 'abstentions': ['p2：没有合适玩法']}
        with patch.object(app, 'load_stage_result', return_value=fixture.opportunity), \
             patch.object(app, 'stage_record', return_value=fixture.events), \
             patch.object(app, 'probe', return_value=fixture.opportunity['metadata']), \
             patch.object(app, 'extract', return_value=[frame]), \
             patch.object(app, 'save_gameplay_frame', return_value='results/fixture/p1.jpg'), \
             patch.object(app, 'urlopen', return_value=fixture_module.completion(answer)), \
             patch.object(app, 'save_record'):
            result = app.suggest_gameplay(fixture.raw, 'clip.mp4', 'b'*32, ['p1','p2'], 'v20')
        self.assertEqual(result['status'], 'success', result.get('error'))
        self.assertEqual(result['validation_status'], 'passed_with_warnings')
        self.assertEqual(len(result['analysis']['recommendations']), 1)
        self.assertEqual(result['validation_warnings'][0]['path'], 'abstentions[0]')
        self.assertEqual(json.loads(result['raw_text']), answer)
        self.assertEqual(result['raw_response']['choices'][0]['message']['content'], result['raw_text'])

    def test_pipeline_missing_core_is_fatal_and_error_has_field_path(self):
        fixture = fixture_module.GameplayChecks(); fixture.setUp()
        frame = {'time_s': 5.5, 'data': base64.b64encode(b'jpeg-fixture').decode()}
        answer = {'recommendations': [self.option(player_action=None)]}
        with patch.object(app, 'load_stage_result', return_value=fixture.opportunity), \
             patch.object(app, 'stage_record', return_value=fixture.events), \
             patch.object(app, 'probe', return_value=fixture.opportunity['metadata']), \
             patch.object(app, 'extract', return_value=[frame]), \
             patch.object(app, 'save_gameplay_frame', return_value='results/fixture/p1.jpg'), \
             patch.object(app, 'urlopen', return_value=fixture_module.completion(answer)), \
             patch.object(app, 'save_record'):
            result = app.suggest_gameplay(fixture.raw, 'clip.mp4', 'b'*32, ['p1'], 'v20')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['validation_error']['path'], 'recommendations[0].player_action')
        self.assertIn('实际为null', result['error'])

    def test_selection_does_not_require_coordinates_parameters_or_playback(self):
        option = self.validate([self.option(game_id='drag')])['recommendations'][0]
        gameplay = {'record_id': 'c'*32, 'parent_record_id': 'b'*32, 'schema_version': 3,
                    'record_file': 'fixture-gameplay.json', 'selected_point_ids': ['p1'],
                    'catalog_snapshot': self.catalog, 'analysis': {'recommendations': [option]},
                    'model': 'qwen3-vl-plus', 'prompt_file': 'prompts/v20/gameplay.txt', 'prompt': 'fixture',
                    'observations': [{'point_id': 'p1', 'requested_time_s': 2, 'frame_time_s': 2,
                                      'frame_sha256': 'hash', 'frame_file': 'results/fixture/p1.jpg'}]}
        opportunity = {'record_id': 'b'*32, 'metadata': {'duration_s': 10}, 'video_name': 'clip.mp4',
                       'video_sha256': 'video-hash', 'analysis': {'interaction_points': [self.point]}}
        selection = {'point_id': 'p1', 'option_id': 'p1-o1', 'status': 'accepted', 'adjusted_time_s': 2}
        saved = recommendations.build_brief({'selections': [selection]}, opportunity, gameplay, {'p1': self.point})[0]
        self.assertEqual(saved['game_id'], 'drag')
        self.assertNotIn('anchors', saved)
        self.assertNotIn('resume', saved)
        for change in ({'option_id': 'p2-o1'}, {'adjusted_time_s': 3}, {'adjusted_time_s': True}):
            with self.assertRaises(ValueError):
                recommendations.build_brief({'selections': [{**selection, **change}]}, opportunity, gameplay, {'p1': self.point})
        manual = {**selection, 'status': 'edited', 'option_id': None, 'design': self.option(game_id='swipe')}
        self.assertEqual(recommendations.build_brief({'selections': [manual]}, opportunity, gameplay, {'p1': self.point})[0]['origin'], 'manual')
        with patch.object(app, 'load_stage_result', side_effect=[opportunity, gameplay]), \
             patch.object(app, 'save_record') as stored, patch.object(app, 'urlopen') as request:
            brief = app.save_gameplay_brief({'opportunity_record_id': 'b'*32,
                'gameplay_record_id': 'c'*32, 'selections': [selection]})
        self.assertEqual(brief['schema_version'], 3)
        self.assertNotIn('template_snapshot', brief)
        self.assertNotIn('resume', brief)
        request.assert_not_called(); stored.assert_called_once()

    @unittest.skipUnless(shutil.which('node'), 'Node.js not installed')
    def test_recommendation_ui(self):
        result = subprocess.run(['node', str(app.DEMO/'tests/frontend/recommendations_ui.js')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
