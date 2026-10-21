"""Local video analysis workbench. Python 3.10+, FFmpeg. No mock inference."""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

DEMO = Path(__file__).resolve().parent
ROOT = DEMO.parent
MAX_VIDEO = 12 * 1024 * 1024


def load_env(path):
    """读取项目根目录 .env；已存在的真实环境变量优先，不被文件覆盖。"""
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env(ROOT / '.env')


def configured_models():
    """BEEROUTE_MODELS 用英文逗号分隔。页面上选择其中一个，每次只调用它。"""
    return [m.strip() for m in os.environ.get('BEEROUTE_MODELS', '').split(',') if m.strip()]


MODEL_NAMES = configured_models()
MODELS = {name: 'beeroute' for name in MODEL_NAMES}
PROVIDER_KEYS = {'qwen': 'DASHSCOPE_API_KEY', 'gemini': 'GEMINI_API_KEY', 'beeroute': 'BEEROUTE_API_KEY'}
TOKEN = secrets.token_urlsafe(24)


def prompt_paths():
    folder = ROOT / 'prompts'
    return sorted(((int(p.stem[1:]), p) for p in folder.glob('v*.txt')
                   if re.fullmatch(r'v[1-9]\d*', p.stem)), key=lambda item: item[0])


def load_prompt(version=None):
    """默认使用最高版本；也允许显式选择一个已存在的版本，不改动旧文件。"""
    versioned = prompt_paths()
    if not versioned:
        raise ValueError('prompts/ 下没有版本提示词，例如 prompts/v1.txt。')
    if version is None:
        path = versioned[-1][1]
    else:
        if not isinstance(version, str) or not re.fullmatch(r'v[1-9]\d*', version):
            raise ValueError('提示词版本格式无效。')
        matching = [p for _, p in versioned if p.stem == version]
        if not matching:
            raise ValueError('提示词版本不存在。')
        path = matching[0]
    text = path.read_text(encoding='utf-8').strip()
    if not text:
        raise ValueError(f'{path.name} 是空文件。')
    return path, text


def run(args):
    try:
        return subprocess.run(args, check=True, capture_output=True, timeout=90).stdout
    except FileNotFoundError:
        raise ValueError('请安装 FFmpeg，并将 ffmpeg / ffprobe 加入 PATH。')
    except subprocess.CalledProcessError:
        raise ValueError('视频无法解码；请使用可播放的 MP4 文件。')


def probe(path):
    info = json.loads(run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]))
    video = next((x for x in info['streams'] if x['codec_type'] == 'video'), None)
    if not video:
        raise ValueError('文件不包含视频画面。')
    duration = float(info['format']['duration'])
    if not math.isfinite(duration) or not 0 < duration <= 60:
        raise ValueError('本 Demo 仅接受 0–60 秒视频；请先裁剪。')
    return {'duration_s': duration, 'width': video['width'], 'height': video['height'],
            'has_audio': any(x['codec_type'] == 'audio' for x in info['streams'])}


def extract(path, directory, duration, fps):
    # fps filter resets its output timebase; use actual source PTS via showinfo after selection.
    vf = f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{1/fps:.9f})',scale=768:768:force_original_aspect_ratio=decrease,showinfo"
    command = ['ffmpeg', '-hide_banner', '-i', str(path), '-an', '-vf', vf,
               '-fps_mode', 'vfr', '-frames:v', '120', '-q:v', '4', str(directory / '%03d.jpg')]
    try:
        proc = subprocess.run(command, check=True, capture_output=True, timeout=90)
    except subprocess.CalledProcessError:
        raise ValueError('抽帧失败。')
    pts = [float(x) for x in re.findall(r'pts_time:([\d.eE+-]+)', proc.stderr.decode(errors='replace'))]
    images = sorted(directory.glob('*.jpg'))
    if not images or len(pts) < len(images):
        raise ValueError('无法提取带时间戳的画面。')
    origin = pts[0]
    return [{'time_s': round(t-origin, 6), 'data': base64.b64encode(p.read_bytes()).decode()}
            for p, t in zip(images, pts)]


def strip_audio(path, output):
    """Remux a visual-only request copy without changing the source file."""
    try:
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0',
                        '-c:v', 'copy', '-an', '-sn', '-dn', '-movflags', '+faststart',
                        str(output)], check=True, capture_output=True, timeout=90)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise ValueError('无法移除视频音轨；未调用模型。请检查 FFmpeg 或视频文件。')
    return output


def extract_json(text):
    text = (text or '').strip()
    fenced = re.search(r'```(?:json)?\s*(.*)```', text, re.S | re.I)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find('{'), text.rfind('}')
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return text


def list_samples():
    folder = ROOT / 'mp4-example'
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.glob('*.mp4') if p.is_file())


def read_sample(name):
    if not name or Path(name).name != name or not name.lower().endswith('.mp4'):
        raise ValueError('只能选择 mp4-example 中的 MP4 文件。')
    folder = (ROOT / 'mp4-example').resolve()
    path = (folder / name).resolve()
    if path.parent != folder or not path.is_file():
        raise ValueError('样例视频不存在。')
    data = path.read_bytes()
    if not data or len(data) > MAX_VIDEO:
        raise ValueError('视频须为非空 MP4，大小不超过 12 MiB。')
    return data


def safe_stem(name):
    stem = Path(name).stem.strip()
    stem = re.sub(r'[\\/:*?"<>|\s]+', '_', stem)
    return stem[:80] or 'video'


def supports_video_audio(model):
    """Only these configured model families can analyze an MP4's audio track."""
    return model.startswith('gemini-') or model == 'qwen3.8-omni-flash'


def build_request(model, mode, fps, data, frames, metadata, prompt):
    provider = MODELS[model]
    audio = mode == 'direct' and metadata['has_audio'] and supports_video_audio(model)
    prompt = prompt + f"\n视频时长 {metadata['duration_s']:.6f} 秒。输入模式 {mode}。可用音频：{'可能有' if audio else '无'}。"
    if audio:
        prompt += '\n视频文件含音轨，当前模型支持分析音频。只报告确实可辨认的声音；静音、噪声或无法听清时不要编造台词，也不必生成音频事件。'
    elif mode == 'frames':
        prompt += '\n提供的是带原始时间戳的顺序采样画面，没有声音；不是完整视频，不能确认采样间发生的短动作。'
    elif metadata['has_audio']:
        prompt += '\n视频文件虽含音轨，但当前模型只分析画面。不得根据口型、姿态或场景推断台词、音效或语气。'
    else:
        prompt += '\n本次视频没有音轨，不可编造对话或声音。'
    if provider == 'qwen':
        content = [{'type': 'text', 'text': prompt}]
        if mode == 'direct':
            content.append({'type': 'video_url', 'video_url': {'url': 'data:video/mp4;base64,' + data}, 'fps': fps})
        else:
            for frame in frames:
                content += [{'type': 'text', 'text': f"画面时间 {frame['time_s']:.6f} 秒"},
                            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + frame['data']}}]
        body = {'model': model, 'messages': [{'role': 'user', 'content': content}],
                'response_format': {'type': 'json_object'}, 'max_tokens': 4096, 'enable_thinking': False}
        base = os.environ.get('DASHSCOPE_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1').rstrip('/')
        if not base.startswith('https://'):
            raise ValueError('模型 API 地址必须使用 HTTPS。')
        return base + '/chat/completions', body, audio
    if provider == 'beeroute':
        content = [{'type': 'text', 'text': prompt}]
        if mode == 'direct':
            video = 'data:video/mp4;base64,' + data
            if model.startswith('gemini'):
                # This gateway drops video_url for Gemini, so the model only sees the prompt.
                content.append({'type': 'image_url', 'image_url': {'url': video, 'mime_type': 'video/mp4'}})
            else:
                content.append({'type': 'video_url', 'video_url': {'url': video}, 'fps': fps})
        else:
            for frame in frames:
                content += [{'type': 'text', 'text': f"画面时间 {frame['time_s']:.6f} 秒"},
                            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + frame['data']}}]
        body = {'model': model, 'messages': [{'role': 'user', 'content': content}], 'max_tokens': 4096}
        if model.startswith('qwen'):
            body['enable_thinking'] = False
        if model == 'qwen3.8-omni-flash' and mode == 'direct':
            body['response_format'] = {'type': 'json_object'}
        base = os.environ.get('BEEROUTE_BASE_URL', 'https://beeroute.ai/v1').rstrip('/')
        if not base.startswith('https://'):
            raise ValueError('模型 API 地址必须使用 HTTPS。')
        return base + '/chat/completions', body, audio
    parts = [{'text': prompt}]
    if mode == 'direct':
        parts.append({'inline_data': {'mime_type': 'video/mp4', 'data': data}, 'video_metadata': {'fps': fps}})
    else:
        for frame in frames:
            parts += [{'text': f"画面时间 {frame['time_s']:.6f} 秒"},
                      {'inline_data': {'mime_type': 'image/jpeg', 'data': frame['data']}}]
    return (f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
            {'contents': [{'role': 'user', 'parts': parts}],
             'generationConfig': {'responseMimeType': 'application/json', 'maxOutputTokens': 4096}}, audio)


def modality_token_counts(usage):
    """Return reported modality counts, or None when no modality breakdown exists."""
    if not isinstance(usage, dict):
        return None
    counts = {'image': 0, 'video': 0, 'audio': 0}
    reported = False
    details = usage.get('prompt_tokens_details') or {}
    if isinstance(details, dict):
        reported = any(key in details for key in ('text_tokens', 'image_tokens', 'video_tokens', 'audio_tokens'))
        for modality in counts:
            counts[modality] = int(details.get(modality + '_tokens') or 0)
    meta = ((usage.get('billing_usage') or {}).get('gemini_usage_metadata') or {})
    parts = meta.get('promptTokensDetails') or []
    if isinstance(parts, list):
        reported = reported or bool(parts)
        for item in parts:
            if isinstance(item, dict):
                modality = str(item.get('modality', '')).lower()
                if modality in counts:
                    counts[modality] = max(counts[modality], int(item.get('tokenCount') or 0))
    return counts if reported else None


def media_token_count(usage):
    counts = modality_token_counts(usage)
    return sum(counts.values()) if counts is not None else None


def validate(text, duration, audio, require_interaction_points=False, require_point_source=False,
             max_points=2, require_action_observed=False):
    result = json.loads(text)
    if not isinstance(result, dict) or not isinstance(result.get('summary'), str):
        raise ValueError('模型输出缺少 summary。')
    if not isinstance(result.get('events'), list) or not isinstance(result.get('limitations'), list):
        raise ValueError('模型输出 events / limitations 格式不正确。')
    if not all(isinstance(x, str) for x in result['limitations']):
        raise ValueError('limitations 必须为字符串数组。')
    missing_uncertainty = False
    adjusted = []
    for event in result['events']:
        if not isinstance(event, dict):
            raise ValueError('事件格式错误。')
        if 'uncertainty' not in event or event.get('uncertainty') is None:
            event['uncertainty'] = '模型未说明'
            missing_uncertainty = True
        for key in ('start_s', 'end_s'):
            n = event.get(key)
            # Models often round the last moment up to the next whole second.
            if type(n) not in (int, float) or not math.isfinite(n) or n < 0 or n > duration + 1:
                raise ValueError('模型返回了越界或无效时间戳，请保留原始结果并人工复核。')
            if n > duration:
                event[key] = duration
                adjusted.append(f'{key} {n:g}→{duration:g}')
        if event['end_s'] < event['start_s']:
            raise ValueError('事件结束时间早于开始时间。')
        for key in ('description', 'evidence', 'source', 'uncertainty'):
            if not isinstance(event.get(key), str):
                raise ValueError('事件字段缺失或格式错误：' + key)
        if not event['evidence'].strip() or event['source'] not in ('visual', 'audio', 'both'):
            raise ValueError('模型未提供有效事件依据。')
        if not audio and event['source'] != 'visual':
            raise ValueError('未确认模型收到音频，却生成了音频依据，结果未通过校验。')
    if missing_uncertainty:
        result['limitations'].append('有事件未返回 uncertainty，本地已标为「模型未说明」。')
    if adjusted:
        result['limitations'].append('有事件时间略超出视频时长，已收到片尾：' + '；'.join(adjusted) + '。')
    result['events'].sort(key=lambda x: x['start_s'])
    points = result.get('interaction_points')
    if points is None and not require_interaction_points:
        points = []
    if not isinstance(points, list) or len(points) > max_points:
        raise ValueError(f'interaction_points 必须是最多包含{max_points}个候选点的数组。')
    for point in points:
        if not isinstance(point, dict):
            raise ValueError('候选交互点格式错误。')
        source = point.get('source')
        if require_point_source and source not in ('visual', 'both'):
            raise ValueError('候选交互点 source 必须是 visual 或 both。')
        if source is not None and source not in ('visual', 'both'):
            raise ValueError('候选交互点 source 无效。')
        if source == 'both' and not audio:
            raise ValueError('未确认模型收到音频，却生成了声画共同依据的候选点。')
        time_s = point.get('time_s')
        if type(time_s) not in (int, float) or not math.isfinite(time_s) or not 0 <= time_s <= duration:
            raise ValueError('候选交互点时间戳无效或超出视频时长。')
        if require_action_observed and 'action_observed' not in point:
            raise ValueError('v9 候选交互点缺少 action_observed。')
        observed = point.get('action_observed', True)
        if type(observed) is not bool:
            raise ValueError('action_observed 必须是布尔值。')
        action_start = point.get('action_start_s')
        if observed:
            if (type(action_start) not in (int, float) or not math.isfinite(action_start)
                    or not 0 <= action_start <= duration):
                raise ValueError('候选交互点动作时间戳无效或超出视频时长。')
            if time_s >= action_start:
                raise ValueError('候选交互点必须早于相关动作开始。')
        elif not require_action_observed or 'action_start_s' not in point or action_start is not None:
            raise ValueError('未拍到后续行动的 v9 候选点必须使用 action_start_s: null。')
        for key in ('visible_cue', 'player_action', 'reason', 'evidence', 'uncertainty'):
            if not isinstance(point.get(key), str) or not point[key].strip():
                raise ValueError('候选交互点字段缺失或为空：' + key)
        if not observed and any('后续行动未拍到' not in point[key] for key in ('evidence', 'uncertainty')):
            raise ValueError('未拍到后续行动时，evidence 和 uncertainty 必须明确说明「后续行动未拍到」。')
    result['interaction_points'] = sorted(points, key=lambda item: item['time_s'])
    return result


def validate_open_interactions(text, duration):
    """v12: validate the clickable structure without judging which moments are interactive."""
    result = json.loads(text)
    if not isinstance(result, dict) or not isinstance(result.get('summary'), str):
        raise ValueError('模型输出缺少 summary。')
    events, points = result.get('events'), result.get('interaction_points')
    if not isinstance(events, list) or not isinstance(points, list):
        raise ValueError('模型输出 events / interaction_points 格式不正确。')

    def in_video(value):
        return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= duration

    for event in events:
        if (not isinstance(event, dict) or not in_video(event.get('start_s'))
                or not in_video(event.get('end_s')) or event['end_s'] < event['start_s']
                or not isinstance(event.get('description'), str) or not event['description'].strip()):
            raise ValueError('事件需要片内起止时间和描述。')
    for point in points:
        if (not isinstance(point, dict) or not in_video(point.get('time_s'))
                or not isinstance(point.get('opportunity'), str) or not point['opportunity'].strip()
                or not isinstance(point.get('reason'), str) or not point['reason'].strip()):
            raise ValueError('交互点需要片内时间、机会描述和理由。')
    limitations = result.get('limitations', [])
    if not isinstance(limitations, list) or not all(isinstance(x, str) for x in limitations):
        raise ValueError('limitations 必须为字符串数组。')
    result['limitations'] = limitations
    result['events'] = sorted(events, key=lambda item: item['start_s'])
    result['interaction_points'] = sorted(points, key=lambda item: item['time_s'])
    return result


def save_record(record):
    model = record['model']
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', model):
        raise ValueError('模型名称不能作为结果文件夹。')
    folder = ROOT / 'results' / model
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%dT%H%M%S%f')
    suffix = record.get('record_id') or secrets.token_hex(4)
    filename = f"{safe_stem(record.get('video_name') or 'video')}__{record['mode']}__{stamp}__{suffix}.json"
    path = folder / filename
    with path.open('x', encoding='utf-8') as output:
        json.dump(record, output, ensure_ascii=False, indent=2)
    record['record_file'] = f'results/{model}/{filename}'
    return path


def stage_prompt_paths(stage):
    if stage not in ('events', 'opportunities', 'gameplay'):
        raise ValueError('无效的阶段提示词。')
    paths = [path for path in (ROOT / 'prompts').glob(f'v*/{stage}.txt')
             if path.is_file() and re.fullmatch(r'v[1-9]\d*', path.parent.name)]
    return sorted(paths, key=lambda path: int(path.parent.name[1:]))


def load_stage_prompt(stage, version=None):
    paths = stage_prompt_paths(stage)
    if not paths:
        raise ValueError(f'没有{stage}提示词。')
    if version is None:
        path = paths[-1]
    else:
        if not isinstance(version, str) or not re.fullmatch(r'v[1-9]\d*', version):
            raise ValueError('提示词版本格式无效。')
        matching = [item for item in paths if item.parent.name == version]
        if not matching:
            raise ValueError('提示词版本不存在。')
        path = matching[0]
    prompt = path.read_text(encoding='utf-8').strip()
    if not prompt:
        raise ValueError(f'{path.name} 是空文件。')
    return path, prompt


def stage_prompt_catalog():
    catalog = {}
    for stage in ('events', 'opportunities', 'gameplay'):
        catalog[stage] = [{'version': path.parent.name,
                           'path': f'prompts/{path.parent.name}/{path.name}',
                           'content': path.read_text(encoding='utf-8').strip()}
                          for path in stage_prompt_paths(stage)]
    return catalog


class TruncatedOutputError(ValueError):
    """The model reached its output cap; keep its raw response and retry smaller media."""


def validate_stage_events(text, duration):
    result = json.loads(text)
    if (not isinstance(result, dict) or not isinstance(result.get('summary'), str)
            or not isinstance(result.get('events'), list)
            or not isinstance(result.get('limitations'), list)
            or not all(isinstance(x, str) for x in result['limitations'])
            or 'interaction_points' in result):
        raise ValueError('事件阶段必须只返回 summary、events 和 limitations。')
    events = []
    for event in result['events']:
        if not isinstance(event, dict):
            raise ValueError('事件格式错误。')
        for key in ('start_s', 'end_s', 'observation_s'):
            value = event.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= duration + 1:
                raise ValueError('事件时间或代表画面时间无效。')
            if value > duration:
                event[key] = duration
                result['limitations'].append(f'{key} {value:g} 秒略超片长，已收到片尾。')
        if not event['start_s'] <= event['observation_s'] <= event['end_s']:
            raise ValueError('代表画面时间必须在事件区间内。')
        for key in ('description', 'evidence', 'uncertainty'):
            if not isinstance(event.get(key), str) or not event[key].strip():
                raise ValueError('事件缺少具体描述、画面依据或不确定性。')
        events.append(event)
    events.sort(key=lambda item: (item['observation_s'], item['start_s']))
    for index, event in enumerate(events, 1):
        event['observation_id'] = f'e{index}'
    result['events'] = events
    return result


def validate_stage_opportunities(text, observations):
    result = json.loads(text)
    if not isinstance(result, dict) or not isinstance(result.get('interaction_points'), list):
        raise ValueError('机会阶段必须返回 interaction_points 数组。')
    by_id = {item['observation_id']: item for item in observations}
    points = []
    seen = set()
    for point in result['interaction_points']:
        if not isinstance(point, dict) or point.get('observation_id') not in by_id:
            raise ValueError('机会引用了未审核的画面 ID。')
        for key in ('opportunity', 'reason'):
            if not isinstance(point.get(key), str) or not point[key].strip():
                raise ValueError('机会缺少参与行为或理由。')
        identity = (point['observation_id'], point['opportunity'].strip())
        if identity in seen:
            continue
        seen.add(identity)
        source = by_id[point['observation_id']]
        points.append({'observation_id': point['observation_id'], 'event_id': source['observation_id'],
                       'time_s': source['frame_time_s'], 'opportunity': point['opportunity'].strip(),
                       'reason': point['reason'].strip()})
    points.sort(key=lambda item: (item['time_s'], item['observation_id']))
    return {'interaction_points': points, 'limitations': []}


def call_v13_model(url, body, key, record):
    payload = json.dumps(body).encode()
    if len(payload) > 19 * 1024 * 1024:
        raise ValueError('编码后请求过大，请减少审核画面或压缩视频。')
    record['request_bytes'] = len(payload)
    record['request_options'] = {'fps': record['fps'], 'response_format': body.get('response_format')}
    record['api_attempts'] = [{'response_format': body.get('response_format')}]
    started = time.perf_counter()
    with urlopen(Request(url, data=payload, headers={'Content-Type': 'application/json',
                                                    'Authorization': 'Bearer ' + key}), timeout=300) as response:
        result = json.load(response)
    record['api_roundtrip_s'] = round(time.perf_counter() - started, 3)
    record['raw_response'] = result
    choice = result['choices'][0]
    content = choice['message'].get('content')
    output = ''.join(p.get('text', '') for p in content if isinstance(p, dict)) if isinstance(content, list) else (content or '')
    record['raw_text'] = output
    record['usage'] = result.get('usage', {})
    record['finish_reason'] = choice.get('finish_reason')
    modalities = modality_token_counts(record['usage'])
    record['media_tokens'] = sum(modalities.values()) if modalities is not None else None
    record['video_tokens'] = modalities['image'] + modalities['video'] if modalities is not None else None
    record['audio_tokens'] = modalities['audio'] if modalities is not None else None
    record['audio_verification'] = 'not_sent'
    record['media_verification'] = 'confirmed' if record['video_tokens'] else 'unreported'
    if modalities is not None and record['video_tokens'] == 0:
        raise ValueError('用量中没有图像或视频 Token，不能确认模型看到了画面。')
    if record['finish_reason'] == 'length':
        raise TruncatedOutputError('模型输出达到 4096 token 上限并被截断，原始响应已保留。')
    if record['finish_reason'] not in ('stop', 'STOP'):
        raise ValueError('模型未正常结束，原始输出已保留。')
    return extract_json(output)


def saved_record_paths(record_id, folder=None):
    if not re.fullmatch(r'[0-9a-f]{32}', record_id or ''):
        raise ValueError('记录 ID 无效。')
    folder = Path(folder) if folder else ROOT / 'results'
    return sorted(path for path in folder.rglob(f'*__{record_id}.json') if path.is_file())


def stage_record(record_id):
    if not re.fullmatch(r'[0-9a-f]{32}', record_id or ''):
        raise ValueError('事件记录 ID 无效。')
    matches = saved_record_paths(record_id)
    if len(matches) != 1:
        raise ValueError('事件记录不存在。' if not matches else '记录 ID 对应多个文件。')
    record = json.loads(matches[0].read_text(encoding='utf-8'))
    if record.get('record_id') != record_id or record.get('stage') != 'events' or record.get('status') != 'success':
        raise ValueError('事件记录不可用于第二阶段。')
    record['record_file'] = matches[0].relative_to(ROOT).as_posix()
    return record


def load_stage_result(record_id, stage, label):
    if not re.fullmatch(r'[0-9a-f]{32}', record_id or ''):
        raise ValueError(f'{label} ID 无效。')
    matches = saved_record_paths(record_id)
    if len(matches) != 1:
        raise ValueError(f'{label}不存在。' if not matches else '记录 ID 对应多个文件。')
    record = json.loads(matches[0].read_text(encoding='utf-8'))
    if record.get('record_id') != record_id or record.get('stage') != stage or record.get('status') != 'success':
        raise ValueError(f'{label}不可用。')
    record['record_file'] = matches[0].relative_to(ROOT).as_posix()
    return record


def segment_windows(duration):
    """Twenty-second ownership windows, each later request sees one preceding second."""
    if duration <= 25.:
        return [(0., duration, 0., duration)]
    windows = []
    core_start = 0.
    while core_start < duration:
        core_end = min(duration, core_start + 20.)
        windows.append((core_start, core_end, max(0., core_start - 1.), core_end))
        core_start = core_end
    return windows


def clip_video(path, output, start_s, end_s):
    run(['ffmpeg', '-v', 'error', '-i', str(path), '-ss', f'{start_s:.6f}',
         '-t', f'{end_s-start_s:.6f}', '-map', '0:v:0', '-an', '-sn', '-dn',
         '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p',
         '-movflags', '+faststart', str(output)])
    if output.stat().st_size > MAX_VIDEO:
        raise ValueError('分段编码后仍超过 12 MiB，请压缩源视频。')
    return output


def analyze_segment(path, folder, core_start, core_end, clip_start, clip_end,
                    prompt, key, parent, depth=0):
    clip = folder / f'segment-{len(parent["segments"])+1}.mp4'
    clip_video(path, clip, clip_start, clip_end)
    metadata = probe(clip)
    data = base64.b64encode(clip.read_bytes()).decode()
    model = parent.get('model') or 'qwen3-vl-plus'
    mode = parent.get('mode') or 'direct'
    fps = parent.get('fps') or 2.
    segment = {'core_start_s': core_start, 'core_end_s': core_end,
               'clip_start_s': clip_start, 'clip_end_s': clip_end,
               'clip_sha256': hashlib.sha256(clip.read_bytes()).hexdigest(),
               'sent_video_bytes': clip.stat().st_size, 'sent_audio': False,
               'metadata': metadata, 'fps': fps, 'status': 'failed'}
    parent['segments'].append(segment)
    try:
        if mode == 'frames':
            frame_dir = folder / f'frames-{len(parent["segments"])}'
            frame_dir.mkdir()
            frames = extract(clip, frame_dir, metadata['duration_s'], fps)
            url, body, _ = build_request(model, 'frames', fps, '', frames, metadata, prompt)
        else:
            url, body, _ = build_request(model, 'direct', fps, data, [], metadata, prompt)
        segment['actual_prompt'] = body['messages'][0]['content'][0]['text']
        output = call_v13_model(url, body, key, segment)
        analysis = validate_stage_events(output, metadata['duration_s'])
        segment['analysis'] = analysis
        segment['status'] = 'success'
    except TruncatedOutputError as error:
        segment['error'] = str(error)
        if depth >= 1 or core_end - core_start <= 6.:
            raise
        middle = (core_start + core_end) / 2.
        return (analyze_segment(path, folder, core_start, middle, max(0., core_start - 1.), middle,
                                prompt, key, parent, depth + 1)
                + analyze_segment(path, folder, middle, core_end, max(0., middle - 1.), core_end,
                                  prompt, key, parent, depth + 1))
    except (ValueError, KeyError, IndexError, TypeError, HTTPError, URLError) as error:
        segment['error'] = str(error)
        raise
    # Only one segment owns each original-video time, despite overlapping input clips.
    events = []
    for event in analysis['events']:
        shifted = dict(event)
        shifted.pop('observation_id', None)
        for field in ('start_s', 'end_s', 'observation_s'):
            shifted[field] = round(event[field] + clip_start, 6)
        at = shifted['observation_s']
        if core_start <= at < core_end or (core_end >= parent['metadata']['duration_s'] and at == core_end):
            shifted['segment_clip_start_s'] = clip_start
            shifted['segment_core_start_s'] = core_start
            events.append(shifted)
    return events


def deduplicate_segment_events(events):
    """Drop only highly overlapping cross-segment continuation descriptions."""
    def bigrams(value):
        value = re.sub(r'\s+', '', value)
        return {value[index:index + 2] for index in range(len(value) - 1)}

    kept, dropped = [], []
    for event in sorted(events, key=lambda item: (item['observation_s'], item['start_s'])):
        boundary = event.get('segment_core_start_s', 0)
        earlier = next((item for item in reversed(kept)
                        if item.get('segment_core_start_s', 0) < boundary
                        and item['end_s'] >= boundary), None)
        if boundary and earlier and event['start_s'] < boundary:
            old_words, new_words = bigrams(earlier['description']), bigrams(event['description'])
            overlap = len(old_words & new_words) / max(1, min(len(old_words), len(new_words)))
            if overlap >= 0.08:
                dropped.append(event)
                continue
        kept.append(event)
    return kept, dropped


def analyze_events_segmented(path, folder, metadata, prompt, key, record):
    record['segments'] = []
    all_events = []
    for start, end, clip_start, clip_end in segment_windows(metadata['duration_s']):
        all_events.extend(analyze_segment(path, folder, start, end, clip_start, clip_end,
                                          prompt, key, record))
    all_events, record['deduplicated_overlap_events'] = deduplicate_segment_events(all_events)
    successful = [item for item in record['segments'] if item['status'] == 'success']
    summaries = [f"约{item['core_start_s']:g}–{item['core_end_s']:g}秒：{item['analysis']['summary']}"
                 for item in successful]
    limitations = ['长视频分段识别；相邻片段重叠约 1 秒，事件按代表画面归入唯一时间段。']
    for item in successful:
        limitations.extend(f"{item['clip_start_s']:g}秒片段：{value}" for value in item['analysis']['limitations'])
    record['analysis'] = validate_stage_events(json.dumps({'summary': '；'.join(summaries),
                                                           'events': all_events, 'limitations': limitations},
                                                          ensure_ascii=False), metadata['duration_s'])
    record['usage'] = {name: sum((item.get('usage') or {}).get(name, 0) for item in record['segments'])
                       for name in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    record['video_tokens'] = sum(item.get('video_tokens') or 0 for item in record['segments'])
    record['audio_tokens'] = 0
    record['audio_verification'] = 'not_sent'
    record['media_verification'] = 'confirmed' if record['video_tokens'] else 'unreported'
    record['request_bytes'] = sum(item.get('request_bytes') or 0 for item in record['segments'])
    record['sent_video_bytes'] = sum(item.get('sent_video_bytes') or 0 for item in record['segments'])
    record['api_roundtrip_s'] = round(sum(item.get('api_roundtrip_s') or 0 for item in record['segments']), 3)
    record['finish_reason'] = 'stop'
    record['status'] = 'success'


def analyze_events(raw, video_name, version='v13', model='qwen3-vl-plus', mode='direct', fps=2.):
    if model not in MODELS or mode not in ('direct', 'frames') or fps not in (0.5, 1., 2.):
        raise ValueError('无效的模型、输入方式或采样率。请在页面上选择 .env 里配置的模型。')
    key = os.environ.get('BEEROUTE_API_KEY')
    if not key:
        raise ValueError('尚未配置 BEEROUTE_API_KEY。')
    if not raw or len(raw) > MAX_VIDEO:
        raise ValueError('视频须为非空 MP4，大小不超过 12 MiB。')
    prompt_path, prompt = load_stage_prompt('events', version)
    started = time.perf_counter()
    record = {'record_id': secrets.token_hex(16), 'stage': 'events', 'created_at': datetime.now(timezone.utc).isoformat(),
              'model': model, 'mode': mode, 'fps': fps, 'video_name': Path(video_name).name,
              'video_sha256': hashlib.sha256(raw).hexdigest(), 'video_bytes': len(raw),
              'prompt_file': f'prompts/{version}/{prompt_path.name}', 'prompt': prompt, 'status': 'failed'}
    try:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            path = folder / 'input.mp4'
            path.write_bytes(raw)
            metadata = probe(path)
            record['metadata'] = metadata
            if version == 'v14':
                record['sent_audio'] = False
                record['preprocess_s'] = round(time.perf_counter() - started, 3)
                analyze_events_segmented(path, folder, metadata, prompt, key, record)
            elif mode == 'frames':
                frames = extract(path, folder, metadata['duration_s'], fps)
                record.update(sent_audio=False, frame_times_s=[item['time_s'] for item in frames])
                url, body, _ = build_request(model, 'frames', fps, '', frames, metadata, prompt)
                record['actual_prompt'] = body['messages'][0]['content'][0]['text']
                record['preprocess_s'] = round(time.perf_counter() - started, 3)
                output = call_v13_model(url, body, key, record)
                record['analysis'] = validate_stage_events(output, metadata['duration_s'])
            else:
                send_audio = metadata['has_audio'] and supports_video_audio(model)
                request_path = path if send_audio or not metadata['has_audio'] else strip_audio(path, folder / 'visual-only.mp4')
                data = base64.b64encode(request_path.read_bytes()).decode()
                record['sent_audio'] = send_audio
                record['sent_video_bytes'] = request_path.stat().st_size
                url, body, _ = build_request(model, 'direct', fps, data, [], metadata, prompt)
                record['actual_prompt'] = body['messages'][0]['content'][0]['text']
                record['preprocess_s'] = round(time.perf_counter() - started, 3)
                output = call_v13_model(url, body, key, record)
                record['analysis'] = validate_stage_events(output, metadata['duration_s'])
            record.update(status='success', cost=None, cost_note='未设置单价；按返回的 usage 核算。')
    except HTTPError as error:
        record['error'] = f'模型 API HTTP {error.code}。'
        record['api_error_detail'] = error.read().decode('utf-8', errors='replace')[:2000]
    except URLError:
        record['error'] = '模型 API 网络连接失败。'
    except (ValueError, KeyError, IndexError, TypeError, subprocess.TimeoutExpired, TimeoutError) as error:
        record['error'] = str(error)
    record['total_s'] = round(time.perf_counter() - started, 3)
    save_record(record)
    return record


def suggest_opportunities(raw, video_name, record_id, selected_ids, version=None, model='qwen3-vl-plus'):
    if not selected_ids or len(selected_ids) != len(set(selected_ids)):
        raise ValueError('请至少审核并选择一条不重复的事件。')
    parent = stage_record(record_id)
    if not raw or len(raw) > MAX_VIDEO or hashlib.sha256(raw).hexdigest() != parent['video_sha256']:
        raise ValueError('第二阶段视频与事件记录不一致，未调用模型。')
    by_id = {event['observation_id']: event for event in parent['analysis']['events']}
    if any(item not in by_id for item in selected_ids):
        raise ValueError('选择了不属于事件记录的画面。')
    if model not in MODELS:
        raise ValueError('无效的模型。请在页面上选择 .env 里配置的模型。')
    key = os.environ.get('BEEROUTE_API_KEY')
    if not key:
        raise ValueError('尚未配置 BEEROUTE_API_KEY。')
    prompt_path, prompt = load_stage_prompt('opportunities', version)
    started = time.perf_counter()
    record = {'record_id': secrets.token_hex(16), 'parent_record_id': record_id,
              'parent_record_file': parent['record_file'], 'stage': 'opportunities',
              'created_at': datetime.now(timezone.utc).isoformat(), 'model': model,
              'mode': 'frames', 'fps': 2., 'video_name': Path(video_name).name,
              'video_sha256': parent['video_sha256'], 'video_bytes': len(raw),
              'prompt_file': f'prompts/{prompt_path.parent.name}/{prompt_path.name}', 'prompt': prompt,
              'selected_event_ids': selected_ids, 'status': 'failed', 'sent_audio': False}
    try:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            path = folder / 'input.mp4'
            path.write_bytes(raw)
            metadata = probe(path)
            record['metadata'] = metadata
            frames = extract(path, folder, metadata['duration_s'], 2.)
            observations = []
            content = [{'type': 'text', 'text': prompt}]
            for event in parent['analysis']['events']:
                if event['observation_id'] not in selected_ids:
                    continue
                frame = min(frames, key=lambda item: abs(item['time_s'] - event['observation_s']))
                preceding = [x['description'] for x in parent['analysis']['events']
                             if x['observation_id'] != event['observation_id'] and x['end_s'] <= event['observation_s']]
                context = ('\n'.join(preceding) if preceding else '无更早的已记录事件')
                content.append({'type': 'text', 'text': f"observation_id={event['observation_id']}；原片画面时间={frame['time_s']:.6f}秒。"
                                f"\n截至此刻的先前事件：{context}\n当前事件：{event['description']}。"
                                f"\n当前事件画面依据：{event['evidence']}"})
                content.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + frame['data']}})
                observations.append({'observation_id': event['observation_id'],
                                     'requested_time_s': event['observation_s'], 'frame_time_s': frame['time_s'],
                                     'frame_sha256': hashlib.sha256(base64.b64decode(frame['data'])).hexdigest()})
            record['observations'] = observations
            record['frame_times_s'] = [item['frame_time_s'] for item in observations]
            record['actual_prompt'] = '\n\n'.join(item['text'] for item in content if item['type'] == 'text')
            body = {'model': model, 'messages': [{'role': 'user', 'content': content}], 'max_tokens': 4096}
            if str(model).startswith('qwen'):
                body['enable_thinking'] = False
            base = os.environ.get('BEEROUTE_BASE_URL', 'https://beeroute.ai/v1').rstrip('/')
            if not base.startswith('https://'):
                raise ValueError('模型 API 地址必须使用 HTTPS。')
            record['preprocess_s'] = round(time.perf_counter() - started, 3)
            output = call_v13_model(base + '/chat/completions', body, key, record)
            record['analysis'] = validate_stage_opportunities(output, observations)
            record.update(status='success', cost=None, cost_note='未设置单价；按返回的 usage 核算。')
    except HTTPError as error:
        record['error'] = f'模型 API HTTP {error.code}。'
        record['api_error_detail'] = error.read().decode('utf-8', errors='replace')[:2000]
    except URLError:
        record['error'] = '模型 API 网络连接失败。'
    except (ValueError, KeyError, IndexError, TypeError, subprocess.TimeoutExpired, TimeoutError) as error:
        record['error'] = str(error)
    record['total_s'] = round(time.perf_counter() - started, 3)
    save_record(record)
    return record


GAME_IDS = ('tap_object', 'choice', 'timing', 'mash', 'drag', 'hold_release', 'swipe')
GAMEPLAY_FIELDS = ('fit', 'player_goal', 'player_action', 'success', 'failure', 'feedback')
RESUME_INTENT = '到点暂停，结束后继续播放'


def load_game_catalog():
    path = ROOT / 'prompts' / 'games' / 'catalog.json'
    catalog = json.loads(path.read_text(encoding='utf-8'))
    types = catalog.get('types') if isinstance(catalog, dict) else None
    if not isinstance(types, list) or not types:
        raise ValueError('小游戏目录无效。')
    seen = set()
    for item in types:
        if not isinstance(item, dict):
            raise ValueError('小游戏目录无效。')
        if item.get('id') not in GAME_IDS or item['id'] in seen:
            raise ValueError('小游戏目录的类型无效或重复。')
        seen.add(item['id'])
        for key in ('name', 'play', 'situations', 'player_goal'):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError('小游戏目录缺少玩法说明。')
    if seen != set(GAME_IDS):
        raise ValueError('小游戏目录必须包含全部预设玩法。')
    return catalog


def catalog_text(catalog):
    return '\n'.join(
        f"- {item['id']}｜{item['name']}｜玩法：{item['play']}｜适用：{item['situations']}｜玩家目标：{item['player_goal']}"
        for item in catalog['types'])


def load_gameplay_prompt(version=None):
    return load_stage_prompt('gameplay', version)


def indexed_points(opportunity):
    points = []
    for index, point in enumerate(opportunity['analysis']['interaction_points'], 1):
        item = dict(point)
        item['point_id'] = f'p{index}'
        points.append(item)
    return points


def finite_time(value, duration):
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value):
        return None
    if value < 0 or value > duration + 1:
        return None
    return duration if value > duration else value


def validate_gameplay(text, points, catalog):
    result = json.loads(text)
    if not isinstance(result, dict) or not isinstance(result.get('recommendations'), list):
        raise ValueError('玩法阶段必须返回 recommendations 数组。')
    by_id = {item['point_id']: item for item in points}
    names = {item['id']: item['name'] for item in catalog['types']}
    recommendations = []
    seen = set()
    for item in result['recommendations']:
        if not isinstance(item, dict) or item.get('point_id') not in by_id:
            raise ValueError('玩法引用了未选择的交互点。')
        if item['point_id'] in seen:
            raise ValueError('同一交互点出现了重复推荐。')
        seen.add(item['point_id'])
        if item.get('game_id') not in names:
            raise ValueError('玩法类型不在预设目录中。')
        for key in GAMEPLAY_FIELDS:
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError('玩法推荐缺少场景关系或玩法说明。')
        source = by_id[item['point_id']]
        recommendations.append({
            'point_id': item['point_id'], 'observation_id': source['observation_id'],
            'event_id': source.get('event_id') or source['observation_id'], 'time_s': source['time_s'],
            'opportunity': source['opportunity'], 'reason': source['reason'], 'game_id': item['game_id'],
            'game_name': names[item['game_id']], **{key: item[key].strip() for key in GAMEPLAY_FIELDS}})
    recommendations.sort(key=lambda item: (item['time_s'], item['point_id']))
    return {'recommendations': recommendations, 'limitations': []}


def suggest_gameplay(raw, video_name, record_id, selected_ids, version=None, model='qwen3-vl-plus'):
    if not selected_ids or len(selected_ids) != len(set(selected_ids)):
        raise ValueError('请至少选择一个不重复的交互点。')
    parent = load_stage_result(record_id, 'opportunities', '机会记录')
    if not raw or len(raw) > MAX_VIDEO or hashlib.sha256(raw).hexdigest() != parent['video_sha256']:
        raise ValueError('玩法推荐使用的视频与机会记录不一致，未调用模型。')
    points = indexed_points(parent)
    by_id = {item['point_id']: item for item in points}
    if any(item not in by_id for item in selected_ids):
        raise ValueError('选择了不属于机会记录的交互点。')
    if model not in MODELS:
        raise ValueError('无效的模型。请在页面上选择 .env 里配置的模型。')
    key = os.environ.get('BEEROUTE_API_KEY')
    if not key:
        raise ValueError('尚未配置 BEEROUTE_API_KEY。')
    prompt_path, prompt = load_gameplay_prompt(version)
    catalog = load_game_catalog()
    if prompt_path.parent.name == 'v17':
        catalog = dict(catalog)
        catalog['types'] = [item for item in catalog['types'] if item['id'] != 'choice']
    started = time.perf_counter()
    record = {'record_id': secrets.token_hex(16), 'parent_record_id': record_id,
              'parent_record_file': parent['record_file'], 'stage': 'gameplay',
              'created_at': datetime.now(timezone.utc).isoformat(), 'model': model,
              'mode': 'frames', 'fps': 2., 'video_name': Path(video_name).name,
              'video_sha256': parent['video_sha256'], 'video_bytes': len(raw),
              'prompt_file': f'prompts/{prompt_path.parent.name}/{prompt_path.name}', 'prompt': prompt,
              'catalog_file': 'prompts/games/catalog.json',
              'excluded_game_ids': ['choice'] if prompt_path.parent.name == 'v17' else [],
              'selected_point_ids': selected_ids,
              'status': 'failed', 'sent_audio': False}
    try:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            path = folder / 'input.mp4'
            path.write_bytes(raw)
            metadata = probe(path)
            record['metadata'] = metadata
            frames = extract(path, folder, metadata['duration_s'], 2.)
            events_by_id = {}
            if parent.get('parent_record_id'):
                try:
                    events_record = stage_record(parent['parent_record_id'])
                    events_by_id = {event['observation_id']: event for event in events_record['analysis']['events']}
                except (ValueError, KeyError, TypeError):
                    events_by_id = {}
            observations = []
            content = [{'type': 'text', 'text': prompt + '\n\n预设小游戏目录：\n' + catalog_text(catalog)}]
            for point_id in selected_ids:
                point = by_id[point_id]
                frame = min(frames, key=lambda item: abs(item['time_s'] - point['time_s']))
                event = events_by_id.get(point['observation_id'])
                if event:
                    event_text = f"当前事件：{event['description']}\n当前事件画面依据：{event['evidence']}"
                else:
                    event_text = '当前事件描述：无'
                content.append({'type': 'text', 'text':
                                f"point_id={point['point_id']}；observation_id={point['observation_id']}；"
                                f"原片画面时间={frame['time_s']:.6f}秒。\n机会：{point['opportunity']}\n"
                                f"理由：{point['reason']}\n{event_text}"})
                content.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + frame['data']}})
                observations.append({'point_id': point['point_id'], 'observation_id': point['observation_id'],
                                     'requested_time_s': point['time_s'], 'frame_time_s': frame['time_s'],
                                     'frame_sha256': hashlib.sha256(base64.b64decode(frame['data'])).hexdigest()})
            record['observations'] = observations
            record['frame_times_s'] = [item['frame_time_s'] for item in observations]
            record['actual_prompt'] = '\n\n'.join(item['text'] for item in content if item['type'] == 'text')
            body = {'model': model, 'messages': [{'role': 'user', 'content': content}], 'max_tokens': 4096}
            if str(model).startswith('qwen'):
                body['enable_thinking'] = False
            base = os.environ.get('BEEROUTE_BASE_URL', 'https://beeroute.ai/v1').rstrip('/')
            if not base.startswith('https://'):
                raise ValueError('模型 API 地址必须使用 HTTPS。')
            record['preprocess_s'] = round(time.perf_counter() - started, 3)
            output = call_v13_model(base + '/chat/completions', body, key, record)
            record['analysis'] = validate_gameplay(output, [by_id[item] for item in selected_ids], catalog)
            record.update(status='success', cost=None, cost_note='未设置单价；按返回的 usage 核算。')
    except HTTPError as error:
        record['error'] = f'模型 API HTTP {error.code}。'
        record['api_error_detail'] = error.read().decode('utf-8', errors='replace')[:2000]
    except URLError:
        record['error'] = '模型 API 网络连接失败。'
    except (ValueError, KeyError, IndexError, TypeError, subprocess.TimeoutExpired, TimeoutError) as error:
        record['error'] = str(error)
    record['total_s'] = round(time.perf_counter() - started, 3)
    save_record(record)
    return record


def save_gameplay_brief(payload):
    if not isinstance(payload, dict):
        raise ValueError('玩法描述格式不正确。')
    opportunity = load_stage_result(payload.get('opportunity_record_id'), 'opportunities', '机会记录')
    gameplay = load_stage_result(payload.get('gameplay_record_id'), 'gameplay', '玩法记录')
    if gameplay.get('parent_record_id') != opportunity['record_id']:
        raise ValueError('玩法记录与机会记录不对应。')
    points = {item['point_id']: item for item in indexed_points(opportunity)}
    recommendations = {item['point_id']: item for item in gameplay['analysis']['recommendations']}
    expected = list(gameplay.get('selected_point_ids') or [])
    selections = payload.get('selections')
    if not isinstance(selections, list):
        raise ValueError('玩法描述缺少 selections。')
    got = [item.get('point_id') if isinstance(item, dict) else None for item in selections]
    if len(got) != len(set(got)) or sorted(got) != sorted(expected):
        raise ValueError('玩法描述必须包含本次推荐的每个交互点，且不能重复。')
    names = {item['id']: item['name'] for item in load_game_catalog()['types']}
    duration = opportunity['metadata']['duration_s']
    saved = []
    for item in selections:
        point = points.get(item['point_id'])
        if point is None:
            raise ValueError('玩法描述引用了不存在的交互点。')
        status = item.get('status')
        if status not in ('accepted', 'edited', 'discarded'):
            raise ValueError('玩法选择状态无效。')
        adjusted = finite_time(item.get('adjusted_time_s', point['time_s']), duration)
        if adjusted is None:
            raise ValueError('调整后的时间超出片长。')
        recommendation = recommendations.get(item['point_id'])
        entry = {'point_id': point['point_id'], 'observation_id': point['observation_id'],
                 'time_s': point['time_s'], 'adjusted_time_s': adjusted,
                 'opportunity': point['opportunity'], 'reason': point['reason'], 'status': status,
                 'ai_game_id': recommendation['game_id'] if recommendation else None,
                 'ai_fit': recommendation['fit'] if recommendation else None, 'resume': RESUME_INTENT}
        if status == 'discarded':
            entry.update(game_id=None, game_name=None, fit='')
            entry.update({key: '' for key in GAMEPLAY_FIELDS if key != 'fit'})
        elif status == 'accepted':
            if not recommendation:
                raise ValueError('没有模型推荐的交互点不能直接接受。')
            entry.update(game_id=recommendation['game_id'], game_name=recommendation['game_name'],
                         fit=recommendation['fit'])
            entry.update({key: recommendation[key] for key in GAMEPLAY_FIELDS if key != 'fit'})
        else:
            if item.get('game_id') not in names:
                raise ValueError('玩法类型不在预设目录中。')
            for key in ('player_goal', 'player_action', 'success', 'failure', 'feedback'):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    raise ValueError('修改后的玩法缺少目标、操作、成功、失败或反馈。')
            fit = item.get('fit')
            if fit is None:
                fit = recommendation['fit'] if recommendation else ''
            if not isinstance(fit, str):
                raise ValueError('玩法描述字段格式不正确。')
            entry.update(game_id=item['game_id'], game_name=names[item['game_id']], fit=fit.strip())
            entry.update({key: item[key].strip() for key in ('player_goal', 'player_action', 'success', 'failure', 'feedback')})
        saved.append(entry)
    saved.sort(key=lambda item: (item['time_s'], item['point_id']))
    record = {'record_id': secrets.token_hex(16), 'stage': 'gameplay_brief',
              'parent_record_id': gameplay['record_id'], 'parent_record_file': gameplay['record_file'],
              'opportunity_record_id': opportunity['record_id'],
              'opportunity_record_file': opportunity['record_file'],
              'gameplay_record_id': gameplay['record_id'], 'gameplay_record_file': gameplay['record_file'],
              'created_at': datetime.now(timezone.utc).isoformat(), 'model': 'qwen3-vl-plus', 'mode': 'brief',
              'video_name': opportunity.get('video_name') or 'video', 'video_sha256': opportunity.get('video_sha256'),
              'prompt_file': 'prompts/games/catalog.json', 'status': 'success', 'selections': saved,
              'resume': RESUME_INTENT, 'cost': None, 'cost_note': '玩法描述由本地选择保存，未调用模型。'}
    save_record(record)
    return record


def analyze(raw, model, mode, fps, video_name, prompt_version=None):
    if model not in MODELS or mode not in ('direct', 'frames') or fps not in (0.5, 1., 2.):
        raise ValueError('无效的模型、模式或采样频率。请在页面上选择 .env 里配置的模型。')
    keyname = PROVIDER_KEYS[MODELS[model]]
    key = os.environ.get(keyname)
    if not key:
        raise ValueError(f'尚未配置 {keyname}。没有调用模型，也没有生成分析结果。')
    if not raw or len(raw) > MAX_VIDEO:
        raise ValueError('视频须为非空 MP4，大小不超过 12 MiB。')
    prompt_path, prompt = load_prompt(prompt_version)
    start = time.perf_counter()
    record = {'created_at': datetime.now(timezone.utc).isoformat(), 'model': model, 'mode': mode,
              'fps': fps, 'video_name': Path(video_name).name, 'video_sha256': hashlib.sha256(raw).hexdigest(),
              'video_bytes': len(raw), 'prompt_file': f'prompts/{prompt_path.name}', 'prompt': prompt, 'status': 'failed'}
    try:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            path = folder / 'input.mp4'
            path.write_bytes(raw)
            metadata = probe(path)
            frames = extract(path, folder, metadata['duration_s'], fps) if mode == 'frames' else []
            record.update(metadata=metadata, frame_times_s=[x['time_s'] for x in frames])
            request_path = path
            if mode == 'direct' and metadata['has_audio'] and not supports_video_audio(model):
                request_path = strip_audio(path, folder / 'visual-only.mp4')
            request_video = request_path.read_bytes() if mode == 'direct' else b''
            record['sent_audio'] = mode == 'direct' and metadata['has_audio'] and supports_video_audio(model)
            record['sent_video_bytes'] = len(request_video)
            url, body, audio_requested = build_request(model, mode, fps, base64.b64encode(request_video).decode(), frames, metadata, prompt)
            payload = json.dumps(body).encode()
            if len(payload) > 19 * 1024 * 1024:
                raise ValueError('编码后请求过大，请压缩视频或降低抽帧率。')
            record['request_bytes'] = len(payload)
            record['request_options'] = {'fps': fps, 'response_format': body.get('response_format')}
            record['api_attempts'] = []
            record['preprocess_s'] = round(time.perf_counter()-start, 3)
            record['actual_prompt'] = body['contents'][0]['parts'][0]['text'] if MODELS[model] == 'gemini' else body['messages'][0]['content'][0]['text']
            headers = {'Content-Type': 'application/json'}
            headers.update({'x-goog-api-key': key} if MODELS[model] == 'gemini' else {'Authorization': 'Bearer ' + key})
            api_start = time.perf_counter()
            try:
                record['api_attempts'].append({'response_format': body.get('response_format')})
                with urlopen(Request(url, data=payload, headers=headers), timeout=300) as response:
                    result = json.load(response)
            except HTTPError as error:
                detail = error.read().decode('utf-8', errors='replace')[:2000]
                json_mode_rejected = (error.code in (400, 422) and body.get('response_format')
                                      and ('response_format' in detail or 'json_object' in detail))
                record['api_attempts'][-1]['http_error'] = error.code
                record['api_attempts'][-1]['error_detail'] = detail
                if not json_mode_rejected:
                    raise
                del body['response_format']
                payload = json.dumps(body).encode()
                record['request_bytes'] = len(payload)
                record['request_options']['response_format'] = None
                record['json_mode_fallback'] = True
                record['api_attempts'].append({'response_format': None})
                with urlopen(Request(url, data=payload, headers=headers), timeout=300) as response:
                    result = json.load(response)
            record['api_roundtrip_s'] = round(time.perf_counter()-api_start, 3)
            record['raw_response'] = result
            if MODELS[model] == 'gemini':
                candidate = result.get('candidates', [{}])[0]
                output = ''.join(p.get('text', '') for p in candidate.get('content', {}).get('parts', []) if not p.get('thought'))
                record['usage'] = result.get('usageMetadata', {})
                record['finish_reason'] = candidate.get('finishReason')
            else:
                content = result['choices'][0]['message'].get('content')
                output = ''.join(p.get('text', '') for p in content if isinstance(p, dict)) if isinstance(content, list) else (content or '')
                record['usage'] = result.get('usage', {})
                record['finish_reason'] = result['choices'][0].get('finish_reason')
            record['raw_text'] = output
            modalities = modality_token_counts(record['usage'])
            record['media_tokens'] = sum(modalities.values()) if modalities is not None else None
            record['audio_tokens'] = modalities['audio'] if modalities is not None else None
            record['video_tokens'] = (modalities['image'] + modalities['video']) if modalities is not None else None
            record['audio_verification'] = ('not_sent' if not audio_requested else
                                            'unreported' if modalities is None else
                                            'confirmed' if modalities['audio'] > 0 else 'zero')
            if mode == 'direct' and modalities is not None and modalities['image'] + modalities['video'] == 0:
                raise ValueError('直接视频的画面未确认进入模型：用量里没有图像或视频 Token。这次输出不能当作看过片子。')
            record['media_verification'] = 'confirmed' if record['media_tokens'] else 'unreported'
            audio_confirmed = audio_requested and record['audio_tokens'] is not None and record['audio_tokens'] > 0
            prompt_version = int(prompt_path.stem[1:])
            day2_prompt = prompt_version >= 3
            if prompt_version == 12:
                record['analysis'] = validate_open_interactions(extract_json(output), metadata['duration_s'])
            else:
                record['analysis'] = validate(extract_json(output), metadata['duration_s'], audio_confirmed,
                                              require_interaction_points=day2_prompt,
                                              require_point_source=prompt_version >= 6,
                                              max_points=1 if prompt_version >= 7 else 2,
                                              require_action_observed=prompt_version >= 9)
            if mode == 'direct' and modalities is None:
                record['analysis']['limitations'].append('服务未返回模态用量明细，无法确认视频是否进入模型，请回看原片核实。')
            if record['finish_reason'] not in ('stop', 'STOP'):
                raise ValueError('模型未正常结束，原始输出已保留，请人工检查。')
            record.update(status='success', cost=None, cost_note='未设置单价；按 Beeroute 返回的 usage 与广场单价核算。')
    except HTTPError as error:
        record['error'] = f'模型 API HTTP {error.code}，请检查凭据、模型权限或额度。'
    except URLError:
        record['error'] = '模型 API 网络连接失败，请检查访问条件。'
    except (ValueError, KeyError, IndexError, TypeError, subprocess.TimeoutExpired, TimeoutError) as error:
        record['error'] = str(error)
    record['total_s'] = round(time.perf_counter()-start, 3)
    save_record(record)
    return record


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, code, data, mime='application/json; charset=utf-8'):
        data = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'):
            return self.respond(403, {'error': '非法 Host'})
        parsed = urlparse(self.path)
        if parsed.path == '/':
            return self.respond(200, (DEMO / 'web' / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        if parsed.path == '/config':
            try:
                prompt_path, _ = load_prompt()
                prompt_file = f'prompts/{prompt_path.name}'
                prompt_files = [f'prompts/{p.name}' for _, p in prompt_paths()]
                prompt_catalog = [{'version': p.stem, 'flow': 'single',
                                   'files': [{'path': f'prompts/{p.name}', 'content': p.read_text(encoding='utf-8').strip()}]}
                                  for _, p in prompt_paths()]
            except ValueError as error:
                prompt_file = str(error)
                prompt_files = []
                prompt_catalog = []
            try:
                event_path, event_prompt = load_stage_prompt('events', 'v13')
                opportunity_path, opportunity_prompt = load_stage_prompt('opportunities', 'v13')
                prompt_catalog.append({'version': 'v13', 'flow': 'two_stage', 'files': [
                    {'path': f'prompts/v13/{event_path.name}', 'content': event_prompt},
                    {'path': f'prompts/v13/{opportunity_path.name}', 'content': opportunity_prompt}]})
            except (FileNotFoundError, ValueError):
                pass
            try:
                event_path, event_prompt = load_stage_prompt('events', 'v14')
                opportunity_path, opportunity_prompt = load_stage_prompt('opportunities', 'v13')
                prompt_catalog.append({'version': 'v14', 'flow': 'two_stage', 'files': [
                    {'path': f'prompts/v14/{event_path.name}', 'content': event_prompt},
                    {'path': f'prompts/v13/{opportunity_path.name}', 'content': opportunity_prompt}]})
            except (FileNotFoundError, ValueError):
                pass
            try:
                games = load_game_catalog()
            except (FileNotFoundError, ValueError, json.JSONDecodeError):
                games = None
            return self.respond(200, {
                'token': TOKEN,
                'models': MODELS,
                'samples': list_samples(),
                'prompt_file': prompt_file,
                'prompt_files': prompt_files,
                'prompt_catalog': prompt_catalog,
                'stage_prompts': stage_prompt_catalog(),
                'games': games,
                'configured': {'beeroute': bool(os.environ.get('BEEROUTE_API_KEY'))}})
        if parsed.path == '/sample':
            try:
                name = unquote(parse_qs(parsed.query).get('name', [''])[0])
                return self.respond(200, read_sample(name), 'video/mp4')
            except ValueError as error:
                return self.respond(404, {'error': str(error)})
        return self.respond(404, {'error': 'Not found'})

    def do_POST(self):
        if self.headers.get('X-Local-Token') != TOKEN:
            return self.respond(403, {'error': '请从本地首页发起请求。'})
        parsed = urlparse(self.path)
        if parsed.path == '/briefs':
            try:
                n = int(self.headers.get('Content-Length', '0'))
                if not 0 < n <= 1_000_000:
                    raise ValueError('玩法描述为空或过大。')
                payload = json.loads(self.rfile.read(n).decode('utf-8'))
                return self.respond(200, save_gameplay_brief(payload))
            except json.JSONDecodeError:
                return self.respond(400, {'error': '玩法描述不是合法 JSON。'})
            except ValueError as error:
                return self.respond(400, {'error': str(error)})
            except Exception:
                return self.respond(500, {'error': '本地处理失败，请检查文件和运行环境。'})
        if parsed.path not in ('/analyze', '/events', '/opportunities', '/gameplay'):
            return self.respond(404, {'error': 'Not found'})
        try:
            q = parse_qs(parsed.query)
            sample = q.get('sample', [''])[0]
            if sample:
                raw = read_sample(sample)
                video_name = sample
            else:
                n = int(self.headers.get('Content-Length', '0'))
                if not 0 < n <= MAX_VIDEO:
                    raise ValueError('请选择不超过 12 MiB 的 MP4。')
                raw = self.rfile.read(n)
                if len(raw) != n:
                    raise ValueError('上传不完整。')
                video_name = unquote(q.get('name', ['upload.mp4'])[0]) or 'upload.mp4'
            if parsed.path == '/events':
                mode = q.get('mode', ['direct'])[0]
                fps = float(q.get('fps', ['2'])[0])
                record = analyze_events(raw, video_name, q.get('version', ['v14'])[0],
                                        q.get('model', ['qwen3-vl-plus'])[0], mode, fps)
            elif parsed.path == '/opportunities':
                selected = [value for value in q.get('selected', [''])[0].split(',') if value]
                record = suggest_opportunities(raw, video_name, q.get('record_id', [''])[0], selected,
                                               q.get('version', [None])[0] or None,
                                               q.get('model', ['qwen3-vl-plus'])[0])
            elif parsed.path == '/gameplay':
                selected = [value for value in q.get('selected', [''])[0].split(',') if value]
                record = suggest_gameplay(raw, video_name, q.get('record_id', [''])[0], selected,
                                          q.get('version', [None])[0] or None,
                                          q.get('model', ['qwen3-vl-plus'])[0])
            else:
                record = analyze(raw, q['model'][0], q['mode'][0], float(q['fps'][0]), video_name,
                                 q.get('prompt', [None])[0])
            return self.respond(200, record)
        except (ValueError, KeyError) as error:
            return self.respond(400, {'error': str(error)})
        except Exception:
            return self.respond(500, {'error': '本地处理失败，请检查文件和运行环境。'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8765'))
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    where = '、'.join(MODEL_NAMES) or '（未设置 BEEROUTE_MODELS）'
    print(f'视频理解工作台：http://127.0.0.1:{port}')
    print(f'可选模型：{where}')
    print('页面上选择一个模型后分析，结果写入 results/ 下的同名文件夹。')
    print('Ctrl+C 退出')
    server.serve_forever()
