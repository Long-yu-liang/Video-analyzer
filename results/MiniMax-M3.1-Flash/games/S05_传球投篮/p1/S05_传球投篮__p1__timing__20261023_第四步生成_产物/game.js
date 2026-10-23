function mount(container, api) {
  'use strict';
  if (!container || !api || typeof api.finish !== 'function') {
    if (api && typeof api.reportError === 'function') api.reportError('mount 需要有效的 container 与 api.finish');
    throw new Error('Invalid mount arguments');
  }

  const PREPARE_MS = 350;
  const TRAVEL_MS = 2800;
  const TOTAL_MS = 5000;
  const CATCH_START = 0.78;
  const CATCH_END = 0.86;
  const CATCH_MIDDLE = (CATCH_START + CATCH_END) / 2;

  let state = 'prepare';
  let activeStart = 0;
  let rafId = 0;
  let prepareTimer = 0;
  let timeoutTimer = 0;
  let finishTimer = 0;
  let finishCalled = false;
  let destroyed = false;
  let audioContext = null;

  const root = document.createElement('div');
  root.id = 'game';
  root.setAttribute('role', 'application');
  root.setAttribute('aria-label', '精准接球时机小游戏');

  const panel = make('div', 'panel');
  const header = make('div', 'header');
  const title = make('h2', 'title', '精准接球时机');
  const status = make('div', 'status', '准备接球…');
  status.setAttribute('aria-live', 'polite');
  header.append(title, status);

  const trajectory = make('div', 'trajectory');
  trajectory.setAttribute('aria-hidden', 'true');
  const route = make('div', 'route');
  const zone = make('div', 'catch-zone');
  zone.style.left = (CATCH_START * 100) + '%';
  zone.style.width = ((CATCH_END - CATCH_START) * 100) + '%';
  const hands = make('div', 'hands');
  hands.style.left = (CATCH_START * 100) + '%';
  hands.style.width = ((CATCH_END - CATCH_START) * 100) + '%';
  const ball = make('div', 'ball');
  ball.style.left = '0%';
  trajectory.append(route, zone, hands, ball);

  const labels = make('div', 'labels');
  labels.append(make('span', 'ball-label', '来球'), make('span', 'zone-label', '双手合拢区'));
  const hint = make('div', 'hint', '篮球进入绿色区域时，立即合拢双手');
  const button = make('button', 'action', '合拢双手');
  button.type = 'button';
  button.disabled = true;
  const resultText = make('div', 'result', '等待你把握接球时机');
  resultText.setAttribute('aria-live', 'assertive');
  panel.append(header, trajectory, labels, hint, button, resultText);
  root.append(panel);
  container.appendChild(root);

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function reportError(error) {
    if (!api || typeof api.reportError !== 'function') return;
    const message = error && error.message ? error.message : String(error);
    try {
      api.reportError('接球小游戏运行错误：' + message);
    } catch (_) {
      // 宿主未提供可用错误通道时保持静默，避免影响原片。
    }
  }

  function currentProgress(now) {
    if (!activeStart) return 0;
    return Math.max(0, Math.min(1, (now - activeStart) / TRAVEL_MS));
  }

  function playMissSound() {
    try {
      const AudioConstructor = window.AudioContext || window.webkitAudioContext;
      if (!AudioConstructor) return;
      if (!audioContext) audioContext = new AudioConstructor();
      if (audioContext.state === 'suspended' && typeof audioContext.resume === 'function') {
        audioContext.resume().catch(() => {});
      }
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      const now = audioContext.currentTime;
      oscillator.type = 'triangle';
      oscillator.frequency.setValueAtTime(180, now);
      oscillator.frequency.exponentialRampToValueAtTime(90, now + 0.16);
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.08, now + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.18);
      oscillator.connect(gain);
      gain.connect(audioContext.destination);
      oscillator.start(now);
      oscillator.stop(now + 0.19);
    } catch (_) {
      // 音频不可用时保留完整视觉反馈。
    }
  }

  function conclude(result, message, kind, progress) {
    if (state !== 'playing') return;
    state = result;
    button.disabled = true;
    cancelAnimationFrame(rafId);
    clearTimeout(timeoutTimer);

    if (result === 'success') {
      root.classList.add('success');
      ball.style.left = (CATCH_MIDDLE * 100) + '%';
      status.textContent = '接球成功';
    } else {
      root.classList.add('failure', kind);
      ball.style.left = (Math.max(0, Math.min(1, progress)) * 100) + '%';
      status.textContent = '接球失败';
      playMissSound();
    }
    resultText.textContent = message;

    finishTimer = setTimeout(() => {
      if (destroyed || finishCalled) return;
      finishCalled = true;
      state = 'ended';
      try {
        api.finish({ result: result, message: message });
      } catch (error) {
        reportError(error);
      }
    }, 600);
  }

  function resolveTiming() {
    if (state !== 'playing') return;
    const progress = currentProgress(performance.now());
    if (progress < CATCH_START) {
      conclude('failure', '太早了：篮球尚未进入双手范围。', 'miss-early', progress);
    } else if (progress <= CATCH_END) {
      conclude('success', '稳稳接住！双手已合拢。', 'success', progress);
    } else {
      conclude('failure', '太晚了：篮球已经越过接球区。', 'miss-late', progress);
    }
  }

  function frame(now) {
    if (state !== 'playing') return;
    try {
      const progress = currentProgress(now);
      ball.style.left = (progress * 100) + '%';
      if (progress >= 1) {
        trajectory.classList.add('passed');
        status.textContent = '篮球已经飞过接球区';
      } else if (progress > CATCH_END) {
        status.textContent = '即将错过接球区！';
      } else if (progress >= CATCH_START) {
        status.textContent = '现在合拢双手！';
      } else {
        status.textContent = '篮球正在传来';
      }
      rafId = requestAnimationFrame(frame);
    } catch (error) {
      reportError(error);
      conclude('failure', '小游戏发生异常，请重试。', 'error', currentProgress(now));
    }
  }

  function onPointerDown(event) {
    if (event.button !== undefined && event.button !== 0) return;
    event.preventDefault();
    try {
      resolveTiming();
    } catch (error) {
      reportError(error);
    }
  }

  function onPointerCancel(event) {
    event.preventDefault();
    // timing 在 pointerdown 时已经提交；取消事件不能生成第二次判定。
  }

  function onKeyDown(event) {
    if (event.key !== 'Enter' && event.key !== ' ' && event.key !== 'Spacebar') return;
    event.preventDefault();
    if (event.repeat) return;
    try {
      resolveTiming();
    } catch (error) {
      reportError(error);
    }
  }

  button.addEventListener('pointerdown', onPointerDown);
  button.addEventListener('pointercancel', onPointerCancel);
  button.addEventListener('keydown', onKeyDown);

  prepareTimer = setTimeout(() => {
    if (destroyed || state !== 'prepare') return;
    state = 'playing';
    activeStart = performance.now();
    button.disabled = false;
    status.textContent = '篮球正在传来';
    rafId = requestAnimationFrame(frame);
  }, PREPARE_MS);

  timeoutTimer = setTimeout(() => {
    if (destroyed || state !== 'playing') return;
    conclude('failure', '超时：未在接球窗口内合拢双手。', 'timeout', 1);
  }, TOTAL_MS);

  return {
    destroy() {
      if (destroyed) return;
      destroyed = true;
      cancelAnimationFrame(rafId);
      clearTimeout(prepareTimer);
      clearTimeout(timeoutTimer);
      clearTimeout(finishTimer);
      button.removeEventListener('pointerdown', onPointerDown);
      button.removeEventListener('pointercancel', onPointerCancel);
      button.removeEventListener('keydown', onKeyDown);
      if (audioContext && typeof audioContext.close === 'function') {
        audioContext.close().catch(() => {});
      }
      if (root.parentNode === container) container.removeChild(root);
    }
  };
}