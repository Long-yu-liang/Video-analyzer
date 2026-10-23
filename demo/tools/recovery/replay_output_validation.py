"""Replay frozen v20 responses locally, without changing inference history or calling APIs."""
import hashlib
import json
import sys
from pathlib import Path

# Support direct execution from any working directory without installing the package.
_demo = str(Path(__file__).resolve().parents[2])
if _demo not in sys.path:
    sys.path.insert(0, _demo)
from video_analyzer import application as app
from video_analyzer.contracts.output import ContractError


def main():
    folder = app.ROOT / 'day3/eval/v20-batch-20261022'
    ledger = json.loads((folder/'ledger.json').read_text(encoding='utf-8'))
    rows=[]
    for group, stages in sorted(ledger.items()):
        entry = stages['gameplay']
        if not entry.get('record_file'):
            continue
        path = app.ROOT/entry['record_file']
        original=path.read_bytes()
        record=json.loads(original)
        parent=app.load_stage_result(record['parent_record_id'],'opportunities','机会')
        selected=set(record['selected_point_ids'])
        points=[p for p in app.indexed_points(parent) if p['point_id'] in selected]
        row={'group':group,'source_file':entry['record_file'],
             'source_sha256':hashlib.sha256(original).hexdigest(),'source_status':record['status'],
             'source_error':record.get('error')}
        try:
            result=app.gameplay_recommendations.validate(app.extract_json(record['raw_text']),points,
                record.get('catalog_snapshot') or app.load_game_catalog())
            row.update(replay_status='success',recommendations=len(result['recommendations']),
                       warnings=result['warnings'],derived_analysis=result)
        except ContractError as error:
            row.update(replay_status='failed',error=error.detail)
        if path.read_bytes()!=original:
            raise ValueError('历史记录被修改。')
        rows.append(row)
    report={'mode':'offline_revalidation_only','new_model_calls':0,
            'note':'这是现有响应在新校验器下的离线结果；原模型记录保持原状态，不是重跑实验或玩法质量提升。',
            'old_success':sum(r['source_status']=='success' for r in rows),
            'new_success':sum(r['replay_status']=='success' for r in rows),
            'rescued_recommendations':sum(r.get('recommendations',0) for r in rows if r['source_status']=='failed'),
            'records':rows}
    target=app.ROOT/'day3/eval/v20-format-fix/replay.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='records'},ensure_ascii=False))
    print(str(target))


if __name__=='__main__':
    main()
