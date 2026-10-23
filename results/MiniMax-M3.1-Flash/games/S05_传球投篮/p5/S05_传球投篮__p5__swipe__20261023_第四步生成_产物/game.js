function mount(container, api) {
  var root = document.createElement('div');
  root.id = 'game';
  root.className = 'pickle-game';
  container.appendChild(root);

  var heading = document.createElement('div');
  heading.className = 'game-heading';
  var title = document.createElement('strong');
  title.textContent = '末端拨球';
  var subtitle = document.createElement('span');
  subtitle.textContent = '向右短滑，修正自绘篮球的落点';
  heading.appendChild(title);
  heading.appendChild(subtitle);

  var playfield = document.createElement('div');
  playfield.className = 'playfield';
  var court = document.createElement('div');
  court.className = 'court';
  var hoop = document.createElement('div');
  hoop.className = 'hoop';
  var hoopInner = document.createElement('div');
  hoopInner.className = 'hoop-inner';
  hoop.appendChild(hoopInner);
  var target = document.createElement('div');
  target.className = 'target-zone';
  target.textContent = '篮心';
  var ball = document.createElement('div');
  ball.className = 'ball';
  ball.textContent = '●';
  var lane = document.createElement('div');
  lane.className = 'lane';
  var gesture = document.createElement('div');
  gesture.className = 'gesture-line';
  var info = document.createElement('div');
  info.className = 'info';
  var status = document.createElement('div');
  status.className = 'status';
  status.textContent = '在球场区域按住并向右短滑';
  var meter = document.createElement('div');
  meter.className = 'meter';
  var meterFill = document.createElement('div');
  meterFill.className = 'meter-fill';
  meter.appendChild(meterFill);
  var timeLabel = document.createElement('div');
  timeLabel.className = 'time-label';
  timeLabel.textContent = '剩余 8.0 秒';

  court.appendChild(lane);
  court.appendChild(target);
  court.appendChild(hoop);
  court.appendChild(ball);
  court.appendChild(gesture);
  playfield.appendChild(court);
  info.appendChild(status);
  info.appendChild(meter);
  info.appendChild(timeLabel);
  root.appendChild(heading);
  root.appendChild(playfield);
  root.appendChild(info);

  var state = 'ready';
  var finished = false;
  var pointerId = null;
  var startX = 0;
  var startY = 0;
  var currentX = 0;
  var currentY = 0;
  var startedAt = Date.now();
  var timerHandle = null;
  var timeoutHandle = null;
  var SUCCESS_MIN = 24;
  var SUCCESS_MAX = 96;
  var SUCCESS_RATIO = 1.2;
  var EXPECTED_DIRECTION = 1;

  function setText(node, text) {
    node.textContent = text;
  }

  function updateClock() {
    var elapsed = (Date.now() - startedAt) / 1000;
    var remaining = Math.max(0, 8 - elapsed);
    timeLabel.textContent = '剩余 ' + remaining.toFixed(1) + ' 秒';
    meterFill.style.width = (remaining / 8 * 100) + '%';
    if (remaining <= 0) {
      fail('未在时限内完成', 'timeout');
    }
  }

  function resetGesture() {
    pointerId = null;
    gesture.style.width = '0px';
    gesture.style.opacity = '0';
  }

  function finish(result, message, visual) {
    if (finished) return;
    finished = true;
    state = 'ended';
    if (timerHandle !== null) {
      clearInterval(timerHandle);
      timerHandle = null;
    }
    if (timeoutHandle !== null) {
      clearTimeout(timeoutHandle);
      timeoutHandle = null;
    }
    if (visual === 'success') {
      ball.classList.add('ball-success');
      hoop.classList.add('hoop-success');
      status.textContent = '精准拨球';
    } else {
      ball.classList.add('ball-fail');
      status.textContent = message;
    }
    gesture.style.opacity = '0';
    api.finish({result: result, message: message});
  }

  function fail(message, kind) {
    if (finished) return;
    if (kind === 'timeout') {
      ball.classList.add('ball-fail');
    } else if (kind === 'cancel') {
      ball.classList.add('ball-fail');
    } else if (kind === 'wrong') {
      ball.classList.add('ball-left');
    } else if (kind === 'short') {
      ball.classList.add('ball-shake');
    } else if (kind === 'long') {
      ball.classList.add('ball-far');
    } else {
      ball.classList.add('ball-fail');
    }
    finish('failure', message, 'failure');
  }

  function onPointerDown(event) {
    if (finished || state === 'ended') return;
    if (pointerId !== null) return;
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    pointerId = event.pointerId;
    startX = event.clientX;
    startY = event.clientY;
    currentX = event.clientX;
    currentY = event.clientY;
    state = 'playing';
    status.textContent = '向右短滑，松开完成拨腕';
    gesture.style.opacity = '1';
    if (court.setPointerCapture) {
      try { court.setPointerCapture(pointerId); } catch (e) {}
    }
    event.preventDefault();
  }

  function onPointerMove(event) {
    if (finished || pointerId !== event.pointerId) return;
    currentX = event.clientX;
    currentY = event.clientY;
    var dx = currentX - startX;
    var dy = currentY - startY;
    var width = court.getBoundingClientRect().width;
    var x = Math.max(0, Math.min(width, startX - court.getBoundingClientRect().left + dx));
    gesture.style.left = (startX - court.getBoundingClientRect().left) + 'px';
    gesture.style.top = (startY - court.getBoundingClientRect().top) + 'px';
    gesture.style.width = Math.max(0, Math.abs(dx)) + 'px';
    gesture.style.transform = 'rotate(' + (dx >= 0 ? 0 : 180) + 'deg)';
    if (Math.abs(dx) >= SUCCESS_MIN) {
      status.textContent = dx > 0 ? '方向正确，继续保持短距离' : '方向相反，篮球会偏出篮心';
    } else {
      status.textContent = '再向右拨一点';
    }
    event.preventDefault();
  }

  function onPointerUp(event) {
    if (finished || pointerId !== event.pointerId) return;
    var dx = event.clientX - startX;
    var dy = event.clientY - startY;
    var horizontal = Math.abs(dx);
    var vertical = Math.abs(dy);
    var direction = dx === 0 ? 0 : (dx > 0 ? 1 : -1);
    resetGesture();
    if (horizontal < SUCCESS_MIN) {
      fail('力度不足', 'short');
      return;
    }
    if (horizontal > SUCCESS_MAX) {
      fail('力度过大', 'long');
      return;
    }
    if (horizontal < vertical * SUCCESS_RATIO) {
      fail('非水平拨动', 'vertical');
      return;
    }
    if (direction !== EXPECTED_DIRECTION) {
      fail('拨反方向', 'wrong');
      return;
    }
    ball.style.left = '68%';
    ball.style.top = '38%';
    finish('success', '精准拨球', 'success');
    event.preventDefault();
  }

  function onPointerCancel(event) {
    if (finished || pointerId !== event.pointerId) return;
    resetGesture();
    fail('操作已取消', 'cancel');
    event.preventDefault();
  }

  court.addEventListener('pointerdown', onPointerDown);
  court.addEventListener('pointermove', onPointerMove);
  court.addEventListener('pointerup', onPointerUp);
  court.addEventListener('pointercancel', onPointerCancel);
  court.addEventListener('lostpointercapture', onPointerCancel);
  timerHandle = setInterval(updateClock, 50);
  timeoutHandle = setTimeout(function () { fail('未在时限内完成', 'timeout'); }, 8000);

  return {
    destroy: function () {
      if (timerHandle !== null) clearInterval(timerHandle);
      if (timeoutHandle !== null) clearTimeout(timeoutHandle);
      court.removeEventListener('pointerdown', onPointerDown);
      court.removeEventListener('pointermove', onPointerMove);
      court.removeEventListener('pointerup', onPointerUp);
      court.removeEventListener('pointercancel', onPointerCancel);
      court.removeEventListener('lostpointercapture', onPointerCancel);
      if (root.parentNode) root.parentNode.removeChild(root);
    }
  };
}