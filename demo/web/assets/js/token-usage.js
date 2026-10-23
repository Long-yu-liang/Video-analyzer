/* Display provider-reported usage only. Never infer money or missing counts. */
(function(root) {
  'use strict';
  const count = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
  const first = (...values) => values.map(count).find(value => value !== null) ?? null;
  const format = value => value === null ? '未返回' : value.toLocaleString('zh-CN');
  function read(record) {
    if (!record) return null;
    const usage = record.usage || {};
    // Segment records are the source of truth; parent sums can turn missing fields into zero.
    if (Array.isArray(record.segments) && record.segments.length) {
      const parts = record.segments.map(read);
      const sum = key => parts.every(part => part?.[key] !== null && part?.[key] !== undefined)
        ? parts.reduce((total, part) => total + part[key], 0) : null;
      return {input: sum('input'), output: sum('output'), total: sum('total'),
        details: [], segments: parts.length, partial: parts.some(part => part?.input === null || part?.output === null || part?.total === null)};
    }
    const inputDetails = usage.prompt_tokens_details || usage.input_tokens_details || {};
    const outputDetails = usage.completion_tokens_details || usage.output_tokens_details || {};
    const details = [
      ['图像', inputDetails.image_tokens], ['视频', inputDetails.video_tokens],
      ['音频输入', inputDetails.audio_tokens], ['缓存命中', inputDetails.cached_tokens],
      ['推理', outputDetails.reasoning_tokens], ['音频输出', outputDetails.audio_tokens],
    ].filter(([, value]) => count(value) !== null);
    return {input: first(usage.prompt_tokens, usage.input_tokens, usage.promptTokenCount),
      output: first(usage.completion_tokens, usage.output_tokens, usage.candidatesTokenCount),
      total: first(usage.total_tokens, usage.totalTokenCount), details};
  }
  function render(host, record, label = '本次调用') {
    if (!host) return;
    const node = (tag, text) => {const element = document.createElement(tag); if(text !== undefined) element.textContent = text; return element;};
    const value = read(record);
    const box = node('div'); box.className = 'token-usage-row';
    box.append(node('strong', label));
    if (!record) box.append(node('span', '暂无调用记录'));
    else {
      box.append(node('span', `输入 ${format(value.input)} · 输出 ${format(value.output)} · 总计 ${format(value.total)} Token`));
      box.append(node('small', `${record.model || '模型未记录'} · ${record.status || '状态未记录'}${value.segments ? ` · ${value.segments} 个片段请求${value.partial ? '（用量未完整返回）' : ''}` : ''}`));
      if (value.details.length) box.append(node('small', value.details.map(([name, tokens]) => `${name} ${format(tokens)}`).join(' · ') + '（明细不额外累加）'));
      if (record.model_call_made === false) box.append(node('small', '此记录未调用模型；不代表一次新消耗。'));
      const detail = node('details'); detail.append(node('summary', '查看原始 usage'));
      detail.append(node('pre', JSON.stringify(record.segments?.length
        ? record.segments.map(item => ({status: item.status, usage: item.usage ?? null})) : record.usage ?? null, null, 2)));
      box.append(detail);
    }
    host.append(box);
  }
  root.TokenUsage = {read, render};
})(typeof window === 'undefined' ? globalThis : window);
