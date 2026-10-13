// 「필요한 값 입력」·새 대화 문서 패널 — 실제 화면(합성 대화)을 헤드리스 크롬으로 연다.
// 실행: PYTHON=.venv/bin/python node tests/chat_value_form_browser.mjs   (저장소 루트에서)
// 보는 것: 붙여 넣기 나눠 채우기 · 모름·나중에 · 비운 칸 안 보냄 · 키보드 이동(Enter/Shift+Enter/Esc) · 저장 뒤 접힘,
//          새 대화에서 실제 마우스로 연 문서 패널이 화면 안(전환이 멈춰도)이고 대화가 있는 화면과 같은 폭인지.
import {spawn} from 'node:child_process';
import {existsSync} from 'node:fs';
import assert from 'node:assert/strict';

const port = 18000 + (process.pid % 1000);
const python = process.env.PYTHON || (existsSync('.venv/bin/python') ? '.venv/bin/python' : 'python3');
const seed = `
import sys, json, tempfile, pathlib
sys.path[:0] = ['src', '.']
import uvicorn
from tests.test_app import FakeDrafter, FakeProcessor, FakeResponder
from zzaimy.app import main, asks
tmp = pathlib.Path(tempfile.mkdtemp())
app = main.create_app(db_path=tmp/'c.db', inbox_dir=tmp/'inbox', processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
db = app.state.db
sid = db.create_chat_session(title='합성 대화', project_id=None, owner='zzaimy')
db.add_chat(sid, 'user', '합성 요청')
db.add_chat(sid, 'assistant', '빈 값이 있습니다. 아래 「필요한 값 입력」에 적어 주세요.')
asks.set_pending(db, sid, [{'name': n, 'hint': h} for n, h in [('가상 이름', '표지에 들어갈 이름'), ('가상 책임자', ''),
    ('가상 금액', '아주 긴 설명 ' * 20), ('가상 기간', '협약 기간'), ('가상 수량', ''), ('가상 목표', '정량 목표')]])
db.set_setting(f'chat_facts:{sid}:suggested', json.dumps({'가상 기간': {'value': '2099. 1. 1. ~ 2099. 12. 31.', 'title': '가상 공고'}}, ensure_ascii=False))
print('ready', flush=True)
uvicorn.run(app, host='127.0.0.1', port=${port}, log_level='warning')
`;
const server = spawn(python, ['-c', seed], {stdio:['ignore', 'pipe', 'pipe'], env:{...process.env, PYTHONPATH:'src'}});
await new Promise((resolve, reject) => {
  server.stdout.on('data', b => { if (String(b).includes('ready')) setTimeout(resolve, 1500); });
  server.on('exit', code => reject(Error('server exit ' + code)));
  setTimeout(() => reject(Error('server startup')), 60000).unref();
});
const chrome = spawn(process.env.CHROME_BIN || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ['--headless', '--disable-gpu', '--no-first-run', '--remote-debugging-port=0', '--user-data-dir=/private/tmp/aura-value-form-' + process.pid, 'about:blank'],
  {stdio:['ignore', 'ignore', 'pipe']});
let ws;
try {
  const url = await new Promise((resolve, reject) => { let out = ''; chrome.stderr.on('data', b => { out += b; const m = out.match(/DevTools listening on (ws:\/\/\S+)/); if (m) resolve(m[1]); }); setTimeout(() => reject(Error('chrome')), 15000).unref(); });
  ws = new WebSocket(url); await new Promise(r => ws.addEventListener('open', r, {once:true}));
  let id = 0, session; const pending = new Map(); const errors = [];
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(Error(JSON.stringify(m.error))) : p.resolve(m.result); }
    if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text); };
  const send = (method, params = {}) => new Promise((resolve, reject) => { const i = ++id; pending.set(i, {resolve, reject}); ws.send(JSON.stringify({id:i, method, params, ...(session ? {sessionId:session} : {})})); });
  const t = await send('Target.createTarget', {url:'about:blank'}); session = (await send('Target.attachToTarget', {targetId:t.targetId, flatten:true})).sessionId;
  await send('Runtime.enable'); await send('Page.enable'); await send('Animation.enable');
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const evaluate = async expression => { const r = await send('Runtime.evaluate', {expression, awaitPromise:true, returnByValue:true}); if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value; };
  const open = async (path, width) => { await send('Emulation.setDeviceMetricsOverride', {width, height:1000, deviceScaleFactor:1, mobile:false}); await send('Page.navigate', {url:`http://127.0.0.1:${port}${path}`}); await wait(1500); };
  const mouse = async (selector, modifiers = 0) => {   // 실제 마우스 눌림(Input.dispatchMouseEvent) — el.click() 과 달리 겹친 요소·pointerdown 처리기를 거친다
    const b = await evaluate(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
    for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', {type, x:b.x, y:b.y, button:'left', clickCount:1, modifiers});
  };
  const key = async (k, shift = false) => { const code = {Enter:13, Escape:27}[k];
    await send('Input.dispatchKeyEvent', {type:'keyDown', key:k, code:k, windowsVirtualKeyCode:code, modifiers:shift ? 8 : 0, ...(k === 'Enter' ? {text:'\r'} : {})});
    await send('Input.dispatchKeyEvent', {type:'keyUp', key:k, code:k, windowsVirtualKeyCode:code, modifiers:shift ? 8 : 0}); };
  const F = name => `document.querySelector('#chatValueForm [data-field="${name}"]')`;

  // ── 값 폼 ──
  await open('/chat/1', 1440);
  assert.equal(await evaluate(`document.querySelector('[data-form-count]').textContent`), '6개 중 1개 채움');
  assert.equal(await evaluate(`document.querySelector('[data-form-submit]').textContent`), '1개 확인');
  // 한 열 목록 — 칸마다 한 줄(이름 칸·입력 칸이 같은 줄), 긴 설명은 한 줄 말줄임 + 툴팁
  const rows = await evaluate(`[...document.querySelectorAll('#chatValueForm .chat-form-field')].map(li=>{const n=li.querySelector('.chat-form-label').getBoundingClientRect(),a=li.querySelector('textarea').getBoundingClientRect();return {sameRow:Math.abs(n.top-a.top)<12, right:a.left>n.right}})`);
  assert.ok(rows.length === 6 && rows.every(r => r.sameRow && r.right), JSON.stringify(rows));
  const longHint = await evaluate(`(()=>{const h=document.querySelectorAll('#chatValueForm .chat-form-hint')[2];return {one:h.getBoundingClientRect().height<24,title:h.title.length>100}})()`);
  assert.deepEqual(longHint, {one:true, title:true});
  // 입력은 한 줄 높이에서 시작
  assert.ok(await evaluate(`${F('가상 이름')}.getBoundingClientRect().height<=40`));
  // 붙여 넣기 — 「이름: 값」 여러 줄을 이름 맞는 칸에 나눠 채운다(물은 적 없는 이름은 넣지 않는다)
  await evaluate(`(()=>{const a=${F('가상 이름')};a.focus();const dt=new DataTransfer();dt.setData('text/plain','가상 이름: 합성 사업단\\n가상 책임자는 합성인입니다\\n가상 수량: 4\\n없는 이름: 무시');a.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}))})()`);
  assert.deepEqual(await evaluate(`[${F('가상 이름')}.value, ${F('가상 책임자')}.value, ${F('가상 수량')}.value, ${F('가상 목표')}.value]`), ['합성 사업단', '합성인', '4', '']);
  assert.match(await evaluate(`document.querySelector('[data-form-status]').textContent`), /3개를 칸에 나눠 채움 · 맞는 칸이 없는 1줄/);
  assert.equal(await evaluate(`document.querySelectorAll('#chatValueForm .chat-form-field.is-filled').length`), 4);
  // 값 하나만 붙여 넣으면 보통 붙여 넣기(가로채지 않는다)
  assert.equal(await evaluate(`(()=>{const dt=new DataTransfer();dt.setData('text/plain','그냥 값');const e=new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true});${F('가상 목표')}.dispatchEvent(e);return e.defaultPrevented})()`), false);
  // 키보드 — Enter 다음 칸, Shift+Enter 는 줄바꿈(가로채지 않음), Esc 는 값 그대로 칸에서 빠짐, 마지막 칸 Enter 는 저장
  await evaluate(`${F('가상 이름')}.focus()`); await key('Enter');
  assert.equal(await evaluate(`document.activeElement.dataset.field`), '가상 책임자');
  assert.equal(await evaluate(`(()=>{const e=new KeyboardEvent('keydown',{key:'Enter',shiftKey:true,bubbles:true,cancelable:true});document.activeElement.dispatchEvent(e);return e.defaultPrevented})()`), false);
  await key('Escape');
  assert.equal(await evaluate(`document.activeElement.tagName`), 'BODY');
  assert.equal(await evaluate(`${F('가상 책임자')}.value`), '합성인');
  // 모름·나중에 — 실제 마우스로 라벨을 누른다. 칸은 잠기고 수·단추 글이 바뀐다
  await mouse('#chatValueForm li:nth-child(3) .chat-form-later');
  assert.equal(await evaluate(`${F('가상 금액')}.disabled`), true);
  assert.equal(await evaluate(`document.querySelector('[data-form-count]').textContent`), '6개 중 4개 채움 · 나중에 1개');
  assert.equal(await evaluate(`document.querySelector('[data-form-submit]').textContent`), '4개 저장 · 1개 나중에');
  // 자료 값 「확인」 — 확인함 표시, 다음 빈 칸으로
  await mouse('#chatValueForm [data-accept-value]');
  assert.match(await evaluate(`document.querySelector('[data-source-state]').textContent`), /^자료에서 찾음 · 확인함/);
  assert.equal(await evaluate(`document.activeElement.dataset.field`), '가상 목표');
  // 저장 — 마지막 칸에서 Enter. 보내는 값을 가로채 본다: 비운 칸은 빠지고 미룬 값은 later 로
  await evaluate(`window.__sent=null;const real=window.fetch;window.fetch=async(u,o)=>{if(o&&o.method==='POST'){window.__sent=[...o.body.entries()].map(([k,v])=>k+'='+v);return real('/chat/1');}return real(u,o)}`);
  await evaluate(`${F('가상 목표')}.focus()`); await key('Enter');
  assert.deepEqual(await evaluate(`[document.querySelector('#chatValueForm')?.classList.contains('is-sent'), document.querySelector('.chat-form-sent')?.textContent]`), [true, '4개 저장 — 에이전트가 반영 중']);
  await wait(800);
  const sent = await evaluate('window.__sent');
  assert.ok(sent.includes('field_가상 이름=합성 사업단') && sent.includes('later=가상 금액') && sent.includes('field_가상 기간=2099. 1. 1. ~ 2099. 12. 31.'), sent.join(' | '));
  assert.ok(!sent.some(x => x.startsWith('field_가상 목표') || x.startsWith('field_가상 금액')), sent.join(' | '));
  // 800px 에서도 한 줄 목록(이름 위, 칸·나중에 아래)이 칸 밖으로 넘치지 않는다
  await open('/chat/1', 800);
  assert.equal(await evaluate(`document.documentElement.scrollWidth<=innerWidth && [...document.querySelectorAll('#chatValueForm textarea')].every(t=>t.getBoundingClientRect().right<=document.querySelector('#chatValueForm').getBoundingClientRect().right)`), true);

  // 줄 고르기 — 실제 마우스로 줄 앞 체크, Shift+체크로 범위, 고른 줄에 나중에 ↔ 취소, 전체 고르기·풀기
  await open('/chat/1', 1440);
  const tools = () => evaluate(`[...document.querySelectorAll('#chatValueForm [data-bulk]')].filter(b=>!b.hidden).map(b=>b.textContent)`);
  const pickRow = n => `#chatValueForm li:nth-child(${n}) [data-pick]`;
  assert.deepEqual(await tools(), ['빈 칸 5개 나중에', '빈 칸 5개 자료에서 찾기']);
  await mouse(pickRow(2)); await mouse(pickRow(5), 8);
  assert.deepEqual(await evaluate(`[...document.querySelectorAll('#chatValueForm [data-pick]')].map(b=>b.checked)`), [false, true, true, true, true, false]);
  assert.deepEqual(await evaluate(`(()=>{const a=document.querySelector('[data-pick-all]');return [a.checked,a.indeterminate]})()`), [false, true]);
  assert.deepEqual(await tools(), ['고른 4개 나중에', '고른 3개 자료에서 찾기', '고르기 풀기']);
  await mouse('#chatValueForm [data-bulk=later]');
  assert.equal(await evaluate(`document.querySelector('[data-form-count]').textContent`), '6개 중 0개 채움 · 나중에 4개');
  assert.equal(await evaluate(`document.querySelectorAll('#chatValueForm [data-pick]:checked').length`), 0);
  await mouse(pickRow(2)); await mouse(pickRow(3));
  assert.deepEqual(await tools(), ['고른 2개 나중에 취소', '고른 2개 자료에서 찾기', '고르기 풀기']);
  await mouse('#chatValueForm [data-bulk=later]');
  assert.equal(await evaluate(`document.querySelector('[data-form-count]').textContent`), '6개 중 0개 채움 · 나중에 2개');
  assert.match(await evaluate(`document.querySelector('[data-form-status]').textContent`), /2개를 다시 지금 받기로 되돌림/);
  await mouse('#chatValueForm [data-pick-all]');
  assert.equal(await evaluate(`document.querySelectorAll('#chatValueForm [data-pick]:checked').length`), 6);
  await mouse('#chatValueForm [data-bulk=clear]');
  assert.deepEqual(await evaluate(`[document.querySelectorAll('#chatValueForm [data-pick]:checked').length, document.querySelector('[data-pick-all]').checked]`), [0, false]);
  // 채운 줄은 오른쪽에 「채움」, 「나중에」 단추는 숨김
  await open('/chat/1', 1440);
  assert.deepEqual(await evaluate(`(()=>{const li=document.querySelector('#chatValueForm li.is-filled');return [getComputedStyle(li.querySelector('.chat-form-done')).display, getComputedStyle(li.querySelector('.chat-form-later')).display]})()`), ['flex', 'none']);

  // ── 새 대화 문서 패널 ──
  const panelBox = `(()=>{const p=document.getElementById('chatDocumentPanel'),r=p.getBoundingClientRect(),c=p.querySelector('[data-close]').getBoundingClientRect();return {open:document.getElementById('chatDocumentOpen').getAttribute('aria-expanded'),left:Math.round(r.left),width:Math.round(r.width),inside:r.right<=innerWidth+0.5&&c.right<=innerWidth}})()`;
  const boxes = {};
  for (const path of ['/chat', '/chat/1']) {
    await open(path, 1710);
    await send('Animation.setPlaybackRate', {playbackRate:0});   // 뒤에 있는 탭처럼 전환이 멈춰도 패널은 화면 안에
    await mouse('#chatDocumentOpen'); await wait(500);
    boxes[path] = await evaluate(panelBox);
    assert.deepEqual({open:boxes[path].open, inside:boxes[path].inside}, {open:'true', inside:true}, path + JSON.stringify(boxes[path]));
    await send('Animation.setPlaybackRate', {playbackRate:1});
  }
  assert.equal(boxes['/chat'].width, boxes['/chat/1'].width); assert.equal(boxes['/chat'].left, boxes['/chat/1'].left);
  // 「드라이브에서 가져오기」 도 화면 안
  assert.ok(await evaluate(`(()=>{const b=[...document.querySelectorAll('#chatDocumentPanel button')].find(x=>x.textContent.includes('드라이브에서 가져오기'));return b&&b.getBoundingClientRect().right<=innerWidth})()`));
  assert.deepEqual(errors, []);
  console.log('chat value form browser: ok');
} finally {
  ws?.close(); chrome.kill(); server.kill();
}
