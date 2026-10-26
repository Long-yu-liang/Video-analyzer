/* Game workbench: host owns video; generated code runs only inside an opaque sandbox. */
(function(root) {
  'use strict';
  let current, panel, busy=false, selectedRecord=null, session=null, sequence=null;
  let theater=null, movingPlayer=false;
  let libraryHost=null, libraryKey='', libraryEpoch=0;
  let arrangementDraft=null;
  let versionDialog=null;
  let returnToVersion=null, libraryRenderedProject=null;
  let taskHost=null,taskTimer=null,taskProject=null,taskEpoch=0;
  const taskStates=new Map();
  const versionDrafts=new Map(),libraryExpansion=new Map();
  const $=id=>document.getElementById(id);
  const element=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  const button=(text,action)=>{const b=element('button',text);b.type='button';
    if(/生成|修复/.test(text)){b.setAttribute('data-game-generate','');b.disabled=busy;}
    b.onclick=()=>Promise.resolve().then(action).catch(showError);return b;};
  async function request(url,payload) {
    const r=await fetch(url,{method:payload?'POST':'GET',headers:{'X-Local-Token':current().token,...(payload?{'Content-Type':'application/json'}:{})},...(payload?{body:JSON.stringify(payload)}:{})});
    const value=await r.json();if(!r.ok)throw Error(value.error||'游戏工作台请求失败');return value;
  }
  function showError(error){const host=versionDialog?.dialog.open?versionDialog.body:panel;
    if(host){const p=element('p',error.message||String(error));p.className='gw-warning';host.append(p);}
    if(theater)theater.status.textContent=error.message||String(error);}
  function draftKey(record,context){return `video-analyzer:version-note:${context.project.project_id}:${record.record_id}`;}
  function readVersionDraft(record,context){
    const key=draftKey(record,context);if(versionDrafts.has(key))return versionDrafts.get(key);
    try{return JSON.parse(root.sessionStorage?.getItem(key)||'null');}catch{return null;}
  }
  function rememberVersion() {
    const view=versionDialog;if(!view?.note||!view.record)return;
    const value={note:view.note.value,review:view.review.value,scroll:view.body.scrollTop||0,
      expanded:[...(view.body.querySelectorAll?.('details')||[])].map(item=>item.open)};
    const key=draftKey(view.record,view.context);versionDrafts.set(key,value);
    try{root.sessionStorage?.setItem(key,JSON.stringify(value));}catch{/* In-memory draft remains available. */}
  }
  function closeVersionDialog() {
    if(!versionDialog)return;
    rememberVersion();
    const old=versionDialog;versionDialog=null;
    old.dialog.close();old.dialog.remove();
    if(old.focus?.isConnected)old.focus.focus();
  }
  function versionHost(record,context) {
    if(versionDialog){
      rememberVersion();
      versionDialog.project=context.project.project_id;
      versionDialog.title.textContent=`游戏版本详情 · ${record.input?.design?.title||context.point.design?.title||context.point.id}`;
      versionDialog.body.replaceChildren();versionDialog.body.scrollTop=0;
      return versionDialog.body;
    }
    const dialog=element('dialog');dialog.className='gw-version-dialog';dialog.setAttribute('aria-labelledby','gameVersionTitle');
    const header=element('div');header.className='gw-version-header';
    const title=element('h2',`游戏版本详情 · ${record.input?.design?.title||context.point.design?.title||context.point.id}`);title.id='gameVersionTitle';
    const close=button('关闭详情',closeVersionDialog);header.append(title,close);
    const body=element('div');body.className='gw-version-body';
    dialog.append(header,body);
    versionDialog={dialog,body,title,project:context.project.project_id,focus:document.activeElement};
    dialog.addEventListener('cancel',event=>{event.preventDefault();closeVersionDialog();});
    dialog.addEventListener('close',()=>{if(versionDialog?.dialog===dialog)closeVersionDialog();});
    document.body.append(dialog);dialog.showModal();close.focus();
    return body;
  }
  function inputView(point){return {time_s:point.time_s,opportunity:point.opportunity,reason:point.reason,design:point.design,brief:point.brief||{},selected:point.selected};}
  function matches(record,point) {
    if(!point||point.review!=='kept'||point.stale||point.merged_into)return false;
    const a=inputView(point),b=record.input;
    return ['time_s','opportunity','reason','design','brief','selected'].every(k=>JSON.stringify(a[k])===JSON.stringify(b[k]));
  }
  function init(getCurrent) {
    current=getCurrent;const outer=element('section');outer.id='gameWorkbenchPanel';document.querySelector('main').append(outer);
    libraryHost=element('div');libraryHost.id='gameLibrary';panel=element('div');panel.id='gameDetail';
    taskHost=element('div');taskHost.setAttribute('aria-live','polite');
    outer.append(taskHost,element('h2','项目游戏库'),libraryHost,panel);
    panel.append(element('p','生成结果自动保存，无需再次保存代码；采用版本单独保存。打开游戏库即可查看和试玩，不必重新调用模型。'));
    refreshLibrary();
    root.addEventListener('message',onMessage);
    const video=$('video');video.addEventListener('timeupdate',onTime);
    video.addEventListener('seeking',()=>{if(movingPlayer)return;if(sequence)sequence.last=null;
      if(session&&!session.positioning)closeGame('exit','播放位置改变',false);});
    video.addEventListener('seeked',()=>{if(sequence)sequence.last=video.currentTime;if(session)session.positioning=false;});
    video.addEventListener('emptied',()=>{if(movingPlayer)return;sequence=null;closeGame('exit','视频切换',false);if(theater)dismissTheater();});
    video.addEventListener('loadedmetadata',fitTheater);
  }
  async function mutate(url,payload) {
    const initial=current();if(busy||initial.busy||session||sequence)throw Error('请先结束生成、保存或试玩，再操作');
    const projectId=initial.project?.project_id;if(!projectId)throw Error('请先保存项目');
    await initial.beforeMutation?.();const now=current();
    if(now.project?.project_id!==projectId)throw Error('项目已切换');
    const value=await request(url,{...payload,project_id:projectId,revision:now.project.revision});
    if(current().project?.project_id===projectId&&value.project){arrangementDraft=null;current().applyProject?.(value.project);}
    refreshLibrary();return value;
  }
  async function selectVersion(pointId,recordId,enabled) {
    return mutate('/arrangement',{entries:[{point_id:pointId,generation_id:recordId,enabled}]});
  }
  async function deletePoint(pointId) {
    const now=current();await now.beforeMutation?.();
    const data=await request('/project-games?project_id='+current().project.project_id);
    const row=data.points.find(p=>p.point_id===pointId);if(!row)throw Error('交互点不存在');
    const tests=row.versions.reduce((n,v)=>n+v.playtest_count,0);
    if(!root.confirm(`删除“${row.title}”交互点？\n同时将 ${row.versions.length} 个游戏版本、产物及 ${tests} 条试玩记录移入项目回收站，可恢复。`))return;
    panel.replaceChildren();await mutate('/game-delete',{kind:'point',point_id:pointId});
  }
  async function deleteVersion(point,version) {
    if(!root.confirm(`删除游戏“${version.title}”？\n时间：${point.time_s}秒；试玩记录：${version.playtest_count}条；${version.selected?'当前采用版本将停用':'其他版本不受影响'}。\n记录与产物移入回收站，可恢复。`))return;
    await mutate('/game-delete',{kind:'game',generation_id:version.record_id});panel.replaceChildren();
  }
  async function showTrash() {
    const projectId=current().project?.project_id;if(!projectId)throw Error('请先打开项目');
    const items=await request('/game-trash?project_id='+projectId);panel.replaceChildren(element('h3','项目回收站（恢复后默认不参与播放）'));
    if(!items.length)panel.append(element('p','回收站为空。'));
    items.forEach(item=>{const card=element('section');card.append(element('h3',item.title),element('p',`${new Date(item.deleted_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})} · ${item.generation_count} 个游戏 · ${item.playtest_count} 条试玩`),button('恢复此项',async()=>{
      if(!root.confirm('恢复此项？已有同名文件或交互点不会被覆盖。'))return;
      await mutate('/game-restore',{operation_id:item.operation_id});await showTrash();
    }));panel.append(card);});
  }
  async function renderArrangement(host) {
    const initial=current();if(!initial.project){host.append(element('p','先保存项目，再编排游戏。'));return;}
    const projectId=initial.project.project_id;
    try {
      const data=await request('/project-games?project_id='+projectId);
      if(current().project?.project_id!==projectId)return;
      if(!arrangementDraft||arrangementDraft.projectId!==projectId)arrangementDraft={projectId,entries:data.points.map(p=>({point_id:p.point_id,generation_id:p.selected_generation_id||null,enabled:p.enabled}))};
      host.replaceChildren(element('p','选择游戏后默认参与；关闭参与开关仍保留所选版本。修改需保存后生效。'));
      const note=element('p',''),checkHost=element('div');checkHost.className='gw-preflight';
      const drawCheck=()=>{
        const result=checkArrangement(data,arrangementDraft?.entries||[],current().verified,current().state.points);
        checkHost.replaceChildren(element('h3',`开始前检查 · ${result.enabledCount} 个启用点`));
        for(const item of result.items)checkHost.append(element('p',`${item.time_s.toFixed(2)} 秒 · ${item.title}：${item.message}`));
        for(const issue of result.errors){const row=element('p',issue.text);row.className='gw-warning';
          if(root.Workbench)row.append(button('前往处理',()=>root.Workbench.navigate(issue.view,issue.point_id)));checkHost.append(row);}
        if(!result.errors.length)checkHost.append(element('p','本地检查通过；开始前仍会保存，并由服务端核对实际版本。'));
        return result;
      };
      host.append(note,button('保存编排',async()=>{await saveArrangement();}),button('开始编排试玩',async()=>{
        if(drawCheck().errors.length){note.textContent='请先处理下方检查项；尚未开始试玩。';checkHost.scrollIntoView({block:'nearest'});return;}
        await saveArrangement();await startSequence();
      }),checkHost);
      data.points.forEach(p=>{
        let entry=arrangementDraft.entries.find(e=>e.point_id===p.point_id);
        if(!entry){entry={point_id:p.point_id,generation_id:p.selected_generation_id||null,enabled:p.enabled};arrangementDraft.entries.push(entry);}
        const row=element('article');row.className='wb-arrangement-row';row.append(element('h3',`${p.time_s.toFixed(2)} 秒 · ${p.title}`));
        const select=element('select');select.setAttribute('aria-label',`${p.time_s}秒的游戏版本`);
        const empty=element('option','请选择已生成游戏');empty.value='';select.append(empty);
        if(entry.generation_id&&!p.versions.some(v=>v.record_id===entry.generation_id)){
          const missing=element('option','原选择已缺失，可清除或关闭参与');missing.value=entry.generation_id;select.append(missing);
        }
        p.versions.forEach(v=>{const option=element('option',`${new Date(v.created_at).toLocaleString('zh-CN')} · ${v.title} · ${v.usable?'可用':v.stale?'输入已变化':'生成失败'}`);option.value=v.record_id;option.disabled=!v.usable&&v.record_id!==entry.generation_id;select.append(option);});
        select.value=entry.generation_id||'';
        const toggle=element('input');toggle.type='checkbox';toggle.checked=entry.enabled;toggle.setAttribute('aria-label',`参与播放 ${p.title}`);
        select.onchange=()=>{entry.generation_id=select.value||null;entry.enabled=!!select.value;toggle.checked=entry.enabled;note.textContent='编排有未保存修改';drawCheck();};
        toggle.onchange=()=>{entry.enabled=toggle.checked;note.textContent='编排有未保存修改';drawCheck();};
        const label=element('label','参与本次播放');label.prepend(toggle);
        row.append(select,label,button('查看描述／单点试玩',async()=>{
          if(!entry.generation_id)throw Error('请先选择游戏');const r=await request('/game-generation?id='+entry.generation_id);
          const now=current(),point=now.state.points.find(x=>x.id===p.point_id);display(r,{project:now.project,point:JSON.parse(JSON.stringify(point))});
        }),button('删除交互点',()=>deletePoint(p.point_id)));host.append(row);
      });drawCheck();
    }catch(error){host.append(element('p',error.message));}
  }
  function checkArrangement(data,entries,verified,points=[]) {
    const items=[],errors=[],times=new Map();let enabledCount=0;
    if(!verified)errors.push({text:'原视频尚未匹配，请先重新选择同一视频。',view:'analysis'});
    for(const p of data.points){
      const entry=entries.find(e=>e.point_id===p.point_id)||{},point=points.find(x=>x.id===p.point_id);
      const item={time_s:p.time_s,title:p.title,message:'未参与本次播放'};items.push(item);
      if(!entry.enabled)continue;enabledCount++;
      const version=p.versions.find(v=>v.record_id===entry.generation_id);
      const fail=(text,view='games')=>{item.message=text;errors.push({text:`${p.time_s.toFixed(2)} 秒 · ${p.title}：${text}`,point_id:p.point_id,view});};
      if(!entry.generation_id)fail('尚未选择游戏版本');
      else if(!version)fail('采用的游戏版本已缺失');
      else if(!version.usable)fail(version.stale?'输入已变化，需要重新核对或生成':'所选版本未通过检查');
      else if(point&&(point.stale||point.review!=='kept'||point.merged_into||point.time_s!==p.time_s))fail('当前方案或位置需要核对','edit');
      else item.message=`将播放 ${version.title}`;
      if(times.has(p.time_s)){fail('与其他启用点时间相同，请错开或停用','edit');}
      times.set(p.time_s,p.point_id);
    }
    if(!enabledCount)errors.push({text:'尚未启用任何游戏，请选择版本并开启参与。',view:'arrange'});
    return {items,errors,enabledCount};
  }
  async function saveArrangement() {
    if(!arrangementDraft)throw Error('请先打开编排页面');
    const entries=JSON.parse(JSON.stringify(arrangementDraft.entries));await mutate('/arrangement',{entries});
  }
  async function refreshLibrary() {
    if(!libraryHost)return;
    const now=current(),epoch=++libraryEpoch;
    if(!now.project){libraryHost.replaceChildren(element('p','请先保存或打开项目。游戏结果自动落盘，采用版本用于串联试玩。'));return;}
    const projectId=now.project.project_id;
    if(libraryRenderedProject!==projectId)libraryHost.replaceChildren(element('p','正在读取本项目已保存的游戏…'));
    try {
      const data=await request('/project-games?project_id='+projectId);
      if(epoch!==libraryEpoch||current().project?.project_id!==projectId)return;
      libraryRenderedProject=projectId;current().reportLibrary?.(data);
      libraryHost.replaceChildren(element('p',`已自动保存 ${data.saved_count} 个生成版本 · 当前 ${data.ready_count} 个玩点已采用可用游戏`));
      const tools=element('div');tools.className='gw-library-tools';
      tools.append(button('刷新游戏库（不调用模型）',refreshLibrary),button('试玩已采用游戏',startSequence),button('项目回收站',showTrash),button('下载项目游戏备份 ZIP',()=>downloadBundle(projectId)));
      libraryHost.append(tools,element('p','修改玩法后旧游戏仍保留，但不自动采用。备份包含草稿、选择和代码，不包含原视频；目前不提供整包一键导入。'));
      for(const warning of data.warnings){const message=element('p',warning);message.className='gw-warning';libraryHost.append(message);}
      for(const point of data.points) {
        const card=element('details');card.className='gw-library-point';
        const expansionKey=projectId+':'+point.point_id;card.open=libraryExpansion.get(expansionKey)||false;
        card.addEventListener('toggle',()=>libraryExpansion.set(expansionKey,card.open));
        card.append(element('summary',`${point.time_s.toFixed(2)} 秒 · ${point.title} · ${point.versions.length} 个已存版本`));
        if(!point.versions.length)card.append(element('p','尚未生成游戏；可在玩法编辑区选择方案后生成。'));
        for(const version of point.versions) {
          const row=element('div');row.className='gw-library-version';
          const state=version.status!=='checked'?'生成／校验失败':version.stale?'待重新核对（输入已变化）':version.selected?(point.enabled?'已采用，可串联':'已选择，未参与播放'):'已保存，未采用';
          row.append(element('strong',`${version.title} · ${state}`),element('p',`${new Date(version.created_at).toLocaleString('zh-CN')} · ${version.playtest_count} 次运行记录 · ${version.manual_review_count} 条人工评价`));
          if(version.error)row.append(element('p',version.error));
          if(version.latest_review)row.append(element('p',`最近记录：${version.latest_review.manual_review?'人工评价':'运行回调'} · ${version.latest_review.result} · ${version.latest_review.note||''}`));
          row.append(button('查看此版本／继续试玩',async()=>{
            const record=await request('/game-generation?id='+version.record_id),active=current();
            if(active.project?.project_id!==projectId)throw Error('项目已切换，请重新选择');
            const p=active.state.points.find(p=>p.id===point.point_id);if(!p)throw Error('当前点不存在');
            display(record,{project:active.project,point:JSON.parse(JSON.stringify(p))});
          }),button('下载此版本 ZIP',()=>downloadBundle(projectId,version.record_id)));
          row.append(button('删除此版本',()=>deleteVersion(point,version)));
          if(version.usable)row.append(button(version.selected?'当前已采用':'采用此版本（自动保存）',async()=>{
            await selectVersion(point.point_id,version.record_id,true);
          }));
          if(version.selected&&version.status==='checked')row.append(button('停用此版本',()=>selectVersion(point.point_id,version.record_id,false)));
          card.append(row);
        }
        libraryHost.append(card);
      }
    }catch(error){if(epoch===libraryEpoch)libraryHost.replaceChildren(element('p','游戏库读取失败：'+error.message),button('重试读取',refreshLibrary));}
  }
  async function downloadBundle(projectId,recordId) {
    const response=await fetch('/game-bundle?project_id='+projectId+(recordId?'&generation_id='+recordId:''),{headers:{'X-Local-Token':current().token}});
    if(!response.ok){const data=await response.json();throw Error(data.error||'导出失败');}
    const url=URL.createObjectURL(await response.blob()),a=element('a');a.href=url;a.download=(recordId?'单点游戏':'项目游戏备份')+'.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function offer(host,point,prepare,editor=false) {
    const add=(where,version)=>where.append(button('生成小游戏（单点计费）',async()=>{
      if(busy)throw Error('已有小游戏生成中，请稍候');
      const context=await prepare(version);
      await showPoint(context);
    }));
    if(editor)add(host,null);
    else point.versions.forEach((v,i)=>{if(host.children[i])add(host.children[i],v);});
  }
  async function showPoint(context) {
    context={...context,point:JSON.parse(JSON.stringify(context.point))};
    if(theater)dismissTheater();
    if(session)closeGame('exit','切换玩点',false);
    const {project,point}=context;
    selectedRecord=null;panel.replaceChildren(element('h2',`游戏工作台 / ${point.design?.title||point.id} · ${point.time_s.toFixed(2)} 秒`));
    panel.append(element('p','一次 MiniMax 调用补全详细设计及 JS。只提交下面这个点的文字，不发送视频或截图。'));
    const summary=element('div');summary.className='gw-input-summary';
    for(const [label,value] of [['方案',point.design?.title],['玩法',point.design?.game_id],['位置',point.time_s+' 秒'],['参与行为',point.opportunity],['玩家目标',point.design?.player_goal],['操作',point.design?.player_action]])summary.append(element('p',`${label}：${value||'未填写'}`));
    const raw=element('details');raw.append(element('summary','查看本次完整输入 JSON'),element('pre',JSON.stringify(inputView(point),null,2)));panel.append(summary,raw);
    panel.append(button('确认输入并生成此点',()=>generate(context)),button('刷新该点生成历史',()=>showPoint(context)),
      button('重新开始串联试玩',()=>startSequence()));
    const records=await request(`/game-generation?project_id=${project.project_id}&point_id=${encodeURIComponent(point.id)}`);
    const history=element('div');history.className='gw-history';panel.append(history);
    records.filter(r=>r.stage==='game_generation').forEach(r=>history.append(button(`${r.created_at} · ${r.status==='checked'?'检查通过，未等同试玩通过':'失败'}`,async()=>display(await request('/game-generation?id='+r.record_id),context))));
    panel.scrollIntoView({block:'start',behavior:'smooth'});
  }
  async function generate(context,parent=null,problem='') {
    if(busy||current().busy)throw Error('正在生成或分析，请稍后');
    const now=current();if(now.project?.project_id!==context.project.project_id)throw Error('项目已切换，请重新选择玩点');
    const p=now.state.points.find(p=>p.id===context.point.id);
    if(!p||JSON.stringify(inputView(p))!==JSON.stringify(inputView(context.point)))throw Error('玩法已变化，请重新保存并选择');
    if(!root.confirm(parent?'确认为该点调用一次 MiniMax 修复？':'确认为此点调用一次 MiniMax？不会生成其他点。'))return;
    busy=true;document.querySelectorAll('[data-game-generate]').forEach(b=>b.disabled=true);
    const status=element('p','正在提交后台生成任务…');(versionDialog?.dialog.open?versionDialog.body:panel).append(status);
    try {
      await request('/game-tasks',{project_id:now.project.project_id,revision:now.project.revision,point_id:p.id,...(parent?{generation_id:parent.record_id,problem}:{})});
      status.textContent='任务已提交，可切换页面。请在生成任务中查看状态；关闭此面板不取消调用。';
      scheduleTasks(true);
    }finally{busy=false;document.querySelectorAll('[data-game-generate]').forEach(b=>b.disabled=false);}
  }
  function scheduleTasks(immediate=false){
    if(!taskHost)return;
    clearTimeout(taskTimer);taskTimer=setTimeout(refreshTasks,immediate?0:2000);
  }
  async function refreshTasks(){
    const project=current().project?.project_id,epoch=taskEpoch;
    if(!project){taskHost.replaceChildren();return;}
    try{
      const tasks=await request('/game-tasks?project_id='+project);
      if(project!==current().project?.project_id||epoch!==taskEpoch)return;
      taskHost.replaceChildren();
      if(tasks.length)taskHost.append(element('h3','游戏生成任务'),element('p','可切换页面或刷新，任务由本地服务继续执行。请勿关闭服务；关闭面板不等于取消计费。'));
      const labels={queued:'已提交，准备生成',waiting_model:'等待模型返回',checking:'检查结构与语法',saving:'保存产物',completed:'已完成，尚不代表试玩通过',failed:'失败',interrupted:'服务中断，未自动重试'};
      for(const task of tasks){
        const active=['queued','waiting_model','checking','saving'].includes(task.status);
        const card=element('article');card.className='gw-task-card';
        const seconds=Math.max(0,Math.floor(((task.finished_at?Date.parse(task.finished_at):Date.now())-Date.parse(task.created_at))/1000));
        card.append(element('strong',`${task.title} · ${labels[task.status]||task.status}`),element('p',`${active?'已等待':'用时'} ${seconds} 秒${active&&seconds>=60?'；仍在等待，不代表失败，请勿重复提交。':''}`));
        if(task.error)card.append(element('p',task.error));
        if(task.generation_id&&!active&&task.status!=='interrupted')card.append(button('查看结果／继续试玩',async()=>{
          const r=await request('/game-generation?id='+task.generation_id),now=current();
          if(now.project?.project_id!==project)return;
          const point=now.state.points.find(p=>p.id===task.point_id);
          if(!point)throw Error('该交互点已移除，请从游戏库查看记录');
          display(r,{project:now.project,point});
        }));
        taskHost.append(card);
        const previous=taskStates.get(task.task_id);taskStates.set(task.task_id,task.status);
        if(previous&&previous!==task.status&&!active){showError({message:`${task.title}：${labels[task.status]}。请查看生成任务。`});refreshLibrary();}
      }
      scheduleTasks();
    }catch(error){if(project===current().project?.project_id){taskHost.replaceChildren(element('p','任务状态暂时无法读取，请勿重新生成；稍后自动恢复检查。'));clearTimeout(taskTimer);taskTimer=setTimeout(refreshTasks,5000);}}
  }
  function renderGameDesign(record) {
    const design=record.output?.design;
    if(!design||typeof design!=='object')return element('p','此版本暂无完整玩法说明。');
    const section=element('div');section.className='gw-design';
    section.append(element('h3','玩法说明'));
    const tags=element('p');tags.className='gw-design-meta';
    tags.append(element('span',record.input?.design?.title||'小游戏'));
    if(Number.isFinite(record.input?.time_s))tags.append(element('span',`视频位置 ${record.input.time_s.toFixed(2)} 秒`));
    if(Number.isFinite(design.timeout_s))tags.append(element('span',`交互时限 ${design.timeout_s} 秒`));
    section.append(tags);
    const card=(key,label)=>{
      const item=element('article');item.className='gw-design-card';
      item.append(element('h4',label),element('p',typeof design[key]==='string'&&design[key].trim()?design[key]:'该版本未提供'));
      return item;
    };
    const main=element('div');main.className='gw-design-grid';
    for(const [key,label] of [['goal','玩家目标'],['input','如何操作'],['rules','判定与结束规则']])main.append(card(key,label));
    section.append(main);
    const extra=element('details');extra.className='gw-design-more';
    extra.append(element('summary','场景、画面与反馈说明'));
    const grid=element('div');grid.className='gw-design-grid';
    for(const [key,label] of [['context','视频情境与设计边界'],['screen','画面呈现'],['feedback','成功、失败与超时反馈'],['states','进入、结束与视频衔接']])grid.append(card(key,label));
    extra.append(grid);section.append(extra);
    return section;
  }
  function display(record,context) {
    selectedRecord=record;
    const box=element('section');versionHost(record,context).append(box);
    box.append(element('h3',record.status==='checked'?'结构与语法检查通过（尚不代表可玩）':'生成或检查失败'),element('p',record.error||`记录 ${record.record_id} · 模型 ${record.model}`));
    root.TokenUsage?.render(box,record,'本次游戏生成／修复用量');
    if(record.output)box.append(renderGameDesign(record));
    const technical=element('details');technical.className='gw-version-technical';
    technical.append(element('summary','原始设计、代码与保存位置'));
    if(record.record_file)technical.append(element('p','保存位置：'+record.record_file));
    if(record.artifacts&&Object.keys(record.artifacts).length)technical.append(element('pre','代码与设计产物：\n'+Object.values(record.artifacts).join('\n')));
    if(record.output){
      const raw=element('details');raw.append(element('summary','查看原始设计 JSON'),element('pre',JSON.stringify(record.output.design,null,2)));technical.append(raw);
      const detail=element('details');detail.append(element('summary','查看 JS / CSS'),element('pre',record.output.javascript+'\n\n'+record.output.css));technical.append(detail);
    }
    box.append(technical);
    if(record.status==='checked') {
      box.append(button('单点立即试玩（大屏）',()=>play(record)),button('小窗预览',()=>play(record,false)),button('启用此版本用于串联',async()=>{
        await selectVersion(record.point_id,record.record_id,true);box.append(element('p','采用版本已自动保存，重新打开项目后可恢复'));
      }),button('停用该点串联游戏',async()=>{await selectVersion(record.point_id,record.record_id,false);box.append(element('p','该点已停用'));}));
    }
    const note=element('textarea');note.placeholder='填写修改要求、运行错误或人工试玩评价';box.append(note);
    const review=element('select');review.setAttribute('aria-label','人工试玩结论');
    [['exit','仅记录备注／退出'],['success','人工确认成功路径通过'],['failure','人工确认失败路径'],['timeout','人工确认超时路径'],['error','人工发现运行错误']].forEach(([value,label])=>{const o=element('option',label);o.value=value;review.append(o);});box.append(review);
    const saved=readVersionDraft(record,context);
    if(saved){note.value=saved.note||'';review.value=['exit','success','failure','timeout','error'].includes(saved.review)?saved.review:'exit';}
    versionDialog.note=note;versionDialog.review=review;versionDialog.record=record;versionDialog.context=context;
    note.oninput=review.onchange=rememberVersion;
    box.append(button('按要求修复此游戏（计费）',async()=>{if(!note.value.trim())throw Error('请填写修复要求');await generate(context,record,note.value);}),
      button('保存人工试玩评价（不调用模型）',async()=>{
        if(!note.value.trim())throw Error('请填写评价');
        await request('/game-playtest',{generation_id:record.record_id,result:review.value,note:note.value,manual_review:true});box.append(element('p','人工评价已保存；不自动宣称全部路径通过'));refreshLibrary();
      }));
    box.append(element('small','未提交的备注和评价选项仅保存在当前浏览器会话中；点击保存人工评价后才写入项目试玩记录。'));
    [...(versionDialog.body.querySelectorAll?.('details')||[])].forEach((item,index)=>{item.open=saved?.expanded?.[index]||false;});
    versionDialog.body.scrollTop=saved?.scroll||0;
  }
  function iframeDocument(record,id) {
    // All model content is JSON-encoded; never interpolate it as HTML or inline script.
    const nonce=crypto.randomUUID().replaceAll('-','');
    const data=JSON.stringify({id,js:record.output.javascript,css:record.output.css}).replaceAll('<','\\u003c');
    return `<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}' blob:; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; media-src 'none'; font-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"><style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}#game{position:relative;width:100%;height:100%;touch-action:none}</style><div id="game"></div><script nonce="${nonce}">
    (()=>{const data=${data};let instance,done=false;
    const send=(type,value)=>parent.postMessage({gameSession:data.id,type,value},'*');
    const api={finish:value=>{if(!done){done=true;send('finish',value);}},reportError:value=>send('error',String(value))};
    addEventListener('error',e=>send('error',e.message));addEventListener('unhandledrejection',e=>send('error',String(e.reason)));
    const style=document.createElement('style');style.textContent=data.css;document.head.append(style);
    const script=document.createElement('script');const url=URL.createObjectURL(new Blob([data.js],{type:'text/javascript'}));script.src=url;
    script.onload=()=>{URL.revokeObjectURL(url);try{if(typeof mount!=='function')throw Error('缺少 mount 函数');instance=mount(document.getElementById('game'),api);if(!instance||typeof instance.destroy!=='function')throw Error('mount 必须返回 destroy');send('ready');}catch(e){send('error',String(e));}};
    script.onerror=()=>send('error','游戏脚本加载失败');document.body.append(script);
    addEventListener('pagehide',()=>{if(instance)instance.destroy();});})();<\/script>`;
  }
  function fitTheater() {
    if(!theater)return;
    const video=$('video'),host=theater.player;
    const ratio=video.videoWidth&&video.videoHeight?video.videoWidth/video.videoHeight:16/9;
    const size=fitSize(host.clientWidth,host.clientHeight,ratio);
    const stage=$('videoStage');stage.style.width=size.width+'px';stage.style.height=size.height+'px';
  }
  function fitSize(width,height,ratio) {const w=Math.min(width,height*ratio);return {width:w,height:w/ratio};}
  function openTheater(record) {
    if(theater){theater.record=record;updateTheater();return;}
    const stage=$('videoStage'),marker=document.createComment('videoStage home');
    stage.parentNode.insertBefore(marker,stage);
    const dialog=element('dialog');dialog.className='gw-theater';dialog.setAttribute('aria-label','大屏小游戏试玩');
    const shell=element('div');shell.className='gw-theater-shell';
    const header=element('header');header.className='gw-theater-header';
    const title=element('h2','大屏试玩');header.append(title);
    const infoToggle=button('收起说明',()=>{const hidden=dialog.classList.toggle('gw-info-hidden');infoToggle.textContent=hidden?'展开说明':'收起说明';});
    const fullscreen=button('浏览器全屏',()=>{});
    // Invoke directly from the user gesture (some browsers drop activation after a promise hop).
    fullscreen.onclick=()=>{
      try {
        const operation=document.fullscreenElement?document.exitFullscreen():shell.requestFullscreen?.();
        if(!operation)throw Error('当前浏览器不支持全屏，请使用大屏弹层');
        operation.catch(showError);
      }catch(error){showError(error);}
    };
    header.append(infoToggle,fullscreen,button('关闭大屏',dismissTheater));
    const body=element('div');body.className='gw-theater-body';
    const player=element('div');player.className='gw-theater-player';
    const info=element('aside');info.className='gw-theater-info';
    body.append(player,info);
    const footer=element('footer');footer.className='gw-theater-footer';
    const status=element('p','准备开始；可折叠说明以扩大游戏区域。');status.setAttribute('aria-live','polite');
    footer.append(button('开始',async()=>{if(!session||session.ended)await play(theater.record);startFrame();}),
      button('重玩此点',()=>play(theater.record)),button('退出小游戏并继续视频',continueVideo),status);
    footer.append(button('跳过当前点继续编排',()=>{
      if(!sequence)throw Error('当前不是串联试玩');
      sequence.error=false;closeGame('exit','跳过当前点',false);$('video').play().catch(showError);
    }),button('结束试玩',dismissTheater));
    shell.append(header,body,footer);dialog.append(shell);document.body.append(dialog);
    theater={dialog,shell,player,info,title,status,record,marker,focus:document.activeElement,
      width:stage.style.width,height:stage.style.height,observer:null,project:record.project_id};
    movingPlayer=true;player.append(stage);movingPlayer=false;
    dialog.addEventListener('cancel',event=>{event.preventDefault();dismissTheater();});
    dialog.addEventListener('close',()=>{if(theater?.dialog===dialog)dismissTheater();});
    if(root.ResizeObserver){theater.observer=new root.ResizeObserver(fitTheater);theater.observer.observe(player);}
    root.addEventListener('resize',fitTheater);
    dialog.showModal();updateTheater();fitTheater();
  }
  function updateTheater() {
    if(!theater)return;const r=theater.record,d=r.output.design;
    theater.title.textContent=`${r.input.design.title} · ${r.input.time_s.toFixed(2)} 秒`;
    theater.info.replaceChildren();
    for(const [key,label] of [['goal','玩家目标'],['input','操作说明'],['rules','判定规则'],['feedback','反馈']]){
      theater.info.append(element('h3',label),element('p',d[key]||'见当前玩法说明'));
    }
  }
  function dismissTheater() {
    if(!theater)return;
    const old=theater;theater=null;sequence=null;
    closeGame('exit','关闭大屏试玩',false);$('video').pause();
    old.observer?.disconnect();root.removeEventListener('resize',fitTheater);
    const stage=$('videoStage');movingPlayer=true;
    old.marker.parentNode.insertBefore(stage,old.marker);old.marker.remove();
    stage.style.width=old.width;stage.style.height=old.height;movingPlayer=false;
    if(document.fullscreenElement===old.shell)document.exitFullscreen().catch(()=>{});
    old.dialog.close();old.dialog.remove();
    if(old.focus?.isConnected)old.focus.focus();
    const previous=returnToVersion;returnToVersion=null;
    if(previous&&current?.().project?.project_id===previous.context.project.project_id&&current().verified&&matches(previous.record,current().state.points.find(p=>p.id===previous.record.point_id)))display(previous.record,previous.context);
  }
  function continueVideo() {
    if(session)closeGame('exit','玩家退出',true);
    else $('video').play().catch(showError);
    if(theater)theater.status.textContent='继续播放原视频，可点击“重玩此点”再次进入。';
  }
  async function play(record,large=true,autoStart=false) {
    const now=current(),p=now.state.points.find(p=>p.id===record.point_id);
    if(busy||now.busy)throw Error('正在生成或分析，暂不能试玩');
    if(now.project?.project_id!==record.project_id||!now.verified||!matches(record,p))throw Error('请核对原视频和当前玩法；旧游戏与当前输入不一致时须重新生成');
    if(record.status!=='checked')throw Error('游戏未通过检查');
    const leaseId=crypto.randomUUID().replaceAll('-','');
    await request('/game-session',{generation_id:record.record_id,session_id:leaseId,active:true});
    if(current().project?.project_id!==record.project_id||!matches(record,current().state.points.find(p=>p.id===record.point_id))){request('/game-session',{session_id:leaseId,active:false}).catch(showError);throw Error('项目或玩法已变化');}
    if(versionDialog){returnToVersion=large&&!autoStart?{record:versionDialog.record,context:versionDialog.context}:null;}
    closeVersionDialog();
    if(!large)panel.append(button('返回版本详情',()=>display(record,{project:current().project,point:current().state.points.find(p=>p.id===record.point_id)})));
    if(!large&&theater)dismissTheater();
    closeGame('exit','切换游戏',false);
    if(large)openTheater(record);
    const video=$('video');video.pause();video.currentTime=record.input.time_s;
    const overlay=$('gameOverlay');overlay.hidden=false;
    const cover=element('div');cover.className='gw-cover';cover.append(element('strong',record.input.design.title),element('p',record.output.design.goal));
    overlay.replaceChildren(cover);
    session={id:crypto.randomUUID(),record,large,leaseId,ended:false,timer:null,frame:null,started:false,positioning:video.seeking};
    session.heartbeat=setInterval(()=>request('/game-session',{generation_id:record.record_id,session_id:leaseId,active:true}).catch(error=>{showError(error);closeGame('error','试玩会话失效',false);}),20000);
    cover.append(button('开始小游戏',()=>startFrame()));
    if(theater)theater.status.textContent='已定位并暂停，点击“开始”或画面内按钮。';
    const exit=button('退出并继续视频',()=>closeGame('exit','玩家退出',true));exit.className='gw-exit';overlay.append(exit);
    overlay.scrollIntoView({block:'center',behavior:'smooth'});
    if(autoStart) {
      if(video.seeking){session.timer=setTimeout(()=>closeGame('error','视频定位超时',false),10000);video.addEventListener('seeked',()=>{if(session?.leaseId===leaseId)startFrame();},{once:true});}
      else startFrame();
    }
  }
  function startFrame() {
    if(!session||session.started)return;
    clearTimeout(session.timer);
    session.started=true;const frame=element('iframe');frame.setAttribute('sandbox','allow-scripts');frame.setAttribute('referrerpolicy','no-referrer');frame.title='隔离小游戏';
    let loads=0;frame.onload=()=>{if(++loads>1)closeGame('error','游戏尝试导航，已停止',false);};
    session.frame=frame;frame.srcdoc=iframeDocument(session.record,session.id);
    $('gameOverlay').querySelector('.gw-cover').remove();$('gameOverlay').prepend(frame);
    session.timer=setTimeout(()=>closeGame('error','游戏加载超时',false),10000);
  }
  function onMessage(event) {
    if(!session||!session.frame||event.source!==session.frame.contentWindow||event.origin!=='null'||event.data?.gameSession!==session.id||session.ended)return;
    const {type,value}=event.data;
    if(type==='ready'&&!session.ready){session.ready=true;if(theater)theater.status.textContent='小游戏运行中';clearTimeout(session.timer);session.timer=setTimeout(()=>finish('timeout','操作超时'),session.record.output.design.timeout_s*1000);}
    else if(type==='error')closeGame('error',String(value).slice(0,2000),false);
    else if(type==='finish'&&value&&['success','failure'].includes(value.result))finish(value.result,String(value.message||value.result).slice(0,1000));
  }
  function logPlay(record,result,note){request('/game-playtest',{generation_id:record.record_id,result,note}).then(()=>refreshLibrary()).catch(showError);}
  function releaseLease(value){if(!value?.leaseId)return;clearInterval(value.heartbeat);request('/game-session',{session_id:value.leaseId,active:false}).catch(showError);value.leaseId=null;}
  function finish(result,message) {
    if(!session||session.ended)return;
    session.ended=true;clearTimeout(session.timer);const record=session.record,large=session.large!==false;
    releaseLease(session);
    logPlay(record,result,message);session.frame?.remove();session.frame=null;
    if(theater)theater.status.textContent=message+'；1 秒后继续原视频。';
    const cover=element('div');cover.className='gw-cover';cover.append(element('strong',message));
    cover.append(button('重玩',()=>{clearTimeout(session?.timer);return play(record,large);}),button('继续原视频',()=>closeGame(null,'',true)));
    $('gameOverlay').prepend(cover);session.timer=setTimeout(()=>closeGame(null,'',true),1000);
  }
  function closeGame(result,note,resume) {
    if(!session)return;const old=session;session=null;clearTimeout(old.timer);
    releaseLease(old);
    if(result&&!old.ended&&(old.started||result==='error'))logPlay(old.record,result,note);
    $('gameOverlay').replaceChildren();$('gameOverlay').hidden=true;
    if(result==='error'){showError(Error(note));if(sequence)sequence.error=true;panel.append(button('错误后继续视频',()=>{if(sequence)sequence.error=false;$('video').play().catch(showError);}));}
    if(resume)$('video').play().catch(showError);
  }
  async function startSequence() {
    await current().beforeMutation?.();
    const now=current();if(!now.verified||!now.project||now.busy||busy)throw Error('请先匹配视频并完成当前操作');
    const games=await request('/game-sequence?project_id='+now.project.project_id);
    if(games.some(r=>!matches(r,now.state.points.find(p=>p.id===r.point_id))))throw Error('页面草稿和保存的编排不一致，请先保存');
    if(!games.length)throw Error('没有已启用且与当前玩法一致的游戏');
    if(new Set(games.map(r=>r.input.time_s)).size!==games.length)throw Error('多个启用游戏时间相同，请错开或停用');
    returnToVersion=null;closeVersionDialog();
    closeGame('exit','重新开始串联',false);sequence={project:now.project.project_id,games:games.sort((a,b)=>a.input.time_s-b.input.time_s),fired:new Set(),last:0};
    openTheater(sequence.games[0]);
    $('video').currentTime=0;await $('video').play();
  }
  function onTime() {
    if(!sequence||session||sequence.error||sequence.triggering)return;const now=current(),video=$('video');
    if(now.project?.project_id!==sequence.project){sequence=null;return;}
    const previous=sequence.last;sequence.last=video.currentTime;if(previous===null||video.seeking||video.paused||video.currentTime<previous)return;
    const record=sequence.games.find(r=>!sequence.fired.has(r.record_id)&&r.input.time_s>=previous&&r.input.time_s<=video.currentTime&&matches(r,now.state.points.find(p=>p.id===r.point_id)));
    if(record){sequence.fired.add(record.record_id);sequence.triggering=true;video.pause();
      play(record,true,true).catch(e=>{showError(e);if(sequence)sequence.error=true;}).finally(()=>{if(sequence)sequence.triggering=false;});}
  }
  function sync(){const now=current();const key=(now.project?.project_id||'')+':'+(now.project?.revision||0);
    if(taskProject!==now.project?.project_id){taskProject=now.project?.project_id;taskEpoch++;scheduleTasks(true);}
    if(versionDialog&&now.project?.project_id!==versionDialog.project)closeVersionDialog();
    if(libraryHost&&key!==libraryKey){const changed=libraryKey.split(':')[0]!==key.split(':')[0];libraryKey=key;if(changed){panel.replaceChildren();arrangementDraft=null;}refreshLibrary();}
    if(theater&&(now.project?.project_id!==theater.project||!now.verified||!matches(theater.record,now.state.points.find(p=>p.id===theater.record.point_id))))dismissTheater();
    if(session){if(now.project?.project_id!==session.record.project_id||!matches(session.record,now.state.points.find(p=>p.id===session.record.point_id)))closeGame('exit','项目或玩法已变化',false);}}
  root.GameWorkbench={init,offer,iframeDocument,matches,sync,fitSize,deletePoint,renderArrangement};
})(typeof window==='undefined'?globalThis:window);
