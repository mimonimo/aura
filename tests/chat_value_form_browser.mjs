// 「필요한 값 입력」·새 대화 문서 패널 — 실제 화면(합성 대화)을 헤드리스 크롬으로 연다.
// 실행: PYTHON=.venv/bin/python node tests/chat_value_form_browser.mjs   (저장소 루트에서)
// 보는 것: 접지 않은 카드 · 붙여 넣기 나눠 채우기 · 자료 칩 · 모름·나중에 · 비운 칸 안 보냄 · 키보드 이동(Enter/Shift+Enter/Esc) · 좁은 폭,
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
from zzaimy.app import chat_preview as cp
sid2 = db.create_chat_session(title='합성 선택', project_id=None, owner='zzaimy')
db.add_chat(sid2, 'user', '합성 표 넣어 줘')
mid2 = db.add_chat(sid2, 'assistant', '합성 표를 넣었습니다.')
row = lambda o: {"type": "table", "head": [["구분", "내용"]], "rows": [["합성 가", "합성 나"]] * 6, "style": {"head_fill": "#DCE6F2" if o == "열" else "#E5E5E5"}, "layout": {"orient": o, "total": True, "place": "이 절 안"}}
card, _ = cp.norm_card([{"header": "표 배치", "question": "다른 배치로 바꿀까요?", "options": [{"label": "행 배치", "description": "합성 설명", "preview": row("행")}, {"label": "열 배치", "preview": row("열")}]},
                        {"header": "글꼴", "question": "본문 글꼴", "options": [{"label": "합성 바탕", "preview": {"type": "font", "font": "없는합성글꼴", "pt": 11}}, {"label": "합성 돋움", "preview": {"type": "font", "font": "Arial", "pt": 12, "bold": True}}]}], "change")
cp.save_card(db, int(mid2), card)
cp.save_done(db, int(mid2), [{"what": "「합성 절」 표", "preview": cp.norm_preview(row("행"))}, {"what": "글자 모양(문서 전체)", "preview": cp.norm_preview({"type": "font", "font": "Arial", "pt": 11})}])
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

  // ── 값 입력 카드 ──
  await open('/chat/1', 1440);
  // 접지 않은 카드 하나 — 여는 단추·줄 체크 없이 바로 쓴다
  assert.deepEqual(await evaluate(`[document.querySelector('#chatValueForm').classList.contains('is-folded'), getComputedStyle(document.querySelector('#chatValueForm .chat-form-list')).display, document.querySelectorAll('#chatValueForm [data-pick], #chatValueForm [data-form-toggle]').length]`), [false, 'block', 0]);
  assert.equal(await evaluate(`document.querySelector('[data-form-count]').textContent`), '6개 중 0개 채움');
  assert.equal(await evaluate(`document.querySelector('[data-form-submit]').textContent`), '모두 보내기');
  assert.equal(await evaluate(`document.querySelector('#chatValueForm [data-bulk=find]').textContent`), '빈 칸 6개 자료에서 찾기');
  // 한 열 목록 — 칸마다 한 줄(이름 칸·입력 칸이 같은 줄), 긴 설명은 한 줄 말줄임 + 툴팁
  const rows = await evaluate(`[...document.querySelectorAll('#chatValueForm .chat-form-field')].map(li=>{const n=li.querySelector('.chat-form-label').getBoundingClientRect(),a=(li.querySelector('.chat-form-typed')||li.querySelector('textarea')).getBoundingClientRect();return {sameRow:Math.abs(n.top-a.top)<14, right:a.left>n.right}})`);
  assert.ok(rows.length === 6 && rows.every(r => r.sameRow && r.right), JSON.stringify(rows));
  const longHint = await evaluate(`(()=>{const h=document.querySelectorAll('#chatValueForm .chat-form-hint')[2];return {one:h.getBoundingClientRect().height<24,title:h.title.length>100}})()`);
  assert.deepEqual(longHint, {one:true, title:true});
  assert.ok(await evaluate(`${F('가상 이름')}.getBoundingClientRect().height<=40`));
  // 붙여 넣기 — 「이름: 값」 여러 줄을 이름 맞는 칸에 나눠 채운다(물은 적 없는 이름은 넣지 않는다)
  await evaluate(`(()=>{const a=${F('가상 이름')};a.focus();const dt=new DataTransfer();dt.setData('text/plain','가상 이름: 합성 사업단\\n가상 책임자는 합성인입니다\\n가상 수량: 4\\n없는 이름: 무시');a.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}))})()`);
  assert.deepEqual(await evaluate(`[${F('가상 이름')}.value, ${F('가상 책임자')}.value, ${F('가상 수량')}.value, ${F('가상 목표')}.value]`), ['합성 사업단', '합성인', '4', '']);
  assert.match(await evaluate(`document.querySelector('[data-form-status]').textContent`), /3개를 칸에 나눠 채움 · 맞는 칸이 없는 1줄/);
  assert.equal(await evaluate(`document.querySelectorAll('#chatValueForm .chat-form-field.is-filled').length`), 3);
  assert.equal(await evaluate(`(()=>{const dt=new DataTransfer();dt.setData('text/plain','그냥 값');const e=new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true});${F('가상 목표')}.dispatchEvent(e);return e.defaultPrevented})()`), false);
  // 키보드 — Enter 다음 칸, Shift+Enter 는 줄바꿈(가로채지 않음), Esc 는 값 그대로 칸에서 빠짐
  await evaluate(`${F('가상 이름')}.focus()`); await key('Enter');
  assert.equal(await evaluate(`document.activeElement.dataset.field`), '가상 책임자');
  assert.equal(await evaluate(`(()=>{const e=new KeyboardEvent('keydown',{key:'Enter',shiftKey:true,bubbles:true,cancelable:true});document.activeElement.dispatchEvent(e);return e.defaultPrevented})()`), false);
  await key('Escape');
  assert.equal(await evaluate(`document.activeElement.tagName`), 'BODY');
  // 자료에서 찾은 값은 칩 — 누르면 칸(기간이면 달력 둘)에 들어가고 다음 빈 칸으로
  assert.equal(await evaluate(`${F('가상 기간')}.value`), '');
  await mouse('#chatValueForm [data-chip-value]');
  assert.equal(await evaluate(`${F('가상 기간')}.value`), '2099. 1. 1. ~ 2099. 12. 31.');
  assert.deepEqual(await evaluate(`[...document.querySelectorAll('#chatValueForm li[data-name="가상 기간"] [data-typed]')].map(i=>i.value)`), ['2099-01-01', '2099-12-31']);
  assert.match(await evaluate(`document.querySelector('[data-source-state]').textContent`), /보내면 확인/);
  assert.equal(await evaluate(`document.querySelector('#chatValueForm [data-chip-value]').classList.contains('is-on')`), true);
  // 모름 — 「확인 필요」 로, 나중에 — 이번에는 넘김(later 로 보냄). 둘은 함께 켜지지 않는다
  await mouse('#chatValueForm li[data-name="가상 금액"] [data-leave]');
  assert.equal(await evaluate(`${F('가상 금액')}.value`), '확인 필요');
  await mouse('#chatValueForm li[data-name="가상 금액"] [data-later]');
  assert.deepEqual(await evaluate(`[${F('가상 금액')}.value, document.querySelector('#chatValueForm li[data-name="가상 금액"] [data-leave]').getAttribute('aria-pressed'), document.querySelector('#chatValueForm li[data-name="가상 금액"] [data-later-input]').disabled]`), ['', 'false', false]);
  assert.equal(await evaluate(`document.querySelector('[data-form-count]').textContent`), '6개 중 4개 채움 · 나중에 1개');
  // 보내기 — 마지막 칸에서 Enter. 비운 칸은 빠지고 미룬 값은 later 로
  await evaluate(`window.__sent=null;const real=window.fetch;window.fetch=async(u,o)=>{if(o&&o.method==='POST'){window.__sent=[...o.body.entries()].map(([k,v])=>k+'='+v);return real('/chat/1');}return real(u,o)}`);
  await evaluate(`${F('가상 목표')}.focus()`); await key('Enter');
  assert.equal(await evaluate(`document.querySelector('#chatValueForm')?.classList.contains('is-sent')`), true);
  await wait(800);
  const sent = await evaluate('window.__sent');
  assert.ok(sent.includes('field_가상 이름=합성 사업단') && sent.includes('field_가상 기간=2099. 1. 1. ~ 2099. 12. 31.') && sent.includes('later=가상 금액'), sent.join(' | '));
  assert.ok(!sent.some(x => x.startsWith('field_가상 목표') || x.startsWith('field_가상 금액')), sent.join(' | '));
  // 800px·좁은 폭(390px)에서도 칸이 카드 밖으로 넘치지 않는다
  for (const w of [800, 390]) {
    await open('/chat/1', w);
    assert.equal(await evaluate(`document.documentElement.scrollWidth<=innerWidth && [...document.querySelectorAll('#chatValueForm textarea:not([hidden]), #chatValueForm .chat-form-typed, #chatValueForm [data-form-submit]')].every(t=>t.getBoundingClientRect().right<=document.querySelector('#chatValueForm').getBoundingClientRect().right+0.5)`), true, 'width ' + w);
  }

  // ── 선택 카드·반영 미리 보기 ──
  await open('/chat/2', 1440);
  assert.equal(await evaluate(`document.querySelectorAll('.cc-done .cc-done-item').length`), 2);
  assert.match(await evaluate(`document.querySelector('.cc-done .pv-table .pv-more').textContent`), /외 2행/);
  assert.match(await evaluate(`document.querySelector('[data-font-check="없는합성글꼴"]').textContent`), /비슷한 글꼴로 보임/);
  // 후보는 나란히(같은 줄) — 키보드: Tab 으로 들어가 화살표로 고른다
  assert.ok(await evaluate(`(()=>{const o=[...document.querySelectorAll('[data-choice-card] .cc-q:first-of-type .cc-opt')].map(x=>x.getBoundingClientRect().top);return Math.abs(o[0]-o[1])<2})()`));
  await evaluate(`document.querySelector('[data-choice-card] input[name=pick_0]').focus()`);
  await send('Input.dispatchKeyEvent', {type:'keyDown', key:'ArrowRight', code:'ArrowRight', windowsVirtualKeyCode:39});
  await send('Input.dispatchKeyEvent', {type:'keyUp', key:'ArrowRight', code:'ArrowRight', windowsVirtualKeyCode:39});
  assert.equal(await evaluate(`document.querySelector('[data-choice-card] input[name=pick_0]:checked')?.value`), '2');
  assert.equal(await evaluate(`document.querySelectorAll('[data-choice-card] .cc-opt.is-on').length`), 1);
  // 아무것도 고르지 않은 질문은 빼고, 고른 것만 보낸다
  await evaluate(`window.__sent=null;const real2=window.fetch;window.fetch=async(u,o)=>{if(o&&o.method==='POST'){window.__sent=[...o.body.entries()].map(([k,v])=>k+'='+v);return real2('/chat/2');}return real2(u,o)}`);
  await mouse('[data-choice-submit]'); await wait(800);
  const sent2 = await evaluate('window.__sent');
  assert.ok(sent2.includes('pick_0=2') && !sent2.some(x => x.startsWith('pick_1')) && sent2.some(x => x.startsWith('choice_mid=')), sent2.join(' | '));
  // 좁은 폭에서는 한 줄에 하나, 카드 밖으로 넘치지 않는다
  await open('/chat/2', 390);
  assert.ok(await evaluate(`document.documentElement.scrollWidth<=innerWidth && (()=>{const o=[...document.querySelectorAll('[data-choice-card] .cc-q:first-of-type .cc-opt')].map(x=>x.getBoundingClientRect());return o[1].top>o[0].bottom-1 && o.every(r=>r.right<=innerWidth)})()`));
  // 빈 선택으로 보내면 안내만
  await open('/chat/2', 1440);
  await mouse('[data-choice-submit]');
  assert.match(await evaluate(`document.querySelector('[data-choice-status]').textContent`), /고르거나 직접 입력/);

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
  // 「파일 가져오기」(부서 폴더·드라이브 파인더 창) 도 화면 안
  assert.ok(await evaluate(`(()=>{const b=[...document.querySelectorAll('#chatDocumentPanel button')].find(x=>x.textContent.includes('파일 가져오기'));return b&&b.getBoundingClientRect().right<=innerWidth})()`));
  assert.deepEqual(errors, []);
  console.log('chat value form browser: ok');
} finally {
  ws?.close(); chrome.kill(); server.kill();
}
