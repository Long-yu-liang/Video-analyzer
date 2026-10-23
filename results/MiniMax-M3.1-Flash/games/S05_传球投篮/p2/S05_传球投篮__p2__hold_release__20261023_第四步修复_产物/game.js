function mount(container, api) {
  const root = document.createElement('div');
  root.className = 'game-root';
  root.setAttribute('role', 'application');
  root.setAttribute('aria-label', '蓄力投篮练习');
  container.appendChild(root);

  const header = document.createElement('div');
  header.className = 'game-header';
  const title = document.createElement('strong');
  title.textContent = '投篮出手';
  const status = document.createElement('span');
  status.textContent = '按住屏幕蓄力';
  header.appendChild(title);
  header.appendChild(status);

  const stage = document.createElement('div');
  stage.className = 'stage';
  stage.setAttribute('aria-label', '按住蓄力并在理想区间松开');

  const courtLine = document.createElement('div');
  courtLine.className = 'court-line';
  stage.appendChild(courtLine);

  const hoop = document.createElement('div');
  hoop.className = 'hoop';
  hoop.setAttribute('aria-label', '自绘篮筐目标');
  stage.appendChild(hoop);

  const ball = document.createElement('div');
  ball.className = 'ball';
  ball.setAttribute('aria-label', '自绘篮球');
  stage.appendChild(ball);

  const stageHint = document.createElement('div');
  stageHint.className = 'stage-hint';
  stageHint.textContent = '按住开始蓄力';
  stage.appendChild(stageHint);

  const panel = document.createElement('div');
  panel.className = 'control-panel';

  const meter = document.createElement('div');
  meter.className = 'meter';
  meter.setAttribute('aria-label', '蓄力力量与理想释放区间');
  const meterFill = document.createElement('div');
  meterFill.className = 'meter-fill';
  const idealBand = document.createElement('div');
  idealBand.className = 'ideal-band';
  idealBand.setAttribute('aria-label', '理想释放区间');
  meter.appendChild(meterFill);
  meter.appendChild(idealBand);

  const meterLabels = document.createElement('div');
  meterLabels.className = 'meter-labels';
  const earlyLabel = document.createElement('span');
  earlyLabel.textContent = '偏早';
  const idealLabel = document.createElement('span');
  idealLabel.textContent = '理想区';
  const lateLabel = document.createElement('span');
  lateLabel.textContent = '偏晚';
  meterLabels.appendChild(earlyLabel);
  meterLabels.appendChild(idealLabel);
  meterLabels.appendChild(lateLabel);

  const readouts = document.createElement('div');
  readouts.className = 'readouts';
  const heightReadout = document.createElement('div');
  heightReadout.className = 'readout';
  const heightName = document.createElement('span');
  heightName.textContent = '举球高度';
  const heightValue = document.createElement('b');
  heightValue.textContent = '40.0%';
  heightReadout.appendChild(heightName);
  heightReadout.appendChild(heightValue);
  const angleReadout = document.createElement('div');
  angleReadout.className = 'readout';
  const angleName = document.createElement('span');
  angleName.textContent = '手腕角度';
  const angleValue = document.createElement('b');
  angleValue.textContent = '60.0°';
  angleReadout.appendChild(angleName);
  angleReadout.appendChild(angleValue);
  readouts.appendChild(heightReadout);
  readouts.appendChild(angleReadout);

  const instruction = document.createElement('p');
  instruction.className = 'instruction';
  instruction.textContent = '力量进入绿色区域后松开即可稳定出手。';
  panel.appendChild(meter);
  panel.appendChild(meterLabels);
  panel.appendChild(readouts);
  panel.appendChild(instruction);

  root.appendChild(header);
  root.appendChild(stage);
  root.appendChild(panel);

  let state = 'prepare';
  let pointerId = null;
  let startedAt = 0;
  let animationFrame = 0;
  let timeoutTimer = 0;
  let finishCalled = false;
  let destroyed = false;

  function report(error) {
    if (api && typeof api.reportError === 'function') {
      api.reportError(String(error && error.message ? error.message : error));
    }
  }

  function setStatus(text) {
    status.textContent = text;
    stageHint.textContent = text;
  }

  function updateMeter(elapsed) {
    const power = Math.max(0, Math.min(100, elapsed / 1.25 * 100));
    const height = 40 + power * 0.60;
    const angle = 60 + power * 0.35;
    meterFill.style.width = power.toFixed(2) + '%';
    heightValue.textContent = height.toFixed(1) + '%';
    angleValue.textContent = angle.toFixed(1) + '°';
    meterFill.classList.toggle('in-ideal', power >= 68 && power <= 92);
  }

  function animate() {
    if (destroyed || state !== 'running') return;
    updateMeter((performance.now() - startedAt) / 1000);
    animationFrame = requestAnimationFrame(animate);
  }

  function finish(result, message, shotClass) {
    if (finishCalled || destroyed) return;
    finishCalled = true;
    state = result === 'success' ? 'success' : 'failure';
    pointerId = null;
    cancelAnimationFrame(animationFrame);
    setStatus(result === 'success' ? '投篮成功' : '投篮失败');
    instruction.textContent = message;
    if (shotClass) ball.classList.add(shotClass);
    if (result === 'success') hoop.classList.add('target-hit');
    try {
      if (!api || typeof api.finish !== 'function') {
        throw new Error('缺少 api.finish 方法');
      }
      api.finish({result: result, message: message});
    } catch (error) {
      report(error);
    }
  }

  function release(event) {
    if (state !== 'running' || pointerId === null || event.pointerId !== pointerId) return;
    event.preventDefault();
    const elapsed = (performance.now() - startedAt) / 1000;
    const releasedPointer = pointerId;
    pointerId = null;
    cancelAnimationFrame(animationFrame);
    updateMeter(elapsed);
    try {
      stage.releasePointerCapture(releasedPointer);
    } catch (error) {}
    if (elapsed < 0.85) {
      finish('failure', '力量不足，弧线偏低！', 'shot-early');
    } else if (elapsed > 1.15) {
      finish('failure', '出手过晚，手腕过度后仰！', 'shot-late');
    } else {
      finish('success', '力量适中，出手稳定！篮球命中目标！', 'shot-success');
    }
  }

  function cancel(event) {
    if (state !== 'running' || pointerId === null || event.pointerId !== pointerId) return;
    event.preventDefault();
    pointerId = null;
    cancelAnimationFrame(animationFrame);
    finish('failure', '操作被取消：投篮失败', 'shot-cancel');
  }

  function begin(event) {
    if (state !== 'prepare' || typeof event.pointerId !== 'number') return;
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    event.preventDefault();
    pointerId = event.pointerId;
    state = 'running';
    startedAt = performance.now();
    setStatus('保持按住，松开出手');
    instruction.textContent = '蓄力中：力量进入绿色区域后松开。';
    updateMeter(0);
    try {
      stage.setPointerCapture(pointerId);
    } catch (error) {}
    animationFrame = requestAnimationFrame(animate);
  }

  function preventContextMenu(event) {
    event.preventDefault();
  }

  stage.addEventListener('pointerdown', begin);
  stage.addEventListener('pointerup', release);
  stage.addEventListener('pointercancel', cancel);
  stage.addEventListener('contextmenu', preventContextMenu);

  timeoutTimer = setTimeout(function timeout() {
    if (state === 'prepare' || state === 'running') {
      pointerId = null;
      cancelAnimationFrame(animationFrame);
      finish('failure', '未在时限内完成出手：投篮失败', 'shot-timeout');
    }
  }, 8000);

  function destroy() {
    if (destroyed) return;
    destroyed = true;
    clearTimeout(timeoutTimer);
    cancelAnimationFrame(animationFrame);
    stage.removeEventListener('pointerdown', begin);
    stage.removeEventListener('pointerup', release);
    stage.removeEventListener('pointercancel', cancel);
    stage.removeEventListener('contextmenu', preventContextMenu);
    if (root.parentNode) root.parentNode.removeChild(root);
  }

  return {destroy: destroy};
}