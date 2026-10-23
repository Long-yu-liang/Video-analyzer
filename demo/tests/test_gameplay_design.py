"""Design contracts use mocked inference; fixture content is not evaluation evidence."""
import base64
import copy
import json
import shutil
import subprocess
import unittest
from unittest.mock import patch
import test_gameplay as fixture_module
from test_gameplay import completion
import app
import gameplay_design as design


class DesignChecks(unittest.TestCase):
    def setUp(self):
        self.spec = design.templates()
        self.catalog = app.load_game_catalog()
        self.point = {'point_id': 'p1', 'observation_id': 'e1', 'time_s': 2,
                      'opportunity': '放置物体', 'reason': '手里有物体'}

    def option(self, **changes):
        item = {key: f'具体的{key}' for key in design.TEXT_FIELDS}
        item.update(point_id='p1', game_id='timing', target_labels=[])
        item.update(changes)
        return item

    def validate(self, recommendations):
        return design.validate_recommendations(json.dumps({'recommendations': recommendations}),
                                               [self.point], self.catalog, self.spec)

    def test_two_options_server_ids_and_reject_duplicate_three_or_unknown(self):
        result = self.validate([self.option(), self.option(game_id='hold_release')])
        self.assertEqual([r['option_id'] for r in result['recommendations']], ['p1-o1', 'p1-o2'])
        for items in ([self.option()] * 2,
                      [self.option(), self.option(game_id='hold_release'), self.option(game_id='mash')],
                      [self.option(point_id='p9')], [self.option(game_id='unknown')]):
            with self.subTest(items=items), self.assertRaises(ValueError):
                self.validate(items)
        empty = self.validate([])
        self.assertEqual(empty['recommendations'], [])
        self.assertEqual(empty['abstentions'][0]['point_id'], 'p1')

    def test_parameters_anchors_and_rules(self):
        for game_id, params in self.spec['defaults'].items():
            self.assertEqual(design.validate_parameters(game_id, params, self.spec), params)
            self.assertIn('暂停原片', design.rules_text(game_id, params))
        for changes in ({'timeout_s': True}, {'zone_min': .9}, {'zone_max': float('nan')}, {'unknown': 1}):
            with self.assertRaises(ValueError):
                design.validate_parameters('timing', {**self.spec['defaults']['timing'], **changes}, self.spec)
        with self.assertRaises(ValueError):
            design.validate_anchors('drag', [], self.spec)
        valid = [{'role': 'start', 'x': .1, 'y': .2, 'w': .1, 'h': .1},
                 {'role': 'destination', 'x': .8, 'y': .8, 'w': .1, 'h': .1}]
        self.assertEqual(design.validate_anchors('drag', valid, self.spec), valid)
        valid[0]['x'] = .99
        with self.assertRaises(ValueError):
            design.validate_anchors('drag', valid, self.spec)

    def test_brief_selected_membership_stale_manual_and_discarded(self):
        recommendations = self.validate([self.option()])['recommendations']
        gameplay = {'template_snapshot': self.spec, 'selected_point_ids': ['p1'],
                    'analysis': {'recommendations': recommendations},
                    'observations': [{'point_id': 'p1', 'requested_time_s': 2,
                                      'frame_time_s': 2.1, 'frame_sha256': 'hash'}]}
        selection = {'point_id': 'p1', 'option_id': 'p1-o1', 'status': 'accepted',
                     'adjusted_time_s': 2, 'decision_reason': '已回看'}
        def build(s):
            return design.build_brief({'selections': [s]}, {'metadata': {'duration_s': 10}},
                                      gameplay, {'p1': self.point}, {'timing': '时机'})
        saved = build(selection)[0]
        self.assertTrue(saved['ready_for_codegen'])
        self.assertEqual(saved['frame_time_s'], 2.1)
        for changes in ({'option_id': 'p2-o1'}, {'decision_reason': ''}, {'adjusted_time_s': 3}):
            with self.assertRaises(ValueError):
                build({**selection, **changes})
        manual = {**selection, 'option_id': None, 'status': 'edited', 'design': recommendations[0]}
        self.assertEqual(build(manual)[0]['origin'], 'manual')
        self.assertFalse(build({**selection, 'status': 'discarded'})[0]['ready_for_codegen'])

    def test_v19_request_uses_adjusted_frame_and_keeps_raw_output(self):
        fixtures = fixture_module.GameplayChecks(); fixtures.setUp()
        frames = [{'time_s': 5.5, 'data': base64.b64encode(b'old').decode()},
                  {'time_s': 8, 'data': base64.b64encode(b'new').decode()}]
        with patch.object(app, 'load_stage_result', return_value=fixtures.opportunity), \
             patch.object(app, 'stage_record', return_value=fixtures.events), \
             patch.object(app, 'probe', return_value=fixtures.opportunity['metadata']), \
             patch.object(app, 'extract', return_value=frames), \
             patch.object(app, 'save_gameplay_frame', return_value='results/fixture/frame.jpg'), \
             patch.object(app, 'urlopen', return_value=completion({'recommendations': [self.option()]})) as request, \
             patch.object(app, 'save_record'):
            result = app.suggest_gameplay(fixtures.raw, 'clip.mp4', 'b'*32, ['p1'], 'v19', times={'p1': 8})
        self.assertEqual(result['status'], 'success', result.get('error'))
        self.assertEqual(result['schema_version'], 2)
        self.assertEqual(result['observations'][0]['original_time_s'], 5.5)
        self.assertEqual(result['observations'][0]['frame_time_s'], 8)
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(body['max_tokens'], 8192)
        self.assertIn(base64.b64encode(b'new').decode(), json.dumps(body))
        self.assertEqual(json.loads(result['raw_text'])['recommendations'][0], self.option())
        self.assertIn('待核实', result['actual_prompt'])

    def test_bad_adjusted_time_never_calls_model(self):
        fixtures = fixture_module.GameplayChecks(); fixtures.setUp()
        for times in ({'p1': -1}, {'p1': 100}, {'p9': 2}, {'p1': True}, []):
            with patch.object(app, 'load_stage_result', return_value=fixtures.opportunity), \
                 patch.object(app, 'urlopen') as request, self.assertRaises(ValueError):
                app.suggest_gameplay(fixtures.raw, 'clip.mp4', 'b'*32, ['p1'], 'v19', times=times)
            request.assert_not_called()

    @unittest.skipUnless(shutil.which('node'), 'Node.js not installed')
    def test_browser_geometry_and_invalidation_contract(self):
        result = subprocess.run(['node', str(app.DEMO / 'tests/frontend/design_geometry.js')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_failure_is_saved_without_losing_parent_and_empty_never_calls(self):
        fixtures = fixture_module.GameplayChecks(); fixtures.setUp()
        frames = [{'time_s': 5.5, 'data': base64.b64encode(b'frame').decode()}]
        with patch.object(app, 'load_stage_result', return_value=fixtures.opportunity), \
             patch.object(app, 'stage_record', return_value=fixtures.events), \
             patch.object(app, 'probe', return_value=fixtures.opportunity['metadata']), \
             patch.object(app, 'extract', return_value=frames), \
             patch.object(app, 'save_gameplay_frame', return_value='results/fixture/frame.jpg'), \
             patch.object(app, 'urlopen', side_effect=app.URLError('fixture failure')), \
             patch.object(app, 'save_record') as saved:
            result = app.suggest_gameplay(fixtures.raw, 'clip.mp4', 'b'*32, ['p1'], 'v19')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['parent_record_id'], fixtures.opportunity['record_id'])
        self.assertEqual(len(fixtures.opportunity['analysis']['interaction_points']), 2)
        saved.assert_called_once()
        with patch.object(app, 'urlopen') as request, self.assertRaises(ValueError):
            app.suggest_gameplay(fixtures.raw, 'clip.mp4', 'b'*32, [], 'v19')
        request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
