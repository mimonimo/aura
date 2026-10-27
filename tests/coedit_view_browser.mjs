// 공동 편집 「보던 자리 지키기」 브라우저 검사(ADR-0070 덧붙임 10-27) — 헤드리스 크롬 세 창(브라우저 맥락 따로 = 사람 셋)이 같은 문서를 연다.
// 창 A(김가람)가 위쪽 칸(절:1)에 글 139자(문단 나눔 하나 포함)를 치고 다시 모두 지운다. 그동안
//  - 창 B(이나래)는 4쪽 절:4 안에 커서를 두고 그 커서가 화면 y≈400 에 보인다(쓰는 사람),
//  - 창 C(박다온)는 커서가 문서 맨 위(화면 밖)이고 절:4 머리가 y≈400 에 보인다(읽기만 하는 사람).
// 받은 글을 넣은 뒤 B 의 커서 높이·C 의 절:4 머리 높이가 3px 안에 머무는지 본다(늘 때·줄 때 모두).
// 기안기는 시험용 가짜(tests/fixtures/fake_webhwp_editor.js — 쪽 배치·두 겹 스크롤·다음 장면 끌어오기를 흉내), 서버도 칸 길만 흉내 낸 작은 서버다.
// 실제 어댑터(webhwp-wrapper.js)·공동 편집 층(webhwp-collab.js)을 그대로 쓴다.
// 실행: node tests/coedit_view_browser.mjs  (저장소 뿌리에서. 고치기 전 판과 견주려면 OLD_REF=<커밋> — 그때는 수치만 찍고 판정하지 않는다)
import {spawn, execFileSync} from 'node:child_process';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {crc32} from 'node:zlib';
import assert from 'node:assert/strict';

const ROOT = process.cwd();
const OLD = process.env.OLD_REF || '';
const ZOOM = +(process.env.ZOOM || 0.7);   // 기안기 배율 — 세 칸 시연은 폭 맞춤으로 1보다 작다. 단위를 가정하지 않는지 보려면 바꿔 돌린다
const src = async p => OLD ? execFileSync('git', ['show', `${OLD}:${p}`], {cwd: ROOT, encoding: 'utf8'}) : readFile(`${ROOT}/${p}`, 'utf8');
const wrapperJs = await src('src/zzaimy/app/static/webhwp-wrapper.js');
const collabJs = await src('src/zzaimy/app/static/webhwp-collab.js');
const fakeJs = await readFile(`${ROOT}/tests/fixtures/fake_webhwp_editor.js`, 'utf8');

// ── 문서: 제목 + 절 다섯(절:4 가 4쪽 근처) ──
const line = (s, n) => (s + ' 사업 운영 계획과 추진 일정을 정리한 문단입니다. ').repeat(4).slice(0, n);
const DOC = [['절:0', ['시험 계획서']]];
[[1, 6], [2, 22], [3, 22], [4, 10], [5, 22]].forEach(([k, n]) => DOC.push([`절:${k}`, Array.from({length: n}, (_, i) => (i === 0 ? `${k}. 절 제목 ${k}` : line(`${k}-${i}`, 100)))]));
const initial = Object.fromEntries(DOC.map(([f, ps]) => [f, ps.join('\n')]));
const NAMES = {A: '김가람', B: '이나래', C: '박다온'};

// ── 칸 길만 흉내 낸 서버(판 번호·바뀐 부분·알림 흐름) ──
const fp = s => (crc32(Buffer.from(s, 'utf8')) >>> 0).toString(16).padStart(8, '0');
const state = {};             // 칸 → {v, text}
let evId = 0;
const events = [], streams = new Set();
function emit(kind, data) {
  const id = ++evId; events.push({id, kind, data});
  for (const r of streams) r.write(`event: ${kind}\nid: ${id}\ndata: ${JSON.stringify(data)}\n\n`);
}
const user = req => (/(?:^|;\s*)u=(\w)/.exec(req.headers.cookie || '') || [])[1] || 'A';
const json = (res, o) => { res.writeHead(200, {'content-type': 'application/json'}); res.end(JSON.stringify(o)); };
const body = req => new Promise(r => { let b = ''; req.on('data', c => { b += c; }); req.on('end', () => { try { r(JSON.parse(b || '{}')); } catch { r({}); } }); });
const page = u => `<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0}#box{position:absolute;left:0;top:40px;width:900px;height:760px}#box iframe{width:100%;height:100%;border:0}</style></head>
<body><div id="box"><iframe src="/editor?zoom=${ZOOM}"></iframe></div><script src="/wrapper.js"></script><script src="/collab.js"></script><script>
var fr = document.querySelector('iframe');
window.ready = new Promise(function (res) { fr.addEventListener('load', function () {
  var W = fr.contentWindow; W.fake.load(${JSON.stringify(DOC)});
  window.hwp = AuraHwpWrapper.create(W.fake.ctrl, document.getElementById('box'));
  window.eng = AuraCollab.engine({fid: 1, me: '${u}', hwp: window.hwp});
  window.eng.start().then(function () { res(true); });
}); });
</script></body></html>`;
const editor = `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;height:100%}#hcwoViewScroll{position:absolute;inset:0;overflow:auto}#spacer{width:10px}
#PaperCanvas{position:absolute;left:0;top:0;right:0;bottom:0;pointer-events:none}</style></head><body><div id="hcwoViewScroll"><div id="spacer"></div></div><div id="PaperCanvas"></div>
<script src="/fake.js"></script></body></html>`;
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x'), p = url.pathname, u = user(req);
  if (p === '/pane') { const who = url.searchParams.get('u'); res.writeHead(200, {'content-type': 'text/html; charset=utf-8', 'set-cookie': `u=${who}; path=/`}); res.end(page(who)); return; }
  if (p === '/editor') { res.writeHead(200, {'content-type': 'text/html; charset=utf-8'}); res.end(editor); return; }
  const js = {'/wrapper.js': wrapperJs, '/collab.js': collabJs, '/fake.js': fakeJs}[p];
  if (js) { res.writeHead(200, {'content-type': 'text/javascript'}); res.end(js); return; }
  if (p === '/hwp/1/events') {
    res.writeHead(200, {'content-type': 'text/event-stream', 'cache-control': 'no-store'});
    res.write(':ok\n\n');
    const last = +(url.searchParams.get('last') || 0);
    for (const e of events) if (e.id > last) res.write(`event: ${e.kind}\nid: ${e.id}\ndata: ${JSON.stringify(e.data)}\n\n`);
    streams.add(res); req.on('close', () => streams.delete(res));
    return;
  }
  if (p === '/hwp/1/coedit') {
    const want = url.searchParams.getAll('field'), fields = {};
    for (const [f, s] of Object.entries(state)) if (!want.length || want.includes(f)) fields[f] = {version: s.v, text: s.text, fp: fp(s.text)};
    json(res, {fields, locks: {}, people: [], names: NAMES, last: evId, now: Date.now() / 1000, conf: {pause_ms: 300, throttle_ms: 300, hash_check: 30}});
    return;
  }
  if (p === '/hwp/1/ops') {
    const b = await body(req), results = [];
    for (const op of b.ops || []) {
      const cur = state[op.field] || {v: 0, text: initial[op.field] ?? ''};
      let text;
      if (op.ins !== undefined) {
        if (op.base !== cur.v) { results.push({seq: op.seq, field: op.field, ok: false, reason: 'delta'}); continue; }
        const cp = Array.from(cur.text);
        text = cp.slice(0, op.at).join('') + op.ins + cp.slice(op.at + op.del).join('');
        if (fp(text) !== op.fp) { results.push({seq: op.seq, field: op.field, ok: false, reason: 'delta'}); continue; }
      } else text = op.text;
      const v = cur.v + 1;
      state[op.field] = {v, text};
      const d = {field: op.field, version: v, by: u, by_name: NAMES[u], client: b.client};
      if (op.ins !== undefined) Object.assign(d, {base: cur.v, at: op.at, del: op.del, ins: op.ins, fp: op.fp}); else d.text = text;
      emit('field-updated', d);
      results.push({seq: op.seq, field: op.field, ok: true, version: v, until: Date.now() / 1000 + 300});
    }
    json(res, {results});
    return;
  }
  if (p === '/hwp/1/lease') { const b = await body(req); json(res, b.action === 'touch' ? {until: {}} : {ok: true, until: Date.now() / 1000 + 300, since: Date.now() / 1000}); return; }
  if (p === '/hwp/1/presence') { json(res, {people: []}); return; }
  if (p.startsWith('/hwp/1/')) { json(res, {}); return; }
  res.writeHead(404); res.end();
});
setInterval(() => { for (const r of streams) r.write('event: beat\ndata: {}\n\n'); }, 10000).unref();
await new Promise(r => server.listen(0, '127.0.0.1', r));
const base = `http://127.0.0.1:${server.address().port}`;

const chrome = spawn(process.env.CHROME_BIN || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ['--headless', '--disable-gpu', '--no-first-run', '--remote-debugging-port=0', `--user-data-dir=/private/tmp/aura-coedit-view-${process.pid}`, 'about:blank'],
  {stdio: ['ignore', 'ignore', 'pipe']});
let ws;
const wait = ms => new Promise(r => setTimeout(r, ms));
try {
  const wsUrl = await new Promise((resolve, reject) => {
    let out = '';
    chrome.stderr.on('data', b => { out += b; const m = out.match(/DevTools listening on (ws:\/\/\S+)/); if (m) resolve(m[1]); });
    setTimeout(() => reject(Error('startup')), 15000).unref();
  });
  ws = new WebSocket(wsUrl); await new Promise(r => ws.addEventListener('open', r, {once: true}));
  let id = 0; const pending = new Map();
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { const q = pending.get(m.id); pending.delete(m.id); m.error ? q.reject(m.error) : q.resolve(m.result); } };
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => { const i = ++id; pending.set(i, {resolve, reject}); ws.send(JSON.stringify({id: i, method, params, ...(sessionId ? {sessionId} : {})})); });
  async function pane(u) {          // 사람마다 브라우저 맥락 따로(쿠키·알림 흐름·창 번호가 섞이지 않게)
    const {browserContextId} = await send('Target.createBrowserContext');
    const t = await send('Target.createTarget', {url: 'about:blank', browserContextId});
    const s = (await send('Target.attachToTarget', {targetId: t.targetId, flatten: true})).sessionId;
    await send('Emulation.setDeviceMetricsOverride', {width: 900, height: 800, deviceScaleFactor: 1, mobile: false}, s);
    await send('Page.enable', {}, s); await send('Emulation.setFocusEmulationEnabled', {enabled: true}, s);
    await send('Page.navigate', {url: `${base}/pane?u=${u}`}, s);
    const ev = async expression => { const r = await send('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true}, s); if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value; };
    for (let i = 0; i < 100; i++) { try { if (await ev('!!window.ready')) break; } catch {} await wait(50); }
    await ev('window.ready');
    return ev;
  }
  const A = await pane('A'), B = await pane('B'), C = await pane('C');
  const F = 'document.querySelector("iframe").contentWindow.fake';
  // B: 절:4 셋째 문단 가운데에 커서, 그 커서가 화면 y≈400 / C: 커서는 문서 맨 위, 절:4 머리가 y≈400
  await B(`${F}.put('절:4', 2, 30); ${F}.scrollTo(0); ${F}.scrollTo(${F}.caretY() - 400); true`);
  await C(`${F}.put('절:0', 0, 0); ${F}.scrollTo(0); ${F}.scrollTo(${F}.paraY('절:4', 0) - 400); true`);
  await wait(1200);                                     // 처음 맞추기·끌어오기가 끝나게
  const snap = async () => ({b: await B(`${F}.caretY()`), bTop: await B(`${F}.top()`), c: await C(`${F}.paraY('절:4', 0)`), cTop: await C(`${F}.top()`)});
  const synced = async () => { const t = await A(`${F}.field('절:1')`); for (let i = 0; i < 200; i++) { if (await B(`${F}.field('절:1')`) === t && await C(`${F}.field('절:1')`) === t) return true; await wait(50); } return false; };
  const results = {};
  for (const phase of ['grow', 'shrink']) {
    const s0 = await snap();
    await A(`${F}.endOf('절:1', 1); true`);
    const keys = phase === 'grow'
      ? [...'가나다라마바사아자차카타파하 '.repeat(5).slice(0, 69), 'Enter', ...'새 문단 추가 글자입니다 '.repeat(6).slice(0, 69)]
      : Array(139).fill('Backspace');
    for (const k of keys) { await A(`${F}.type(${JSON.stringify(k)}); true`); await wait(25); }
    assert.equal(await synced(), true, `${phase}: 세 창 글이 같아야`);
    await wait(1500);                                   // 마지막 묶음 넣기 + 지키기(0.4초)가 끝나게
    const s1 = await snap();
    results[phase] = {B_caret: [s0.b, s1.b, +(s1.b - s0.b).toFixed(1)], B_scrollTop: [s0.bTop, s1.bTop], C_anchor: [s0.c, s1.c, +(s1.c - s0.c).toFixed(1)], C_scrollTop: [s0.cTop, s1.cTop]};
  }
  results.grew = initial['절:1'].length;
  console.log(JSON.stringify({wrapper: OLD || 'working tree', zoom: ZOOM, results}, null, 1));
  if (!OLD) {
    for (const phase of ['grow', 'shrink']) {
      assert.ok(Math.abs(results[phase].B_caret[2]) <= 3, `${phase}: B 커서 높이 차 ${results[phase].B_caret[2]}px`);
      assert.ok(Math.abs(results[phase].C_anchor[2]) <= 3, `${phase}: C 보던 자리 차 ${results[phase].C_anchor[2]}px`);
    }
    assert.ok(results.grow.B_scrollTop[1] > results.grow.B_scrollTop[0], 'B 는 위쪽이 늘어난 만큼 아래로 굴러야');
    console.log('coedit view browser: ok');
  }
} finally {
  ws?.close(); chrome.kill(); server.close();
}
