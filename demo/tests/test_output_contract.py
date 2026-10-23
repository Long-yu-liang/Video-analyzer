"""Formatting regressions are local replays, never paid requests."""
import io
import json
import unittest
from unittest.mock import patch
import app
import gameplay_recommendations as games
from output_contract import ContractError
from test_gameplay import completion


class OutputChecks(unittest.TestCase):
    def setUp(self):
        self.points = [{'point_id': 'p1', 'time_s': 1}, {'point_id': 'p2', 'time_s': 2}]
        self.catalog = app.load_game_catalog()
        self.option = {'point_id': 'p1', 'game_id': 'timing',
                       **{key: '具体的'+key for key in games.FIELDS}}

    def validate(self, notes=None, recommendations=None, include=True):
        value = {'recommendations': recommendations if recommendations is not None else [self.option]}
        if include:
            value['abstentions'] = notes
        return games.validate(json.dumps(value), self.points, self.catalog)

    def test_bad_auxiliary_item_retains_recommendations_with_exact_warning(self):
        result = self.validate(['p2：没有合适玩法'])
        self.assertEqual(len(result['recommendations']), 1)
        self.assertEqual(result['warnings'][0]['path'], 'abstentions[0]')
        self.assertIn('应为对象，实际为字符串', result['warnings'][0]['message'])
        self.assertEqual(result['warnings'][0]['actual_type'], '字符串')
        self.assertEqual(result['abstentions'][0]['point_id'], 'p2')

    def test_bad_auxiliary_container_or_reason_is_warning_not_failure(self):
        for notes, path in [('原因', 'abstentions'), (None, 'abstentions'),
                            ([{'point_id':'p2','reason':3}], 'abstentions[0].reason'),
                            ([{'point_id':'p2'}], 'abstentions[0].reason'),
                            ([{'point_id':[]}], 'abstentions[0].point_id')]:
            with self.subTest(notes=notes):
                result = self.validate(notes)
                self.assertEqual(len(result['recommendations']), 1)
                self.assertEqual(result['warnings'][0]['path'], path)

    def test_valid_missing_empty_conflicting_and_duplicate_notes(self):
        valid = {'point_id':'p2','reason':'没有合适目标'}
        self.assertEqual(self.validate([valid])['warnings'], [])
        self.assertEqual(self.validate(include=False)['warnings'], [])
        self.assertEqual(self.validate([])['warnings'], [])
        self.assertEqual(self.validate([valid,valid])['warnings'][0]['code'], 'duplicate_abstention')
        self.assertEqual(self.validate([{'point_id':'p1','reason':'放弃'}])['warnings'][0]['code'], 'conflicting_abstention')
        empty = self.validate(['p1：没有玩法'], recommendations=[])
        self.assertEqual(empty['recommendations'], [])
        self.assertEqual(len(empty['abstentions']), 2)

    def test_unknown_points_and_missing_core_remain_fatal_with_paths(self):
        cases = [({'point_id':'p99'},'recommendations[0].point_id'),
                 ({'game_id':'unknown'},'recommendations[0].game_id'),
                 ({'player_action':3},'recommendations[0].player_action'),
                 ({'fit':''},'recommendations[0].fit')]
        for changes, path in cases:
            with self.subTest(changes=changes), self.assertRaises(ContractError) as raised:
                self.validate(['malformed auxiliary'], recommendations=[{**self.option,**changes}])
            self.assertEqual(raised.exception.detail['path'], path)
        with self.assertRaises(ContractError) as raised:
            self.validate([{'point_id':'p99','reason':'不能把未知点当合法辅助信息'}])
        self.assertEqual(raised.exception.detail['path'], 'abstentions[0].point_id')

    def test_invalid_json_root_and_missing_array_have_locatable_errors(self):
        for text, path in [('{"recommendations":[}', '$'), ('[]','$'), ('{}','recommendations'),
                           ('{"recommendations":null}','recommendations'),
                           ('{"recommendations":[],"x":NaN}','$')]:
            with self.subTest(text=text), self.assertRaises(ContractError) as raised:
                games.validate(text,self.points,self.catalog)
            self.assertEqual(raised.exception.detail['path'],path)

    def test_schema_uses_same_core_fields_and_actual_ids(self):
        schema = games.output_schema(self.points,self.catalog)
        rec = schema['properties']['recommendations']['items']
        self.assertEqual(rec['required'],['point_id','game_id',*games.FIELDS])
        self.assertNotIn('title',rec['required'])
        self.assertEqual(rec['properties']['point_id']['enum'],['p1','p2'])
        self.assertEqual(schema['properties']['abstentions']['items']['required'],['point_id','reason'])

    def body(self):
        return {'model':'qwen3-vl-plus','messages':[{'role':'user','content':'返回JSON'}],'max_tokens':4096}

    def test_json_object_is_requested_and_recorded_without_claiming_strict_schema(self):
        record={'fps':2}
        with patch.object(app,'urlopen',return_value=completion({'recommendations':[]})) as request:
            app.call_v13_model('https://fixture.invalid',self.body(),'fixture-key',record)
        body=json.loads(request.call_args.args[0].data)
        self.assertEqual(body['response_format'],{'type':'json_object'})
        self.assertEqual(record['request_options']['response_format'],{'type':'json_object'})
        self.assertEqual(len(record['api_attempts']),1)
        self.assertIn('raw_response',record)

    def test_explicit_json_mode_rejection_falls_back_once_and_keeps_reason(self):
        error=app.HTTPError('https://fixture.invalid',400,'bad request',{},
            io.BytesIO(b'{"error":"response_format json_object unsupported"}'))
        record={'fps':2}
        with patch.object(app,'urlopen',side_effect=[error,completion({'recommendations':[]})]) as request:
            app.call_v13_model('https://fixture.invalid',self.body(),'fixture-key',record)
        bodies=[json.loads(call.args[0].data) for call in request.call_args_list]
        self.assertIn('response_format',bodies[0]);self.assertNotIn('response_format',bodies[1])
        self.assertTrue(record['json_mode_fallback'])
        self.assertEqual(len(record['api_attempts']),2)
        self.assertEqual(record['request_warnings'][0]['code'],'json_mode_unsupported')

    def test_other_http_errors_and_wrong_json_do_not_trigger_hidden_retries(self):
        for code, detail in [(401,b'unauthorized'),(429,b'rate limit'),
                              (400,b'{"error":"response_format requires JSON in messages"}')]:
            error=app.HTTPError('https://fixture.invalid',code,'error',{},io.BytesIO(detail))
            with self.subTest(code=code,detail=detail), patch.object(app,'urlopen',side_effect=error) as request, self.assertRaises(app.HTTPError):
                app.call_v13_model('https://fixture.invalid',self.body(),'fixture-key',{'fps':2})
            self.assertEqual(request.call_count,1)
        with patch.object(app,'urlopen',return_value=completion({'recommendations':'wrong type'})) as request:
            text=app.call_v13_model('https://fixture.invalid',self.body(),'fixture-key',{'fps':2})
            with self.assertRaises(ContractError):
                games.validate(text,self.points,self.catalog)
            self.assertEqual(request.call_count,1)


if __name__=='__main__':
    unittest.main()
