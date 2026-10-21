// Execute the page's real inline script against a small DOM/fetch fixture.
// No API call or browser package is needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'web', 'index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script, 'page script exists');

function element(id = '') {
  const node = { id, value: '', files: [], disabled: false, children: [], currentTime: 0,
    pause() { this.paused = true; },
    replaceChildren(...children) { this.children = children; this._text = ''; },
    append(...children) { this.children.push(...children); },
    click() { if (this.onclick) this.onclick(); },
  };
  Object.defineProperty(node, 'textContent', {
    get() { return this._text || ''; },
    set(value) { this._text = value; this.children = []; },
  });
  return node;
}

const ids = ['model', 'prompt', 'sample', 'file', 'video', 'mode', 'fps', 'analyze',
  'download', 'summary', 'metrics', 'points', 'events', 'limitations',
  'raw', 'status', 'configState'];
const nodes = Object.fromEntries(ids.map(id => [id, element(id)]));
nodes.mode.value = 'frames';
nodes.fps.value = '2';

let requestedURL;
let points = [{ time_s: 11.5, action_start_s: 12.5, source: 'both', visible_cue: '持球',
  player_action: '判断出手时机', reason: '出手前', evidence: '11.5 秒画面', uncertainty: '采样误差' }];
const fetch = async url => {
  if (url === '/config') return { json: async () => ({
    models: { 'qwen3.8-flash': 'beeroute' }, samples: ['S05_传球投篮.mp4'],
    configured: { beeroute: true }, prompt_file: 'prompts/v7.txt',
    prompt_files: ['prompts/v5.txt', 'prompts/v6.txt', 'prompts/v7.txt'], token: 'fixture',
  }) };
  assert.match(url, /^\/analyze\?/);
  requestedURL = url;
  return { ok: true, json: async () => ({
    status: 'success', record_file: 'fixture.json', model: 'qwen3.8-flash',
    video_name: 'S05_传球投篮.mp4', mode: 'frames', prompt_file: 'prompts/v5.txt',
    total_s: 1, api_roundtrip_s: 1, usage: {},
    analysis: { summary: '测试', events: [], interaction_points: points, limitations: [] },
  }) };
};
vm.runInNewContext(script, {
  document: { getElementById: id => nodes[id], createElement: () => element() },
  fetch, URLSearchParams, encodeURIComponent, URL, Blob, setTimeout, console,
});

(async () => {
  await new Promise(resolve => setImmediate(resolve)); // /config callback
  assert.equal(nodes.prompt.value, 'v6');
  nodes.sample.value = 'S05_传球投篮.mp4';
  await nodes.sample.onchange();
  await nodes.analyze.onclick();
  assert.match(requestedURL, /prompt=v6/);
  assert.equal(nodes.points.children.length, 1);
  assert.match(nodes.points.children[0].children[0].textContent, /声画共同/);
  nodes.points.children[0].click();
  assert.equal(nodes.video.currentTime, 11.5);
  assert.equal(nodes.video.paused, true);
  points = [{ time_s: 2.1, action_observed: false, action_start_s: null,
    source: 'visual', visible_cue: '准备姿态', player_action: '决定何时开始',
    reason: '行动尚未拍到', evidence: '2.1 秒画面；后续行动未拍到', uncertainty: '后续行动未拍到' }];
  nodes.prompt.value = 'v9';
  await nodes.analyze.onclick();
  assert.match(requestedURL, /prompt=v9/);
  assert.match(nodes.points.children[0].children[0].textContent, /后续行动未拍到/);
  nodes.points.children[0].click();
  assert.equal(nodes.video.currentTime, 2.1);
  points = [
    { time_s: 1.2, opportunity: '观察物体', reason: '物体出现' },
    { time_s: 2.4, opportunity: '决定方向', reason: '经过岔路' },
  ];
  nodes.prompt.value = 'v12';
  await nodes.analyze.onclick();
  assert.match(requestedURL, /prompt=v12/);
  assert.equal(nodes.points.children.length, 2);
  assert.match(nodes.points.children[1].textContent, /决定方向/);
  assert.doesNotMatch(nodes.points.children[1].children[0].textContent, /undefined/);
  nodes.points.children[1].click();
  assert.equal(nodes.video.currentTime, 2.4);
  points = [];
  nodes.prompt.value = 'v7';
  await nodes.analyze.onclick();
  assert.match(requestedURL, /prompt=v7/);
  assert.equal(nodes.points.children.length, 0);
  assert.equal(nodes.points.textContent, '本次未建议交互点。');
  console.log('UI point seek and empty state OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
