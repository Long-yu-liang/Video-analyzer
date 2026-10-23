const assert=require('node:assert/strict');
function element(tag='') {
  return {tag,children:[],value:'',textContent:'',attributes:{},append(...xs){this.children.push(...xs);},
    replaceChildren(...xs){this.children=xs;},setAttribute(k,v){this.attributes[k]=v;},click(){this.onclick?.();}};
}
global.document={createElement:element};
require('../../web/assets/js/gameplay/recommendations.js');
const api=global.GameplayRecommendations;
const core={fit:'杯子与水壶可见',player_goal:'表达倒水',player_action:'选择一种操作',success:'操作完成',failure:'操作未完成',feedback:'显示操作结果'};
const record={catalog_snapshot:{types:[{id:'choice',name:'选择'},{id:'drag',name:'拖拽'}]},
  observations:[{point_id:'p1',requested_time_s:2,frame_time_s:2.1}],
  analysis:{recommendations:[{...core,point_id:'p1',option_id:'p1-o1',game_id:'choice',game_name:'选择'},
    {...core,point_id:'p1',option_id:'p1-o2',game_id:'drag',game_name:'拖拽'}]}};
const controls=[{pointId:'p1',point:{opportunity:'参与倒水'},include:{checked:true}}];
const container=element();let seekTime;
const partial=api.render(record,[...controls,{pointId:'p2',point:{opportunity:'另一点'},include:{checked:true}}],container,()=>{},()=>{});
assert.equal(partial.length,1,'restored single-point runs must not render unrelated controls without a frame');
let drafts=api.render(record,controls,container,()=>{},t=>{seekTime=t;});
function all(node){return [node,...node.children.flatMap(all)];}
function find(className){return all(container).find(e=>e.className===className);}
find('recommendation-seek').click();assert.equal(seekTime,2.1);
const buttons=all(container).filter(e=>e.className==='choose-recommendation');assert.equal(buttons.length,2);
buttons[1].click();find('recommendation-accept').click();
let saved=api.selections(drafts)[0];assert.equal(saved.design.game_id,'drag');assert.equal(saved.status,'accepted');
assert.equal(saved.decision_reason,'');assert.equal('anchors' in saved,false);
assert.equal(all(container).some(e=>e.tag==='canvas'),false);
find('recommendation-player_action').value='拖向杯口';find('recommendation-player_action').oninput();
assert.throws(()=>api.selections(drafts));find('recommendation-confirm').click();
assert.equal(api.selections(drafts)[0].status,'edited');
api.invalidate(drafts,controls[0]);assert.throws(()=>api.selections(drafts),/重新推荐/);
assert.equal(drafts[0].design.player_action,'拖向杯口');
drafts[0].restore({option_id:'p1-o2',design:{game_id:'drag',...core,player_action:'恢复的人工操作'},
  status:'edited',decision_reason:'保存后恢复',stale:false});
saved=api.selections(drafts)[0];assert.equal(saved.design.player_action,'恢复的人工操作');
assert.equal(saved.decision_reason,'保存后恢复');assert.equal(saved.option_id,'p1-o2');
record.analysis.warnings=[{path:'abstentions[0]',message:'abstentions[0] 应为对象，实际为字符串。'}];
drafts=api.render(record,controls,container,()=>{},()=>{});
assert.ok(find('validation-warnings'));
assert.ok(all(container).some(e=>e.textContent==='abstentions[0] 应为对象，实际为字符串。'));
all(container).find(e=>e.className==='choose-recommendation').click();
find('recommendation-accept').click();assert.equal(api.selections(drafts)[0].status,'accepted');
record.analysis.recommendations=[];
drafts=api.render(record,controls,container,()=>{},()=>{});
find('recommendation-discard').click();assert.equal(api.selections(drafts)[0].status,'discarded');
console.log('Recommendation choice, drag without anchors, edits, stale draft and empty state passed');
