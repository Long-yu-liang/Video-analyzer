function mount(container, api) {
  var root = document.createElement('div');
  root.id = 'game';
  root.setAttribute('role', 'application');
  root.setAttribute('aria-label', '蓄力托球');

  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  var hud = make('div', 'hud');
  var title = make('div', 'title', '蓄力托球');
  var goal = make('div', 'goal', '按住蓄力，在合适时机松开');
  var hint = make('div', 'hint', '按住底部按钮开始蓄力');
  hud.appendChild(title);
  hud.appendChild(goal);
  hud.appendChild(hint);

  var scene = make('div', 'scene');
  var player = make('div', 'player');
  var torso = make('div', 'torso');
  var leftArm = make('div', 'arm left-arm');
  var rightArm = make('div', 'arm right-arm');
  var hands = make('div', 'hands');
  var ball = make('div', 'ball', '球');
  hands.appendChild(leftArm);
  hands.appendChild(rightArm);
  player.appendChild(torso);
  player.appendChild(hands);
  player.appendChild(ball);
  scene.appendChild(player);

  var gaugeWrap = make('div', 'gauge-wrap');
  var gaugeLabel = make('div', 'gauge-label', '出手蓄力');
  var gauge = make('div', 'gauge');
  var targetBand = make('div', 'target-band');
  var fill = make('div', 'fill');
  var needle = make('div', 'needle');
  gauge.appendChild(targetBand);
  gauge.appendChild(fill);
  gauge.appendChild(needle);
  var gaugeValue = make('div', 'gauge-value', '0%');
  var scale = make('div', 'scale', '偏弱 0%                 65%—85% 理想区间                 过猛 100%');
  gaugeWrap.appendChild(gaugeLabel);
  gaugeWrap.appendChild(gauge);
  gaugeWrap.appendChild(gaugeValue);
  gaugeWrap.appendChild(scale);
  scene.appendChild(gaugeWrap);

  var controls = make('div', 'controls');
  var pad = make('button', 'charge-pad', '按住蓄力');
  pad.type = 'button';
  controls.appendChild(pad);

  var result = make('div', 'result');
  var resultTitle = make('div', 'result-title');
  var resultText = make('div', 'result-text');
  result.appendChild(resultTitle);
  result.appendChild(resultText);

  root.appendChild(hud);
  root.appendChild(scene);
  root.appendChild(controls);
  root.appendChild(result);
  container.appendChild(root);

  var state = 'ready';
  var activePointer = null;
  var chargeStart = 0;
  var rafId = 0;
  var timeoutId = null;
  var finishCalled = false;
  var CHARGE_DURATION = 2400;
  var TARGET_MIN = 65;
  var TARGET_MAX = 85;

  function setHint(text) {
    hint.textContent = text;
  }

  function updateGauge(percent) {
    var p = Math.max(0, Math.min(100, percent));
    fill.style.width = p + '%';
    needle.style.left = p + '%';
    gaugeValue.textContent = Math.round(p) + '%';
  }

  function animateCharge(now) {
    if (state !== 'playing' || activePointer === null) return;
    var percent = Math.min(100, ((now - chargeStart) / CHARGE_DURATION) * 100);
    updateGauge(percent);
    if (percent < 100) {
      rafId = window.requestAnimationFrame(animateCharge);
    }
  }

  function reportError(message) {
    if (api && typeof api.reportError === 'function') api.reportError(message);
  }

  function finishGame(success, message, kind) {
    if (state === 'success' || state === 'failure' || finishCalled) return;
    state = success ? 'success' : 'failure';
    activePointer = null;
    if (rafId) {
      window.cancelAnimationFrame(rafId);
      rafId = 0;
    }
    if (timeoutId !== null) {
      window.clearTimeout(timeoutId);
      timeoutId = null;
    }
    root.classList.remove('charging');
    root.classList.add(state);
    pad.classList.remove('pressed');
    pad.disabled = true;
    if (kind === 'success') player.classList.add('launch');
    if (kind === 'weak') player.classList.add('weak');
    if (kind === 'over') player.classList.add('over');
    resultTitle.textContent = success ? '力度适中' : (kind === 'weak' ? '力度偏弱' : (kind === 'over' ? '力度过猛' : '蓄力中断'));
    resultText.textContent = message;
    result.classList.add('show');
    if (!finishCalled) {
      finishCalled = true;
      try {
        api.finish({ result: success ? 'success' : 'failure', message: message });
      } catch (error) {
        reportError('结束小游戏时发生错误：' + (error && error.message ? error.message : String(error)));
      }
    }
  }

  function releaseCharge(cancelled) {
    if (state !== 'playing' || activePointer === null) return;
    var elapsed = performance.now() - chargeStart;
    var percent = Math.min(100, (elapsed / CHARGE_DURATION) * 100);
    updateGauge(percent);
    activePointer = null;
    root.classList.remove('charging');
    pad.classList.remove('pressed');
    if (cancelled) {
      finishGame(false, '蓄力被中断，未能完成出手准备。', 'cancel');
    } else if (percent >= TARGET_MIN && percent <= TARGET_MAX) {
      finishGame(true, '力度适中：托球稳定，已完成合适蓄力。', 'success');
    } else if (percent < TARGET_MIN) {
      finishGame(false, '力度偏弱：释放过早，出手基础不足。', 'weak');
    } else {
      finishGame(false, '力度过猛：释放过晚，手臂失去平衡。', 'over');
    }
  }

  function onPointerDown(event) {
    if (state === 'success' || state === 'failure') return;
    if (event.target !== pad && !pad.contains(event.target)) {
      setHint('请按住底部蓄力按钮');
      return;
    }
    if (activePointer !== null) return;
    event.preventDefault();
    activePointer = event.pointerId;
    chargeStart = performance.now();
    state = 'playing';
    root.classList.add('charging');
    pad.classList.add('pressed');
    setHint('保持按住，观察蓄力条进入绿色区间');
    updateGauge(0);
    try { pad.setPointerCapture(event.pointerId); } catch (error) { /* 捕获失败时仍监听根节点释放 */ }
    rafId = window.requestAnimationFrame(animateCharge);
  }

  function onPointerUp(event) {
    if (activePointer === null || event.pointerId !== activePointer) return;
    event.preventDefault();
    releaseCharge(false);
  }

  function onPointerCancel(event) {
    if (activePointer === null || event.pointerId !== activePointer) return;
    event.preventDefault();
    releaseCharge(true);
  }

  function onContextMenu(event) {
    event.preventDefault();
  }

  root.addEventListener('pointerdown', onPointerDown);
  root.addEventListener('pointerup', onPointerUp);
  root.addEventListener('pointercancel', onPointerCancel);
  root.addEventListener('contextmenu', onContextMenu);

  timeoutId = window.setTimeout(function () {
    if (state === 'ready' || state === 'playing') {
      finishGame(false, '未在限时内完成蓄力。', 'timeout');
    }
  }, 8000);

  return {
    destroy: function () {
      if (rafId) window.cancelAnimationFrame(rafId);
      if (timeoutId !== null) window.clearTimeout(timeoutId);
      root.removeEventListener('pointerdown', onPointerDown);
      root.removeEventListener('pointerup', onPointerUp);
      root.removeEventListener('pointercancel', onPointerCancel);
      root.removeEventListener('contextmenu', onContextMenu);
      if (root.parentNode) root.parentNode.removeChild(root);
    }
  };
}