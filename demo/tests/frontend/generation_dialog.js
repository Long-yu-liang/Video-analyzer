const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Node {
  constructor(tag){this.tag=tag;this.children=[];this.listeners={};this.isConnected=true;}
  append(...items){for(const item of items){item.remove();item.parentNode=this;this.children.push(item);}}
  remove(){if(this.parentNode){const items=this.parentNode.children;items.splice(items.indexOf(this),1);this.parentNode=null;}}
  replaceChildren(...items){for(const item of this.children)item.parentNode=null;this.children=[];this.append(...items);}
  setAttribute(){} addEventListener(name,fn){this.listeners[name]=fn;}
  showModal(){this.open=true;}close(){this.open=false;this.listeners.close?.();}focus(){this.focused=true;}
}
const focus=new Node('button'),body=new Node('body'),calls=[];
let release,pending=false;
const context={document:{body,activeElement:focus,createElement:tag=>new Node(tag),querySelectorAll:()=>[]},confirm:()=>true,
  fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>pending?await new Promise(resolve=>release=resolve):url==='/game-tasks'?{task_id:'fixture'}:[]};},console};
vm.createContext(context);
const source=fs.readFileSync(path.join(__dirname,'../../web/assets/js/game-workbench.js'),'utf8');
vm.runInContext(source.replace('root.GameWorkbench={',`root.generateTest={
 setup(ctx){current=()=>({project:ctx.project,state:{points:[ctx.point]},token:'fixture'});panel=element('div');},
 open:showPoint,get:()=>generationDialog,close:closeGenerationDialog,inline:()=>panel.children.length
};root.GameWorkbench={`),context);
const point={id:'p2',time_s:2.53,opportunity:'把握启动时机',reason:'人物准备操作',design:{title:'时机操作',game_id:'timing',player_goal:'适时启动',player_action:'点击'},brief:{},selected:'option',versions:[]};
const snapshot={project:{project_id:'project',revision:1},point};
(async()=>{
  context.generateTest.setup(snapshot);const host=new Node('section');
  context.GameWorkbench.offer(host,point,async()=>snapshot,true);await host.children[0].onclick();
  let modal=context.generateTest.get();assert.ok(modal.dialog.open);assert.ok(modal.title.textContent.includes('2.53 秒'));
  assert.equal(context.generateTest.inline(),0,'input confirmation is never appended below the library');
  assert.equal(body.children.length,1);assert.ok(calls.every(c=>c.url.startsWith('/game-generation?')),'opening does not call generation');
  const original=modal.dialog;await context.generateTest.open(snapshot);modal=context.generateTest.get();
  assert.equal(modal.dialog,original);assert.equal(body.children.length,1,'reuse instead of stacking dialogs');
  await modal.body.children.find(n=>n.textContent==='确认输入并生成此点').onclick();
  const submit=calls.find(c=>c.url==='/game-tasks');assert.equal(JSON.parse(submit.options.body).point_id,'p2');
  assert.ok(modal.body.children.some(n=>n.textContent?.includes('任务已提交')),'task status remains in the dialog');
  modal.dialog.listeners.cancel({preventDefault(){}});assert.equal(context.generateTest.get(),null);assert.ok(focus.focused);
  assert.equal(body.children.length,0);
  pending=true;const opening=context.generateTest.open(snapshot);await new Promise(resolve=>setImmediate(resolve));context.generateTest.close();release([]);await opening;
  assert.equal(context.generateTest.get(),null,'late history response does not reopen a dismissed dialog');
  console.log('Generation dialog: button entry, snapshot, single-point submit, no scroll/stack, Esc and stale response checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
