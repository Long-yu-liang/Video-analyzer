"""Persistent background generation jobs. Never automatically retry a paid call."""
import contextvars
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
from . import generation
from .. import settings

LOCK = threading.RLock()
OWNER = secrets.token_hex(16)
ACTIVE = {}
RUNNING = {'queued', 'waiting_model', 'checking', 'saving'}


def now():
    return datetime.now(timezone.utc).isoformat()


def folder(root, project_id):
    generation.Store(Path(root)/'projects').read(generation.identifier(project_id))
    return Path(root)/'projects'/project_id/'game-tasks'


def write(path, task):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.task-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(task, stream, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def listing(root, project_id):
    directory = folder(root, project_id)
    result = []
    with LOCK:
        for path in directory.glob('*.json'):
            task = json.loads(path.read_text(encoding='utf-8'))
            if task['status'] in RUNNING and task.get('owner') != OWNER:
                # A crashed server may have completed the record write before the job write.
                try:
                    record = generation.read(root, task.get('generation_id'))
                except (ValueError, OSError):
                    record = None
                task.update(status=('completed' if record['status']=='checked' else 'failed') if record else 'interrupted',
                            error=record.get('error', '') if record else '服务已重启，任务状态中断。上游调用可能已发生；请先检查游戏库，不会自动重试。',
                            finished_at=now())
                write(path, task)
            result.append({k:v for k,v in task.items() if k != 'owner'})
    return sorted(result, key=lambda item:item['created_at'], reverse=True)


def start(root, payload, runner=None):
    runner = runner or generation.generate
    root = Path(root)
    with settings.scope(root):
        project, data, digest = generation.snapshot(root, payload.get('project_id'), payload.get('point_id'), payload.get('revision'))
        if type(payload.get('revision')) is not int:
            raise ValueError('必须提供项目修订。')
        if not settings.get('MINIMAX_API_KEY'):
            raise ValueError('请先在设置中配置 MiniMax 密钥。')
        generation.resolve_node()
        if payload.get('generation_id'):
            parent = generation.read(root, payload['generation_id'])
            if parent['project_id'] != project['project_id'] or parent['point_id'] != data['point_id'] or parent['input_hash'] != digest:
                raise ValueError('修复来源与当前方案不匹配。')
            if not isinstance(payload.get('problem'), str) or not 1 <= len(payload['problem'].strip()) <= 6000:
                raise ValueError('请填写不超过6000字的修复要求。')
        context = contextvars.copy_context()
        key = (str(root.resolve()), project['project_id'], data['point_id'])
        with LOCK, generation.LOCK:
            if key in ACTIVE or (project['project_id'], data['point_id']) in generation.ACTIVE:
                raise generation.Conflict('该点已有生成任务，请查看任务卡片，不要重复提交。')
            task = {'task_id':secrets.token_hex(16), 'project_id':project['project_id'], 'point_id':data['point_id'],
                    'title':data['design']['title'], 'status':'queued', 'created_at':now(), 'updated_at':now(),
                    'owner':OWNER, 'kind':'repair' if payload.get('generation_id') else 'generate', 'input_hash':digest}
            path = folder(root, project['project_id'])/(task['task_id']+'.json')
            write(path, task)
            ACTIVE[key] = task['task_id']
            generation.ACTIVE.add((project['project_id'],data['point_id']))
        def update(status, record_id):
            with LOCK:
                task.update(status=status, generation_id=record_id, updated_at=now())
                try:
                    write(path, task)
                except OSError:
                    pass  # A progress-write failure must not skip saving the actual model record.
        def work():
            try:
                record = runner(root, dict(payload), progress=update, _reserved=True)
                with LOCK:
                    task.update(status='completed' if record['status']=='checked' else 'failed',
                                generation_id=record['record_id'], error=record.get('error',''), finished_at=now(), updated_at=now())
                    write(path, task)
            except Exception:
                with LOCK:
                    task.update(status='failed', error='任务未正常完成，请检查游戏库及服务日志；不会自动重试。', finished_at=now())
                    write(path, task)
            finally:
                with LOCK:
                    ACTIVE.pop(key, None)
                with generation.LOCK:
                    generation.ACTIVE.discard((project['project_id'],data['point_id']))
        try:
            threading.Thread(target=lambda:context.run(work), daemon=True).start()
        except RuntimeError:
            with LOCK, generation.LOCK:
                ACTIVE.pop(key, None)
                generation.ACTIVE.discard((project['project_id'],data['point_id']))
                task.update(status='failed', error='本地任务线程未能启动，未调用模型。', finished_at=now())
                write(path, task)
            raise ValueError('本地任务线程未能启动，未调用模型。')
        return {k:v for k,v in task.items() if k != 'owner'}
