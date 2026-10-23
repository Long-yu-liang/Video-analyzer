"""Project game inventory and portable, non-executing backups. No inference."""
import io
import json
from pathlib import Path
import zipfile
from ..projects.store import Store, identifier
from ..storage import games
from . import generation


def inventory(root, project_id):
    project = Store(Path(root) / 'projects').read(identifier(project_id))
    path = Path(root) / 'projects' / project_id / 'games.json'
    warnings = []
    try:
        selected = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if not isinstance(selected, dict):raise ValueError('启用索引不是对象')
    except (ValueError, OSError) as error:
        selected = {};warnings.append('游戏启用索引无法读取，未覆盖原文件：'+str(error))
    records = [];reviews = {}
    for file in games.candidates(root):
        try:item = json.loads(file.read_text(encoding='utf-8'))
        except (ValueError, OSError):continue
        if not isinstance(item, dict) or item.get('project_id') != project_id:continue
        if item.get('stage') == 'game_generation':
            records.append({**item, 'record_file': file.relative_to(root).as_posix()})
        elif item.get('stage') == 'game_playtest':
            reviews.setdefault(item.get('generation_id'), []).append(item)
    rows = []
    for point in sorted(project['state']['points'],key=lambda p:p['time_s']):
        try:_, _, current_hash = generation.snapshot(root, project_id, point['id'])
        except ValueError:current_hash = None
        versions = []
        for item in sorted((r for r in records if r.get('point_id') == point['id']), key=lambda r:r['created_at'], reverse=True):
            tests = sorted(reviews.get(item['record_id'], []), key=lambda r:r['created_at'], reverse=True)
            stale = item.get('input_hash') != current_hash or current_hash is None
            versions.append({'record_id': item['record_id'], 'created_at': item['created_at'], 'status': item['status'],
                'title': item.get('input', {}).get('design', {}).get('title', '未命名游戏'), 'record_file': item['record_file'],
                'stale': stale, 'usable': item['status']=='checked' and not stale,
                'selected': selected.get(point['id'])==item['record_id'], 'error': item.get('error'),
                'playtest_count': len(tests), 'manual_review_count': sum(t.get('manual_review') is True for t in tests),
                'latest_review': {k: tests[0].get(k) for k in ('result','note','manual_review')} if tests else None})
        chosen = selected.get(point['id'])
        if chosen and not any(v['record_id']==chosen for v in versions):
            warnings.append(f"{point['id']} 的采用版本缺失或不属于该项目，其他游戏仍可使用。")
        rows.append({'point_id': point['id'], 'time_s': point['time_s'], 'enabled':point['enabled'], 'title': (point.get('design') or {}).get('title') or point['opportunity'],
                     'versions': versions, 'selected_generation_id': chosen})
    return {'project_id': project_id, 'project_revision': project['revision'], 'points': rows, 'warnings': warnings,
            'saved_count': len(records), 'ready_count': sum(p['enabled'] and any(v['selected'] and v['usable'] for v in p['versions']) for p in rows)}


def bundle(root, project_id, generation_id=None):
    project = Store(Path(root) / 'projects').read(identifier(project_id))
    overview = inventory(root, project_id)
    ids = [generation_id] if generation_id else [v['record_id'] for p in overview['points'] for v in p['versions']]
    if not ids:raise ValueError('项目尚无可导出的游戏。')
    records = []
    for record_id in dict.fromkeys(ids):
        item = games.read(root, record_id)
        if item.get('project_id') != project_id or item.get('stage') != 'game_generation':
            raise ValueError('游戏不属于该项目。')
        records.append(item)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('project.json', json.dumps(project, ensure_ascii=False, indent=2))
        archive.writestr('manifest.json', json.dumps(overview, ensure_ascii=False, indent=2))
        archive.writestr('README.txt', '游戏备份包，不含原视频、不自动运行代码。原视频重新选择后须核对 SHA-256。\n项目草稿、采用版本及全部代码快照供离线留存，当前没有一键导入整包入口。\nchecked 仅代表结构与语法检查，stale 表示当前项目已修改，不代表已人工试玩通过。\n')
        for item in records:
            prefix = 'games/'+item['record_id']+'/'
            archive.writestr(prefix+'record.json', json.dumps(item, ensure_ascii=False, indent=2))
            data = item.get('output') or {}
            for key,name in (('javascript','game.js'),('css','style.css'),('design','design.json')):
                value = data.get(key)
                if value is not None and (key == 'design' or isinstance(value, str)):
                    archive.writestr(prefix+name, json.dumps(value, ensure_ascii=False, indent=2) if key=='design' else value)
        for file in games.candidates(root):
            try:test = json.loads(file.read_text(encoding='utf-8'))
            except (ValueError, OSError):continue
            if (isinstance(test, dict) and test.get('stage')=='game_playtest'
                    and test.get('project_id')==project_id and test.get('generation_id') in ids):
                archive.writestr('playtests/'+identifier(test['record_id'])+'.json', json.dumps(test, ensure_ascii=False, indent=2))
    return output.getvalue()
