const assert=require('node:assert/strict');
require('../../web/assets/js/workbench.js');
const M=global.WorkbenchModel;
const state=M.blank();const parent='b'.repeat(32),game='c'.repeat(32);
const core={title:'接球后的操作',game_id:'timing',...Object.fromEntries(['fit','player_goal','player_action','success','failure','feedback'].map(k=>[k,'明确说明']))};
const records={opportunities:{record_id:parent,analysis:{interaction_points:[{point_id:'p1',time_s:2,opportunity:'参与动作',reason:'持物'}]}},
  gameplay:{record_id:game,parent_record_id:parent,prompt_file:'v20',observations:[{point_id:'p1',requested_time_s:2}],
    analysis:{recommendations:[{...core,point_id:'p1',option_id:'p1-option-1'}]}}};
M.ingest(state,records);M.ingest(state,records);
assert.equal(state.points.length,1);assert.equal(state.points[0].versions.length,1);
const p=state.points[0];M.choose(p,p.versions[0]);p.review='kept';
assert.equal(p.stale,false);assert.throws(()=>M.confirmPoint(p),/补齐/);
p.brief={screen:'控件',rules:'明确判定',transition:'返回视频'};M.confirmPoint(p);assert.equal(p.confirmed,true);
M.change(p,{time_s:3});assert.equal(p.stale,true);assert.equal(p.confirmed,false);assert.equal(p.versions[0].time_s,2);
M.choose(p,p.versions[0]);assert.equal(p.stale,true);
const old=JSON.stringify(p.design);records.gameplay={...records.gameplay,record_id:'d'.repeat(32)};
M.ingest(state,records);assert.equal(p.versions.length,2);assert.equal(JSON.stringify(p.design),old);
assert.equal(p.selected,`${game}_p1-option-1`);
assert.equal('raw_response' in state,false);
const manual=M.point('manual_1',3,'拿起物体','手接近物体');state.points.push(manual);
manual.review='kept';manual.enabled=true;p.enabled=true;p.review='kept';
assert.ok(M.warnings(state).some(t=>t.includes('同一时间')));
M.merge(state,manual,p);assert.equal(manual.review,'discarded');assert.equal(manual.enabled,false);
assert.equal(manual.merged_into,p.id);assert.equal(manual.origin,'manual');
assert.throws(()=>M.merge(state,p,p));
console.log('Workbench project model, versions, invalidation, merge and arrangement OK');
