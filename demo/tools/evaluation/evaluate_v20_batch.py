"""Run the requested 2/2/3/3 full-pipeline experiment; failures are not retried.

--prepare freezes local inputs without model calls. --run makes paid requests.
The ledger resumes completed stages but never silently retries an interrupted request.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import threading
import sys
from pathlib import Path

# Support direct execution from any working directory without installing the package.
_demo = str(Path(__file__).resolve().parents[2])
if _demo not in sys.path:
    sys.path.insert(0, _demo)
from video_analyzer import application as app

MODEL = 'qwen3-vl-plus'
CASES = [('S03_抛球接球.mp4', 2), ('S04_无人走廊.mp4', 2),
         ('S05_传球投篮.mp4', 3), ('S06_有声乒乓球.mp4', 3)]
DEFAULT_FOLDER = app.ROOT / 'day3/eval/v20-batch-20261022'


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def prepare(folder):
    prompt_data = {}
    for stage in ('events', 'opportunities', 'gameplay'):
        path, content = app.load_stage_prompt(stage)
        prompt_data[stage] = {'version': path.parent.name, 'file': path.relative_to(app.ROOT).as_posix(),
                              'sha256': hashlib.sha256(content.encode()).hexdigest(), 'content': content}
    videos = []
    for name, count in CASES:
        path = app.ROOT / 'mp4-example' / name
        raw = app.read_sample(name)
        videos.append({'name': name, 'groups': count, 'sha256': hashlib.sha256(raw).hexdigest(),
                       'bytes': len(raw), 'metadata': app.probe(path)})
    manifest = {'model': MODEL, 'events_mode': 'direct', 'fps': 2, 'prompts': prompt_data,
                'videos': videos, 'catalog_sha256': hashlib.sha256(
                    (app.ROOT/'prompts/games/catalog.json').read_bytes()).hexdigest(),
                'review_policy': '全部事件及候选自动送入下一步，阶段间不人工筛选；运行后代理逐图复核，不冒充用户审核。',
                'retry_policy': '不追加重跑失败阶段；第一步保留Demo本身的截断分段重试，并在报告中计数。'}
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'manifest.json'
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8')) != manifest:
            raise ValueError('冻结输入已变化；请使用新的实验目录，不覆盖已有实验。')
    else:
        dump(path, manifest)
    print(json.dumps({'prepared': str(path), 'versions': {k:v['version'] for k,v in prompt_data.items()},
                      'groups': sum(v['groups'] for v in videos)}, ensure_ascii=False), flush=True)
    return manifest


def run(folder):
    if MODEL not in app.MODELS or not app.os.environ.get('BEEROUTE_API_KEY'):
        raise ValueError('模型或密钥未配置。')
    manifest = prepare(folder)
    ledger_path = folder / 'ledger.json'
    ledger = json.loads(ledger_path.read_text(encoding='utf-8')) if ledger_path.exists() else {}
    lock = threading.Lock()

    def verify():
        for stage, frozen in manifest['prompts'].items():
            _, text = app.load_stage_prompt(stage, frozen['version'])
            if hashlib.sha256(text.encode()).hexdigest() != frozen['sha256']:
                raise ValueError('实验期间提示词发生变化，停止后续调用。')
        if hashlib.sha256((app.ROOT/'prompts/games/catalog.json').read_bytes()).hexdigest() != manifest['catalog_sha256']:
            raise ValueError('实验期间小游戏目录发生变化。')

    def call(group, stage, function):
        with lock:
            entry = ledger.setdefault(group, {}).get(stage)
            if entry:
                if entry['status'] == 'started':
                    raise ValueError(f'{group}/{stage}上次被中断，请先核对原始记录，不能自动重试。')
                if entry['status'] != 'success':
                    return None
                return app.load_stage_result(entry['record_id'], stage, '实验记录')
            verify()
            entry = {'status': 'started', 'started_at': datetime.now(timezone.utc).isoformat()}
            ledger[group][stage] = entry
            dump(ledger_path, ledger)
            print(f'START {group} {stage}', flush=True)
        result = function()
        if result.get('prompt', '').strip() != manifest['prompts'][stage]['content']:
            raise ValueError('实际提示词不等于冻结文本，停止实验。')
        if stage == 'events':
            count = len(result.get('analysis', {}).get('events', []))
        elif stage == 'opportunities':
            count = len(result.get('analysis', {}).get('interaction_points', []))
        else:
            count = len(result.get('analysis', {}).get('recommendations', []))
        with lock:
            entry.update(status=result['status'], record_id=result['record_id'],
                         record_file=result['record_file'], error=result.get('error'), count=count,
                         total_s=result.get('total_s'), usage=result.get('usage'),
                         request_count=app.model_request_count(result))
            dump(ledger_path, ledger)
            print(json.dumps({'group': group, 'stage': stage, **entry}, ensure_ascii=False), flush=True)
        return result if result['status'] == 'success' else None

    def skip(group, stage, reason):
        with lock:
            ledger.setdefault(group, {}).setdefault(stage, {'status': 'skipped', 'reason': reason})
            dump(ledger_path, ledger)

    def pipeline(video, repeat):
        name = video['name']; group = f'{name[:3]}-R{repeat}'
        raw = app.read_sample(name)
        if hashlib.sha256(raw).hexdigest() != video['sha256']:
            raise ValueError('视频哈希发生变化。')
        events = call(group, 'events', lambda: app.analyze_events(
            raw, name, manifest['prompts']['events']['version'], MODEL, 'direct', 2.))
        if events is None or not events['analysis']['events']:
            skip(group, 'opportunities', '第一步失败或没有事件')
            skip(group, 'gameplay', '没有可用事件')
            return
        event_ids = [e['observation_id'] for e in events['analysis']['events']]
        opportunities = call(group, 'opportunities', lambda: app.suggest_opportunities(
            raw, name, events['record_id'], event_ids, manifest['prompts']['opportunities']['version'], MODEL))
        if opportunities is None or not opportunities['analysis']['interaction_points']:
            skip(group, 'gameplay', '第二步失败或空候选；未调用第三步')
            return
        point_ids = [p['point_id'] for p in app.indexed_points(opportunities)]
        call(group, 'gameplay', lambda: app.suggest_gameplay(
            raw, name, opportunities['record_id'], point_ids, manifest['prompts']['gameplay']['version'], MODEL))

    jobs = [(video, repeat) for video in manifest['videos'] for repeat in range(1, video['groups']+1)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(pipeline, video, repeat) for video, repeat in jobs]
        for task in tasks:
            task.result()
    print('FINISHED ' + str(ledger_path), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, default=DEFAULT_FOLDER)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if args.run:
        run(args.folder)
    elif args.prepare:
        prepare(args.folder)
