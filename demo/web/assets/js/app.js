
const $=id=>document.getElementById(id);let config,record,eventRecord,opportunityRecord,gameplayRecord,blobURL,busy=false,reviewChecks=[],pointControls=[],selectionDrafts=[],designDrafts=[],recommendationDrafts=[];
const selectedEventIds=()=>reviewChecks.filter(item=>item.checked).map(item=>item.value);
const selectedPointIds=()=>pointControls.filter(item=>item.include.checked).map(item=>item.pointId);
function clearGameplay(){gameplayRecord=null;selectionDrafts=[];designDrafts=[];recommendationDrafts=[];$('gameplay').replaceChildren();}
function renderTokenUsage(){
  const host=$('tokenUsage');if(!host||typeof TokenUsage==='undefined')return;
  host.replaceChildren();
  const records={events:eventRecord,opportunities:opportunityRecord,gameplay:gameplayRecord};
  if(record&&Object.hasOwn(records,record.stage))records[record.stage]=record;
  for(const [stage,label] of [['events','事件识别'],['opportunities','交互机会'],['gameplay','玩法推荐']])TokenUsage.render(host,records[stage],label);
}
function adjustedTime(control){
  const duration=Number(opportunityRecord?.metadata?.duration_s);
  let value=Number(control.time.value);
  if(!Number.isFinite(value))value=control.point.time_s;
  if(Number.isFinite(duration))value=Math.min(duration,Math.max(0,value));
  return value;
}
function renderCatalog(){
  const types=config?.games?.types||[];
  $('catalogNote').replaceChildren();
  if(!types.length){$('catalogNote').textContent='小游戏目录未加载。';return}
  for(const type of types){
    const line=document.createElement('p');
    const params=config.game_templates?.defaults?.[type.id];
    line.textContent=$('gameplayPrompt').value==='v19'&&params
      ?`${type.name}（${type.id}）适用：${type.situations} ${GameplayDesign.ruleText(type.id,params)}`
      :`${type.name}（${type.id}）：${type.play}适用：${type.situations}目标：${type.player_goal}`;
    $('catalogNote').append(line);
  }
}
function renderPromptPreview(){
  $('promptPreview').replaceChildren();
  for(const [label,stage,version] of [['事件识别','events',$('eventPrompt').value],['机会发现','opportunities',$('opportunityPrompt').value],['玩法推荐','gameplay',$('gameplayPrompt').value]]){
    const item=(config.stage_prompts?.[stage]||[]).find(entry=>entry.version===version);
    const title=document.createElement('h3');title.textContent=item?`${label} · ${item.path}`:`${label}未选择`;
    const body=document.createElement('pre');body.textContent=item?item.content:'此步骤没有可用提示词。';
    $('promptPreview').append(title,body);
  }
}
function fillPromptSelect(id,items){
  const select=$(id);
  select.replaceChildren(...items.map(item=>{const option=document.createElement('option');option.value=item.version;option.textContent=`${item.version} · ${item.path}`;return option}));
  if(items.length)select.value=items[items.length-1].version;
}
function applyInitialChoices(){
  const names=Object.keys(config.models||{});
  if(config.models?.['qwen3-vl-plus'])$('model').value='qwen3-vl-plus';
  else if(names.length)$('model').value=names[0];
  $('mode').value='direct';
  $('fps').value='2';
  const stages=config.stage_prompts||{};
  fillPromptSelect('eventPrompt',stages.events||[]);
  fillPromptSelect('opportunityPrompt',stages.opportunities||[]);
  fillPromptSelect('gameplayPrompt',stages.gameplay||[]);
}
function clearResults(){
  record=null;eventRecord=null;opportunityRecord=null;reviewChecks=[];pointControls=[];clearGameplay();
  $('download').disabled=true;$('summary').textContent='尚未调用模型。没有预置分析结果。';
  $('points').replaceChildren();$('events').replaceChildren();$('metrics').textContent='';$('limitations').replaceChildren();$('raw').textContent='暂无';
  $('reviewHint').textContent='先识别事件。采样率和输入方式只用于第一步。可以取消勾选失实事件，再运行第二步。';
}
function ready(){
  if(!config)return;
  $('download').textContent=record?.restored_summary?'导出关联记录摘要':'导出当前完整记录';
  const hasVideo=(!!$('sample').value||!!$('file').files.length)&&(typeof Workbench==='undefined'||Workbench.mediaReady());
  const hasModel=!!$('model').value&&!!config.models?.[$('model').value]&&!!config.configured.beeroute;
  $('recognize').disabled=busy||!hasVideo||!hasModel||!$('eventPrompt').value;
  $('suggest').disabled=busy||!hasVideo||!hasModel||!eventRecord||!selectedEventIds().length||!$('opportunityPrompt').value;
  $('recommend').disabled=busy||!hasVideo||!hasModel||!opportunityRecord||!selectedPointIds().length||!$('gameplayPrompt').value;
  const stagedDrafts=gameplayRecord?.schema_version===3?recommendationDrafts:designDrafts;
  $('saveBrief').disabled=busy||!gameplayRecord||([2,3].includes(gameplayRecord.schema_version)
    ?!stagedDrafts.length||stagedDrafts.some(d=>d.stale||d.status==='pending'):!selectionDrafts.length);
  $('reset').disabled=busy;
  $('model').disabled=$('mode').disabled=$('fps').disabled=busy;
  $('eventPrompt').disabled=$('opportunityPrompt').disabled=$('gameplayPrompt').disabled=busy;
  const sampling=$('mode').value==='frames'?'顺序采样':'直接视频';
  $('flowHint').textContent=$('eventPrompt').value==='v14'
    ?`第一步使用 ${$('model').value||'未选模型'}、${sampling}、${$('fps').value} fps。事件识别选 v14 时，超过 25 秒会分段并多次调用。第二步和第三步沿用所选模型，并查看对应画面。`
    :`第一步使用 ${$('model').value||'未选模型'}、${sampling}、${$('fps').value} fps。第二步和第三步沿用所选模型。`;
  const key=config.configured.beeroute?'已配置':'待配置 BEEROUTE_API_KEY';
  const names=Object.keys(config.models||{});
  $('configState').textContent=names.length?`密钥${key}。事件 ${$('eventPrompt').value||'未选'}，机会 ${$('opportunityPrompt').value||'未选'}，玩法 ${$('gameplayPrompt').value||'未选'}。当前模型 ${$('model').value||'未选'}，结果写入 results/${$('model').value||'模型名'}/。`:'请在 .env 设置 BEEROUTE_MODELS 后重启。';
  renderTokenUsage();
  if(typeof Workbench!=='undefined')Workbench.notify();
}
fetch('/config').then(r=>r.json()).then(c=>{
  config=c;
  const model=$('model');
  model.replaceChildren(...Object.keys(c.models||{}).map(name=>{const o=document.createElement('option');o.value=o.textContent=name;return o}));
  applyInitialChoices();
  const sample=$('sample');
  const blank=document.createElement('option');blank.value='';blank.textContent='不使用样例，改为上传';
  sample.replaceChildren(blank,...(c.samples||[]).map(name=>{const o=document.createElement('option');o.value=o.textContent=name;return o}));
  renderPromptPreview();renderCatalog();
  if(typeof Workbench!=='undefined')Workbench.init(createWorkbenchBridge());
  ready();
}).catch(()=>{$('configState').textContent='请在项目根目录运行 python demo/app.py，再访问本地地址。'});
$('model').onchange=$('mode').onchange=$('fps').onchange=ready;
for(const id of ['eventPrompt','opportunityPrompt','gameplayPrompt'])$(id).onchange=()=>{renderPromptPreview();renderCatalog();ready()};
$('sample').onchange=async()=>{
  if(blobURL){URL.revokeObjectURL(blobURL);blobURL=''}
  $('file').value='';
  const name=$('sample').value;
  if(name){$('video').src='/sample?name='+encodeURIComponent(name)}
  record=null;eventRecord=null;opportunityRecord=null;reviewChecks=[];pointControls=[];clearGameplay();$('reviewHint').textContent='先识别事件。采样率和输入方式只用于第一步。可以取消勾选失实事件，再运行第二步。';$('download').disabled=true;$('summary').textContent=name?('已选择 '+name+'，等待分析。'):'尚未选择视频。';
  $('points').replaceChildren();$('events').replaceChildren();$('metrics').textContent='';$('limitations').replaceChildren();$('raw').textContent='暂无';ready();
};
$('file').onchange=()=>{
  $('sample').value='';
  if(blobURL)URL.revokeObjectURL(blobURL);
  const f=$('file').files[0];
  if(f){blobURL=URL.createObjectURL(f);$('video').src=blobURL}
  record=null;eventRecord=null;opportunityRecord=null;reviewChecks=[];pointControls=[];clearGameplay();$('reviewHint').textContent='先识别事件。采样率和输入方式只用于第一步。可以取消勾选失实事件，再运行第二步。';$('download').disabled=true;$('summary').textContent='已选择上传文件，等待分析。';
  $('points').replaceChildren();$('events').replaceChildren();$('metrics').textContent='';$('limitations').replaceChildren();$('raw').textContent='暂无';ready();
};
function videoRequestParams(){
  const params={};let body;
  if($('sample').value)params.sample=$('sample').value;
  else{params.name=$('file').files[0].name;body=$('file').files[0]}
  return {params,body};
}
function renderReviewedEvents(events){
  $('events').replaceChildren();reviewChecks=[];
  for(const e of events){
    const row=document.createElement('div');row.className='event';
    const box=document.createElement('input');box.type='checkbox';box.checked=true;box.value=e.observation_id;box.onchange=ready;reviewChecks.push(box);
    const seek=document.createElement('button');seek.type='button';seek.textContent=`${e.observation_s.toFixed(2)} 秒 · ${e.description}`;
    seek.onclick=()=>{$('video').currentTime=e.observation_s;$('video').pause()};
    const detail=document.createElement('span');detail.textContent=`区间 ${e.start_s.toFixed(2)}–${e.end_s.toFixed(2)} 秒；画面依据：${e.evidence}；不确定：${e.uncertainty}`;
    row.append(box,seek,detail);$('events').append(row);
  }
  $('reviewHint').textContent=events.length?'请逐条回看；取消勾选失实事件后，再运行第二步。':'第一步未记录事件；不能运行第二步。';
  ready();
}
function renderStagePoints(points,duration,warnings=[]){
  $('points').replaceChildren();pointControls=[];
  if(warnings.length){const note=document.createElement('aside');note.className='validation-warnings';
    const heading=document.createElement('p');heading.textContent=`有 ${warnings.length} 项候选未采用，合法交互点已保留；未自动改绑画面。`;note.append(heading);
    for(const warning of warnings){const line=document.createElement('p');line.textContent=warning.message;note.append(line);}$('points').append(note);}
  if(!points.length){$('points').textContent='本次未建议交互点。';ready();return}
  points.forEach((point,index)=>{
    const pointId=point.point_id||`p${index+1}`;
    const card=document.createElement('div');card.className='event';
    const tools=document.createElement('div');tools.className='point-tools';
    const include=document.createElement('input');include.type='checkbox';include.className='include';include.checked=true;
    include.onchange=()=>{for(const d of designDrafts)GameplayDesign.invalidate(designDrafts,d.control);for(const d of recommendationDrafts)GameplayRecommendations.invalidate(recommendationDrafts,d.control);ready();};
    const includeLabel=document.createElement('span');includeLabel.textContent='送入第三步';
    const timeLabel=document.createElement('span');timeLabel.textContent='画面位置（秒）';
    const time=document.createElement('input');time.type='number';time.className='time';time.step='0.1';time.value=Number(point.time_s).toFixed(2);
    if(Number.isFinite(duration)){time.min='0';time.max=String(duration)}
    tools.append(include,includeLabel,timeLabel,time);
    const seek=document.createElement('button');seek.type='button';seek.className='seek';
    seek.textContent=`${Number(point.time_s).toFixed(2)} 秒 · ${point.opportunity}`;
    seek.onclick=()=>{$('video').currentTime=adjustedTime({time,point});$('video').pause()};
    const detail=document.createElement('span');detail.textContent=`事件 ${point.event_id}；原始位置 ${Number(point.time_s).toFixed(2)} 秒；原因：${point.reason}`;
    card.append(tools,seek,detail);$('points').append(card);
    const control={point,pointId,include,time};
    time.oninput=()=>{
      if(designDrafts.length){GameplayDesign.invalidate(designDrafts,control);$('download').disabled=true;}
      if(recommendationDrafts.length){GameplayRecommendations.invalidate(recommendationDrafts,control);$('download').disabled=true;}
      ready();};
    pointControls.push(control);
  });
  ready();
}
function renderRecommendations(recommendations){
  $('gameplay').replaceChildren();selectionDrafts=[];designDrafts=[];recommendationDrafts=[];
  if(gameplayRecord?.schema_version===3){
    const observedControls=pointControls.filter(c=>gameplayRecord.observations?.some(o=>o.point_id===c.pointId));
    recommendationDrafts=GameplayRecommendations.render(gameplayRecord,observedControls,$('gameplay'),ready,
      time=>{$('video').currentTime=time;$('video').pause();});ready();return;
  }
  if(gameplayRecord?.schema_version===2){
    const observedControls=pointControls.filter(c=>gameplayRecord.observations?.some(o=>o.point_id===c.pointId));
    designDrafts=GameplayDesign.render(gameplayRecord,observedControls,config,$('gameplay'),ready);
    ready();return;
  }
  const legacy=document.createElement('p');legacy.textContent='先确认玩法与场景是否适配，具体运行规则在选定玩法后再设计。';$('gameplay').append(legacy);
  const sent=pointControls.filter(item=>item.include.checked);
  const byId=Object.fromEntries(recommendations.map(item=>[item.point_id,item]));
  if(!recommendations.length){const note=document.createElement('p');note.className='empty-recommendation';note.textContent='本次未推荐玩法。';$('gameplay').append(note)}
  for(const control of sent){
    const ai=byId[control.pointId]||null;
    const card=document.createElement('div');card.className='event';
    const seek=document.createElement('button');seek.type='button';seek.className='seek';
    seek.textContent=`${adjustedTime(control).toFixed(2)} 秒 · ${control.point.opportunity}`;
    seek.onclick=()=>{$('video').currentTime=adjustedTime(control);$('video').pause()};
    const fit=document.createElement('span');fit.className='fit';
    fit.textContent=ai?`推荐${ai.game_name}：${ai.fit}`:'模型未推荐这个交互点，可以从预设类型里自行选择。';
    const game=document.createElement('select');game.className='game';
    for(const type of (config.games?.types||[])){const option=document.createElement('option');option.value=type.id;option.textContent=type.name;game.append(option)}
    game.value=ai?ai.game_id:(config.games?.types?.[0]?.id||'');
    const fields=[['goal','玩家目标'],['action','玩家操作'],['success','怎样算完成'],['failure','怎样算未完成'],['feedback','玩家看到的反馈']];
    const inputs={};
    const state=document.createElement('span');state.className='choice-state';state.textContent=ai?'待确认':'自行填写';
    const draft={control,ai,game,inputs,state,status:ai?'pending':'edited'};
    const markEdited=()=>{if(draft.status==='discarded')return;draft.status='edited';state.textContent='已修改'};
    game.onchange=markEdited;
    for(const [key,label] of fields){
      const box=document.createElement('textarea');box.className=key;box.value=ai?ai[{goal:'player_goal',action:'player_action',success:'success',failure:'failure',feedback:'feedback'}[key]]:'';
      box.placeholder=label;box.oninput=markEdited;inputs[key]=box;
    }
    const accept=document.createElement('button');accept.type='button';accept.className='accept';accept.textContent='接受';
    accept.onclick=()=>{
      if(!ai)return;
      draft.status='accepted';game.value=ai.game_id;
      inputs.goal.value=ai.player_goal;inputs.action.value=ai.player_action;inputs.success.value=ai.success;inputs.failure.value=ai.failure;inputs.feedback.value=ai.feedback;
      state.textContent='已接受';ready();
    };
    const discard=document.createElement('button');discard.type='button';discard.className='discard';discard.textContent='放弃';
    discard.onclick=()=>{draft.status='discarded';state.textContent='已放弃';ready()};
    card.append(seek,fit,game,...fields.map(([key])=>inputs[key]),state,accept,discard);
    $('gameplay').append(card);selectionDrafts.push(draft);
  }
  ready();
}
$('recognize').onclick=async()=>{
  busy=true;ready();$('file').disabled=$('sample').disabled=true;
  eventRecord=null;opportunityRecord=null;reviewChecks=[];pointControls=[];clearGameplay();$('download').disabled=true;
  $('events').replaceChildren();$('points').replaceChildren();$('limitations').replaceChildren();$('summary').textContent='第一步：识别事件中…';
  $('status').textContent=`正在调用 ${$('model').value} 识别画面事件（计费）。`;
  let mediaFailure=false;
  try{
    const {params,body}=videoRequestParams();
    params.version=$('eventPrompt').value;params.model=$('model').value;params.mode=$('mode').value;params.fps=$('fps').value;
    const response=await fetch('/events?'+new URLSearchParams(params),{method:'POST',headers:{'X-Local-Token':config.token},body});
    const result=await response.json();mediaFailure=!!result.media_error;record=result;$('raw').textContent=JSON.stringify(result,null,2);$('download').disabled=!result.record_file;
    if(!response.ok||result.status!=='success')throw Error(result.error||'事件识别失败');
    eventRecord=result;$('summary').textContent=result.analysis.summary;
    const segmentInfo=result.segments?` · 成功片段 ${result.segments.filter(item=>item.status==='success').length} / 处理 ${result.segments.length} 次`:'';
    $('metrics').textContent=`第一步 ${result.model} · ${result.mode} · ${result.fps} fps · ${result.video_name} · ${result.prompt_file}${segmentInfo} · ${result.total_s}s · ${result.record_file}`;
    renderReviewedEvents(result.analysis.events);
    for(const item of result.analysis.limitations){const li=document.createElement('li');li.textContent=item;$('limitations').append(li)}
    $('status').textContent=`事件已保存${segmentInfo}。请回看并审核，再点击第二步。`;
  }catch(error){$('summary').textContent=mediaFailure?'视频预处理失败，分析未完成。':'第一步未获得通过校验的事件。';$('status').textContent=error.message}
  finally{busy=false;$('file').disabled=$('sample').disabled=false;ready()}
};
$('suggest').onclick=async()=>{
  const selected=selectedEventIds();if(!eventRecord||!selected.length)return;
  busy=true;opportunityRecord=null;pointControls=[];clearGameplay();ready();$('file').disabled=$('sample').disabled=true;
  $('points').replaceChildren();$('status').textContent=`正在调用 ${$('model').value} 寻找参与机会（计费）。`;
  try{
    const {params,body}=videoRequestParams();params.record_id=eventRecord.record_id;params.selected=selected.join(',');params.version=$('opportunityPrompt').value;params.model=$('model').value;
    const response=await fetch('/opportunities?'+new URLSearchParams(params),{method:'POST',headers:{'X-Local-Token':config.token},body});
    const result=await response.json();record=result;$('raw').textContent=JSON.stringify(result,null,2);$('download').disabled=!result.record_file;
    if(!response.ok||result.status!=='success')throw Error(result.error||'交互机会分析失败');
    opportunityRecord=result;
    renderStagePoints(result.analysis.interaction_points,result.metadata&&result.metadata.duration_s,result.analysis.warnings||[]);
    $('metrics').textContent=`第二步 ${result.model} · ${result.video_name} · ${result.prompt_file} · ${result.total_s}s · ${result.record_file}；第一步 ${eventRecord.record_file}`;
    const counts=result.analysis.validation_summary;
    $('status').textContent=result.analysis.warnings?.length
      ?`保留 ${result.analysis.interaction_points.length} 条可定位候选，另有 ${counts?.rejected_count||result.analysis.warnings.length} 项未采用；请查看警告并回看。`
      :'候选已保存。点击每个位置回看，核查理由是否符合该画面。';
  }catch(error){$('status').textContent=error.message}
  finally{busy=false;$('file').disabled=$('sample').disabled=false;ready()}
};
$('recommend').onclick=async()=>{
  const selected=selectedPointIds();if(!opportunityRecord||!selected.length)return;
  busy=true;const previous={gameplayRecord,selectionDrafts,designDrafts,recommendationDrafts};ready();$('file').disabled=$('sample').disabled=true;
  $('status').textContent=`正在调用 ${$('model').value} 推荐预设玩法（计费）。`;
  try{
    const {params,body}=videoRequestParams();params.record_id=opportunityRecord.record_id;params.selected=selected.join(',');params.version=$('gameplayPrompt').value;params.model=$('model').value;
    const times={};
    for(const c of pointControls.filter(c=>c.include.checked)){
      const t=Number(c.time.value),duration=opportunityRecord.metadata.duration_s;
      if(c.time.value.trim()===''||!Number.isFinite(t)||t<0||t>duration)throw Error('画面位置须位于片长范围内');
      times[c.pointId]=t;
    }
    params.times=JSON.stringify(times);
    const response=await fetch('/gameplay?'+new URLSearchParams(params),{method:'POST',headers:{'X-Local-Token':config.token},body});
    const result=await response.json();record=result;$('raw').textContent=JSON.stringify(result,null,2);$('download').disabled=!result.record_file;
    if(!response.ok||result.status!=='success')throw Error(result.error||'玩法推荐失败');
    gameplayRecord=result;
    renderRecommendations(result.analysis.recommendations||[]);
    $('metrics').textContent=`第三步 ${result.model} · ${result.video_name} · ${result.prompt_file} · ${result.total_s}s · ${result.record_file}`;
    const warningCount=(result.analysis.warnings||[]).length+(result.request_warnings||[]).length;
    $('status').textContent=warningCount?`玩法推荐已保存，有 ${warningCount} 项输出警告，合法玩法已保留；请查看下方详情。`
      :gameplayRecord.schema_version===2?'历史 v19 规则设计：请比较方案并完成对应审核。':'玩法推荐已保存。请查看为什么适合、玩家怎么玩，再接受、修改或放弃。';
  }catch(error){gameplayRecord=previous.gameplayRecord;selectionDrafts=previous.selectionDrafts;designDrafts=previous.designDrafts;recommendationDrafts=previous.recommendationDrafts;$('status').textContent=error.message+(previous.gameplayRecord?'；已有草稿保留。':'')}
  finally{busy=false;$('file').disabled=$('sample').disabled=false;ready()}
};
$('saveBrief').onclick=async()=>{
  if(!opportunityRecord||!gameplayRecord||(!selectionDrafts.length&&!designDrafts.length&&!recommendationDrafts.length))return;
  if(selectionDrafts.some(item=>item.status==='pending')){$('status').textContent='请先接受、修改或放弃每条推荐。';return}
  busy=true;ready();
  $('status').textContent='正在保存玩法描述，此步骤不调用模型。';
  try{
    const selections=gameplayRecord.schema_version===3?GameplayRecommendations.selections(recommendationDrafts)
      :gameplayRecord.schema_version===2?GameplayDesign.selections(designDrafts):selectionDrafts.map(item=>({
      point_id:item.control.pointId,adjusted_time_s:adjustedTime(item.control),status:item.status,game_id:item.game.value,
      fit:item.ai?item.ai.fit:'',player_goal:item.inputs.goal.value,player_action:item.inputs.action.value,
      success:item.inputs.success.value,failure:item.inputs.failure.value,feedback:item.inputs.feedback.value}));
    const response=await fetch('/briefs',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':config.token},body:JSON.stringify({opportunity_record_id:opportunityRecord.record_id,gameplay_record_id:gameplayRecord.record_id,selections})});
    const result=await response.json();record=result;$('raw').textContent=JSON.stringify(result,null,2);$('download').disabled=!result.record_file;
    if(!response.ok||result.status!=='success')throw Error(result.error||'玩法描述保存失败');
    $('metrics').textContent=`玩法描述 ${result.video_name} · ${result.record_file}；推荐 ${gameplayRecord.record_file}`;
    $('status').textContent='玩法描述已保存，未调用模型。后续可对接受或修改后的玩法细化设计。';
  }catch(error){$('status').textContent=error.message}
  finally{busy=false;ready()}
};
$('reset').onclick=()=>{
  if(busy)return;
  if(blobURL){URL.revokeObjectURL(blobURL);blobURL=''}
  $('file').value='';
  $('sample').value='';
  $('video').src='';
  if($('video').load)$('video').load();
  applyInitialChoices();
  clearResults();
  renderPromptPreview();
  renderCatalog();
  $('status').textContent='已恢复到初始状态。';
  ready();
};
$('download').onclick=()=>{const u=URL.createObjectURL(new Blob([JSON.stringify(record,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=u;a.download=(record.record_file||'video-analysis').split('/').pop();a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)};
// Additive project adapter. Existing inference handlers above remain the sole request builders.
function createWorkbenchBridge(){
  let previewUntil=null;
  $('video').addEventListener('timeupdate',()=>{if(previewUntil!==null&&$('video').currentTime>=previewUntil){$('video').pause();previewUntil=null;}});
  return {
    token:()=>config.token, catalog:()=>config.games?.types||[], samples:()=>config.samples||[], ready, isBusy:()=>busy,
    time:()=>Number($('video').currentTime)||0,
    duration:()=>Number.isFinite($('video').duration)?$('video').duration:(opportunityRecord?.metadata?.duration_s||eventRecord?.metadata?.duration_s||0),
    source:()=>({sample:$('sample').value,file:$('file').files[0]}),
    seek:time=>{previewUntil=null;$('video').currentTime=time;$('video').pause();},
    previewEnd:time=>{previewUntil=time;const play=$('video').play();if(play?.catch)play.catch(()=>{});},
    peek:()=>({records:{events:eventRecord,opportunities:opportunityRecord,gameplay:gameplayRecord},session:{
      events:eventRecord?.record_id||null,opportunities:opportunityRecord?.record_id||null,gameplay:gameplayRecord?.record_id||null,
      settings:Object.fromEntries(['model','mode','fps','eventPrompt','opportunityPrompt','gameplayPrompt'].map(id=>[id,$(id).value])),
      reviews:reviewChecks.map(c=>({id:c.value,checked:c.checked})),
      controls:pointControls.map(c=>({point_id:c.pointId,checked:c.include.checked,time_s:adjustedTime(c)})),
      drafts:recommendationDrafts.map(d=>({point_id:d.control.pointId,option_id:d.option_id,
        design:d.design?WorkbenchModel.cleanDesign(d.design):null,status:d.status,decision_reason:d.decision_reason,stale:d.stale})),
    }}),
    restore:(session,records,video,keepSource=false)=>{
      clearResults();
      if(!keepSource){
        if(blobURL){URL.revokeObjectURL(blobURL);blobURL='';}
        $('file').value='';$('sample').value=video?.sample||'';
        $('video').src=video?.sample?'/sample?name='+encodeURIComponent(video.sample):'';
        if($('video').load)$('video').load();
      }
      for(const [id,value] of Object.entries(session.settings||{})){
        if(['model','mode','fps','eventPrompt','opportunityPrompt','gameplayPrompt'].includes(id)&&Array.from($(id).options).some(o=>o.value===value))$(id).value=value;
      }
      eventRecord=records.events||null;opportunityRecord=records.opportunities||null;gameplayRecord=records.gameplay||null;
      if(eventRecord){renderReviewedEvents(eventRecord.analysis.events);$('summary').textContent=eventRecord.analysis.summary;
        for(const c of reviewChecks){const stored=session.reviews?.find(r=>r.id===c.value);if(stored)c.checked=stored.checked;}}
      if(opportunityRecord){renderStagePoints(opportunityRecord.analysis.interaction_points,opportunityRecord.metadata?.duration_s,opportunityRecord.analysis.warnings||[]);
        for(const c of pointControls){const stored=session.controls?.find(r=>r.point_id===c.pointId);if(stored){c.include.checked=stored.checked;c.time.value=stored.time_s;}}}
      if(gameplayRecord)renderRecommendations(gameplayRecord.analysis.recommendations||[]);
      for(const d of recommendationDrafts){const saved=session.drafts?.find(s=>s.point_id===d.control.pointId);if(saved&&d.restore)d.restore(saved);}
      record=gameplayRecord||opportunityRecord||eventRecord;
      $('raw').textContent=record?'关联记录摘要；完整原始响应保留在 '+record.record_file+'\n'+JSON.stringify(record,null,2):'暂无';$('download').disabled=!record;
      renderPromptPreview();renderCatalog();ready();
    },
    retry:async(pointId,time)=>{
      if(busy)throw Error('已有模型请求进行中');
      const c=pointControls.find(c=>c.pointId===pointId);if(!c)throw Error('当前批次没有此点');
      const previous=pointControls.map(c=>({c,checked:c.include.checked,time:c.time.value}));
      for(const item of previous)item.c.include.checked=item.c===c;
      c.time.value=time;
      try{await $('recommend').onclick();}
      finally{for(const item of previous){item.c.include.checked=item.checked;item.c.time.value=item.time;}ready();}
    },
  };
}
