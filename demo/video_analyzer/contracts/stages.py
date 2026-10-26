"""Facts, opportunity and legacy gameplay response validation."""
import math
from .output import ContractError, MISSING, expected, parse_json, require_text
from ..gameplay.recommendations import FIELDS as GAMEPLAY_FIELDS

EVENT_TIME_TOLERANCE_S = 0.1  # Rounding tolerance, not a claim of model timestamp accuracy.


def validate_event(event, path, duration):
    if not isinstance(event, dict):
        raise expected(path, '对象', event)
    event = dict(event)
    corrections = []
    for key in ('start_s', 'end_s', 'observation_s'):
        value = event.get(key)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise expected(path+'.'+key, '有限数字', event.get(key, MISSING))
    if event['start_s'] > event['end_s']:
        raise ContractError(path+'.end_s', '事件结束时间早于开始时间，不自动交换。', 'invalid_time')
    original_target = min(event['end_s'], max(event['start_s'], event['observation_s']))
    if abs(original_target-event['observation_s']) > EVENT_TIME_TOLERANCE_S+1e-9:
        raise ContractError(path+'.observation_s', '代表画面时间必须在该事件 start_s–end_s 区间内。', 'invalid_time')
    def correct(key, target):
        original = event[key]
        if original == target:
            return
        corrections.append({'path': path+'.'+key, 'code': 'time_rounding_corrected',
            'action': 'clamp_to_boundary', 'original_value': original, 'corrected_value': target,
            'tolerance_s': EVENT_TIME_TOLERANCE_S,
            'message': f'{path}.{key} 从 {original:g} 秒收到边界 {target:g} 秒（容差 0.05 秒），原始响应未修改。'})
        event[key] = target
    for key in ('start_s', 'end_s', 'observation_s'):
        value = event[key]
        if value < -EVENT_TIME_TOLERANCE_S-1e-9 or value > duration+EVENT_TIME_TOLERANCE_S+1e-9:
            raise ContractError(path+'.'+key, f'时间 {value:g} 秒超出片长 {duration:g} 秒允许范围。', 'invalid_time')
        correct(key, min(duration, max(0., value)))
    observed = event['observation_s']
    target = min(event['end_s'], max(event['start_s'], observed))
    if abs(target-observed) > EVENT_TIME_TOLERANCE_S+1e-9:
        raise ContractError(path+'.observation_s', '代表画面时间必须在该事件 start_s–end_s 区间内。', 'invalid_time')
    correct('observation_s', target)
    for key in ('description', 'evidence', 'uncertainty'):
        require_text(event, key, path)
    return event, corrections


def validate_stage_events(text, duration):
    result = parse_json(text)
    if not isinstance(result, dict):
        raise expected('$', '对象', result)
    result.pop('validation_warnings', None)  # This metadata is produced by the validator only.
    for key, kind, label in (('summary', str, '字符串'), ('events', list, '数组')):
        if not isinstance(result.get(key), kind):
            raise expected(key, label, result.get(key, MISSING))
    # Normalize auxiliary data only; never change the saved raw model response.
    if 'limitations' not in result:
        result['limitations'] = []
        result['validation_warnings'] = [{
            'path': 'limitations', 'code': 'missing_auxiliary_field', 'action': 'default_empty_array',
            'message': '模型未返回 limitations；本地兼容为空数组，原始响应未修改。'}]
    elif not isinstance(result['limitations'], list):
        raise expected('limitations', '数组', result['limitations'])
    for index, item in enumerate(result['limitations']):
        if not isinstance(item, str):
            raise expected(f'limitations[{index}]', '字符串', item)
    if 'interaction_points' in result:
        raise ContractError('interaction_points', '不属于事件识别阶段，应只记录事实事件。', 'unexpected_field')
    events, rejected = [], []
    warnings = result.pop('validation_warnings', [])
    returned_count = len(result['events'])
    for index, event in enumerate(result['events']):
        path = f'events[{index}]'
        try:
            valid, corrections = validate_event(event, path, duration)
            events.append(valid)
            warnings.extend(corrections)
        except ContractError as error:
            rejected.append({'index': index, **error.detail, 'action': 'rejected_event'})
    warnings.extend(rejected)
    if returned_count and not events:
        error = ContractError(rejected[0]['path'], '所有返回事件均未通过校验；不是未发现事件。'+rejected[0]['message'], 'no_valid_events')
        error.detail.update(warnings=warnings, returned_count=returned_count, rejected_count=len(rejected))
        raise error
    events.sort(key=lambda item: (item['observation_s'], item['start_s']))
    for index, event in enumerate(events, 1):
        event['observation_id'] = f'e{index}'
    result['events'] = events
    if warnings:
        result['validation_warnings'] = warnings
    result['validation_summary'] = {'returned_count': returned_count, 'accepted_count': len(events), 'rejected_count': len(rejected)}
    return result


def validate_stage_opportunities(text, observations):
    result = parse_json(text)
    if not isinstance(result, dict):
        raise expected('$', '对象', result)
    if not isinstance(result.get('interaction_points'), list):
        raise expected('interaction_points', '数组', result.get('interaction_points', MISSING))
    by_id = {item['observation_id']: item for item in observations}
    points, warnings = [], []
    seen = set()
    for index, point in enumerate(result['interaction_points']):
        path = f'interaction_points[{index}]'
        try:
            if not isinstance(point, dict):
                raise expected(path, '对象', point)
            observation_id = require_text(point, 'observation_id', path)
            if observation_id not in by_id:
                failure = ContractError(path+'.observation_id',
                    f'引用 {observation_id!r}，但本次仅审核并发送了 {", ".join(by_id) or "无"}；该项未采用，未自动改绑画面。',
                    'unknown_observation')
                failure.detail.update(actual_value=observation_id, allowed_ids=list(by_id))
                raise failure
            opportunity = require_text(point, 'opportunity', path)
            reason = require_text(point, 'reason', path)
        except ContractError as error:
            warnings.append({**error.detail, 'action': 'rejected_item', 'message': str(error)+' 原始输出已保留。'})
            continue
        identity = (observation_id, opportunity)
        if identity in seen:
            warnings.append({'path': path, 'code': 'duplicate_opportunity', 'action': 'rejected_item',
                             'message': f'{path} 与同一画面的已有机会重复，该重复项未采用。'})
            continue
        seen.add(identity)
        source = by_id[observation_id]
        points.append({'observation_id': observation_id, 'event_id': source['observation_id'],
                       'time_s': source['frame_time_s'], 'opportunity': opportunity, 'reason': reason})
    if result['interaction_points'] and not points:
        failure = ContractError('interaction_points',
            '模型返回的候选均不可用，并非未发现机会。'+warnings[0]['message'], 'no_valid_opportunities')
        failure.detail.update(warnings=warnings, rejected_count=len(warnings), returned_count=len(result['interaction_points']))
        raise failure
    points.sort(key=lambda item: (item['time_s'], item['observation_id']))
    return {'interaction_points': points, 'limitations': [], 'warnings': warnings,
            'validation_summary': {'returned_count': len(result['interaction_points']),
                                   'accepted_count': len(points), 'rejected_count': len(warnings)}}


def validate_gameplay(text, points, catalog):
    result = parse_json(text)
    if not isinstance(result, dict):
        raise expected('$', '对象', result)
    if not isinstance(result.get('recommendations'), list):
        raise expected('recommendations', '数组', result.get('recommendations', MISSING))
    by_id = {item['point_id']: item for item in points}
    names = {item['id']: item['name'] for item in catalog['types']}
    recommendations = []
    seen = set()
    for index, item in enumerate(result['recommendations']):
        path = f'recommendations[{index}]'
        if not isinstance(item, dict):
            raise expected(path, '对象', item)
        point_id = require_text(item, 'point_id', path)
        game_id = require_text(item, 'game_id', path)
        if point_id not in by_id:
            raise ContractError(path+'.point_id', f'引用了未输入的交互点 {point_id!r}。', 'unknown_point')
        if point_id in seen:
            raise ContractError(path, f'同一交互点 {point_id!r} 出现重复推荐。', 'duplicate_recommendation')
        seen.add(point_id)
        if game_id not in names:
            raise ContractError(path+'.game_id', f'玩法 {game_id!r} 不在预设目录中。', 'unknown_game')
        for key in GAMEPLAY_FIELDS:
            require_text(item, key, path)
        source = by_id[item['point_id']]
        recommendations.append({
            'point_id': item['point_id'], 'observation_id': source['observation_id'],
            'event_id': source.get('event_id') or source['observation_id'], 'time_s': source['time_s'],
            'opportunity': source['opportunity'], 'reason': source['reason'], 'game_id': item['game_id'],
            'game_name': names[item['game_id']], **{key: item[key].strip() for key in GAMEPLAY_FIELDS}})
    recommendations.sort(key=lambda item: (item['time_s'], item['point_id']))
    return {'recommendations': recommendations, 'limitations': []}
