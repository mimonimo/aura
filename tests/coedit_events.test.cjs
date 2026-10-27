// 공동 편집 알림 흐름 나눠 쓰기(webhwp-collab.js eventHub) — 같은 브라우저에서 문서마다 EventSource 하나만 열린다.
// 창(iframe·탭)마다 vm 맥락을 따로 두고, Web Locks·BroadcastChannel·EventSource·시계는 흉내 낸 것을 나눠 준다.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, '../src/zzaimy/app/static/webhwp-collab.js'), 'utf8');
const tick = () => new Promise((r) => setTimeout(r, 0));
async function settle() { for (let i = 0; i < 10; i++) await tick(); }

function browser(opts = {}) {
  let clock = 1_000_000;
  const sources = [];        // 열린 EventSource 모두
  const held = new Map();    // 락 이름 → {win, release, reject}
  const waiting = [];        // [{name, win, cb, resolve, reject, signal}]
  const channels = [];
  function grant(req) {
    let release, reject;
    const done = new Promise((res, rej) => { release = res; reject = rej; });
    held.set(req.name, {win: req.win, release, reject});
    Promise.resolve().then(() => req.cb({name: req.name})).then(() => {
      if (held.get(req.name) && held.get(req.name).release === release) { held.delete(req.name); pump(req.name); }
      release();
    }, (e) => { held.delete(req.name); pump(req.name); release(); });
    done.then(req.resolve, req.reject);
  }
  function pump(name) {
    if (held.has(name)) return;
    const i = waiting.findIndex((w) => w.name === name);
    if (i < 0) return;
    const [req] = waiting.splice(i, 1);
    grant(req);
  }
  function locksFor(win) {
    return {request(name, o, cb) {
      return new Promise((resolve, reject) => {
        const req = {name, win, cb, resolve, reject};
        if (o.steal) {
          const h = held.get(name);
          if (h) { held.delete(name); const e = new Error('stolen'); e.name = 'AbortError'; h.reject(e); }
          grant(req); return;
        }
        if (o.signal) o.signal.addEventListener('abort', () => {
          const i = waiting.indexOf(req);
          if (i >= 0) { waiting.splice(i, 1); const e = new Error('aborted'); e.name = 'AbortError'; reject(e); }
        });
        waiting.push(req); pump(name);
      });
    }};
  }
  function windowCtx(win) {
    const intervals = [];
    class FakeES {
      constructor(url) { this.url = url; this.readyState = 0; this.ls = {}; this.win = win; sources.push(this); setTimeout(() => { if (this.readyState !== 2) { this.readyState = 1; this.onopen && this.onopen(); } }, 0); }
      addEventListener(k, fn) { (this.ls[k] = this.ls[k] || []).push(fn); }
      close() { this.readyState = 2; }
      fire(kind, data, id) { (this.ls[kind] || []).forEach((fn) => fn({data: JSON.stringify(data), lastEventId: id ? String(id) : ''})); }
    }
    class FakeBC {
      constructor(name) { this.name = name; this.win = win; this.onmessage = null; channels.push(this); }
      postMessage(m) {
        const copy = JSON.parse(JSON.stringify(m));
        channels.filter((c) => c !== this && c.name === this.name && !c.win.dead).forEach((c) => setTimeout(() => c.onmessage && c.onmessage({data: copy}), 0));
      }
    }
    const ctx = {
      console, setTimeout, clearTimeout, Promise, JSON, Math, Object, Array, String, Number, Error, AbortController,
      setInterval: (fn) => { intervals.push(fn); return intervals.length; }, clearInterval: () => {},
      Date: {now: () => clock},
      EventSource: FakeES,
      sessionStorage: {getItem: () => null, setItem: () => {}},
    };
    if (!opts.noShare) { ctx.BroadcastChannel = FakeBC; ctx.navigator = {locks: locksFor(win)}; }
    else ctx.navigator = {};
    ctx.window = ctx;
    vm.createContext(ctx);
    vm.runInContext(SRC, ctx);
    win.ctx = ctx; win.intervals = intervals;
    return win;
  }
  return {
    sources, held, waiting,
    open(name) { return windowCtx({name, dead: false}); },
    advance(ms) { clock += ms; },
    // 창 닫힘 — 그 창의 락이 풀리고 채널·연결이 사라진다
    kill(win) {
      win.dead = true;
      sources.filter((s) => s.win === win).forEach((s) => s.close());
      for (const [name, h] of held) if (h.win === win) { held.delete(name); pump(name); }
      for (let i = waiting.length - 1; i >= 0; i--) if (waiting[i].win === win) waiting.splice(i, 1);
    },
  };
}

function subscribe(win, fid, startLast = 0) {
  const got = [];
  let last = startLast;
  win.ctx.AuraCollab.eventHub('/hwp/' + fid + '/events', String(fid)).add({
    last: () => last,
    msg: (m) => { got.push(m); if (m.id) last = Math.max(last, m.id); },
  });
  return got;
}
const live = (b) => b.sources.filter((s) => s.readyState !== 2);

test('같은 문서를 연 세 창 — EventSource 는 하나, 알림은 셋 모두에 원문 그대로', async () => {
  const b = browser();
  const wins = ['a', 'b', 'c'].map((n) => b.open(n));
  const got = wins.map((w) => subscribe(w, 7));
  await settle();
  assert.strictEqual(live(b).length, 1);
  live(b)[0].fire('field-updated', {field: '절:1', client: 'cX', by: 'demo_a', version: 3}, 41);
  await settle();
  for (const g of got) {
    const ev = g.filter((m) => m.t === 'ev');
    assert.strictEqual(ev.length, 1);
    assert.deepStrictEqual(JSON.parse(ev[0].data), {field: '절:1', client: 'cX', by: 'demo_a', version: 3});
    assert.strictEqual(ev[0].kind, 'field-updated');
    assert.strictEqual(ev[0].id, 41);
  }
  // 다른 문서는 따로 하나
  subscribe(wins[0], 8);
  await settle();
  assert.strictEqual(live(b).length, 2);
});

test('이끄미 창이 닫히면 다른 창이 마지막 번호 뒤부터 이어받는다', async () => {
  const b = browser();
  const wins = ['a', 'b', 'c'].map((n) => b.open(n));
  const got = wins.map((w) => subscribe(w, 7));
  await settle();
  const first = live(b)[0];
  first.fire('doc-saved', {by: 'demo_b'}, 55);
  await settle();
  b.kill(first.win);
  await settle();
  const now = live(b);
  assert.strictEqual(now.length, 1);
  assert.notStrictEqual(now[0].win, first.win);
  assert.match(now[0].url, /\?last=55$/);
  now[0].fire('lock-changed', {field: '절:2', user: 'demo_c'}, 56);
  await settle();
  const alive = wins.filter((w) => !w.dead);
  alive.forEach((w) => {
    const g = got[wins.indexOf(w)];
    assert.deepStrictEqual(g.filter((m) => m.t === 'ev').map((m) => m.id), [55, 56]);
    assert.ok(g.some((m) => m.t === 'open' && m.again), '이어받은 뒤에는 다시 맞추기 신호');
  });
});

test('이끄미가 조용하면(얼었거나 연결이 죽음) 듣는 창이 락을 빼앗아 이어받고, 옛 이끄미는 연결을 닫는다', async () => {
  const b = browser();
  const wins = ['a', 'b'].map((n) => b.open(n));
  wins.forEach((w) => subscribe(w, 9));
  await settle();
  const old = live(b)[0];
  b.advance(40_000);           // beat 없이 40초 — 듣는 창의 기준(30초 + 흔들기 5초 안)을 넘김, 이끄미 기준(45초)은 아직
  const follower = wins.find((w) => w !== old.win);
  follower.intervals.forEach((fn) => fn());
  await settle();
  const now = live(b);
  assert.strictEqual(now.length, 1);
  assert.strictEqual(now[0].win, follower);
  assert.strictEqual(old.readyState, 2);
  // 옛 이끄미는 다시 듣는 창이 되어 새 이끄미의 알림을 받는다
  const oldGot = [];
  old.win.ctx.AuraCollab.eventHub('/hwp/9/events', '9').add({last: () => 0, msg: (m) => oldGot.push(m)});
  now[0].fire('aura', {field: '절:3'}, 70);
  await settle();
  assert.deepStrictEqual(oldGot.filter((m) => m.t === 'ev').map((m) => m.id), [70]);
});

test('같은 알림이 두 길로 와도 한 번만', async () => {
  const b = browser();
  const w = b.open('a');
  const got = subscribe(w, 3);
  await settle();
  const es = live(b)[0];
  es.fire('presence', {user: 'x'}, 5);
  es.fire('presence', {user: 'x'}, 5);
  es.fire('beat', {}, 0);
  await settle();
  assert.deepStrictEqual(got.filter((m) => m.t === 'ev').map((m) => m.kind + ':' + m.id), ['presence:5', 'beat:0']);
});

test('Web Locks·BroadcastChannel 이 없으면 창마다 따로 연다', async () => {
  const b = browser({noShare: true});
  ['a', 'b'].forEach((n) => subscribe(b.open(n), 7));
  await settle();
  assert.strictEqual(live(b).length, 2);
});
