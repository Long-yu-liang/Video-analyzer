"""Readable append-only game records; legacy ID files remain readable."""
from datetime import datetime, timedelta, timezone
import json
import hashlib
from pathlib import Path
import re
from ..projects.store import identifier
from .records import safe_stem


def identity(record):
    data = record.get('input', {})
    model = record.get('model', 'unknown-model')
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', model) or model in ('.', '..'):
        raise ValueError('游戏记录模型目录无效。')
    video = safe_stem(record.get('video_name') or data.get('video', {}).get('name') or 'video')[:55]
    point = record.get('point_id', 'point')
    match = re.search(r'(?:^|_)(p\d+)$', point)
    label = match.group(1) if match else safe_stem(point)[:24]
    game = safe_stem(record.get('game_id') or data.get('design', {}).get('game_id') or 'game')[:24]
    return model, video, label, game


def destination(root, record):
    model, video, point, game = identity(record)
    day = datetime.fromisoformat(record['created_at']).astimezone(timezone(timedelta(hours=8))).strftime('%Y%m%d')
    label = ('第四步修复' if record.get('parent_record_id') else '第四步生成') if record['stage'] == 'game_generation' else '试玩记录'
    directory = Path(root) / 'results' / model / 'games' / video / point
    if record['stage'] == 'game_playtest':directory = directory / '试玩'
    directory.mkdir(parents=True, exist_ok=True)
    stem = f'{video}__{point}__{game}__{day}_{label}'
    number = 1
    while True:
        path = directory / f"{stem}{'_' + str(number) if number > 1 else ''}.json"
        if not path.exists():return path
        number += 1


def candidates(root):
    results = Path(root) / 'results'
    yield from (results / 'day4').glob('*.json')
    for path in results.glob('*/games/*/*/*.json'):
        yield path
    yield from results.glob('*/games/*/*/试玩/*.json')


def migrate_playtests(root):
    report = {'moved': [], 'skipped': []}
    for source in list(candidates(root)):
        try:
            item = json.loads(source.read_text(encoding='utf-8'))
            if not isinstance(item, dict) or item.get('stage') != 'game_playtest' or source.parent.name == '试玩':continue
            parent = read(root, item.get('generation_id'))
            if parent.get('stage') != 'game_generation' or parent.get('project_id') != item.get('project_id') or parent.get('point_id') != item.get('point_id'):
                raise ValueError('试玩与生成记录归属不一致')
            enriched = {**item, 'model': parent['model'], 'video_name': parent['input']['video']['name'], 'game_id':parent['input']['design']['game_id']}
            target = destination(root, enriched)
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            if not target.resolve().is_relative_to((Path(root)/'results').resolve()):raise ValueError('目标不安全')
            source.rename(target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != before:
                target.rename(source);raise ValueError('哈希不一致，已退回原路径')
            report['moved'].append({'record_id':item['record_id'],'from':source.relative_to(root).as_posix(),'to':target.relative_to(root).as_posix(),'sha256':before})
        except (ValueError, KeyError, OSError, TypeError) as error:
            report['skipped'].append({'path':source.relative_to(root).as_posix(),'reason':str(error)})
    return report


def read(root, record_id):
    identifier(record_id)
    matches = []
    for path in candidates(root):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError):continue
        if isinstance(record, dict) and record.get('record_id') == record_id:
            matches.append((path, record))
    if len(matches) != 1:
        raise ValueError('游戏记录不存在或 ID 不唯一。')
    path, record = matches[0]
    record['record_file'] = path.relative_to(root).as_posix()
    directory = path.parent / (path.stem + '_产物')
    record['artifacts'] = {key: (directory / name).relative_to(root).as_posix()
                           for key, name in (('javascript', 'game.js'), ('css', 'style.css'), ('design', 'design.json'))
                           if (directory / name).is_file()}
    return record


def artifacts(root, path, record):
    """Derived convenience files are not executable until checks pass."""
    output = record.get('output')
    if not isinstance(output, dict):return {}
    directory = path.parent / (path.stem + '_产物')
    directory.mkdir(exist_ok=True)
    result = {}
    for key, filename in (('javascript', 'game.js'), ('css', 'style.css'), ('design', 'design.json')):
        value = output.get(key)
        if value is None:continue
        if key == 'design':value = json.dumps(value, ensure_ascii=False, indent=2)
        if not isinstance(value, str):continue
        target = directory / filename
        with target.open('x', encoding='utf-8', newline='') as stream:stream.write(value)
        result[key] = target.relative_to(root).as_posix()
    return result


def write(root, record):
    identifier(record['record_id'])
    path = destination(root, record)
    # Exclusive creation protects concurrent calls even if names collide.
    while True:
        try:
            stream = path.open('x', encoding='utf-8')
            break
        except FileExistsError:path = destination(root, record)
    record['record_file'] = path.relative_to(root).as_posix()
    with stream:
        try:record['artifacts'] = artifacts(root, path, record)
        except OSError as error:
            record['artifact_warning'] = f'产物导出失败，原始 JSON 已保留：{error}'
        json.dump(record, stream, ensure_ascii=False, indent=2)
    return path


def migrate(root, record_id):
    """Relocate exactly one legacy file, preserving its original bytes and ID."""
    original = read(root, record_id)
    source = Path(root) / original['record_file']
    if source.parent.resolve() != (Path(root) / 'results/day4').resolve():
        return source
    target = destination(root, original)
    if not target.resolve().is_relative_to((Path(root) / 'results').resolve()):
        raise ValueError('迁移目标不安全。')
    source.rename(target)
    artifacts(root, target, original)
    return target
