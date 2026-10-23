"""Game workbench browser fixture; mocked generation and temporary projects/results."""
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import app
from video_analyzer.gameplay import generation

original_root = app.ROOT
temporary = tempfile.TemporaryDirectory(prefix='game-workbench-browser-')
app.ROOT = Path(temporary.name)
shutil.copytree(original_root / 'prompts/game-generation', app.ROOT / 'prompts/game-generation')
(app.ROOT / 'mp4-example').mkdir()
shutil.copyfile(original_root / 'mp4-example/S05_传球投篮.mp4', app.ROOT / 'mp4-example/sample.mp4')
point = {'id': 'fixture_point', 'origin': 'manual', 'time_s': 1, 'opportunity': '本地夹具：持物操作',
         'reason': '本地协议测试，不是真实分析', 'review': 'kept', 'enabled': True, 'versions': [], 'selected': None,
         'design': {'title': '本地夹具：点击与按住', 'game_id': 'hold_release',
                    **{k: '测试夹具说明' for k in app.projects.DESIGN_FIELDS}}, 'brief': {}, 'confirmed': False, 'stale': False}
state = {'name': '游戏工作台浏览器夹具', 'video': {'name': 'sample.mp4', 'sha256': 'a'*64, 'duration_s': 16.68, 'sample': ''},
         'session': {}, 'points': [point], 'record_ids': [], 'seen_record_ids': [], 'density_s': 3}
project = app.projects.Store(app.ROOT / 'projects').save({'state': state})


def fake_model(request, timeout):
    body = json.loads(request.data)
    data = {'design': {**{k: '夹具设计，不代表模型效果' for k in generation.DESIGN_FIELDS}, 'timeout_s': 8},
            'javascript': """function mount(c,a){
              const b=document.createElement('button');b.textContent='按住后松开（夹具）';c.append(b);let start;
              b.onpointerdown=e=>{start=Date.now();b.setPointerCapture(e.pointerId);};
              b.onpointerup=()=>a.finish({result:Date.now()-start>400?'success':'failure',message:Date.now()-start>400?'成功（夹具）':'太早（夹具）'});
              const x=document.createElement('button');x.textContent='模拟运行错误';c.append(x);x.onclick=()=>a.reportError('夹具模拟错误');
              return {destroy(){b.remove();x.remove();}};
            }""", 'css': '#game{display:flex;align-items:center;justify-content:center;gap:12px}#game button{padding:20px;background:#fff8dc;border:2px solid #23362f;border-radius:10px}'}
    return io.BytesIO(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(data)}}],
                                 'usage': {'prompt_tokens': 0, 'completion_tokens': 0}}).encode())


generation.urlopen = fake_model


class Fixture(app.Handler):
    def do_GET(self):
        if self.path == '/layout':
            controls = ''.join(f'<label>模型设置 {i}<select><option>测试选项</option></select></label>' for i in range(15))
            content = ('单点文字输入，长内容不应被侧栏遮挡。' * 70) + ('very_long_identifier_' * 30)
            html = f'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
            <link rel="stylesheet" href="/assets/css/base.css"><link rel="stylesheet" href="/assets/css/workbench.css"><link rel="stylesheet" href="/assets/css/game-workbench.css">
            <body data-workbench-view="design"><h1>游戏工作台布局回归夹具（无模型调用）</h1>
            <button onclick="document.getElementById('gameWorkbenchPanel').scrollIntoView()">查看游戏工作台</button>
            <button onclick="document.body.dataset.workbenchView='arrange'">切换编排布局</button>
            <main class="grid"><section id="inputPanel"><h2>输入与视频回看</h2>{controls}<button>第一步</button><button>第二步</button><button>第三步</button></section>
            <section id="wbTimelinePanel">时间轴</section><div id="wbDesign"><section style="height:450px">玩法比较与编辑</section></div>
            <section id="wbArrange">编排清单</section><section id="legacyGameplayPanel">兼容入口</section>
            <section id="gameWorkbenchPanel"><h2>游戏工作台 / 单点生成与试玩</h2><p>只发送当前点的文字，不发送视频或截图。</p><pre>{content}</pre>
            <button>确认输入并生成此点</button><button>重新开始串联试玩</button></section></main></body>'''
            return self.respond(200, html.encode(), 'text/html; charset=utf-8')
        if self.path == '/fixture':
            data = json.dumps(app.projects.Store(app.ROOT/'projects').read(project['project_id']), ensure_ascii=False).replace('<', '\\u003c')
            html = '''<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/assets/css/base.css"><link rel="stylesheet" href="/assets/css/game-workbench.css">
            <h1>游戏工作台浏览器夹具（模拟生成，不计费）</h1><main><section><div id="videoStage"><video id="video" controls src="/sample?name=sample.mp4"></video><div id="gameOverlay" hidden></div></div><div id="offers"></div></section></main>
            <script src="/assets/js/token-usage.js"></script><script src="/assets/js/game-workbench.js"></script><script>let project=DATA;
            let state=project.state;GameWorkbench.init(()=>({project,state,verified:true,busy:false,token:TOKEN,applyProject:p=>{project=p;state=p.state;GameWorkbench.sync();GameWorkbench.renderArrangement(arrange);}}));
            GameWorkbench.offer(document.getElementById('offers'),state.points[0],async()=>({project,point:state.points[0]}),true);
            const arrange=document.createElement('section');document.querySelector('main').append(arrange);GameWorkbench.renderArrangement(arrange);
            </script>'''.replace('DATA', data).replace('TOKEN', json.dumps(app.TOKEN))
            return self.respond(200, html.encode(), 'text/html; charset=utf-8')
        return super().do_GET()


if __name__ == '__main__':
    # Explicit fake credential, used only by mocked local transport.
    import os
    os.environ['MINIMAX_API_KEY'] = 'fixture-not-a-real-key'
    if '--seed' in sys.argv:
        from copy import deepcopy
        second=deepcopy(point);second['id']='fixture_second';second['time_s']=5;second['opportunity']='夹具第二个操作';second['design']['title']='本地夹具：第二个操作'
        project['state']['points'].append(second);project=app.projects.Store(app.ROOT/'projects').save(project)
        payload = {'project_id': project['project_id'], 'revision': project['revision'], 'point_id': point['id']}
        first = generation.generate(app.ROOT, payload, fake_model)
        generation.generate(app.ROOT, payload, fake_model)
        generation.select(app.ROOT, {'generation_id':first['record_id'], 'enabled':True})
        generation.playtest(app.ROOT, {'generation_id':first['record_id'],'result':'exit','note':'夹具预置记录，不是人工试玩','manual_review':False})
        next_game=generation.generate(app.ROOT,{**payload,'point_id':second['id']},fake_model)
        generation.select(app.ROOT,{'generation_id':next_game['record_id'],'enabled':True})
    server = app.ThreadingHTTPServer(('127.0.0.1', 8771), Fixture)
    print('Game workbench fixture http://127.0.0.1:8771/fixture', flush=True)
    try:server.serve_forever()
    finally:server.server_close();temporary.cleanup()
