function mount(container, api) {
  const doc = container.ownerDocument || document;
  const view = doc.defaultView || window;
  const root = doc.createElement('div');
  root.className = 'mash-root';

  const hud = doc.createElement('div');
  hud.className = 'mash-hud';
  const title = doc.createElement('div');
  title.className = 'mash-title';
  title.textContent = '起跑专注';
  const stats = doc.createElement('div');
  stats.className = 'mash-stats';
  stats.setAttribute('aria-live', 'polite');
  hud.append(title, stats);

  const stage = doc.createElement('div');
  stage.className = 'mash-stage';
  const ringWrap = doc.createElement('div');
  ringWrap.className = 'mash-ring-wrap';
  const ring = doc.createElement('div');
  ring.className = 'mash-ring inhale';
  const ringCore = doc.createElement('div');
  ringCore.className = 'mash-ring-core';
  ringCore.textContent = '预备';
  ring.append(ringCore);
  const phaseText = doc.createElement('div');
  phaseText.className = 'mash-phase';
  phaseText.textContent = '准备开始';
  const cycleText = doc.createElement('div');
  cycleText.className = 'mash-cycle';
  cycleText.textContent = '第 1 / 5 轮';
  ringWrap.append(ring, phaseText, cycleText);

  const meterBox = doc.createElement('div');
  meterBox.className = 'mash-meter-box';
  const meterLabel = doc.createElement('div');
  meterLabel.className = 'mash-meter-label';
  meterLabel.textContent = '专注度';
  const meterTrack = doc.createElement('div');
  meterTrack.className = 'mash-meter-track';
  const meterFill = doc.createElement('div');
  meterFill.className = 'mash-meter-fill';
  meterTrack.append(meterFill);
  meterBox.append(meterLabel, meterTrack);
  stage.append(ringWrap, meterBox);

  const pad = doc.createElement('div');
  pad.className = 'mash-pad';
  pad.setAttribute('role', 'button');
  pad.setAttribute('aria-label', '呼气阶段连点区域');
  pad.setAttribute('tabindex', '0');
  const padTitle = doc.createElement('strong');
  padTitle.textContent = '呼气时连点';
  const padHint = doc.createElement('span');
  padHint.textContent = '每轮点击 2–3 次';
  pad.append(padTitle, padHint);

  const status = doc.createElement('div');
  status.className = 'mash-status';
  status.setAttribute('aria-live', 'assertive');
  status.textContent = '准备阶段，请等待呼吸提示。';

  const result = doc.createElement('div');
  result.className = 'mash-result';
  result.hidden = true;

  root.append(hud, stage, pad, status, result);
  container.appendChild(root);

  const PREP_MS = 800;
  const DURATION_MS = 6000;
  const CYCLE_MS = 1200;
  const INHALE_MS = 300;
  const CYCLES = 5;
  const MAX_PER_CYCLE = 3;
  const MIN_PER_CYCLE = 2;
  const MAX_TOTAL = CYCLES * MAX_PER_CYCLE;
  const counts = new Array(CYCLES).fill(0);

  let state = 'ready';
  let startAt = 0;
  let prepEndAt = 0;
  let rafId = 0;
  let prepTimer = 0;
  let nextCycleToEvaluate = 0;
  let activePointer = null;
  let totalClicks = 0;
  let lastPhaseKey = '';
  let finished = false;
  let destroyed = false;

  function reportError(error) {
    if (api && typeof api.reportError === 'function') {
      api.reportError(error && error.message ? error.message : String(error));
    }
  }

  function setStatus(message, tone) {
    status.textContent = message;
    status.className = 'mash-status' + (tone ? ' ' + tone : '');
  }

  function updateMeter() {
    const value = Math.min(100, totalClicks / MAX_TOTAL * 100);
    meterFill.style.width = value.toFixed(2) + '%';
  }

  function updateReady(now) {
    if (destroyed || state !== 'ready') return;
    const remain = Math.max(0, prepEndAt - now);
    phaseText.textContent = '预备 · ' + (remain / 1000).toFixed(1) + ' 秒';
    stats.textContent = '正式挑战 6 秒 · 共 5 轮';
    rafId = view.requestAnimationFrame(updateReady);
  }

  function updateRunning(now) {
    if (destroyed || state !== 'running') return;
    try {
      const elapsed = Math.max(0, now - startAt);
      const completedByTime = Math.min(CYCLES, Math.floor(elapsed / CYCLE_MS));

      while (nextCycleToEvaluate < completedByTime) {
        const index = nextCycleToEvaluate;
        const count = counts[index];
        if (count < MIN_PER_CYCLE) {
          finishFailure('准备度下降：第' + (index + 1) + '轮需要2–3次点击。');
          return;
        }
        nextCycleToEvaluate += 1;
      }

      if (elapsed >= DURATION_MS) {
        if (nextCycleToEvaluate < CYCLES) {
          const index = nextCycleToEvaluate;
          if (counts[index] < MIN_PER_CYCLE) {
            finishFailure('准备度下降：第' + (index + 1) + '轮需要2–3次点击。');
            return;
          }
          nextCycleToEvaluate += 1;
        }
        finishSuccess();
        return;
      }

      const cycle = Math.min(CYCLES - 1, Math.floor(elapsed / CYCLE_MS));
      const phaseOffset = elapsed - cycle * CYCLE_MS;
      const phase = phaseOffset < INHALE_MS ? 'inhale' : 'exhale';
      const phaseKey = phase + ':' + cycle;

      if (phaseKey !== lastPhaseKey) {
        lastPhaseKey = phaseKey;
        ring.classList.remove('inhale', 'exhale');
        ring.classList.add(phase);
        if (phase === 'inhale') {
          ringCore.textContent = '吸气';
          phaseText.textContent = '吸气 · 暂不点击';
          setStatus('保持吸气，等圆环变大再点击。');
        } else {
          ringCore.textContent = '呼气';
          phaseText.textContent = '呼气 · 连点 2–3 次';
          setStatus('现在有0.9秒，请连点2–3次！', 'good');
        }
      }

      cycleText.textContent = '第 ' + (cycle + 1) + ' / ' + CYCLES + ' 轮';
      stats.textContent = '本轮 ' + counts[cycle] + ' 次 · 累计 ' + totalClicks + ' 次';
      updateMeter();
      rafId = view.requestAnimationFrame(updateRunning);
    } catch (error) {
      reportError(error);
      finishFailure('小游戏运行异常，已中止本次挑战。');
    }
  }

  function beginChallenge() {
    if (destroyed || state !== 'ready') return;
    state = 'running';
    startAt = view.performance.now();
    nextCycleToEvaluate = 0;
    lastPhaseKey = '';
    ringCore.textContent = '吸气';
    phaseText.textContent = '吸气 · 暂不点击';
    cycleText.textContent = '第 1 / 5 轮';
    setStatus('保持吸气，等圆环变大再点击。');
    rafId = view.requestAnimationFrame(updateRunning);
  }

  function onPointerDown(event) {
    if (destroyed || finished) return;
    try {
      if (state === 'ready') {
        event.preventDefault();
        setStatus('还在准备，请等待预备倒计时结束。', 'warn');
        return;
      }
      if (state !== 'running') return;
      event.preventDefault();
      if (activePointer !== null) {
        setStatus('请使用单指操作；额外的按下不会计次。', 'warn');
        return;
      }

      activePointer = event.pointerId;
      try {
        pad.setPointerCapture(event.pointerId);
      } catch (ignore) {}

      const elapsed = view.performance.now() - startAt;
      if (elapsed < 0 || elapsed >= DURATION_MS) {
        setStatus('本次操作已超时，不计次。', 'warn');
        return;
      }

      const cycle = Math.min(CYCLES - 1, Math.floor(elapsed / CYCLE_MS));
      const phaseOffset = elapsed - cycle * CYCLE_MS;
      if (phaseOffset < INHALE_MS) {
        setStatus('现在是吸气阶段，本次不计数。', 'warn');
        return;
      }

      counts[cycle] += 1;
      totalClicks += 1;
      if (counts[cycle] > MAX_PER_CYCLE) {
        finishFailure('节奏紊乱：第' + (cycle + 1) + '轮点击超过3次。');
        return;
      }

      stats.textContent = '本轮 ' + counts[cycle] + ' 次 · 累计 ' + totalClicks + ' 次';
      updateMeter();
      setStatus('已记录 ' + counts[cycle] + '/3 次，继续保持节奏。', 'good');
    } catch (error) {
      reportError(error);
      finishFailure('小游戏运行异常，已中止本次挑战。');
    }
  }

  function onPointerUp(event) {
    if (activePointer === event.pointerId) activePointer = null;
  }

  function onPointerCancel(event) {
    if (activePointer === event.pointerId) activePointer = null;
    if (state === 'running') {
      setStatus('操作已取消，不会补记点击。', 'warn');
    }
  }

  function finishSuccess() {
    if (finished) return;
    finished = true;
    state = 'success';
    root.classList.add('success');
    pad.classList.add('locked');
    ring.classList.remove('inhale', 'exhale');
    ring.classList.add('success-ring');
    ringCore.textContent = '就绪';
    phaseText.textContent = '节奏稳定';
    cycleText.textContent = CYCLES + ' / ' + CYCLES + ' 轮完成';
    meterFill.style.width = '100%';
    stats.textContent = '累计 ' + totalClicks + ' 次有效点击';
    const message = '节奏稳定，启动准备已就绪！';
    status.textContent = message;
    status.className = 'mash-status good';
    result.hidden = false;
    result.className = 'mash-result success-result';
    result.textContent = message;
    try {
      api.finish({ result: 'success', message: message });
    } catch (error) {
      reportError(error);
    }
  }

  function finishFailure(message) {
    if (finished) return;
    finished = true;
    state = 'failure';
    root.classList.add('failure');
    pad.classList.add('locked');
    ring.classList.remove('inhale', 'exhale');
    ring.classList.add('failure-ring');
    ringCore.textContent = '失衡';
    phaseText.textContent = '节奏中断';
    status.textContent = message;
    status.className = 'mash-status bad';
    result.hidden = false;
    result.className = 'mash-result failure-result';
    result.textContent = message;
    try {
      api.finish({ result: 'failure', message: message });
    } catch (error) {
      reportError(error);
    }
  }

  pad.addEventListener('pointerdown', onPointerDown);
  pad.addEventListener('pointerup', onPointerUp);
  pad.addEventListener('pointercancel', onPointerCancel);

  prepEndAt = view.performance.now() + PREP_MS;
  rafId = view.requestAnimationFrame(updateReady);
  prepTimer = view.setTimeout(beginChallenge, PREP_MS);

  return {
    destroy() {
      if (destroyed) return;
      destroyed = true;
      view.clearTimeout(prepTimer);
      view.cancelAnimationFrame(rafId);
      pad.removeEventListener('pointerdown', onPointerDown);
      pad.removeEventListener('pointerup', onPointerUp);
      pad.removeEventListener('pointercancel', onPointerCancel);
      if (root.parentNode) root.parentNode.removeChild(root);
    }
  };
}