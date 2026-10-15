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
  // 대화 칸 고정(chat-scroll.js) — 보낸 뒤·답이 온 뒤 늦게 늘어나는 높이에도 최신 글에 남고, 사용자가 스크롤하면 놓는다
  const pin = window.ChatScroll ? window.ChatScroll.createPin(scroller) : {
    toBottom() { scroller.scrollTop = scroller.scrollHeight; }, toLatest() { scroller.scrollTop = scroller.scrollHeight; },
    release() {}, active() { return false; }, nearBottom() { return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 100; },
  };
  if (window.ChatScroll) window.ChatScroll.releaseOnUserScroll(scroller, pin);
  function bottom() { pin.toBottom(); latest.hidden = true; }
  function nearBottom() { return pin.nearBottom(); }
  function newestAnswer() {
    const answers = scroller.querySelectorAll('.turn.assistant:not(.chat-progress)');
    return answers[answers.length - 1] || null;
  }
  // 쓰는 중인 답의 가벼운 그리기 — 글은 먼저 이스케이프하고 굵게·목록·제목 기호만 바꾼다(최종 답은 서버가 그린다)
  function lightMarkdown(text) {
    const esc = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return esc.split('\n').map(line => {
      let l = line.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
      if (/^\s*[*-]\s+/.test(l)) l = l.replace(/^(\s*)[*-]\s+/, '$1• ');
      if (/^#{1,4}\s+/.test(l)) l = '<b>' + l.replace(/^#{1,4}\s+/, '') + '</b>';
      return l;
    }).join('\n');
  }
  function showProgress(progress) {
    const panel = document.getElementById('chatProgress');
    if (!panel || !progress || !Array.isArray(progress.steps) || !progress.steps.length) return;
    const steps = progress.steps.filter(step => typeof step === 'string');
    if (!steps.length) return;
    panel.querySelector('[data-progress-label]').textContent = steps[steps.length - 1];
    if (Number.isFinite(progress.started_at)) {
      const now0 = Number.isFinite(progress.now) ? progress.now : Date.now() / 1000;
      panel.dataset.startedAt = String(progress.started_at);
      panel.dataset.clockSkew = String(Date.now() / 1000 - now0);          // 서버·브라우저 시계 차이를 빼고 센다
      panel.querySelector('[data-progress-time]').textContent = Math.max(0, Math.floor(now0 - progress.started_at)) + '초';
    }
    const list = panel.querySelector('ol');
    // 단계마다 걸린 초 — 다음 단계가 시작된 시각까지, 지금 단계는 지금까지(서버 시각 기준)
    const at = Array.isArray(progress.at) && progress.at.length === progress.steps.length ? progress.at : null;
    const now = Number.isFinite(progress.now) ? progress.now : Date.now() / 1000;
    list.replaceChildren(...steps.map((text, index) => {
      const item = document.createElement('li');
      item.textContent = text;
      if (at && Number.isFinite(at[index])) {
        const end = index + 1 < at.length ? at[index + 1] : now;
        const sec = document.createElement('span');
        sec.className = 'chat-step-sec';
        sec.textContent = ' · ' + Math.max(0, end - at[index]).toFixed(index === steps.length - 1 ? 0 : 1) + '초';
        if (index === steps.length - 1) { sec.dataset.liveFrom = String(at[index]); sec.dataset.clockSkew = String(Date.now() / 1000 - now); }
        item.append(sec);
      }
      if (index === steps.length - 1) item.setAttribute('aria-current', 'step');
      return item;
    }));
    status.textContent = steps[steps.length - 1];
    // 쓰는 중인 답 — 다 쓰면 최종 답(가림·출처 처리 후)으로 바뀐다
    let draft = panel.querySelector('.chat-partial');
    if (typeof progress.partial === 'string' && progress.partial.trim()) {
      if (!draft) {
        draft = document.createElement('div');
        draft.className = 'chat-partial';
        draft.setAttribute('aria-live', 'off');
        panel.append(draft);
      }
      const near = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
      draft.innerHTML = lightMarkdown(progress.partial);
      if (near) scroller.scrollTop = scroller.scrollHeight;
    } else if (draft) draft.remove();
    // 문서 패널(chat-documents.js)도 같은 진행 단계를 편집기 위에 표시한다
    root.dispatchEvent(new CustomEvent('chat-progress', {detail:{label:steps[steps.length - 1], steps}}));
  }
  // 기다리는 동안 경과 초를 1초마다 — 서버에 다시 묻는 간격(2~4초)마다 뛰어 「끊겨 보이는」 것을 막는다
  setInterval(() => {
    const panel = document.getElementById('chatProgress');
    if (!panel || panel.hidden) return;
    const time = panel.querySelector('[data-progress-time]');
    const started = Number(panel.dataset.startedAt), skew = Number(panel.dataset.clockSkew || 0);
    if (time && Number.isFinite(started) && started > 0) time.textContent = Math.max(0, Math.floor(Date.now() / 1000 - skew - started)) + '초';
    const live = panel.querySelector('.chat-step-sec[data-live-from]');
    if (live) live.textContent = ' · ' + Math.max(0, Math.floor(Date.now() / 1000 - Number(live.dataset.clockSkew || 0) - Number(live.dataset.liveFrom))) + '초';
  }, 1000);
  // 답이 「필요한 값 입력」을 가리키면 그 칸으로 데려간다(칸은 마지막 답 아래, 받지 못한 값이 있는 동안 늘 있다)
  scroller.addEventListener('click', event => {
    if (!event.target.closest('[data-jump-form]')) return;
    const valueForm = document.getElementById('chatValueForm');
    if (!valueForm) return;
    pin.release();
    valueForm.scrollIntoView({block:'center'});
    ([...valueForm.querySelectorAll('textarea:not(:disabled)')].find(t => !t.value) || valueForm.querySelector('textarea:not(:disabled)'))?.focus({preventScroll:true});   // 빈 칸부터
  });
  scroller.addEventListener('click', event => {
    const button = event.target.closest('[data-progress-toggle]');
    if (!button) return;
    const list = document.getElementById('chatProgressSteps');
    list.hidden = !list.hidden;
    button.setAttribute('aria-expanded', String(!list.hidden));
    button.textContent = list.hidden ? '작업 내역' : '접기';
  });
  // 필요한 값 입력 — 채운 수·저장 단추 글, 키보드 이동, 「이름: 값」 붙여 넣어 나눠 채우기, 모름·나중에, 자료 값 확인/고치기.
  // 붙여 넣기 나누기는 서버 asks.parse_pairs 와 같은 규칙(물었던 이름만, 「이름: 값」·「이름은 값」, 끝말 「입니다」 떼기)
  const PAIR_SEP = /[;\n]|,\s*(?=[^,]{1,40}?\s*(?:[:：]|은|는|이|가)\s)/;
  const PAIR_TAIL = /\s*(?:입니다|이다|이고|이며|임|예요|에요)\.?$/;
  const escapeRe = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  function parsePairs(text, names) {
    const out = {};
    if (!text || !names.length) return out;
    const ordered = [...names].sort((a, b) => b.length - a.length);
    for (const raw of text.split(PAIR_SEP)) {
      const piece = (raw || '').trim();
      if (!piece) continue;
      for (const name of ordered) {
        const m = piece.match(new RegExp('^\\s*' + escapeRe(name) + '\\s*(?:[:：]|은|는|이|가)\\s*(.+)$'));
        if (!m) continue;
        const value = m[1].trim().replace(PAIR_TAIL, '').replace(/^[ .]+|[ .]+$/g, '');
        if (value && !/^(?:[○◯?？]+|\(값\)|\(번호\))$/.test(value)) out[name] = value;
        break;
      }
    }
    return out;
  }
  window.ChatValueForm = {parsePairs};
  const valueRows = form_ => [...form_.querySelectorAll('.chat-form-field')];
  const rowArea = row => row.querySelector('textarea');
  const rowLater = row => row.querySelector('input[name=later]');
  const rowPick = row => row.querySelector('input[data-pick]');
  const isPicked = row => Boolean(rowPick(row)?.checked);
  function valueCounts(form_) {
    let filled = 0, later = 0, typed = 0;
    for (const row of valueRows(form_)) {
      if (rowLater(row)?.checked) { later++; continue; }
      const area = rowArea(row);
      if (!area.value.trim()) continue;
      filled++;
      if (!row.classList.contains('is-suggested') || area.value !== area.defaultValue) typed++;
    }
    return {filled, later, typed};
  }
  function paintValueForm(form_) {
    const total = Number(form_.dataset.total) || valueRows(form_).length;
    for (const row of valueRows(form_)) {
      const later = Boolean(rowLater(row)?.checked), area = rowArea(row);
      area.disabled = later;
      row.classList.toggle('is-later', later);
      row.classList.toggle('is-filled', !later && Boolean(area.value.trim()));
      row.classList.toggle('is-picked', isPicked(row));
      if (row.classList.contains('is-suggested') && area.value !== area.defaultValue && !row.classList.contains('is-edited')) {
        row.classList.add('is-edited'); row.classList.remove('is-accepted');
        const note = row.querySelector('[data-source-state]');
        if (note) note.textContent = '자료 값을 고쳐 적음 — ' + note.textContent.replace(/^자료에서 찾음 · 확인 (?:전|함) — /, '');
      }
    }
    const {filled, later, typed} = valueCounts(form_);
    const count = form_.querySelector('[data-form-count]');
    if (count) count.textContent = total + '개 중 ' + filled + '개 채움' + (later ? ' · 나중에 ' + later + '개' : '');
    form_.style.setProperty('--vf-done', String(total ? Math.min(1, (filled + later) / total) : 0));
    // 한꺼번에 — 선택한 줄이 있으면 선택한 줄, 없으면 아직 비어 있는(미루지 않은) 줄이 대상
    const rows = valueRows(form_), picked = rows.filter(isPicked);
    const empty = rows.filter(row => !rowLater(row)?.checked && !rowArea(row).value.trim());
    const laterRows = rows.filter(row => rowLater(row)?.checked);
    const all = form_.querySelector('[data-pick-all]');
    if (all) { all.checked = rows.length > 0 && picked.length === rows.length; all.indeterminate = picked.length > 0 && picked.length < rows.length; }
    form_.classList.toggle('is-picking', picked.length > 0);
    // 자료에서 찾기는 선택한 줄에만 — 먼저 체크로 고르고(빈 칸 선택로 한 번에 체크한 뒤 빼도 된다) 그다음 찾기
    const toolFind = form_.querySelector('[data-bulk=find]'),
          toolClear = form_.querySelector('[data-bulk=clear]'), toolPick = form_.querySelector('[data-bulk=pick-empty]');
    const unpicked = empty.filter(row => !isPicked(row));
    if (toolPick) {
      toolPick.hidden = !unpicked.length || picked.length > 0;
      toolPick.textContent = '빈 칸 ' + unpicked.length + '개 선택';
    }
    if (toolFind) {
      const targets = picked.filter(row => !rowArea(row).value.trim() || rowLater(row)?.checked);
      toolFind.hidden = !picked.length;
      toolFind.textContent = '선택한 ' + targets.length + '개 자료에서 찾기';
      toolFind.disabled = !targets.length;
    }
    if (toolClear) { toolClear.hidden = !picked.length; toolClear.textContent = '선택 해제'; }
    const pickHint = form_.querySelector('[data-pick-hint]');
    if (pickHint) pickHint.hidden = picked.length > 0 || !unpicked.length;
    const verb = typed ? '저장' : (form_.dataset.verb || '저장');
    const submit = form_.querySelector('[data-form-submit]');
    if (submit) {
      // 비운 칸은 저장할 때 다음 차례로 — 따로 「나중에」 를 누르지 않는다. 다 비우고 누르면 이 칸들을 통째로 다음에
      const rest = empty.length + later;
      submit.textContent = filled ? filled + '개 ' + verb + (rest ? ' · 빈 칸 ' + rest + '개는 다음에' : '') : '모두 다음에 받기';
      submit.disabled = false;
      submit.classList.toggle('is-skip', !filled);
    }
  }
  function paintValueForms() { scroller.querySelectorAll('form.chat-suggestion-form').forEach(paintValueForm); }
  function formStatus(form_, text) {
    const box = form_.querySelector('[data-form-status]');
    if (!box) return;
    box.textContent = text;
    clearTimeout(box._clear);
    if (text) box._clear = setTimeout(() => { box.textContent = ''; }, 5000);           // 알림은 잠깐만, 남아 있으면 지금 상태로 오해한다
  }
  let lastPick = null;
  scroller.addEventListener('input', event => {
    if (event.target.type === 'checkbox') return;                                          // 체크는 change 에서 — input 이 먼저 와서 전체 선택를 덮어쓴다
    const form_ = event.target.closest('form.chat-suggestion-form');
    if (form_) { formStatus(form_, ''); paintValueForm(form_); }
  });
  scroller.addEventListener('change', event => {
    const form_ = event.target.closest('form.chat-suggestion-form');
    if (!form_) return;
    if (event.target.matches('[data-pick-all]')) {
      const on = event.target.checked;
      valueRows(form_).forEach(row => { const box = rowPick(row); if (box) box.checked = on; });
      lastPick = null;
      paintValueForm(form_);
      return;
    }
    if (event.target.matches('[data-pick]')) { paintValueForm(form_); return; }
    if (event.target.name !== 'later') return;
    paintValueForm(form_);
    if (!event.target.checked) rowArea(event.target.closest('.chat-form-field'))?.focus();
  });
  scroller.addEventListener('keydown', event => {
    const area = event.target.closest('form.chat-suggestion-form textarea');
    if (!area) return;
    const form_ = area.form;
    if (event.key === 'Escape') { event.preventDefault(); area.blur(); return; }      // 값은 그대로, 칸에서만 빠진다
    if (event.key !== 'Enter' || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.isComposing || event.keyCode === 229) return;                                // 한글 조합 중 Enter 는 글자 확정
    event.preventDefault();
    const areas = [...form_.querySelectorAll('textarea')].filter(item => !item.disabled);
    const next = areas[areas.indexOf(area) + 1];
    if (next) { next.focus(); next.setSelectionRange(next.value.length, next.value.length); }
    else form_.requestSubmit();
  });
  scroller.addEventListener('paste', event => {
    const area = event.target.closest('form.chat-suggestion-form textarea');
    if (!area) return;
    const form_ = area.form;
    const text = event.clipboardData?.getData('text') || '';
    const rows = valueRows(form_);
    const pairs = parsePairs(text, rows.map(row => rowArea(row).dataset.field));
    const names = Object.keys(pairs);
    if (!names.length) return;                                                             // 값 하나 — 보통 붙여 넣기
    event.preventDefault();
    for (const row of rows) {
      const target = rowArea(row);
      if (!(target.dataset.field in pairs)) continue;
      const later = rowLater(row);
      if (later) later.checked = false;
      target.value = pairs[target.dataset.field];
    }
    paintValueForm(form_);
    const lines = text.split(PAIR_SEP).filter(line => line.trim()).length;
    formStatus(form_, '붙여 넣은 ' + names.length + '개를 칸에 나눠 채움' + (lines > names.length ? ' · 맞는 칸이 없는 ' + (lines - names.length) + '줄은 넣지 않음' : ''));
  });
  scroller.addEventListener('click', event => {
    // 여러 줄 선택 — 줄 앞 체크, Shift+체크는 앞에 선택한 줄부터 범위, Ctrl(⌘)+이름도 같은 동작
    const pickBox = event.target.closest('form.chat-suggestion-form input[data-pick]');
    if (pickBox) {
      const rows = valueRows(pickBox.form), row = pickBox.closest('.chat-form-field');
      if (event.shiftKey && lastPick && lastPick !== row && rows.includes(lastPick)) {
        const [a, b] = [rows.indexOf(lastPick), rows.indexOf(row)].sort((x, y) => x - y);
        rows.slice(a, b + 1).forEach(item => { rowPick(item).checked = pickBox.checked; });
      }
      lastPick = row;
      paintValueForm(pickBox.form);
      return;
    }
    const nameHit = event.target.closest('form.chat-suggestion-form .chat-form-label');
    if (nameHit && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      const row = nameHit.closest('.chat-form-field'), box = rowPick(row);
      if (box) box.checked = !box.checked;
      lastPick = row;
      paintValueForm(row.closest('form'));
      return;
    }
    const tool = event.target.closest('form.chat-suggestion-form [data-bulk]');
    if (tool) {
      const form_ = tool.closest('form'), rows = valueRows(form_);
      const picked = rows.filter(isPicked);
      const unpick = () => { picked.forEach(row => { rowPick(row).checked = false; }); lastPick = null; };
      if (tool.dataset.bulk === 'pick-empty') {
        rows.filter(row => !rowLater(row)?.checked && !rowArea(row).value.trim()).forEach(row => { rowPick(row).checked = true; });
        lastPick = null;
      } else if (tool.dataset.bulk === 'clear') {
        unpick();
      } else if (tool.dataset.bulk === 'find') {
        const names = picked.filter(row => !rowArea(row).value.trim() || rowLater(row)?.checked).map(row => rowArea(row).dataset.field);
        if (names.length && input && form) {
          input.value = names.map(name => '「' + name + '」').join(', ') + ' 값을 이 프로젝트 자료에서 찾아 채워 줘';
          input.dispatchEvent(new Event('input', {bubbles: true}));
          form.requestSubmit();
        }
        return;
      }
      paintValueForm(form_);
      return;
    }
    const hint = event.target.closest('form.chat-suggestion-form .chat-form-hint');
    if (hint) { hint.classList.toggle('is-open'); return; }
    const accept = event.target.closest('[data-accept-value]'), edit = event.target.closest('[data-edit-value]');
    if (!accept && !edit) return;
    const row = event.target.closest('.chat-form-field'), area = rowArea(row);
    if (edit) { area.focus(); area.select(); return; }
    row.classList.add('is-accepted');
    const note = row.querySelector('[data-source-state]');
    if (note) note.textContent = note.textContent.replace('확인 전', '확인함');
    const areas = [...area.form.querySelectorAll('textarea')].filter(item => !item.disabled);
    (areas.slice(areas.indexOf(area) + 1).find(item => !item.value.trim()) || area.form.querySelector('[data-form-submit]'))?.focus();
  });
  async function sendValueForm(form_) {
    if (sending || waiting) return;
    const {filled} = valueCounts(form_);
    const payload = new FormData(form_);
    for (const [key, value] of [...payload.entries()]) {                                  // 비운 칸은 보내지 않는다
      if (key.startsWith('field_') && !String(value).trim()) payload.delete(key);
    }
    const label = filled ? filled + '개 저장 — 에이전트가 반영 중' : '이 칸들은 다음 차례로 — 다음 값을 불러오는 중';
    const sent = document.createElement('div');
    sent.className = 'chat-form-sent'; sent.setAttribute('role', 'status');
    sent.innerHTML = '<span class="chat-progress-dot" aria-hidden="true"></span><span></span>';
    sent.lastChild.textContent = label;
    form_.append(sent); form_.classList.add('is-sent');
    sending = true; paint();
    try {
      const response = await fetch(form_.action, {method:'POST', body:payload});
      if (!response.ok) throw new Error('send failed');
      installPage(await response.text(), response.url, true);
      const progress = document.querySelector('#chatProgress [data-progress-label]');
      if (progress && filled) { progress.textContent = label; status.textContent = label; }
    } catch (_) {
      sent.remove(); form_.classList.remove('is-sent');
      formStatus(form_, '저장하지 못했습니다. 적은 값은 그대로 있습니다. 잠시 후 다시 저장하세요.');
    } finally {
      sending = false; paint();
      if (waiting) { clearTimeout(timer); timer = setTimeout(poll, 2000); }
    }
  }
  function installPage(html, url, forceScroll = false) {
    const parsed = new DOMParser().parseFromString(html, 'text/html');
    const next = parsed.getElementById('chatWorkspace');
    const nextScroll = parsed.getElementById('chatScroll');
    const target = new URL(url, location.href);
    if (!next || !nextScroll || target.origin !== location.origin || !/^\/chat\/\d+$/.test(target.pathname)) {
      throw new Error('invalid chat response');
    }
    const shouldScroll = forceScroll || nearBottom() || pin.active();
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
    root.dispatchEvent(new CustomEvent('chat-session-updated', {detail:{session:next.dataset.session, waiting}}));
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
    // 보낸 직후는 맨 아래(진행 줄), 답이 막 온 때는 새 답으로 — 긴 답이면 답의 머리에 맞춘다
    if (forceScroll || (shouldScroll && waiting)) bottom();
    else if (shouldScroll) { pin.toLatest(newestAnswer()); latest.hidden = true; }
    else { scroller.scrollTop = position; latest.hidden = false; }
    status.textContent = waiting ? '요청 처리 중' : '';
    paint();
    paintValueForms();
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
      status.textContent = '요청 처리 중입니다.';
      showProgress(state.progress);
      timer = setTimeout(poll, state.progress && state.progress.partial ? 1000 : 3000);   // 답이 흘러나오는 동안은 1초마다
      return;
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
  // 답변 갱신으로 교체되는 제안에도 같은 비동기 채팅 전송 경로를 사용한다.
  scroller.addEventListener('submit', event => {
    const suggestion = event.target.closest('form.chat-suggestion');
    if (!suggestion) return;
    event.preventDefault();
    if (suggestion.matches('.chat-suggestion-form')) { sendValueForm(suggestion); return; }   // 칸 값은 칸 폼 그대로(field_·template·later)
    if (sending || waiting) return;
    if (editor || input.value.trim() || file.files.length || mustAttach) {
      notify('작성 중인 질문을 보내거나 비운 뒤 제안을 선택해 주세요.');
      (editor?.querySelector('textarea') || input).focus();
      return;
    }
    const question = suggestion.querySelector('[name=question]')?.value;
    if (!question?.trim()) return;
    input.value = question;
    paint(); resize();
    form.requestSubmit();
  });
  input.addEventListener('input', () => { paint(); resize(); });
  // 첨부 단추는 파일 선택 하나만 — 답변 방식(웹 검색 포함)은 입력줄 오른쪽 선택에서 정한다
  document.getElementById('attachButton').addEventListener('click', () => file.click());
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
  paintValueForms();
  // 처음 연 화면도 최신 글에서 — 아이콘·단추가 늦게 그려져도 맨 위로 밀리지 않게
  if (scroller.querySelector('.turn')) bottom();
  if (waiting) timer = setTimeout(poll, 2000);
})();
