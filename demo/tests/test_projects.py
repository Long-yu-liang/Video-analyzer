"""Local project contracts: no inference calls or production result writes."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
import shutil
import subprocess
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import app
import projects


def state():
    return {'name': '测试项目', 'video': {'name':'sample.mp4', 'sha256':'a'*64, 'sample':'', 'duration_s':12},
            'session': {}, 'record_ids': [], 'seen_record_ids': [], 'points': [], 'density_s':3}


def point():
    return {'id':'manual_test', 'origin':'manual', 'time_s':2, 'opportunity':'拿起物体', 'reason':'物体和手都可见',
            'review':'kept', 'enabled':True, 'versions':[], 'selected':None,
            'design':{'game_id':'drag', 'title':'对准目标', **{k:'人工填写规则' for k in projects.DESIGN_FIELDS}},
            'brief':{k:'人工填写设计，不是视频事实' for k in projects.BRIEF_FIELDS}, 'confirmed':True, 'stale':False}


class ProjectChecks(unittest.TestCase):
    def test_independent_manual_option_descriptions_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            store=projects.Store(folder)
            data=state();p=point();data['points']=[p]
            p['versions']=[{'id':'manual_a','origin':'manual','record_id':None,'option_id':'manual_a',
                            'label':'自行设计','design':copy.deepcopy(p['design']),'time_s':2,
                            'brief':{'screen':'A画面'},'edited':True,'confirmed':False}]
            p['selected']='manual_a';p['brief']={'screen':'A画面'};p['gameplay_committed']=True
            saved=store.save({'state':data})
            restored=store.read(saved['project_id'])
            self.assertEqual(restored['state']['points'][0]['versions'][0]['brief']['screen'],'A画面')
            self.assertIs(restored['state']['points'][0]['gameplay_committed'],True)
            bad=copy.deepcopy(restored);bad['state']['points'][0]['versions'][0]['origin']='ai'
            with self.assertRaises(ValueError):store.save(bad)

    @unittest.skipUnless(shutil.which('node'), 'Node.js not installed')
    def test_delete_ui(self):
        result = subprocess.run(['node', str(app.DEMO / 'tests/frontend/project_delete.js')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node.js not installed')
    def test_workbench_model(self):
        result=subprocess.run(['node',str(app.DEMO/'tests/frontend/workbench_model.js')],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_save_restore_revision_snapshots_and_no_media(self):
        with tempfile.TemporaryDirectory() as folder:
            store = projects.Store(folder)
            first = store.save({'state':state()})
            first['state']['points'].append(point())
            second = store.save(first)
            self.assertEqual(second['revision'],2)
            self.assertEqual(store.read(first['project_id']),second)
            self.assertEqual(len(list(Path(folder).rglob('revision-*.json'))),2)
            self.assertEqual(store.listing()[0]['name'],'测试项目')
            self.assertEqual(len(list(Path(folder).rglob('*.mp4'))),0)
            with self.assertRaises(projects.Conflict):
                store.save(first)
            self.assertEqual(store.read(first['project_id']),second)

    def test_path_video_time_unknown_fields_and_point_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            store=projects.Store(folder)
            for value in ('../results', '', 'z'*32, 'a'*31):
                with self.assertRaises(ValueError):store.read(value)
            original=store.save({'state':state()})
            update=copy.deepcopy(original);update['state']['video']['sha256']='b'*64
            with self.assertRaisesRegex(ValueError,'不能替换'):store.save(update)
        for value in (float('nan'),float('inf'),-1,13,True):
            data=state();p=point();p['time_s']=value;data['points']=[p]
            with self.assertRaises(ValueError):projects.validate(data)
        data=state();data['raw_response']={'base64':'media'}
        with self.assertRaises(ValueError):projects.validate(data)
        data=state();data['points']=[point(),point()]
        with self.assertRaises(ValueError):projects.validate(data)

    def test_delete_is_recoverable_revision_checked_and_preserves_results(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = projects.Store(root / 'projects')
            item = store.save({'state': state()})
            other = store.save({'state': state()})
            original = root / 'original.mp4'
            result = root / 'results.json'
            original.write_bytes(b'original video')
            result.write_bytes(b'original inference')
            with self.assertRaises(projects.Conflict):
                store.delete({'project_id': item['project_id'], 'revision': 0})
            for bad in ('../results', '', 'z' * 32):
                with self.assertRaises(ValueError):
                    store.delete({'project_id': bad, 'revision': 1})
            with patch('projects.Path.rename', side_effect=OSError('disk error')):
                with self.assertRaises(OSError):store.delete(item)
            self.assertEqual(store.read(item['project_id']), item)
            self.assertTrue(store.delete(item)['recoverable'])
            self.assertEqual([p['project_id'] for p in store.listing()], [other['project_id']])
            with self.assertRaisesRegex(ValueError, '不存在'):store.read(item['project_id'])
            with self.assertRaisesRegex(ValueError, '不存在'):store.save(item)
            with self.assertRaises(ValueError):store.delete(item)
            archived = list((root / 'projects' / '.trash').glob('*'))
            self.assertEqual(len(archived), 1)
            self.assertEqual(json.loads((archived[0] / 'project.json').read_text(encoding='utf-8')), item)
            self.assertTrue(list(archived[0].glob('revision-*.json')))
            self.assertEqual(original.read_bytes(), b'original video')
            self.assertEqual(result.read_bytes(), b'original inference')
    def test_confirmation_export_and_conflict(self):
        data=state();data['points']=[point()]
        project={'project_id':'a'*32,'revision':1,'state':data}
        exported=projects.export_project(project,True)
        self.assertEqual(exported['purpose'],'confirmed_designs_not_tested_code')
        self.assertFalse(exported['playable'])
        data['points'][0]['confirmed']=False
        with self.assertRaises(ValueError):projects.export_project(project,True)
        self.assertEqual(len(projects.export_project(project)['points']),1)
        data['points'][0]['confirmed']=True;data['points'][0]['brief']['rules']=''
        self.assertEqual(len(projects.export_project(project,True)['points']),1)
        data['points'][0]['brief']={}
        self.assertEqual(len(projects.export_project(project,True)['points']),1)
        data['points'][0]['design']['player_action']=''
        with self.assertRaises(ValueError):projects.export_project(project,True)
        data['points']=[point(),{**point(),'id':'manual_other'}]
        with self.assertRaisesRegex(ValueError,'同一时间'):projects.export_project(project,True)

    def test_stale_foreign_selection_and_discard_confirmation_rejected(self):
        for updates in ({'stale':True},{'review':'discarded'},{'selected':'foreign-option'}):
            data=state();data['points']=[{**point(),**updates}]
            with self.assertRaises(ValueError):projects.validate(data)

    def test_failed_atomic_replace_preserves_previous(self):
        with tempfile.TemporaryDirectory() as folder:
            store=projects.Store(folder);original=store.save({'state':state()})
            with patch('projects.os.replace',side_effect=OSError('disk error')):
                with self.assertRaises(OSError):store.save(original)
            self.assertEqual(store.read(original['project_id']),original)
            self.assertFalse(list(Path(folder).rglob('*.tmp')))

    def test_project_state_rejects_tokens_and_cycle_merges(self):
        data=state();data['session']={'settings':{'token':'do-not-store-credentials'}}
        with self.assertRaises(ValueError):projects.validate(data)
        data=state();a=point();b={**point(),'id':'manual_other'}
        a.update(review='discarded',enabled=False,confirmed=False,merged_into=b['id'])
        b.update(review='discarded',enabled=False,confirmed=False,merged_into=a['id'])
        data['points']=[a,b]
        with self.assertRaisesRegex(ValueError,'循环'):projects.validate(data)

    def test_http_routes_are_local_only_and_do_not_call_model(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(app,'ROOT',Path(folder)), \
                patch.object(app,'call_v13_model',side_effect=AssertionError('no inference')):
            server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            base=f'http://127.0.0.1:{server.server_port}'
            def request(path,payload=None,token=True):
                headers={'X-Local-Token':app.TOKEN} if token else {}
                raw=json.dumps(payload).encode() if payload else None
                with urlopen(Request(base+path,data=raw,headers=headers),timeout=3) as response:return json.load(response)
            try:
                with self.assertRaises(HTTPError) as caught:request('/projects',token=False)
                self.assertEqual(caught.exception.code,403)
                item=request('/projects',{'state':state()})
                self.assertEqual(request('/project?id='+item['project_id']),item)
                self.assertEqual(len(request('/projects')),1)
                source=Path(folder)/'original.json'
                original={'record_id':'c'*32,'stage':'gameplay','status':'success','video_sha256':'a'*64,
                          'analysis':{'recommendations':[]},'raw_response':{'private':'raw payload'},
                          'usage':{'prompt_tokens':123,'completion_tokens':45,'total_tokens':168},
                          'segments':[{'status':'success','usage':{'total_tokens':168},'raw_response':{'private':'not for browser'}}],
                          'observations':[{'point_id':'p1','frame_time_s':2,'preview_jpeg_base64':'old-inline-image'}]}
                source.write_text(json.dumps(original),encoding='utf-8')
                with patch.object(app,'saved_record_paths',return_value=[source]):
                    restored=request('/project-record?record_id='+'c'*32)
                self.assertTrue(restored['restored_summary'])
                self.assertNotIn('raw_response',restored)
                self.assertEqual(restored['usage'],original['usage'])
                self.assertEqual(restored['segments'],[{'status':'success','usage':{'total_tokens':168}}])
                self.assertNotIn('preview_jpeg_base64',restored['observations'][0])
                self.assertEqual(json.loads(source.read_text(encoding='utf-8')),original)
                item['state']['points']=[point()];saved=request('/projects',item)
                self.assertEqual(len(request('/project-export?id='+item['project_id']+'&ready=1')['points']),1)
                with self.assertRaises(HTTPError) as caught:request('/projects',item)
                self.assertEqual(caught.exception.code,409)
                self.assertEqual(saved['revision'],2)
                with self.assertRaises(HTTPError) as caught:request('/project-delete', saved, token=False)
                self.assertEqual(caught.exception.code, 403)
                with self.assertRaises(HTTPError) as caught:request('/project-delete', item)
                self.assertEqual(caught.exception.code, 409)
                self.assertTrue(request('/project-delete', saved)['deleted'])
                self.assertEqual(request('/projects'), [])
                self.assertTrue(source.exists())
            finally:
                server.shutdown();server.server_close();worker.join()


if __name__=='__main__':
    unittest.main()
