(() => {
  const root = document.getElementById('chatWorkspace');
  if (!root) return;
  const form = document.getElementById('chatForm');
  const input = document.getElementById('chatInput');
  const file = document.getElementById('chatFile');
  const send = document.getElementById('sendBtn');
  const status = document.getElementById('chatSendState');
  const hint = document.getElementById('dockHint');
  const scroller = document.getElementById('chatScroll');
  const latest = document.getElementById('chatLatest');
  const editNote = document.getElementById('chatEditNote');
  let waiting = root.dataset.waiting === 'true';
  let sending = false;
  let timer;
  let previousInput = null;
  let mustAttach = false;
  function resize() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 190) + 'px';
  }
  function paint() {
    send.disabled = sending || waiting || !input.value.trim();
    send.textContent = sending ? '전송 중…' : '보내기';
    form.setAttribute('aria-busy', String(sending));
  }
  function notify(text) { hint.textContent = text; hint.hidden = false; }
  function bottom() { scroller.scrollTop = scroller.scrollHeight; latest.hidden = true; }
  function nearBottom() { return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 100; }
  function installPage(html, url, forceScroll = false) {
    const parsed = new DOMParser().parseFromString(html, 'text/html');
    const next = parsed.getElementById('chatWorkspace');
    const nextScroll = parsed.getElementById('chatScroll');
    const target = new URL(url, location.href);
    if (!next || !nextScroll || target.origin !== location.origin || !/^\/chat\/\d+$/.test(target.pathname)) {
      throw new Error('invalid chat response');
    }
    const shouldScroll = forceScroll || nearBottom();
    const position = scroller.scrollTop;
    scroller.innerHTML = nextScroll.innerHTML;
    const sources = parsed.getElementById('chatSources');
    if (sources) document.getElementById('chatSources').innerHTML = sources.innerHTML;
    root.dataset.session = next.dataset.session;
    waiting = next.dataset.waiting === 'true';
    root.dataset.waiting = String(waiting);
    let sessionField = form.querySelector('[name="session_id"]');
    if (!sessionField) {
      sessionField = document.createElement('input');
      sessionField.type = 'hidden'; sessionField.name = 'session_id'; form.appendChild(sessionField);
    }
    sessionField.value = next.dataset.session;
    history.replaceState(null, '', target.pathname);
    try {
      sessionStorage.setItem('agentSession', next.dataset.session);
      const criteria = [...form.querySelectorAll('[name="criteria"]')].map(item => Number(item.value));
      sessionStorage.setItem('chatCriteria:' + next.dataset.session, JSON.stringify(criteria));
      sessionStorage.removeItem('chatCriteria:new');
    } catch (_) {}
    if (shouldScroll) bottom();
    else { scroller.scrollTop = position; latest.hidden = false; }
    status.textContent = waiting ? '답변을 기다리는 동안 다음 질문을 작성할 수 있습니다.' : '답변을 확인하고 이어서 질문하세요.';
    paint();
    clearTimeout(timer);
    if (waiting) timer = setTimeout(poll, 2000);
  }
  async function poll() {
    if (!waiting || sending) return;
    const session = root.dataset.session;
    if (!session) return;
    try {
      const response = await fetch('/chat/' + session + '/status', {cache:'no-store'});
      if (!response.ok || response.redirected) throw new Error('status failed');
      const state = await response.json();
      if (typeof state.waiting !== 'boolean') throw new Error('invalid status');
      if (!state.waiting) {
        const page = await fetch('/chat/' + session, {cache:'no-store'});
        if (!page.ok) throw new Error('page failed');
        installPage(await page.text(), page.url);
        return;
      }
      status.textContent = '답변 작성 중입니다. 다음 질문을 미리 적어 두세요.';
    } catch (_) {
      status.textContent = '연결을 다시 확인하고 있습니다. 작성 중인 내용은 유지됩니다.';
    }
    timer = setTimeout(poll, 4000);
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (sending || waiting) return;
    if (!input.value.trim()) { notify('보낼 내용을 입력하세요.'); input.focus(); return; }
    if (mustAttach && !file.files.length) {
      notify('이전 질문에 첨부한 파일은 자동으로 다시 전송되지 않습니다. 파일을 다시 선택해 주세요.');
      document.getElementById('attachButton').focus(); return;
    }
    sending = true;
    hint.hidden = true;
    paint();
    const sentText = input.value;
    const sentFile = file.files[0];
    const payload = new FormData(form);
    try {
      const response = await fetch(form.action, {method:'POST', body:payload});
      if (!response.ok) {
        notify(response.status === 400 ? '파일 형식과 입력 내용을 확인한 뒤 다시 보내세요.' : '전송하지 못했습니다. 입력을 유지했으니 잠시 후 다시 보내세요.');
        return;
      }
      installPage(await response.text(), response.url, true);
      if (input.value === sentText) input.value = '';
      if (file.files[0] === sentFile) { file.value = ''; file.dispatchEvent(new Event('change')); }
      previousInput = null;
      mustAttach = false;
      editNote.hidden = true;
      resize();
      input.focus();
    } catch (_) {
      notify('전송 결과를 확인하지 못했습니다. 입력은 유지됩니다. 대화 기록에서 전송 여부를 확인한 뒤 다시 보내세요.');
    } finally {
      sending = false;
      paint();
      if (waiting) { clearTimeout(timer); timer = setTimeout(poll, 2000); }
    }
  });
  input.addEventListener('input', () => { paint(); resize(); });
  document.getElementById('attachButton').addEventListener('click', () => file.click());
  scroller.addEventListener('click', event => {
    const button = event.target.closest('[data-edit-question],[data-resend-question]');
    if (!button) return;
    if (sending) { notify('전송이 끝난 뒤 질문을 수정해 주세요.'); return; }
    if (input.value.trim() && !confirm('작성 중인 질문을 이전 질문으로 바꿀까요? 취소 버튼으로 되돌릴 수 있습니다.')) return;
    if (previousInput === null) previousInput = input.value;
    let question = button.dataset.editQuestion ?? button.dataset.resendQuestion;
    mustAttach = question.startsWith('[첨부]') && question.includes('\n');
    if (mustAttach) question = question.substring(question.indexOf('\n') + 1);
    input.value = question;
    document.getElementById('chatEditText').textContent = mustAttach
      ? '첨부 파일을 다시 선택하고 보내세요. 이전 대화는 유지됩니다.'
      : '질문을 확인한 뒤 보내세요. 이전 대화는 유지됩니다.';
    editNote.hidden = false;
    hint.hidden = true;
    paint(); resize(); input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  });
  document.getElementById('chatEditCancel').addEventListener('click', () => {
    input.value = previousInput ?? '';
    previousInput = null; mustAttach = false; editNote.hidden = true;
    paint(); resize(); input.focus();
  });
  // 시작 제안은 기존 템플릿에서 입력을 채운다. 버튼 상태만 동기화한다.
  document.querySelectorAll('[data-fill]').forEach(button => button.addEventListener('click', paint));
  latest.addEventListener('click', bottom);
  scroller.addEventListener('scroll', () => { if (nearBottom()) latest.hidden = true; });
  window.addEventListener('beforeunload', event => {
    if (input.value.trim() || file.files.length || sending) {
      event.preventDefault(); event.returnValue = '';
    }
  });
  paint();
  if (waiting) timer = setTimeout(poll, 2000);
})();
