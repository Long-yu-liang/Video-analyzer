// Exercise real view switching without model calls or project writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../web/assets/js/workbench.js'), 'utf8');
const views = ['analysis', 'opportunities', 'design', 'edit', 'games', 'arrange'];
const buttons = views.map(view => ({dataset: {wbTab: view}, attributes: {},
  setAttribute(k, v) {this.attributes[k] = v;}, removeAttribute(k) {delete this.attributes[k];}}));
const nodes = {wbViewTitle: {}, wbViewHint: {}};
const context = {document: {body: {dataset: {}}, getElementById: id => nodes[id], querySelectorAll: () => buttons}};
vm.createContext(context);
vm.runInContext(source.replace('root.Workbench={init,',
  'root.navigationTest={setup(){render=()=>{};renderEditor=()=>{};touch=()=>{};bridge={catalog:()=>[{id:"timing"}]};selectedId="retained";},selected:()=>selectedId,toggle:toggleDraftEditor,collapsed:p=>collapsedDraftEditors.has(p)};root.Workbench={init,'), context);
context.navigationTest.setup();
for (const view of views) {
  context.Workbench.navigate(view);
  assert.equal(context.document.body.dataset.workbenchView, view);
  assert.equal(buttons.filter(b => b.attributes['aria-current'] === 'step').length, 1);
  assert.equal(buttons.find(b => b.attributes['aria-current'] === 'step').dataset.wbTab, view);
  assert.ok(nodes.wbViewTitle.textContent);
  assert.equal(context.navigationTest.selected(), 'retained', 'navigation must preserve the chosen point');
}
context.Workbench.navigate('unknown');
assert.equal(context.document.body.dataset.workbenchView, 'arrange');
const html = fs.readFileSync(path.join(__dirname, '../../web/index.html'), 'utf8');
assert.equal((html.match(/data-wb-tab=/g) || []).length, 6);
assert.match(html, /id="eventResults"[\s\S]*id="events"[\s\S]*id="analysisResults"/);
assert.equal((html.match(/id="suggest"/g) || []).length, 1);
assert.match(html, /id="analysisResults"[\s\S]*id="suggest"/);
assert.equal((html.match(/id="recommend"/g) || []).length, 1);
assert.ok(html.includes('id="promptDialog"') && !html.includes('id="promptDialog" open'), 'prompt settings stay in a closed dialog');
assert.ok(html.includes('id="tokenDialog"') && !html.includes('id="tokenDialog" open'), 'token usage stays in a closed dialog');
assert.ok(!html.includes('导入已有分析结果') && !html.includes('id="importRecord"'), 'analysis import is removed');
const draft={design:{title:'已填方案',player_action:'已编辑操作'},brief:{rules:'已填规则'},selected:'original',confirmed:true};
const before=JSON.stringify(draft);
context.navigationTest.toggle(draft);assert.equal(context.navigationTest.collapsed(draft),true);
context.navigationTest.toggle(draft);assert.equal(context.navigationTest.collapsed(draft),false);
assert.equal(JSON.stringify(draft),before,'collapse and reopen must not mutate the draft or its confirmation');
const manual={design:null,brief:{}};context.navigationTest.toggle(manual);
assert.equal(manual.design.title,'人工玩法');assert.equal(context.navigationTest.collapsed(manual),false);
context.navigationTest.toggle(manual);assert.equal(context.navigationTest.collapsed(manual),true);
assert.equal(manual.design.title,'人工玩法','hiding a new design must retain it');
assert.ok(html.includes('<summary>历史方案编辑器</summary>'));
assert.ok(source.includes("button('确认玩法描述',"));
console.log('Six-view navigation, separated events/opportunities and context retention checks passed');
