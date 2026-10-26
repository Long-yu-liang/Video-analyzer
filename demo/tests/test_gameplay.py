"""Day 3 gameplay recommendation contracts. Model payloads below are fixtures."""
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


def completion(answer):
    payload = {'choices': [{'message': {'content': json.dumps(answer, ensure_ascii=False)}, 'finish_reason': 'stop'}],
               'usage': {'prompt_tokens_details': {'image_tokens': 100, 'text_tokens': 20}}}
    return io.BytesIO(json.dumps(payload).encode())


from test_support import MockModelTestCase


class GameplayChecks(MockModelTestCase):
    def setUp(self):
        self.raw = b'fixture'
        self.opportunity = {
            'record_id': 'b' * 32, 'stage': 'opportunities', 'status': 'success',
            'video_sha256': app.hashlib.sha256(self.raw).hexdigest(),
            'record_file': 'results/qwen3-vl-plus/v14/opp.json', 'parent_record_id': 'a' * 32,
            'video_name': 'clip.mp4', 'metadata': {'duration_s': 58, 'width': 320, 'height': 240, 'has_audio': False},
            'analysis': {'interaction_points': [
                {'observation_id': 'e7', 'event_id': 'e7', 'time_s': 5.5, 'opportunity': '选择传球', 'reason': '持球'},
                {'observation_id': 'e8', 'event_id': 'e8', 'time_s': 6.5, 'opportunity': '选择出手', 'reason': '球未离手'}]}}
        self.events = {'record_id': 'a' * 32, 'stage': 'events', 'status': 'success', 'analysis': {'events': [
            {'observation_id': 'e7', 'description': '黑队持球', 'evidence': '球在手中'},
            {'observation_id': 'e9', 'description': '后来的得分不该出现', 'evidence': '球入网'}]}}
        self.points = app.indexed_points(self.opportunity)

    def catalog(self):
        return app.load_game_catalog()

    def recommendation(self, **changes):
        item = {'point_id': 'p1', 'time_s': 100, 'game_id': 'choice', 'fit': '画面里有两条传球方向',
                'player_goal': '作出选择', 'player_action': '选择传球对象', 'success': '选中一个对象',
                'failure': '未选择', 'feedback': '显示所选对象'}
        item.update(changes)
        return item

    def test_catalog_is_the_closed_set_and_not_a_single_stage_prompt(self):
        catalog = self.catalog()
        self.assertEqual([item['id'] for item in catalog['types']], list(app.GAME_IDS))
        self.assertEqual(len({item['id'] for item in catalog['types']}), 7)
        self.assertTrue(all(path.name != 'gameplay.txt' for _, path in app.prompt_paths()))
        self.assertIn('不生成 JavaScript', app.load_gameplay_prompt()[1])
        self.assertIn('拿到球后的出手', app.load_gameplay_prompt('v18')[1])
        self.assertEqual(app.load_gameplay_prompt()[0].parent.name, 'v20')
        self.assertEqual([path.parent.name for path in app.stage_prompt_paths('events')], ['v13', 'v14'])
        self.assertIn('v14', [path.parent.name for path in app.stage_prompt_paths('opportunities')])
        self.assertEqual(app.load_stage_prompt('opportunities', 'v14')[0].parent.name, 'v14')
        with self.assertRaises(ValueError):
            app.load_stage_prompt('events', 'v15')

    def test_validation_owns_time_and_rejects_unknown_or_duplicate_points(self):
        points = [self.points[0]]
        result = app.validate_gameplay(json.dumps({'recommendations': [self.recommendation()]}), points, self.catalog())
        self.assertEqual(result['recommendations'][0]['time_s'], 5.5)
        self.assertEqual(result['recommendations'][0]['game_name'], '剧情选择')
        self.assertEqual(app.validate_gameplay('{"recommendations":[]}', points, self.catalog())['recommendations'], [])
        for answer in (
                {'recommendations': [self.recommendation(game_id='soccer')]},
                {'recommendations': [self.recommendation(point_id='p9')]},
                {'recommendations': [self.recommendation(), self.recommendation(fit='另一条')]}):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                app.validate_gameplay(json.dumps(answer), points, self.catalog())

    def test_third_call_sends_catalog_and_only_the_selected_frame(self):
        frames = [{'time_s': 5.5, 'data': base64.b64encode(b'frame-a').decode()},
                  {'time_s': 6.5, 'data': base64.b64encode(b'frame-b').decode()}]
        with patch.object(app, 'load_stage_result', return_value=self.opportunity), \
             patch.object(app, 'stage_record', return_value=self.events), \
             patch.object(app, 'probe', return_value=self.opportunity['metadata']), \
             patch.object(app, 'extract', return_value=frames), \
             patch.object(app, 'urlopen', return_value=completion({'recommendations': [self.recommendation()]})) as request, \
             patch.object(app, 'save_record', side_effect=lambda record: record.update(record_file='gameplay.json')):
            result = app.suggest_gameplay(self.raw, 'clip.mp4', 'b' * 32, ['p1'], 'v16')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['prompt_file'], 'prompts/v16/gameplay.txt')
        self.assertEqual(result['analysis']['recommendations'][0]['time_s'], 5.5)
        self.assertEqual(result['observations'][0]['frame_sha256'], app.hashlib.sha256(b'frame-a').hexdigest())
        self.assertIn('剧情选择', result['actual_prompt'])
        self.assertIn('黑队持球', result['actual_prompt'])
        self.assertNotIn('后来的得分不该出现', result['actual_prompt'])
        self.assertNotIn('截至此刻的先前事件', result['actual_prompt'])
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(sum(part['type'] == 'image_url' for part in body['messages'][0]['content']), 1)
        self.assertIn(base64.b64encode(b'frame-a').decode(), body['messages'][0]['content'][-1]['image_url']['url'])

    def test_empty_selection_and_wrong_video_never_call_model(self):
        with patch.object(app, 'urlopen') as request:
            with self.assertRaises(ValueError):
                app.suggest_gameplay(self.raw, 'clip.mp4', 'b' * 32, [])
            request.assert_not_called()
        with patch.object(app, 'load_stage_result', return_value=self.opportunity), patch.object(app, 'urlopen') as request:
            with self.assertRaises(ValueError):
                app.suggest_gameplay(b'wrong', 'clip.mp4', 'b' * 32, ['p1'])
            request.assert_not_called()

    def test_record_lookup_reads_nested_results_without_rewriting_them(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            nested_id, top_id = 'c' * 32, 'd' * 32
            nested = folder / 'v14'
            nested.mkdir()
            (nested / f'clip__{nested_id}.json').write_text('{}', encoding='utf-8')
            (folder / f'clip__{top_id}.json').write_text('{}', encoding='utf-8')
            self.assertEqual(app.saved_record_paths(nested_id, folder), [nested / f'clip__{nested_id}.json'])
            self.assertEqual(len(app.saved_record_paths(top_id, folder)), 1)
            (folder / f'copy__{nested_id}.json').write_text('{}', encoding='utf-8')
            self.assertEqual(len(app.saved_record_paths(nested_id, folder)), 2)
        found = app.saved_record_paths('c50a07d0c8c1691549508ce6f17d5e65')
        self.assertEqual(len(found), 1)
        self.assertIn('v14', found[0].parts)
        events = app.stage_record('7fa3da3fb8ff803438e62872eb808945')
        self.assertEqual(events['stage'], 'events')
        self.assertIn('v14/', events['record_file'])

    def gameplay_record(self):
        return {'record_id': 'e' * 32, 'stage': 'gameplay', 'status': 'success',
                'parent_record_id': self.opportunity['record_id'],
                'record_file': 'results/qwen3-vl-plus/gameplay.json', 'selected_point_ids': ['p1', 'p2'],
                'analysis': {'recommendations': [{
                    'point_id': 'p1', 'observation_id': 'e7', 'time_s': 5.5, 'opportunity': '选择传球',
                    'reason': '持球', 'game_id': 'choice', 'game_name': '剧情选择', 'fit': '两条方向',
                    'player_goal': '作出选择', 'player_action': '选择传球对象', 'success': '选中',
                    'failure': '未选', 'feedback': '显示选项'}]}}

    def save_brief(self, selections):
        gameplay = self.gameplay_record()

        def load(record_id, stage, label):
            return self.opportunity if stage == 'opportunities' else gameplay

        with patch.object(app, 'load_stage_result', side_effect=load), \
             patch.object(app, 'save_record', side_effect=lambda record: record.update(record_file='brief.json')) as saved, \
             patch.object(app, 'urlopen') as request:
            result = app.save_gameplay_brief({'opportunity_record_id': self.opportunity['record_id'],
                                              'gameplay_record_id': gameplay['record_id'], 'selections': selections})
        request.assert_not_called()
        return result, saved

    def test_brief_keeps_accepted_text_and_edited_choice_without_model(self):
        result, saved = self.save_brief([
            {'point_id': 'p1', 'adjusted_time_s': 5.2, 'status': 'accepted', 'player_action': '忽略客户端改写'},
            {'point_id': 'p2', 'adjusted_time_s': 6.5, 'status': 'edited', 'game_id': 'timing', 'fit': '球还在手中',
             'player_goal': '卡准出手', 'player_action': '在球离手前按下', 'success': '窗口内按下',
             'failure': '过早或过晚', 'feedback': '显示早了或晚了'}])
        self.assertEqual(saved.call_count, 1)
        accepted, edited = result['selections']
        self.assertEqual(accepted['player_action'], '选择传球对象')
        self.assertEqual(accepted['adjusted_time_s'], 5.2)
        self.assertEqual(accepted['ai_game_id'], 'choice')
        self.assertEqual(accepted['resume'], '到点暂停，结束后继续播放')
        self.assertEqual(edited['game_id'], 'timing')
        self.assertIsNone(edited['ai_game_id'])
        self.assertEqual(edited['player_action'], '在球离手前按下')

    def test_brief_rejects_bad_time_and_empty_action_without_model(self):
        gameplay = self.gameplay_record()

        def load(record_id, stage, label):
            return self.opportunity if stage == 'opportunities' else gameplay

        edited = {'point_id': 'p2', 'adjusted_time_s': 6.5, 'status': 'edited', 'game_id': 'timing',
                  'player_goal': '卡准出手', 'player_action': '按下', 'success': '命中', 'failure': '错过', 'feedback': '提示'}
        cases = (
            [{'point_id': 'p1', 'adjusted_time_s': 80, 'status': 'accepted'}, edited],
            [{'point_id': 'p1', 'adjusted_time_s': 5.5, 'status': 'discarded'}, dict(edited, player_action='   ')],
        )
        for selections in cases:
            with self.subTest(selections=selections), patch.object(app, 'load_stage_result', side_effect=load), \
                 patch.object(app, 'save_record') as saved, patch.object(app, 'urlopen') as request:
                with self.assertRaises(ValueError):
                    app.save_gameplay_brief({'opportunity_record_id': self.opportunity['record_id'],
                                             'gameplay_record_id': gameplay['record_id'], 'selections': selections})
                request.assert_not_called()
                saved.assert_not_called()
