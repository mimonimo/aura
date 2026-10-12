// 공용 확인창(workspace-navigation.js) 브라우저 검사 — 헤드리스 크롬에서 data-confirm 폼이 플랫폼 대화상자를 띄우고
// 확인·취소·Esc·바깥 누름·두 번 제출 막기·누른 단추 값·위험 동작 빨강·data-own-confirm 건너뛰기가 실제로 동작하는지 본다.
// 실행: node tests/confirm_dialog_browser.mjs  (저장소 뿌리에서, 크롬 경로는 CHROME_BIN 로 바꿀 수 있다)
import {spawn} from 'node:child_process';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

const root = process.cwd() + '/src/zzaimy/app/static/';
const js = await readFile(root + 'workspace-navigation.js', 'utf8');
const css = await readFile(root + 'workspace-shell.css', 'utf8');
const posts = [];
const page = `<!doctype html><html><head><meta charset="utf-8"><style>
:root{--ink:#1F2937;--line:#E2E8F0;--navy-deep:#14325A;--red:#B3312F;--panel:#F8FAFC}${css}</style></head><body>
<form id="run" action="/run" method="post" data-confirm="시험 작업을 실행할까요?">
  <input type="hidden" name="item" value="7">
  <button type="submit" name="mode" value="a" id="runA">가 방식 실행</button>
  <button type="submit" name="mode" value="b" id="runB">나 방식 실행</button>
</form>
<form id="del" action="/items/3/delete" method="post"><button type="submit" id="delBtn">항목 삭제</button></form>
<form id="danger" action="/link/revoke" method="post" data-confirm="연결을 해제할까요?" data-confirm-danger><button id="dangerBtn">연결 해제</button></form>
<form id="sink" action="/sink" method="post" target="sinkFrame" data-confirm="내려받을까요?"><button id="sinkBtn">내려받기</button></form>
<iframe name="sinkFrame" hidden></iframe>
<form id="own" action="/own" method="post" data-confirm="자체 확인" data-own-confirm><button id="ownBtn">자체 확인 화면</button></form>
<script src="/nav.js" defer></script></body></html>`;
const server = createServer((req, res) => {
  if (req.method === 'POST') {
    let body = '';
    req.on('data', b => { body += b; });
    // 응답을 늦춰 「제출이 떠난 뒤 다시 누름」 구간을 만든다
    req.on('end', () => { posts.push({path: req.url, body}); setTimeout(() => { res.writeHead(200, {'content-type': 'text/html; charset=utf-8'}); res.end('<p id="done">완료</p>'); }, 300); });
    return;
  }
  if (req.url === '/nav.js') { res.writeHead(200, {'content-type': 'text/javascript'}); res.end(js); return; }
  res.writeHead(200, {'content-type': 'text/html; charset=utf-8'}); res.end(page);
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const base = `http://127.0.0.1:${server.address().port}`;

const chrome = spawn(process.env.CHROME_BIN || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ['--headless', '--disable-gpu', '--no-first-run', '--remote-debugging-port=0', '--user-data-dir=/private/tmp/aura-confirm-browser', 'about:blank'],
  {stdio: ['ignore', 'ignore', 'pipe']});
let ws;
try {
  const url = await new Promise((resolve, reject) => {
    let out = '';
    chrome.stderr.on('data', b => { out += b; const m = out.match(/DevTools listening on (ws:\/\/\S+)/); if (m) resolve(m[1]); });
    setTimeout(() => reject(Error('startup')), 15000).unref();
  });
  ws = new WebSocket(url); await new Promise(r => ws.addEventListener('open', r, {once: true}));
  let id = 0, session; const pending = new Map();
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(m.error) : p.resolve(m.result); } };
  const send = (method, params = {}) => new Promise((resolve, reject) => { const i = ++id; pending.set(i, {resolve, reject}); ws.send(JSON.stringify({id: i, method, params, ...(session ? {sessionId: session} : {})})); });
  const t = await send('Target.createTarget', {url: 'about:blank'});
  session = (await send('Target.attachToTarget', {targetId: t.targetId, flatten: true})).sessionId;
  await send('Emulation.setDeviceMetricsOverride', {width: 1200, height: 800, deviceScaleFactor: 1, mobile: false});
  await send('Page.enable'); await send('Page.bringToFront'); await send('Emulation.setFocusEmulationEnabled', {enabled: true});
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const evaluate = async expression => { const r = await send('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true}); if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value; };
  const open = async () => { await send('Page.navigate', {url: base + '/'}); await wait(500); };
  const dialog = () => evaluate(`(()=>{const d=document.querySelector('dialog.confirm-dialog');if(!d)return null;const a=d.querySelector('[data-confirm-accept]');
    return {open:d.open,modal:d.matches(':modal'),title:d.querySelector('h2').textContent,text:d.querySelector('p').textContent,
      ok:a.textContent,danger:a.classList.contains('danger'),focus:document.activeElement===a,
      red:getComputedStyle(a).backgroundColor}})()`);
  // 닫힘(close 이벤트 뒤 대화상자 제거)은 다음 작업에서 일어난다 — 잠깐씩 다시 본다
  const closed = async () => { for (let i = 0; i < 40; i++) { if (await dialog() === null) return true; await wait(50); } return false; };
  const key = async k => { for (const type of ['keyDown', 'keyUp']) await send('Input.dispatchKeyEvent', {type, key: k, code: k, windowsVirtualKeyCode: 27}); };
  const mouse = async (x, y) => { for (const type of ['mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', {type, x, y, button: 'left', clickCount: 1}); };

  // 1) data-confirm 폼 — 브라우저 기본 창이 아니라 플랫폼 대화상자, 제목은 누른 단추, 확인 단추에 초점, 위험 아님
  await open();
  await evaluate(`window.confirm=()=>{throw Error('기본 confirm 이 불렸다')};document.getElementById('runB').click()`);
  let d = await dialog();
  assert.deepEqual({open: d.open, modal: d.modal, title: d.title, text: d.text, ok: d.ok, danger: d.danger, focus: d.focus},
    {open: true, modal: true, title: '나 방식 실행', text: '시험 작업을 실행할까요?', ok: '확인', danger: false, focus: true});
  // Esc = 취소
  await key('Escape');
  assert.equal(await closed(), true); assert.equal(posts.length, 0);
  // 바깥(배경) 누름 = 취소
  await evaluate(`document.getElementById('runB').click()`); await mouse(8, 8);
  assert.equal(await closed(), true); assert.equal(posts.length, 0);
  // 「취소」 = 취소, 초점은 누른 단추로 돌아온다
  await evaluate(`document.getElementById('runB').click()`);
  await evaluate(`document.querySelector('[data-confirm-cancel]').click()`);
  assert.equal(await closed(), true);
  assert.equal(await evaluate(`document.activeElement.id`), 'runB'); assert.equal(posts.length, 0);
  // 대화상자가 떠 있는 동안 다시 눌러도 하나만
  await evaluate(`document.getElementById('runB').click();document.getElementById('runA').click()`);
  assert.equal(await evaluate(`document.querySelectorAll('dialog.confirm-dialog').length`), 1);
  // 「확인」 → 누른 단추의 name/value 가 실린 원래 제출 하나(닫히는 사이의 거듭 누름은 막힌다)
  await evaluate(`document.querySelector('[data-confirm-accept]').click();document.getElementById('runB').click();document.getElementById('runA').click()`);
  for (let i = 0; i < 120 && !posts.length; i++) await wait(25);
  await wait(300);
  assert.equal(posts.length, 1);
  assert.equal(posts[0].path, '/run');
  assert.deepEqual(Object.fromEntries(new URLSearchParams(posts[0].body)), {item: '7', mode: 'b'});
  assert.equal(await evaluate(`Boolean(document.getElementById('done'))`), true);

  // 2) 삭제 경로는 data-confirm 이 없어도 묻고, 확인 단추가 빨강
  await open();
  await evaluate(`document.getElementById('delBtn').click()`);
  d = await dialog();
  assert.equal(d.title, '항목 삭제'); assert.equal(d.text, '이 항목을 삭제할까요?'); assert.equal(d.danger, true);
  assert.equal(d.red, 'rgb(179, 49, 47)');
  await evaluate(`document.querySelector('[data-confirm-cancel]').click()`); assert.equal(await closed(), true); assert.equal(posts.length, 1);

  // 3) data-confirm-danger 도 빨강, 확인하면 제출
  await evaluate(`document.getElementById('dangerBtn').click()`);
  d = await dialog(); assert.equal(d.danger, true); assert.equal(d.text, '연결을 해제할까요?');
  await evaluate(`document.querySelector('[data-confirm-accept]').click()`); await wait(800);
  assert.equal(posts.length, 2); assert.equal(posts[1].path, '/link/revoke');

  // 3-1) 화면이 바뀌지 않는 제출(다른 창으로 가는 제출) — 확인한 제출이 떠난 뒤 거듭 누름은 묻지도 보내지도 않는다
  await open();
  await evaluate(`document.getElementById('sinkBtn').click()`);
  await evaluate(`document.querySelector('[data-confirm-accept]').click()`); assert.equal(await closed(), true);
  for (let i = 0; i < 40 && posts.length < 3; i++) await wait(25);
  assert.equal(posts.length, 3); assert.equal(posts[2].path, '/sink');
  await evaluate(`document.getElementById('sinkBtn').click()`);
  assert.equal(await evaluate(`document.querySelectorAll('dialog.confirm-dialog').length`), 0);
  await wait(600); assert.equal(posts.length, 3);

  // 4) data-own-confirm 은 공용 확인창을 건너뛴다(그 화면이 스스로 묻는다)
  await open();
  await evaluate(`document.getElementById('ownBtn').click()`); await wait(800);
  assert.equal(posts.length, 4); assert.equal(posts[3].path, '/own');
  console.log('confirm dialog browser: ok');
} finally {
  ws?.close(); chrome.kill(); server.close();
}
