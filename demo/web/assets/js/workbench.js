/* Project/UI layer only: no prompt construction, model policy or inference validation here. */
(function(root) {
  'use strict';
  const clone = x => JSON.parse(JSON.stringify(x));
  const designFields = ['fit', 'player_goal', 'player_action', 'success', 'failure', 'feedback'];
  const labels = {fit:'画面与玩法的关系', player_goal:'玩家目标', player_action:'玩家具体操作',
    success:'成功或完成条件', failure:'失败或未完成条件', feedback:'玩家反馈'};
  const briefLabels = {screen:'新增画面元素（控件、标记、动画等）', rules:'具体判定与结束规则（无输赢可写选择完成条件）',
    transition:'进入交互、结束或跳过后的视频衔接'};
  function blank() { return {name:'未命名视频项目', video:null, session:{}, points:[], record_ids:[], seen_record_ids:[], density_s:3}; }
  function cleanDesign(o) {
    return Object.fromEntries(['title', 'game_id', ...designFields].map(k => [k, o[k] || '']));
  }
  function point(id, time, opportunity, reason, origin='manual') {
    return {id, time_s:time, opportunity, reason, origin, review:'kept', enabled:false,
      versions:[], selected:null, design:null, brief:{}, confirmed:false, stale:false, decision_reason:''};
  }
  function gameReadyPoints(points){
    return points.filter(p=>p.review==='kept'&&!p.merged_into&&p.design&&
      (p.selected||(p.gameplay_committed===undefined&&!(p.versions||[]).length)));
  }
  const manualGameplaySelection=new WeakSet();
  function availableGameplayPoints(){return state.points.filter(p=>p.review==='kept'&&!p.merged_into&&(p.gameplay_committed===true||(p.gameplay_committed===undefined&&(p.design||p.versions.length))));}
  function commitGameplaySelection(){
    const session=bridge.peek().session;
    const selected=state.points.filter(p=>p.review==='kept'&&!p.merged_into&&(p.origin==='manual'?manualGameplaySelection.has(p):
      p.source_record_id===session.opportunities&&session.controls?.some(c=>c.point_id===p.point_id&&c.checked)));
    if(!selected.length){message('请先勾选至少一个交互点，再点击“下一步：选择玩法”。',true);return;}
    const ids=new Set(selected.map(p=>p.id));
    for(const p of state.points)p.gameplay_committed=ids.has(p.id);
    selectedId=selected[0].id;touch();switchView('design');
  }
  function change(p, values) {
    const changed = Object.keys(values).some(k => JSON.stringify(p[k]) !== JSON.stringify(values[k]));
    if (!changed) return;
    if (['time_s', 'opportunity', 'reason'].some(k => k in values && p[k] !== values[k])) p.stale = true;
    Object.assign(p, values); p.confirmed = false;
  }
  function choose(p, version) {
    if (!p.versions.some(v => v.id === version.id)) throw Error('方案不属于该点');
    const previous=p.versions.find(v=>v.id===p.selected);
    if(previous&&p.design){previous.design=clone(p.design);previous.brief=clone(p.brief||{});previous.confirmed=p.confirmed;}
    p.selected = version.id; p.design = clone(version.design); p.brief = clone(version.brief||{}); p.confirmed = false;
    delete p.merged_into;
    p.stale = Math.abs(p.time_s - version.time_s) > 0.000001;
  }
  function saveOption(p,versionId,design,brief,id){
    let version=p.versions.find(v=>v.id===versionId);
    if(versionId&&!version)throw Error('方案已被删除');
    if(!version){
      if(p.versions.length>=100)throw Error('该点最多保存100个方案');
      version={id,record_id:null,option_id:id,origin:'manual',label:'自行设计',time_s:p.time_s,design:cleanDesign(design)};
      p.versions.push(version);
    }
    // Save the old draft before editing or switching its selected version.
    const old=p.versions.find(v=>v.id===p.selected);
    if(old&&old.id!==version.id&&p.design){old.design=clone(p.design);old.brief=clone(p.brief||{});}
    version.design=cleanDesign(design);version.brief=clone(brief);version.edited=true;
    version.time_s=p.time_s;version.confirmed=false;
    p.selected=version.id;p.design=clone(version.design);p.brief=clone(version.brief);p.confirmed=false;
    return version;
  }
  function removeVersion(p, versionId) {
    if(!p.versions.some(v=>v.id===versionId))throw Error('方案不存在或已被删除');
    p.versions=p.versions.filter(v=>v.id!==versionId);
    if(p.selected===versionId){
      // Preserve edited content as a manual draft; do not silently select another recommendation.
      p.selected=null;p.confirmed=false;p.enabled=false;
    }
  }
  function confirmPoint(p) {
    if (p.stale || p.review !== 'kept' || !p.design) throw Error('请先保留该点并核对当前画面');
    if (!designFields.every(k => p.design[k]?.trim())) {
      throw Error('请补齐核心玩法说明');
    }
    p.confirmed = true;
  }
  function merge(state, from, target) {
    if (from.id === target.id || target.merged_into) throw Error('请选择其他未合并的点作为目标');
    let node = target;
    while (node?.merged_into) {
      if (node.merged_into === from.id) throw Error('不能循环合并');
      node = state.points.find(p => p.id === node.merged_into);
    }
    from.merged_into = target.id; from.review = 'discarded'; from.enabled = false; from.confirmed = false;
  }
  function ingest(state, records) {
    const opportunities = records.opportunities;
    if (opportunities && !state.seen_record_ids.includes(opportunities.record_id)) {
      (opportunities.analysis?.interaction_points || []).forEach((o, i) => {
        const pointId = o.point_id || `p${i+1}`;
        if((state.deleted_point_ids||[]).includes(`ai_${opportunities.record_id}_${pointId}`))return;
        state.points.push({...point(`ai_${opportunities.record_id}_${pointId}`, o.time_s, o.opportunity, o.reason, 'ai'),
          source_record_id:opportunities.record_id, point_id:pointId});
      });
      state.seen_record_ids.push(opportunities.record_id);
    }
    const gameplay = records.gameplay;
    if (gameplay && !state.seen_record_ids.includes(gameplay.record_id)) {
      for (const o of gameplay.analysis?.recommendations || []) {
        const p = state.points.find(p => p.source_record_id === gameplay.parent_record_id && p.point_id === o.point_id);
        if (!p) continue;
        const observation = gameplay.observations?.find(f => f.point_id === o.point_id);
        // Historical records without option IDs remain readable in the original editor.
        if (!o.option_id) continue;
        p.versions.push({id:`${gameplay.record_id}_${o.option_id}`, record_id:gameplay.record_id,
          option_id:o.option_id, time_s:observation?.requested_time_s ?? p.time_s,
          label:`${gameplay.model||'历史模型'} · ${gameplay.prompt_file || '历史版本'} · ${gameplay.record_id.slice(0,8)}`, design:cleanDesign(o)});
      }
      state.seen_record_ids.push(gameplay.record_id);
    }
    for (const r of Object.values(records).filter(Boolean)) {
      if (r.record_id && !state.record_ids.includes(r.record_id)) state.record_ids.push(r.record_id);
    }
  }
  function warnings(state) {
    const active = state.points.filter(p => p.enabled && p.review === 'kept').sort((a,b) => a.time_s-b.time_s);
    const result = [];
    active.forEach((p,i) => {
      if (!p.confirmed || p.stale) result.push(`${p.time_s.toFixed(2)} 秒：方案未确认或需要重新核对`);
      const previous = active[i-1]; if (!previous) return;
      if (p.time_s === previous.time_s) result.push(`${p.time_s.toFixed(2)} 秒同一时间存在多个启用交互，需合并、错开或停用`);
      else if (p.time_s-previous.time_s < state.density_s) result.push(`${previous.time_s.toFixed(2)}–${p.time_s.toFixed(2)} 秒交互较密集`);
      if (p.design?.game_id && p.design.game_id === previous.design?.game_id) result.push('相邻玩法类型相同，请检查是否重复');
    });
    return result;
  }
  function missingFields(p) {
    return designFields.map(k=>['design',k,labels[k]])
      .filter(([group,key])=>!p[group]?.[key]?.trim());
  }
  function workflowProgress(state,records={},library=null) {
    const points=state.points.filter(p=>p.review==='kept'&&!p.merged_into),selected=points.filter(p=>p.design).length;
    const events=records.events?.analysis?.events||[];
    const enabled=points.filter(p=>p.enabled).length;
    return {analysis:records.events?`已识别 ${events.length} 个事件`:'尚未识别事件',
      opportunities:records.opportunities||points.length?`${points.length} 个交互点`:'等待寻找机会',
      design:`${selected} / ${points.length} 个点已选玩法`,
      edit:`${points.filter(p=>p.confirmed&&!p.stale).length} 个方案已确认`,
      games:library?`${library.points.flatMap(p=>p.versions).filter(v=>v.usable).length} 个可用版本`:'游戏库尚未读取',
      arrange:`${enabled} 个点已启用（需检查）`};
  }
  const Model = {blank, point, change, choose, saveOption, removeVersion, confirmPoint, merge, ingest, warnings, cleanDesign, missingFields, workflowProgress, gameReadyPoints};
  root.WorkbenchModel = Model;
  if (typeof document === 'undefined') return;

  let bridge, project, state=blank(), dirty=false, saving=false, timer, verified=false, mediaEpoch=0;
  let currentView='analysis', selectedId=null, loading=false, initialized=false, saveAgain=false, retrying=false;
  const collapsedDraftEditors=new WeakSet();
  const editorGroups=new Map();
  let libraryProgress=null;
  const $ = id => document.getElementById(id);
  const make = (tag, text, className) => {const e=document.createElement(tag); if(text!==undefined)e.textContent=text;
    if(className)e.className=className; return e;};
  const button = (text, action, className='ghost') => {const b=make('button',text,className);b.type='button';
    b.onclick=async()=>{try{await action();}catch(error){message(error.message,true);}};return b;};
  function message(value, error=false) { $('projectStatus').textContent=value; $('projectStatus').className=error?'wb-error':'wb-save-state'; }
  function deleteRecommendation(p,v){
    const selected=p.selected===v.id;
    if(!root.confirm(`删除推荐方案“${v.design.title||gameName(v.design.game_id)}”？\n仅从当前项目的推荐列表移除，不删除原始模型记录或已生成游戏。${selected?'\n这是当前所选方案：已编辑内容会保留为草稿，需要重新确认；不自动选择其他方案。':''}`))return;
    removeVersion(p,v.id);touch(true);
  }
  function duration() { return state.video?.duration_s || bridge.duration() || 0; }
  function checkTime(value) { const n=Number(value); if(String(value).trim()===''||!Number.isFinite(n)||n<0||n>duration())throw Error('位置须在视频时长范围内');return n; }
  function seek(time) { if(!verified){message('请先重新选择同一视频，哈希匹配后才能回看',true);return;} bridge.seek(time); }
  async function api(url, payload) {
    const response=await fetch(url,{method:payload?'POST':'GET',headers:{'X-Local-Token':bridge.token(),
      ...(payload?{'Content-Type':'application/json'}:{})},...(payload?{body:JSON.stringify(payload)}:{})});
    const value=await response.json();if(!response.ok)throw Error(value.error || '本地项目请求失败');return value;
  }
  function touch(repaint=false) {
    if(loading)return;
    dirty=true;message('有未保存修改…');clearTimeout(timer);timer=setTimeout(()=>save(),700);
    if(repaint)render();
    else {const p=state.points.find(p=>p.id===selectedId);if(p&&$('wbEditingState'))$('wbEditingState').textContent=status(p);}
  }
  async function save() {
    clearTimeout(timer);if(loading||!dirty)return true;
    if(saving){saveAgain=true;return false;}
    saving=true;const snapshot=JSON.stringify(state);message('正在保存项目…');
    try {
      const value=await api('/projects',{project_id:project?.project_id,revision:project?.revision,state:JSON.parse(snapshot)});
      project=value;dirty=JSON.stringify(state)!==snapshot;
      message(dirty?'保存后又有新修改…':`已保存 · 修订 ${value.revision}`);
      refreshList();return !dirty;
    } catch(error) {dirty=true;message(`${error.message}（未保存；可重试保存或在项目管理中导出草稿备份）`,true);return false;}
    finally {saving=false;if(saveAgain){saveAgain=false;if(dirty)timer=setTimeout(()=>save(),100);}render();}
  }
  async function flush() {
    if(saving)throw Error('正在保存，请稍后再切换项目');
    if(dirty && !await save())throw Error('当前修改未保存，请重试或导出备份后再切换');
  }
  async function refreshList() {
    try {const items=await api('/projects');const select=$('projectList');const chosen=select.value;
      select.replaceChildren(make('option','选择已保存项目…'));select.firstChild.value='';
      items.forEach(p=>{const o=make('option',`${p.name}${p.video_name?' · '+p.video_name:''}`);o.value=p.project_id;select.append(o);});
      select.value=project?.project_id||chosen;
    }catch(error){message(error.message,true);}
  }
  function sourceKey(source) { return source.sample || (source.file ? `${source.file.name}:${source.file.size}:${source.file.lastModified}` : ''); }
  async function verifySource() {
    if(!initialized)return;
    const epoch=++mediaEpoch;verified=false;bridge.ready();
    const source=bridge.source();
    if(!sourceKey(source)){message(state.video?'重新选择同一视频后可回看和调用模型；文字草稿可继续编辑':'请选择视频，或打开已保存项目');return;}
    try {
      message('正在核对视频 SHA-256（本地操作）…');
      const data=source.file?await source.file.arrayBuffer():await (await fetch('/sample?name='+encodeURIComponent(source.sample))).arrayBuffer();
      const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',data)),b=>b.toString(16).padStart(2,'0')).join('');
      if(epoch!==mediaEpoch)return;
      if(state.video && state.video.sha256!==hash){message('视频与当前项目不一致，请重新选择原视频，或新建项目；未调用模型',true);return;}
      if(!state.video){state.video={name:source.sample||source.file.name,sample:source.sample||'',sha256:hash,duration_s:bridge.duration()||0};touch();}
      if(state.session.events&&!bridge.peek().records.events){
        const records={};loading=true;
        try{
          for(const key of ['events','opportunities','gameplay'])if(state.session[key]){
            try{
              records[key]=await api('/project-record?record_id='+state.session[key]);
              if(records[key].video_sha256!==hash){delete records[key];throw Error('关联记录与项目视频不一致，未恢复');}
            }catch(error){message(error.message+'；项目草稿仍保留',true);}
          }
          if(epoch!==mediaEpoch)return;
          bridge.restore(state.session,records,state.video,true);
        }finally{loading=false;}
      }
      verified=true;message(dirty?'视频已匹配，项目修改待保存':'视频已匹配，可回看和继续分析');notify();
    }catch(error){if(epoch===mediaEpoch)message('视频核验失败：'+error.message,true);}
    finally{bridge.ready();render();}
  }
  function notify() {
    if(!initialized||loading)return;
    const before=JSON.stringify(state);const current=bridge.peek();
    // Only ingest new inference records when their video identity matches this project.
    if(state.video){
      const records=Object.fromEntries(Object.entries(current.records).filter(([,r])=>!r||r.video_sha256===state.video.sha256));
      ingest(state,records);
      const d=bridge.duration()||Object.values(records).find(r=>r?.metadata?.duration_s)?.metadata.duration_s;
      if(d && verified)state.video.duration_s=d;
    }
    if(verified&&!bridge.isBusy()&&!retrying){
      if(state.session.opportunities===current.session.opportunities){
        for(const c of current.session.controls||[]){
          const previous=state.session.controls?.find(x=>x.point_id===c.point_id);
          const p=state.points.find(x=>x.source_record_id===current.session.opportunities&&x.point_id===c.point_id);
          if(previous&&p&&previous.time_s!==c.time_s)change(p,{time_s:c.time_s});
        }
      }
      state.session=current.session;
    }
    if(JSON.stringify(state)!==before){touch();render();}
    renderTimeline();
  }
  function switchView(view,pointId) {
    if(view==='edit')view='design'; // Historical links now share the recommendation/description workspace.
    if(!['analysis','opportunities','design','games','arrange'].includes(view))return;
    if(view==='design'&&!availableGameplayPoints().length){message('请先在第二步勾选交互点，并点击“下一步：选择玩法”。',true);return;}
    if(pointId&&state.points.some(p=>p.id===pointId))selectedId=pointId;
    currentView=view;document.body.dataset.workbenchView=view;
    document.querySelectorAll('[data-wb-tab]').forEach(b=>{
      b.setAttribute('aria-selected',String(b.dataset.wbTab===view));
      if(b.dataset.wbTab===view)b.setAttribute('aria-current','step');else b.removeAttribute('aria-current');
    });
    const headings={analysis:['视频分析','识别可观察事件，回看画面并排除失实描述。'],
      opportunities:['交互点管理','寻找交互机会，先确定定位时间、参与行为和理由，再推荐玩法。'],
      design:['玩法推荐与选择','选择或自行设计方案，在弹窗完善并确认描述，再生成游戏。'],
      games:['游戏生成与管理','按单个玩点生成游戏，管理版本并独立试玩。'],
      arrange:['视频交互编排','选择采用版本，安排参与顺序并连续试玩。']};
    if($('wbViewTitle'))$('wbViewTitle').textContent=headings[view][0];
    if($('wbViewHint'))$('wbViewHint').textContent=headings[view][1];
    render();
  }
  async function openProject(id) {
    if(!id)return;await flush();if(bridge.isBusy())throw Error('模型调用期间不能切换项目');
    loading=true;
    try {
      const value=await api('/project?id='+encodeURIComponent(id));project=value;state=clone(value.state);dirty=false;
      selectedId=state.points[0]?.id||null;verified=false;++mediaEpoch;
      const records={};const errors=[];
      for(const key of ['events','opportunities','gameplay']) {
        if(!state.session[key])continue;
        try{records[key]=await api('/project-record?record_id='+state.session[key]);
          if(records[key].video_sha256!==state.video?.sha256){delete records[key];throw Error('关联记录的视频哈希与项目不一致，未恢复该分析结果');}}
        catch(error){errors.push(error.message);}
      }
      bridge.restore(state.session,records,state.video);$('projectName').value=state.name;
      message(errors.length?errors.join('；'):'项目已打开。上传视频需重新选择原文件；样例将自动核对。',!!errors.length);
    }finally{loading=false;render();bridge.ready();}
    if(bridge.source().sample)await verifySource();
  }
  async function newProject() {
    await flush();if(bridge.isBusy())throw Error('模型调用期间不能新建项目');
    loading=true;bridge.restore({}, {}, null);project=null;state=blank();dirty=false;verified=false;++mediaEpoch;
    selectedId=null;$('projectName').value=state.name;loading=false;message('新项目：请选择视频');render();bridge.ready();
  }
  async function deleteProject() {
    if(!project)throw Error('请先打开要删除的已保存项目');
    if(loading||saving||bridge.isBusy())throw Error('正在处理项目或调用模型，请稍后删除');
    if(!root.confirm(`删除当前项目“${state.name}”？\n项目及修订将移入本地回收目录，原视频和模型原始结果不受影响。`))return;
    await flush();
    loading=true;clearTimeout(timer);render();
    try {
      await api('/project-delete',{project_id:project.project_id,revision:project.revision});
      project=null;state=blank();dirty=false;verified=false;selectedId=null;++mediaEpoch;
      bridge.restore({}, {}, null);$('projectName').value=state.name;
      await refreshList();$('projectList').value='';
      message('项目已删除并移入 projects/.trash，可手动恢复；原视频和模型结果未删除');
    }finally{loading=false;render();bridge.ready();}
  }
  function textInput(label,value,action,multiline=true) {
    const wrap=make('label',label);const input=make(multiline?'textarea':'input');input.value=value||'';
    input.oninput=()=>action(input.value);wrap.append(input);return wrap;
  }
  function selection(label, values, value, action) {
    const wrap=make('label',label), select=make('select');values.forEach(([id,name])=>{const o=make('option',name);o.value=id;select.append(o);});
    select.value=value;select.onchange=()=>action(select.value);wrap.append(select);return wrap;
  }
  function status(p) {return p.review==='discarded'?'已停用':p.stale?'需要重新核对':p.confirmed?'描述已确认':p.design?'已选草稿':'未选玩法';}
  function renderTimeline() {
    const container=$('wbTimeline');if(!container)return;container.replaceChildren();
    const track=make('div',undefined,'wb-track');container.append(track);const d=duration();
    [...state.points].sort((a,b)=>a.time_s-b.time_s).forEach(p=>{const b=button(`${p.time_s.toFixed(2)}s`,()=>{selectedId=p.id;seek(p.time_s);render();},'wb-marker');
      b.style.left=`${d?p.time_s/d*100:0}%`;b.title=`${p.opportunity} · ${status(p)}`;b.dataset.state=p.review;
      b.setAttribute('aria-label',b.title);track.append(b);});
    const current=make('span',`${bridge.time().toFixed(2)} / ${d.toFixed(2)} 秒`,'wb-playhead');container.append(current);
  }
  function renderPoints() {
    const list=$('wbPointList');list.replaceChildren();
    if(!state.points.length)list.append(make('p','尚无项目交互点。可以先分析视频，也可以在当前播放位置人工新增。'));
    const visible=currentView==='design'?availableGameplayPoints():currentView==='games'?gameReadyPoints(state.points):state.points;
    if(['design','games'].includes(currentView)&&!visible.some(p=>p.id===selectedId))selectedId=visible[0]?.id||null;
    if(currentView==='games'&&!visible.length)list.append(make('p','尚无已选玩法，请先在第三步选择或保存方案。'));
    for(const p of [...visible].sort((a,b)=>a.time_s-b.time_s)) {
      const card=make('article',undefined,'wb-point'+(p.id===selectedId?' selected':''));
      const version=p.versions.find(v=>v.id===p.selected)||p.versions[0];
      if(version){const img=make('img');img.loading='lazy';img.alt=`${p.time_s.toFixed(2)} 秒关联推荐画面`;
        img.src='/gameplay-frame?'+new URLSearchParams({record_id:version.record_id,point_id:p.point_id});img.onerror=()=>img.remove();card.append(img);}
      card.append(make('small',`${p.origin==='manual'?'人工新增':'AI 候选'} · ${status(p)}`),
        button(`${p.time_s.toFixed(2)} 秒 · ${p.opportunity}`,()=>{selectedId=p.id;seek(p.time_s);render();}));
      card.append(make('p',p.reason));list.append(card);
    }
  }
  function toggleDraftEditor(p) {
    if(p.design){
      if(collapsedDraftEditors.has(p))collapsedDraftEditors.delete(p);else collapsedDraftEditors.add(p);
      renderEditor();return;
    }
    p.design={title:'人工玩法',game_id:bridge.catalog()[0]?.id||'timing',...Object.fromEntries(designFields.map(k=>[k,'']))};
    p.selected=null;p.confirmed=false;p.review='kept';delete p.merged_into;p.brief=p.brief||{};
    collapsedDraftEditors.delete(p);touch(true);
  }
  function editorGroup(p,key,title,open=false) {
    const identity=`${project?.project_id||'unsaved'}:${p.id}:${key}`;
    const group=make('details',undefined,'wb-editor-group');group.append(make('summary',title));
    group.open=editorGroups.has(identity)?editorGroups.get(identity):open;
    group.addEventListener('toggle',()=>editorGroups.set(identity,group.open));return group;
  }
  function renderProgress() {
    const progress=workflowProgress(state,bridge.peek().records,libraryProgress?.project_id===project?.project_id&&libraryProgress?.project_revision===project?.revision&&!dirty?libraryProgress:null);
    document.querySelectorAll('[data-wb-tab]').forEach(b=>{
      let badge=b.querySelector('[data-wb-progress]');
      if(!badge){b.setAttribute('aria-label',b.textContent);badge=make('small');badge.setAttribute('data-wb-progress','');badge.setAttribute('aria-hidden','true');b.append(badge);}
      badge.textContent=progress[b.dataset.wbTab];b.title=progress[b.dataset.wbTab];
    });
    const hint=$('wbNextHint');if(!hint)return;
    const p=state.points.find(p=>p.id===selectedId);
    const hints={analysis:!state.video?'先选择视频，再识别可观察事件。':'识别后请回看并勾选可信事件，再前往第二步。',
      opportunities:!bridge.peek().records.events?'先在第一步识别并勾选事件，或在当前画面人工新增交互点。':'先在交互点设置中调整定位、参与行为和理由，再勾选候选点，前往第三步推荐玩法。',
      design:!state.points.length?'先在第二步寻找或人工新增交互点。':'选择或自行设计方案，打开玩法描述弹窗确认后，前往第四步生成游戏。',
      games:!project?'先保存项目，再生成或管理游戏。':!verified?'先回第一步重新选择同一视频，核对后才能试玩。':'为当前点生成游戏，试玩后采用合适版本，再前往第五步编排。',
      arrange:'先检查启用点、采用版本和时间冲突；保存成功后再开始串联。'};
    hint.textContent=hints[currentView];
  }
  function openOptionEditor(p,version,fromDraft=false){
    const useDraft=fromDraft||version?.id===p.selected;
    const draft={design:clone(useDraft&&p.design?p.design:version?.design||{title:'自行设计',game_id:bridge.catalog()[0]?.id||'tap_object'}),
      brief:clone(useDraft?p.brief||{}:version?.brief||{})};
    const dialog=make('dialog',undefined,'wb-dialog wb-dialog-wide');dialog.setAttribute('aria-label','玩法描述编辑');
    const head=make('div',undefined,'wb-dialog-head'),body=make('div',undefined,'wb-dialog-body');
    let changed=false;
    const close=()=>{if(changed&&!root.confirm('放弃本次尚未保存的玩法描述？'))return;dialog.close();dialog.remove();};
    head.append(make('h2',version?'编辑玩法描述':'自行设计新方案'),button('关闭',close));
    body.append(make('p',`对应交互点：${p.time_s.toFixed(2)} 秒 · ${p.opportunity}`));
    const fields={};
    body.append(textInput('方案名称',draft.design.title||'',v=>{draft.design.title=v;changed=true;},false));
    body.append(selection('玩法类型',bridge.catalog().map(t=>[t.id,t.name]),draft.design.game_id,v=>{draft.design.game_id=v;changed=true;}));
    for(const key of designFields){const field=textInput(labels[key],draft.design[key]||'',v=>{draft.design[key]=v;changed=true;});fields[key]=field.querySelector('textarea');body.append(field);}
    const extra=make('details');extra.append(make('summary','补充画面与衔接（选填）'));
    for(const [key,label] of Object.entries(briefLabels))extra.append(textInput(label,draft.brief[key]||'',v=>{draft.brief[key]=v;changed=true;}));
    body.append(extra);
    const error=make('p');error.setAttribute('role','alert');body.append(error);
    const commit=async confirm=>{
      if(confirm){
        const missing=designFields.filter(k=>!draft.design[k]?.trim());
        if(missing.length){error.textContent='请补充：'+missing.map(k=>labels[k]).join('、');fields[missing[0]].focus();return;}
        if(p.stale||p.review!=='kept'){error.textContent='请先回看并核对交互点画面，或先保存为草稿。';return;}
      }
      const saved=saveOption(p,version?.id||null,draft.design,draft.brief,'manual_'+crypto.randomUUID().replaceAll('-',''));
      version=saved;
      if(confirm){confirmPoint(p);saved.confirmed=true;}
      touch(true);
      if(await save()){changed=false;close();}else error.textContent='未保存到磁盘，内容仍保留在项目草稿中，请重试保存。';
    };
    body.append(button('保存方案草稿',()=>commit(false)),button('保存并确认玩法描述',()=>commit(true)));
    dialog.append(head,body);dialog.addEventListener('cancel',event=>{event.preventDefault();close();});document.body.append(dialog);dialog.showModal();
  }
  function renderEditor() {
    const host=$('wbEditor');host.replaceChildren();const p=state.points.find(p=>p.id===selectedId);
    if(!p){host.append(make('p',currentView==='games'?'尚无可生成的已选方案，请先在第三步选择或保存玩法。':'尚无交互点，请先在“寻找交互机会”中分析或新增。'));return;}
    if(currentView==='design'){
      host.append(make('h2',`${p.time_s.toFixed(2)} 秒 · ${p.opportunity}`),make('p',p.reason));
      renderOptionCards(host,p);
      host.append(button('下一步：生成游戏 →',()=>switchView('games')));
      return;
    }
    if(currentView==='games'){
      host.append(make('h2',`${p.time_s.toFixed(2)} 秒 · ${p.design?.title||p.opportunity}`));
      if(!p.design){host.append(make('p','尚未选择玩法，请先选择或编辑方案。'),button('选择玩法 →',()=>switchView('design')));return;}
      host.append(make('p',`${gameName(p.design.game_id)} · ${p.design.player_action||'请先补充玩家操作'}`),
        button('编辑当前方案',()=>switchView('edit')));
      if(root.GameWorkbench)root.GameWorkbench.offer(host,p,async()=>{
        await flush();if(!project)throw Error('请先保存项目');
        return {project,point:p,verified,token:bridge.token(),isBusy:()=>bridge.isBusy()};
      },true);
      return;
    }
    host.append(make('h2',`${p.time_s.toFixed(2)} 秒 · ${p.opportunity}`),make('p',p.reason),button('返回交互点管理',()=>switchView('opportunities',p.id)));
    renderOptionCards(host,p);
  }
  function renderPointSettings(){
    const host=$('wbPointSettingsBody');if(!host)return;host.replaceChildren();
    const p=state.points.find(p=>p.id===selectedId);
    if(!p){host.append(make('p','暂无交互点。先寻找机会，或在当前画面人工新增。'));return;}
    host.append(selection('选择要编辑的交互点',state.points.map(p=>[p.id,`${p.time_s.toFixed(2)} 秒 · ${p.opportunity}`]),p.id,id=>{selectedId=id;render();}),make('p',`来源：${p.origin==='ai'?'AI 候选；修改不覆盖原始模型记录':'人工新增'}`));
    if(p.origin==='manual'){
      const label=make('label','送入第三步');const check=make('input');check.type='checkbox';check.checked=manualGameplaySelection.has(p);
      check.onchange=()=>{if(check.checked)manualGameplaySelection.add(p);else manualGameplaySelection.delete(p);};label.prepend(check);host.append(label);
    }
    const latest=bridge.peek().records.gameplay;
    const opportunities=bridge.peek().records.opportunities;
    const outputWarnings=[...(opportunities?.analysis?.warnings||[]),...(latest?.analysis?.warnings||[]),...(latest?.request_warnings||[])];
    if(outputWarnings.length){const aside=make('details',undefined,'validation-warnings');
      aside.append(make('summary',`查看 ${outputWarnings.length} 项分析警告`));
      outputWarnings.forEach(w=>aside.append(make('p',w.message)));host.append(aside);}
    const contextGroup=make('section');contextGroup.append(make('h3','定位与交互点设置'));host.append(contextGroup);
    const time=textInput('定位时间（秒）',String(p.time_s),()=>{},false);const timeBox=time.querySelector('input');timeBox.type='number';timeBox.step='0.1';
    timeBox.onchange=()=>{try{change(p,{time_s:checkTime(timeBox.value)});bridge.setPointTime?.(p.point_id,p.source_record_id,p.time_s);touch(true);}catch(e){message(e.message,true);timeBox.value=p.time_s;}};
    contextGroup.append(time,button('回看前后 2 秒',()=>{seek(Math.max(0,p.time_s-2));bridge.previewEnd(Math.min(duration(),p.time_s+2));}),
      textInput('玩家可以参与什么',p.opportunity,v=>{change(p,{opportunity:v});touch();}),
      textInput('为什么适合（人工备注与原始理由分开保存）',p.reason,v=>{change(p,{reason:v});touch();}));
    if(p.review==='discarded')contextGroup.append(make('p','此历史点已停用，不会自动参与播放。'),button('恢复参与编辑',()=>{p.review='kept';delete p.merged_into;p.enabled=false;touch(true);}));
    if(root.GameWorkbench)contextGroup.append(button('删除交互点及关联游戏',()=>root.GameWorkbench.deletePoint(p.id)));
    const targets=state.points.filter(x=>x.id!==p.id&&!x.merged_into);
    if(targets.length){const sel=selection('合并到其他点（保留本点来源，不自动搬移方案）',[['','请选择'],...targets.map(x=>[x.id,`${x.time_s.toFixed(2)} 秒 · ${x.opportunity}`])],'',v=>{
      if(v){merge(state,p,state.points.find(x=>x.id===v));touch(true);}
    });contextGroup.append(sel);}
    if(p.stale)host.append(make('p','定位或情境已改变，已有方案和游戏需要重新核对。'),button('已回看当前画面，确认适配',()=>{if(!verified)throw Error('请先选择原视频并回看');p.stale=false;p.confirmed=false;touch(true);}));
    host.append(button('下一步：推荐与选择玩法 →',commitGameplaySelection));
  }
  function renderOptionCards(host,p){
    const alternatives=make('section');alternatives.append(make('h3','玩法方案'),make('p','每个方案有独立描述。选择用于当前交互点的方案，或打开弹窗编辑。'));host.append(alternatives);
    const grid=make('div',undefined,'option-grid');alternatives.append(grid);
    if(!p.versions.length)grid.append(make('p','尚无可比较的推荐。可运行原有第三步，或自行设计。历史无 option_id 的方案在兼容编辑器中查看。'));
    p.versions.forEach(v=>{const card=make('article',undefined,'option-card');
      card.append(make('h3',v.design.title||gameName(v.design.game_id)),make('small',`${v.label} · 画面 ${v.time_s.toFixed(2)} 秒`));
      for(const k of ['player_goal','player_action','fit'])card.append(make('p',`${labels[k]}：${v.design[k]}`));
      card.append(button(p.selected===v.id?'当前所选':'选择此版本',()=>{choose(p,v);p.review='kept';touch(true);}),button('查看／编辑玩法描述',()=>openOptionEditor(p,v)),button('删除方案',()=>deleteRecommendation(p,v)));grid.append(card);});
    if(currentView==='design'&&p.origin==='ai')host.append(button('仅重新推荐此点（计费，需确认）',async()=>{
      if(!verified)throw Error('请先选择与项目匹配的视频');
      if(p.source_record_id!==bridge.peek().records.opportunities?.record_id)throw Error('该点不在当前分析批次，请先打开其对应项目工作状态；旧方案不会丢失');
      const confirmation=make('aside',undefined,'validation-warnings');
      confirmation.append(make('p','仅为当前点调用一次原有第三步流程，会计费。保留旧方案，不自动替换当前选择。'),
        button('确认并重新推荐当前点',async()=>{
          confirmation.remove();retrying=true;
          try{await bridge.retry(p.point_id,p.time_s);}finally{retrying=false;notify();render();}
        }),
        button('取消重新推荐',()=>confirmation.remove()));host.append(confirmation);
    }));
    host.append(button('自行设计新方案',()=>openOptionEditor(p,null)),button('前往游戏生成与管理 →',()=>switchView('games')));
    if(p.design&&!p.selected)host.append(button('将现有草稿另存为新方案',()=>openOptionEditor(p,null,true)));
    if(p.stale)host.append(make('p','交互点已变化，请返回第二步回看并核对适配性。'),button('回到交互点管理核对',()=>switchView('opportunities',p.id)));
    host.append(textInput('选择或修改理由',p.decision_reason,v=>{p.decision_reason=v;touch();}));
  }
  function gameName(id) {return bridge.catalog().find(t=>t.id===id)?.name||id;}
  function renderArrangement() {
    const host=$('wbArrangement');host.replaceChildren();
    host.append(make('h2','编排与视频试玩'),make('p','选择各点的游戏版本并保存；从头播放，到点自动进入游戏，结束后继续原片。'));
    if(root.GameWorkbench){const editor=make('div');host.append(editor);root.GameWorkbench.renderArrangement(editor);host.append(button('导出全部描述草稿',()=>download(false)));return;}
    const threshold=textInput('节奏提醒阈值（秒，仅作提醒）',String(state.density_s),()=>{},false);
    const input=threshold.querySelector('input');input.type='number';input.min='0';input.max='60';input.onchange=()=>{
      try{const v=Number(input.value);if(!input.value.trim()||!Number.isFinite(v)||v<0||v>60)throw Error('提醒阈值应在 0–60 秒内');state.density_s=v;touch(true);}catch(e){message(e.message,true);}
    };host.append(threshold);
    const notes=warnings(state);if(notes.length){const aside=make('aside',undefined,'validation-warnings');notes.forEach(n=>aside.append(make('p',n)));host.append(aside);}
    const kept=state.points.filter(p=>p.review==='kept').sort((a,b)=>a.time_s-b.time_s);
    if(!kept.length)host.append(make('p','尚无保留的交互点。请先审核和选择玩法。'));
    for(const p of kept){const row=make('article',undefined,'wb-arrangement-row');
      const toggle=make('input');toggle.type='checkbox';toggle.checked=p.enabled;toggle.setAttribute('aria-label',`启用 ${p.opportunity}`);
      toggle.onchange=()=>{p.enabled=toggle.checked;touch(true);};const enabled=make('label','启用');enabled.prepend(toggle);
      row.append(enabled,make('h3',`${p.time_s.toFixed(2)} 秒 · ${p.design?.title||p.opportunity}`),make('p',`${gameName(p.design?.game_id||'未选玩法')} · ${status(p)}`),
        button('返回编辑',()=>{selectedId=p.id;switchView('edit');}),button('回看',()=>seek(p.time_s)));
      const detail=make('details'),summary=make('summary','查看完整描述');detail.append(summary);
      for(const [k,label] of Object.entries({...labels,...briefLabels})){detail.append(make('p',`${label}：${p.design?.[k]||p.brief[k]||'待补充'}`));}
      row.append(detail);host.append(row);
    }
    host.append(button('导出全部草稿',()=>download(false)),button('导出已确认且启用方案',()=>download(true)));
  }
  async function download(ready) {
    if(!await save()&&dirty)throw Error('先成功保存项目，再导出确认集合；可在项目管理中导出草稿备份');
    if(!project)throw Error('请先保存项目');
    const value=await api(`/project-export?id=${project.project_id}&ready=${ready?1:0}`);
    downloadJSON(value,`${state.name}-${ready?'已确认方案':'全部草稿'}.json`);
  }
  function downloadJSON(value,name) {const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
    const a=make('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function render() {
    if(!initialized)return;
    if(!state.points.some(p=>p.id===selectedId))selectedId=state.points[0]?.id||null;
    if(root.GameWorkbench)root.GameWorkbench.sync();
    renderProgress();
    renderTimeline();renderPoints();if(currentView==='opportunities')renderPointSettings();if(['design','games'].includes(currentView))renderEditor();if(currentView==='arrange')renderArrangement();
    $('wbMediaHint').textContent=state.video?`${state.video.name} · ${verified?'视频已匹配':'需重新选择同一视频'} · 原始模型结果不受本地编辑影响`:'未绑定视频';
    const locked=bridge.isBusy()||loading;for(const id of ['newProject','openProject','projectList','saveProject','saveProjectCopy','projectName'])$(id).disabled=locked;
    $('deleteProject').disabled=locked||saving||!project;
  }
  function init(adapter) {
    bridge=adapter;initialized=true;
    if(root.GameWorkbench)root.GameWorkbench.init(()=>({project,state,verified,busy:bridge.isBusy()||saving,token:bridge.token(),
      reportLibrary:data=>{libraryProgress=data;renderProgress();},
      beforeMutation:flush,applyProject:value=>{
        project=value;state=clone(value.state);dirty=false;
        if(!state.points.some(p=>p.id===selectedId))selectedId=state.points[0]?.id||null;
        render();refreshList();message('项目与游戏编排已保存');
      }}));
    $('projectName').value=state.name;$('projectName').oninput=()=>{state.name=$('projectName').value;touch();};
    $('saveProject').onclick=()=>save();$('newProject').onclick=()=>newProject().catch(e=>message(e.message,true));
    $('saveProjectCopy').onclick=async()=>{
      if(saving||bridge.isBusy()){message('正在保存或调用模型，请稍后另存',true);return;}
      clearTimeout(timer);project=null;state.name=state.name.slice(0,110)+' · 副本';$('projectName').value=state.name;dirty=true;await save();
    };
    $('openProject').onclick=()=>openProject($('projectList').value).catch(e=>message(e.message,true));
    $('deleteProject').onclick=()=>deleteProject().catch(e=>message(e.message,true));
    $('backupProject').onclick=()=>downloadJSON({purpose:'unsaved_draft_backup',state},`${state.name}-本地草稿备份.json`);
    for(const [openId,dialogId,closeId] of [['openProjectMenu','projectDialog','closeProjectDialog'],['openTokenDialog','tokenDialog','closeTokenDialog'],['openPromptDialog','promptDialog','closePromptDialog']]){
      $(openId).onclick=()=>{const dialog=$(dialogId);if(!dialog.open)dialog.showModal();};
      $(closeId).onclick=()=>$(dialogId).close();
    }
    $('addManualPoint').onclick=()=>{try{
      if(!verified||!duration())throw Error('先选择并核对可播放的视频');
      const p=point('manual_'+crypto.randomUUID().replaceAll('-',''),checkTime(bridge.time()),'请填写玩家可参与的行为','请填写与当前画面的关系');
      state.points.push(p);selectedId=p.id;touch();switchView('opportunities');
    }catch(e){message(e.message,true);}};
    document.querySelectorAll('[data-wb-tab]').forEach(b=>b.onclick=()=>switchView(b.dataset.wbTab));
    document.querySelectorAll('[data-wb-go]').forEach(b=>b.onclick=()=>b.dataset.wbGo==='design'&&currentView==='opportunities'?commitGameplaySelection():switchView(b.dataset.wbGo));
    for(const id of ['sample','file']){
      const original=$(id).onchange;
      $(id).onchange=async function(event){
        if(bridge.isBusy())return;
        // Preserve unsaved project work even though the original analysis panel resets its transient view.
        verified=false;++mediaEpoch;await original.call(this,event);await verifySource();
      };
    }
    $('video').addEventListener('loadedmetadata',()=>{if(verified&&state.video){state.video.duration_s=bridge.duration();touch();render();}});
    $('video').addEventListener('timeupdate',renderTimeline);
    document.addEventListener('change',()=>{notify();});
    root.addEventListener('beforeunload',event=>{if(dirty||saving){event.preventDefault();event.returnValue='';}});
    switchView('analysis');refreshList();message('项目工作台已就绪；分析与推荐策略保持不变');
  }
  root.Workbench={init,navigate:switchView,notify,mediaReady:()=>verified,sourceChanged:verifySource,
    gameplayPointIds:()=>availableGameplayPoints().filter(p=>p.source_record_id===bridge.peek().session.opportunities).map(p=>p.point_id)};
})(typeof window==='undefined'?globalThis:window);
