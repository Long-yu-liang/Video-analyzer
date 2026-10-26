"""HTTP transport; delegate workflows and mutable configuration to application."""
import json
import os
import re
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, unquote, urlparse
from .. import application as services
from .assets import STATIC_ASSETS
from ..gameplay import generation
from ..gameplay import library
from ..projects import game_management as management
from .. import settings


class Handler(BaseHTTPRequestHandler):
    def configured(self):
        try:
            values = settings.Store(services.ROOT).read()['values']
            return {'beeroute': bool(values['BEEROUTE_API_KEY']), 'minimax': bool(values['MINIMAX_API_KEY'])}
        except (ValueError, OSError):
            return {'beeroute': False, 'minimax': False}

    def log_message(self, *args):
        pass

    def respond(self, code, data, mime='application/json; charset=utf-8'):
        data = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        content_range = None
        if code == 200 and mime == 'video/mp4' and self.headers.get('Range'):
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', self.headers['Range'])
            total = len(data)
            if match and any(match.groups()):
                first, last = match.groups()
                start = int(first) if first else max(0, total-int(last))
                end = min(int(last), total-1) if first and last else total-1
                if 0 <= start <= end < total:
                    data = data[start:end+1];code = 206
                    content_range = f'bytes {start}-{end}/{total}'
                else:
                    code = 416;data = b'';content_range = f'bytes */{total}'
            else:
                code = 416;data = b'';content_range = f'bytes */{total}'
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if mime == 'video/mp4':self.send_header('Accept-Ranges', 'bytes')
        if content_range:self.send_header('Content-Range', content_range)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'):
            return self.respond(403, {'error': '非法 Host'})
        parsed = urlparse(self.path)
        if parsed.path in ('/settings', '/settings/export'):
            if self.headers.get('X-Local-Token') != services.TOKEN:
                return self.respond(403, {'error': '请从本地首页打开设置。'})
            try:
                store = settings.Store(services.ROOT)
                data = (store.export(parse_qs(parsed.query).get('include_secrets') == ['1'])
                        if parsed.path.endswith('/export') else store.public())
                return self.respond(200, data)
            except (ValueError, OSError):
                return self.respond(400, {'error': '配置读取失败，请检查 config/settings.json，原文件未被覆盖。'})
        if parsed.path in ('/game-generation', '/game-selection', '/project-games', '/game-bundle', '/game-trash', '/game-sequence'):
            if self.headers.get('X-Local-Token') != services.TOKEN:
                return self.respond(403, {'error': '请从本地首页读取游戏。'})
            try:
                q = parse_qs(parsed.query)
                if parsed.path == '/game-trash':return self.respond(200,management.listing(services.ROOT,q.get('project_id',[''])[0]))
                if parsed.path == '/game-sequence':return self.respond(200,management.sequence(services.ROOT,q.get('project_id',[''])[0]))
                if parsed.path == '/project-games':
                    return self.respond(200, library.inventory(services.ROOT, q.get('project_id', [''])[0]))
                if parsed.path == '/game-bundle':
                    return self.respond(200, library.bundle(services.ROOT, q.get('project_id', [''])[0], q.get('generation_id', [None])[0]), 'application/zip')
                if parsed.path == '/game-selection':
                    return self.respond(200, generation.selections(services.ROOT, q.get('project_id', [''])[0]))
                if 'id' in q:
                    return self.respond(200, generation.read(services.ROOT, q['id'][0]))
                return self.respond(200, generation.listing(services.ROOT, q.get('project_id', [''])[0], q.get('point_id', [''])[0]))
            except (ValueError, KeyError, OSError) as error:
                return self.respond(400, {'error': str(error)})
        asset = STATIC_ASSETS.get(parsed.path)
        if asset:
            relative, mime = asset
            return self.respond(200, (services.DEMO / 'web' / relative).read_bytes(), mime)
        if parsed.path in ('/projects', '/project', '/project-record', '/project-export'):
            if self.headers.get('X-Local-Token') != services.TOKEN:
                return self.respond(403, {'error': '请从本地首页读取项目。'})
            try:
                store = services.projects.Store(services.ROOT / 'projects')
                q = parse_qs(parsed.query)
                if parsed.path == '/projects':
                    return self.respond(200, store.listing())
                if parsed.path == '/project-record':
                    record_id = services.projects.identifier(q.get('record_id', [''])[0])
                    matches = services.saved_record_paths(record_id)
                    if len(matches) != 1:
                        raise ValueError('关联记录不存在或不唯一，原草稿仍可查看。')
                    source = json.loads(matches[0].read_text(encoding='utf-8'))
                    # UI needs analysis and provenance, never embedded historical media or raw API payloads.
                    keys = ('record_id', 'stage', 'status', 'schema_version', 'video_name', 'video_sha256',
                            'metadata', 'model', 'mode', 'fps', 'prompt_file', 'total_s', 'analysis',
                            'catalog_snapshot', 'template_snapshot', 'observations', 'parent_record_id',
                            'request_warnings', 'record_kind', 'model_call_made', 'source_record_id', 'source_record_file',
                            'validation_status', 'validation_warnings', 'validation_error', 'media_error', 'error', 'usage',
                            'partial_analysis', 'segment_coverage', 'usage_reported_subtotal', 'usage_incomplete')
                    result = {k: source[k] for k in keys if k in source}
                    if isinstance(source.get('segments'), list):
                        result['segments'] = [{k: item[k] for k in ('record_id', 'status', 'usage', 'clip_start_s', 'clip_end_s')
                                               if k in item} for item in source['segments'] if isinstance(item, dict)]
                    result['record_file'] = matches[0].relative_to(services.ROOT).as_posix()
                    result['restored_summary'] = True
                    result['observations'] = [{k: v for k, v in item.items() if k != 'preview_jpeg_base64'}
                                              for item in result.get('observations', [])]
                    return self.respond(200, result)
                item = store.read(q.get('id', [''])[0])
                if parsed.path == '/project-export':
                    item = services.projects.export_project(item, q.get('ready') == ['1'])
                return self.respond(200, item)
            except (ValueError, KeyError, OSError) as error:
                return self.respond(400, {'error': str(error)})
        if parsed.path == '/gameplay-frame':
            try:
                query = parse_qs(parsed.query)
                data = services.read_gameplay_frame(query.get('record_id', [''])[0], query.get('point_id', [''])[0])
                return self.respond(200, data, 'image/jpeg')
            except (ValueError, KeyError, OSError):
                return self.respond(404, {'error': '画面不存在或不可用。'})
        if parsed.path == '/config':
            try:
                prompt_path, _ = services.load_prompt()
                prompt_file = f'prompts/{prompt_path.name}'
                prompt_files = [f'prompts/{p.name}' for _, p in services.prompt_paths()]
                prompt_catalog = [{'version': p.stem, 'flow': 'single',
                                   'files': [{'path': f'prompts/{p.name}', 'content': p.read_text(encoding='utf-8').strip()}]}
                                  for _, p in services.prompt_paths()]
            except ValueError as error:
                prompt_file = str(error)
                prompt_files = []
                prompt_catalog = []
            try:
                event_path, event_prompt = services.load_stage_prompt('events', 'v13')
                opportunity_path, opportunity_prompt = services.load_stage_prompt('opportunities', 'v13')
                prompt_catalog.append({'version': 'v13', 'flow': 'two_stage', 'files': [
                    {'path': f'prompts/v13/{event_path.name}', 'content': event_prompt},
                    {'path': f'prompts/v13/{opportunity_path.name}', 'content': opportunity_prompt}]})
            except (FileNotFoundError, ValueError):
                pass
            try:
                event_path, event_prompt = services.load_stage_prompt('events', 'v14')
                opportunity_path, opportunity_prompt = services.load_stage_prompt('opportunities', 'v13')
                prompt_catalog.append({'version': 'v14', 'flow': 'two_stage', 'files': [
                    {'path': f'prompts/v14/{event_path.name}', 'content': event_prompt},
                    {'path': f'prompts/v13/{opportunity_path.name}', 'content': opportunity_prompt}]})
            except (FileNotFoundError, ValueError):
                pass
            try:
                games = services.load_game_catalog()
            except (FileNotFoundError, ValueError, json.JSONDecodeError):
                games = None
            return self.respond(200, {
                'token': services.TOKEN,
                'models': services.runtime_models(),
                'samples': services.list_samples(),
                'prompt_file': prompt_file,
                'prompt_files': prompt_files,
                'prompt_catalog': prompt_catalog,
                'stage_prompts': services.stage_prompt_catalog(),
                'games': games,
                'game_templates': services.gameplay_design.templates(),
                'configured': self.configured()})
        if parsed.path == '/sample':
            try:
                name = unquote(parse_qs(parsed.query).get('name', [''])[0])
                return self.respond(200, services.read_sample(name), 'video/mp4')
            except ValueError as error:
                return self.respond(404, {'error': str(error)})
        return self.respond(404, {'error': 'Not found'})

    def do_POST(self):
        if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'):
            return self.respond(403, {'error': '非法 Host'})
        if self.headers.get('X-Local-Token') != services.TOKEN:
            return self.respond(403, {'error': '请从本地首页发起请求。'})
        parsed = urlparse(self.path)
        if parsed.path in ('/settings', '/settings/check-tools'):
            try:
                n = int(self.headers.get('Content-Length', '0'))
                if not 0 < n <= 128000:
                    raise ValueError('设置请求大小无效。')
                payload = json.loads(self.rfile.read(n))
                if not isinstance(payload, dict):
                    raise ValueError('设置请求应为对象。')
                if parsed.path.endswith('/check-tools'):
                    return self.respond(200, settings.check_tools(services.ROOT))
                return self.respond(200, settings.Store(services.ROOT).save(payload))
            except settings.SettingsConflict as error:
                return self.respond(409, {'error': str(error)})
            except (ValueError, TypeError):
                return self.respond(400, {'error': '设置格式无效，请检查字段、HTTPS 地址及模型名称。'})
            except OSError:
                return self.respond(500, {'error': '设置保存失败，请检查 config 目录权限；旧配置未被替换。'})
        if parsed.path in ('/game-generation', '/game-repair', '/game-playtest', '/game-selection', '/game-delete', '/game-restore', '/arrangement', '/game-session'):
            try:
                n = int(self.headers.get('Content-Length', '0'))
                if not 0 < n <= (128000 if parsed.path=='/arrangement' else 32000):
                    raise ValueError('游戏请求为空或过大。')
                payload = json.loads(self.rfile.read(n).decode('utf-8'))
                if not isinstance(payload, dict):
                    raise ValueError('游戏请求必须为对象。')
                if parsed.path == '/game-repair' and not payload.get('generation_id'):
                    raise ValueError('修复必须提供原生成记录 ID。')
                if parsed.path == '/game-generation' and payload.get('generation_id'):
                    raise ValueError('修复请使用修复入口。')
                operations={'/game-delete':management.remove,'/game-restore':management.restore,'/arrangement':management.save_arrangement,'/game-session':management.lease}
                if parsed.path in operations:return self.respond(200,operations[parsed.path](services.ROOT,payload))
                value = (generation.select(services.ROOT, payload) if parsed.path == '/game-selection' else
                         generation.playtest(services.ROOT, payload) if parsed.path == '/game-playtest'
                         else generation.generate(services.ROOT, payload))
                return self.respond(200, value)
            except services.projects.Conflict as error:
                return self.respond(409, {'error': str(error)})
            except (ValueError, KeyError, OSError) as error:
                return self.respond(400, {'error': str(error)})
        if parsed.path in ('/projects', '/project-delete'):
            try:
                n = int(self.headers.get('Content-Length', '0'))
                if not 0 < n <= 4_000_000:
                    raise ValueError('项目为空或超过 4 MB。')
                payload = json.loads(self.rfile.read(n).decode('utf-8'))
                if not isinstance(payload, dict):
                    raise ValueError('项目请求应为对象。')
                store = services.projects.Store(services.ROOT / 'projects')
                return self.respond(200, store.delete(payload) if parsed.path == '/project-delete' else store.save(payload))
            except services.projects.Conflict as error:
                return self.respond(409, {'error': str(error)})
            except (ValueError, KeyError, TypeError, UnicodeError) as error:
                return self.respond(400, {'error': str(error)})
            except OSError:
                return self.respond(500, {'error': '项目操作失败，请刷新列表核对后重试。'})
        if parsed.path == '/briefs':
            try:
                n = int(self.headers.get('Content-Length', '0'))
                if not 0 < n <= 1_000_000:
                    raise ValueError('玩法描述为空或过大。')
                payload = json.loads(self.rfile.read(n).decode('utf-8'))
                return self.respond(200, services.save_gameplay_brief(payload))
            except json.JSONDecodeError:
                return self.respond(400, {'error': '玩法描述不是合法 JSON。'})
            except ValueError as error:
                return self.respond(400, {'error': str(error)})
            except Exception:
                return self.respond(500, {'error': '本地处理失败，请检查文件和运行环境。'})
        if parsed.path not in ('/analyze', '/events', '/opportunities', '/gameplay'):
            return self.respond(404, {'error': 'Not found'})
        try:
            q = parse_qs(parsed.query)
            sample = q.get('sample', [''])[0]
            if sample:
                raw = services.read_sample(sample)
                video_name = sample
            else:
                n = int(self.headers.get('Content-Length', '0'))
                if not 0 < n <= services.MAX_VIDEO:
                    raise ValueError('请选择不超过 12 MiB 的 MP4。')
                raw = self.rfile.read(n)
                if len(raw) != n:
                    raise ValueError('上传不完整。')
                video_name = unquote(q.get('name', ['upload.mp4'])[0]) or 'upload.mp4'
            if parsed.path == '/events':
                mode = q.get('mode', ['direct'])[0]
                fps = float(q.get('fps', ['2'])[0])
                record = services.analyze_events(raw, video_name, q.get('version', ['v14'])[0],
                                        q.get('model', ['qwen3-vl-plus'])[0], mode, fps)
            elif parsed.path == '/opportunities':
                selected = [value for value in q.get('selected', [''])[0].split(',') if value]
                record = services.suggest_opportunities(raw, video_name, q.get('record_id', [''])[0], selected,
                                               q.get('version', [None])[0] or None,
                                               q.get('model', ['qwen3-vl-plus'])[0])
            elif parsed.path == '/gameplay':
                selected = [value for value in q.get('selected', [''])[0].split(',') if value]
                record = services.suggest_gameplay(raw, video_name, q.get('record_id', [''])[0], selected,
                                          q.get('version', [None])[0] or None,
                                          q.get('model', ['qwen3-vl-plus'])[0],
                                          json.loads(q['times'][0]) if 'times' in q else None)
            else:
                record = services.analyze(raw, q['model'][0], q['mode'][0], float(q['fps'][0]), video_name,
                                 q.get('prompt', [None])[0])
            return self.respond(200, record)
        except (ValueError, KeyError) as error:
            return self.respond(400, {'error': str(error)})
        except Exception:
            return self.respond(500, {'error': '本地处理失败，请检查文件和运行环境。'})
