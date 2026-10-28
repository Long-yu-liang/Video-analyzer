"""Bounded recommendation calls, explicit per-point coverage and partial results."""
import json
import time
from . import recommendations
from .. import settings

BATCH_SIZE = 5


def run(record, selected, points, catalog, prompt, catalog_text, messages, url, key, call):
    batch_size = int(settings.get('GAMEPLAY_BATCH_SIZE', str(BATCH_SIZE)))
    record['batches'] = []
    combined = {'recommendations':[], 'abstentions':[], 'incomplete_points':[], 'warnings':[], 'limitations':[]}
    for offset in range(0, len(selected), batch_size):
        ids = selected[offset:offset+batch_size]
        batch_points = [points[identity] for identity in ids]
        first = prompt+'\n\n预设小游戏目录：\n'+catalog_text
        first += '\n本批交互点 ID：'+json.dumps(ids, ensure_ascii=False)
        first += '\n逐点处理：每点给出推荐，或在 abstentions 中给出具体理由；不得忽略。不必强行推荐玩法。'
        first += '\n输出字段约定：'+json.dumps(recommendations.output_schema(batch_points,catalog),ensure_ascii=False)
        content = [{'type':'text','text':first}]
        for identity in ids:content.extend(messages[identity])
        batch = {'batch_index':len(record['batches'])+1, 'selected_point_ids':ids,
                 'model':record['model'], 'mode':'frames', 'fps':record['fps'], 'status':'failed',
                 'actual_prompt':'\n\n'.join(item['text'] for item in content if item['type']=='text')}
        record['batches'].append(batch)
        body = {'model':record['model'],'messages':[{'role':'user','content':content}],**settings.analysis_options(record['model'])}
        started=time.perf_counter()
        try:
            text=call(url,body,key,batch)
            analysis=recommendations.validate(text,batch_points,catalog)
            batch.update(analysis=analysis,status='success' if analysis['coverage']['complete'] else 'partial')
            for field in combined:combined[field].extend(analysis.get(field,[]))
        except Exception as error:
            batch['error']=str(error).replace(key,'[REDACTED]')[:4000]
            if hasattr(error,'detail'):batch['validation_error']=error.detail
            combined['incomplete_points'].extend({'point_id':identity,'reason':f"第 {batch['batch_index']} 批失败：{batch['error']}"} for identity in ids)
            combined['warnings'].append({'path':f"batches[{batch['batch_index']-1}]",'code':'batch_failed','message':batch['error']})
        finally:batch['total_s']=round(time.perf_counter()-started,3)
    recommended={item['point_id'] for item in combined['recommendations']}
    coverage={'requested_count':len(selected),'recommended_count':len(recommended),
              'abstained_count':len(combined['abstentions']),'incomplete_count':len(combined['incomplete_points']),
              'complete':not combined['incomplete_points']}
    combined['coverage']=coverage
    record.update(analysis=combined,batch_size=batch_size,coverage=coverage,
                  status='success' if coverage['complete'] else 'partial' if any('analysis' in b for b in record['batches']) else 'failed',
                  validation_status='incomplete' if not coverage['complete'] else 'passed_with_warnings' if combined['warnings'] else 'passed',
                  validation_warnings=combined['warnings'],
                  actual_prompt='\n\n'.join(f"第 {b['batch_index']} 批\n{b['actual_prompt']}" for b in record['batches']))
    if not coverage['complete']:record['error']=f"{coverage['incomplete_count']} 个交互点未完成；已返回的合法玩法保留，可逐点重新推荐。"
    usage, subtotal = {}, {}
    def reported_usage(batch):
        value = batch.get('usage')
        return value if isinstance(value, dict) else {}
    def details(batch, category):
        value = reported_usage(batch).get(category)
        return value if isinstance(value, dict) else {}
    for field in ('prompt_tokens','completion_tokens','total_tokens'):
        values=[reported_usage(b).get(field) for b in record['batches']]
        reported=[v for v in values if type(v) in (int,float)]
        if reported:subtotal[field]=sum(reported)
        if len(reported)==len(values):usage[field]=sum(reported)
    record.update(usage=usage,usage_incomplete=len(usage)<3,usage_reported_subtotal=subtotal)
    for category in ('prompt_tokens_details','completion_tokens_details'):
        detail_keys={field for batch in record['batches'] for field in details(batch,category)}
        totals={}
        for field in detail_keys:
            values=[details(batch,category).get(field) for batch in record['batches']]
            if all(type(value) in (int,float) for value in values):totals[field]=sum(values)
        if totals:usage[category]=totals
    record['model_request_count']=sum(len(b.get('api_attempts',[])) for b in record['batches'])
    record['request_bytes']=sum(b.get('request_bytes',0) for b in record['batches'])
    record['api_roundtrip_s']=round(sum(b.get('api_roundtrip_s',0) for b in record['batches']),3)
    for field in ('video_tokens','media_tokens','audio_tokens'):
        values=[b.get(field) for b in record['batches'] if type(b.get(field)) in (int,float)]
        record[field]=sum(values) if values else None
    record['audio_verification']='not_sent'
    record['media_verification']='confirmed' if record.get('video_tokens') else 'unreported'
    if len(record['batches'])==1:
        batch=record['batches'][0]
        for field in ('raw_text','raw_response','finish_reason','api_attempts','request_options','validation_error','api_error_detail','requested_response_format'):
            if field in batch:record[field]=batch[field]
        record['actual_prompt']=batch['actual_prompt']
        if batch.get('error'):record['error']=batch['error']
