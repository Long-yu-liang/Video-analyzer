"""Short tail handling is decided locally before paid requests."""
import io
import json
from pathlib import Path
from unittest.mock import patch
import app
from video_analyzer.media.processing import segment_windows
from test_support import MockModelTestCase


def response(start=1,end=2,observed=1.5):
    data={'summary':'可见行为','events':[{'start_s':start,'end_s':end,'observation_s':observed,
          'description':'人物准备操作','evidence':'手靠近物体','uncertainty':'无'}],'limitations':[]}
    return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(data)}}],
        'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15,'prompt_tokens_details':{'image_tokens':8}}}).encode())


class ShortSegmentTests(MockModelTestCase):
    def test_tail_is_merged_without_gaps_or_tiny_calls(self):
        self.assertEqual(segment_windows(40.75),[(0.,20.,0.,20.),(20.,40.75,19.,40.75)])
        for duration in (25.01,40.,40.75,44.99,45.,58.,60.1,81.,120.):
            rows=segment_windows(duration)
            self.assertEqual(rows[0][0],0)
            self.assertEqual(rows[-1][1],duration)
            for previous,current in zip(rows,rows[1:]):self.assertEqual(previous[1],current[0])
            if len(rows)>1:self.assertGreaterEqual(rows[-1][1]-rows[-1][0],5.)

    def test_4075_seconds_uses_two_requests_and_preserves_tail_events(self):
        meta={'duration_s':40.75,'width':320,'height':240,'has_audio':False,'video_codec':'h264'}
        def clip(source,target,start,end):target.write_bytes(b'clip');return target
        with patch.object(app,'probe',side_effect=[meta,{**meta,'duration_s':20},{**meta,'duration_s':21.75}]), \
             patch.object(app,'clip_video',side_effect=clip),patch.object(app,'urlopen',side_effect=[response(),response(21,21.7,21.5)]) as cloud, \
             patch.object(app,'save_record',side_effect=lambda r:r.update(record_file='fixture.json')):
            result=app.analyze_events(b'video','test.mp4','v22')
        self.assertEqual(result['status'],'success',result.get('error'))
        self.assertEqual(cloud.call_count,2)
        self.assertEqual(result['analysis']['events'][-1]['observation_s'],40.5)
        self.assertTrue(result['segment_coverage']['complete'])

    def test_subsecond_video_uses_frames_before_first_request(self):
        meta={'duration_s':.75,'width':320,'height':240,'has_audio':True,'video_codec':'h264'}
        with patch.object(app,'probe',return_value=meta),patch.object(app,'extract',return_value=[{'time_s':0.5,'data':'ZmFrZQ=='}]), \
             patch.object(app,'urlopen',return_value=response(0,.7,.5)) as cloud,patch.object(app,'strip_audio') as strip, \
             patch.object(app,'save_record',side_effect=lambda r:r.update(record_file='fixture.json')):
            result=app.analyze_events(b'video','short.mp4','v22',mode='direct')
        self.assertEqual(result['status'],'success',result.get('error'))
        self.assertEqual(cloud.call_count,1)
        strip.assert_not_called()
        content=json.loads(cloud.call_args.args[0].data)['messages'][0]['content']
        self.assertTrue(any(item['type']=='image_url' for item in content))
        self.assertFalse(any(item['type']=='video_url' for item in content))
        self.assertEqual(result['segments'][0]['mode'],'frames')
        self.assertIn('input_fallback_reason',result['segments'][0])
