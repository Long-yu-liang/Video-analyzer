"""Day 3 recommendation-only contracts; execution rules are a later design step."""
import math
from ..contracts.output import ContractError, MISSING, abstentions, expected, parse_json, require_text

FIELDS = ('fit', 'player_goal', 'player_action', 'success', 'failure', 'feedback')


def output_schema(points, catalog):
    text = {'type': 'string', 'minLength': 1}
    point_id = {'type': 'string', 'enum': [p['point_id'] for p in points]}
    recommendation = {'type': 'object', 'properties': {
        'point_id': point_id, 'game_id': {'type': 'string', 'enum': [g['id'] for g in catalog['types']]},
        **{key: dict(text) for key in FIELDS}, 'title': dict(text), 'difference': dict(text)},
        'required': ['point_id', 'game_id', *FIELDS]}
    return {'type': 'object', 'required': ['recommendations'], 'properties': {
        'recommendations': {'type': 'array', 'maxItems': 2*len(points), 'items': recommendation},
        'abstentions': {'type': 'array', 'items': {'type': 'object', 'required': ['point_id', 'reason'],
            'properties': {'point_id': point_id, 'reason': dict(text)}}}}}


def validate(text, points, catalog):
    output = parse_json(text)
    if not isinstance(output, dict):
        raise expected('$', '对象', output)
    if not isinstance(output.get('recommendations'), list):
        raise expected('recommendations', '数组', output.get('recommendations', MISSING))
    by_id = {p['point_id']: p for p in points}
    names = {g['id']: g['name'] for g in catalog['types']}
    results, counts, fingerprints = [], {}, set()
    for index, item in enumerate(output['recommendations']):
        path = f'recommendations[{index}]'
        if not isinstance(item, dict):
            raise expected(path, '对象', item)
        point_id = require_text(item, 'point_id', path)
        game_id = require_text(item, 'game_id', path)
        if point_id not in by_id:
            raise ContractError(f'{path}.point_id', f'引用了未输入的交互点 {point_id!r}。', 'unknown_point')
        if game_id not in names:
            raise ContractError(f'{path}.game_id', f'玩法 {game_id!r} 不在预设目录中。', 'unknown_game')
        for key in FIELDS:
            require_text(item, key, path)
        counts[point_id] = counts.get(point_id, 0) + 1
        if counts[point_id] > 2:
            raise ContractError(path, f'交互点 {point_id} 超过最多两个玩法的上限。', 'too_many_options')
        fingerprint = (point_id, game_id, item['player_goal'].strip(), item['player_action'].strip())
        if fingerprint in fingerprints:
            raise ContractError(path, f'与交互点 {point_id} 的已有玩法重复。', 'duplicate_recommendation')
        fingerprints.add(fingerprint)
        result = {**by_id[point_id], **{k: item[k].strip() for k in FIELDS},
                  'option_id': f'{point_id}-o{counts[point_id]}', 'game_id': game_id,
                  'game_name': names[game_id], 'title': names[game_id]}
        for key in ('title', 'difference'):
            if isinstance(item.get(key), str) and item[key].strip():
                result[key] = item[key].strip()
        results.append(result)
    notes, warnings = abstentions(output, by_id, counts, fill_missing=False)
    handled = set(counts) | {note['point_id'] for note in notes}
    incomplete = [{'point_id': point_id, 'reason': '模型未返回推荐或有效的不推荐理由。'}
                  for point_id in by_id if point_id not in handled]
    for item in incomplete:
        warnings.append({'path':item['point_id'], 'code':'missing_point_result',
                         'message':item['point_id']+' 未完成：'+item['reason']})
    return {'recommendations': results, 'abstentions': notes, 'incomplete_points': incomplete,
            'coverage': {'requested_count':len(by_id), 'recommended_count':len(counts),
                         'abstained_count':len(notes), 'incomplete_count':len(incomplete),
                         'complete':not incomplete}, 'warnings': warnings, 'limitations': []}


def build_brief(payload, opportunity, gameplay, points):
    selections = payload.get('selections')
    if not isinstance(selections, list) or not all(isinstance(s, dict) for s in selections):
        raise ValueError('玩法选择格式无效。')
    ids = [s.get('point_id') for s in selections]
    if len(set(ids)) != len(ids) or set(ids) != set(gameplay['selected_point_ids']):
        raise ValueError('每个交互点须保存一个选择或放弃。')
    options = {r['option_id']: r for r in gameplay['analysis']['recommendations']}
    observations = {o['point_id']: o for o in gameplay['observations']}
    names = {g['id']: g['name'] for g in gameplay['catalog_snapshot']['types']}
    results = []
    for selection in selections:
        point_id = selection['point_id']
        status = selection.get('status')
        if status not in ('accepted', 'edited', 'discarded'):
            raise ValueError('请接受、确认修改或放弃该交互点。')
        observation = observations[point_id]
        requested = selection.get('adjusted_time_s', observation['requested_time_s'])
        if (type(requested) not in (int, float) or not math.isfinite(requested)
                or not 0 <= requested <= opportunity['metadata']['duration_s']):
            raise ValueError('画面位置无效。')
        if abs(requested - observation['requested_time_s']) > 1e-6:
            raise ValueError('画面位置已改变，请重新推荐。')
        reason = selection.get('decision_reason', '')
        if not isinstance(reason, str):
            raise ValueError('处理理由须为文字。')
        base = {**points[point_id], 'adjusted_time_s': requested,
                'frame_time_s': observation['frame_time_s'], 'frame_sha256': observation['frame_sha256'],
                'status': status, 'decision_reason': reason.strip()}
        if observation.get('frame_file'):
            base['frame_file'] = observation['frame_file']
        if status == 'discarded':
            results.append({**base, 'option_id': None})
            continue
        option_id = selection.get('option_id')
        original = options.get(option_id)
        manual = option_id is None and status == 'edited'
        if not manual and (original is None or original['point_id'] != point_id):
            raise ValueError('方案不属于当前交互点。')
        design = original if status == 'accepted' else selection.get('design')
        if not isinstance(design, dict) or design.get('game_id') not in names:
            raise ValueError('玩法类型无效。')
        for key in FIELDS:
            if not isinstance(design.get(key), str) or not design[key].strip():
                raise ValueError(f'玩法描述缺少 {key}。')
        entry = {**base, **{k: design[k].strip() for k in FIELDS},
                 'option_id': option_id, 'game_id': design['game_id'],
                 'game_name': names[design['game_id']], 'origin': 'manual' if manual else 'ai'}
        for key in ('title', 'difference'):
            if isinstance(design.get(key), str) and design[key].strip():
                entry[key] = design[key].strip()
        results.append(entry)
    return results
