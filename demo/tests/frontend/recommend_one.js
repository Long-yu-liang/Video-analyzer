const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Node{
  constructor(){this.children=[];this.listeners={};}
  append(...items){for(const item of items){item.parent=this;this.children.push(item);}}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);}
  setAttribute(){} addEventListener(name,fn){this.listeners[name]=fn;}
}
const status={},calls=[],context={document:{createElement:()=>new Node(),getElementById:()=>status},calls};vm.createContext(context);
const source=fs.readFileSync(path.join(__dirname,'../../web/assets/js/workbench.js'),'utf8');
vm.runInContext(source.replace('root.Workbench={',`root.singleTest={
 setup(){verified=true;currentView='design';bridge={peek:()=>({records:{opportunities:{record_id:'source'}}}),retry:async(id,time)=>root.calls.push({id,time})};notify=()=>{};render=()=>{};},render:renderOptionCards
};root.Workbench={`),context);
(async()=>{
  context.singleTest.setup();const host=new Node();
  context.singleTest.render(host,{origin:'ai',source_record_id:'source',point_id:'p8',id:'ai-p8',time_s:32,versions:[],design:null,selected:null});
  const find=(node,text)=>node.children.find(n=>n.textContent===text)||node.children.map(n=>find(n,text)).find(Boolean);
  const button=find(host,'仅推荐此点');assert.ok(button,'empty point still has recommendation entry');await button.onclick();
  const confirm=find(host,'确认推荐当前点');assert.ok(confirm);await confirm.onclick();
  assert.equal(calls.length,1);assert.deepEqual(JSON.parse(JSON.stringify(calls[0])),{id:'p8',time:32});
  console.log('Single-point recommendation works with no previous option or selected gameplay');
})().catch(error=>{console.error(error);process.exitCode=1;});
