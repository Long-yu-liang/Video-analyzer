"""Deterministic workbench browser fixture. Uses a temp project store, never AI/results writes."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import parse_qs, urlparse
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import app
import projects

SAMPLE='S05_传球投篮.mp4'
SHA=hashlib.sha256(app.read_sample(SAMPLE)).hexdigest()
META={'duration_s':16.68,'width':1920,'height':1080}
COMMON={'status':'success','video_name':SAMPLE,'video_sha256':SHA,'metadata':META,'model':'qwen3-vl-plus',
        'total_s':0,'record_file':'FIXTURE-only.json'}
EVENTS={**COMMON,'record_id':'a'*32,'stage':'events','prompt_file':'prompts/v14/events.txt','mode':'direct','fps':2,
        'analysis':{'summary':'本地界面夹具，不是真实模型结果','limitations':[], 'events':[
            {'observation_id':'e1','start_s':1,'end_s':4,'observation_s':3,'description':'人物持球',
             'evidence':'测试夹具的画面说明','uncertainty':'不作事实评估'}]}}
OPP={**COMMON,'record_id':'b'*32,'parent_record_id':'a'*32,'stage':'opportunities','prompt_file':'prompts/v15/opportunities.txt',
     'analysis':{'interaction_points':[
         {'point_id':'p1','event_id':'e1','time_s':3,'opportunity':'控制出手时机','reason':'测试夹具：人物持球'},
         {'point_id':'p2','event_id':'e1','time_s':6,'opportunity':'选择下一步行为','reason':'测试夹具：可参与行为'}],
         'warnings':[{'path':'interaction_points[0].observation_id','code':'unknown_observation',
                      'message':'interaction_points[0].observation_id 引用了未审核的 e99；该项未采用，合法候选保留（模拟数据）。'}],
         'validation_summary':{'returned_count':3,'accepted_count':2,'rejected_count':1}}}
TEMP=tempfile.TemporaryDirectory(prefix='video-workbench-fixture-')
STORE=projects.Store(Path(TEMP.name)/'projects')
RECORDS={EVENTS['record_id']:EVENTS,OPP['record_id']:OPP}
COUNTER=0
FAIL_NEXT=False


class Fixture(app.Handler):
    def do_GET(self):
        path=urlparse(self.path);q=parse_qs(path.query)
        if path.path=='/config':
            return self.respond(200,{'token':app.TOKEN,'models':{'qwen3-vl-plus':'local-fixture'},
                'configured':{'beeroute':True},'samples':[SAMPLE],'games':app.load_game_catalog(),
                'game_templates':app.gameplay_design.templates(),'stage_prompts':app.stage_prompt_catalog()})
        if path.path in ('/projects','/project','/project-export','/project-record'):
            if self.headers.get('X-Local-Token')!=app.TOKEN:return self.respond(403,{'error':'fixture token'})
            try:
                if path.path=='/projects':result=STORE.listing()
                elif path.path=='/project-record':result=RECORDS[q['record_id'][0]]
                else:
                    result=STORE.read(q['id'][0])
                    if path.path=='/project-export':result=projects.export_project(result,q.get('ready')==['1'])
                return self.respond(200,result)
            except (ValueError,KeyError) as e:return self.respond(400,{'error':str(e)})
        return super().do_GET()

    def do_POST(self):
        global COUNTER,FAIL_NEXT
        parsed=urlparse(self.path);q=parse_qs(parsed.query)
        if self.headers.get('X-Local-Token')!=app.TOKEN:return self.respond(403,{'error':'fixture token'})
        if parsed.path=='/projects':
            try:
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                return self.respond(200,STORE.save(payload))
            except projects.Conflict as e:return self.respond(409,{'error':str(e)})
            except ValueError as e:return self.respond(400,{'error':str(e)})
        if parsed.path=='/events':return self.respond(200,EVENTS)
        if parsed.path=='/opportunities':return self.respond(200,OPP)
        if parsed.path=='/fixture-fail-next':
            FAIL_NEXT=True;return self.respond(200,{'ok':True})
        if parsed.path=='/gameplay':
            if FAIL_NEXT:
                FAIL_NEXT=False;return self.respond(400,{'error':'模拟单点失败：没有调用模型'})
            COUNTER+=1;record_id=f'{COUNTER+100:032x}'
            selected=q['selected'][0].split(',');times=json.loads(q['times'][0]);options=[]
            for point_id in selected:
                for i,game in enumerate(('timing','choice')):
                    options.append({'point_id':point_id,'option_id':f'{point_id}-option-{i}', 'title':f'夹具方案 {COUNTER}-{game}',
                        'game_id':game,'game_name':game,'difference':'不同参与方式',
                        **{k:'夹具说明：'+k for k in projects.DESIGN_FIELDS}})
            result={**COMMON,'record_id':record_id,'parent_record_id':'b'*32,'stage':'gameplay','schema_version':3,
                'prompt_file':'prompts/v20/gameplay.txt','catalog_snapshot':app.load_game_catalog(),
                'observations':[{'point_id':p,'requested_time_s':times[p],'frame_time_s':times[p]} for p in selected],
                'analysis':{'recommendations':options,'abstentions':[],
                    'warnings':[{'message':'abstentions[0] 应为对象，实际为字符串（夹具警告）'}]}}
            RECORDS[record_id]=result;return self.respond(200,result)
        if parsed.path=='/briefs':return self.respond(200,{'status':'success',**COMMON})
        return self.respond(404,{'error':'fixture route not found'})


if __name__=='__main__':
    print('Workbench fixture http://127.0.0.1:8768 — no model calls, temp projects only',flush=True)
    try:app.ThreadingHTTPServer(('127.0.0.1',8768),Fixture).serve_forever()
    finally:TEMP.cleanup()
