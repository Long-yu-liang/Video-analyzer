"""Private local settings and immutable per-request configuration snapshots."""
import contextvars
import copy
import functools
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from urllib.parse import urlsplit

DEFAULTS = {
    'BEEROUTE_API_KEY': '', 'BEEROUTE_BASE_URL': 'https://beeroute.ai/v1',
    'BEEROUTE_MODELS': 'qwen3-vl-plus,qwen3.8-flash,gemini-2.5-flash-lite,qwen3.8-omni-flash',
    'MINIMAX_API_KEY': '', 'MINIMAX_BASE_URL': 'https://api.minimax.cn/v1',
    'MINIMAX_MODEL': 'MiniMax-M3.1-Flash',
    'MINIMAX_REASONING_EFFORT': 'medium',
    'MINIMAX_MAX_COMPLETION_TOKENS': '32768',
    'FFMPEG_BINARY': '', 'FFPROBE_BINARY': '', 'NODE_BINARY': '',
    'DASHSCOPE_API_KEY': '', 'DASHSCOPE_BASE_URL': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    'GEMINI_API_KEY': '',
}
SECRETS = {key for key in DEFAULTS if key.endswith('_API_KEY')}
LOCK = threading.RLock()
CURRENT = contextvars.ContextVar('video_analyzer_settings', default=None)


class SettingsConflict(ValueError):
    pass


def validate(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise ValueError('配置包含未知字段。')
    for key, value in values.items():
        if not isinstance(value, str) or len(value) > 16000 or any(c in value for c in '\r\n\x00'):
            raise ValueError(f'{key} 应为单行文本。')
        if key.endswith('_BASE_URL'):
            parsed = urlsplit(value)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError(f'{key} 必须是无凭据、查询参数的 HTTPS 地址。')
        if key in ('BEEROUTE_MODELS', 'MINIMAX_MODEL') and not value.strip():
            raise ValueError(f'{key} 不能为空。')
        if key == 'MINIMAX_REASONING_EFFORT' and value not in ('low', 'medium', 'high', 'xhigh', 'max'):
            raise ValueError('MiniMax 思考深度须为 low、medium、high、xhigh 或 max。')
        if key == 'MINIMAX_MAX_COMPLETION_TOKENS' and (not value.isdecimal() or not 1024 <= int(value) <= 131072):
            raise ValueError('游戏生成输出上限须为 1024–131072 之间的整数。')


class Store:
    def __init__(self, root):
        self.path = Path(root) / 'config' / 'settings.json'

    def read(self):
        if not self.path.exists():
            return {'schema_version': 1, 'revision': 0,
                    'values': {k: os.environ.get(k, v) for k, v in DEFAULTS.items()}}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if (not isinstance(data, dict) or data.get('schema_version') != 1
                    or type(data.get('revision')) is not int or data['revision'] < 1):
                raise ValueError()
            validate(data.get('values'))
            data['values'] = {**DEFAULTS, **data['values']}
            return data
        except (ValueError, TypeError) as error:
            raise ValueError('本地配置损坏或格式无效；请备份并检查 config/settings.json，未覆盖原文件。') from error

    def public(self):
        data = self.read()
        values = data['values']
        return {'revision': data['revision'], 'values': {k: v for k, v in values.items() if k not in SECRETS},
                'configured': {k: bool(values[k]) for k in SECRETS}, 'storage': 'config/settings.json'}

    def save(self, payload):
        with LOCK:
            current = self.read()
            if type(payload.get('revision')) is not int or payload['revision'] != current['revision']:
                raise SettingsConflict('配置已在其他窗口更新，请重新读取。')
            changes = payload.get('values', {})
            validate(changes)
            clears = payload.get('clear_keys', [])
            if not isinstance(clears, list) or any(k not in SECRETS for k in clears):
                raise ValueError('无效的密钥清除字段。')
            values = dict(current['values'])
            values.update({k: v.strip() for k, v in changes.items() if k not in SECRETS or v.strip()})
            for key in clears:
                values[key] = ''
            validate(values)
            result = {'schema_version': 1, 'revision': current['revision'] + 1, 'values': values}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix='.settings-')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump(result, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return self.public()

    def export(self, include_secrets=False):
        values = self.read()['values']
        return {'app': 'video-analyzer', 'schema_version': 1,
                'values': {k: v for k, v in values.items() if include_secrets or k not in SECRETS}}


def get(key, default=None):
    active = CURRENT.get()
    return active.get(key, default) if active is not None else os.environ.get(key, default)


@contextmanager
def scope(root, models=None):
    if CURRENT.get() is not None:
        yield
        return
    store = Store(root)
    data = store.read()
    values = copy.deepcopy(data['values'])
    values['_models'] = ({name.strip(): 'beeroute' for name in values['BEEROUTE_MODELS'].split(',') if name.strip()}
                         if data['revision'] or models is None else dict(models))
    token = CURRENT.set(values)
    try:
        yield
    finally:
        CURRENT.reset(token)


def scoped(root_getter):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            root, models = root_getter(*args, **kwargs)
            with scope(root, models):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def tool(name):
    if CURRENT.get() is None:
        from .paths import ROOT
        value = Store(ROOT).read()['values'].get(name.upper() + '_BINARY', '')
    else:
        value = get(name.upper() + '_BINARY', '')
    return value.strip() or name


def check_tools(root):
    result = {}
    with scope(root):
        for name in ('ffmpeg', 'ffprobe', 'node'):
            executable = tool(name)
            try:
                process = subprocess.run([executable, '--version' if name == 'node' else '-version'],
                                         capture_output=True, timeout=5)
                message = (process.stdout or process.stderr).decode('utf-8', errors='replace').splitlines()
                result[name] = {'ok': process.returncode == 0, 'path': executable,
                                'message': message[0][:300] if message else '没有版本信息'}
            except (OSError, subprocess.TimeoutExpired):
                result[name] = {'ok': False, 'path': executable, 'message': '无法启动或检查超时，请核对可执行文件路径。'}
    return result
