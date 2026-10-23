"""Versioned Day 3 design contracts; no inference or video IO in this module."""
import copy
import json
import math
from pathlib import Path
from ..contracts.output import ContractError, MISSING, abstentions, expected, parse_json, require_text

from ..paths import ROOT

TEMPLATE_PATH = ROOT / 'prompts/games/templates.json'
TEXT_FIELDS = ('title', 'fit', 'player_goal', 'screen_description', 'player_action',
               'success', 'failure', 'feedback', 'difference')


def templates():
    return json.loads(TEMPLATE_PATH.read_text(encoding='utf-8'))


def number(value, low, high, label):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label}无效，须在 {low}–{high} 内。')
    return value


def validate_parameters(game_id, params, spec):
    defaults = spec['defaults'].get(game_id)
    if defaults is None or not isinstance(params, dict) or set(params) != set(defaults):
        raise ValueError('玩法参数与模板不一致。')
    for key in ('timeout_s', 'feedback_s', 'period_s', 'charge_s'):
        if key in params:
            number(params[key], 0.1, 120, key)
    if 'zone_min' in params:
        number(params['zone_min'], 0, 1, '目标区起点')
        number(params['zone_max'], 0, 1, '目标区终点')
        if params['zone_min'] >= params['zone_max']:
            raise ValueError('目标区起点必须小于终点。')
    if 'clicks' in params and (type(params['clicks']) is not int or not 1 <= params['clicks'] <= 1000):
        raise ValueError('连点次数须为 1–1000 的整数。')
    if 'direction' in params and params['direction'] not in ('left', 'right', 'up', 'down'):
        raise ValueError('短滑方向无效。')
    if 'distance_ratio' in params:
        number(params['distance_ratio'], 0.01, 1, '滑动距离比例')
    if 'choices' in params:
        choices = params['choices']
        if (not isinstance(choices, list) or not 2 <= len(choices) <= 3
                or not all(isinstance(x, str) and x.strip() for x in choices)
                or len(set(choices)) != len(choices)):
            raise ValueError('剧情选项须为 2–3 个不同的非空文本。')
    return copy.deepcopy(params)


def validate_anchors(game_id, anchors, spec):
    roles = spec['anchor_roles'].get(game_id, [])
    if not isinstance(anchors, list) or len(anchors) != len(roles):
        raise ValueError('请完成该类型必需的对象框选。')
    if {a.get('role') for a in anchors if isinstance(a, dict)} != set(roles):
        raise ValueError('框选对象角色不符或重复。')
    for anchor in anchors:
        for key in ('x', 'y', 'w', 'h'):
            number(anchor.get(key), 0, 1, '框选坐标')
        if (anchor['w'] <= 0 or anchor['h'] <= 0
                or anchor['x'] + anchor['w'] > 1.000001
                or anchor['y'] + anchor['h'] > 1.000001):
            raise ValueError('框选须位于原片画面内并具有面积。')
    return copy.deepcopy(anchors)


def rules_text(game_id, p):
    end = f"；成功或失败反馈显示 {p['feedback_s']:g} 秒后继续原片，结果不改变视频剧情。"
    timeout = f"{p['timeout_s']:g} 秒内"
    rules = {
        'tap_object': '点中人工标注的目标区域成功，超时失败',
        'choice': '选择任意一个按钮完成，超时未选择失败；选项：' + '、'.join(p.get('choices', [])),
        'timing': f"新控件指针从零开始按 {p.get('period_s', 2):g} 秒周期完成零到满到零的往返，在 {p.get('zone_min', .65):.0%}–{p.get('zone_max', .85):.0%} 按下成功，区域外按下或超时失败",
        'hold_release': f"新蓄力条按住 {p.get('charge_s', 2):g} 秒从零到满，在 {p.get('zone_min', .65):.0%}–{p.get('zone_max', .85):.0%} 松开成功，过早、过晚、蓄满未松或超时失败",
        'mash': f"累计点击 {p.get('clicks', 10)} 次成功，超时未达到失败",
        'drag': '从起点拖动叠加标记，释放点在人工标注的落点区域内成功，释放到区域外或超时失败',
        'swipe': f"朝 {p.get('direction', 'right')} 短滑达到画面短边 {p.get('distance_ratio', .1):.0%} 成功；以位移较大的轴判方向，两轴相等不判定，错误方向或超时失败"
    }
    return '暂停原片后，' + timeout + rules[game_id] + end


def validate_recommendations(text, points, catalog, spec):
    output = parse_json(text)
    if not isinstance(output, dict):
        raise expected('$', '对象', output)
    if not isinstance(output.get('recommendations'), list):
        raise expected('recommendations', '数组', output.get('recommendations', MISSING))
    by_id = {p['point_id']: p for p in points}
    names = {g['id']: g['name'] for g in catalog['types']}
    result, counts, fingerprints = [], {}, set()
    for index, item in enumerate(output['recommendations']):
        path = f'recommendations[{index}]'
        if not isinstance(item, dict):
            raise expected(path, '对象', item)
        point_id = require_text(item, 'point_id', path)
        game_id = require_text(item, 'game_id', path)
        if point_id not in by_id:
            raise ContractError(f'{path}.point_id', f'引用了未输入的交互点 {point_id!r}。', 'unknown_point')
        if game_id not in names:
            raise ContractError(f'{path}.game_id', f'玩法 {game_id!r} 不在目录中。', 'unknown_game')
        for key in TEXT_FIELDS:
            require_text(item, key, path)
        counts[point_id] = counts.get(point_id, 0) + 1
        if counts[point_id] > 2:
            raise ContractError(path, f'交互点 {point_id} 超过最多两个方案的上限。')
        fingerprint = (point_id, game_id, item['player_goal'].strip(), item['player_action'].strip())
        if fingerprint in fingerprints:
            raise ContractError(path, f'与交互点 {point_id} 的已有方案重复。')
        fingerprints.add(fingerprint)
        labels = item.get('target_labels')
        if not isinstance(labels, list) or not all(isinstance(x, str) and x.strip() for x in labels):
            raise expected(f'{path}.target_labels', '非空字符串组成的数组', item.get('target_labels', MISSING))
        if len(labels) != len(spec['anchor_roles'].get(game_id, [])):
            raise ContractError(f'{path}.target_labels', f'应有 {len(spec["anchor_roles"].get(game_id, []))} 个目标描述，实际为 {len(labels)} 个。')
        params = copy.deepcopy(spec['defaults'][game_id])
        if game_id == 'choice':
            params['choices'] = item.get('choice_labels')
        if game_id == 'swipe':
            params['direction'] = item.get('direction')
        try:
            validate_parameters(game_id, params, spec)
        except ValueError as error:
            field = 'choice_labels' if game_id == 'choice' else 'direction' if game_id == 'swipe' else 'parameters'
            raise ContractError(f'{path}.{field}', str(error)) from error
        result.append({**by_id[point_id], **{k: item[k].strip() for k in TEXT_FIELDS},
                       'option_id': f'{point_id}-o{counts[point_id]}', 'game_id': game_id,
                       'game_name': names[game_id], 'parameters': params, 'anchors': [],
                       'target_labels': labels, 'rules_text': rules_text(game_id, params),
                       'review_status': 'pending'})
    notes, warnings = abstentions(output, by_id, counts)
    return {'recommendations': result, 'abstentions': notes, 'warnings': warnings, 'limitations': []}


def build_brief(payload, opportunity, gameplay, points, names):
    spec = gameplay['template_snapshot']
    expected = gameplay['selected_point_ids']
    selections = payload.get('selections')
    if not isinstance(selections, list) or not all(isinstance(x, dict) for x in selections):
        raise ValueError('玩法选择格式无效。')
    ids = [s.get('point_id') for s in selections]
    if len(set(ids)) != len(ids) or set(ids) != set(expected):
        raise ValueError('每个点须且只能保存一个选择。')
    options = {r['option_id']: r for r in gameplay['analysis']['recommendations']}
    observations = {o['point_id']: o for o in gameplay['observations']}
    saved = []
    for selection in selections:
        point_id = selection['point_id']
        status = selection.get('status')
        if status not in ('accepted', 'edited', 'discarded'):
            raise ValueError('请确认或放弃方案。')
        reason = selection.get('decision_reason')
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('请填写选择、修改或放弃的原因。')
        obs = observations[point_id]
        requested = selection.get('adjusted_time_s', obs['requested_time_s'])
        number(requested, 0, opportunity['metadata']['duration_s'], '暂停时间')
        if abs(requested - obs['requested_time_s']) > 1e-6:
            raise ValueError('暂停时间已改变，请重新推荐。')
        base = {**points[point_id], 'adjusted_time_s': requested, 'frame_time_s': obs['frame_time_s'],
                'frame_sha256': obs['frame_sha256'], 'status': status, 'decision_reason': reason.strip()}
        if status == 'discarded':
            saved.append({**base, 'option_id': None, 'ready_for_codegen': False})
            continue
        option_id = selection.get('option_id')
        original = options.get(option_id)
        manual = option_id is None and status == 'edited'
        if not manual and (original is None or original['point_id'] != point_id):
            raise ValueError('方案不属于该交互点。')
        design = copy.deepcopy(original if status == 'accepted' else selection.get('design'))
        if not isinstance(design, dict) or design.get('game_id') not in names:
            raise ValueError('玩法设计无效。')
        for key in TEXT_FIELDS:
            if not isinstance(design.get(key), str) or not design[key].strip():
                raise ValueError(f'玩法描述缺少 {key}。')
        game_id = design['game_id']
        parameters = validate_parameters(game_id, design.get('parameters'), spec)
        anchors = validate_anchors(game_id, selection.get('anchors', design.get('anchors', [])), spec)
        saved.append({**base, **{key: design[key] for key in TEXT_FIELDS}, 'option_id': option_id,
                      'game_id': game_id, 'game_name': names[game_id], 'parameters': parameters,
                      'anchors': anchors, 'rules_text': rules_text(game_id, parameters),
                      'origin': 'manual' if manual else 'ai', 'ready_for_codegen': True,
                      'resume': spec['playback']})
    return saved
