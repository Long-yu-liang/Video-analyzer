"""Historical single-stage output contracts, unchanged for existing records."""
import json
import math


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
