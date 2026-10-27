// Execute the page's real app entry against a small DOM/fetch fixture.
// No API call or browser package is needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', '..', 'web', 'index.html'), 'utf8');
assert.ok(html.includes('src="/assets/js/app.js"'), 'page loads the real app entry');
const script = fs.readFileSync(path.join(__dirname, '..', '..', 'web', 'assets', 'js', 'app.js'), 'utf8');
assert.ok(script, 'page script exists');

function element(id = '') {
  const node = { id, value: '', files: [], disabled: false, children: [], currentTime: 0,
    pause() { this.paused = true; },
    replaceChildren(...children) { this.children = children; this._text = ''; },
    append(...children) { this.children.push(...children); },
    prepend(...children) { this.children.unshift(...children); },
    click() { if (this.onclick) this.onclick(); },
  };
  Object.defineProperty(node, 'textContent', {
    get() { return this._text || ''; },
    set(value) { this._text = value; this.children = []; },
  });
  return node;
}

const ids = ['model', 'mode', 'fps', 'eventPrompt', 'opportunityPrompt', 'gameplayPrompt', 'promptPreview', 'flowHint', 'sample', 'file', 'video', 'recognize', 'suggest', 'recommend', 'reset', 'reviewHint',
  'download', 'summary', 'metrics', 'points', 'events', 'limitations', 'catalogNote', 'gameplay', 'saveBrief',
  'raw', 'status', 'configState', 'tokenUsage', 'generationPromptPreviewVersion', 'generationPromptActive', 'generationPromptPreview'];
const nodes = Object.fromEntries(ids.map(id => [id, element(id)]));

let requestedURL, briefBody;
let failMedia = false;
let partialFailure = false;
let partialGameplay = false;
let gameplayRecommendations = [{ point_id: 'p1', observation_id: 'e1', event_id: 'e1', time_s: 1.5,
  opportunity: '决定是否投篮', reason: '球已经在手中', game_id: 'choice', game_name: '剧情选择',
  fit: '持球且有可见分支', player_goal: '作出选择', player_action: '在动作里选一个',
  success: '选中一个动作', failure: '时间结束未选', feedback: '显示所选项' }];
const fetch = async (url, options = {}) => {
  if (url === '/config') return { json: async () => ({
    models: { 'qwen3.8-flash': 'beeroute', 'qwen3-vl-plus': 'beeroute' }, samples: ['S05_传球投篮.mp4'],
    configured: { beeroute: true }, prompt_file: 'prompts/v7.txt',
    game_generation_prompts: ['v1','v2'].map(version=>({version,files:['generate','repair'].map(name=>({label:name==='generate'?'生成游戏':'修复游戏',path:`prompts/game-generation/${version}/${name}.txt`,content:`${version} ${name} 全文`,active:version==='v2'}))})),
    prompt_files: ['prompts/v5.txt', 'prompts/v6.txt', 'prompts/v7.txt'], token: 'fixture',
    stage_prompts: {
      events: [
        { version: 'v13', path: 'prompts/v13/events.txt', content: '只识别事件' },
        { version: 'v14', path: 'prompts/v14/events.txt', content: '长视频分段识别' },
      ],
      opportunities: [
        { version: 'v13', path: 'prompts/v13/opportunities.txt', content: '寻找参与机会' },
        { version: 'v14', path: 'prompts/v14/opportunities.txt', content: 'v14 机会' },
      ],
      gameplay: [
        { version: 'v15', path: 'prompts/v15/gameplay.txt', content: '推荐玩法' },
      ],
    },
    games: { version: 'day3', types: [
      { id: 'choice', name: '剧情选择', play: '选一个下一步。', situations: '有可见分支。', player_goal: '作出选择。' },
      { id: 'timing', name: '时机操作', play: '在窗口内按下。', situations: '动作即将发生。', player_goal: '卡准时刻。' },
    ] },
    prompt_catalog: [
      ...['v5','v6','v7','v9','v12'].map(version => ({ version, flow: 'single',
        files: [{ path: `prompts/${version}.txt`, content: `${version} prompt body` }] })),
      { version: 'v13', flow: 'two_stage', files: [
        { path: 'prompts/v13/events.txt', content: '只识别事件' },
        { path: 'prompts/v13/opportunities.txt', content: '寻找参与机会' }] },
      { version: 'v14', flow: 'two_stage', files: [
        { path: 'prompts/v14/events.txt', content: '长视频分段识别' },
        { path: 'prompts/v13/opportunities.txt', content: '寻找参与机会' }] },
    ],
  }) };
  if (url.startsWith('/events?')) {
    requestedURL = url;
    if(partialFailure)return {ok:true,json:async()=>({status:'failed',stage:'events',record_id:'a'.repeat(32),metadata:{duration_s:58},model:'fixture',record_file:'partial.json',
      error:'events[0].observation_s 超出片长',segments:[{core_start_s:20,core_end_s:40,status:'failed',error:'核心时间错误'}],
      segment_coverage:{complete:false,ranges:[{start_s:0,end_s:20,status:'success'},{start_s:20,end_s:40,status:'failed'},{start_s:40,end_s:58,status:'unprocessed'}]},
      partial_analysis:{summary:'仅前20秒',limitations:['全片未完成'],events:[{observation_id:'e1',start_s:1,end_s:2,observation_s:1.5,description:'持球',evidence:'球在手中',uncertainty:'无'}]}})};
    if (failMedia) return {ok:false,json:async()=>({status:'failed',stage:'events',
      error:'视频分段/重新编码失败（ffmpeg，退出码 8987）',media_error:{operation:'transcode',exit_code:8987}})};
    return { ok: true, json: async () => ({ status: 'success', record_id: 'a'.repeat(32),
      record_file: 'events.json', model: 'qwen3-vl-plus', video_name: 'S05_传球投篮.mp4',
      prompt_file: 'prompts/v13/events.txt', total_s: 1,
      analysis: { summary: '传接球', limitations: [], events: [
        { observation_id: 'e1', start_s: 1, end_s: 2, observation_s: 1.5,
          description: '接住球', evidence: '球在手中', uncertainty: '无' },
        { observation_id: 'e2', start_s: 3, end_s: 4, observation_s: 3.5,
          description: '背景路人', evidence: '路人走过', uncertainty: '无' },
      ] } }) };
  }
  if (url.startsWith('/opportunities?')) {
    requestedURL = url;
    return { ok: true, json: async () => ({ status: 'success', record_id: 'b'.repeat(32), record_file: 'opportunities.json',
      model: 'qwen3-vl-plus', video_name: 'S05_传球投篮.mp4', metadata: { duration_s: 12 },
      prompt_file: 'prompts/v13/opportunities.txt', total_s: 1,
      analysis: { warnings:[{path:'interaction_points[1].observation_id',message:'interaction_points[1].observation_id 未审核，未自动改绑画面。'}],
        validation_summary:{returned_count:2,accepted_count:1,rejected_count:1},
        interaction_points: [{ observation_id: 'e1', event_id: 'e1', time_s: 1.5,
        opportunity: '决定是否投篮', reason: '球已经在手中' }] } }) };
  }
  if (url.startsWith('/gameplay?')) {
    requestedURL = url;
    if(partialGameplay)return {ok:true,json:async()=>({status:'partial',record_id:'d'.repeat(32),record_file:'partial-gameplay.json',batches:[{status:'partial'}],
      analysis:{recommendations:[],incomplete_points:[{point_id:'p1',reason:'模型漏答'}],coverage:{requested_count:1,recommended_count:0,abstained_count:0,incomplete_count:1,complete:false}}})};
    return { ok: true, json: async () => ({ status: 'success', record_id: 'c'.repeat(32), record_file: 'gameplay.json',
      model: 'qwen3-vl-plus', video_name: 'S05_传球投篮.mp4', prompt_file: 'prompts/v15/gameplay.txt', total_s: 1,
      analysis: { recommendations: gameplayRecommendations } }) };
  }
  if (url === '/briefs') {
    briefBody = JSON.parse(options.body);
    return { ok: true, json: async () => ({ status: 'success', record_file: 'brief.json', video_name: 'S05_传球投篮.mp4' }) };
  }
  throw new Error('unexpected request ' + url);
};
const usageScript = fs.readFileSync(path.join(__dirname, '../../web/assets/js/token-usage.js'), 'utf8');
const recordPreviewScript = fs.readFileSync(path.join(__dirname, '../../web/assets/js/record-preview.js'), 'utf8');
vm.runInNewContext(usageScript + '\n' + recordPreviewScript + '\n' + script, {
  document: { getElementById: id => nodes[id], createElement: () => element() },
  fetch, URLSearchParams, encodeURIComponent, URL, Blob, setTimeout, console,
});

(async () => {
  await new Promise(resolve => setImmediate(resolve)); // /config callback
  assert.equal(nodes.model.value, 'qwen3-vl-plus');
  assert.equal(nodes.model.disabled, false);
  assert.equal(nodes.mode.value, 'direct');
  assert.equal(nodes.fps.value, '2');
  assert.equal(nodes.eventPrompt.value, 'v14');
  assert.equal(nodes.opportunityPrompt.value, 'v14');
  assert.equal(nodes.gameplayPrompt.value, 'v15');
  assert.equal(nodes.generationPromptPreviewVersion.value,'v2');
  assert.match(nodes.generationPromptActive.textContent,/生成游戏 v2/);
  assert.equal(nodes.generationPromptPreview.children.length,2);
  assert.equal(nodes.generationPromptPreview.children[0].children[1].textContent,'v2 generate 全文');
  nodes.generationPromptPreviewVersion.value='v1';nodes.generationPromptPreviewVersion.onchange();
  assert.equal(nodes.generationPromptPreview.children[1].children[1].textContent,'v1 repair 全文');
  assert.match(nodes.generationPromptActive.textContent,/生成游戏 v2/,'preview selection never changes runtime version');
  assert.match(nodes.promptPreview.children.map(child => child.textContent).join('\n'), /长视频分段识别/);
  assert.match(nodes.promptPreview.children.map(child => child.textContent).join('\n'), /v14 机会/);
  assert.match(nodes.promptPreview.children.map(child => child.textContent).join('\n'), /推荐玩法/);
  nodes.opportunityPrompt.value = 'v13';
  nodes.opportunityPrompt.onchange();
  assert.match(nodes.promptPreview.children.map(child => child.textContent).join('\n'), /寻找参与机会/);
  assert.equal(nodes.eventPrompt.value, 'v14');
  nodes.sample.value = 'S05_传球投篮.mp4';
  await nodes.sample.onchange();
  assert.equal(nodes.recognize.disabled, false);
  await nodes.recognize.onclick();
  assert.equal(nodes.tokenUsage.children.length, 3, 'stage usage remains visible after a call');
  assert.match(requestedURL, /^\/events\?/);
  assert.match(requestedURL, /version=v14/);
  assert.match(requestedURL, /model=qwen3-vl-plus/);
  assert.match(requestedURL, /mode=direct/);
  assert.match(requestedURL, /fps=2/);
  assert.equal(nodes.events.children.length, 2);
  nodes.events.children[0].children[1].click();
  assert.equal(nodes.video.currentTime, 1.5);
  nodes.events.children[1].children[0].checked = false;
  nodes.events.children[1].children[0].onchange();
  await nodes.suggest.onclick();
  assert.match(requestedURL, /selected=e1(?:&|$)/);
  assert.match(requestedURL, /version=v13(?:&|$)/);
  assert.equal(nodes.points.children.length, 2);
  assert.ok(nodes.points.children.find(child=>child.className==='validation-warnings'));
  assert.match(nodes.status.textContent,/保留 1 条/);
  const pointCard = nodes.points.children.find(child=>child.className==='event');
  const time = pointCard.children[0].children.find(child => child.className === 'time');
  const seek = pointCard.children.find(child => child.className === 'seek');
  seek.click();
  assert.equal(nodes.video.currentTime, 1.5);
  time.value = '2.25';
  seek.click();
  assert.equal(nodes.video.currentTime, 2.25);
  time.value = '99';
  seek.click();
  assert.equal(nodes.video.currentTime, 12);
  time.value = '2.25';
  assert.match(nodes.catalogNote.children.map(child => child.textContent).join(''), /剧情选择/);
  assert.equal(nodes.recommend.disabled, false);
  await nodes.recommend.onclick();
  assert.match(requestedURL, /\/gameplay\?/);
  assert.match(requestedURL, /selected=p1(?:&|$)/);
  assert.match(requestedURL, /version=v15(?:&|$)/);
  const gameCard = nodes.gameplay.children.find(child => child.className === 'event');
  const state = gameCard.children.find(child => child.className === 'choice-state');
  gameCard.children.find(child => child.className === 'accept').click();
  assert.match(state.textContent, /已接受/);
  const game = gameCard.children.find(child => child.className === 'game');
  game.value = 'timing';
  game.onchange();
  assert.match(state.textContent, /已修改/);
  gameCard.children.find(child => child.className === 'discard').click();
  assert.match(state.textContent, /已放弃/);
  gameCard.children.find(child => child.className === 'seek').click();
  assert.equal(nodes.video.currentTime, 2.25);
  await nodes.saveBrief.onclick();
  assert.equal(briefBody.selections[0].status, 'discarded');
  assert.equal(briefBody.selections[0].adjusted_time_s, 2.25);
  assert.match(nodes.status.textContent, /未调用模型/);
  gameplayRecommendations = [];
  await nodes.recommend.onclick();
  assert.equal(nodes.gameplay.children.find(child => child.className === 'empty-recommendation').textContent, '本次未推荐玩法。');
  assert.match(nodes.gameplay.children.find(child => child.className === 'event').children.find(child => child.className === 'fit').textContent, /自行选择/);
  partialGameplay=true;await nodes.recommend.onclick();assert.match(nodes.status.textContent,/1 个未完成/);partialGameplay=false;
  nodes.events.children[0].children[0].checked = false;
  nodes.events.children[0].children[0].onchange();
  assert.equal(nodes.suggest.disabled, true);
  nodes.model.value = 'qwen3.8-flash';
  nodes.mode.value = 'frames';
  nodes.fps.value = '1';
  nodes.sample.value = 'S05_传球投篮.mp4';
  nodes.summary.textContent = '已有结果';
  nodes.reset.click();
  assert.equal(nodes.sample.value, '');
  assert.equal(nodes.model.value, 'qwen3-vl-plus');
  assert.equal(nodes.mode.value, 'direct');
  assert.equal(nodes.fps.value, '2');
  assert.equal(nodes.eventPrompt.value, 'v14');
  assert.equal(nodes.summary.textContent, '');
  assert.match(nodes.status.textContent, /初始状态/);
  nodes.sample.value='S05_传球投篮.mp4';
  await nodes.sample.onchange();
  failMedia=true;
  await nodes.recognize.onclick();
  assert.equal(nodes.summary.textContent,'视频预处理失败，分析未完成。');
  assert.match(nodes.status.textContent,/8987/);
  assert.equal(nodes.suggest.disabled,true);
  failMedia=false;partialFailure=true;
  await nodes.recognize.onclick();
  assert.match(nodes.summary.textContent,/仅已完成片段/);
  assert.equal(nodes.suggest.disabled,false,'completed events remain usable for step two');
  assert.match(nodes.reviewHint.textContent,/全片未完成/);
  const partialEvent=nodes.events.children.find(child=>child.className==='event');
  assert.equal(partialEvent.children[0].disabled,false);
  assert.equal(partialEvent.children[0].checked,true);
  partialEvent.children[1].click();assert.equal(nodes.video.currentTime,1.5,'partial events remain seekable');
  const diagnostic=nodes.events.children.find(child=>child.className==='validation-warnings');
  assert.ok(diagnostic.children.some(child=>child.textContent.includes('尚未处理')));
  assert.match(nodes.status.textContent,/超出片长/);
  await nodes.suggest.onclick();assert.equal(nodes.recommend.disabled,false,'partial input can produce selectable opportunities');
  console.log('UI point seek and empty state OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
