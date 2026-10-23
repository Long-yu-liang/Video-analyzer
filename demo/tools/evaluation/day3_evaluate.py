"""Explicit Day 3 comparison runner. --prepare is offline; --run costs at most nine calls.

All generated records are real API responses. Never retry failures to fill success counts.
"""
import argparse
import base64
import hashlib
import json
import tempfile
import sys
from pathlib import Path

# Support direct execution from any working directory without installing the package.
_demo = str(Path(__file__).resolve().parents[2])
if _demo not in sys.path:
    sys.path.insert(0, _demo)
from video_analyzer import application as app

FOLDER = app.ROOT / 'day3/eval/v19'
CASES = [
    ('S05', 'a0ccdb55e915fcb66fa700bdff23905d', ['p2', 'p5']),
    ('S01', '9404c94c9312b9d338ba8ae13048283d', ['p1', 'p2']),
    ('S06', '456b55c8b1bdc87960d7075dc38c3c88', ['p1', 'p3']),
]
PREP_TIMES = {'S05': {'p2': 2.5, 'p5': 9.5}}


def prepare():
    FOLDER.mkdir(parents=True, exist_ok=True)
    for name, record_id, selected in CASES:
        parent = app.load_stage_result(record_id, 'opportunities', '机会')
        video = app.ROOT / 'mp4-example' / parent['video_name']
        with tempfile.TemporaryDirectory() as temp:
            frames = app.extract(video, Path(temp), parent['metadata']['duration_s'], 2.)
            for point in app.indexed_points(parent):
                if point['point_id'] not in selected:
                    continue
                requested = PREP_TIMES.get(name, {}).get(point['point_id'], point['time_s'])
                frame = min(frames, key=lambda f: abs(f['time_s']-requested))
                path = FOLDER / f"{name}-{point['point_id']}-frozen.jpg"
                path.write_bytes(base64.b64decode(frame['data']))
                print(json.dumps({'case':name, 'point_id':point['point_id'], 'requested':requested,
                                  'frame':frame['time_s'], 'image':str(path),
                                  'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}, ensure_ascii=False), flush=True)


def run():
    manifest = json.loads((FOLDER / 'manifest.json').read_text(encoding='utf-8'))
    if not manifest.get('frozen'):
        raise ValueError('先审核并冻结 manifest.json。')
    ledger_path = FOLDER / 'calls.json'
    ledger = json.loads(ledger_path.read_text(encoding='utf-8')) if ledger_path.exists() else []
    for case in manifest['cases']:
        if not case['selected_point_ids']:
            continue
        parent = app.load_stage_result(case['parent_record_id'], 'opportunities', '机会')
        raw = app.read_sample(parent['video_name'])
        if hashlib.sha256(raw).hexdigest() != parent['video_sha256']:
            raise ValueError('素材哈希变化。')
        for version, repeat in [('v18', 1), ('v19', 1), ('v19', 2)]:
            key = f"{case['name']}-{version}-{repeat}"
            if any(item['key'] == key for item in ledger):
                continue
            if len(ledger) >= 9:
                raise ValueError('已达到九次实验请求上限。')
            # Reserve the attempt before the network call: an interruption is never silently retried.
            entry = {'key':key, 'case':case['name'], 'version':version, 'repeat':repeat,
                     'status':'started', 'parent_record_id':case['parent_record_id']}
            ledger.append(entry)
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'START {key}', flush=True)
            result = app.suggest_gameplay(raw, parent['video_name'], case['parent_record_id'],
                                          case['selected_point_ids'], version, 'qwen3-vl-plus', case['times'])
            entry.update(status=result['status'], record_file=result.get('record_file'),
                         record_id=result['record_id'], error=result.get('error'),
                         usage=result.get('usage'), total_s=result.get('total_s'))
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(entry, ensure_ascii=False), flush=True)
    corridor = app.load_stage_result('385534e1e39e9a2bbb6839e56fc10528', 'opportunities', '机会')
    assert not corridor['analysis']['interaction_points']
    print('S04 empty: no third-stage call', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if args.prepare:
        prepare()
    if args.run:
        run()
