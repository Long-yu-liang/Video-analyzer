// Exercise the real deletion handler with isolated project/DOM/network fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../web/assets/js/workbench.js'), 'utf8');
function fixture({confirm = true, failure = false, busy = false} = {}) {
  const nodes = {projectName: {}, projectList: {}, projectStatus: {}};
  const calls = [];
  const context = {
    document: {getElementById: id => nodes[id]},
    confirm: () => confirm, clearTimeout() {}, setTimeout() {},
    fetch: async (url, options) => {
      calls.push({url, body: JSON.parse(options.body)});
      return {ok: !failure, json: async () => failure ? {error: 'conflict'} : {deleted: true}};
    },
  };
  vm.createContext(context);
  const hook = `root.test = {
    setup() {
      project={project_id:'abc',revision:2};state=blank();state.name='saved';
      dirty=false;render=()=>{};refreshList=async()=>{};
      bridge={isBusy:()=>${busy},token:()=> 'local',restore:()=>{},ready:()=>{}};
    }, remove:deleteProject, inspect:()=>({project,state,loading,dirty})
  };`;
  vm.runInContext(source.replace('root.Workbench={init,', hook + '\n  root.Workbench={init,'), context);
  context.test.setup();
  return {context, calls, nodes};
}
(async () => {
  let f = fixture({confirm: false});
  await f.context.test.remove();
  assert.equal(f.calls.length, 0);
  assert.equal(f.context.test.inspect().project.project_id, 'abc');
  f = fixture({busy: true});
  await assert.rejects(f.context.test.remove(), /稍后删除/);
  assert.equal(f.calls.length, 0);
  f = fixture({failure: true});
  await assert.rejects(f.context.test.remove(), /conflict/);
  assert.equal(f.context.test.inspect().project.project_id, 'abc');
  assert.equal(f.context.test.inspect().loading, false);
  f = fixture();
  await f.context.test.remove();
  assert.equal(f.calls[0].url, '/project-delete');
  assert.equal(f.calls[0].body.revision, 2);
  assert.equal(f.context.test.inspect().project, null);
  assert.equal(f.context.test.inspect().dirty, false);
  assert.equal(f.nodes.projectList.value, '');
  assert.match(f.nodes.projectStatus.textContent, /原视频和模型结果未删除/);
  console.log('project delete UI checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
