"""Row-level opportunity recovery must never invent IDs, facts, or model calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import app
from output_contract import ContractError


class OpportunityRecoveryChecks(unittest.TestCase):
    def setUp(self):
        self.observations=[{'observation_id':f'e{i}','frame_time_s':t} for i,t in ((2,4),(3,6),(6,12),(7,14.5))]

    def item(self, observation_id='e2', **values):
        return {'observation_id':observation_id,'opportunity':'参与当前行为','reason':'当前可见的对象关系',**values}

    def validate(self, items):
        return app.validate_stage_opportunities(json.dumps({'interaction_points':items}),self.observations)

    def test_unreviewed_first_row_keeps_three_valid_rows_without_remapping(self):
        values=[self.item('e1'),self.item('e3'),self.item('e6'),self.item('e7')]
        original=copy.deepcopy(values)
        result=self.validate(values)
        self.assertEqual([p['observation_id'] for p in result['interaction_points']],['e3','e6','e7'])
        self.assertEqual([p['time_s'] for p in result['interaction_points']],[6,12,14.5])
        self.assertEqual(result['validation_summary'],{'returned_count':4,'accepted_count':3,'rejected_count':1})
        warning=result['warnings'][0]
        self.assertEqual(warning['path'],'interaction_points[0].observation_id')
        self.assertEqual(warning['allowed_ids'],['e2','e3','e6','e7'])
        self.assertEqual(warning['actual_value'],'e1')
        self.assertEqual(values,original)

    def test_bad_item_types_missing_fields_and_unhashable_ids_are_isolated(self):
        bad=['wrong', None, [], self.item([]), self.item({}), self.item(2), self.item(reason=''), self.item(opportunity=None)]
        result=self.validate(bad+[self.item()])
        self.assertEqual(len(result['interaction_points']),1)
        self.assertEqual(len(result['warnings']),len(bad))
        self.assertEqual(result['warnings'][0]['actual_type'],'字符串')
        self.assertIn('reason',result['warnings'][6]['path'])

    def test_empty_is_not_confused_with_all_rejected(self):
        self.assertEqual(self.validate([])['validation_summary']['returned_count'],0)
        with self.assertRaises(ContractError) as caught:self.validate([self.item('e1'),self.item(reason='')])
        detail=caught.exception.detail
        self.assertEqual(detail['code'],'no_valid_opportunities')
        self.assertEqual(detail['rejected_count'],2)
        self.assertIn('并非未发现机会',str(caught.exception))

    def test_duplicates_are_warned_distinct_opportunities_same_picture_allowed(self):
        result=self.validate([self.item(),self.item(),self.item(opportunity='另一个可参与目标')])
        self.assertEqual(len(result['interaction_points']),2)
        self.assertEqual(result['warnings'][0]['code'],'duplicate_opportunity')

    def test_invalid_json_root_and_array_are_precisely_located(self):
        for raw,path in (('{bad','$'),('[]','$'),('{}','interaction_points'),('null','$'),
                         ('{"interaction_points":null}','interaction_points')):
            with self.subTest(raw=raw),self.assertRaises(ContractError) as caught:
                app.validate_stage_opportunities(raw,self.observations)
            self.assertEqual(caught.exception.detail['path'],path)

    def test_other_stage_type_errors_are_not_generic_reference_errors(self):
        event={'summary':'事实','events':[{'start_s':1,'end_s':2,'observation_s':True}], 'limitations':[]}
        with self.assertRaises(ContractError) as caught:app.validate_stage_events(json.dumps(event),4)
        self.assertEqual(caught.exception.detail['path'],'events[0].observation_s')
        with self.assertRaises(ContractError) as caught:
            app.validate_gameplay('{"recommendations":[{"point_id":[],"game_id":"timing"}]}',[],app.load_game_catalog())
        self.assertEqual(caught.exception.detail['path'],'recommendations[0].point_id')

    def test_pipeline_persists_partial_warnings_and_distinguishes_total_failure(self):
        parent={'record_id':'a'*32,'record_file':'fixture-events.json','video_sha256':app.hashlib.sha256(b'fixture').hexdigest(),
                'analysis':{'events':[{'observation_id':o['observation_id'],'observation_s':o['frame_time_s'],
                                      'end_s':o['frame_time_s'],'description':'可见事件','evidence':'可见物体'} for o in self.observations]}}
        frames=[{'time_s':o['frame_time_s'],'data':'aW1hZ2U='} for o in self.observations]
        partial={'interaction_points':[self.item('e1'),self.item('e3')]}
        invalid={'interaction_points':[self.item('e1')]}
        with patch.object(app,'stage_record',return_value=parent), \
                patch.object(app,'probe',return_value={'duration_s':16}),patch.object(app,'extract',return_value=frames), \
                patch.object(app,'call_v13_model',side_effect=[json.dumps(partial),json.dumps(invalid)]), \
                patch.object(app,'save_record') as saved:
            selected=[o['observation_id'] for o in self.observations]
            result=app.suggest_opportunities(b'fixture','fixture.mp4','a'*32,selected,'v15')
            self.assertEqual(result['status'],'success')
            self.assertEqual(result['validation_status'],'passed_with_warnings')
            self.assertEqual(len(result['validation_warnings']),1)
            result=app.suggest_opportunities(b'fixture','fixture.mp4','a'*32,selected,'v15')
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['validation_error']['code'],'no_valid_opportunities')
            self.assertEqual(saved.call_count,2)

    def test_local_revalidation_is_derived_and_never_changes_source_or_charges(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(app,'ROOT',Path(folder)), \
                patch.object(app,'urlopen') as request,patch.object(app,'call_v13_model') as inference:
            parent={'record_id':'a'*32,'stage':'events','status':'success','model':'qwen3-vl-plus','mode':'direct',
                    'video_name':'clip.mp4','video_sha256':'f'*64,
                    'analysis':{'events':[{'observation_id':f'e{i}'} for i in range(1,8)]}}
            app.save_record(parent)
            source={'record_id':'b'*32,'stage':'opportunities','status':'failed','error':'old error',
                    'parent_record_id':parent['record_id'],'parent_record_file':parent['record_file'],
                    'model':'qwen3-vl-plus','mode':'frames','video_name':'clip.mp4','video_sha256':'f'*64,
                    'metadata':{'duration_s':16},'finish_reason':'stop','usage':{'total_tokens':99},
                    'selected_event_ids':['e2','e3','e6','e7'],'observations':self.observations,
                    'raw_text':json.dumps({'interaction_points':[self.item('e1'),self.item('e6')]})}
            app.save_record(source);original=(app.ROOT/source['record_file']).read_bytes()
            recovered=app.revalidate_opportunity_record(source['record_id'])
            self.assertEqual(recovered['record_kind'],'local_revalidation')
            self.assertFalse(recovered['model_call_made'])
            self.assertEqual(recovered['usage'],{})
            self.assertEqual(recovered['raw_text'],source['raw_text'])
            self.assertEqual((app.ROOT/source['record_file']).read_bytes(),original)
            self.assertNotEqual(source['record_id'],recovered['record_id'])
            self.assertEqual(recovered['analysis']['interaction_points'][0]['time_s'],12)
            request.assert_not_called();inference.assert_not_called()
            source['finish_reason']='length';app.save_record({**source,'record_id':'c'*32})
            with self.assertRaisesRegex(ValueError,'未正常结束'):app.revalidate_opportunity_record('c'*32)


if __name__=='__main__':unittest.main()
