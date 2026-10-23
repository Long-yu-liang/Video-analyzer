"""Local browser fixture, never calls an AI or saves production inference records.

Run from repo root: python demo/tests/fixtures/browser_design_fixture.py (port 8767).
"""
import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import parse_qs, urlparse
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import app

PARENT = app.load_stage_result('9404c94c9312b9d338ba8ae13048283d', 'opportunities', '机会')
PARENT['record_id'] = 'b'*32
PARENT['analysis']['interaction_points'] = PARENT['analysis']['interaction_points'][:1]
SPEC = app.gameplay_design.templates()
CATALOG = app.load_game_catalog()
LAST = None


class Fixture(app.Handler):
    def do_GET(self):
        if urlparse(self.path).path == '/config':
            return self.respond(200, {'token': app.TOKEN, 'models': {'qwen3-vl-plus':'fixture'},
                'configured': {'beeroute': True}, 'samples': [PARENT['video_name']],
                'games': CATALOG, 'game_templates': SPEC, 'stage_prompts': app.stage_prompt_catalog()})
        return super().do_GET()

    def do_POST(self):
        global LAST
        parsed=urlparse(self.path);q=parse_qs(parsed.query)
        if self.headers.get('X-Local-Token') != app.TOKEN:
            return self.respond(403, {'error':'fixture token'})
        if parsed.path == '/events':
            return self.respond(200, {'status':'success','record_id':'a'*32,'record_file':'FIXTURE-events.json',
                'model':'qwen3-vl-plus','video_name':PARENT['video_name'],'prompt_file':'FIXTURE','total_s':0,
                'analysis': {'summary':'浏览器测试夹具，不是模型调用','limitations':[], 'events':[
                    {'observation_id':'e2','start_s':1,'end_s':3,'observation_s':2.08,
                     'description':'容器靠近杯子','evidence':'测试画面','uncertainty':'fixture'}]}})
        if parsed.path == '/opportunities':
            return self.respond(200, {**PARENT,'record_file':'FIXTURE-opportunities.json'})
        if parsed.path == '/gameplay':
            if q.get('version') == ['v18']:
                return self.respond(200, {'status':'failed','error':'模拟网络失败，未调用模型'})
            requested=json.loads(q['times'][0])['p1']
            with tempfile.TemporaryDirectory() as temp:
                frames=app.extract(app.ROOT/'mp4-example'/PARENT['video_name'],Path(temp),PARENT['metadata']['duration_s'],2)
            frame=min(frames,key=lambda f:abs(f['time_s']-requested))
            point={**app.indexed_points(PARENT)[0], 'time_s':frame['time_s']}
            common={k:'测试夹具：'+k for k in app.gameplay_design.TEXT_FIELDS}
            if q.get('version') == ['v20']:
                core={key:'测试夹具：'+key for key in app.gameplay_recommendations.FIELDS}
                options=[{**core,'point_id':'p1','game_id':'choice','title':'选择取用对象'},
                         {**core,'point_id':'p1','game_id':'drag','title':'对准杯口'}]
                answer={'recommendations':options,'abstentions':['p1：格式警告测试夹具，不是真实模型结果']}
                LAST={'status':'success','schema_version':3,'record_id':'c'*32,
                    'record_file':'FIXTURE-recommendation.json','parent_record_id':PARENT['record_id'],
                    'selected_point_ids':['p1'],'model':'qwen3-vl-plus','video_name':PARENT['video_name'],
                    'prompt_file':'prompts/v20/gameplay.txt','total_s':0,'catalog_snapshot':CATALOG,
                    'observations':[{'point_id':'p1','requested_time_s':requested,'frame_time_s':frame['time_s'],
                        'frame_sha256':hashlib.sha256(base64.b64decode(frame['data'])).hexdigest()}],
                    'raw_text':json.dumps(answer,ensure_ascii=False),
                    'analysis':app.gameplay_recommendations.validate(json.dumps(answer),[point],CATALOG)}
                return self.respond(200,LAST)
            options=[{**common,'point_id':'p1','game_id':'timing','title':'对准节奏','target_labels':[]},
                     {**common,'point_id':'p1','game_id':'tap_object','title':'指出杯子','target_labels':['玻璃杯']}]
            LAST={'status':'success','schema_version':2,'record_id':'c'*32,'record_file':'FIXTURE-gameplay.json',
                'parent_record_id':PARENT['record_id'],'selected_point_ids':['p1'],'model':'qwen3-vl-plus',
                'video_name':PARENT['video_name'],'prompt_file':'prompts/v19/gameplay.txt','total_s':0,
                'template_snapshot':SPEC,'catalog_snapshot':CATALOG,
                'observations':[{'point_id':'p1','requested_time_s':requested,'frame_time_s':frame['time_s'],
                  'frame_sha256':hashlib.sha256(base64.b64decode(frame['data'])).hexdigest(),
                  'preview_jpeg_base64':frame['data']}],
                'analysis':app.gameplay_design.validate_recommendations(json.dumps({'recommendations':options}),[point],CATALOG,SPEC)}
            return self.respond(200,LAST)
        if parsed.path == '/briefs':
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            try:
                if LAST.get('schema_version') == 3:
                    selections=app.gameplay_recommendations.build_brief(payload,PARENT,LAST,
                        {'p1':app.indexed_points(PARENT)[0]})
                else:
                    selections=app.gameplay_design.build_brief(payload,PARENT,LAST,
                        {'p1':app.indexed_points(PARENT)[0]}, {t['id']:t['name'] for t in CATALOG['types']})
                return self.respond(200, {'status':'success','record_file':'FIXTURE-brief.json',
                    'video_name':PARENT['video_name'],'schema_version':LAST.get('schema_version'),'selections':selections})
            except ValueError as error:
                return self.respond(400,{'error':str(error)})
        return self.respond(404,{'error':'Unknown fixture route'})


if __name__ == '__main__':
    print('Browser fixture at http://127.0.0.1:8767 — no model calls',flush=True)
    app.ThreadingHTTPServer(('127.0.0.1',8767),Fixture).serve_forever()
