const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Node {
  constructor(tag){this.tag=tag;this.children=[];this.listeners={};this.open=false;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(){} addEventListener(name,fn){this.listeners[name]=fn;}
}
let project='project-a',tasks=[
  {task_id:'a',title:'正在生成',status:'waiting_model',created_at:new Date().toISOString()},
  {task_id:'b',title:'完成版本',status:'completed',created_at:new Date().toISOString(),generation_id:'one'},
  {task_id:'c',title:'失败版本',status:'failed',created_at:new Date().toISOString(),generation_id:'two'}
];
const context={console,setTimeout:()=>1,clearTimeout(){},document:{createElement:tag=>new Node(tag)},
  fetch:async(url,options)=>{
    if(url==='/game-task-dismiss'){const payload=JSON.parse(options.body);tasks=tasks.filter(t=>t.task_id!==payload.task_id);return {ok:true,json:async()=>({dismissed:true})};}
    assert.ok(url.startsWith('/game-tasks?'));return {ok:true,json:async()=>tasks};
  },getProject:()=>project};
vm.createContext(context);
const source=fs.readFileSync(path.join(__dirname,'../../web/assets/js/game-workbench.js'),'utf8');
vm.runInContext(source.replace('root.GameWorkbench={',`root.taskTest={
  setup(){current=()=>({project:{project_id:root.getProject()},token:'fixture'});taskHost=element('div');panel=element('div');refreshLibrary=()=>{};},
  refresh:refreshTasks,get:()=>({details:taskDisclosure,summary:taskSummary,list:taskList})
};root.GameWorkbench={`),context);
(async()=>{
  context.taskTest.setup();await context.taskTest.refresh();
  let state=context.taskTest.get();assert.equal(state.details.open,false,'tasks default collapsed');
  assert.ok(state.summary.textContent.includes('3 项 · 1 项进行中'));
  assert.ok(state.summary.textContent.includes('展开全部任务'));
  state.details.open=true;state.details.listeners.toggle();const details=state.details;
  await context.taskTest.refresh();state=context.taskTest.get();
  assert.equal(state.details,details,'polling reuses disclosure');assert.equal(state.details.open,true);
  assert.ok(state.summary.textContent.includes('收起任务'));
  assert.ok(!state.list.children[0].children.some(n=>n.textContent==='移除任务'),'running task cannot be removed');
  await state.list.children[1].children.find(n=>n.textContent==='移除任务').onclick();
  assert.equal(tasks.length,2);assert.equal(context.taskTest.get().list.children.length,2);
  project='project-b';await context.taskTest.refresh();assert.equal(context.taskTest.get().details.open,false,'new project starts collapsed');
  console.log('Task UI collapse, polling state, completed removal and project isolation passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
