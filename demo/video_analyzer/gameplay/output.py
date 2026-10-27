"""Parse code as raw sections, not JSON strings. Never edits generated source."""
import json
import re


MARKERS = ('GAME_DESIGN', 'END_GAME_DESIGN', 'GAME_JAVASCRIPT',
           'END_GAME_JAVASCRIPT', 'GAME_CSS', 'END_GAME_CSS')
SECTIONS = re.compile(
    r'\s*<<<GAME_DESIGN>>>\r?\n(.*?)^<<<END_GAME_DESIGN>>>\s*'
    r'<<<GAME_JAVASCRIPT>>>\r?\n(.*?)^<<<END_GAME_JAVASCRIPT>>>\s*'
    r'<<<GAME_CSS>>>\r?\n(.*?)^<<<END_GAME_CSS>>>\s*', re.DOTALL | re.MULTILINE)
FENCED_BLOCKS = re.compile(
    r'\s*```json[ \t]*\r?\n(.*?)^```[ \t]*\r?\n\s*'
    r'```(?:javascript|js)[ \t]*\r?\n(.*?)^```[ \t]*\r?\n\s*'
    r'```css[ \t]*\r?\n(.*?)^```[ \t]*\s*', re.DOTALL | re.MULTILINE | re.IGNORECASE)


def remove_boundary_newline(text):
    return text[:-2] if text.endswith('\r\n') else text[:-1] if text.endswith('\n') else text


def compatible_sections(text):
    """Only relax redundant metadata/JS separators; code boundaries must be explicit."""
    match = re.fullmatch(
        r'\s*<<<GAME_DESIGN>>>\r?\n(.*?)^<<<GAME_CSS>>>\r?\n(.*?)^<<<END_GAME_CSS>>>\s*',
        text, re.DOTALL | re.MULTILINE)
    if not match:return None
    head, css = match.groups()
    # The first complete JSON value unambiguously ends the design; no guessing braces.
    stripped = head.lstrip()
    try:design, end = json.JSONDecoder().raw_decode(stripped)
    except json.JSONDecodeError:return None
    if not isinstance(design, dict):return None
    tail = stripped[end:]
    tail = re.sub(r'\A[ \t]*\r?\n', '', tail, count=1)
    prefixes = []
    while True:
        prefix = re.match(r'\A[ \t\r\n]*<<<(END_GAME_DESIGN|GAME_JAVASCRIPT|END_GAME_JAVASCRIPT)>>>[ \t]*\r?\n', tail)
        if not prefix:break
        prefixes.append(prefix.group(1));tail=tail[prefix.end():]
    if len(prefixes)>2 or len(prefixes)!=len(set(prefixes)):return None
    closing = re.search(r'^<<<END_GAME_JAVASCRIPT>>>[ \t]*\s*\Z', tail, re.MULTILINE)
    if not closing:return None
    javascript=remove_boundary_newline(tail[:closing.start()])
    css=remove_boundary_newline(css)
    # Never merge multiple programs or accept additional/unknown separator blocks.
    if re.search(r'^<<<[A-Z_]+>>>', javascript+'\n'+css, re.MULTILINE):return None
    if not javascript.strip():return None
    return {'design':design, 'javascript':javascript, 'css':css}


def format_warnings(output_format):
    descriptions = {
        'raw_sections_compatible_v1':'模型分隔标记不标准；已依据完整设计 JSON 及明确的 JS/CSS 边界兼容读取，未改写源代码。',
        'markdown_blocks_v1':'模型使用 Markdown 代码块；已按 JSON、JavaScript、CSS 三个明确标签读取，未改写源代码。',
        'json_fenced':'模型将完整 JSON 放在 Markdown 围栏中；已移除展示围栏读取，原始返回保留。'}
    return [descriptions[output_format]] if output_format in descriptions else []


class OutputFormatError(ValueError):
    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


def diagnose(text):
    """Read-only diagnostics, also usable for historical records with vague errors."""
    if not isinstance(text, str):return {'issues': ['模型返回内容不是文本']}
    if not text.strip():return {'issues': ['未取得模型返回内容']}
    if text.lstrip().startswith('{'):
        try:parse_json(text, '游戏 JSON')
        except OutputFormatError as error:return error.details
        return {'issues': [], 'format': 'json'}
    found = re.findall(r'^<<<([A-Z_]+)>>>\r?$', text, re.MULTILINE)
    missing = [marker for marker in MARKERS if marker not in found]
    duplicates = [marker for marker in dict.fromkeys(found) if found.count(marker)>1]
    empty = []
    for name in ('GAME_DESIGN', 'GAME_JAVASCRIPT', 'GAME_CSS'):
        match = re.search(r'^<<<'+name+r'>>>\r?\n(.*?)^<<<END_'+name+r'>>>', text, re.MULTILINE | re.DOTALL)
        if match and not match.group(1).strip():empty.append(name)
    issues = ['缺少标记：'+', '.join(missing)] if missing else []
    if duplicates:issues.append('标记重复：'+', '.join(duplicates))
    if found!=list(MARKERS) and not missing and not duplicates:issues.append('标记顺序或标签不符合协议')
    if empty:issues.append('空内容段：'+', '.join(empty))
    return {'expected_markers': list(MARKERS), 'found_markers': found,
            'missing_markers': missing, 'duplicate_markers': duplicates, 'empty_sections': empty,
            'issues': issues}


def parse_json(text, field):
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise OutputFormatError(
            f'{field} 格式错误：{error.msg}（第 {error.lineno} 行，第 {error.colno} 列）；原始响应已保留，未改写代码。',
            {'field': field, 'message': error.msg, 'line': error.lineno,
             'column': error.colno, 'position': error.pos}) from error


def parse_output(text):
    if not isinstance(text, str) or not text.strip():
        raise OutputFormatError('模型未返回游戏内容。', {'field': 'content', 'message': 'empty'})
    if text.lstrip().startswith('{'):
        return parse_json(text, '旧版游戏 JSON'), 'json'
    single_fence = re.fullmatch(r'\s*```json[ \t]*\r?\n(.*?)^```[ \t]*\s*', text, re.DOTALL | re.MULTILINE | re.IGNORECASE)
    if single_fence and not re.search(r'^```', single_fence.group(1), re.MULTILINE):
        return parse_json(single_fence.group(1), '游戏 JSON'), 'json_fenced'
    fenced = FENCED_BLOCKS.fullmatch(text)
    if fenced:
        design, javascript, css = [remove_boundary_newline(part) for part in fenced.groups()]
        if re.search(r'^```',javascript+'\n'+css,re.MULTILINE):
            raise OutputFormatError('存在多个或嵌套代码块，不能确定游戏代码边界。', {'field':'content','message':'ambiguous_blocks'})
        return {'design':parse_json(design,'design'),'javascript':javascript,'css':css}, 'markdown_blocks_v1'
    found = re.findall(r'^<<<([A-Z_]+)>>>\r?$', text, re.MULTILINE)
    match = SECTIONS.fullmatch(text)
    if not match or tuple(found) != MARKERS:
        compatible = compatible_sections(text)
        if compatible is not None:return compatible, 'raw_sections_compatible_v1'
        raise OutputFormatError(
            '游戏响应分段不完整或顺序错误：需要完整的设计、JavaScript、CSS 三段；未自动重试。',
            {'field': 'content', 'message': 'invalid_sections', 'markers': found[:12], **diagnose(text)})
    design, javascript, css = [remove_boundary_newline(part) for part in match.groups()]
    # Captured JS/CSS are kept byte-for-byte as text, including backslashes and newlines.
    return {'design': parse_json(design, 'design') if design.strip() else None,
            'javascript': javascript, 'css': css}, 'raw_sections_v1'


def input_design(data):
    """Explicit input summary for manual review, never a claim about generated code rules."""
    design = data.get('design') or {}
    brief = data.get('brief') or {}
    required = ('fit', 'player_goal', 'player_action', 'success', 'failure', 'feedback')
    if not all(isinstance(design.get(key), str) and design[key].strip() for key in required):
        raise OutputFormatError('模型未返回详细设计，且输入方案不足以提供可核对摘要。',
                                {'field': 'design', 'message': 'missing_design'})
    return {'context': '以下为原输入方案摘要，模型未返回详细设计：'+design['fit'],
            'goal': design['player_goal'], 'input': design['player_action'],
            'screen': brief.get('screen') or '模型未提供界面说明，需实际试玩核对生成界面。',
            'rules': brief.get('rules') or ('原方案成功条件：'+design['success']+'；原方案失败条件：'+design['failure']+'。具体实现参数需回看代码与试玩核对。'),
            'states': brief.get('transition') or '以下为宿主约定：启动游戏，接收结果，反馈后继续原视频；游戏内部状态尚未核实。',
            'feedback': design['feedback'], 'timeout_s': 60}
