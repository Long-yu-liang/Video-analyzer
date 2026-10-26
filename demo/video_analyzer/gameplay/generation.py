"""Single-point text-to-JS generation. Never executes generated code on the server."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import threading
from urllib.request import Request, urlopen
from ..projects.store import Store, Conflict, identifier
from ..storage import games as game_store
from .. import settings

LOCK = threading.RLock()
ACTIVE = set()
DESIGN_FIELDS = ('context', 'goal', 'screen', 'input', 'rules', 'states', 'feedback')


def snapshot(root, project_id, point_id, revision=None):
    project = Store(Path(root) / 'projects').read(identifier(project_id))
    if revision is not None and (type(revision) is not int or revision != project['revision']):
        raise Conflict('项目已有更新，请重新保存或打开后生成。')
    points = [p for p in project['state']['points'] if p['id'] == point_id]
    if len(points) != 1:
        raise ValueError('项目中没有这个玩点。')
    point = points[0]
    if point['review'] != 'kept' or point['stale'] or point.get('merged_into'):
        raise ValueError('请先保留并核对当前玩点。')
    design = point.get('design') or {}
    if not all(isinstance(design.get(k), str) and design[k].strip()
               for k in ('title', 'game_id', 'fit', 'player_goal', 'player_action', 'success', 'failure', 'feedback')):
        raise ValueError('请先选择玩法并补齐核心玩法说明。')
    data = {'video': project['state']['video'], 'point_id': point['id'], 'time_s': point['time_s'],
            'opportunity': point['opportunity'], 'reason': point['reason'], 'design': design,
            'brief': point.get('brief', {}), 'selected': point.get('selected')}
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return project, data, digest


def folder(root):
    """Historical output directory, retained for compatibility only."""
    return Path(root) / 'results' / 'day4'


def write(root, record):
    return game_store.write(root, record)


def read(root, record_id):
    return game_store.read(root, record_id)


def listing(root, project_id, point_id):
    Store(Path(root) / 'projects').read(identifier(project_id))
    items = []
    for path in game_store.candidates(root):
        try:item = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError):continue
        if item.get('project_id') == project_id and item.get('point_id') == point_id:
            items.append({k: item.get(k) for k in ('record_id', 'created_at', 'status', 'error', 'input_hash', 'stage')})
    return sorted(items, key=lambda x: x['created_at'], reverse=True)


def validate_output(data):
    if not isinstance(data, dict) or set(data) != {'design', 'javascript', 'css'}:
        raise ValueError('输出必须为包含 design、javascript、css 的 JSON 对象。')
    design = data['design']
    if not isinstance(design, dict):
        raise ValueError('design 应为对象。')
    for key in DESIGN_FIELDS:
        if not isinstance(design.get(key), str) or not design[key].strip():
            raise ValueError(f'design.{key} 必须为非空字符串。')
    timeout = design.get('timeout_s')
    if type(timeout) not in (int, float) or not 1 <= timeout <= 60:
        raise ValueError('design.timeout_s 应为 1–60 秒的数字。')
    for key, limit in (('javascript', 120000), ('css', 30000)):
        if not isinstance(data[key], str) or len(data[key]) > limit or (key == 'javascript' and not data[key].strip()):
            raise ValueError(f'{key} 内容为空、过大或类型错误。')
    node = resolve_node()
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / 'game.js'
        script.write_text(data['javascript'], encoding='utf-8')
        result = subprocess.run([node, '--check', str(script)], capture_output=True, timeout=15)
        if result.returncode:
            raise ValueError('JS 语法检查失败：' + result.stderr.decode('utf-8', errors='replace')[-3000:])
    return data


def resolve_node():
    configured = settings.get('NODE_BINARY', '').strip()
    node = (shutil.which(configured) or (configured if Path(configured).is_file() else None)) if configured else shutil.which('node')
    if not node:
        raise ValueError('服务进程未找到 Node.js；请设置 NODE_BINARY 为 node.exe 的绝对路径或配置 PATH 后重启。')
    return node


@settings.scoped(lambda root, *a, **kw: (root, None))
def generate(root, payload, opener=None):
    opener = opener or urlopen
    if type(payload.get('revision')) is not int:
        raise ValueError('必须提供当前项目修订 revision。')
    project, data, digest = snapshot(root, payload.get('project_id'), payload.get('point_id'), payload['revision'])
    parent = None
    problem = payload.get('problem', '')
    if payload.get('generation_id'):
        parent = read(root, payload['generation_id'])
        if parent.get('stage') != 'game_generation' or parent['project_id'] != project['project_id'] or parent['point_id'] != data['point_id']:
            raise ValueError('修复记录不属于当前项目玩点。')
        if parent['input_hash'] != digest:
            raise ValueError('玩法已修改，请重新生成，不要修复旧输入。')
        if not isinstance(problem, str) or not 1 <= len(problem.strip()) <= 6000:
            raise ValueError('请填写不超过 6000 字的修复要求。')
    key = settings.get('MINIMAX_API_KEY', '')
    if not key:
        raise ValueError('尚未配置 MINIMAX_API_KEY，请在页面设置中保存密钥。')
    resolve_node()  # Preflight before any paid request, not after generation.
    active_key = (project['project_id'], data['point_id'])
    with LOCK:
        project, data, digest = snapshot(root, payload.get('project_id'), payload.get('point_id'), payload['revision'])
        if active_key in ACTIVE:
            raise Conflict('该玩点正在生成，请勿重复提交。')
        ACTIVE.add(active_key)
    record = {'record_id': secrets.token_hex(16), 'stage': 'game_generation', 'status': 'failed',
              'project_id': project['project_id'], 'project_revision': project['revision'], 'point_id': data['point_id'],
              'input': data, 'input_hash': digest, 'created_at': datetime.now(timezone.utc).isoformat(),
              'parent_record_id': parent['record_id'] if parent else None, 'problem': problem,
              'model': settings.get('MINIMAX_MODEL', 'MiniMax-M3.1-Flash'), 'model_call_made': False}
    try:
        prompt_path = Path(root) / 'prompts/game-generation/v1' / ('repair.txt' if parent else 'generate.txt')
        prompt = prompt_path.read_text(encoding='utf-8')
        record.update(prompt_file=prompt_path.relative_to(root).as_posix(), prompt=prompt)
        request_input = {'single_point': data}
        if parent:
            request_input.update(previous=parent.get('output') or parent.get('raw_text'), problem=problem)
        body = {'model': record['model'], 'messages': [{'role': 'system', 'content': prompt},
                {'role': 'user', 'content': json.dumps(request_input, ensure_ascii=False)}],
                'max_completion_tokens': 16384, 'reasoning_effort': 'medium'}
        record['request'] = body
        base = settings.get('MINIMAX_BASE_URL', 'https://api.minimax.cn/v1').rstrip('/')
        if not base.startswith('https://'):
            raise ValueError('MiniMax 接口必须使用 HTTPS。')
        record['endpoint'] = base + '/chat/completions'
        record['model_call_made'] = True
        with opener(Request(record['endpoint'], data=json.dumps(body).encode(),
                            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}), timeout=300) as response:
            raw = json.load(response)
        record.update(raw_response=raw, usage=raw.get('usage', {}))
        choice = raw['choices'][0]
        record.update(raw_text=choice['message'].get('content', ''), finish_reason=choice.get('finish_reason'))
        if record['finish_reason'] != 'stop':
            raise ValueError('模型未正常结束或输出截断；未自动重试。')
        output = json.loads(record['raw_text'])
        record['output'] = output
        validate_output(output)
        record.update(status='checked', validation={'structure': True, 'syntax': True, 'playtested': False})
        try:
            _, _, current_hash = snapshot(root, project['project_id'], data['point_id'])
            record['stale'] = current_hash != digest
        except ValueError:
            record['stale'] = True
    except Exception as error:
        # Never persist credentials or response headers in failure messages.
        record['error'] = str(error).replace(key, '[REDACTED]')[:4000]
    finally:
        try:
            write(root, record)
        finally:
            with LOCK:
                ACTIVE.discard(active_key)
    return record


def playtest(root, payload):
    original = read(root, payload.get('generation_id'))
    if original.get('stage') != 'game_generation' or original.get('status') != 'checked':
        raise ValueError('只有检查通过的生成记录可以保存试玩。')
    result = payload.get('result')
    if result not in ('success', 'failure', 'timeout', 'error', 'exit'):
        raise ValueError('试玩结果无效。')
    note = payload.get('note', '')
    if not isinstance(note, str) or len(note) > 6000:
        raise ValueError('试玩备注过长或类型错误。')
    record = {'record_id': secrets.token_hex(16), 'stage': 'game_playtest', 'generation_id': original['record_id'],
              'model': original['model'], 'video_name': original['input']['video']['name'],
              'game_id': original['input']['design']['game_id'],
              'project_id': original['project_id'], 'point_id': original['point_id'],
              'created_at': datetime.now(timezone.utc).isoformat(), 'result': result, 'note': note,
              'manual_review': payload.get('manual_review') is True, 'model_call_made': False}
    write(root, record)
    return record


def selections(root, project_id):
    project = Store(Path(root) / 'projects').read(identifier(project_id))
    enabled_ids={p['id'] for p in project['state']['points'] if p['enabled']}
    path = Path(root) / 'projects' / project_id / 'games.json'
    values = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    records = []
    for record_id in values.values():
        try:
            item = read(root, record_id)
            if item.get('project_id') != project_id or item.get('stage') != 'game_generation':continue
            _, _, digest = snapshot(root, project['project_id'], item['point_id'])
            if item['point_id'] in enabled_ids and item['status'] == 'checked' and item['input_hash'] == digest:
                records.append(item)
        except (ValueError, OSError, KeyError, TypeError):
            continue
    return records


def select(root, payload):
    item = read(root, payload.get('generation_id'))
    if item.get('stage') != 'game_generation' or item['status'] != 'checked' or type(payload.get('enabled')) is not bool:
        raise ValueError('只能启用或停用已通过检查的游戏。')
    with LOCK:
        _, _, digest = snapshot(root, item['project_id'], item['point_id'])
        if payload['enabled'] and digest != item['input_hash']:
            raise ValueError('玩法已变化，旧游戏不能启用，请重新生成。')
        path = Path(root) / 'projects' / item['project_id'] / 'games.json'
        values = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if not isinstance(values, dict):raise ValueError('启用索引损坏，未覆盖；请先备份检查。')
        if payload['enabled']:
            if values.get(item['point_id']) == item['record_id']:
                return {'enabled': True, 'generation_id': item['record_id'], 'unchanged': True}
            values[item['point_id']] = item['record_id']
        else:
            if values.get(item['point_id']) not in (None, item['record_id']):
                raise Conflict('当前点已采用其他版本，请刷新游戏库；未停用新版本。')
            values.pop(item['point_id'], None)
        history = path.parent / 'games-revisions'
        history.mkdir(exist_ok=True)
        saved = {'created_at': datetime.now(timezone.utc).isoformat(), 'selections': values}
        stamp = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S_%f')
        (history / (stamp+'_采用版本.json')).write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding='utf-8')
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(values), encoding='utf-8')
        os.replace(temp, path)
    return {'enabled': payload['enabled'], 'generation_id': item['record_id']}
