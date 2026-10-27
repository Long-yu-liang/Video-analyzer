"""Transport regression tests; all responses are local mocks, no paid calls."""
import io
import json
from pathlib import Path
import unittest
import test_game_workbench as fixtures
from video_analyzer.catalog.prompts import game_generation_catalog
from video_analyzer.gameplay import generation
from video_analyzer.gameplay.output import OutputFormatError, parse_output


def sections(data):
    return ('<<<GAME_DESIGN>>>\n'+json.dumps(data['design'], ensure_ascii=False)+'\n<<<END_GAME_DESIGN>>>\n'
            '<<<GAME_JAVASCRIPT>>>\n'+data['javascript']+'\n<<<END_GAME_JAVASCRIPT>>>\n'
            '<<<GAME_CSS>>>\n'+data['css']+'\n<<<END_GAME_CSS>>>')


class OutputTests(unittest.TestCase):
    def test_wrong_metadata_js_separator_is_compatible_without_code_rewrite(self):
        data=fixtures.output()
        raw=sections(data).replace('<<<END_GAME_DESIGN>>>\n<<<GAME_JAVASCRIPT>>>','<<<END_GAME_JAVASCRIPT>>>')
        parsed,fmt=parse_output(raw)
        self.assertEqual(parsed,data)
        self.assertEqual(fmt,'raw_sections_compatible_v1')
        # A missing redundant JS start is also identifiable after the complete JSON.
        self.assertEqual(parse_output(sections(data).replace('<<<GAME_JAVASCRIPT>>>\n',''))[0],data)

    def test_markdown_wrappers_preserve_sources(self):
        data=fixtures.output();data['css']=r'#game::after {content:"\2192";}'
        text='```json\n'+json.dumps(data['design'])+'\n```\n```js\n'+data['javascript']+'\n```\n```css\n'+data['css']+'\n```'
        self.assertEqual(parse_output(text),(data,'markdown_blocks_v1'))
        self.assertEqual(parse_output('```json\n'+json.dumps(data)+'\n```'),(data,'json_fenced'))

    def test_raw_code_escapes_and_newlines_are_unchanged(self):
        data=fixtures.output()
        data['javascript'] += '\n'+r'const pattern = /\d+\s*/; const text = "line\nend";'
        data['css'] = '\n'+r'#game::after { content: "\2192"; }'+'\n'
        parsed, transport=parse_output(sections(data))
        self.assertEqual(parsed,data)
        self.assertEqual(transport,'raw_sections_v1')
        self.assertEqual(parse_output(json.dumps(data))[0],data)

    def test_legacy_invalid_escape_is_diagnostic_not_silently_rewritten(self):
        for escape in ('\\;', '\\ ', '\\,'):
            with self.subTest(escape=escape), self.assertRaises(OutputFormatError) as caught:
                parse_output('{"css":"width:0;'+escape+'"}')
            self.assertEqual(caught.exception.details['message'],'Invalid \\escape')
            self.assertIn('position',caught.exception.details)

    def test_incomplete_duplicate_and_extra_sections_are_rejected(self):
        valid=sections(fixtures.output())
        for bad in (valid[:-10], 'explanation\n'+valid, valid+'\nextra',
                    valid.replace('<<<GAME_CSS>>>','<<<GAME_CSS>>>\n<<<GAME_CSS>>>'),
                    valid.replace('<<<GAME_JAVASCRIPT>>>','<<<WRONG>>>')):
            with self.subTest(bad=bad[-40:]),self.assertRaises(OutputFormatError):parse_output(bad)

    def test_design_is_still_strict_json(self):
        text=sections(fixtures.output())
        malformed=text.replace('"context":', '"context"=',1)
        with self.assertRaises(OutputFormatError) as caught:parse_output(malformed)
        self.assertEqual(caught.exception.details['field'],'design')


class GenerationTransportTests(unittest.TestCase):
    setUp=fixtures.GameWorkbenchTests.setUp

    def call(self,text,finish='stop',payload=None):
        self.calls=0
        def opener(request,timeout):
            self.calls+=1
            return io.BytesIO(json.dumps({'choices':[{'finish_reason':finish,'message':{'content':text}}],
                                         'usage':{'prompt_tokens':20,'completion_tokens':50}}).encode())
        return generation.generate(self.root,payload or self.payload,opener)

    def test_v5_generates_and_repairs_without_changing_artifacts(self):
        data=fixtures.output();data['css']=r'#game::after { content: "\2192"; }'
        raw=sections(data);result=self.call(raw)
        self.assertEqual(result['status'],'checked')
        self.assertEqual(result['prompt_file'],'prompts/game-generation/v5/generate.txt')
        self.assertEqual(result['parsed_output_format'],'raw_sections_v1')
        self.assertEqual(result['raw_text'],raw)
        self.assertEqual(result['output'],data)
        self.assertEqual(self.calls,1)
        saved=generation.read(self.root,result['record_id'])
        self.assertEqual((self.root/saved['artifacts']['css']).read_text(encoding='utf-8'),data['css'])
        repair=self.call(raw,payload={**self.payload,'generation_id':result['record_id'],'problem':'检查退出'})
        self.assertEqual(repair['prompt_file'],'prompts/game-generation/v5/repair.txt')
        self.assertEqual(repair['parent_record_id'],result['record_id'])
        self.assertEqual(repair['status'],'checked')
        catalog=game_generation_catalog(self.root)
        self.assertEqual(catalog[-1]['version'],'v5')
        self.assertTrue(all(f['active'] for f in catalog[-1]['files']))

    def test_compatible_output_still_validates_and_records_warning(self):
        data=fixtures.output()
        text=sections(data).replace('<<<END_GAME_DESIGN>>>\n<<<GAME_JAVASCRIPT>>>','<<<END_GAME_JAVASCRIPT>>>')
        record=self.call(text)
        self.assertEqual(record['status'],'checked')
        self.assertTrue(record['warnings'])
        self.assertFalse(record['format_compatibility']['code_rewritten'])
        self.assertEqual(record['raw_text'],text)
        self.assertEqual(record['output'],data)
        self.assertNotIn('design_source',record)
        bad=text.replace(data['javascript'],'function mount( {')
        self.assertEqual(self.call(bad)['status'],'failed','format compatibility never bypasses syntax checks')

    def test_truncation_format_and_syntax_failures_do_not_retry(self):
        for name,text,finish in [('truncated',sections(fixtures.output())[:-20],'length'),
                                 ('format','{"css":"width:0;\\;"}','stop'),
                                 ('syntax',sections({**fixtures.output(),'javascript':'function mount( {'}),'stop')]:
            with self.subTest(name=name):
                result=self.call(text,finish)
                self.assertEqual(result['status'],'failed')
                self.assertEqual(self.calls,1)
                self.assertEqual(result['raw_text'],text)
                self.assertEqual(result['usage']['prompt_tokens'],20)
                if name=='format':self.assertIn('parse_error',result)

    def test_core_design_validation_remains_required(self):
        data=fixtures.output();data['design']['goal']=''
        result=self.call(sections(data))
        self.assertEqual(result['status'],'failed')
        self.assertIn('design.goal',result['error'])

    def test_empty_design_keeps_code_and_labels_input_summary(self):
        data=fixtures.output()
        text=sections(data).replace(json.dumps(data['design'],ensure_ascii=False)+'\n','',1)
        self.assertIsNone(parse_output(text)[0]['design'])
        record=self.call(text)
        self.assertEqual(record['status'],'checked')
        self.assertEqual(record['design_source'],'input_summary')
        self.assertTrue(record['warnings'])
        self.assertEqual(record['output']['javascript'],data['javascript'])
        self.assertEqual(record['output']['design']['timeout_s'],60)
        self.assertFalse(record['validation']['playtested'])

    def test_local_recovery_preserves_original_and_does_not_charge(self):
        from unittest.mock import patch
        data=fixtures.output()
        text=sections(data).replace(json.dumps(data['design'],ensure_ascii=False)+'\n','',1)
        source=self.call(text);source['status']='failed'
        (self.root/source['record_file']).write_text(json.dumps(source),encoding='utf-8')
        before=(self.root/source['record_file']).read_bytes()
        with patch.object(generation,'urlopen') as cloud:
            result=generation.reparse(self.root,{**self.payload,'generation_id':source['record_id']})
            cloud.assert_not_called()
        self.assertFalse(result['model_call_made'])
        self.assertEqual(result['usage'],{})
        self.assertEqual(result['parent_record_id'],source['record_id'])
        self.assertEqual(result['output']['javascript'],data['javascript'])
        self.assertEqual((self.root/source['record_file']).read_bytes(),before)
        with self.assertRaises(ValueError):generation.reparse(self.root,{**self.payload,'project_id':'other','generation_id':source['record_id']})

    def test_configured_reasoning_depth_used_in_request(self):
        from video_analyzer import settings
        settings.Store(self.root).save({'revision':0,'values':{'MINIMAX_API_KEY':'fake-key-not-real','MINIMAX_REASONING_EFFORT':'high','MINIMAX_MAX_COMPLETION_TOKENS':'65536'}})
        def opener(request,timeout):
            body=json.loads(request.data);self.assertEqual(body['reasoning_effort'],'high')
            self.assertEqual(body['max_completion_tokens'],65536)
            return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':sections(fixtures.output())}}]}).encode())
        self.assertEqual(generation.generate(self.root,self.payload,opener)['status'],'checked')
        with self.assertRaises(ValueError):settings.Store(self.root).save({'revision':1,'values':{'MINIMAX_REASONING_EFFORT':'none'}})
        for invalid in ('0','-1','1.5','524289','not-a-number'):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):settings.Store(self.root).save({'revision':1,'values':{'MINIMAX_MAX_COMPLETION_TOKENS':invalid}})

    def test_repair_includes_original_error_diagnostics_and_unmodified_response(self):
        text='{"css":"width:0;\\;"}'
        failed=self.call(text)
        captured={}
        def opener(request,timeout):
            captured.update(json.loads(request.data))
            return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':sections(fixtures.output())}}]}).encode())
        result=generation.generate(self.root,{**self.payload,'generation_id':failed['record_id'],'problem':'修复格式错误'},opener)
        self.assertEqual(result['status'],'checked')
        user=json.loads(captured['messages'][1]['content'])
        self.assertEqual(user['problem'],'修复格式错误')
        self.assertEqual(user['previous']['raw_text'],text)
        self.assertEqual(user['previous']['error'],failed['error'])
        self.assertEqual(user['previous']['parse_error'],failed['parse_error'])
        self.assertIn('position',user['previous']['failure_details'])

    def test_historical_empty_design_gets_precise_read_only_diagnostic(self):
        from video_analyzer.gameplay.output import diagnose
        data=fixtures.output();text=sections(data).replace(json.dumps(data['design'],ensure_ascii=False)+'\n','',1)
        info=diagnose(text)
        self.assertEqual(info['missing_markers'],[])
        self.assertEqual(info['empty_sections'],['GAME_DESIGN'])
        self.assertTrue(any('空内容段' in message for message in info['issues']))
