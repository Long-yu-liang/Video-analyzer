"""Explicit local-only relocation of legacy game records; no model calls."""
import argparse
import hashlib
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from video_analyzer.paths import ROOT
from video_analyzer.storage import games


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('record_ids', nargs='*')
    parser.add_argument('--playtests',action='store_true',help='归档已有试玩到玩点/试玩目录')
    parser.add_argument('--apply', action='store_true', help='实际迁移；默认仅预览')
    args = parser.parse_args()
    if args.playtests:
        if not args.apply:print('试玩归档尚未执行，添加 --apply')
        else:
            import json
            report=games.migrate_playtests(ROOT)
            target=ROOT/'day4/试玩归档迁移结果.json';target.parent.mkdir(exist_ok=True)
            target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(f"试玩归档：移动 {len(report['moved'])}，跳过 {len(report['skipped'])}；报告 day4/试玩归档迁移结果.json")
    for record_id in args.record_ids:
        record = games.read(ROOT, record_id)
        source = ROOT / record['record_file']
        if not args.apply:
            print(f'{record_id}: {source.relative_to(ROOT)}（尚未迁移，添加 --apply）')
            continue
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        target = games.migrate(ROOT, record_id)
        if hashlib.sha256(target.read_bytes()).hexdigest() != before:
            raise RuntimeError('迁移后哈希不一致。')
        print(f'{target.relative_to(ROOT)}：原始 JSON SHA-256 一致，无模型调用')
