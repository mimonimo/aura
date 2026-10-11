// chat-scroll.js 단위 시험 — 가짜 스크롤 칸으로 "보낸 뒤 내용이 늦게 늘어나도 최신 글에 남는가"를 본다.
const test = require('node:test');
const assert = require('node:assert/strict');
const { createPin } = require('../src/zzaimy/app/static/chat-scroll.js');

function fakeScroller(height, client) {
  return {
    scrollHeight: height, clientHeight: client, scrollTop: 0,
    getBoundingClientRect() { return { top: 100, height: client }; },
  };
}
function manualFrames() {
  const queue = [];
  return { frame: fn => queue.push(fn), run() { while (queue.length) queue.shift()(); }, queue };
}

test('보낸 뒤 내용이 늘어나도 정한 시간 동안 맨 아래를 지킨다', () => {
  let t = 0; const f = manualFrames();
  const s = fakeScroller(1000, 400);
  const pin = createPin(s, { now: () => t, frame: f.frame, settleMs: 2000 });
  pin.toBottom();
  assert.equal(s.scrollTop, 600);
  // 아이콘·단추가 그려져 높이가 늘고, 무언가가 스크롤을 맨 위로 되돌린다
  s.scrollHeight = 1300; s.scrollTop = 0; t = 500;
  f.queue.shift()();
  assert.equal(s.scrollTop, 900);
  // 시간이 지나면 더는 붙잡지 않는다
  t = 2600; s.scrollHeight = 1500;
  f.run();
  assert.equal(s.scrollTop, 900);
  assert.equal(pin.active(), false);
});

test('사용자가 놓으면 바로 멈춘다', () => {
  let t = 0; const f = manualFrames();
  const s = fakeScroller(1000, 400);
  const pin = createPin(s, { now: () => t, frame: f.frame });
  pin.toBottom();
  pin.release();
  s.scrollTop = 120; s.scrollHeight = 1400;
  f.run();
  assert.equal(s.scrollTop, 120);
});

test('새 답이 화면보다 길면 답의 머리에 맞추고, 짧으면 맨 아래', () => {
  let t = 0; const f = manualFrames();
  const s = fakeScroller(3000, 500);
  s.scrollTop = 0;
  const longTurn = { isConnected: true, getBoundingClientRect: () => ({ top: 100 + 1800 - s.scrollTop, height: 1200 }) };
  const pin = createPin(s, { now: () => t, frame: f.frame, margin: 12 });
  pin.toLatest(longTurn);
  assert.equal(s.scrollTop, 1788);
  const shortTurn = { isConnected: true, getBoundingClientRect: () => ({ top: 100 + 2700 - s.scrollTop, height: 300 }) };
  pin.toLatest(shortTurn);
  assert.equal(s.scrollTop, 2500);
  // 앵커가 화면에서 빠지면(대화 교체) 맨 아래
  pin.toLatest({ isConnected: false, getBoundingClientRect: () => ({ top: 0, height: 9999 }) });
  assert.equal(s.scrollTop, 2500);
});

test('nearBottom 은 남은 거리로 판단한다', () => {
  const s = fakeScroller(1000, 400);
  const pin = createPin(s, { now: () => 0, frame: () => {} });
  s.scrollTop = 520; assert.equal(pin.nearBottom(), true);
  s.scrollTop = 300; assert.equal(pin.nearBottom(), false);
});
