/* Day 3 design editor: compare and annotate; this does not run a game. */
(function (root) {
  'use strict';
  const copy = value => JSON.parse(JSON.stringify(value));
  const fields = {title:'方案名称', fit:'与画面的关系', player_goal:'玩家目标', screen_description:'暂停后画面呈现',
    player_action:'玩家操作', success:'成功反馈的语义', failure:'失败反馈的语义', feedback:'成功与失败反馈', difference:'与另一方案的差异'};
  const paramNames = {timeout_s:'操作时限（秒）',feedback_s:'反馈时长（秒）',period_s:'指针往返周期（秒）',
    charge_s:'蓄满时间（秒）',zone_min:'目标区起点（0–1）',zone_max:'目标区终点（0–1）',
    clicks:'目标点击次数',direction:'短滑方向',distance_ratio:'滑动距离／画面短边',choices:'选项（每行一个）'};
  function rectFromPoints(a, b) {
    const clamp = v => Math.max(0, Math.min(1, v));
    const x = clamp(Math.min(a.x,b.x)), y = clamp(Math.min(a.y,b.y));
    return {x,y,w:clamp(Math.max(a.x,b.x))-x,h:clamp(Math.max(a.y,b.y))-y};
  }
  function framePoint(x,y,bounds,width,height) {
    const scale = Math.min(bounds.width/width,bounds.height/height);
    const w=width*scale,h=height*scale,left=bounds.left+(bounds.width-w)/2,top=bounds.top+(bounds.height-h)/2;
    if(x<left||x>left+w||y<top||y>top+h)return null;
    return {x:(x-left)/w,y:(y-top)/h};
  }
  function ruleText(id,p) {
    const zone=`${Math.round(p.zone_min*100)}%–${Math.round(p.zone_max*100)}%`;
    const text={tap_object:'点中框选区域成功，超时失败',choice:`选择任一按钮完成；${(p.choices||[]).join(' / ')}；超时失败`,
      timing:`新指针以 ${p.period_s} 秒周期往返，在 ${zone} 按下成功；区域外按下或超时失败`,
      hold_release:`新蓄力条 ${p.charge_s} 秒到满，在 ${zone} 松开成功；过早、过晚、蓄满未松或超时失败`,
      mash:`累计点 ${p.clicks} 次成功；超时未达到失败`,drag:'拖动叠加标记至落点区域释放成功；区域外释放或超时失败',
      swipe:`朝 ${p.direction} 滑动达到短边 ${p.distance_ratio*100}% 成功；错误方向或超时失败，主轴判方向、两轴相等不判定`};
    return `暂停原片后，${p.timeout_s} 秒内${text[id]}；反馈 ${p.feedback_s} 秒后继续原片，不改变视频剧情。`;
  }
  function render(record, controls, config, container, notify) {
    container.replaceChildren();
    const drafts=[];
    const make=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
    const warnings=[...(record.analysis.warnings||[]),...(record.request_warnings||[])];
    if(warnings.length){const note=make('aside');note.className='validation-warnings';note.setAttribute('role','status');
      note.append(make('p',`${warnings.length} 项输出警告，合法方案已保留。`));
      const list=make('ul');for(const warning of warnings)list.append(make('li',warning.message));note.append(list);container.append(note);}
    for(const control of controls.filter(c=>c.include.checked)) {
      const observation=record.observations.find(o=>o.point_id===control.pointId);
      const options=record.analysis.recommendations.filter(o=>o.point_id===control.pointId);
      const spec=record.template_snapshot;
      const d={control,observation,options,option_id:null,design:null,anchors:[],status:'pending',decision_reason:'',stale:false};
      drafts.push(d);
      const card=make('article');card.className='design-point';
      const heading=make('h3',`${observation.requested_time_s.toFixed(2)} 秒 · ${control.point.opportunity}`);
      const state=make('p','待选择方案');state.className='design-state';d.state=state;
      card.append(heading,make('p',`实际画面 ${observation.frame_time_s.toFixed(3)} 秒；原位置 ${control.point.time_s.toFixed(3)} 秒。新增控件与原片事实分别描述。`),state);
      const compare=make('div');compare.className='option-grid';card.append(compare);
      const editor=make('div');editor.className='design-editor';card.append(editor);
      const reasonLabel=make('label','选择／修改／放弃理由');
      const reason=make('textarea');reason.placeholder='修改或放弃请写具体原因；接受可使用下面的确认理由';
      reason.oninput=()=>{d.decision_reason=reason.value;notify();};
      const confirmed=make('button','已回看画面，目标与操作适配');confirmed.type='button';
      confirmed.onclick=()=>{reason.value='已回看画面，目标与操作适配';d.decision_reason=reason.value;notify();};
      const markEdited=()=>{d.status='pending';state.textContent='已修改，待确认';notify();};
      let image=null,canvas=null;
      function draw() {
        if(!canvas||!image)return;
        const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0,canvas.width,canvas.height);
        for(const a of d.anchors){ctx.strokeStyle=a.role==='destination'?'#cf4d28':'#168056';ctx.lineWidth=4;ctx.strokeRect(a.x*canvas.width,a.y*canvas.height,a.w*canvas.width,a.h*canvas.height);ctx.fillStyle=ctx.strokeStyle;ctx.font='20px sans-serif';ctx.fillText(a.role,a.x*canvas.width,Math.max(22,a.y*canvas.height));}
      }
      function buildEditor() {
        editor.replaceChildren();
        const design=d.design;if(!design)return;
        const gameLabel=make('label','玩法类型');const game=make('select');game.className='design-game';
        for(const t of record.catalog_snapshot.types){const o=make('option',t.name);o.value=t.id;game.append(o);}game.value=design.game_id;
        game.onchange=()=>{design.game_id=game.value;design.parameters=copy(spec.defaults[game.value]);d.anchors=[];design.target_labels=[];
          design.screen_description='';design.player_action='';design.success='';design.failure='';design.feedback='';
          markEdited();buildEditor();};editor.append(gameLabel,game);
        for(const [key,label] of Object.entries(fields)){
          const l=make('label',label),input=make('textarea');input.value=design[key]||'';input.className=`design-${key}`;
          input.setAttribute('aria-label',label);
          input.oninput=()=>{design[key]=input.value;markEdited();};editor.append(l,input);
        }
        const rules=make('p',ruleText(design.game_id,design.parameters));rules.className='canonical-rules';
        editor.append(make('h4','执行规则（下列参数为判定依据）'));
        for(const [key,value] of Object.entries(design.parameters)){
          const l=make('label',paramNames[key]||key);
          const input=make(key==='direction'?'select':key==='choices'?'textarea':'input');input.className=`param-${key}`;
          input.setAttribute('aria-label',paramNames[key]||key);
          if(key==='direction'){for(const dir of ['left','right','up','down']){const o=make('option',dir);o.value=dir;input.append(o);}}
          else if(key!=='choices'){input.type='number';input.step=key==='clicks'?'1':'0.01';}
          input.value=Array.isArray(value)?value.join('\n'):value;
          input.oninput=()=>{design.parameters[key]=key==='choices'?input.value.split('\n').map(x=>x.trim()).filter(Boolean):key==='direction'?input.value:Number(input.value);rules.textContent=ruleText(design.game_id,design.parameters);markEdited();};
          editor.append(l,input);
        }
        editor.append(rules);
        const roles=spec.anchor_roles[design.game_id]||[];
        const role=make('select');role.className='anchor-role';
        roles.forEach((r,i)=>{const o=make('option',`${r} · ${design.target_labels?.[i]||'请按画面标注对象'}`);o.value=r;role.append(o);});
        if(roles.length)editor.append(make('label','拖动框选操作对象（移动的是叠加标记）'),role);
        canvas=make('canvas');canvas.className='annotation-canvas';canvas.width=640;canvas.height=360;
        canvas.setAttribute('role','img');canvas.setAttribute('aria-label','实际送入模型的暂停画面，拖动框选');editor.append(canvas);
        const anchorNote=make('p');anchorNote.className='anchor-note';editor.append(anchorNote);
        const updateNote=()=>{anchorNote.textContent=roles.length?`必需：${roles.join('、')}；已标注：${d.anchors.map(a=>a.role).join('、')||'无'}`:'此类型使用叠加控件，无需框选。';};updateNote();
        d.clearAnnotations=()=>{draw();updateNote();};
        d.imageReady=false;image=new Image();image.onload=()=>{d.imageReady=true;canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;draw();};
        image.onerror=()=>{anchorNote.textContent='画面加载失败，不能确认；请重新推荐。';d.imageFailed=true;};
        image.src=observation.preview_jpeg_base64?'data:image/jpeg;base64,'+observation.preview_jpeg_base64
          :'/gameplay-frame?'+new URLSearchParams({record_id:record.record_id,point_id:control.pointId});
        let start=null;
        canvas.onpointerdown=e=>{if(d.stale||!roles.length)return;start=framePoint(e.clientX,e.clientY,canvas.getBoundingClientRect(),canvas.width,canvas.height);if(start)canvas.setPointerCapture(e.pointerId);};
        canvas.onpointerup=e=>{if(!start)return;const end=framePoint(e.clientX,e.clientY,canvas.getBoundingClientRect(),canvas.width,canvas.height);if(end){const box=rectFromPoints(start,end);if(box.w>.001&&box.h>.001){d.anchors=d.anchors.filter(a=>a.role!==role.value);d.anchors.push({role:role.value,...box});markEdited();draw();updateNote();}}start=null;};
        const reset=make('button','清除框选');reset.type='button';reset.onclick=()=>{d.anchors=[];draw();updateNote();markEdited();};if(roles.length)editor.append(reset);
        const accept=make('button','接受此方案');accept.type='button';accept.className='design-accept';
        const confirm=make('button','确认修改／人工方案');confirm.type='button';confirm.className='design-confirm';
        function complete(status){
          if(d.stale||d.imageFailed||!d.imageReady){state.textContent='请等待画面加载；过期或失败时请重新推荐';return;}
          if(!Object.keys(fields).every(k=>typeof design[k]==='string'&&design[k].trim())){state.textContent='请补齐玩法描述后确认';return;}
          if(!roles.every(r=>d.anchors.some(a=>a.role===r))){state.textContent='请先完成必需框选';return;}
          if(!d.decision_reason.trim()){state.textContent='请填写或确认处理理由';return;}
          d.status=status;state.textContent=status==='accepted'?'已接受':'已确认修改／人工方案';notify();
        }
        accept.disabled=!d.option_id;accept.onclick=()=>{
          const original=options.find(o=>o.option_id===d.option_id);
          if(JSON.stringify(design)!==JSON.stringify(original)){state.textContent='内容已修改，请使用确认修改';return;}
          complete('accepted');};confirm.onclick=()=>complete('edited');editor.append(accept,confirm);
      }
      for(const [i,option] of options.entries()){
        const box=make('div');box.className='option-card';
        box.append(make('h4',`${i===0?'首选':'备选'} · ${option.title} / ${option.game_name}`),make('p',option.fit),
          make('p',`目标：${option.player_goal}`),make('p',option.screen_description),make('p',`操作：${option.player_action}`),
          make('p',option.rules_text),make('p',`反馈：${option.feedback}`),make('p',`差异：${option.difference}`));
        const choose=make('button','选择此方案并编辑');choose.type='button';choose.className='choose-option';
        choose.onclick=()=>{if(d.stale)return;d.option_id=option.option_id;d.design=copy(option);d.anchors=[];markEdited();buildEditor();};box.append(choose);compare.append(box);
      }
      if(!options.length){const why=record.analysis.abstentions?.find(x=>x.point_id===control.pointId);compare.append(make('p',`未推荐：${why?.reason||'没有合适方案'}`));}
      const manual=make('button','从模板手工新增');manual.type='button';manual.className='manual-design';
      manual.onclick=()=>{if(d.stale)return;d.option_id=null;d.design={game_id:'tap_object',parameters:copy(spec.defaults.tap_object),target_labels:[]};d.anchors=[];markEdited();buildEditor();};
      const discard=make('button','放弃此交互点');discard.type='button';discard.className='design-discard';
      discard.onclick=()=>{if(!d.decision_reason.trim()){state.textContent='请填写放弃原因';return;}d.status='discarded';state.textContent='已放弃';notify();};
      card.append(manual,reasonLabel,reason,confirmed,discard);container.append(card);
    }
    return drafts;
  }
  function invalidate(drafts, control) {
    for(const d of drafts.filter(d=>d.control===control)) {d.stale=true;d.status='pending';d.anchors=[];if(d.clearAnnotations)d.clearAnnotations();d.state.textContent='暂停位置已改变，需要重新推荐';}
  }
  function selections(drafts) {
    if(drafts.some(d=>d.stale||d.status==='pending'))throw Error('请重新推荐过期方案，并确认或放弃每个交互点');
    return drafts.map(d=>({point_id:d.control.pointId,option_id:d.option_id,status:d.status,
      adjusted_time_s:d.observation.requested_time_s,decision_reason:d.decision_reason,design:d.design,anchors:d.anchors}));
  }
  root.GameplayDesign={render,invalidate,selections,rectFromPoints,framePoint,ruleText};
})(typeof window==='undefined'?globalThis:window);
