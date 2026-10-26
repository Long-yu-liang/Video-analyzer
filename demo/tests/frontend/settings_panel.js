const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag = 'div') { this.tagName = tag.toUpperCase(); this.children = []; this.value = ''; this.checked = false; this.hidden = false; this.textContent = ''; this.attributes = {}; }
  append(...children) { this.children.push(...children); }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(key, handler) { this['on' + key] = handler; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  click() { if (this.onclick) return this.onclick(); }
}
const ids = Object.fromEntries(['settingsTabs','settingsBody','settingsStatus','settingsSave','settingsReload',
  'openSettings','closeSettings','settingsDialog','settingsExport','settingsExportSecrets','settingsImport'].map(id => [id, new Element()]));
const requests = [];
let revision = 0, configured = true, conflict = false, refreshed = 0, confirmed = false;
let values = {BEEROUTE_MODELS:'qwen3-vl-plus',BEEROUTE_BASE_URL:'https://fixture.example/v1',MINIMAX_BASE_URL:'https://fixture.example/v1',MINIMAX_MODEL:'fixture-mini'};
const context = {console,Blob,URL,setTimeout: fn => fn(),confirm: () => confirmed,
  document: {getElementById: id => ids[id], createElement: tag => new Element(tag)},
  fetch: async (url, options) => {
    requests.push({url,options});
    assert.equal(options.headers['X-Local-Token'],'fixture-token');
    if (url === '/settings/check-tools') return {ok:true,json: async () => ({node:{ok:true,message:'fixture version'}})};
    if (url.startsWith('/settings/export')) return {ok:true,json: async () => ({app:'video-analyzer',schema_version:1,values})};
    if (options.method === 'POST') {
      const body = JSON.parse(options.body);
      if (conflict) return {ok:false,json:async () => ({error:'revision conflict'})};
      assert.equal(body.revision, revision);
      values = {...values,...body.values}; configured = !body.clear_keys.includes('BEEROUTE_API_KEY'); revision++;
    }
    return {ok:true,json:async () => ({revision,values,configured:{BEEROUTE_API_KEY:configured}})};
  }};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../../web/assets/js/settings-panel.js'),'utf8') + '\nthis.panel=SettingsPanel;',context);
const find = id => Object.values(ids).flatMap(root => {
  const walk = el => [el, ...el.children.filter(c => typeof c === 'object').flatMap(walk)]; return walk(root);
}).find(el => el.id === 'setting-' + id);

(async () => {
  context.panel.init({token:'fixture-token',afterSave:async () => { refreshed++; }});
  await ids.openSettings.onclick();
  assert.equal(find('BEEROUTE_API_KEY').value,'');
  assert.match(find('BEEROUTE_API_KEY').placeholder,/已配置/);
  find('BEEROUTE_MODELS').value = 'qwen3-vl-plus\nfixture-other\nfixture-other';
  find('BEEROUTE_MODELS').oninput();
  await ids.settingsSave.onclick();
  assert.equal(values.BEEROUTE_MODELS,'qwen3-vl-plus,fixture-other');
  assert.equal(refreshed,1);
  conflict = true;
  find('MINIMAX_MODEL').value='unsaved-draft'; find('MINIMAX_MODEL').oninput();
  await ids.settingsSave.onclick();
  assert.equal(find('MINIMAX_MODEL').value,'unsaved-draft');
  assert.equal(ids.settingsStatus.textContent,'revision conflict');
  ids.closeSettings.onclick(); await ids.openSettings.onclick();
  assert.equal(find('MINIMAX_MODEL').value,'unsaved-draft','close/reopen preserves unsaved draft');
  await ids.settingsReload.onclick();
  assert.equal(find('MINIMAX_MODEL').value,'unsaved-draft','cancel reload preserves draft');
  confirmed=true; conflict=false;
  await ids.settingsReload.onclick();
  assert.equal(find('MINIMAX_MODEL').value,'fixture-mini');
  const file={size:100,text:async()=>JSON.stringify({app:'video-analyzer',schema_version:1,values:{MINIMAX_MODEL:'imported',DASHSCOPE_BASE_URL:'https://legacy.example/v1'}})};
  await ids.settingsImport.onchange({target:{files:[file],value:'fixture'}});
  assert.equal(find('MINIMAX_MODEL').value,'imported');
  await ids.settingsSave.onclick();
  assert.equal(values.DASHSCOPE_BASE_URL,'https://legacy.example/v1');
  await ids.settingsExport.onclick();
  assert.equal(requests.at(-1).url,'/settings/export');
  ids.settingsExportSecrets.checked=true; confirmed=false;
  const before=requests.length; await ids.settingsExport.onclick(); assert.equal(requests.length,before);
  confirmed=true; await ids.settingsExport.onclick(); assert.equal(requests.at(-1).url,'/settings/export?include_secrets=1');
  console.log('Settings UI handlers: save, redaction, conflicts, drafts, import/export OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
