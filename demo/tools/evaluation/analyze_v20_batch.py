"""Offline evidence sheets and machine-counted metrics; no model calls."""
import argparse
import base64
from collections import Counter
import hashlib
import json
import math
import sys
from pathlib import Path

# Support direct execution from any working directory without installing the package.
_demo = str(Path(__file__).resolve().parents[2])
if _demo not in sys.path:
    sys.path.insert(0, _demo)
from video_analyzer import application as app

FOLDER = app.ROOT / 'day3/eval/v20-batch-20261022'


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def evidence():
    from PIL import Image, ImageDraw, ImageFont
    manifest = json.loads((FOLDER/'manifest.json').read_text(encoding='utf-8'))
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 17)
    for video in manifest['videos']:
        name = video['name']; code = name[:3]
        folder = FOLDER/'evidence'/code
        folder.mkdir(parents=True, exist_ok=True)
        index = folder/'index.json'
        if index.exists():
            continue
        path = app.ROOT/'mp4-example'/name
        if hashlib.sha256(path.read_bytes()).hexdigest() != video['sha256']:
            raise ValueError('素材哈希不匹配。')
        frames = app.extract(path, folder, video['metadata']['duration_s'], 2.)
        items = []
        for i, frame in enumerate(frames, 1):
            image_path = folder/f'{i:03}.jpg'
            items.append({'time_s':frame['time_s'], 'file':image_path.relative_to(app.ROOT).as_posix(),
                          'sha256':hashlib.sha256(image_path.read_bytes()).hexdigest()})
        selected = items[::2]
        width, height, columns = (180, 360, 5) if video['metadata']['height'] > video['metadata']['width'] else (320, 205, 4)
        sheet = Image.new('RGB', (columns*width, math.ceil(len(selected)/columns)*height), '#202522')
        draw = ImageDraw.Draw(sheet)
        for i, item in enumerate(selected):
            picture = Image.open(app.ROOT/item['file']).convert('RGB')
            picture.thumbnail((width, height-25))
            x, y = (i%columns)*width, (i//columns)*height
            sheet.paste(picture, (x+(width-picture.width)//2,y))
            draw.text((x+5,y+height-23),f"{code}  {item['time_s']:.3f}s",font=font,fill='white')
        sheet.save(folder/'contact.jpg', quality=90)
        write_json(index, items)
        print(str(folder/'contact.jpg'), flush=True)


def summarize():
    manifest = json.loads((FOLDER/'manifest.json').read_text(encoding='utf-8'))
    ledger = json.loads((FOLDER/'ledger.json').read_text(encoding='utf-8'))
    rows, points, events, records = [], [], [], []
    for group, stages in sorted(ledger.items()):
        row = {'group':group, 'stages':{}, 'events':None,'points':None,'recommendations':None,'types':{}}
        parsed = {}
        for stage in ('events','opportunities','gameplay'):
            entry = stages.get(stage, {'status':'not_started'})
            row['stages'][stage] = entry
            if entry.get('record_file'):
                record = json.loads((app.ROOT/entry['record_file']).read_text(encoding='utf-8'))
                parsed[stage] = record
                records.append({'group':group,'stage':stage,'file':entry['record_file'],
                                'record_id':record['record_id'],'status':record['status'],
                                'usage':record.get('usage',{}),'request_count':entry.get('request_count',0),
                                'total_s':record['total_s'], 'error':record.get('error'),
                                'media_verification':record.get('media_verification'),
                                'segments':[{'status':s.get('status'),'media_verification':s.get('media_verification'),
                                             'finish_reason':s.get('finish_reason')} for s in record.get('segments',[])]})
        if 'events' in parsed and parsed['events']['status']=='success':
            stage_events = parsed['events']['analysis']['events']
            row['events'] = len(stage_events)
            events.extend({'group':group,**e} for e in stage_events)
        if 'opportunities' in parsed and parsed['opportunities']['status']=='success':
            opportunities = app.indexed_points(parsed['opportunities'])
            row['points'] = len(opportunities)
            gameplay = parsed.get('gameplay',{})
            recommendations = gameplay.get('analysis',{}).get('recommendations',[])
            if gameplay.get('status') == 'success':
                row['recommendations'] = len(recommendations)
                row['recommended_points'] = len({r['point_id'] for r in recommendations})
                row['types'] = dict(Counter(r['game_id'] for r in recommendations))
                row['choice_primary'] = sum(r['game_id']=='choice' and r['option_id'].endswith('-o1') for r in recommendations)
                row['choice_secondary'] = sum(r['game_id']=='choice' and r['option_id'].endswith('-o2') for r in recommendations)
            observations = {o['point_id']:o for o in gameplay.get('observations',[])}
            for point in opportunities:
                points.append({'group':group,**point,'observation':observations.get(point['point_id']),
                               'options':[r for r in recommendations if r['point_id']==point['point_id']],
                               'gameplay_status':gameplay.get('status','skipped')})
        rows.append(row)
    summary = {'manifest':manifest,'groups':rows,'points':points,'events':events,'records':records,
               'totals':{'groups':len(rows),'records':len(records),
                         'requests':sum(r['request_count'] for r in records),
                         'prompt_tokens':sum(r['usage'].get('prompt_tokens',0) for r in records),
                         'completion_tokens':sum(r['usage'].get('completion_tokens',0) for r in records)}}
    write_json(FOLDER/'summary.json', summary)
    print(json.dumps({'totals':summary['totals'],'groups':[
        {k:v for k,v in r.items() if k!='stages'} for r in rows]},ensure_ascii=False,indent=2))


def candidate_sheets():
    from PIL import Image, ImageDraw, ImageFont
    summary = json.loads((FOLDER/'summary.json').read_text(encoding='utf-8'))
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 17)
    for code in ('S03','S05','S06'):
        items = [p for p in summary['points'] if p['group'].startswith(code)]
        if not items:
            continue
        index = json.loads((FOLDER/'evidence'/code/'index.json').read_text(encoding='utf-8'))
        w,h,columns = (240,480,4) if code=='S03' else (384,240,3)
        sheet = Image.new('RGB',(columns*w,math.ceil(len(items)/columns)*h),'#202522')
        draw = ImageDraw.Draw(sheet)
        for i,point in enumerate(items):
            observation = point.get('observation') or {}
            file = observation.get('frame_file')
            expected_hash = observation.get('frame_sha256')
            if not file:
                frame = min(index,key=lambda f:abs(f['time_s']-point['time_s']))
                file,expected_hash = frame['file'],frame['sha256']
            path = app.ROOT/file
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
                raise ValueError('候选回看图片哈希不符。')
            picture = Image.open(path).convert('RGB');picture.thumbnail((w,h-26))
            x,y = (i%columns)*w,(i//columns)*h
            sheet.paste(picture,(x+(w-picture.width)//2,y))
            draw.text((x+4,y+h-23),f"{point['group']} {point['point_id']} {point['time_s']:.3f}s",font=font,fill='white')
        target = FOLDER/'evidence'/code/'candidates.jpg'
        sheet.save(target,quality=93)
        print(str(target),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',action='store_true')
    parser.add_argument('--summarize',action='store_true')
    parser.add_argument('--candidates',action='store_true')
    args=parser.parse_args()
    if args.evidence:
        evidence()
    if args.summarize:
        summarize()
    if args.candidates:
        candidate_sheets()
