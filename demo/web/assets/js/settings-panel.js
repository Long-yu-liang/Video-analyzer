/* Local configuration only. API keys are never stored in browser storage. */
const SettingsPanel = (() => {
  let token, afterSave, revision = 0, dirty = false, loaded = false, saving = false, importedExtras = {};
  const fields = {}, clearChecks = {};
  const groups = [
    ['视频分析', [['BEEROUTE_BASE_URL', 'API 地址'], ['BEEROUTE_API_KEY', 'API Key'], ['BEEROUTE_MODELS', '模型名称（每行一个）']]],
    ['游戏生成', [['MINIMAX_BASE_URL', 'MiniMax API 地址'], ['MINIMAX_API_KEY', 'MiniMax API Key'], ['MINIMAX_MODEL', '生成模型']]],
    ['本地工具', [['FFMPEG_BINARY', 'FFmpeg 路径'], ['FFPROBE_BINARY', 'ffprobe 路径'], ['NODE_BINARY', 'Node.js 路径']]]
  ];
  const byId = id => document.getElementById(id);
  function status(message) { byId('settingsStatus').textContent = message; }
  async function request(path, method = 'GET', body) {
    const response = await fetch(path, {method, headers: {'X-Local-Token': token, 'Content-Type': 'application/json'},
      ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `请求失败 ${response.status}`);
    return result;
  }
  function populate(data) {
    revision = data.revision;
    for (const [key, input] of Object.entries(fields)) {
      const secret = key.endsWith('_API_KEY');
      input.value = secret ? '' : (data.values[key] || '').replace(key === 'BEEROUTE_MODELS' ? /,/g : /$^/, '\n');
      if (secret) {
        input.placeholder = data.configured[key] ? '已配置；留空保留原密钥' : '未配置，请填写';
        clearChecks[key].checked = false;
      }
    }
    dirty = false; loaded = true; importedExtras = {};
  }
  async function load() {
    if (dirty && !confirm('重新读取将丢弃未保存的设置，是否继续？')) return;
    try { populate(await request('/settings')); status('设置保存在本机 config/settings.json，保存后新请求生效。'); }
    catch (error) { status(error.message); }
  }
  async function save() {
    if (saving || !loaded) return;
    saving = true; byId('settingsSave').disabled = true;
    const values = {...importedExtras, ...Object.fromEntries(Object.entries(fields).map(([key, input]) => [key, input.value.trim()]))};
    values.BEEROUTE_MODELS = [...new Set(values.BEEROUTE_MODELS.split(/[,\n]/).map(v => v.trim()).filter(Boolean))].join(',');
    try {
      const data = await request('/settings', 'POST', {revision, values,
        clear_keys: Object.keys(clearChecks).filter(key => clearChecks[key].checked)});
      populate(data);
      status('设置已保存。正在运行的请求不受影响。');
      try { await afterSave(); } catch { status('已保存，但页面模型列表刷新失败，请刷新页面。'); }
    } catch (error) { status(error.message); }
    finally { saving = false; byId('settingsSave').disabled = false; }
  }
  function init(options) {
    token = options.token; afterSave = options.afterSave;
    const tabs = byId('settingsTabs'), body = byId('settingsBody');
    groups.forEach(([title, items], index) => {
      const tab = document.createElement('button'); tab.type = 'button'; tab.textContent = title; tab.className = 'ghost';
      tab.setAttribute('aria-pressed', String(index === 0));
      const panel = document.createElement('section'); panel.hidden = index !== 0;
      tab.onclick = () => {
        [...body.children].forEach((el, i) => { el.hidden = i !== index; });
        [...tabs.children].forEach((el, i) => el.setAttribute('aria-pressed', String(i === index)));
      };
      items.forEach(([key, title]) => {
        const label = document.createElement('label'); label.textContent = title;
        const input = document.createElement(key === 'BEEROUTE_MODELS' ? 'textarea' : 'input');
        input.id = 'setting-' + key; input.name = key;
        const secret = key.endsWith('_API_KEY');
        if (input.tagName === 'INPUT') input.type = secret ? 'password' : 'text';
        input.autocomplete = secret ? 'new-password' : 'off'; input.spellcheck = false;
        input.oninput = () => { dirty = true; }; fields[key] = input;
        label.append(input); panel.append(label);
        if (secret) {
          const clearLabel = document.createElement('label'); clearLabel.className = 'settings-check';
          const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.onchange = () => { dirty = true; };
          clearChecks[key] = checkbox; clearLabel.append(checkbox, '清除已保存的密钥'); panel.append(clearLabel);
        }
      });
      if (index === 2) {
        const hint = document.createElement('p'); hint.textContent = '留空使用 PATH。请只填写可信的本机可执行文件路径，不加引号或命令参数。';
        const check = document.createElement('button'); check.type = 'button'; check.className = 'ghost'; check.textContent = '检查已保存的工具路径（不调用模型）';
        check.onclick = async () => {
          if (dirty) { status('请先保存配置，再检查工具。'); return; }
          check.disabled = true;
          try { const result = await request('/settings/check-tools', 'POST', {});
            status(Object.entries(result).map(([name, info]) => `${name}：${info.ok ? '可用' : '不可用'} · ${info.message}`).join('\n'));
          } catch (error) { status(error.message); } finally { check.disabled = false; }
        };
        panel.append(hint, check);
      }
      tabs.append(tab); body.append(panel);
    });
    byId('openSettings').onclick = open;
    byId('closeSettings').onclick = () => { if (!saving) byId('settingsDialog').close(); };
    byId('settingsDialog').addEventListener('cancel', event => { if (saving) event.preventDefault(); });
    byId('settingsSave').onclick = save; byId('settingsReload').onclick = load;
    byId('settingsExport').onclick = async () => {
      const include = byId('settingsExportSecrets').checked;
      if (include && !confirm('导出文件将包含明文 API Key，请勿分享或提交到 Git。是否继续？')) return;
      try {
        const data = await request('/settings/export' + (include ? '?include_secrets=1' : ''));
        const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
        const link = document.createElement('a'); link.href = url; link.download = 'video-analyzer-settings.json'; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (error) { status(error.message); }
    };
    byId('settingsImport').onchange = async event => {
      const file = event.target.files[0]; if (!file) return;
      try {
        if (!loaded) throw new Error('请先成功读取当前配置。');
        if (file.size > 128000) throw new Error('配置文件过大。');
        const data = JSON.parse(await file.text());
        if (data.app !== 'video-analyzer' || data.schema_version !== 1 || !data.values || typeof data.values !== 'object' || Array.isArray(data.values)) throw new Error('不是兼容的配置文件。');
        const legacy = ['DASHSCOPE_API_KEY', 'DASHSCOPE_BASE_URL', 'GEMINI_API_KEY'];
        if (Object.entries(data.values).some(([key, value]) => (!fields[key] && !legacy.includes(key)) || typeof value !== 'string')) throw new Error('包含未知字段或无效值。');
        if (dirty && !confirm('导入将替换当前设置草稿，是否继续？')) return;
        importedExtras = {};
        for (const [key, value] of Object.entries(data.values)) {
          if (fields[key]) fields[key].value = key === 'BEEROUTE_MODELS' ? value.replace(/,/g, '\n') : value;
          else importedExtras[key] = value;
        }
        for (const check of Object.values(clearChecks)) check.checked = false;
        dirty = true; status('已导入草稿，点击保存后生效；未提供的密钥保持不变。');
      } catch (error) { status(error.message); } finally { event.target.value = ''; }
    };
  }
  async function open() {
    byId('settingsDialog').showModal();
    if (!loaded) await load();
  }
  return {init, open};
})();
