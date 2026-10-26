"""Model-specific payload construction and modality usage parsing."""
import os
import re
from .. import settings


def supports_video_audio(model):
    """Only these configured model families can analyze an MP4's audio track."""
    return model.startswith('gemini-') or model == 'qwen3.8-omni-flash'


def build_request(model, mode, fps, data, frames, metadata, prompt, models, supports_audio=None):
    supports_audio = supports_audio or supports_video_audio
    provider = models[model]
    audio = mode == 'direct' and metadata['has_audio'] and supports_audio(model)
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
        base = settings.get('DASHSCOPE_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1').rstrip('/')
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
            body['response_format'] = {'type': 'json_object'}
        base = settings.get('BEEROUTE_BASE_URL', 'https://beeroute.ai/v1').rstrip('/')
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


def extract_json(text):
    text = (text or '').strip()
    fenced = re.search(r'```(?:json)?\s*(.*)```', text, re.S | re.I)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find('{'), text.rfind('}')
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return text
