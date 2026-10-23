"""HTTP transport; delegate workflows and mutable configuration to application."""
import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, unquote, urlparse
from .. import application as services
from .assets import STATIC_ASSETS


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, code, data, mime='application/json; charset=utf-8'):
        data = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'):
            return self.respond(403, {'error': '非法 Host'})
        parsed = urlparse(self.path)
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
                            'validation_status', 'validation_warnings', 'validation_error', 'error')
                    result = {k: source[k] for k in keys if k in source}
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
                'models': services.MODELS,
                'samples': services.list_samples(),
                'prompt_file': prompt_file,
                'prompt_files': prompt_files,
                'prompt_catalog': prompt_catalog,
                'stage_prompts': services.stage_prompt_catalog(),
                'games': games,
                'game_templates': services.gameplay_design.templates(),
                'configured': {'beeroute': bool(os.environ.get('BEEROUTE_API_KEY'))}})
        if parsed.path == '/sample':
            try:
                name = unquote(parse_qs(parsed.query).get('name', [''])[0])
                return self.respond(200, services.read_sample(name), 'video/mp4')
            except ValueError as error:
                return self.respond(404, {'error': str(error)})
        return self.respond(404, {'error': 'Not found'})

    def do_POST(self):
        if self.headers.get('X-Local-Token') != services.TOKEN:
            return self.respond(403, {'error': '请从本地首页发起请求。'})
        parsed = urlparse(self.path)
        if parsed.path == '/projects':
            try:
                n = int(self.headers.get('Content-Length', '0'))
                if not 0 < n <= 4_000_000:
                    raise ValueError('项目为空或超过 4 MB。')
                payload = json.loads(self.rfile.read(n).decode('utf-8'))
                if not isinstance(payload, dict):
                    raise ValueError('项目请求应为对象。')
                return self.respond(200, services.projects.Store(services.ROOT / 'projects').save(payload))
            except services.projects.Conflict as error:
                return self.respond(409, {'error': str(error)})
            except (ValueError, KeyError, TypeError, UnicodeError) as error:
                return self.respond(400, {'error': str(error)})
            except OSError:
                return self.respond(500, {'error': '项目保存失败，草稿未确认保存，请重试。'})
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
