// 편집 화면(hwp_edit.html)의 칸 밖 고침 살리기 — rescueOutside 를 꺼내 흉내 낸 fetch·편집기로 돌린다(11-10 9명 동시 실측: 남의 문서 틀 저장 뒤
// 다시 열면서 내가 칸 밖에 쓴 글이 사라짐). 1) 남이 문서 틀을 쥐고 있으면 기다렸다가 다시 보낸다 2) 옮기지 못한 글은 사본(keepCopy)으로
// 3) 살릴 것이 없으면 아무것도 보내지 않는다.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const TPL = fs.readFileSync(path.join(__dirname, '../src/zzaimy/app/templates/hwp_edit.html'), 'utf8');
function grab(name) {
  const at = TPL.indexOf('function ' + name + '(');
  assert.ok(at >= 0, name + ' 없음');
  let i = TPL.indexOf('{', at), depth = 0;
  for (; i < TPL.length; i++) {
    if (TPL[i] === '{') depth++;
    else if (TPL[i] === '}') { depth--; if (depth === 0) break; }
  }
  return TPL.slice(at, i + 1);
}

function page(replies) {
  const calls = [], says = [], copies = [];
  const ctx = {
    console, Promise, JSON, Array, String, Math,
    setTimeout: (fn) => setTimeout(fn, 0),
    FormData: class { constructor() { this.parts = []; } append(k, v) { this.parts.push(k); } },
    Blob: class { constructor(p) { this.p = p; } },
    fetch: (url, o) => { calls.push({url, parts: o.body.parts}); const r = replies.shift(); return Promise.resolve({json: () => Promise.resolve(r)}); },
    hwp: {hwpx: () => Promise.resolve(new Uint8Array([80, 75]))},
    say: (t, k) => says.push([t, k]),
    eng: {keepCopy: (f, t) => copies.push([f, t])},
    fileUrl: '/hwp/7/file', readonly: false, baseBlob: {b: 1}, outsidePending: true,
  };
  vm.createContext(ctx);
  vm.runInContext(grab('rescueOutside') + '; this.rescueOutside = rescueOutside;', ctx);
  return {ctx, calls, says, copies};
}

test('남이 문서 틀을 쥐면 기다렸다가 다시 보내고, 옮긴 뒤 살릴 표시를 지운다', async () => {
  const p = page([{ok: false, wait: true, by: 'park', by_name: '박다온'}, {ok: true, saved: true, moved: 2, lost: []}]);
  await p.ctx.rescueOutside();
  assert.strictEqual(p.calls.length, 2);
  assert.strictEqual(p.calls[0].url, '/hwp/7/rescue');
  assert.deepStrictEqual(p.calls[0].parts, ['base', 'mine']);
  assert.strictEqual(p.ctx.outsidePending, false);
  assert.ok(p.says.some(([t]) => t.includes('박다온') && t.includes('옮깁니다')));
  assert.ok(p.says.some(([t, k]) => t.includes('2곳') && k === 'ok'));
});

test('옮기지 못한 글은 사본으로 남긴다(조용히 버리지 않음)', async () => {
  const p = page([{ok: true, saved: false, moved: 0, lost: ['[동시2]', '[동시3]']}]);
  await p.ctx.rescueOutside();
  assert.deepStrictEqual(p.copies.map((c) => c[1]), ['[동시2]', '[동시3]']);
  assert.strictEqual(p.ctx.outsidePending, false);
});

test('살릴 것이 없으면 보내지 않는다', async () => {
  const p = page([]);
  p.ctx.outsidePending = false;
  await p.ctx.rescueOutside();
  assert.strictEqual(p.calls.length, 0);
});
