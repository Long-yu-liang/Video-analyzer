"""Local output contracts: strict core data, recoverable optional explanations."""
import json

MISSING = object()


def type_name(value):
    if value is MISSING:
        return '缺失'
    if value is None:
        return 'null'
    return {str: '字符串', dict: '对象', list: '数组', bool: '布尔值',
            int: '数字', float: '数字'}.get(type(value), type(value).__name__)


class ContractError(ValueError):
    def __init__(self, path, message, code='invalid_value'):
        self.detail = {'path': path, 'code': code, 'message': f'{path} {message}'}
        super().__init__(self.detail['message'])


def expected(path, description, value):
    error = ContractError(path, f'应为{description}，实际为{type_name(value)}。',
                          'missing_field' if value is MISSING else 'invalid_type')
    error.detail.update(expected=description, actual_type=type_name(value))
    return error


def parse_json(text):
    if not isinstance(text, str):
        raise expected('$', 'JSON字符串', text)
    def invalid_constant(value):
        raise ContractError('$', f'不是合法 JSON：不允许 {value}。', 'invalid_json')
    try:
        return json.loads(text, parse_constant=invalid_constant)
    except json.JSONDecodeError as error:
        failure = ContractError('$', f'不是合法 JSON：第 {error.lineno} 行、第 {error.colno} 列：{error.msg}', 'invalid_json')
        failure.detail.update(line=error.lineno, column=error.colno, offset=error.pos)
        raise failure from error


def require_text(item, key, path):
    value = item.get(key, MISSING)
    if not isinstance(value, str) or not value.strip():
        if isinstance(value, str):
            raise ContractError(f'{path}.{key}', '应为非空字符串，实际为空字符串。')
        raise expected(f'{path}.{key}', '非空字符串', value)
    return value.strip()


def abstentions(output, known_points, recommended, fill_missing=True):
    """Skip malformed optional notes; never invent a mapping from free-form text.

    Explicit unknown point references remain fatal, including in auxiliary objects.
    Original output is kept by the caller; this function only produces derived data.
    """
    values = output.get('abstentions', [])
    warnings, valid, seen = [], [], set()
    if not isinstance(values, list):
        warning = expected('abstentions', '数组', values).detail
        warnings.append({**warning, 'message': warning['message'] + '该辅助字段未采用，合法玩法已保留。'})
        values = []
    for index, item in enumerate(values):
        path = f'abstentions[{index}]'
        try:
            if not isinstance(item, dict):
                raise expected(path, '对象', item)
            point_id = require_text(item, 'point_id', path)
            if point_id not in known_points:
                raise ContractError(f'{path}.point_id', f'引用了未输入的交互点 {point_id!r}。', 'unknown_point')
            reason = require_text(item, 'reason', path)
            if point_id in recommended:
                raise ContractError(path, f'与 {point_id} 的合法推荐冲突，不能同时标记为未推荐。', 'conflicting_abstention')
            if point_id in seen:
                raise ContractError(path, f'重复说明 {point_id} 的未推荐原因。', 'duplicate_abstention')
        except ContractError as error:
            if error.detail['code'] == 'unknown_point':
                raise
            warnings.append({**error.detail, 'message': str(error) + '该项未采用，合法玩法已保留。'})
            continue
        seen.add(point_id)
        valid.append({'point_id': point_id, 'reason': reason})
    for point_id in known_points:
        if fill_missing and point_id not in recommended and point_id not in seen:
            valid.append({'point_id': point_id, 'reason': '模型未提供可用的未推荐原因，可回看后自行补充。'})
    return valid, warnings
