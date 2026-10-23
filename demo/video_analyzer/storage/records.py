"""Append-only inference record names and legacy-compatible ID lookup."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import secrets


def safe_stem(name):
    stem = Path(name).stem.strip()
    stem = re.sub(r'[\\/:*?"<>|\s]+', '_', stem)
    return stem[:80] or 'video'


def save_record(record, root, clock=datetime, make_id=secrets.token_hex, stemmer=safe_stem):
    model = record['model']
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', model) or model in ('.', '..'):
        raise ValueError('模型名称不能作为结果文件夹。')
    if record.get('mode') not in ('direct', 'frames', 'brief'):
        raise ValueError('记录输入方式无效。')
    folder = root / 'results' / model
    folder.mkdir(parents=True, exist_ok=True)
    stamp = clock.now(timezone(timedelta(hours=8))).strftime('%Y%m%d')
    labels = {'events': '第一步', 'opportunities': '第二步', 'gameplay': '第三步',
              'gameplay_brief': '玩法描述'}
    label = labels.get(record.get('stage'), '单阶段分析')
    stem = f"{stemmer(record.get('video_name') or 'video')}__{record['mode']}__{stamp}_{label}"
    record.setdefault('record_id', make_id(16))
    number = 1
    while True:
        filename = f"{stem}{'_' + str(number) if number > 1 else ''}.json"
        path = folder / filename
        record_file = f'results/{model}/{filename}'
        try:
            # Exclusive creation also protects concurrent requests from overwriting each other.
            with path.open('x', encoding='utf-8') as output:
                json.dump({**record, 'record_file': record_file}, output, ensure_ascii=False, indent=2)
        except FileExistsError:
            number += 1
            continue
        record['record_file'] = record_file
        return path


def saved_record_paths(record_id, folder):
    if not re.fullmatch(r'[0-9a-f]{32}', record_id or ''):
        raise ValueError('记录 ID 无效。')
    folder = Path(folder)
    matches = []
    for path in folder.rglob('*.json'):
        if not path.is_file():
            continue
        if path.name.endswith(f'__{record_id}.json'):
            matches.append(path)
            continue
        if not re.search(r'__\d{8}_(第一步|第二步|第三步|玩法描述|单阶段分析)(?:_\d+)?\.json$', path.name):
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get('record_id') == record_id:
            matches.append(path)
    return sorted(matches)
