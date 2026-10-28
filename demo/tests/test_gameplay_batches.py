"""All recommendation batching tests use local callbacks, never a model service."""
import json
import unittest
import io
import base64
import hashlib
import re
import tempfile
from pathlib import Path
from unittest.mock import patch
import app
from test_support import MockModelTestCase
from video_analyzer.gameplay import batching, recommendations


def option(identity):
    return {'point_id':identity,'game_id':'timing',**{field:'具体参与说明' for field in recommendations.FIELDS}}


class GameplayBatchTests(unittest.TestCase):
    def run_batches(self,count,reply):
        ids=[f'p{i}' for i in range(1,count+1)]
        record={'model':'qwen3.8-flash','fps':2}
        points={identity:{'point_id':identity,'time_s':i} for i,identity in enumerate(ids)}
        messages={identity:[{'type':'text','text':identity},{'type':'image_url','image_url':{'url':'data:image/jpeg;base64,fixture'}}] for identity in ids}
        calls=[]
        def call(url,body,key,batch):
            calls.append((body,list(batch['selected_point_ids'])))
            batch.update(api_attempts=[{'attempt':1}],usage={'prompt_tokens':10,'completion_tokens':20,'total_tokens':30},video_tokens=8)
            output=reply(batch)
            batch['raw_text']=json.dumps(output);batch['raw_response']={'fixture_content':batch['raw_text']};batch['finish_reason']='stop'
            return batch['raw_text']
        batching.run(record,ids,points,{'types':[{'id':'timing','name':'时机操作'}]},'prompt','catalog',messages,'https://fixture.invalid','fake',call)
        return record,calls

    def test_nineteen_points_all_sent_once_in_four_bounded_batches(self):
        def reply(batch):
            ids=batch['selected_point_ids']
            return {'recommendations':[option(identity) for identity in ids if identity!='p7'],
                    'abstentions':[{'point_id':'p7','reason':'没有清楚参与目标'}] if 'p7' in ids else []}
        record,calls=self.run_batches(19,reply)
        self.assertEqual([len(ids) for _,ids in calls],[5,5,5,4])
        self.assertEqual([identity for _,ids in calls for identity in ids],[f'p{i}' for i in range(1,20)])
        self.assertEqual(record['status'],'success')
        self.assertEqual(record['coverage']['recommended_count'],18)
        self.assertEqual(record['coverage']['abstained_count'],1)
        self.assertEqual(record['usage']['total_tokens'],120)
        self.assertEqual(record['model_request_count'],4)
        for body,ids in calls:
            self.assertEqual(sum(item['type']=='image_url' for item in body['messages'][0]['content']),len(ids))
        self.assertTrue(all(batch.get('raw_response') for batch in record['batches']))

    def test_omission_is_incomplete_not_a_fabricated_abstention(self):
        record,calls=self.run_batches(3,lambda batch:{'recommendations':[option('p1')],
            'abstentions':[{'point_id':'p2','reason':'物体没有具体参与目标'}]})
        self.assertEqual(record['status'],'partial')
        self.assertEqual(record['coverage'],{'requested_count':3,'recommended_count':1,'abstained_count':1,'incomplete_count':1,'complete':False})
        self.assertEqual(record['analysis']['incomplete_points'][0]['point_id'],'p3')
        self.assertNotIn('p3',[item['point_id'] for item in record['analysis']['abstentions']])
        self.assertEqual(len(calls),1,'omission does not trigger extra paid calls')

    def test_batch_failure_preserves_other_batches_and_continues(self):
        def reply(batch):
            if batch['batch_index']==2:raise ValueError('fixture error')
            return {'recommendations':[option(identity) for identity in batch['selected_point_ids']]}
        record,calls=self.run_batches(12,reply)
        self.assertEqual(len(calls),3)
        self.assertEqual(record['status'],'partial')
        self.assertEqual(record['coverage']['recommended_count'],7)
        self.assertEqual(record['coverage']['incomplete_count'],5)
        self.assertEqual(record['usage']['total_tokens'],90)

    def test_unknown_id_cannot_bind_across_batches(self):
        record,calls=self.run_batches(6,lambda batch:{'recommendations':[option(identity) for identity in
            (batch['selected_point_ids'] if batch['batch_index']==1 else ['p1'])]})
        self.assertEqual(record['status'],'partial')
        self.assertEqual(record['analysis']['incomplete_points'][0]['point_id'],'p6')
        self.assertEqual(len(record['analysis']['recommendations']),5)

    def test_small_batches_keep_all_images_and_cover_every_point(self):
        from video_analyzer import settings
        with patch.dict('os.environ', {'GAMEPLAY_BATCH_SIZE':'2'}):
            record,calls=self.run_batches(6,lambda batch:{'recommendations':[option(pid) for pid in batch['selected_point_ids']]})
        self.assertEqual([len(ids) for _,ids in calls],[2,2,2])
        self.assertEqual(record['batch_size'],2)
        self.assertTrue(record['coverage']['complete'])
        self.assertEqual(sum(sum(item['type']=='image_url' for item in body['messages'][0]['content']) for body,_ in calls),6)

    def test_null_usage_details_and_failed_batch_do_not_break_totals(self):
        def reply(batch):
            if batch['batch_index']==1:
                batch['usage']=None
                raise TimeoutError('fixture upstream timeout')
            batch['usage']['prompt_tokens_details']=None
            batch['usage']['completion_tokens_details']={'reasoning_tokens':0}
            return {'recommendations':[option(pid) for pid in batch['selected_point_ids']]}
        record,calls=self.run_batches(6,reply)
        self.assertEqual(record['status'],'partial')
        self.assertEqual(record['coverage']['incomplete_count'],5)
        self.assertTrue(record['usage_incomplete'])
        self.assertEqual(record['usage_reported_subtotal']['total_tokens'],30)
        self.assertNotIn('total_tokens',record['usage'])
        self.assertEqual(record['model_request_count'],2)
        self.assertIn('未完成',record['error'])

    def test_single_point_can_start_without_prior_gameplay(self):
        record,calls=self.run_batches(1,lambda batch:{'recommendations':[option('p1')]})
        self.assertEqual(record['status'],'success')
        self.assertEqual(len(calls),1)


class GameplayPipelineBatchTests(MockModelTestCase):
    def test_partial_gameplay_frames_remain_readable_and_status_is_not_promoted(self):
        raw=b'jpeg';identity='c'*32
        record={'record_id':identity,'stage':'gameplay','status':'partial','batches':[{'status':'partial'}],
                'analysis':{'recommendations':[],'incomplete_points':[{'point_id':'p2','reason':'漏答'}]},
                'observations':[{'point_id':'p1','frame_file':'results/fixture/frame.jpg','frame_sha256':hashlib.sha256(raw).hexdigest()}]}
        with tempfile.TemporaryDirectory() as folder,patch.object(app,'ROOT',Path(folder)):
            directory=Path(folder)/'results/fixture';directory.mkdir(parents=True)
            source=directory/f'game__{identity}.json';source.write_text(json.dumps(record),encoding='utf-8');before=source.read_bytes()
            (directory/'frame.jpg').write_bytes(raw)
            self.assertEqual(app.read_gameplay_frame(identity,'p1'),raw)
            self.assertEqual(app.load_stage_result(identity,'gameplay','记录')['status'],'partial')
            self.assertEqual(source.read_bytes(),before)

    def test_actual_pipeline_sends_all_nineteen_frames_in_four_calls(self):
        raw=b'fixture-video';ids=[f'p{i}' for i in range(1,20)]
        parent={'record_id':'b'*32,'stage':'opportunities','status':'success','record_file':'fixture-parent.json',
                'video_sha256':hashlib.sha256(raw).hexdigest(),'metadata':{'duration_s':120,'width':320,'height':240,'has_audio':False},
                'analysis':{'interaction_points':[{'point_id':pid,'observation_id':f'e{i}', 'time_s':i,
                    'opportunity':'参与当前行为','reason':'当前可见动作'} for i,pid in enumerate(ids,1)]}}
        frames=[{'time_s':i,'data':base64.b64encode(b'jpeg').decode()} for i in range(1,20)]
        calls=[]
        def opener(request,timeout):
            body=json.loads(request.data);content=body['messages'][0]['content']
            sent=re.findall(r'point_id=(p\d+)；','\n'.join(item['text'] for item in content if item['type']=='text'))
            calls.append(sent)
            self.assertEqual(sum(item['type']=='image_url' for item in content),len(sent))
            return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'recommendations':[option(pid) for pid in sent]})}}],
                 'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30,'prompt_tokens_details':{'image_tokens':8}}}).encode())
        with patch.object(app,'load_stage_result',return_value=parent),patch.object(app,'probe',return_value=parent['metadata']), \
             patch.object(app,'extract',return_value=frames),patch.object(app,'save_gameplay_frame',return_value='fixture-frame.jpg'), \
             patch.object(app,'urlopen',side_effect=opener),patch.object(app,'save_record'):
            result=app.suggest_gameplay(raw,'fixture.mp4','b'*32,ids,'v23')
        self.assertEqual(result['status'],'success',result.get('error'))
        self.assertEqual([len(items) for items in calls],[5,5,5,4])
        self.assertEqual([pid for items in calls for pid in items],ids)
        self.assertEqual(result['coverage']['recommended_count'],19)
        self.assertEqual(result['usage']['total_tokens'],120)
        self.assertEqual(len(result['observations']),19)
        self.assertEqual(app.model_request_count(result),4)
