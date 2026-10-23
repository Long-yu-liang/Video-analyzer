/* Recommendation and selection only. Execution parameters belong to later design. */
(function(root) {
  'use strict';
  const fields = {fit:'为什么适合这个场景',player_goal:'玩家目标',player_action:'玩家怎么参与',
    success:'怎样算完成',failure:'怎样算未完成',feedback:'玩家得到的反馈'};
  const copy = value => JSON.parse(JSON.stringify(value));
  function render(record,controls,container,notify,seek) {
    container.replaceChildren();const drafts=[];
    const make=(tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;};
    const warnings=[...(record.analysis.warnings||[]),...(record.request_warnings||[])];
    if(warnings.length){
      const note=make('aside');note.className='validation-warnings';note.setAttribute('role','status');
      note.append(make('p',`${warnings.length} 项输出警告，已保留合法玩法，请回看审核。`));
      const list=make('ul');for(const warning of warnings)list.append(make('li',warning.message));
      note.append(list);container.append(note);
    }
    for(const control of controls.filter(c=>c.include.checked)) {
      const observation=record.observations.find(o=>o.point_id===control.pointId);
      // A restored single-point run does not contain frames for every current batch point.
      if(!observation)continue;
      const options=record.analysis.recommendations.filter(o=>o.point_id===control.pointId);
      const draft={control,observation,options,option_id:null,design:null,status:'pending',decision_reason:'',stale:false};
      drafts.push(draft);
      const card=make('article');card.className='recommendation-point design-point';
      const locate=make('button',`${observation.frame_time_s.toFixed(2)} 秒 · ${control.point.opportunity}`);
      locate.type='button';locate.className='recommendation-seek';locate.onclick=()=>seek(observation.frame_time_s);
      const state=make('p','请选择适合的玩法，或放弃此交互点');state.className='recommendation-state';draft.state=state;
      card.append(locate,make('p','先看适合怎么玩。播放方式、精确参数和坐标在选定玩法后再设计。'),state);
      const compare=make('div');compare.className='option-grid';card.append(compare);
      const editor=make('div');editor.className='recommendation-editor';card.append(editor);
      const edited=()=>{draft.status='pending';state.textContent='内容已修改，请确认';notify();};
      function buildEditor() {
        editor.replaceChildren();const design=draft.design;if(!design)return;
        const game=make('select');game.className='recommendation-game';game.setAttribute('aria-label','玩法类型');
        for(const type of record.catalog_snapshot.types){const option=make('option',type.name);option.value=type.id;game.append(option);}game.value=design.game_id;
        game.onchange=()=>{design.game_id=game.value;edited();};editor.append(make('label','玩法类型'),game);
        for(const [key,label] of Object.entries(fields)) {
          const input=make('textarea');input.className=`recommendation-${key}`;input.value=design[key]||'';input.setAttribute('aria-label',label);
          input.oninput=()=>{design[key]=input.value;edited();};editor.append(make('label',label),input);
        }
        const accept=make('button','接受此玩法');accept.className='recommendation-accept';accept.type='button';accept.disabled=!draft.option_id;
        const confirm=make('button','确认修改／自行设计');confirm.className='recommendation-confirm';confirm.type='button';
        function complete(status) {
          if(draft.stale){state.textContent='画面位置已改变，请重新推荐';return;}
          if(!Object.keys(fields).every(k=>typeof design[k]==='string'&&design[k].trim())){state.textContent='请补齐玩法和参与说明';return;}
          draft.status=status;state.textContent=status==='accepted'?'已接受玩法':'已确认修改／自行设计';notify();
        }
        accept.onclick=()=>{const original=options.find(o=>o.option_id===draft.option_id);
          if(JSON.stringify(design)!==JSON.stringify(original)){state.textContent='内容已修改，请确认修改';return;}complete('accepted');};
        confirm.onclick=()=>complete('edited');editor.append(accept,confirm);
      }
      for(const [index,option] of options.entries()) {
        const box=make('div');box.className='option-card';
        box.append(make('h3',`${index===0?'推荐':'备选'} · ${option.title||option.game_name} / ${option.game_name}`));
        for(const [key,label] of Object.entries(fields))box.append(make('p',`${label}：${option[key]}`));
        if(option.difference)box.append(make('p',`参与方式的特点：${option.difference}`));
        const choose=make('button','选择这个玩法');choose.className='choose-recommendation';choose.type='button';
        choose.onclick=()=>{if(draft.stale)return;draft.option_id=option.option_id;draft.design=copy(option);draft.status='pending';state.textContent='已选择，请接受或修改';buildEditor();notify();};
        box.append(choose);compare.append(box);
      }
      if(!options.length){const reason=record.analysis.abstentions?.find(x=>x.point_id===control.pointId)?.reason;
        compare.append(make('p',`本次未推荐玩法。${reason||'可以自行补充或放弃。'}`));}
      const manual=make('button','自行设计玩法');manual.className='manual-recommendation';manual.type='button';
      manual.onclick=()=>{if(draft.stale)return;draft.option_id=null;draft.design={game_id:record.catalog_snapshot.types[0].id};edited();buildEditor();};
      const reason=make('textarea');reason.className='recommendation-reason';reason.placeholder='选择、修改或放弃的理由（选填）';
      reason.setAttribute('aria-label','处理理由（选填）');reason.oninput=()=>{draft.decision_reason=reason.value;};
      const discard=make('button','放弃此交互点');discard.className='recommendation-discard';discard.type='button';
      discard.onclick=()=>{draft.status='discarded';state.textContent='已放弃';notify();};
      draft.restore=saved=>{
        draft.option_id=saved.option_id;const original=options.find(o=>o.option_id===saved.option_id);
        draft.design=saved.design?{...(original?copy(original):{}),...copy(saved.design)}:null;
        draft.status=saved.status;draft.decision_reason=saved.decision_reason||'';draft.stale=!!saved.stale;
        reason.value=draft.decision_reason;buildEditor();
        state.textContent=draft.stale?'画面已改变，请重新核对':draft.status==='pending'?'待确认':draft.status==='discarded'?'已放弃':'已恢复保存的选择';
      };
      card.append(manual,reason,discard);container.append(card);
    }
    return drafts;
  }
  function invalidate(drafts,control) {
    for(const draft of drafts.filter(d=>d.control===control)){draft.stale=true;draft.status='pending';draft.state.textContent='画面位置已改变，请重新推荐；已有玩法草稿保留';}
  }
  function selections(drafts) {
    if(drafts.some(d=>d.stale||d.status==='pending'))throw Error('请接受、确认修改或放弃每个点；位置改变后请重新推荐');
    return drafts.map(d=>({point_id:d.control.pointId,option_id:d.option_id,status:d.status,
      adjusted_time_s:d.observation.requested_time_s,decision_reason:d.decision_reason,design:d.design}));
  }
  root.GameplayRecommendations={render,invalidate,selections};
})(typeof window==='undefined'?globalThis:window);
