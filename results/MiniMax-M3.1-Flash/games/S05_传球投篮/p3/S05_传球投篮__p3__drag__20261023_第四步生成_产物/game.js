function mount(container, api) {
  if (!container || !api || typeof api.finish !== 'function') {
    if (api && typeof api.reportError === 'function') api.reportError('挂载接球稳位小游戏失败：缺少有效容器或 api.finish。');
    return { destroy() {} };
  }

  const TOTAL_MS = 8000;
  const root = document.createElement('div');
  root.className = 'sg-root is-ready';
  root.setAttribute('role', 'application');
  root.setAttribute('aria-label', '接球稳位小游戏');

  const hud = document.createElement('div');
  hud.className = 'sg-hud';
  const titleWrap = document.createElement('div');
  titleWrap.className = 'sg-title-wrap';
  const title = document.createElement('div');
  title.className = 'sg-title';
  title.textContent = '接球稳位';
  const stateText = document.createElement('div');
  stateText.className = 'sg-state';
  stateText.textContent = '按住篮球，拖向左上方的绿色目标';
  titleWrap.appendChild(title);
  titleWrap.appendChild(stateText);
  const timer = document.createElement('div');
  timer.className = 'sg-timer';
  timer.textContent = '8.0s';
  hud.appendChild(titleWrap);
  hud.appendChild(timer);

  const stage = document.createElement('div');
  stage.className = 'sg-stage';

  const figure = document.createElement('div');
  figure.className = 'sg-figure';
  figure.setAttribute('aria-hidden', 'true');
  const head = document.createElement('span');
  head.className = 'sg-head';
  const torso = document.createElement('span');
  torso.className = 'sg-torso';
  const armA = document.createElement('span');
  armA.className = 'sg-arm sg-arm-a';
  const armB = document.createElement('span');
  armB.className = 'sg-arm sg-arm-b';
  const legs = document.createElement('span');
  legs.className = 'sg-legs';
  figure.appendChild(head);
  figure.appendChild(torso);
  figure.appendChild(armA);
  figure.appendChild(armB);
  figure.appendChild(legs);

  const target = document.createElement('div');
  target.className = 'sg-target';
  target.style.left = '45%';
  target.style.top = '30%';
  const targetLabel = document.createElement('span');
  targetLabel.className = 'sg-target-label';
  targetLabel.textContent = '右肩前';
  target.appendChild(targetLabel);

  const guide = document.createElement('div');
  guide.className = 'sg-guide';
  guide.setAttribute('aria-hidden', 'true');

  const ball = document.createElement('div');
  ball.className = 'sg-ball';
  ball.style.left = '58%';
  ball.style.top = '44%';
  ball.setAttribute('aria-label', '可拖拽篮球标记');

  stage.appendChild(figure);
  stage.appendChild(guide);
  stage.appendChild(target);
  stage.appendChild(ball);

  const footer = document.createElement('div');
  footer.className = 'sg-footer';
  const instruction = document.createElement('div');
  instruction.className = 'sg-instruction';
  instruction.textContent = '拖动橙色篮球，让球心落入绿色椭圆';
  const result = document.createElement('div');
  result.className = 'sg-result';
  result.setAttribute('aria-live', 'polite');
  result.textContent = '准备中';
  footer.appendChild(instruction);
  footer.appendChild(result);

  root.appendChild(hud);
  root.appendChild(stage);
  root.appendChild(footer);
  container.appendChild(root);

  let state = 'ready';
  let finished = false;
  let destroyed = false;
  let activePointerId = null;
  let moved = false;
  let startX = 0;
  let startY = 0;
  let deadline = Date.now() + TOTAL_MS;
  let timeoutId = 0;
  let intervalId = 0;
  let audioContext = null;
  const bindings = [];

  function bind(node, type, handler, options) {
    node.addEventListener(type, handler, options);
    bindings.push([node, type, handler, options]);
  }

  function setBallPosition(clientX, clientY) {
    const rect = stage.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const radius = ball.offsetWidth * 0.5;
    const x = Math.max(radius + 4, Math.min(rect.width - radius - 4, clientX - rect.left));
    const y = Math.max(radius + 4, Math.min(rect.height - radius - 4, clientY - rect.top));
    ball.style.left = ((x / rect.width) * 100).toFixed(3) + '%';
    ball.style.top = ((y / rect.height) * 100).toFixed(3) + '%';
  }

  function updateTimer() {
    if (finished) return;
    const remaining = Math.max(0, deadline - Date.now());
    timer.textContent = (remaining / 1000).toFixed(1) + 's';
    timer.classList.toggle('is-urgent', remaining <= 2000);
    if (remaining <= 0) fail('操作超时，未能在 8 秒内完成持球稳位。');
  }

  function releaseActivePointer() {
    if (activePointerId !== null) {
      try {
        if (ball.hasPointerCapture(activePointerId)) ball.releasePointerCapture(activePointerId);
      } catch (error) {
        if (api && typeof api.reportError === 'function') api.reportError('释放拖拽指针失败：' + String(error));
      }
      activePointerId = null;
    }
    ball.classList.remove('is-dragging');
  }

  function playFailureTone() {
    const AudioCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtor) return;
    try {
      if (!audioContext) audioContext = new AudioCtor();
      if (audioContext.state === 'suspended') audioContext.resume().catch(() => {});
      const now = audioContext.currentTime;
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      oscillator.type = 'triangle';
      oscillator.frequency.setValueAtTime(155, now);
      oscillator.frequency.exponentialRampToValueAtTime(105, now + 0.18);
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.045, now + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.2);
      oscillator.connect(gain);
      gain.connect(audioContext.destination);
      oscillator.start(now);
      oscillator.stop(now + 0.2);
    } catch (error) {
      // 音频为可选反馈，不影响玩法判定。
    }
  }

  function endGame(outcome, message) {
    if (finished || destroyed) return;
    finished = true;
    state = outcome;
    releaseActivePointer();
    window.clearTimeout(timeoutId);
    window.clearInterval(intervalId);
    root.classList.remove('is-ready', 'is-running');
    root.classList.add(outcome === 'success' ? 'is-success' : 'is-failure');
    if (outcome === 'success') {
      stateText.textContent = '稳住！已进入标准持球预备位';
      result.textContent = '成功';
      result.className = 'sg-result is-success';
    } else {
      stateText.textContent = message;
      result.textContent = '未稳住';
      result.className = 'sg-result is-failure';
      playFailureTone();
    }
    try {
      api.finish({ result: outcome, message: message });
    } catch (error) {
      if (typeof api.reportError === 'function') api.reportError('结束接球稳位小游戏失败：' + String(error));
    }
  }

  function fail(message) {
    if (finished) return;
    endGame('failure', message);
  }

  function onPointerDown(event) {
    if (finished || (state !== 'ready' && state !== 'running')) return;
    if (event.isPrimary === false) return;
    if (typeof event.button === 'number' && event.button !== 0) return;
    event.preventDefault();
    if (activePointerId !== null) return;
    if (state === 'ready') {
      state = 'running';
      root.classList.remove('is-ready');
      root.classList.add('is-running');
      stateText.textContent = '微调球心位置，松开完成判定';
      result.textContent = '拖拽中';
      result.className = 'sg-result';
    }
    activePointerId = event.pointerId;
    moved = false;
    startX = event.clientX;
    startY = event.clientY;
    ball.classList.add('is-dragging');
    try {
      ball.setPointerCapture(event.pointerId);
    } catch (error) {
      if (typeof api.reportError === 'function') api.reportError('无法捕获拖拽指针：' + String(error));
      fail('当前设备无法捕获拖拽操作。');
    }
  }

  function onPointerMove(event) {
    if (finished || state !== 'running' || event.pointerId !== activePointerId) return;
    event.preventDefault();
    const distance = Math.hypot(event.clientX - startX, event.clientY - startY);
    if (distance >= 4) moved = true;
    setBallPosition(event.clientX, event.clientY);
  }

  function onPointerUp(event) {
    if (finished || state !== 'running' || event.pointerId !== activePointerId) return;
    event.preventDefault();
    releaseActivePointer();
    if (!moved) {
      fail('操作过早：尚未移动篮球便已松开。');
      return;
    }
    const ballRect = ball.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const ballCenterX = ballRect.left + ballRect.width / 2;
    const ballCenterY = ballRect.top + ballRect.height / 2;
    const targetCenterX = targetRect.left + targetRect.width / 2;
    const targetCenterY = targetRect.top + targetRect.height / 2;
    const halfWidth = targetRect.width / 2;
    const halfHeight = targetRect.height / 2;
    if (halfWidth <= 0 || halfHeight <= 0) {
      fail('目标区域尺寸异常，无法完成判定。');
      return;
    }
    const dx = (ballCenterX - targetCenterX) / halfWidth;
    const dy = (ballCenterY - targetCenterY) / halfHeight;
    const distanceSquared = dx * dx + dy * dy;
    if (distanceSquared <= 1 + 1e-6) {
      endGame('success', '稳住！篮球已进入右肩前方的标准持球区域。');
    } else {
      let reason = '球心尚未进入绿色目标区域。';
      if (dy > 0.2) reason = '球位偏低，需要向左上方调整。';
      else if (Math.abs(dx) > 0.3) reason = '球位左右偏离，需要重新对准目标。';
      fail(reason + ' 本次稳位失败。');
    }
  }

  function onPointerCancel(event) {
    if (event.pointerId !== activePointerId) return;
    releaseActivePointer();
    fail('拖拽操作被取消，未能完成持球稳位。');
  }

  bind(ball, 'pointerdown', onPointerDown, { passive: false });
  bind(ball, 'pointermove', onPointerMove, { passive: false });
  bind(ball, 'pointerup', onPointerUp, { passive: false });
  bind(ball, 'pointercancel', onPointerCancel, { passive: false });

  timeoutId = window.setTimeout(() => fail('操作超时，未能在 8 秒内完成持球稳位。'), TOTAL_MS);
  intervalId = window.setInterval(updateTimer, 100);
  updateTimer();

  return {
    destroy() {
      if (destroyed) return;
      destroyed = true;
      finished = true;
      window.clearTimeout(timeoutId);
      window.clearInterval(intervalId);
      releaseActivePointer();
      for (const item of bindings) {
        item[0].removeEventListener(item[1], item[2], item[3]);
      }
      bindings.length = 0;
      if (audioContext) {
        const closing = audioContext.close();
        if (closing && typeof closing.catch === 'function') closing.catch(() => {});
        audioContext = null;
      }
      if (root.parentNode === container) container.removeChild(root);
    }
  };
}