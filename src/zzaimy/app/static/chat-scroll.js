// 대화 칸을 최신 글에 붙여 두는 고정 장치 — chat-workspace.js 가 쓴다.
// 보낸 뒤·답이 온 뒤에는 아이콘 그리기, 「문서에서 보기」 단추, 문서 패널 여닫기처럼 내용 높이가 한동안
// 늦게 바뀐다. 한 번만 맨 아래로 옮기면 그 뒤 바뀐 높이 때문에 엉뚱한 곳(맨 위)에 남는다. 그래서 정한 시간
// 동안 매 프레임 목표 위치를 다시 맞추고, 사용자가 직접 스크롤하면 바로 놓는다.
(function (root) {
  'use strict';
  function createPin(scroller, options) {
    var opts = options || {};
    var settleMs = opts.settleMs == null ? 2500 : opts.settleMs;
    var margin = opts.margin == null ? 12 : opts.margin;
    var now = opts.now || function () { return Date.now(); };
    var frame = opts.frame || (typeof requestAnimationFrame === 'function' ? requestAnimationFrame : function (fn) { return setTimeout(fn, 16); });
    var until = -Infinity, anchor = null, looping = false;

    function maxTop() { return Math.max(0, scroller.scrollHeight - scroller.clientHeight); }
    // 앵커(새 답의 머리)가 화면보다 길면 그 머리에, 아니면 맨 아래에 맞춘다 — 긴 답을 끝부터 보이지 않게
    function target() {
      if (anchor && anchor.isConnected !== false && typeof anchor.getBoundingClientRect === 'function') {
        var a = anchor.getBoundingClientRect(), s = scroller.getBoundingClientRect();
        if (a.height > scroller.clientHeight - margin) {
          return Math.min(maxTop(), Math.max(0, scroller.scrollTop + a.top - s.top - margin));
        }
      }
      return maxTop();
    }
    function apply() {
      if (now() > until) return false;
      var t = target();
      if (Math.abs(scroller.scrollTop - t) > 1) scroller.scrollTop = t;
      return true;
    }
    function loop() {
      if (looping) return;
      looping = true;
      (function tick() {
        if (!apply()) { looping = false; return; }
        frame(tick);
      })();
    }
    function hold(el, ms) {
      anchor = el || null;
      until = now() + (ms == null ? settleMs : ms);
      apply();
      loop();
    }
    return {
      toBottom: function (ms) { hold(null, ms); },
      toLatest: function (el, ms) { hold(el, ms); },
      release: function () { until = -Infinity; anchor = null; },
      active: function () { return now() <= until; },
      settle: apply,
      nearBottom: function (px) { return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < (px == null ? 100 : px); }
    };
  }
  // 사용자가 직접 움직였다는 신호 — 휠·터치·키·스크롤 막대 끌기. 이때는 고정을 놓는다.
  function releaseOnUserScroll(scroller, pin) {
    var keys = { ArrowUp: 1, ArrowDown: 1, PageUp: 1, PageDown: 1, Home: 1, End: 1, ' ': 1 };
    scroller.addEventListener('wheel', function () { pin.release(); }, { passive: true });
    scroller.addEventListener('touchmove', function () { pin.release(); }, { passive: true });
    scroller.addEventListener('keydown', function (e) { if (keys[e.key]) pin.release(); });
    scroller.addEventListener('pointerdown', function (e) { if (e.target === scroller) pin.release(); });
  }
  var api = { createPin: createPin, releaseOnUserScroll: releaseOnUserScroll };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ChatScroll = api;
})(typeof window !== 'undefined' ? window : this);
