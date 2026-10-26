"""v21 identity protocol; all requests are mocked, older prompt inputs stay reproducible."""
import json
import unittest
from unittest.mock import patch
import app


from test_support import MockModelTestCase


class OpportunityIdentityChecks(MockModelTestCase):
    def run_version(self, version):
        raw=b'fixture'
        events=[{'observation_id':f'e{i}','observation_s':float(i),'end_s':float(i),
                 'description':description,'evidence':'画面依据'} for i,description in (
                     (1,'已排除的早期事件文本'),(2,'保留的准备动作'),(3,'已排除的中间事件文本'),(5,'保留的后续画面'))]
        parent={'record_id':'a'*32,'record_file':'fixture-events.json',
                'video_sha256':app.hashlib.sha256(raw).hexdigest(),'analysis':{'events':events}}
        frames=[{'time_s':float(i),'data':'aW1hZ2U='} for i in (1,2,3,5)]
        output={'interaction_points':[{'observation_id':'e2','opportunity':'参与动作','reason':'画面依据'}]}
        with patch.object(app,'stage_record',return_value=parent),patch.object(app,'probe',return_value={'duration_s':6}), \
                patch.object(app,'extract',return_value=frames),patch.object(app,'save_record'), \
                patch.object(app,'call_v13_model',return_value=json.dumps(output)) as request:
            result=app.suggest_opportunities(raw,'fixture.mp4','a'*32,['e5','e2'],version)
        self.assertEqual(result['status'],'success')
        return result,request.call_args.args[1]

    def test_v21_dynamic_enum_preserves_sparse_original_labels(self):
        result,body=self.run_version('v21')
        contract=result['opportunity_input_contract']
        self.assertEqual(contract['allowed_observation_ids'],['e2','e5'])
        self.assertEqual(contract['output_schema']['properties']['interaction_points']['items']['properties']['observation_id']['enum'],['e2','e5'])
        contents=body['messages'][0]['content']
        self.assertEqual(sum(p['type']=='image_url' for p in contents),2)
        self.assertIn(json.dumps(contract,ensure_ascii=False),result['actual_prompt'])
        self.assertEqual(body['max_tokens'],4096)
        self.assertEqual(body['model'],'qwen3-vl-plus')
        self.assertNotIn('response_format',body)  # Existing request helper adds JSON Object, not strict schema.
        self.assertEqual(result['observations'][0]['observation_id'],'e2')
        self.assertEqual(result['observations'][1]['observation_id'],'e5')

    def test_v21_does_not_leak_excluded_event_text_to_earlier_context(self):
        result,_=self.run_version('v21')
        self.assertNotIn('已排除的早期事件文本',result['actual_prompt'])
        self.assertNotIn('已排除的中间事件文本',result['actual_prompt'])
        self.assertIn('截至此刻的先前事件：保留的准备动作',result['actual_prompt'])
        self.assertEqual(result['context_policy'],'reviewed_events_only')

    def test_v15_input_policy_remains_unchanged_and_catalog_selects_new_stage(self):
        old,_=self.run_version('v15')
        self.assertIn('已排除的早期事件文本',old['actual_prompt'])
        self.assertNotIn('opportunity_input_contract',old)
        self.assertEqual(app.load_stage_prompt('opportunities')[0].parent.name,'v21')
        prompt=app.load_stage_prompt('opportunities','v21')[1]
        self.assertIn('allowed_observation_ids',prompt)
        self.assertNotIn('"observation_id":"e1"',prompt)
        self.assertEqual(app.stage_prompt_catalog()['opportunities'][-1]['version'],'v21')


if __name__=='__main__':unittest.main()
