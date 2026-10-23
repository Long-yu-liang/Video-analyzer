"""Offline recovery: python demo/tools/recovery/revalidate_opportunities.py <original-record-id>."""
import argparse
import json
import sys
from pathlib import Path

# Support direct execution from any working directory without installing the package.
_demo = str(Path(__file__).resolve().parents[2])
if _demo not in sys.path:
    sys.path.insert(0, _demo)
from video_analyzer import application as app

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='重新校验已有机会响应，另存本地派生结果，不调用模型。')
    parser.add_argument('record_id')
    args = parser.parse_args()
    try:
        record = app.revalidate_opportunity_record(args.record_id)
    except ValueError as error:
        parser.exit(1, str(error)+'\n')
    print(json.dumps({'record_file':record['record_file'], 'source_record_id':record['source_record_id'],
                      'new_model_calls':0, 'validation_summary':record['analysis']['validation_summary'],
                      'warnings':record['analysis']['warnings']}, ensure_ascii=False, indent=2))
