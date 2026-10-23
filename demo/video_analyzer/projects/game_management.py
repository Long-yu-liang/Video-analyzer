"""Recoverable game/point operations and saved video arrangements. No inference."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import time
from .store import Store, LOCK, Conflict, identifier
from ..storage import games
from ..gameplay import generation

LEASES = {}


def atomic(path, data):
    path = Path(path);path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.'+path.name+'-'+secrets.token_hex(6)+'.tmp')
    try:
        temp.write_bytes(data);os.replace(temp, path)
    finally:
        if temp.exists():temp.unlink()


def encode(value):return json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')


def restore_bytes(path,before):
    if before is None:
        if path.exists():path.unlink()
    elif not path.exists() or path.read_bytes()!=before:
        atomic(path,before)


def mapping(root, project_id):
    path = Store(Path(root)/'projects').folder(project_id)/'games.json'
    result = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(result, dict):raise ValueError('采用索引损坏，未覆盖原文件。')
    return result


def project_for(root, payload):
    store = Store(Path(root)/'projects');project = store.read(identifier(payload.get('project_id')))
    if type(payload.get('revision')) is not int or project['revision'] != payload['revision']:
        raise Conflict('项目已有更新，请刷新后操作。')
    return store, project


def check_idle(project_id, point_ids):
    for point_id in point_ids:
        if (project_id, point_id) in generation.ACTIVE:raise Conflict('关联玩点正在生成或修复，不能删除或修改。')
    for lease in LEASES.values():
        if lease['expires']>time.monotonic() and lease['project_id']==project_id and lease['point_id'] in point_ids:
            raise Conflict('关联玩点正在试玩，请先结束试玩。')


def lease(root, payload):
    token = payload.get('session_id', '')
    if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',token):raise ValueError('试玩会话 ID 无效。')
    with LOCK, generation.LOCK:
        if payload.get('active') is False:
            LEASES.pop(token,None);return {'active':False}
        if payload.get('active') is not True:raise ValueError('active 应为布尔值。')
        record = games.read(root,payload.get('generation_id'))
        if record.get('status')!='checked' or record.get('stage')!='game_generation':raise ValueError('游戏不可试玩。')
        _,_,digest=generation.snapshot(root,record['project_id'],record['point_id'])
        if digest!=record['input_hash']:raise ValueError('游戏输入已变化。')
        LEASES[token]={'project_id':record['project_id'],'point_id':record['point_id'],'expires':time.monotonic()+90}
    return {'active':True}


def commit(root, store, project, state, selections):
    """Update project head and adoption together, restoring the previous head on failure."""
    base=store.folder(project['project_id']);head=base/'project.json';index=base/'games.json'
    previous_head=head.read_bytes();previous_index=index.read_bytes() if index.exists() else None
    try:
        saved=store.save({'project_id':project['project_id'],'revision':project['revision'],'state':state})
        atomic(index,encode(selections))
        history=base/'games-revisions'/f"revision-{saved['revision']:06d}-{secrets.token_hex(4)}_编排.json"
        atomic(history,encode({'project_revision':saved['revision'],'selections':selections,
                              'enabled_points':[p['id'] for p in state['points'] if p['enabled']]}))
        return saved
    except Exception:
        restore_bytes(head,previous_head);restore_bytes(index,previous_index)
        raise


def save_arrangement(root,payload):
    with LOCK,generation.LOCK:
        store,project=project_for(root,payload);state=deepcopy(project['state']);values=mapping(root,project['project_id'])
        entries=payload.get('entries')
        if not isinstance(entries,list) or len(entries)>300:raise ValueError('编排列表无效。')
        points={p['id']:p for p in state['points']};seen=set()
        for entry in entries:
            if not isinstance(entry,dict) or entry.get('point_id') not in points or entry['point_id'] in seen:raise ValueError('编排玩点无效或重复。')
            point=points[entry['point_id']];seen.add(point['id']);check_idle(project['project_id'],[point['id']])
            if type(entry.get('enabled')) is not bool:raise ValueError('参与开关无效。')
            record_id=entry.get('generation_id')
            if record_id:
                try:record=games.read(root,record_id)
                except (ValueError,OSError):
                    if entry['enabled'] or values.get(point['id'])!=record_id:raise ValueError('选择的游戏记录不存在。')
                    point['enabled']=False;continue
                if record.get('project_id')!=project['project_id'] or record.get('point_id')!=point['id'] or record.get('stage')!='game_generation':raise ValueError('游戏不属于该项目玩点。')
                if entry['enabled']:
                    _,_,digest=generation.snapshot(root,project['project_id'],point['id'])
                    if record['status']!='checked' or record['input_hash']!=digest:raise ValueError('启用游戏失败、过期或未核对。')
                values[point['id']]=record_id
            elif entry['enabled']:raise ValueError('启用的玩点必须选择游戏。')
            else:values.pop(point['id'],None)
            point['enabled']=entry['enabled']
        saved=commit(root,store,project,state,values)
        return {'project':saved,'selections':values}


def sequence(root,project_id):
    project=Store(Path(root)/'projects').read(identifier(project_id));values=mapping(root,project_id);records=[];times=set();issues=[]
    for point in sorted(project['state']['points'],key=lambda p:p['time_s']):
        if not point['enabled']:continue
        try:
            if point['time_s'] in times:raise ValueError('与另一个启用点时间相同')
            record=games.read(root,values.get(point['id']))
            _,_,digest=generation.snapshot(root,project_id,point['id'])
            if record.get('project_id')!=project_id or record.get('point_id')!=point['id'] or record.get('status')!='checked' or record.get('input_hash')!=digest:raise ValueError('采用游戏不属于当前点、失败或已过期')
            times.add(point['time_s']);records.append(record)
        except (ValueError,KeyError,OSError) as error:issues.append(f"{point['time_s']} 秒：{error}")
    if issues:raise ValueError('编排不能开始：'+'；'.join(issues))
    if not records:raise ValueError('没有启用的游戏。')
    return records


def safe(root,relative,area):
    path=(Path(root)/relative).resolve()
    if not path.is_relative_to((Path(root)/area).resolve()) or path==(Path(root)/area).resolve():raise ValueError('文件路径不安全。')
    return path


def listing(root,project_id):
    base=Store(Path(root)/'projects').folder(identifier(project_id));Store(Path(root)/'projects').read(project_id)
    result=[]
    for path in (base/'trash').glob('*/manifest.json'):
        try:
            item=json.loads(path.read_text(encoding='utf-8'))
            if item['status']=='deleted':result.append({k:item.get(k) for k in ('operation_id','kind','title','deleted_at','generation_count','playtest_count')})
        except (ValueError,KeyError,OSError):continue
    return sorted(result,key=lambda i:i['deleted_at'],reverse=True)


def remove(root,payload):
    with LOCK,generation.LOCK:
        store,project=project_for(root,payload);project_id=project['project_id'];values=mapping(root,project_id)
        state=deepcopy(project['state']);kind=payload.get('kind');point_snapshot=None;record_id=None
        if kind=='point':
            found=[p for p in state['points'] if p['id']==payload.get('point_id')]
            if len(found)!=1:raise ValueError('交互点不存在。')
            point_snapshot=found[0];point_id=point_snapshot['id'];title=point_snapshot.get('design',{}).get('title') if point_snapshot.get('design') else point_snapshot['opportunity']
        elif kind=='game':
            record=games.read(root,payload.get('generation_id'))
            if record.get('project_id')!=project_id or record.get('stage')!='game_generation':raise ValueError('游戏归属无效。')
            point_id=record['point_id'];record_id=record['record_id'];title=record.get('input',{}).get('design',{}).get('title','游戏')
        else:raise ValueError('删除类型无效。')
        check_idle(project_id,[point_id]);generations=[];tests=[]
        for path in games.candidates(root):
            try:item=json.loads(path.read_text(encoding='utf-8'))
            except (ValueError,OSError):continue
            if not isinstance(item,dict) or item.get('project_id')!=project_id or item.get('point_id')!=point_id:continue
            if item.get('stage')=='game_generation' and (kind=='point' or item.get('record_id')==record_id):generations.append((path,item))
        ids={item['record_id'] for _,item in generations}
        for path in games.candidates(root):
            try:item=json.loads(path.read_text(encoding='utf-8'))
            except (ValueError,OSError):continue
            if isinstance(item,dict) and item.get('project_id')==project_id and item.get('stage')=='game_playtest' and (item.get('generation_id') in ids or (kind=='point' and item.get('point_id')==point_id)):tests.append((path,item))
        paths=[p for p,_ in generations+tests]
        paths += [p.parent/(p.stem+'_产物') for p,_ in generations if (p.parent/(p.stem+'_产物')).exists()]
        if kind=='point':
            state['points']=[p for p in state['points'] if p['id']!=point_id]
            for p in state['points']:
                if p.get('merged_into')==point_id:p.pop('merged_into');p['enabled']=False
            state.setdefault('deleted_point_ids',[])
            if point_id not in state['deleted_point_ids']:state['deleted_point_ids'].append(point_id)
        if kind=='point' or values.get(point_id) in ids:
            values.pop(point_id,None)
            for p in state['points']:
                if p['id']==point_id:p['enabled']=False
        operation=secrets.token_hex(16);base=store.folder(project_id)/'trash'/operation;base.mkdir(parents=True)
        manifest={'operation_id':operation,'kind':kind,'title':title,'point_id':point_id,'point_snapshot':point_snapshot,'status':'preparing',
                  'deleted_at':datetime.now(timezone.utc).isoformat(),'generation_count':len(generations),'playtest_count':len(tests),'files':[]}
        head=store.folder(project_id)/'project.json';index=store.folder(project_id)/'games.json'
        before_head=head.read_bytes();before_index=index.read_bytes() if index.exists() else None
        moved=[]
        try:
            for source in paths:
                source=safe(root,source.relative_to(root),'results');target=base/'files'/source.relative_to(root)
                safe(root,target.relative_to(root),f'projects/{project_id}/trash/{operation}')
                target.parent.mkdir(parents=True,exist_ok=True);source.rename(target);moved.append((source,target))
                manifest['files'].append({'original':source.relative_to(root).as_posix(),'stored':target.relative_to(root).as_posix()})
            atomic(base/'manifest.json',encode(manifest))
            saved=commit(root,store,project,state,values)
            manifest['status']='deleted';atomic(base/'manifest.json',encode(manifest))
        except Exception:
            try:restore_bytes(head,before_head);restore_bytes(index,before_index)
            finally:
                for source,target in reversed(moved):
                    if target.exists():
                        if source.exists():raise Conflict('回滚文件存在冲突，请保留回收目录并人工检查。')
                        source.parent.mkdir(parents=True,exist_ok=True);target.rename(source)
            raise
        return {'project':saved,'operation_id':operation,'generation_count':len(generations),'playtest_count':len(tests)}


def restore(root,payload):
    with LOCK,generation.LOCK:
        store,project=project_for(root,payload);project_id=project['project_id'];operation=identifier(payload.get('operation_id'))
        base=store.folder(project_id)/'trash'/operation;manifest=json.loads((base/'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('status')!='deleted':raise ValueError('回收项已恢复或不可恢复。')
        state=deepcopy(project['state']);point=manifest.get('point_snapshot')
        if point:
            if any(p['id']==point['id'] for p in state['points']):raise Conflict('已存在同 ID 玩点，不能覆盖。')
            point=deepcopy(point);point['enabled']=False;point['confirmed']=False;point['review']='kept';point.pop('merged_into',None);state['points'].append(point)
            state['deleted_point_ids']=[i for i in state.get('deleted_point_ids',[]) if i!=point['id']]
        targets=[]
        for entry in manifest['files']:
            source=safe(root,entry['stored'],f'projects/{project_id}/trash/{operation}');target=safe(root,entry['original'],'results')
            if target.exists():raise Conflict('恢复目标已存在，未覆盖任何文件。')
            if not source.exists():raise ValueError('回收文件缺失，不能恢复。')
            targets.append((source,target))
        check_idle(project_id,[manifest['point_id']])
        head=store.folder(project_id)/'project.json';index=store.folder(project_id)/'games.json'
        before_head=head.read_bytes();before_index=index.read_bytes() if index.exists() else None
        moved=[]
        try:
            for source,target in targets:target.parent.mkdir(parents=True,exist_ok=True);source.rename(target);moved.append((source,target))
            saved=commit(root,store,project,state,mapping(root,project_id))
            manifest['status']='restored';atomic(base/'manifest.json',encode(manifest))
        except Exception:
            try:restore_bytes(head,before_head);restore_bytes(index,before_index)
            finally:
                for source,target in reversed(moved):
                    if source.exists():raise Conflict('恢复回滚存在文件冲突，请保留回收目录检查。')
                    source.parent.mkdir(parents=True,exist_ok=True);target.rename(source)
            raise
        return {'project':saved,'restored':True}
