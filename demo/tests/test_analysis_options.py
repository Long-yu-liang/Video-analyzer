"""Thinking and truncation regressions, with local mocked responses only."""
import io
import json
import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from video_analyzer import settings, application as app
from video_analyzer.models.requests import build_request
from video_analyzer.models.client import call_v13_model, TruncatedOutputError

class AnalysisOptionsTests(unittest.TestCase):
    def test_uppercase_model_config_and_original_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            store=settings.Store(folder)
            store.save({'revision':0,'values':{'ANALYSIS_MAX_TOKENS':'24576','ANALYSIS_THINKING':'disabled'}})
            with settings.scope(folder):
                _,body,_=build_request('Qwen3.8-Flash-Next','frames',2,'',[],
                    {'duration_s':10,'has_audio':False},'fixture',{'Qwen3.8-Flash-Next':'beeroute'})
            self.assertEqual(body['model'],'Qwen3.8-Flash-Next')
            self.assertEqual(body['max_tokens'],24576)
            self.assertIs(body['enable_thinking'],False)
            self.assertEqual(body['response_format'],{'type':'json_object'})
            for mode,expected in [('enabled',True),('default',None)]:
                store.save({'revision':store.read()['revision'],'values':{'ANALYSIS_THINKING':mode}})
                with settings.scope(folder):
                    options=settings.analysis_options('Qwen3.8-Flash-Next')
                    self.assertIs(options.get('enable_thinking'),expected)
                    self.assertNotIn('enable_thinking',settings.analysis_options('gemini-test'))

    def test_chat_template_parameter_survives_transport_and_json_format(self):
        with tempfile.TemporaryDirectory() as folder:
            settings.Store(folder).save({'revision':0,'values':{'ANALYSIS_THINKING_PARAMETER':'chat_template','ANALYSIS_THINKING':'disabled'}})
            sent=[]
            def opener(request,timeout):
                sent.append(json.loads(request.data))
                return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{}'}}],
                    'usage':{'completion_tokens':2,'completion_tokens_details':{'reasoning_tokens':0}}}).encode())
            with settings.scope(folder):
                record={'fps':2}
                call_v13_model('https://fixture',{'model':'Qwen3.8-Flash-Next'},'fixture',record,opener,lambda _:None,json.loads)
            self.assertNotIn('enable_thinking',sent[0])
            self.assertEqual(sent[0]['chat_template_kwargs'],{'enable_thinking':False})
            self.assertEqual(sent[0]['response_format'],{'type':'json_object'})
            self.assertEqual(record['request_options']['chat_template_kwargs'],{'enable_thinking':False})

    def test_invalid_options_rejected(self):
        for values in ({'ANALYSIS_MAX_TOKENS':'0'},{'ANALYSIS_MAX_TOKENS':'500000'},
                       {'ANALYSIS_MAX_TOKENS':'1.5'},{'ANALYSIS_THINKING':'off'},{'GAMEPLAY_BATCH_SIZE':'0'},{'GAMEPLAY_BATCH_SIZE':'6'},
                       {'ANALYSIS_THINKING_PARAMETER':'unknown'}):
            with self.assertRaises(ValueError):settings.validate(values)

    def test_reasoning_exhaustion_keeps_raw_and_diagnosis(self):
        raw={'choices':[{'finish_reason':'length','message':{'content':''}}],
             'usage':{'completion_tokens':4096,'completion_tokens_details':{'reasoning_tokens':4096}}}
        record={'fps':2}
        calls=[]
        def opener(request,timeout):
            calls.append(json.loads(request.data))
            return io.BytesIO(json.dumps(raw).encode())
        with self.assertRaises(TruncatedOutputError) as caught:
            call_v13_model('https://fixture/v1',{'model':'Qwen3.8-Flash-Next','max_tokens':4096},
                'fixture',record,opener,lambda _:None,json.loads)
        self.assertTrue(caught.exception.thinking_budget_exhausted)
        self.assertEqual(len(calls),1)
        self.assertIs(calls[0]['enable_thinking'],False)
        self.assertEqual(record['raw_response'],raw)
        self.assertTrue(record['truncation_diagnosis']['thinking_budget_exhausted'])

    def test_thinking_exhaustion_does_not_split_video(self):
        meta={'duration_s':16.,'has_audio':False,'video_codec':'h264'}
        error=TruncatedOutputError('fixture reasoning limit')
        error.thinking_budget_exhausted=True
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'video.mp4';source.write_bytes(b'fixture')
            parent={'model':'Qwen3.8-Flash-Next','mode':'direct','fps':2,'segments':[],'metadata':meta}
            with patch.object(app,'prepare_full_video',return_value=(source,meta,'original_video')), \
                 patch.object(app,'build_request',return_value=('https://fixture',{'messages':[{'content':[{'text':'fixture'}]}]},False)), \
                 patch.object(app,'call_v13_model',side_effect=error) as cloud,patch.object(app,'clip_video') as clip:
                with self.assertRaises(TruncatedOutputError):
                    app.analyze_segment(source,Path(folder),0,16,0,16,'fixture','fixture',parent)
            self.assertEqual(cloud.call_count,1)
            clip.assert_not_called()
            self.assertEqual(len(parent['segments']),1)

if __name__=='__main__':unittest.main()
