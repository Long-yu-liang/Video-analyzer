"""Game workbench tests are mocked: no inference or user project mutations."""
import copy
import io
import zipfile
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import app
from video_analyzer.gameplay import generation as games
from video_analyzer.storage import games as game_store
from video_analyzer.gameplay import library
from video_analyzer.projects import game_management as management
from test_projects import state, point


def output():
    return {'design': {**{k: '新增小游戏设计说明' for k in games.DESIGN_FIELDS}, 'timeout_s': 8},
            'javascript': "function mount(c,a){const b=document.createElement('button');b.textContent='点击';c.append(b);b.onclick=()=>a.finish({result:'success',message:'完成'});return {destroy(){b.remove();}};}",
            'css': '#game button{font-size:24px}'}


class GameWorkbenchTests(unittest.TestCase):
    def test_game_workbench_layout_reserves_desktop_column_and_bounds_sidebar(self):
        css = (app.DEMO / 'web/assets/css/workbench.css').read_text(encoding='utf-8')
        self.assertIn('body[data-workbench-view=games] #gameWorkbenchPanel', css)
        self.assertIn('#wbDesign,#wbArrange,#legacyGameplayPanel,#wbRecommendationActions,#gameWorkbenchPanel{display:none}', css)
        self.assertIn('max-height:calc(100dvh - 32px);overflow:auto', css)
        responsive = (app.DEMO / 'web/assets/css/game-workbench.css').read_text(encoding='utf-8')
        self.assertIn('@media(max-width:950px)', responsive)
        self.assertIn('body[data-workbench-view=games] #gameWorkbenchPanel{grid-column:2}', responsive)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(app.ROOT / 'prompts/game-generation', self.root / 'prompts/game-generation')
        data = state();data['points'] = [point(), {**point(), 'id': 'other', 'opportunity': 'DO_NOT_SEND_OTHER'}]
        self.store = games.Store(self.root / 'projects')
        self.project = self.store.save({'state': data})
        self.payload = {'project_id': self.project['project_id'], 'revision': 1, 'point_id': 'manual_test'}
        self.env = patch.dict(os.environ, {'MINIMAX_API_KEY': 'fake-key-not-real', 'MINIMAX_MODEL': 'MiniMax-M3.1-Flash'})
        self.env.start();self.addCleanup(self.env.stop)

    def opener(self, request, timeout):
        self.body = json.loads(request.data)
        return io.BytesIO(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(output())}}],
                                    'usage': {'prompt_tokens': 20, 'completion_tokens': 50}}).encode())

    def test_single_point_and_separate_record(self):
        result = games.generate(self.root, self.payload, self.opener)
        self.assertEqual(result['status'], 'checked')
        self.assertNotIn('DO_NOT_SEND_OTHER', json.dumps(self.body))
        self.assertNotIn('image_url', json.dumps(self.body))
        self.assertNotIn('fake-key-not-real', json.dumps(result))
        self.assertEqual(games.read(self.root, result['record_id'])['input_hash'], result['input_hash'])
        self.assertEqual(self.store.read(self.project['project_id']), self.project)
        self.assertFalse(result['validation']['playtested'])

    def test_v2_generation_and_repair_prompts_are_recorded(self):
        shutil.rmtree(self.root / 'prompts/game-generation/v5')
        shutil.rmtree(self.root / 'prompts/game-generation/v4')
        shutil.rmtree(self.root / 'prompts/game-generation/v3')
        first = games.generate(self.root, self.payload, self.opener)
        self.assertEqual(first['prompt_file'], 'prompts/game-generation/v2/generate.txt')
        self.assertIn('输出前的简要自检', self.body['messages'][0]['content'])
        self.assertFalse(first['validation']['playtested'])
        repaired = games.generate(self.root, {**self.payload, 'generation_id': first['record_id'],
                                             'problem': '检查元素绑定'}, self.opener)
        self.assertEqual(repaired['prompt_file'], 'prompts/game-generation/v2/repair.txt')
        self.assertIn('不以可选链', repaired['prompt'])

    def test_v3_prompts_selected_and_visible_without_few_shot(self):
        shutil.rmtree(self.root / 'prompts/game-generation/v5')
        shutil.rmtree(self.root / 'prompts/game-generation/v4')
        from video_analyzer.catalog.prompts import game_generation_catalog, load_stage_prompt
        first = games.generate(self.root, self.payload, self.opener)
        self.assertEqual(first['prompt_file'], 'prompts/game-generation/v3/generate.txt')
        self.assertNotIn('示例一', first['prompt'])
        repaired = games.generate(self.root, {**self.payload, 'generation_id': first['record_id'],
                                             'problem': '检查元素绑定'}, self.opener)
        self.assertEqual(repaired['prompt_file'], 'prompts/game-generation/v3/repair.txt')
        catalog = game_generation_catalog(self.root)
        self.assertEqual([item['version'] for item in catalog], ['v1', 'v2', 'v3'])
        self.assertTrue(all(file['active'] for file in catalog[-1]['files']))
        self.assertTrue(all(not file['active'] for item in catalog[:-1] for file in item['files']))
        for stage in ('events', 'opportunities', 'gameplay'):
            path, text = load_stage_prompt(app.ROOT, stage)
            self.assertEqual(path.parent.name, 'v23' if stage=='gameplay' else 'v22')
            self.assertNotIn('假设示例', text)
        text = load_stage_prompt(app.ROOT, 'gameplay')[1]
        self.assertIn('七种玩法地位平等', text)
        self.assertNotIn('通常作为备选', text)

    def test_readable_storage_artifacts_and_name_collisions(self):
        first = games.generate(self.root, self.payload, self.opener)
        second = games.generate(self.root, self.payload, self.opener)
        self.assertIn('/MiniMax-M3.1-Flash/games/sample/manual_test/', first['record_file'])
        self.assertIn('_第四步生成.json', first['record_file'])
        self.assertIn('_第四步生成_2.json', second['record_file'])
        for item in (first, second):
            saved = games.read(self.root, item['record_id'])
            self.assertEqual(saved['artifacts'], item['artifacts'])
            self.assertEqual((self.root / saved['artifacts']['javascript']).read_text(encoding='utf-8'), output()['javascript'])
        # A damaged unrelated JSON never hides valid history.
        (self.root / first['record_file']).with_name('broken.json').write_text('{', encoding='utf-8')
        self.assertEqual(len(games.listing(self.root, self.project['project_id'], 'manual_test')), 2)

    def test_legacy_relocation_preserves_bytes_and_id(self):
        record = {'record_id': 'c'*32, 'stage': 'game_generation', 'status': 'failed', 'model': 'MiniMax-M3.1-Flash',
                  'project_id': self.project['project_id'], 'point_id': 'ai_'+'b'*32+'_p2',
                  'input': {'video': {'name': 'S05_传球投篮.mp4'}, 'design': {'game_id': 'hold_release'}},
                  'created_at': '2026-10-23T03:03:28+00:00', 'output': output(), 'error': 'original failure'}
        legacy = self.root / 'results/day4';legacy.mkdir(parents=True)
        original = legacy / (record['record_id']+'.json')
        raw = json.dumps(record, ensure_ascii=False, indent=4).encode();original.write_bytes(raw)
        self.assertEqual(games.read(self.root, record['record_id'])['error'], 'original failure')
        target = game_store.migrate(self.root, record['record_id'])
        self.assertFalse(original.exists());self.assertEqual(target.read_bytes(), raw)
        self.assertIn('/games/S05_传球投篮/p2/', target.as_posix())
        self.assertEqual(games.read(self.root, record['record_id'])['status'], 'failed')
        self.assertEqual(game_store.migrate(self.root, record['record_id']), target)

    def test_missing_node_preflight_never_bills(self):
        with patch.dict(os.environ, {'NODE_BINARY': ''}), patch.object(games.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'NODE_BINARY'):
                games.generate(self.root, self.payload, lambda *a, **kw: self.fail('must not bill'))

    def test_project_library_restore_backup_and_stale(self):
        first = games.generate(self.root, self.payload, self.opener)
        second = games.generate(self.root, self.payload, self.opener)
        games.select(self.root, {'generation_id': first['record_id'], 'enabled': True})
        games.playtest(self.root, {'generation_id': first['record_id'], 'result':'success', 'manual_review':True,'note':'人工测试备注'})
        overview = library.inventory(self.root, self.project['project_id'])
        self.assertEqual(overview['saved_count'], 2);self.assertEqual(overview['ready_count'], 1)
        versions = overview['points'][0]['versions']
        chosen = next(v for v in versions if v['selected'])
        self.assertEqual(chosen['record_id'], first['record_id']);self.assertEqual(chosen['manual_review_count'], 1)
        # Fresh store/reader restores the same saved choice; no inference is required.
        self.assertEqual(games.selections(self.root, self.project['project_id'])[0]['record_id'], first['record_id'])
        with zipfile.ZipFile(io.BytesIO(library.bundle(self.root, self.project['project_id']))) as archive:
            self.assertIn('project.json', archive.namelist());self.assertIn('manifest.json', archive.namelist())
            self.assertEqual(archive.read('games/'+first['record_id']+'/game.js').decode(), output()['javascript'])
            self.assertEqual(len([p for p in archive.namelist() if p.startswith('playtests/')]), 1)
            self.assertFalse(any(p.endswith('.mp4') for p in archive.namelist()))
            self.assertNotIn('fake-key-not-real', b''.join(archive.read(p) for p in archive.namelist()).decode())
        games.select(self.root, {'generation_id': second['record_id'], 'enabled': True})
        with self.assertRaises(games.Conflict):games.select(self.root, {'generation_id':first['record_id'],'enabled':False})
        changed = copy.deepcopy(self.project);changed['state']['points'][0]['design']['title']='新的玩法';self.store.save(changed)
        updated = library.inventory(self.root, self.project['project_id'])
        self.assertEqual(updated['ready_count'],0);self.assertTrue(all(v['stale'] for v in updated['points'][0]['versions']))
        self.assertEqual(len(list((self.root/'projects'/self.project['project_id']/'games-revisions').glob('*.json'))), 2)

    def test_library_missing_selection_and_foreign_bundle(self):
        item = games.generate(self.root, self.payload, self.opener)
        path=self.root/'projects'/self.project['project_id']/'games.json'
        path.write_text(json.dumps({'manual_test':'a'*32}),encoding='utf-8')
        overview=library.inventory(self.root,self.project['project_id'])
        self.assertEqual(len(overview['warnings']),1);self.assertEqual(overview['saved_count'],1)
        self.assertEqual(games.selections(self.root,self.project['project_id']),[])
        saved=management.save_arrangement(self.root,{**self.payload,'entries':[{'point_id':'manual_test','generation_id':'a'*32,'enabled':False},{'point_id':'other','generation_id':None,'enabled':False}]})
        self.assertFalse(saved['project']['state']['points'][0]['enabled'])
        other=self.store.save({'state':state()})
        with self.assertRaises(ValueError):library.bundle(self.root,other['project_id'],item['record_id'])

    def test_playtest_subfolder_and_legacy_migration_preserves_bytes(self):
        item=games.generate(self.root,self.payload,self.opener)
        test=games.playtest(self.root,{'generation_id':item['record_id'],'result':'exit'})
        self.assertIn('/试玩/',test['record_file'])
        target=self.root/test['record_file'];raw=target.read_bytes();old=target.parent.parent/target.name
        target.rename(old)
        report=game_store.migrate_playtests(self.root)
        self.assertEqual(len(report['moved']),1);self.assertFalse(old.exists())
        relocated=self.root/games.read(self.root,test['record_id'])['record_file']
        self.assertEqual(relocated.read_bytes(),raw)
        self.assertEqual(game_store.migrate_playtests(self.root)['moved'],[])
        with zipfile.ZipFile(io.BytesIO(library.bundle(self.root,self.project['project_id']))) as archive:
            self.assertTrue(any(p.startswith('playtests/') for p in archive.namelist()))

    def test_delete_game_restore_and_keep_repaired_version(self):
        first=games.generate(self.root,self.payload,self.opener)
        second=games.generate(self.root,{**self.payload,'generation_id':first['record_id'],'problem':'修复'},self.opener)
        games.select(self.root,{'generation_id':first['record_id'],'enabled':True})
        test=games.playtest(self.root,{'generation_id':first['record_id'],'result':'success'})
        raw=(self.root/first['record_file']).read_bytes()
        removed=management.remove(self.root,{**self.payload,'kind':'game','generation_id':first['record_id']})
        with self.assertRaises(ValueError):games.read(self.root,first['record_id'])
        self.assertFalse((self.root/first['artifacts']['javascript']).exists())
        with self.assertRaises(ValueError):games.read(self.root,test['record_id'])
        self.assertEqual(games.read(self.root,second['record_id'])['record_id'],second['record_id'])
        self.assertEqual(management.mapping(self.root,self.project['project_id']),{})
        self.assertEqual(len(management.listing(self.root,self.project['project_id'])),1)
        restored=management.restore(self.root,{'project_id':self.project['project_id'],'revision':removed['project']['revision'],'operation_id':removed['operation_id']})
        self.assertEqual((self.root/first['record_file']).read_bytes(),raw)
        self.assertEqual(management.mapping(self.root,self.project['project_id']),{})
        self.assertFalse(restored['project']['state']['points'][0]['enabled'])

    def test_delete_point_cascades_tombstone_and_recovery(self):
        first=games.generate(self.root,self.payload,self.opener)
        test=games.playtest(self.root,{'generation_id':first['record_id'],'result':'exit'})
        changed=copy.deepcopy(self.project)
        changed['state']['points'][1].update(merged_into='manual_test',review='discarded',enabled=False,confirmed=False)
        changed=self.store.save(changed)
        removed=management.remove(self.root,{**self.payload,'revision':changed['revision'],'kind':'point'})
        self.assertEqual(removed['generation_count'],1);self.assertEqual(removed['playtest_count'],1)
        self.assertIn('manual_test',removed['project']['state']['deleted_point_ids'])
        other=removed['project']['state']['points'][0]
        self.assertNotIn('merged_into',other);self.assertFalse(other['enabled'])
        restored=management.restore(self.root,{'project_id':self.project['project_id'],'revision':removed['project']['revision'],'operation_id':removed['operation_id']})
        recovered=next(p for p in restored['project']['state']['points'] if p['id']=='manual_test')
        self.assertFalse(recovered['enabled']);self.assertNotIn('manual_test',restored['project']['state']['deleted_point_ids'])

    def test_delete_failure_rolls_back_and_restore_conflict(self):
        item=games.generate(self.root,self.payload,self.opener)
        original_rename=Path.rename;calls=[]
        def fail_second(path,target):
            calls.append(path)
            if len(calls)==2:raise OSError('simulated move error')
            return original_rename(path,target)
        with patch.object(Path,'rename',fail_second):
            with self.assertRaises(OSError):management.remove(self.root,{**self.payload,'kind':'game','generation_id':item['record_id']})
        self.assertTrue((self.root/item['record_file']).exists())
        self.assertEqual(self.store.read(self.project['project_id']),self.project)
        removed=management.remove(self.root,{**self.payload,'kind':'game','generation_id':item['record_id']})
        target=self.root/item['record_file'];target.write_text('do not overwrite',encoding='utf-8')
        with self.assertRaises(games.Conflict):management.restore(self.root,{'project_id':self.project['project_id'],'revision':removed['project']['revision'],'operation_id':removed['operation_id']})
        self.assertEqual(target.read_text(encoding='utf-8'),'do not overwrite')
        self.assertEqual(len(management.listing(self.root,self.project['project_id'])),1)

    def test_busy_leases_and_revision_prevent_deletion(self):
        item=games.generate(self.root,self.payload,self.opener)
        payload={**self.payload,'kind':'game','generation_id':item['record_id']}
        with self.assertRaises(games.Conflict):management.remove(self.root,{**payload,'revision':999})
        games.ACTIVE.add((self.project['project_id'],'manual_test'))
        try:
            with self.assertRaises(games.Conflict):management.remove(self.root,payload)
        finally:games.ACTIVE.discard((self.project['project_id'],'manual_test'))
        token='test_session_123456789'
        management.lease(self.root,{'session_id':token,'generation_id':item['record_id'],'active':True})
        try:
            with self.assertRaises(games.Conflict):management.remove(self.root,payload)
        finally:management.lease(self.root,{'session_id':token,'active':False})

    def test_arrangement_persists_disabled_choice_and_strict_sequence(self):
        first=games.generate(self.root,self.payload,self.opener)
        second=games.generate(self.root,{**self.payload,'point_id':'other'},self.opener)
        entries=[{'point_id':'manual_test','generation_id':first['record_id'],'enabled':True},{'point_id':'other','generation_id':second['record_id'],'enabled':False}]
        saved=management.save_arrangement(self.root,{**self.payload,'entries':entries})
        self.assertEqual(len(management.sequence(self.root,self.project['project_id'])),1)
        self.assertEqual(saved['selections']['other'],second['record_id'])
        entries[1]['enabled']=True
        duplicate=management.save_arrangement(self.root,{**self.payload,'revision':saved['project']['revision'],'entries':entries})
        with self.assertRaisesRegex(ValueError,'时间相同'):management.sequence(self.root,self.project['project_id'])
        modified=copy.deepcopy(duplicate['project']);modified['state']['points'][1]['time_s']=5;self.store.save(modified)
        with self.assertRaisesRegex(ValueError,'过期'):management.sequence(self.root,self.project['project_id'])

    def test_atomic_arrangement_failure_preserves_previous_head(self):
        item=games.generate(self.root,self.payload,self.opener);original_atomic=management.atomic;failed=[]
        def fail_index(path,data):
            if Path(path).name=='games.json' and not failed:
                failed.append(True);raise OSError('disk full')
            return original_atomic(path,data)
        with patch.object(management,'atomic',fail_index):
            with self.assertRaises(OSError):management.save_arrangement(self.root,{**self.payload,'entries':[{'point_id':'manual_test','generation_id':item['record_id'],'enabled':True}]})
        self.assertEqual(self.store.read(self.project['project_id']),self.project)

    def test_conflict_empty_stale_missing_key_never_calls(self):
        def no_call(*a, **kw):raise AssertionError('must not call')
        with self.assertRaises(games.Conflict):games.generate(self.root, {**self.payload, 'revision': 0}, no_call)
        with patch.dict(os.environ, {'MINIMAX_API_KEY': ''}):
            with self.assertRaisesRegex(ValueError, 'MINIMAX_API_KEY'):games.generate(self.root, self.payload, no_call)
        for updates in ({'review': 'discarded', 'enabled': False, 'confirmed': False}, {'design': None, 'confirmed': False}):
            changed = copy.deepcopy(self.project);changed['state']['points'][0].update(updates);changed['state']['points'][0]['confirmed'] = False
            saved = self.store.save(changed)
            with self.assertRaises(ValueError):games.generate(self.root, {**self.payload, 'revision': saved['revision']}, no_call)
            self.project = saved

    def test_modified_point_generates_without_manual_review_but_old_game_expires(self):
        first=games.generate(self.root,self.payload,self.opener)
        changed=copy.deepcopy(self.project)
        changed['state']['points'][0].update(time_s=3,stale=True,confirmed=False)
        saved=self.store.save(changed)
        with self.assertRaises(ValueError):games.select(self.root,{'generation_id':first['record_id'],'enabled':True})
        result=games.generate(self.root,{**self.payload,'revision':saved['revision']},self.opener)
        self.assertEqual(result['status'],'checked')
        self.assertEqual(result['input']['time_s'],3)
        games.select(self.root,{'generation_id':result['record_id'],'enabled':True})
        self.assertEqual(games.selections(self.root,saved['project_id'])[0]['record_id'],result['record_id'])

    def test_failed_json_truncation_syntax_and_timeout_kept(self):
        for raw, finish in (('not JSON', 'stop'), ('{}', 'length'), (json.dumps({**output(), 'javascript': 'function mount( {'}), 'stop')):
            def opener(*args, **kw):
                return io.BytesIO(json.dumps({'choices': [{'finish_reason': finish, 'message': {'content': raw}}]}).encode())
            result = games.generate(self.root, self.payload, opener)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(games.read(self.root, result['record_id'])['raw_text'], raw)
        def timeout(*args, **kw):raise TimeoutError('timeout')
        result = games.generate(self.root, self.payload, timeout)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['model_call_made'])
        self.assertEqual(len(games.listing(self.root, self.project['project_id'], 'manual_test')), 4)

    def test_inflight_duplicate_rejected_and_input_change_marks_stale(self):
        def opener(*args, **kw):
            with self.assertRaises(games.Conflict):games.generate(self.root, self.payload, self.opener)
            changed = copy.deepcopy(self.project)
            changed['state']['points'][0]['design']['title'] = '修改后的玩法'
            self.store.save(changed)
            return self.opener(*args, **kw)
        result = games.generate(self.root, self.payload, opener)
        self.assertTrue(result['stale'])
        with self.assertRaisesRegex(ValueError, '旧游戏'):games.select(self.root, {'generation_id': result['record_id'], 'enabled': True})

    def test_selection_and_repair_and_playtest(self):
        first = games.generate(self.root, self.payload, self.opener)
        games.select(self.root, {'generation_id': first['record_id'], 'enabled': True})
        self.assertEqual(len(games.selections(self.root, self.project['project_id'])), 1)
        repair = games.generate(self.root, {**self.payload, 'generation_id': first['record_id'], 'problem': '增大按钮'}, self.opener)
        self.assertEqual(repair['parent_record_id'], first['record_id'])
        games.select(self.root, {'generation_id': repair['record_id'], 'enabled': True})
        self.assertEqual([r['record_id'] for r in games.selections(self.root, self.project['project_id'])], [repair['record_id']])
        saved = games.playtest(self.root, {'generation_id': repair['record_id'], 'result': 'success', 'note': '模拟协议结果，不是人工试玩'})
        self.assertFalse(saved['manual_review'])
        self.assertFalse(saved['model_call_made'])
        changed = copy.deepcopy(self.project);changed['state']['points'][0]['time_s'] = 3
        self.store.save(changed)
        self.assertEqual(games.selections(self.root, self.project['project_id']), [])
        with self.assertRaises(ValueError):games.generate(self.root, {**self.payload, 'revision': 2, 'generation_id': first['record_id'], 'problem': '修改'}, self.opener)
        with self.assertRaises(ValueError):games.read(self.root, '../escape')

    def test_http_auth_and_routes(self):
        with patch.object(app, 'ROOT', self.root), patch.object(games, 'urlopen', self.opener):
            server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True);worker.start()
            base = f'http://127.0.0.1:{server.server_port}'
            def req(path, data=None, token=True):
                headers = {'X-Local-Token': app.TOKEN} if token else {}
                with urlopen(Request(base+path, data=json.dumps(data).encode() if data else None, headers=headers), timeout=10) as response:return json.load(response)
            try:
                with patch.object(app, 'read_sample', return_value=b'0123456789'):
                    with urlopen(Request(base+'/sample?name=fixture.mp4', headers={'Range': 'bytes=2-5'})) as response:
                        self.assertEqual(response.status, 206)
                        self.assertEqual(response.headers['Content-Range'], 'bytes 2-5/10')
                        self.assertEqual(response.read(), b'2345')
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(Request(base+'/sample?name=fixture.mp4', headers={'Range': 'bytes=99-'}))
                    self.assertEqual(caught.exception.code, 416)
                with self.assertRaises(HTTPError):req('/game-generation?id='+'a'*32, token=False)
                with self.assertRaises(HTTPError):req('/game-generation', self.payload, token=False)
                result = req('/game-generation', self.payload)
                self.assertEqual(result['status'], 'checked')
                self.assertEqual(req('/game-generation?id='+result['record_id'])['record_id'], result['record_id'])
                req('/game-selection', {'generation_id': result['record_id'], 'enabled': True})
                self.assertEqual(len(req('/game-selection?project_id='+self.project['project_id'])), 1)
                self.assertEqual(req('/project-games?project_id='+self.project['project_id'])['ready_count'],1)
                with urlopen(Request(base+'/game-bundle?project_id='+self.project['project_id'],headers={'X-Local-Token':app.TOKEN})) as response:
                    self.assertEqual(response.headers['Content-Type'],'application/zip')
                    self.assertTrue(zipfile.is_zipfile(io.BytesIO(response.read())))
                with self.assertRaises(HTTPError):req('/project-games?project_id='+self.project['project_id'],token=False)
                arranged=req('/arrangement',{**self.payload,'entries':[{'point_id':'manual_test','generation_id':result['record_id'],'enabled':True},{'point_id':'other','generation_id':None,'enabled':False}]})
                self.assertEqual(len(req('/game-sequence?project_id='+self.project['project_id'])),1)
                removed=req('/game-delete',{**self.payload,'revision':arranged['project']['revision'],'kind':'game','generation_id':result['record_id']})
                self.assertEqual(len(req('/game-trash?project_id='+self.project['project_id'])),1)
                req('/game-restore',{'project_id':self.project['project_id'],'revision':removed['project']['revision'],'operation_id':removed['operation_id']})
                self.assertEqual(req('/game-trash?project_id='+self.project['project_id']),[])
                with self.assertRaises(HTTPError):req('/game-repair', self.payload)
            finally:
                server.shutdown();server.server_close();worker.join()

    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_frontend_protocol(self):
        result = subprocess.run(['node', str(app.DEMO / 'tests/frontend/game_workbench.js')], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)


if __name__ == '__main__':unittest.main()
