const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../../web/assets/js/game-workbench.js'),'utf8');
const context={crypto:require('node:crypto').webcrypto};vm.createContext(context);vm.runInContext(source,context);
const point={review:'kept',stale:false,time_s:2,opportunity:'持物',reason:'可操作',design:{title:'拖动'},brief:{},selected:null};
assert.deepEqual(JSON.parse(JSON.stringify(context.GameWorkbench.fitSize(1000,400,16/9))),{width:400*16/9,height:400});
assert.deepEqual(JSON.parse(JSON.stringify(context.GameWorkbench.fitSize(300,600,16/9))),{width:300,height:168.75});
const record={input:JSON.parse(JSON.stringify(point)),output:{javascript:'function mount(c,a){return {destroy(){}}} // </script><img src=x>',css:'#game{color:red}'}};
assert.equal(context.GameWorkbench.matches(record,point),true);
assert.equal(context.GameWorkbench.matches(record,{...point,time_s:3}),false);
assert.equal(context.GameWorkbench.matches(record,{...point,stale:true}),false);
const html=context.GameWorkbench.iframeDocument(record,'test-session');
assert.match(html,/connect-src 'none'/);assert.match(html,/frame-src 'none'/);assert.ok(!html.includes('<img src=x>'));
const bootstrap=html.match(/<script nonce="[^"]+">([\s\S]*)<\/script>/)[1];
assert.doesNotThrow(()=>new vm.Script(bootstrap));
assert.ok(source.includes("event.origin!=='null'"));assert.ok(source.includes('event.source!==session.frame.contentWindow'));
// Execute actual message/timeout/sequence handlers against a deterministic host DOM.
function node(){return {children:[],setAttribute(){},append(...v){this.children.push(...v);},prepend(...v){this.children.unshift(...v);},replaceChildren(...v){this.children=v;},remove(){this.removed=true;}};}
const overlay=node(),video={currentTime:2,paused:false,seeking:false,pause(){this.paused=true;},play(){this.paused=false;return Promise.resolve();}};
let requests=0;const timers=[];
const runtime={document:{getElementById:id=>id==='video'?video:overlay,createElement:()=>node()},crypto:require('node:crypto').webcrypto,
  fetch:async()=>{requests++;return {ok:true,json:async()=>({})};},clearTimeout(){},setTimeout(fn){timers.push(fn);return timers.length;}};
vm.createContext(runtime);
vm.runInContext(source.replace('root.GameWorkbench={',`root.test={
  setup(r){current=()=>({token:'fixture',project:{project_id:'project'},state:{points:[r.input]}});panel=document.createElement('section');session={id:'secret',record:r,frame:{contentWindow:{},remove(){}},started:true,ended:false};},
  source:()=>session?.frame?.contentWindow,receive:onMessage,finished:()=>session?.ended,
  getSession:()=>session,close:closeGame,finish,
  setupSequence(r){session=null;sequence={project:'project',games:[r],fired:new Set(),last:0};},
  tick:onTime,fired:()=>sequence.fired.size,
  autoSetup(records){session=null;current=()=>({project:{project_id:'project'},state:{points:records.map(r=>r.input)}});sequence={project:'project',games:records,fired:new Set(),last:0};root.autoCalls=[];
    play=async(r,large,auto)=>{root.autoCalls.push({id:r.record_id,large,auto});session={record:r};};},
  resume(){session=null;sequence.triggering=false;document.getElementById('video').paused=false;}
};root.GameWorkbench={`),runtime);
record.project_id='project';record.point_id='point';record.input.id='point';record.output.design={timeout_s:8};
runtime.test.setup(record);
runtime.test.receive({source:{},origin:'null',data:{gameSession:'secret',type:'finish',value:{result:'success'}}});
assert.equal(runtime.test.finished(),false,'foreign frame ignored');
runtime.test.receive({source:runtime.test.source(),origin:'https://wrong',data:{gameSession:'secret',type:'finish',value:{result:'success'}}});
assert.equal(runtime.test.finished(),false,'wrong origin ignored');
runtime.test.receive({source:runtime.test.source(),origin:'null',data:{gameSession:'wrong',type:'finish',value:{result:'success'}}});
assert.equal(runtime.test.finished(),false,'wrong session ignored');
runtime.test.receive({source:runtime.test.source(),origin:'null',data:{gameSession:'secret',type:'finish',value:{result:'success',message:'done'}}});
assert.equal(runtime.test.finished(),true);assert.equal(requests,1);
runtime.test.finish('failure','duplicate');assert.equal(requests,1,'finish logs once');
timers.at(-1)();assert.equal(video.paused,false);assert.equal(runtime.test.getSession(),null);
runtime.test.setup(record);runtime.test.finish('timeout','timeout');assert.equal(requests,2);
runtime.test.setup(record);runtime.test.close('error','test error',false);assert.equal(runtime.test.getSession(),null);
runtime.test.setupSequence(record);video.currentTime=1;runtime.test.tick();assert.equal(runtime.test.fired(),0);
runtime.test.setupSequence(record);video.currentTime=3;video.seeking=true;runtime.test.tick();assert.equal(runtime.test.fired(),0,'seeking never retroactively triggers');
console.log('Game workbench snapshot, sandbox, message isolation, single finish, timeout, exit and seek checks passed');

// Modal moves the existing stage, not a cloned player; Esc restores it and focus.
let modalDoc;
class DomNode {
  constructor(tag){this.tag=tag;this.children=[];this.style={width:'',height:''};this.listeners={};this.clientWidth=900;this.clientHeight=500;this.isConnected=true;}
  append(...items){for(const item of items){item.remove();item.parentNode=this;this.children.push(item);}}
  insertBefore(item,before){item.remove();item.parentNode=this;this.children.splice(this.children.indexOf(before),0,item);}
  remove(){if(this.parentNode){const list=this.parentNode.children;list.splice(list.indexOf(this),1);this.parentNode=null;}}
  replaceChildren(...items){this.children.forEach(n=>n.parentNode=null);this.children=[];this.append(...items);}
  setAttribute(){} addEventListener(type,fn){this.listeners[type]=fn;}
  showModal(){this.open=true;} close(){this.open=false;this.listeners.close?.();}
  focus(){this.focused=true;} requestFullscreen(){modalDoc.fullscreenElement=this;return Promise.resolve();}
}
const originalHost=new DomNode('section'),stage=new DomNode('div'),media=new DomNode('video'),previousFocus=new DomNode('button');
media.videoWidth=1920;media.videoHeight=1080;media.currentTime=6;media.pause=()=>{media.paused=true;};stage.append(media);originalHost.append(stage);
modalDoc={body:new DomNode('body'),activeElement:previousFocus,createElement:tag=>new DomNode(tag),createComment:()=>new DomNode('comment'),
  getElementById:id=>id==='video'?media:stage,exitFullscreen(){this.fullscreenElement=null;return Promise.resolve();}};
const modalContext={document:modalDoc,addEventListener(){},removeEventListener(){},crypto:require('node:crypto').webcrypto,
  ResizeObserver:class{observe(){} disconnect(){this.disconnected=true;}}};vm.createContext(modalContext);
vm.runInContext(source.replace('root.GameWorkbench={','root.modalTest={open:openTheater,close:dismissTheater,get:()=>theater};root.GameWorkbench={'),modalContext);
modalContext.modalTest.open(record);
let theaterState=modalContext.modalTest.get();
assert.equal(stage.parentNode,theaterState.player);assert.equal(stage.children[0],media);
assert.ok(Math.abs(parseFloat(stage.style.width)-500*16/9)<0.001);
theaterState.shell.children[0].children[2].onclick();
assert.equal(modalDoc.fullscreenElement,theaterState.shell,'full screen targets a div, never the dialog');
let prevented=false;theaterState.dialog.listeners.cancel({preventDefault(){prevented=true;}});
assert.equal(prevented,true);assert.equal(stage.parentNode,originalHost);
assert.equal(originalHost.children.length,1,'placeholder removed');assert.equal(media.currentTime,6,'progress retained');
assert.equal(media.paused,true);assert.equal(stage.style.width,'');assert.equal(previousFocus.focused,true);
assert.equal(modalContext.modalTest.get(),null);assert.equal(modalDoc.fullscreenElement,null);
modalContext.modalTest.open(record);modalContext.modalTest.close();assert.equal(stage.parentNode,originalHost,'reopen/close remains stable');
console.log('Game workbench big-screen move/restore, aspect ratio, full-screen target, Esc and focus checks passed');
// Version details live in one modal, never appended to the library's inline panel.
const sessionDraftStore=new Map();
const detailsContext={document:modalDoc,sessionStorage:{getItem:key=>sessionDraftStore.get(key)||null,setItem:(key,value)=>sessionDraftStore.set(key,value)}};vm.createContext(detailsContext);
vm.runInContext(source.replace('root.GameWorkbench={',`root.versionTest={
  setup(){panel=document.createElement('div');},open:display,close:closeVersionDialog,
  get:()=>versionDialog,inlineCount:()=>panel.children.length,check:checkArrangement,clearDraftMemory:()=>versionDrafts.clear()
};root.GameWorkbench={`),detailsContext);
detailsContext.versionTest.setup();previousFocus.focused=false;
const versionRecord={...record,status:'checked',project_id:'test-project',record_id:'version-1',
  output:{...record.output,design:{goal:'测试目标',input:'点击目标',rules:'8秒内点中成功',context:'新增游戏设计',screen:'显示候选标记',feedback:'显示结果',states:'结束后继续视频',timeout_s:8}}};
const originalDesign=JSON.stringify(versionRecord.output.design);
const versionContext={project:{project_id:'test-project'},point:{id:'p1',design:{title:'本地版本'}}};
detailsContext.versionTest.open(versionRecord,versionContext);
let versionModal=detailsContext.versionTest.get();
versionModal.note.value='未提交的修复要求';versionModal.note.oninput();versionModal.review.value='failure';versionModal.review.onchange();
assert.equal(versionModal.dialog.open,true);assert.equal(detailsContext.versionTest.inlineCount(),0);
assert.equal(versionModal.body.children.length,1);
assert.ok(versionModal.body.children[0].children.some(n=>n.textContent==='单点立即试玩（大屏）'));
const walk=n=>[n,...n.children.flatMap(walk)];
const designView=walk(versionModal.body).find(n=>n.className==='gw-design');
assert.ok(designView,'design has a readable presentation instead of a bare JSON pre');
const labels=walk(designView).map(n=>n.textContent).filter(Boolean).join(' ');
for(const label of ['玩家目标','如何操作','判定与结束规则','视频情境与设计边界','画面呈现','成功、失败与超时反馈','进入、结束与视频衔接','交互时限 8 秒'])assert.ok(labels.includes(label),label);
assert.equal(JSON.stringify(versionRecord.output.design),originalDesign,'rendering never rewrites model output');
const technical=walk(versionModal.body).find(n=>n.className==='gw-version-technical');
assert.ok(technical&&!technical.open,'raw design/code/paths remain collapsed by default');
assert.ok(walk(technical).some(n=>n.textContent==='查看原始设计 JSON'));
detailsContext.versionTest.open({...versionRecord,record_id:'version-2'},versionContext);
assert.equal(detailsContext.versionTest.get().dialog,versionModal.dialog,'reuse modal instead of stacking');
assert.equal(versionModal.body.children.length,1,'replace old detail content');
versionModal.dialog.listeners.cancel({preventDefault(){}});
assert.equal(detailsContext.versionTest.get(),null);assert.equal(previousFocus.focused,true);
assert.equal(versionModal.dialog.parentNode,null,'closed modal removed');
detailsContext.versionTest.open(versionRecord,versionContext);
assert.equal(detailsContext.versionTest.get().note.value,'未提交的修复要求','version note survives close and reopen');
assert.equal(detailsContext.versionTest.get().review.value,'failure');
detailsContext.versionTest.close();
assert.equal(detailsContext.versionTest.get(),null,'explicit close works after reopening');
detailsContext.versionTest.clearDraftMemory();detailsContext.versionTest.open(versionRecord,versionContext);
assert.equal(detailsContext.versionTest.get().note.value,'未提交的修复要求','session storage restores note after in-memory cache is cleared');
detailsContext.versionTest.close();
console.log('Game version detail modal, replacement, Esc and focus restoration checks passed');
const inventory={points:[{point_id:'p1',time_s:1,title:'操作一',versions:[{record_id:'v1',usable:true,title:'游戏一'}]},
  {point_id:'p2',time_s:5,title:'操作二',versions:[{record_id:'v2',usable:false,stale:true,title:'过期游戏'}]}]};
const check=detailsContext.versionTest.check;
assert.equal(check(inventory,[],true).enabledCount,0);assert.ok(check(inventory,[],true).errors.length);
assert.equal(check(inventory,[{point_id:'p1',generation_id:'v1',enabled:true}],true).errors.length,0);
assert.ok(check(inventory,[{point_id:'p1',generation_id:'missing',enabled:true}],true).errors.some(e=>e.text.includes('缺失')));
assert.ok(check(inventory,[{point_id:'p2',generation_id:'v2',enabled:true}],true).errors.some(e=>e.text.includes('变化')));
assert.ok(check(inventory,[{point_id:'p1',generation_id:'v1',enabled:true}],false).errors.some(e=>e.view==='analysis'));
const collision=JSON.parse(JSON.stringify(inventory));collision.points[1].time_s=1;
assert.ok(check(collision,[{point_id:'p1',generation_id:'v1',enabled:true},{point_id:'p2',generation_id:'v2',enabled:true}],true).errors.some(e=>e.text.includes('时间相同')));
assert.ok(check(inventory,[{point_id:'p1',generation_id:'v1',enabled:true}],true,[{id:'p1',time_s:2,review:'kept'}]).errors.some(e=>e.view==='edit'));
console.log('Arrangement preflight: no selection, missing/stale versions, video mismatch and time collisions OK');
const nextRecord=JSON.parse(JSON.stringify(record));record.record_id='first';nextRecord.record_id='second';nextRecord.point_id='next';nextRecord.input.id='next';nextRecord.input.time_s=5;
runtime.test.autoSetup([record,nextRecord]);video.currentTime=2.2;video.paused=false;video.seeking=false;
runtime.test.tick();runtime.test.tick();assert.equal(runtime.autoCalls.length,1,'same crossing triggers once');
assert.equal(runtime.autoCalls[0].auto,true,'sequence starts game without per-point confirmation');
runtime.test.resume();video.currentTime=5.2;runtime.test.tick();assert.equal(runtime.autoCalls.length,2);
assert.equal(runtime.autoCalls[1].id,'second');assert.equal(runtime.autoCalls[1].auto,true);
console.log('Game workbench two-point automatic sequence crossing checks passed');
