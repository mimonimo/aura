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
  const topbar = document.querySelector('.topbar');
  if (topbar) new ResizeObserver(() => {
    document.documentElement.style.setProperty('--workspace-topbar-height', topbar.offsetHeight + 'px');
  }).observe(topbar);
  let waiting = root.dataset.waiting === 'true';
  let sending = false;
  let timer;
  let previousInput = null;
  let mustAttach = false;
  let editor = null;
  try {
    const transfer = JSON.parse(sessionStorage.getItem('agentDraftTransfer') || 'null');
    sessionStorage.removeItem('agentDraftTransfer');
    if (transfer && String(transfer.session) === root.dataset.session) {
      input.value = (transfer.quote ? '「' + transfer.quote + '」\n' : '') + transfer.text;
      resize();
    }
  } catch (_) {}
  function resize() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 190) + 'px';
  }
  function paint() {
    send.disabled = sending || waiting || !input.value.trim();
    send.setAttribute('aria-label', sending ? '전송 중' : '보내기');
    send.title = sending ? '전송 중' : '보내기';
    send.querySelector('iconify-icon').setAttribute('icon', sending ? 'solar:hourglass-linear' : 'solar:arrow-up-linear');
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
    const heading = next.querySelector('.chat-heading');
    if (heading) root.querySelector('.chat-heading').replaceChildren(...heading.childNodes);
    const sessions = parsed.getElementById('chatSessionList');
    if (sessions) document.getElementById('chatSessionList').replaceChildren(...sessions.childNodes);
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
    status.textContent = waiting ? '답변 생성 중' : '';
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
      status.textContent = '답변 작성 중입니다.';
    } catch (_) {
      status.textContent = '연결을 다시 확인하고 있습니다. 작성 중인 내용은 유지됩니다.';
    }
    timer = setTimeout(poll, 4000);
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (sending || waiting) return;
    if (editor) { editor.querySelector('textarea').focus(); notify('질문 수정을 저장하거나 취소한 뒤 보내세요.'); return; }
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
  const toolsButton = document.getElementById('attachButton'), toolsMenu = document.getElementById('chatTools');
  function placeTools() {
    const rect = toolsButton.getBoundingClientRect();
    toolsMenu.style.left = Math.max(12, Math.min(rect.left, innerWidth - 292)) + 'px';
    toolsMenu.style.bottom = Math.max(12, innerHeight - rect.top + 10) + 'px';
  }
  toolsButton.addEventListener('click', () => { placeTools(); toolsMenu.togglePopover(); });
  toolsMenu.addEventListener('toggle', event => {
    toolsButton.setAttribute('aria-expanded', String(event.newState === 'open'));
    if (event.newState === 'open') toolsMenu.querySelector('button').focus();
  });
  toolsMenu.addEventListener('keydown', event => {
    if (event.key === 'Escape') toolsButton.focus();
  });
  toolsMenu.querySelectorAll('[data-chat-tool]').forEach(button => button.addEventListener('click', () => {
    toolsMenu.hidePopover();
    toolsButton.focus();
    if (button.dataset.chatTool === 'file') file.click();
    else if (button.dataset.chatTool === 'document') document.getElementById('chatDocumentOpen').click();
  }));
  window.addEventListener('resize', () => { if (toolsMenu.matches(':popover-open')) placeTools(); });
  function editQuestion(button) {
    if (waiting || sending) { notify('답변 작성이 끝난 뒤 수정해 주세요.'); return; }
    if (editor) { editor.querySelector('textarea').focus(); return; }
    const turn = button.closest('[data-message-id]');
    const original = button.dataset.editQuestion;
    const tail = scroller.querySelectorAll('[data-message-id]');
    const expectedTail = tail[tail.length - 1].dataset.messageId;
    const box = document.createElement('form');
    box.className = 'chat-inline-editor';
    box.innerHTML = '<textarea rows="2" required aria-label="수정할 질문"></textarea><p class="chat-edit-explanation">수정하면 답변을 다시 작성합니다. 이후 대화는 수정 이력에 남습니다.</p><div class="chat-edit-attachment"><input type="file" name="attachment" hidden aria-label="교체할 첨부 파일"><span class="chat-edit-filename" role="status"></span></div><div class="chat-edit-actions"><button type="button" class="secondary chat-edit-file"><iconify-icon icon="solar:paperclip-linear"></iconify-icon><span>첨부 변경</span></button><button type="button" class="secondary" data-edit-cancel>취소</button><button type="submit">저장하고 다시 받기</button></div><p role="status" class="chat-edit-status"></p>';
    const area = box.querySelector('textarea');
    area.value = original.startsWith('[첨부') ? original.substring(original.indexOf('\n') + 1) : original;
    area.defaultValue = area.value;
    const replacement = box.querySelector('[type=file]');
    replacement.accept = file.accept;
    const attachmentButton = box.querySelector('.chat-edit-file');
    attachmentButton.querySelector('span').textContent = original.startsWith('[첨부') ? '첨부 변경' : '파일 첨부';
    attachmentButton.onclick = () => replacement.click();
    replacement.onchange = () => { box.querySelector('.chat-edit-filename').textContent = replacement.files[0]?.name || ''; };
    const message = turn.querySelector('.msg');
    const actions = turn.querySelector('.turn-acts');
    message.hidden = actions.hidden = true;
    turn.appendChild(box); editor = box; area.focus();
    function sizeEditor() { area.style.height = 'auto'; area.style.height = Math.min(Math.max(area.scrollHeight, 56), 180) + 'px'; }
    sizeEditor(); area.addEventListener('input', sizeEditor);
    box.querySelector('[data-edit-cancel]').addEventListener('click', () => {
      if (sending) return;
      box.remove(); editor = null; message.hidden = actions.hidden = false; button.focus();
    });
    box.addEventListener('submit', async event => {
      event.preventDefault();
      if (sending || !area.value.trim()) return;
      const payload = new FormData(box);
      payload.set('question', area.value.trim());
      payload.set('expected_content', original);
      payload.set('expected_tail_id', expectedTail);
      sending = true; paint();
      box.querySelectorAll('button').forEach(item => item.disabled = true);
      const localStatus = box.querySelector('.chat-edit-status');
      localStatus.textContent = '수정 내용을 저장하고 있습니다…';
      let saved = false;
      try {
        const response = await fetch('/chat/' + root.dataset.session + '/messages/' + turn.dataset.messageId + '/edit', {method:'POST', body:payload});
        if (!response.ok) {
          const result = await response.json();
          throw new Error(typeof result.detail === 'string' ? result.detail : '수정 내용을 확인해 주세요.');
        }
        saved = true;
        const page = await fetch('/chat/' + root.dataset.session, {cache:'no-store'});
        if (!page.ok || page.redirected) throw new Error('수정은 저장되었습니다. 새로고침하여 대화를 확인해 주세요.');
        installPage(await page.text(), page.url, true); editor = null;
      } catch (error) {
        localStatus.textContent = saved ? '수정은 저장되었습니다. 새로고침하여 대화를 확인해 주세요.' : (error.message === 'Failed to fetch' ? '연결을 확인해 주세요. 수정 입력은 유지됩니다. 저장 여부를 확인한 뒤 다시 시도하세요.' : error.message);
      } finally {
        sending = false; paint();
        box.querySelectorAll('button').forEach(item => item.disabled = false);
        if (waiting) { clearTimeout(timer); timer = setTimeout(poll, 2000); }
      }
    });
  }
  document.getElementById('chatHistory')?.addEventListener('click', async () => {
    if (document.querySelector('.chat-history-dialog')) return;
    const dialog = document.createElement('dialog'); dialog.className = 'chat-history-dialog';
    dialog.setAttribute('aria-labelledby', 'revisionDialogTitle');
    dialog.innerHTML = '<header><h2 id="revisionDialogTitle">수정 이력</h2><button type="button" class="secondary" aria-label="수정 이력 닫기" title="닫기"><iconify-icon icon="solar:close-square-linear"></iconify-icon></button></header><div class="chat-history-content" role="status">이력을 불러오는 중…</div>';
    document.body.appendChild(dialog);
    dialog.querySelector('button').onclick = () => dialog.close();
    dialog.addEventListener('close', () => dialog.remove()); dialog.showModal();
    const content = dialog.querySelector('[role=status]');
    try {
      const response = await fetch('/chat/' + root.dataset.session + '/revisions', {cache:'no-store'});
      if (!response.ok || response.redirected) throw new Error();
      const data = await response.json(); content.textContent = data.revisions.length ? '' : '아직 수정한 질문이 없습니다.';
      data.revisions.forEach(revision => {
        const item = document.createElement('details'); const title = document.createElement('summary');
        title.textContent = new Date(revision.created_at).toLocaleString() + ' · ' + revision.messages[0].content.slice(0, 70);
        item.appendChild(title);
        revision.messages.forEach(message => {
          const text = document.createElement('p'); text.textContent = (message.role === 'user' ? '나: ' : '에이전트: ') + message.content; item.appendChild(text);
        }); content.appendChild(item);
      });
    } catch (_) { content.textContent = '이력을 불러오지 못했습니다. 잠시 후 다시 확인해 주세요.'; }
  });
  scroller.addEventListener('click', async event => {
    const button = event.target.closest('[data-edit-question],[data-resend-question]');
    if (!button) return;
    if (button.hasAttribute('data-edit-question')) { editQuestion(button); return; }
    if (sending || waiting) { notify('답변 작성이 끝난 뒤 다시 생성해 주세요.'); return; }
    if (editor) { notify('수정 중인 질문을 저장하거나 취소해 주세요.'); editor.querySelector('textarea').focus(); return; }
    const original = button.dataset.resendQuestion;
    const messageId = button.dataset.resendId;
    const turns = scroller.querySelectorAll('[data-message-id]');
    if (!messageId || !turns.length) return;
    const question = original.startsWith('[첨부') && original.includes('\n')
      ? original.substring(original.indexOf('\n') + 1) : original;
    const payload = new FormData();
    payload.set('question', question);
    payload.set('expected_content', original);
    payload.set('expected_tail_id', turns[turns.length - 1].dataset.messageId);
    sending = true; paint(); button.disabled = true;
    status.textContent = '선택한 질문부터 답변을 다시 생성합니다…';
    let saved = false;
    try {
      const response = await fetch('/chat/' + root.dataset.session + '/messages/' + messageId + '/edit', {method:'POST', body:payload});
      if (!response.ok || response.redirected) {
        const result = await response.json().catch(() => ({}));
        throw new Error(typeof result.detail === 'string' ? result.detail : '다시 생성하지 못했습니다. 잠시 후 다시 시도해 주세요.');
      }
      saved = true;
      const page = await fetch('/chat/' + root.dataset.session, {cache:'no-store'});
      if (!page.ok || page.redirected) throw new Error();
      installPage(await page.text(), page.url);
      const selected = scroller.querySelector('[data-message-id="' + messageId + '"]');
      selected?.scrollIntoView({block:'nearest'});
      selected?.querySelector('[data-resend-question]')?.focus({preventScroll:true});
      notify('이 질문부터 다시 생성했습니다. 이전 답변과 이후 대화는 수정 이력에서 볼 수 있습니다.');
    } catch (error) {
      notify(saved ? '재생성 요청은 저장되었습니다. 새로고침하여 답변을 확인해 주세요.'
        : (error.message === 'Failed to fetch' ? '연결을 확인해 주세요. 입력 중인 내용은 유지됩니다.' : error.message));
      status.textContent = '재생성 상태를 확인해 주세요.';
    } finally {
      sending = false; button.disabled = false; paint();
      if (waiting) { clearTimeout(timer); timer = setTimeout(poll, 2000); }
    }
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
    const edited = editor && (editor.querySelector('textarea').value !== editor.querySelector('textarea').defaultValue ||
      editor.querySelector('[type=file]').files.length > 0);
    if (input.value.trim() || file.files.length || sending || edited) {
      event.preventDefault(); event.returnValue = '';
    }
  });
  paint();
  if (waiting) timer = setTimeout(poll, 2000);
})();
