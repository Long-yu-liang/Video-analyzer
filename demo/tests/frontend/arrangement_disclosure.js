const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Element {
  constructor(){this.children=[];this.listeners={};this.open=false;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  addEventListener(name,fn){this.listeners[name]=fn;}
}
const host=new Element();let calls=0;
const context={document:{getElementById:()=>host,createElement:()=>new Element()},
  GameWorkbench:{renderArrangement(editor){calls++;editor.append(new Element());}}};
vm.createContext(context);
const source=fs.readFileSync(path.join(__dirname,'../../web/assets/js/workbench.js'),'utf8');
vm.runInContext(source.replace('root.Workbench={',`root.arrangementTest={setup(id){project={project_id:id};state={...blank(),points:[{id:'p1',enabled:true,review:'kept'}]};},render:renderArrangement};root.Workbench={`),context);
context.arrangementTest.setup('a');context.arrangementTest.render();
let disclosure=host.children[0];assert.equal(disclosure.open,false);assert.equal(calls,0,'collapsed view does not load editor');
assert.ok(disclosure.children[0].textContent.includes('1 个点已启用'));
disclosure.open=true;disclosure.listeners.toggle();assert.equal(calls,1);
assert.ok(disclosure.children[0].textContent.includes('收起设置'));
disclosure.listeners.toggle();assert.equal(calls,1,'same editor loads once');
context.arrangementTest.render();disclosure=host.children[0];assert.equal(disclosure.open,true,'rerender retains expansion');
disclosure.open=false;disclosure.listeners.toggle();context.arrangementTest.render();assert.equal(host.children[0].open,false);
context.arrangementTest.setup('b');context.arrangementTest.render();assert.equal(host.children[0].open,false,'project state is independent');
console.log('Arrangement defaults collapsed, loads on expansion and retains per-project state');
