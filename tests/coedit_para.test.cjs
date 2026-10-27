// 문단 단위 공동 편집(webhwp-collab.js — paraHunks·mapPara·merge3, 엔진의 받은 글 합치기). ADR-0070 덧붙임 10-27.
// 1) 글 셈이 서버(webhwp_paras.py)와 같은 답인지. 2) 두 창(엔진 둘, 창마다 vm 맥락)이 한 칸의 다른 문단을 동시에 고치고, 한 사람이 위에 문단을
// 넣어도 두 글이 모두 남고 서로 같아지며, 다른 사람의 커서가 같은 문단에 머무는지. 서버는 서버 규칙(옛 판 바탕 문단 합치기)을 흉내 낸 작은 것.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC = fs.readFileSync(path.join(__dirname, '../src/zzaimy/app/static/webhwp-collab.js'), 'utf8');
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
function context(extra = {}) {
  const ctx = Object.assign({console, setTimeout, clearTimeout, Promise, JSON, Math, Date, TextEncoder, performance,
    setInterval: () => 0, clearInterval: () => {}, addEventListener() {}, sessionStorage: {getItem: () => null, setItem: () => {}}, navigator: {}}, extra);
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(SRC, ctx);
  return ctx;
}
const C = context().AuraCollab;

test('문단 셈 — 서버(webhwp_paras)와 같은 답', () => {
  const b = 'a\nb\nc\nd';
  const m = (x, y, z) => { const r = C.merge3(x, y, z); return [r.text, Array.from(r.clash)]; };
  assert.deepStrictEqual(m(b, 'a\nB\nc\nd', 'a\nb\nc\nD'), ['a\nB\nc\nD', []]);
  assert.deepStrictEqual(m(b, 'a\nb\nc\nD', 'a\nNEW\nb\nc\nd'), ['a\nNEW\nb\nc\nD', []]);
  assert.deepStrictEqual(m(b, 'a\nX\nc\nd', 'a\nY\nc\nd'), ['a\nX\nc\nd', [1]]);
  assert.deepStrictEqual(m(b, 'a\nb\nm\nc\nd', 'a\nb\nt\nc\nd'), ['a\nb\nt\nm\nc\nd', []]);
  assert.deepStrictEqual(m(b, 'a\nB\nc\nd', 'a\nB\nc\nd'), ['a\nB\nc\nd', []]);
  assert.deepStrictEqual(m(b, 'a\nc\nd', 'a\nb\nc\nD'), ['a\nc\nD', []]);
  assert.deepStrictEqual([0, 1, 2, 3].map((p) => C.mapPara(b, 'a\nNEW\nb\nc\nd', p)), [0, 2, 3, 4]);
  assert.deepStrictEqual([0, 1, 2, 3].map((p) => C.mapPara(b, 'b\nc\nd', p)), [0, 0, 1, 2]);
  assert.deepStrictEqual(Array.from(C.paraHunks(['x', 'y', 'z'], ['x', 'q', 'y', 'z'])).map((h) => Array.from(h)), [[1, 1, 1, 2]]);
});

// ── 작은 서버: 판·옛 판 글(합치기 바탕)·바뀐 부분 알림. 서버 apply_ops 의 문단 합치기 규칙을 그대로 ──
function server(base) {
  const F = '절:3', hist = [base], st = {v: 0, text: base, client: '', merges: 0}, sources = [];
  let evId = 0;
  const diff = (a, b) => { let i = 0, j = 0; const n = Math.min(a.length, b.length); while (i < n && a[i] === b[i]) i++; while (j < n - i && a[a.length - 1 - j] === b[b.length - 1 - j]) j++; return {at: i, del: a.length - i - j, ins: b.slice(i, b.length - j)}; };
  function emit(kind, d) { evId++; sources.forEach((s) => s.readyState === 1 && (s.ls[kind] || []).forEach((fn) => fn({data: JSON.stringify(d), lastEventId: String(evId)}))); }
  function ops(user, client, list) {
    const ck = user + '|' + client;
    return list.map((op) => {
      const old = st.text, isD = typeof op.ins === 'string' && op.text === undefined;
      let text, merged = false;
      if (op.base !== st.v && st.client !== ck) {                  // 옛 판 — 그 판 글을 바탕으로 문단 합치기
        const bt = hist[op.base];
        const mine = isD ? bt.slice(0, op.at) + op.ins + bt.slice(op.at + op.del) : op.text;
        if (isD && C.fp(mine) !== op.fp) return {seq: op.seq, field: F, ok: false, reason: 'delta'};
        text = C.merge3(bt, mine, st.text).text; merged = true; st.merges++;
      } else if (isD) {
        if (op.base !== st.v) return {seq: op.seq, field: F, ok: false, reason: 'delta'};
        text = old.slice(0, op.at) + op.ins + old.slice(op.at + op.del);
      } else text = op.text;
      st.v += 1; st.text = text; st.client = merged ? '' : ck; hist[st.v] = text;
      const d = diff(old, text);
      emit('field-updated', {field: F, version: st.v, base: st.v - 1, at: d.at, del: d.del, ins: d.ins, fp: C.fp(text), by: user, client});
      const r = {seq: op.seq, field: F, ok: true, version: st.v};
      if (merged) Object.assign(r, {merged: true, text});
      return r;
    });
  }
  function fetchFor(user) {
    return (url, o) => {
      const body = o && o.body ? JSON.parse(o.body) : {};
      let out = {};
      if (url.includes('/coedit')) out = {fields: st.v ? {[F]: {version: st.v, text: st.text, fp: C.fp(st.text)}} : {}, locks: {}, para_locks: [], people: [], names: {}, last: evId, now: Date.now() / 1000, conf: {pause_ms: 40, throttle_ms: 40}};
      else if (url.endsWith('/ops')) out = {results: ops(user, body.client, body.ops)};
      else if (url.endsWith('/lease')) out = body.action === 'touch' ? {until: {}} : {ok: true, para: body.para, until: Date.now() / 1000 + 300};
      else if (url.endsWith('/presence')) out = {people: []};
      return new Promise((r) => setTimeout(() => r({ok: true, status: 200, json: () => Promise.resolve(out)}), 3));
    };
  }
  class ES {
    constructor() { this.readyState = 0; this.ls = {}; sources.push(this); setTimeout(() => { this.readyState = 1; this.onopen && this.onopen(); }, 0); }
    addEventListener(k, fn) { (this.ls[k] = this.ls[k] || []).push(fn); }
    close() { this.readyState = 2; }
  }
  return {F, st, fetchFor, ES};
}

// ── 가짜 기안기: 칸 하나·커서(문단·자리). 받은 글을 넣으면 실제처럼 커서가 칸 처음으로 갔다가 되돌리기로 돌아온다 ──
function editor(F, text) {
  const s = {text, caret: {para: 0, pos: 0}, cbs: {edit: [], caret: [], activity: [], commit: []}, last: 0};
  const hwp = {
    attach: () => true, fields: () => [F], read: (f) => (f === F ? s.text : null), readMany: (ns) => Object.fromEntries(ns.map((n) => [n, hwp.read(n)])),
    write: (f, t) => { s.text = t; s.caret = {para: 0, pos: 0}; return true; },
    applyDelta: (f, t0, at, del, ins) => { if (t0 !== s.text) return false; s.text = t0.slice(0, at) + ins + t0.slice(at + del); s.caret = {para: 0, pos: 0}; return true; },
    setEditable() {}, setFormMode() {}, formMode: () => false, setReadOnly() {}, currentField: () => F,
    getCursor: () => ({pos: {}, rel: {field: F, dp: s.caret.para, pos: s.caret.pos}}),
    restoreCursor: (c) => { if (c && c.rel) s.caret = {para: c.rel.dp, pos: c.rel.pos}; },
    caretPara: () => ({field: F, para: s.caret.para}), caretInField: () => null, selectionFields: () => null, snapIntoField: () => F,
    isComposing: () => false, isProbing: () => false, lastInputAt: () => s.last, lockUndo() {}, caretPage: () => 1, viewTop: () => 0,
    onLocalEdit: (cb) => s.cbs.edit.push(cb), onCaret: (cb) => s.cbs.caret.push(cb), onActivity: (cb) => s.cbs.activity.push(cb), onCommit: (cb) => s.cbs.commit.push(cb),
    setToolbarGuard() {}, isHwpxRich: () => false,
  };
  const edited = () => { s.last = Date.now(); s.cbs.edit.forEach((cb) => cb({composing: false})); };
  s.put = (para, pos) => { s.caret = {para, pos}; s.cbs.caret.forEach((cb) => cb({type: 'pointerup'})); };
  s.type = (str) => { const p = s.text.split('\n'), c = s.caret; p[c.para] = p[c.para].slice(0, c.pos) + str + p[c.para].slice(c.pos); c.pos += str.length; s.text = p.join('\n'); edited(); };
  s.enter = () => { const p = s.text.split('\n'), c = s.caret; p.splice(c.para, 1, p[c.para].slice(0, c.pos), p[c.para].slice(c.pos)); s.text = p.join('\n'); s.caret = {para: c.para + 1, pos: 0}; edited(); s.cbs.caret.forEach((cb) => cb({type: 'keyup', key: 'Enter'})); };
  return {hwp, s};
}

test('두 창이 한 칸의 다른 문단을 동시에 — 두 글 모두 남고, 위에 문단이 들어도 커서는 제 문단', async () => {
  const base = '가) 첫째\n나) 둘째\n다) 셋째\n라) 넷째';
  const srv = server(base);
  const open = async (user) => {
    const ctx = context({fetch: srv.fetchFor(user), EventSource: srv.ES});
    const ed = editor(srv.F, base);
    const eng = ctx.AuraCollab.engine({fid: 1, me: user, hwp: ed.hwp});
    await eng.start();
    return {eng, s: ed.s};
  };
  const K = await open('kim'), P = await open('park');
  K.s.put(1, 5); P.s.put(3, 5);                         // 둘째·넷째 문단 끝
  // 같은 순간에 둘 다 친다 — 한쪽은 옛 판으로 닿아 서버가 합친다
  for (let i = 0; i < 6; i++) { K.s.type('K'); P.s.type('P'); await wait(15); }
  // 김가람이 둘째 문단 끝에서 Enter 로 새 문단 — 박다온 문단 위에 문단이 하나 는다. 박다온은 계속 친다
  K.s.enter(); K.s.type('새 문단');
  for (let i = 0; i < 4; i++) { P.s.type('Q'); await wait(20); }
  const want = '가) 첫째\n나) 둘째KKKKKK\n새 문단\n다) 셋째\n라) 넷째PPPPPPQQQQ';
  for (let i = 0; i < 200 && !(K.s.text === want && P.s.text === want && srv.st.text === want && !K.eng.pending() && !P.eng.pending()); i++) await wait(20);
  assert.ok(srv.st.merges > 0, '옛 판으로 닿은 고침이 있어 서버가 합쳤다');
  assert.strictEqual(srv.st.text, want);
  assert.strictEqual(K.s.text, want);
  assert.strictEqual(P.s.text, want);
  // 박다온 커서는 넷째 문단(이제 다섯째, 번호 4) 끝 그대로 — 남의 문단 넣기·받은 글 넣기로 옮겨 가지 않았다
  assert.deepStrictEqual({...P.s.caret}, {para: 4, pos: 15});
  assert.deepStrictEqual({...K.s.caret}, {para: 2, pos: 4});
  assert.ok(P.eng.myPara() && P.eng.myPara().field === srv.F, '박다온은 문단 임대를 쥐었다(칸째가 아님)');
});
