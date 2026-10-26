"""Versioned prompt discovery and the shared gameplay catalog."""
import json
import re


def prompt_paths(root):
    folder = root / 'prompts'
    return sorted(((int(p.stem[1:]), p) for p in folder.glob('v*.txt')
                   if re.fullmatch(r'v[1-9]\d*', p.stem)), key=lambda item: item[0])


def load_prompt(root, version=None):
    """默认使用最高版本；也允许显式选择一个已存在的版本，不改动旧文件。"""
    versioned = prompt_paths(root)
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


def stage_prompt_paths(root, stage):
    if stage not in ('events', 'opportunities', 'gameplay'):
        raise ValueError('无效的阶段提示词。')
    paths = [path for path in (root / 'prompts').glob(f'v*/{stage}.txt')
             if path.is_file() and re.fullmatch(r'v[1-9]\d*', path.parent.name)]
    return sorted(paths, key=lambda path: int(path.parent.name[1:]))


def load_stage_prompt(root, stage, version=None):
    paths = stage_prompt_paths(root, stage)
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


def stage_prompt_catalog(root):
    catalog = {}
    for stage in ('events', 'opportunities', 'gameplay'):
        catalog[stage] = [{'version': path.parent.name,
                           'path': f'prompts/{path.parent.name}/{path.name}',
                           'content': path.read_text(encoding='utf-8').strip()}
                          for path in stage_prompt_paths(root, stage)]
    return catalog


def game_generation_catalog(root):
    """Read-only generation/repair prompt versions; does not change runtime selection."""
    folder = root / 'prompts/game-generation'
    result = []
    for version in ('v1', 'v2'):
        files = []
        for name, label in (('generate.txt', '生成游戏'), ('repair.txt', '修复游戏')):
            path = folder / version / name
            if path.is_file():
                active = 'v2' if (folder / 'v2' / name).is_file() else 'v1'
                files.append({'label': label, 'path': path.relative_to(root).as_posix(),
                              'content': path.read_text(encoding='utf-8').strip(), 'active': version == active})
        if files:
            result.append({'version': version, 'files': files})
    return result


def load_game_catalog(root, game_ids):
    path = root / 'prompts' / 'games' / 'catalog.json'
    catalog = json.loads(path.read_text(encoding='utf-8'))
    types = catalog.get('types') if isinstance(catalog, dict) else None
    if not isinstance(types, list) or not types:
        raise ValueError('小游戏目录无效。')
    seen = set()
    for item in types:
        if not isinstance(item, dict):
            raise ValueError('小游戏目录无效。')
        if item.get('id') not in game_ids or item['id'] in seen:
            raise ValueError('小游戏目录的类型无效或重复。')
        seen.add(item['id'])
        for key in ('name', 'play', 'situations', 'player_goal'):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError('小游戏目录缺少玩法说明。')
    if seen != set(game_ids):
        raise ValueError('小游戏目录必须包含全部预设玩法。')
    return catalog


def catalog_text(catalog):
    return '\n'.join(
        f"- {item['id']}｜{item['name']}｜玩法：{item['play']}｜适用：{item['situations']}｜玩家目标：{item['player_goal']}"
        for item in catalog['types'])
