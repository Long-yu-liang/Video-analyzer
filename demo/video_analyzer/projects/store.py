"""Local editable projects. Never invokes inference or rewrites result records."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading

LOCK = threading.RLock()
DESIGN_FIELDS = ('fit', 'player_goal', 'player_action', 'success', 'failure', 'feedback')
BRIEF_FIELDS = ('screen', 'rules', 'transition')
GAME_IDS = {'timing', 'hold_release', 'swipe', 'drag', 'tap_object', 'mash', 'choice'}


class Conflict(ValueError):
    pass


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('项目或记录 ID 无效。')
    return value


def text(value, label, required=False, limit=12000):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f'{label} 应为{"非空" if required else ""}文字，最多 {limit} 字。')
    return value


def number(value, duration, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= duration:
        raise ValueError(f'{label} 应为片长范围内的有限数字。')
    return value


def validate(state):
    """Project contract is independent of, and does not tighten, model output contracts."""
    if not isinstance(state, dict):
        raise ValueError('项目内容应为对象。')
    state = deepcopy(state)
    allowed = {'name', 'video', 'session', 'points', 'record_ids', 'seen_record_ids', 'density_s'}
    if set(state) - allowed:
        raise ValueError('项目含未知字段，不接受原始响应或内嵌媒体。')
    text(state.get('name'), '项目名称', True, 120)
    video = state.get('video')
    if video is not None:
        if not isinstance(video, dict) or set(video) - {'name', 'sha256', 'sample', 'duration_s'}:
            raise ValueError('视频身份字段无效。')
        text(video.get('name'), '视频名', True, 250)
        if not re.fullmatch(r'[0-9a-f]{64}', video.get('sha256', '')):
            raise ValueError('视频 SHA-256 无效。')
        sample = video.get('sample', '')
        text(sample, '样例名', limit=250)
        if '/' in sample or '\\' in sample or sample in ('.', '..'):
            raise ValueError('样例只能使用文件名。')
        number(video.get('duration_s'), 60, '视频时长')
    duration = video['duration_s'] if video else 0
    session = state.setdefault('session', {})
    if not isinstance(session, dict) or set(session) - {'events', 'opportunities', 'gameplay', 'settings', 'reviews', 'controls', 'drafts'}:
        raise ValueError('工作区状态无效。')
    for stage in ('events', 'opportunities', 'gameplay'):
        if session.get(stage):
            identifier(session[stage])
    if len(json.dumps(session, ensure_ascii=False)) > 200000:
        raise ValueError('工作区状态过大。')
    settings = session.get('settings', {})
    if not isinstance(settings, dict) or set(settings) - {'model', 'mode', 'fps', 'eventPrompt', 'opportunityPrompt', 'gameplayPrompt'}:
        raise ValueError('工作区设置含未知字段。')
    for key, value in settings.items():
        text(value, key, limit=150)
    for key in ('reviews', 'controls', 'drafts'):
        rows = session.get(key, [])
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValueError('工作区编辑状态过多。')
        for row in rows:
            allowed_row = {'id', 'checked'} if key == 'reviews' else {'point_id', 'checked', 'time_s'} if key == 'controls' else {
                'point_id', 'option_id', 'design', 'status', 'decision_reason', 'stale'}
            if not isinstance(row, dict) or set(row) - allowed_row:
                raise ValueError('工作区编辑字段无效。')
            text(row.get('id' if key == 'reviews' else 'point_id'), '工作区点 ID', True, 150)
            if key != 'drafts' and type(row.get('checked')) is not bool:
                raise ValueError('审核状态应为布尔值。')
            if key == 'controls':
                number(row.get('time_s'), duration, '工作区画面时间')
            if key == 'drafts':
                if row.get('status') not in ('pending', 'accepted', 'edited', 'discarded') or type(row.get('stale')) is not bool:
                    raise ValueError('兼容编辑草稿状态无效。')
                if row.get('option_id') is not None:
                    text(row['option_id'], '方案 ID', True, 150)
                if row.get('design') is not None:
                    check_design(row['design'], False)
                text(row.get('decision_reason', ''), '处理理由')
    for key in ('record_ids', 'seen_record_ids'):
        values = state.setdefault(key, [])
        if not isinstance(values, list) or len(values) > 500:
            raise ValueError('记录引用过多。')
        for value in values:
            identifier(value)
    number(state.setdefault('density_s', 3), 60, '节奏提醒阈值')
    points = state.setdefault('points', [])
    if not isinstance(points, list) or len(points) > 300 or (points and not video):
        raise ValueError('交互点数量或视频身份无效。')
    ids = set()
    for index, point in enumerate(points):
        label = f'points[{index}]'
        if not isinstance(point, dict) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', point.get('id', '')) or point['id'] in ids:
            raise ValueError(f'{label} ID 无效或重复。')
        ids.add(point['id'])
        allowed_point = {'id', 'origin', 'source_record_id', 'point_id', 'time_s', 'opportunity', 'reason', 'review',
                         'enabled', 'versions', 'selected', 'design', 'brief', 'confirmed', 'stale', 'merged_into', 'decision_reason'}
        if set(point) - allowed_point:
            raise ValueError(f'{label} 含未知字段。')
        if point.get('origin') not in ('ai', 'manual') or point.get('review') not in ('pending', 'kept', 'discarded'):
            raise ValueError(f'{label} 来源或审核状态无效。')
        number(point.get('time_s'), duration, label+'.time_s')
        for field in ('opportunity', 'reason'):
            text(point.get(field), label+'.'+field, True)
        text(point.get('decision_reason', ''), label+'.decision_reason')
        if point['origin'] == 'ai':
            identifier(point.get('source_record_id'))
            text(point.get('point_id'), label+'.point_id', True, 100)
        for field in ('enabled', 'confirmed', 'stale'):
            if type(point.get(field)) is not bool:
                raise ValueError(f'{label}.{field} 应为布尔值。')
        if point['enabled'] and point['review'] != 'kept':
            raise ValueError('只有保留的交互点才能启用。')
        versions = point.get('versions', [])
        if not isinstance(versions, list) or len(versions) > 100:
            raise ValueError(f'{label} 方案版本过多。')
        version_ids = set()
        for version in versions:
            if not isinstance(version, dict) or set(version) - {'id', 'record_id', 'option_id', 'design', 'time_s', 'label'}:
                raise ValueError(f'{label} 方案版本字段无效。')
            text(version.get('id'), '方案版本 ID', True, 200)
            if version['id'] in version_ids:
                raise ValueError('方案版本重复。')
            version_ids.add(version['id'])
            identifier(version.get('record_id'))
            text(version.get('option_id'), '方案 ID', True, 150)
            text(version.get('label', ''), '版本名称', limit=500)
            number(version.get('time_s'), duration, '版本画面位置')
            check_design(version.get('design'), False)
        if point.get('selected') is not None and point['selected'] not in version_ids:
            raise ValueError('所选方案不属于该点。')
        if point.get('design') is not None:
            check_design(point['design'], False)
        brief = point.setdefault('brief', {})
        if not isinstance(brief, dict) or set(brief) - set(BRIEF_FIELDS):
            raise ValueError('玩法描述字段无效。')
        for field in BRIEF_FIELDS:
            text(brief.get(field, ''), field)
        if point['confirmed']:
            if point['stale'] or point['review'] != 'kept':
                raise ValueError('待核对或未保留的方案不能确认。')
            check_design(point.get('design'), True)
            for field in BRIEF_FIELDS:
                text(brief.get(field), field, True)
    for point in points:
        if point.get('merged_into') and (point['merged_into'] not in ids or point['merged_into'] == point['id']
                                         or point['review'] != 'discarded' or point['enabled']):
            raise ValueError('合并目标或被合并点状态无效。')
        seen = {point['id']}
        target = point.get('merged_into')
        while target:
            if target in seen:
                raise ValueError('不能循环合并交互点。')
            seen.add(target)
            target = next(p for p in points if p['id'] == target).get('merged_into')
    return state


def check_design(design, complete):
    if not isinstance(design, dict) or set(design) - {'title', 'game_id', *DESIGN_FIELDS}:
        raise ValueError('玩法草稿字段无效。')
    if design.get('game_id') not in GAME_IDS:
        raise ValueError('玩法类型无效。')
    text(design.get('title', ''), '玩法名称', limit=300)
    for field in DESIGN_FIELDS:
        text(design.get(field, ''), field, complete)


def export_project(project, ready=False):
    state = validate(project['state'])
    points = state['points']
    if ready:
        points = [p for p in points if p['enabled'] and p['review'] == 'kept']
        if not points:
            raise ValueError('没有启用的已确认方案。')
        if any(not p['confirmed'] or p['stale'] for p in points):
            raise ValueError('启用项含未确认或待核对方案，请确认或停用后再导出。')
        times = [p['time_s'] for p in points]
        if len(times) != len(set(times)):
            raise ValueError('存在同一时间启用的交互，请错开或停用。')
    return {'export_version': 1, 'purpose': 'confirmed_designs_not_tested_code' if ready else 'all_drafts',
            'project_id': project['project_id'], 'revision': project['revision'], 'name': state['name'],
            'video': state['video'], 'record_ids': state['record_ids'],
            'points': sorted(points, key=lambda p: p['time_s']), 'playable': False}


class Store:
    def __init__(self, root):
        self.root = Path(root)

    def folder(self, project_id):
        folder = (self.root / identifier(project_id)).resolve()
        if folder.parent != self.root.resolve():
            raise ValueError('项目路径不安全。')
        return folder

    def read(self, project_id):
        try:
            item = json.loads((self.folder(project_id) / 'project.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise ValueError('项目不存在。')
        if (not isinstance(item, dict) or item.get('project_version') != 1 or item.get('project_id') != project_id
                or type(item.get('revision')) is not int or item['revision'] < 1):
            raise ValueError('项目头信息损坏，当前文件未修改，可从修订快照恢复。')
        item['state'] = validate(item.get('state'))
        return item

    def listing(self):
        result = []
        for path in self.root.glob('*/project.json'):
            if not re.fullmatch(r'[0-9a-f]{32}', path.parent.name):
                continue
            try:
                item = self.read(path.parent.name)
                result.append({k: item[k] for k in ('project_id', 'revision', 'updated_at')} |
                              {'name': item['state']['name'], 'video_name': (item['state']['video'] or {}).get('name', '')})
            except (ValueError, KeyError, OSError):
                continue
        return sorted(result, key=lambda p: p['updated_at'], reverse=True)

    def save(self, payload):
        state = validate(payload.get('state'))
        with LOCK:
            project_id = payload.get('project_id')
            if project_id:
                previous = self.read(project_id)
                if type(payload.get('revision')) is not int or payload['revision'] != previous['revision']:
                    raise Conflict('项目已有更新，请重新打开或另存副本，未覆盖已有内容。')
                if previous['state']['video'] and (not state['video'] or previous['state']['video']['sha256'] != state['video']['sha256']):
                    raise ValueError('项目视频不能替换，请为新视频创建项目。')
                revision = previous['revision'] + 1
            else:
                project_id, revision = secrets.token_hex(16), 1
            item = {'project_version': 1, 'project_id': project_id, 'revision': revision,
                    'updated_at': datetime.now(timezone.utc).isoformat(), 'state': state}
            folder = self.folder(project_id)
            folder.mkdir(parents=True, exist_ok=True)
            data = json.dumps(item, ensure_ascii=False, indent=2, allow_nan=False)
            # Persist snapshot before replacing current head. A failed save keeps old head valid.
            snapshot = folder / f'revision-{revision:06d}-{secrets.token_hex(4)}.json'
            snapshot.write_text(data, encoding='utf-8')
            fd, temp = tempfile.mkstemp(prefix='.project-', suffix='.tmp', dir=folder)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, folder / 'project.json')
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            return item
