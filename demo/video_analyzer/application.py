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
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from .gameplay import legacy_design as gameplay_design
from .gameplay import recommendations as gameplay_recommendations
from .projects import store as projects
from .contracts.output import ContractError, MISSING, expected, parse_json, require_text
from .contracts import legacy as legacy_contracts, stages as stage_contracts
from .media import processing as media_processing
from .media.processing import MediaProcessingError
from .models import requests as model_requests, client as model_client
from .models.client import TruncatedOutputError
from .catalog import prompts as prompt_catalog
from .storage import records as record_store
from . import settings

from .paths import DEMO, ROOT, MAX_VIDEO, MAX_MODEL_REQUEST


# Configuration is loaded from the private settings file, never from .env.


def configured_models():
    """BEEROUTE_MODELS 用英文逗号分隔。页面上选择其中一个，每次只调用它。"""
    try:
        value = settings.Store(ROOT).read()['values']['BEEROUTE_MODELS']
    except (ValueError, OSError):
        value = settings.DEFAULTS['BEEROUTE_MODELS']
    return [m.strip() for m in value.split(',') if m.strip()]


MODEL_NAMES = configured_models()
MODELS = {name: 'beeroute' for name in MODEL_NAMES}
runtime_config = settings.scoped(lambda *a, **kw: (ROOT, MODELS))


def runtime_models():
    active = settings.CURRENT.get()
    if active is not None:
        return active['_models']
    return {name: 'beeroute' for name in configured_models()} if settings.Store(ROOT).path.exists() else MODELS

PROVIDER_KEYS = {'qwen': 'DASHSCOPE_API_KEY', 'gemini': 'GEMINI_API_KEY', 'beeroute': 'BEEROUTE_API_KEY'}
TOKEN = secrets.token_urlsafe(24)


def prompt_paths():
    return prompt_catalog.prompt_paths(ROOT)


def load_prompt(version=None):
    return prompt_catalog.load_prompt(ROOT, version)


def run(args):
    return media_processing.run(args)


def probe(path):
    return media_processing.probe(path, runner=run)


def extract(path, directory, duration, fps):
    return media_processing.extract(path, directory, duration, fps)


def strip_audio(path, output):
    return media_processing.strip_audio(path, output)


def prepare_full_video(path, output, metadata, mode):
    return media_processing.prepare_full_video(path, output, metadata, mode,
        remux=strip_audio, prober=probe, max_video=MAX_VIDEO)


def extract_json(text):
    return model_requests.extract_json(text)


def list_samples():
    return media_processing.list_samples(ROOT)


def read_sample(name):
    return media_processing.read_sample(ROOT, name, MAX_VIDEO)


def safe_stem(name):
    return record_store.safe_stem(name)


def supports_video_audio(model):
    return model_requests.supports_video_audio(model)


def build_request(model, mode, fps, data, frames, metadata, prompt):
    return model_requests.build_request(model, mode, fps, data, frames, metadata, prompt, runtime_models(), supports_video_audio)


def modality_token_counts(usage):
    return model_requests.modality_token_counts(usage)


def media_token_count(usage):
    counts = modality_token_counts(usage)
    return sum(counts.values()) if counts is not None else None


def validate(text, duration, audio, require_interaction_points=False, require_point_source=False,
             max_points=2, require_action_observed=False):
    return legacy_contracts.validate(text, duration, audio, require_interaction_points,
                                     require_point_source, max_points, require_action_observed)


def validate_open_interactions(text, duration):
    return legacy_contracts.validate_open_interactions(text, duration)


def save_record(record):
    return record_store.save_record(record, ROOT, clock=datetime, make_id=secrets.token_hex, stemmer=safe_stem)


def stage_prompt_paths(stage):
    return prompt_catalog.stage_prompt_paths(ROOT, stage)


def load_stage_prompt(stage, version=None):
    return prompt_catalog.load_stage_prompt(ROOT, stage, version)


def stage_prompt_catalog():
    return prompt_catalog.stage_prompt_catalog(ROOT)




def validate_stage_events(text, duration):
    return stage_contracts.validate_stage_events(text, duration)


def validate_stage_opportunities(text, observations):
    return stage_contracts.validate_stage_opportunities(text, observations)


def call_v13_model(url, body, key, record):
    return model_client.call_v13_model(url, body, key, record, urlopen, modality_token_counts, extract_json)


def saved_record_paths(record_id, folder=None):
    return record_store.saved_record_paths(record_id, folder if folder is not None else ROOT / 'results')


def stage_record(record_id):
    if not re.fullmatch(r'[0-9a-f]{32}', record_id or ''):
        raise ValueError('事件记录 ID 无效。')
    matches = saved_record_paths(record_id)
    if len(matches) != 1:
        raise ValueError('事件记录不存在。' if not matches else '记录 ID 对应多个文件。')
    record = json.loads(matches[0].read_text(encoding='utf-8'))
    if record.get('record_id') != record_id or record.get('stage') != 'events':
        raise ValueError('事件记录不可用于第二阶段。')
    if record.get('status') != 'success':
        partial = record.get('partial_analysis')
        if not isinstance(partial, dict) or not partial.get('events'):
            raise ValueError('没有可用的已完成事件，请重新识别。')
        duration = record.get('metadata', {}).get('duration_s')
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
            raise ValueError('部分事件缺少有效视频时长。')
        events, ids = [], set()
        for index, event in enumerate(partial['events']):
            item, _ = stage_contracts.validate_event(event, f'partial_analysis.events[{index}]', duration)
            identity = require_text(item, 'observation_id', f'partial_analysis.events[{index}]')
            if identity in ids:raise ValueError('部分事件画面 ID 重复，未重新绑定。')
            ids.add(identity);events.append(item)
        record['analysis'] = {**partial, 'events': events}
        record['partial_input'] = True
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
    return media_processing.segment_windows(duration)


def clip_video(path, output, start_s, end_s):
    return media_processing.clip_video(path, output, start_s, end_s, runner=run, max_video=MAX_VIDEO)


def model_request_count(record):
    """A failed local preprocessing segment is not a model request."""
    parts = record.get('segments') if isinstance(record.get('segments'), list) else [record]
    return sum(len(item['api_attempts']) if isinstance(item.get('api_attempts'), list)
               else int(bool(item.get('raw_response'))) for item in parts)


def analyze_segment(path, folder, core_start, core_end, clip_start, clip_end,
                    prompt, key, parent, depth=0):
    model = parent.get('model') or 'qwen3-vl-plus'
    mode = parent.get('mode') or 'direct'
    request_mode = 'frames' if mode == 'direct' and clip_end-clip_start < 5. else mode
    fps = parent.get('fps') or 2.
    output = folder / f'segment-{len(parent["segments"])+1}.mp4'
    segment = {'core_start_s': core_start, 'core_end_s': core_end,
               'clip_start_s': clip_start, 'clip_end_s': clip_end,
               'sent_audio': False, 'fps': fps, 'status': 'failed',
               'preprocessing_status': 'processing'}
    segment.update(requested_mode=mode,mode=request_mode)
    if request_mode != mode:
        segment['input_fallback_reason'] = '短片段不足 5 秒，调用前改用顺序抽帧，避免视频接口最小时长限制。'
    parent['segments'].append(segment)
    try:
        source_metadata = parent['metadata']
        full_source = (source_metadata['duration_s'] <= 25. and clip_start == 0.
                       and abs(clip_end - source_metadata['duration_s']) <= 1e-6
                       and (request_mode == 'frames' or source_metadata.get('video_codec') == 'h264'))
        if full_source:
            segment['preprocessing_method'] = ('original_frames' if request_mode == 'frames' else
                'stream_copy_without_audio' if source_metadata['has_audio'] else 'original_video')
            clip, metadata, method = prepare_full_video(path, output, source_metadata, request_mode)
        else:
            segment['preprocessing_method'] = 'segment_transcode'
            clip_video(path, output, clip_start, clip_end)
            clip, metadata, method = output, probe(output), 'segment_transcode'
        if not clip.is_file() or not 0 < clip.stat().st_size <= MAX_VIDEO:
            raise MediaProcessingError('verify', code='invalid_output', message='预处理视频为空或超过 30 MiB，未调用模型。')
        segment.update(preprocessing_method=method, preprocessing_status='success', metadata=metadata,
                       clip_sha256=hashlib.sha256(clip.read_bytes()).hexdigest(), sent_video_bytes=clip.stat().st_size)
        if request_mode == 'frames':
            frame_dir = folder / f'frames-{len(parent["segments"])}'
            frame_dir.mkdir()
            frames = extract(clip, frame_dir, metadata['duration_s'], fps)
            url, body, _ = build_request(model, 'frames', fps, '', frames, metadata, prompt)
        else:
            data = base64.b64encode(clip.read_bytes()).decode()
            url, body, _ = build_request(model, 'direct', fps, data, [], metadata, prompt)
        segment['actual_prompt'] = body['messages'][0]['content'][0]['text']
        output = call_v13_model(url, body, key, segment)
        analysis = validate_stage_events(output, metadata['duration_s'])
        segment['analysis'] = analysis
        segment['validation_warnings'] = analysis.get('validation_warnings', [])
        segment['status'] = 'success'
    except TruncatedOutputError as error:
        segment['error'] = str(error)
        if depth >= 1 or core_end - core_start <= 6.:
            raise
        middle = (core_start + core_end) / 2.
        recovered = (analyze_segment(path, folder, core_start, middle, max(0., core_start - 1.), middle,
                                prompt, key, parent, depth + 1)
                + analyze_segment(path, folder, middle, core_end, max(0., middle - 1.), core_end,
                                  prompt, key, parent, depth + 1))
        segment['recovered_by_split'] = True
        return recovered
    except (ValueError, KeyError, IndexError, TypeError, HTTPError, URLError) as error:
        segment['error'] = str(error)
        if isinstance(error, MediaProcessingError):
            segment.update(media_error=error.detail, preprocessing_status='failed')
        if isinstance(error, ContractError):
            segment['validation_error'] = error.detail
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
    segment['mapped_events'] = events
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
    record['segment_policy'] = {'target_core_seconds':20,'merge_tail_below_seconds':5,
                                'short_direct_input':'frames_before_call'}
    record['segment_plan'] = [{'start_s': start, 'end_s': end}
                              for start, end, _, _ in segment_windows(metadata['duration_s'])]
    for start, end, clip_start, clip_end in segment_windows(metadata['duration_s']):
        analyze_segment(path, folder, start, end, clip_start, clip_end, prompt, key, record)
    summarize_event_segments(record, complete=True)


def summarize_event_segments(record, complete=False):
    """Retain usable segments and reported costs even when a later segment fails."""
    metadata = record['metadata']
    successful = [item for item in record['segments'] if item['status'] == 'success']
    all_events = [event for item in successful for event in item.get('mapped_events', [])]
    all_events, record['deduplicated_overlap_events'] = deduplicate_segment_events(all_events)
    summaries = [f"约{item['core_start_s']:g}–{item['core_end_s']:g}秒：{item['analysis']['summary']}"
                 for item in successful]
    limitations = (['长视频分段识别；相邻片段重叠约 1 秒，事件按代表画面归入唯一时间段。']
                   if metadata['duration_s'] > 25. else ['短视频按完整画面时间轴识别；图片或视频输入均不分析音轨。'])
    if any(item.get('input_fallback_reason') for item in record['segments']):
        limitations.append('不足 5 秒的短片段在调用前改用顺序抽帧；不含声音，实际输入方式见各片段记录。')
    for item in successful:
        limitations.extend(f"{item['clip_start_s']:g}秒片段：{value}" for value in item['analysis']['limitations'])
    warnings = [{**warning, 'core_start_s': item['core_start_s'], 'core_end_s': item['core_end_s']}
                for item in successful for warning in item.get('validation_warnings', [])]
    record['validation_warnings'] = warnings
    record['event_validation_summary'] = {
        key: sum(item['analysis'].get('validation_summary', {}).get(key, 0) for item in successful)
        for key in ('returned_count', 'accepted_count', 'rejected_count')}
    if not complete:
        limitations.insert(0, '仅包含已完成片段，不代表全片分析成功；可选择这些事件继续第二步，失败区间未补造。')
    if successful:
        analysis = validate_stage_events(json.dumps({'summary': '；'.join(summaries),
                                                           'events': all_events, 'limitations': limitations},
                                                          ensure_ascii=False), metadata['duration_s'])
        record['analysis' if complete else 'partial_analysis'] = analysis
    # Unknown usage is not zero. Keep complete totals and known subtotals separate.
    calls = [item for item in record['segments'] if model_request_count(item)]
    usage, subtotal = {}, {}
    for name in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        values = [(item.get('usage') or {}).get(name) for item in calls]
        known = [v for v in values if type(v) in (int, float) and math.isfinite(v) and v >= 0]
        if known:
            subtotal[name] = sum(known)
        if values and len(known) == len(values):
            usage[name] = sum(known)
    record.update(usage=usage, usage_reported_subtotal=subtotal,
                  usage_incomplete=bool(calls) and len(usage) < 3)
    boundaries = sorted({0., metadata['duration_s'], *(v for item in record['segments']
                         for v in (item['core_start_s'], item['core_end_s']))})
    ranges = []
    for start, end in zip(boundaries, boundaries[1:]):
        matching = [item for item in record['segments'] if item['core_start_s'] <= start and item['core_end_s'] >= end]
        state = 'success' if any(item['status'] == 'success' for item in matching) else 'failed' if matching else 'unprocessed'
        ranges.append({'start_s': start, 'end_s': end, 'status': state})
    record['segment_coverage'] = {'complete': complete, 'ranges': ranges}
    record['video_tokens'] = sum(item.get('video_tokens') or 0 for item in record['segments'])
    record['audio_tokens'] = 0
    record['audio_verification'] = 'not_sent'
    record['media_verification'] = 'confirmed' if record['video_tokens'] else 'unreported'
    record['request_bytes'] = sum(item.get('request_bytes') or 0 for item in record['segments'])
    record['sent_video_bytes'] = sum(item.get('sent_video_bytes') or 0 for item in record['segments'])
    record['api_roundtrip_s'] = round(sum(item.get('api_roundtrip_s') or 0 for item in record['segments']), 3)
    if complete:
        record['finish_reason'] = 'stop'
        record['status'] = 'success'


@runtime_config
def analyze_events(raw, video_name, version='v13', model='qwen3-vl-plus', mode='direct', fps=2.):
    if model not in runtime_models() or mode not in ('direct', 'frames') or fps not in (0.5, 1., 2.):
        raise ValueError('无效的模型、输入方式或采样率。请在页面设置中配置模型。')
    key = settings.get('BEEROUTE_API_KEY')
    if not key:
        raise ValueError('尚未配置 BEEROUTE_API_KEY。')
    if not raw or len(raw) > MAX_VIDEO:
        raise ValueError('视频须为非空 MP4，大小不超过 30 MiB。')
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
            if version in ('v14', 'v22'):
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
    except MediaProcessingError as error:
        record.update(error=str(error), media_error=error.detail)
    except ContractError as error:
        record.update(error=str(error), validation_error=error.detail, validation_status='failed')
    except HTTPError as error:
        record['error'] = f'模型 API HTTP {error.code}。'
        record['api_error_detail'] = error.read().decode('utf-8', errors='replace')[:2000] or record.get('api_error_detail', '')
    except URLError:
        record['error'] = '模型 API 网络连接失败。'
    except (ValueError, KeyError, IndexError, TypeError, subprocess.TimeoutExpired, TimeoutError) as error:
        record['error'] = str(error)
    if record.get('segments') and record['status'] != 'success':
        summarize_event_segments(record)
    if record.get('analysis', {}).get('validation_warnings'):
        record['validation_warnings'] = record['analysis']['validation_warnings']
    if record['status'] == 'success':
        record['validation_status'] = 'passed_with_warnings' if record.get('validation_warnings') else 'passed'
    record['total_s'] = round(time.perf_counter() - started, 3)
    record['model_request_count'] = model_request_count(record)
    save_record(record)
    return record


def revalidate_opportunity_record(record_id):
    """Produce an explicitly local derived record; never retry a model or rewrite history."""
    projects.identifier(record_id)
    paths = saved_record_paths(record_id)
    if len(paths) != 1:
        raise ValueError('待复核记录不存在或不唯一。')
    source_bytes = paths[0].read_bytes()
    source = json.loads(source_bytes)
    if source.get('stage') != 'opportunities' or source.get('record_id') != record_id:
        raise ValueError('只能重新校验机会分析记录。')
    if source.get('finish_reason') not in ('stop', 'STOP'):
        raise ValueError('原模型响应未正常结束，不能将截断或失败响应恢复为成功。')
    parent = stage_record(source.get('parent_record_id'))
    if parent['video_sha256'] != source.get('video_sha256'):
        raise ValueError('来源视频与事件记录不一致，不能恢复。')
    observations = source.get('observations')
    selected = source.get('selected_event_ids')
    if (not isinstance(observations, list) or not observations or not isinstance(selected, list)
            or len(observations) != len(selected) or len(set(selected)) != len(selected)
            or {o['observation_id'] for o in observations} != set(selected)
            or not set(selected) <= {e['observation_id'] for e in parent['analysis']['events']}):
        raise ValueError('原审核画面映射不完整，不能猜测或恢复定位。')
    for item in observations:
        value = item.get('frame_time_s')
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= source['metadata']['duration_s']:
            raise ValueError('原始画面时间无效，不能恢复。')
    analysis = validate_stage_opportunities(source.get('raw_text'), observations)
    keys = ('parent_record_id', 'parent_record_file', 'model', 'mode', 'fps', 'video_name', 'video_sha256',
            'video_bytes', 'metadata', 'prompt_file', 'prompt', 'actual_prompt', 'selected_event_ids', 'observations', 'frame_times_s')
    record = {k: source[k] for k in keys if k in source}
    record.update(record_id=secrets.token_hex(16), stage='opportunities', status='success',
                  record_kind='local_revalidation', model_call_made=False,
                  created_at=datetime.now(timezone.utc).isoformat(), source_record_id=record_id,
                  source_record_file=paths[0].relative_to(ROOT).as_posix(),
                  source_record_sha256=hashlib.sha256(source_bytes).hexdigest(),
                  source_status=source.get('status'), source_error=source.get('error'),
                  raw_text=source['raw_text'], raw_response_source=paths[0].relative_to(ROOT).as_posix(),
                  analysis=analysis, validation_warnings=analysis['warnings'],
                  validation_status='passed_with_warnings' if analysis['warnings'] else 'passed',
                  usage={}, api_attempts=[], total_s=0, cost=0,
                  cost_note='仅本地重新校验，未发起新模型调用；原调用用量见 source_record_file。')
    save_record(record)
    return record


@runtime_config
def suggest_opportunities(raw, video_name, record_id, selected_ids, version=None, model='qwen3-vl-plus'):
    if not selected_ids or len(selected_ids) != len(set(selected_ids)):
        raise ValueError('请至少审核并选择一条不重复的事件。')
    parent = stage_record(record_id)
    if not raw or len(raw) > MAX_VIDEO or hashlib.sha256(raw).hexdigest() != parent['video_sha256']:
        raise ValueError('第二阶段视频与事件记录不一致，未调用模型。')
    by_id = {event['observation_id']: event for event in parent['analysis']['events']}
    if any(item not in by_id for item in selected_ids):
        raise ValueError('选择了不属于事件记录的画面。')
    if model not in runtime_models():
        raise ValueError('无效的模型。请在页面设置中配置模型。')
    key = settings.get('BEEROUTE_API_KEY')
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
    if parent.get('partial_input'):
        record.update(partial_event_input=True, parent_status=parent.get('status'),
                      parent_segment_coverage=parent.get('segment_coverage'))
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
            if parent.get('partial_input'):
                content.append({'type':'text','text':'第一步仅完成部分视频区间。本次只分析已选择事件和对应图片，不推测失败或未处理区间，也不声称覆盖全片。'})
            reviewed_only = int(prompt_path.parent.name[1:]) >= 21
            if reviewed_only:
                allowed_ids = [e['observation_id'] for e in parent['analysis']['events'] if e['observation_id'] in selected_ids]
                output_schema = {'type': 'object', 'required': ['interaction_points'], 'additionalProperties': False,
                    'properties': {'interaction_points': {'type': 'array', 'items': {
                        'type': 'object', 'required': ['observation_id', 'opportunity', 'reason'], 'additionalProperties': False,
                        'properties': {'observation_id': {'type': 'string', 'enum': allowed_ids},
                                       'opportunity': {'type': 'string', 'minLength': 1},
                                       'reason': {'type': 'string', 'minLength': 1}}}}}}
                contract = {'allowed_observation_ids': allowed_ids, 'output_schema': output_schema}
                content.append({'type': 'text', 'text': '本次画面身份与输出字段约定（只复制实际标签，不重新编号）：\n'
                                + json.dumps(contract, ensure_ascii=False)})
                record.update(opportunity_input_contract=contract, context_policy='reviewed_events_only',
                              schema_enforcement='prompt_schema_with_local_validation_not_strict_api_schema')
            for event in parent['analysis']['events']:
                if event['observation_id'] not in selected_ids:
                    continue
                frame = min(frames, key=lambda item: abs(item['time_s'] - event['observation_s']))
                preceding = [x['description'] for x in parent['analysis']['events']
                             if x['observation_id'] != event['observation_id'] and x['end_s'] <= event['observation_s']
                             and (not reviewed_only or x['observation_id'] in selected_ids)]
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
            base = settings.get('BEEROUTE_BASE_URL', 'https://beeroute.ai/v1').rstrip('/')
            if not base.startswith('https://'):
                raise ValueError('模型 API 地址必须使用 HTTPS。')
            record['preprocess_s'] = round(time.perf_counter() - started, 3)
            output = call_v13_model(base + '/chat/completions', body, key, record)
            record['analysis'] = validate_stage_opportunities(output, observations)
            record['validation_warnings'] = record['analysis']['warnings']
            record['validation_status'] = 'passed_with_warnings' if record['validation_warnings'] or record.get('request_warnings') else 'passed'
            record.update(status='success', cost=None, cost_note='未设置单价；按返回的 usage 核算。')
    except MediaProcessingError as error:
        record.update(error=str(error), media_error=error.detail)
    except ContractError as error:
        record.update(error=str(error), validation_error=error.detail, validation_status='failed',
                      validation_warnings=error.detail.get('warnings', []))
    except HTTPError as error:
        record['error'] = f'模型 API HTTP {error.code}。'
        record['api_error_detail'] = error.read().decode('utf-8', errors='replace')[:2000] or record.get('api_error_detail', '')
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
    return prompt_catalog.load_game_catalog(ROOT, GAME_IDS)


def catalog_text(catalog):
    return prompt_catalog.catalog_text(catalog)


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
    return stage_contracts.validate_gameplay(text, points, catalog)


def save_gameplay_frame(record, point_id, data):
    """Keep binary JPEGs outside JSON; the request alone uses base64."""
    model = re.sub(r'[^a-zA-Z0-9_.-]+', '_', record['model'])
    folder = (ROOT / 'results' / model / 'frames' / record['record_id']).resolve()
    if not folder.is_relative_to((ROOT / 'results').resolve()):
        raise ValueError('画面保存路径无效。')
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{point_id}.jpg'
    path.write_bytes(base64.b64decode(data, validate=True))
    return path.relative_to(ROOT).as_posix()


def read_gameplay_frame(record_id, point_id):
    if not isinstance(point_id, str) or not re.fullmatch(r'p[1-9]\d*', point_id):
        raise ValueError('画面 ID 无效。')
    record = load_stage_result(record_id, 'gameplay', '玩法记录')
    observation = next((o for o in record.get('observations', []) if o['point_id'] == point_id), None)
    if observation is None:
        raise ValueError('画面不存在。')
    if observation.get('frame_file'):
        path = (ROOT / observation['frame_file']).resolve()
        if not path.is_relative_to((ROOT / 'results').resolve()) or path.suffix.lower() != '.jpg':
            raise ValueError('画面路径无效。')
        data = path.read_bytes()
    else:
        # Read historical inline previews without rewriting the historical JSON.
        data = base64.b64decode(observation.get('preview_jpeg_base64', ''), validate=True)
    if not data or hashlib.sha256(data).hexdigest() != observation['frame_sha256']:
        raise ValueError('画面缺失或哈希不符。')
    return data


@runtime_config
def suggest_gameplay(raw, video_name, record_id, selected_ids, version=None, model='qwen3-vl-plus', times=None):
    if not selected_ids or len(selected_ids) != len(set(selected_ids)):
        raise ValueError('请至少选择一个不重复的交互点。')
    parent = load_stage_result(record_id, 'opportunities', '机会记录')
    if not raw or len(raw) > MAX_VIDEO or hashlib.sha256(raw).hexdigest() != parent['video_sha256']:
        raise ValueError('玩法推荐使用的视频与机会记录不一致，未调用模型。')
    points = indexed_points(parent)
    by_id = {item['point_id']: item for item in points}
    if any(item not in by_id for item in selected_ids):
        raise ValueError('选择了不属于机会记录的交互点。')
    times = {} if times is None else times
    if not isinstance(times, dict) or any(item not in selected_ids for item in times):
        raise ValueError('调整时间须引用所选交互点。')
    for point_id in selected_ids:
        value = times.get(point_id, by_id[point_id]['time_s'])
        gameplay_design.number(value, 0, parent['metadata']['duration_s'], '画面位置')
        by_id[point_id]['original_time_s'] = by_id[point_id]['time_s']
        by_id[point_id]['requested_time_s'] = value
    if model not in runtime_models():
        raise ValueError('无效的模型。请在页面设置中配置模型。')
    key = settings.get('BEEROUTE_API_KEY')
    if not key:
        raise ValueError('尚未配置 BEEROUTE_API_KEY。')
    prompt_path, prompt = load_gameplay_prompt(version)
    design_v2 = prompt_path.parent.name == 'v19'
    recommendation_only = int(prompt_path.parent.name[1:]) >= 20
    template_snapshot = gameplay_design.templates() if design_v2 else None
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
    record['catalog_snapshot'] = catalog
    if design_v2:
        record.update(schema_version=2, template_snapshot=template_snapshot,
                      template_file='prompts/games/templates.json')
    elif recommendation_only:
        record.update(schema_version=3, analysis_focus='gameplay_recommendation',
                      schema_enforcement='local_core_strict_auxiliary_warnings',
                      output_schema=gameplay_recommendations.output_schema(
                          [by_id[item] for item in selected_ids], catalog))
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
            if design_v2:
                content[0]['text'] += '\n游戏设计模板（新增控件，不是视频事实）：\n' + json.dumps(template_snapshot, ensure_ascii=False)
            if recommendation_only:
                content[0]['text'] += '\n输出字段约定（JSON Schema；由本地验证，辅助说明异常单列警告）：\n' + json.dumps(record['output_schema'], ensure_ascii=False)
            for point_id in selected_ids:
                point = by_id[point_id]
                frame = min(frames, key=lambda item: abs(item['time_s'] - point['requested_time_s']))
                event = events_by_id.get(point['observation_id'])
                if event:
                    event_text = f"原定位事件（待核实，调整后可能失效）：{event['description']}\n原事件依据（待核实）：{event['evidence']}"
                else:
                    event_text = '当前事件描述：无'
                content.append({'type': 'text', 'text':
                                f"point_id={point['point_id']}；observation_id={point['observation_id']}；"
                                f"实际画面时间={frame['time_s']:.6f}秒；请求画面位置={point['requested_time_s']:.6f}秒。\n"
                                f"旧机会（待核实）：{point['opportunity']}\n旧理由（待核实）：{point['reason']}\n{event_text}"})
                content.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + frame['data']}})
                observations.append({'point_id': point['point_id'], 'observation_id': point['observation_id'],
                                     'original_time_s': point['original_time_s'],
                                     'requested_time_s': point['requested_time_s'], 'frame_time_s': frame['time_s'],
                                     'frame_sha256': hashlib.sha256(base64.b64decode(frame['data'])).hexdigest()})
                if design_v2 or recommendation_only:
                    observations[-1]['frame_file'] = save_gameplay_frame(record, point_id, frame['data'])
                    point['time_s'] = frame['time_s']
            record['observations'] = observations
            record['frame_times_s'] = [item['frame_time_s'] for item in observations]
            record['actual_prompt'] = '\n\n'.join(item['text'] for item in content if item['type'] == 'text')
            body = {'model': model, 'messages': [{'role': 'user', 'content': content}], 'max_tokens': 8192 if design_v2 else 4096}
            if str(model).startswith('qwen'):
                body['enable_thinking'] = False
            base = settings.get('BEEROUTE_BASE_URL', 'https://beeroute.ai/v1').rstrip('/')
            if not base.startswith('https://'):
                raise ValueError('模型 API 地址必须使用 HTTPS。')
            record['preprocess_s'] = round(time.perf_counter() - started, 3)
            output = call_v13_model(base + '/chat/completions', body, key, record)
            if recommendation_only:
                record['analysis'] = gameplay_recommendations.validate(
                    output, [by_id[item] for item in selected_ids], catalog)
            elif design_v2:
                record['analysis'] = gameplay_design.validate_recommendations(
                    output, [by_id[item] for item in selected_ids], catalog, template_snapshot)
            else:
                record['analysis'] = validate_gameplay(output, [by_id[item] for item in selected_ids], catalog)
            record['validation_warnings'] = record['analysis'].get('warnings', [])
            record['validation_status'] = ('passed_with_warnings' if record['validation_warnings'] or record.get('request_warnings') else 'passed')
            record.update(status='success', cost=None, cost_note='未设置单价；按返回的 usage 核算。')
    except MediaProcessingError as error:
        record.update(error=str(error), media_error=error.detail)
    except ContractError as error:
        record.update(error=str(error), validation_error=error.detail, validation_status='failed')
    except HTTPError as error:
        record['error'] = f'模型 API HTTP {error.code}。'
        record['api_error_detail'] = error.read().decode('utf-8', errors='replace')[:2000] or record.get('api_error_detail', '')
    except URLError:
        record['error'] = '模型 API 网络连接失败。'
    except (ValueError, KeyError, IndexError, TypeError, OSError, subprocess.TimeoutExpired, TimeoutError) as error:
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
    if gameplay.get('schema_version') == 3:
        saved = gameplay_recommendations.build_brief(payload, opportunity, gameplay, points)
        record = {'schema_version': 3, 'record_id': secrets.token_hex(16), 'stage': 'gameplay_brief',
                  'parent_record_id': gameplay['record_id'], 'parent_record_file': gameplay['record_file'],
                  'opportunity_record_id': opportunity['record_id'], 'gameplay_record_id': gameplay['record_id'],
                  'created_at': datetime.now(timezone.utc).isoformat(), 'model': gameplay['model'], 'mode': 'brief',
                  'video_name': opportunity['video_name'], 'video_sha256': opportunity['video_sha256'],
                  'prompt_file': gameplay['prompt_file'], 'prompt': gameplay['prompt'],
                  'catalog_snapshot': gameplay['catalog_snapshot'], 'analysis_focus': 'gameplay_recommendation',
                  'status': 'success', 'selections': saved, 'cost': None,
                  'cost_note': '本地玩法选择保存，未调用模型。'}
        save_record(record)
        return record
    if gameplay.get('schema_version') == 2:
        catalog = gameplay['catalog_snapshot']
        names = {item['id']: item['name'] for item in catalog['types']}
        saved = gameplay_design.build_brief(payload, opportunity, gameplay, points, names)
        record = {'schema_version': 2, 'record_id': secrets.token_hex(16), 'stage': 'gameplay_brief',
                  'parent_record_id': gameplay['record_id'], 'parent_record_file': gameplay['record_file'],
                  'opportunity_record_id': opportunity['record_id'], 'gameplay_record_id': gameplay['record_id'],
                  'created_at': datetime.now(timezone.utc).isoformat(), 'model': gameplay['model'], 'mode': 'brief',
                  'video_name': opportunity['video_name'], 'video_sha256': opportunity['video_sha256'],
                  'prompt_file': gameplay['prompt_file'], 'prompt': gameplay['prompt'],
                  'catalog_snapshot': catalog, 'template_snapshot': gameplay['template_snapshot'],
                  'review_actor': payload.get('review_actor', 'local_editor'),
                  'rules_authority': 'parameters 与服务端生成的 rules_text；设计文字须与其核对',
                  'status': 'success', 'selections': saved, 'cost': None,
                  'cost_note': '本地人工选择保存，未调用模型。'}
        save_record(record)
        return record
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
              'created_at': datetime.now(timezone.utc).isoformat(), 'model': gameplay.get('model', 'qwen3-vl-plus'), 'mode': 'brief',
              'video_name': opportunity.get('video_name') or 'video', 'video_sha256': opportunity.get('video_sha256'),
              'prompt_file': 'prompts/games/catalog.json', 'status': 'success', 'selections': saved,
              'resume': RESUME_INTENT, 'cost': None, 'cost_note': '玩法描述由本地选择保存，未调用模型。'}
    save_record(record)
    return record


@runtime_config
def analyze(raw, model, mode, fps, video_name, prompt_version=None):
    if model not in runtime_models() or mode not in ('direct', 'frames') or fps not in (0.5, 1., 2.):
        raise ValueError('无效的模型、模式或采样频率。请在页面设置中配置模型。')
    keyname = PROVIDER_KEYS[runtime_models()[model]]
    key = settings.get(keyname)
    if not key:
        raise ValueError(f'尚未配置 {keyname}。没有调用模型，也没有生成分析结果。')
    if not raw or len(raw) > MAX_VIDEO:
        raise ValueError('视频须为非空 MP4，大小不超过 30 MiB。')
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
            if len(payload) > MAX_MODEL_REQUEST:
                raise ValueError('编码后请求过大，请压缩视频或降低抽帧率。')
            record['request_bytes'] = len(payload)
            record['request_options'] = {'fps': fps, 'response_format': body.get('response_format')}
            record['api_attempts'] = []
            record['preprocess_s'] = round(time.perf_counter()-start, 3)
            record['actual_prompt'] = body['contents'][0]['parts'][0]['text'] if runtime_models()[model] == 'gemini' else body['messages'][0]['content'][0]['text']
            headers = {'Content-Type': 'application/json'}
            headers.update({'x-goog-api-key': key} if runtime_models()[model] == 'gemini' else {'Authorization': 'Bearer ' + key})
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
            if runtime_models()[model] == 'gemini':
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
    except MediaProcessingError as error:
        record.update(error=str(error), media_error=error.detail)
    except HTTPError as error:
        record['error'] = f'模型 API HTTP {error.code}，请检查凭据、模型权限或额度。'
    except URLError:
        record['error'] = '模型 API 网络连接失败，请检查访问条件。'
    except (ValueError, KeyError, IndexError, TypeError, subprocess.TimeoutExpired, TimeoutError) as error:
        record['error'] = str(error)
    record['total_s'] = round(time.perf_counter()-start, 3)
    save_record(record)
    return record




from .web.server import Handler


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Local video interaction workbench')
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8765')))
    port = parser.parse_args().port
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    where = '、'.join(MODEL_NAMES) or '（未设置 BEEROUTE_MODELS）'
    print(f'视频理解工作台：http://127.0.0.1:{port}')
    print(f'可选模型：{where}')
    print('页面上选择一个模型后分析，结果写入 results/ 下的同名文件夹。')
    print('Ctrl+C 退出')
    server.serve_forever()
