"""Background task lifecycle uses fake runners and temporary projects only."""
import json
import threading
import time
import unittest
from unittest.mock import patch
import test_game_workbench as fixtures
from video_analyzer.gameplay import tasks, generation
from video_analyzer import settings


class TaskTests(unittest.TestCase):
    setUp = fixtures.GameWorkbenchTests.setUp

    def wait_done(self):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            rows=tasks.listing(self.root,self.project['project_id'])
            if rows and rows[0]['status'] not in tasks.RUNNING:
                return rows[0]
            threading.Event().wait(.01)
        self.fail('fixture task did not finish')

    def test_background_duplicate_reload_and_immutable_settings(self):
        started=threading.Event();release=threading.Event();seen=[]
        def runner(root,payload,progress,**kwargs):
            seen.append(settings.get('MINIMAX_MODEL'));progress('waiting_model','a'*32);started.set()
            release.wait(2)
            seen.append(settings.get('MINIMAX_MODEL'));progress('checking','a'*32)
            return {'status':'checked','record_id':'a'*32}
        try:
            job=tasks.start(self.root,self.payload,runner=runner)
            self.assertTrue(started.wait(1))
            self.assertIn(job['status'],tasks.RUNNING)
            self.assertEqual(tasks.listing(self.root,self.project['project_id'])[0]['task_id'],job['task_id'])
            with self.assertRaises(generation.Conflict):tasks.start(self.root,self.payload,runner=runner)
            with self.assertRaises(generation.Conflict):self.store.delete(self.project)
            settings.Store(self.root).save({'revision':0,'values':{'MINIMAX_MODEL':'changed-after-start'}})
        finally:
            release.set()
        result=self.wait_done()
        self.assertEqual(result['status'],'completed')
        self.assertEqual(seen,['MiniMax-M3.1-Flash','MiniMax-M3.1-Flash'])
        self.assertNotIn('fake-key-not-real',json.dumps(result))

    def test_failure_and_restart_never_retry_model(self):
        def fail(*args,**kwargs):raise RuntimeError('do not expose fake-key-not-real')
        tasks.start(self.root,self.payload,runner=fail)
        result=self.wait_done()
        self.assertEqual(result['status'],'failed')
        self.assertNotIn('fake-key-not-real',json.dumps(result))
        path=tasks.folder(self.root,self.project['project_id'])/(result['task_id']+'.json')
        data=json.loads(path.read_text(encoding='utf-8'));data.update(owner='old-server',status='waiting_model')
        tasks.write(path,data)
        with patch.object(generation,'generate') as generate:
            rows=tasks.listing(self.root,self.project['project_id']);tasks.listing(self.root,self.project['project_id'])
            self.assertEqual(rows[0]['status'],'interrupted');generate.assert_not_called()

    def test_invalid_inputs_never_start_worker(self):
        with patch.object(tasks.threading,'Thread') as thread:
            with self.assertRaises(ValueError):tasks.start(self.root,{**self.payload,'point_id':'unknown'})
            with self.assertRaises(generation.Conflict):tasks.start(self.root,{**self.payload,'revision':99})
            thread.assert_not_called()

    def test_generation_record_links_to_completed_task(self):
        fixture=fixtures.GameWorkbenchTests()
        def runner(root,payload,**kwargs):
            return generation.generate(root,payload,opener=fixture.opener,**kwargs)
        tasks.start(self.root,self.payload,runner=runner)
        result=self.wait_done()
        self.assertEqual(result['status'],'completed')
        record=generation.read(self.root,result['generation_id'])
        self.assertEqual(record['status'],'checked')
        self.assertFalse(record['validation']['playtested'])
