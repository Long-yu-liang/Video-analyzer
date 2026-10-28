"""Private local settings and immutable per-request configuration snapshots."""
import contextvars
import copy
import functools
import json
import ipaddress
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
    'ANALYSIS_MAX_TOKENS': '16384', 'ANALYSIS_THINKING': 'disabled',
    'ANALYSIS_THINKING_PARAMETER': 'top_level', 'GAMEPLAY_BATCH_SIZE': '5',
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


def api_base_url(value, label='模型 API 地址'):
    """Allow unencrypted HTTP only for explicit LAN or loopback destinations."""
    message=f'{label} 须为 HTTPS 地址，或内网 IP／localhost 的 HTTP 地址；不能包含账号、密码、查询参数或片段。'
    if not isinstance(value,str) or any(c.isspace() for c in value) or '\\' in value:
        raise ValueError(message)
    try:
        parsed=urlsplit(value)
        host=parsed.hostname
        port=parsed.port
        if not host or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError(message)
        if port is not None and not 1<=port<=65535:raise ValueError(message)
        if parsed.scheme=='https':return value.rstrip('/')
        local=host.lower().rstrip('.')=='localhost'
        try:
            address=ipaddress.ip_address(host)
            if isinstance(address,ipaddress.IPv6Address) and address.ipv4_mapped:address=address.ipv4_mapped
            networks=('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','127.0.0.0/8') if address.version==4 else ('::1/128','fc00::/7')
            local=any(address in ipaddress.ip_network(network) for network in networks)
        except ValueError:pass
        if parsed.scheme=='http' and local:return value.rstrip('/')
    except (ValueError,TypeError):
        raise ValueError(message) from None
    raise ValueError(message)


def validate(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise ValueError('配置包含未知字段。')
    for key, value in values.items():
        if not isinstance(value, str) or len(value) > 16000 or any(c in value for c in '\r\n\x00'):
            raise ValueError(f'{key} 应为单行文本。')
        if key.endswith('_BASE_URL'):
            api_base_url(value,key)
        if key in ('BEEROUTE_MODELS', 'MINIMAX_MODEL') and not value.strip():
            raise ValueError(f'{key} 不能为空。')
        if key == 'ANALYSIS_MAX_TOKENS' and (not value.isdecimal() or not 1024 <= int(value) <= 131072):
            raise ValueError('分析输出上限须为 1024–131072 之间的整数。')
        if key == 'GAMEPLAY_BATCH_SIZE' and (not value.isdecimal() or not 1 <= int(value) <= 5):
            raise ValueError('玩法推荐每批点数须为 1–5 之间的整数。')
        if key == 'ANALYSIS_THINKING_PARAMETER' and value not in ('top_level', 'chat_template'):
            raise ValueError('思考参数位置须为 top_level 或 chat_template。')
        if key == 'ANALYSIS_THINKING' and value not in ('disabled', 'enabled', 'default'):
            raise ValueError('分析思考设置须为 disabled、enabled 或 default。')
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


def analysis_options(model):
    """Keep the submitted model name intact; family matching is case insensitive."""
    options = {'max_tokens': int(get('ANALYSIS_MAX_TOKENS', DEFAULTS['ANALYSIS_MAX_TOKENS']))}
    thinking = get('ANALYSIS_THINKING', DEFAULTS['ANALYSIS_THINKING'])
    if str(model).lower().startswith('qwen') and thinking != 'default':
        if get('ANALYSIS_THINKING_PARAMETER', 'top_level') == 'chat_template':
            options['chat_template_kwargs'] = {'enable_thinking': thinking == 'enabled'}
        else:
            options['enable_thinking'] = thinking == 'enabled'
    return options


def requested_thinking(body):
    template = body.get('chat_template_kwargs')
    if isinstance(template, dict) and 'enable_thinking' in template:
        return template['enable_thinking']
    return body.get('enable_thinking')


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
