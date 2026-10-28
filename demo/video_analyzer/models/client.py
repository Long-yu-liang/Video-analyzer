"""Model transport, JSON Object fallback, raw responses and usage verification."""
import json
import time
from urllib.request import Request
from urllib.error import HTTPError
from ..paths import MAX_MODEL_REQUEST
from .. import settings


class TruncatedOutputError(ValueError):
    """The model reached its output cap; keep its raw response and retry smaller media."""


def call_v13_model(url, body, key, record, opener, count_modalities, output_parser):
    body = dict(body)
    for name, value in settings.analysis_options(body.get('model', '')).items():
        body.setdefault(name, value)
    if str(body.get('model', '')).lower().startswith('qwen'):
        if settings.requested_thinking(body) is False:
            # Vision input supports JSON Object, not guaranteed strict JSON Schema.
            body.setdefault('response_format', {'type': 'json_object'})
    record['requested_response_format'] = body.get('response_format')
    record['api_attempts'] = []
    started = time.perf_counter()
    for attempt in range(2):
        payload = json.dumps(body).encode()
        if len(payload) > MAX_MODEL_REQUEST:
            raise ValueError('编码后请求过大，请减少审核画面或压缩视频。')
        record['request_bytes'] = len(payload)
        record['request_options'] = {'fps': record['fps'], 'response_format': body.get('response_format'),
                                     'max_tokens': body.get('max_tokens'), 'enable_thinking': body.get('enable_thinking'),
                                     'chat_template_kwargs': body.get('chat_template_kwargs')}
        entry = {'response_format': body.get('response_format'), 'request_bytes': len(payload)}
        record['api_attempts'].append(entry)
        try:
            with opener(Request(url, data=payload, headers={'Content-Type': 'application/json',
                                                            'Authorization': 'Bearer ' + key}), timeout=300) as response:
                result = json.load(response)
            break
        except HTTPError as error:
            detail = error.read().decode('utf-8', errors='replace')[:2000]
            record['api_error_detail'] = detail
            entry.update(http_error=error.code, error_detail=detail)
            lower = detail.lower()
            names_format = any(name in lower for name in ('response_format', 'json_object', 'json mode', 'json_mode'))
            explicitly_unsupported = any(term in lower for term in (
                'unsupported', 'not supported', 'not support', 'unknown parameter', 'unknown field',
                'unrecognized', '不支持', '未支持'))
            if (attempt != 0 or error.code not in (400, 422)
                    or body.get('response_format') != {'type': 'json_object'}
                    or not names_format or not explicitly_unsupported):
                raise
            del body['response_format']
            record.update(json_mode_fallback=True, json_mode_fallback_reason=detail)
            record.setdefault('request_warnings', []).append({
                'path': 'request.response_format', 'code': 'json_mode_unsupported',
                'message': '网关明确拒绝 JSON Object，已回退一次为提示词约束输出；本地校验仍执行。'})
    record['api_roundtrip_s'] = round(time.perf_counter() - started, 3)
    record['raw_response'] = result
    choice = result['choices'][0]
    content = choice['message'].get('content')
    output = ''.join(p.get('text', '') for p in content if isinstance(p, dict)) if isinstance(content, list) else (content or '')
    record['raw_text'] = output
    record['usage'] = result.get('usage', {})
    record['finish_reason'] = choice.get('finish_reason')
    modalities = count_modalities(record['usage'])
    record['media_tokens'] = sum(modalities.values()) if modalities is not None else None
    record['video_tokens'] = modalities['image'] + modalities['video'] if modalities is not None else None
    record['audio_tokens'] = modalities['audio'] if modalities is not None else None
    record['audio_verification'] = 'not_sent'
    record['media_verification'] = 'confirmed' if record['video_tokens'] else 'unreported'
    if modalities is not None and record['video_tokens'] == 0:
        raise ValueError('用量中没有图像或视频 Token，不能确认模型看到了画面。')
    if record['finish_reason'] == 'length':
        details = record['usage'].get('completion_tokens_details') or {}
        reasoning = details.get('reasoning_tokens') or 0
        completion = record['usage'].get('completion_tokens') or 0
        thinking_limited = completion > 0 and reasoning >= completion * .8
        record['truncation_diagnosis'] = {
            'max_tokens': body.get('max_tokens'), 'reasoning_tokens': reasoning,
            'completion_tokens': completion, 'content_chars': len(output),
            'thinking_budget_exhausted': thinking_limited,
            'requested_enable_thinking': settings.requested_thinking(body)}
        message = f"模型输出达到 {body.get('max_tokens')} token 上限并被截断，原始响应已保留。"
        if thinking_limited:
            message += f"其中思考消耗 {reasoning} Token；请检查网关思考设置或提高分析输出额度，未自动拆段重试。"
        error = TruncatedOutputError(message)
        error.thinking_budget_exhausted = thinking_limited
        raise error
    if record['finish_reason'] not in ('stop', 'STOP'):
        raise ValueError('模型未正常结束，原始输出已保留。')
    return output_parser(output)
