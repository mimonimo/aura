(() => {
  // Only explicit settings forms participate; chat drafts have their own lifecycle.
  const protectedForms = [...document.querySelectorAll('form[data-protect-changes]')];
  const snapshot = form => JSON.stringify([...new FormData(form).entries()].map(([key, value]) =>
    [key, typeof value === 'string' ? value : [value.name, value.size, value.lastModified]]));
  const initialValues = new Map(protectedForms.map(form => [form, snapshot(form)]));
  let savingForm = null;
  if (protectedForms.length) {
    window.addEventListener('beforeunload', event => {
      if (!protectedForms.some(form => form.isConnected && form !== savingForm && snapshot(form) !== initialValues.get(form))) return;
      event.preventDefault();
      event.returnValue = '';
    });
    window.addEventListener('submit', event => {
      if (event.defaultPrevented || !initialValues.has(event.target)) return;
      savingForm = event.target;
      queueMicrotask(() => { if (event.defaultPrevented) savingForm = null; });
    });
    // Back/forward restoration must not retain the prior navigation's save bypass.
    window.addEventListener('pageshow', () => { savingForm = null; });
  }
  // 되돌리기 어려운 동작의 확인 — 삭제 경로(/delete)는 늘, 그 밖의 폼은 data-confirm 을 단 것만 묻는다.
  // 자체 확인창을 쓰는 화면은 폼에 data-own-confirm 을 달아 두 번 묻지 않게 한다.
  // 확인창은 플랫폼 대화상자(<dialog>) — 제목(data-confirm-title)·설명(data-confirm)·「취소」「확인」(data-confirm-ok),
  // 위험 동작(삭제 경로, 폼이나 누른 단추의 data-confirm-danger)은 확인 단추가 빨강. Esc·바깥 누름은 취소.
  // 확인하면 누른 단추(name/value)를 그대로 실어 다시 제출하고, 제출이 떠난 뒤의 거듭 누름은 막는다.
  const accepted = new Set(), sent = new Set();
  let asking = false;
  // choices — 고를 것이 있는 확인(예: 아래 부서를 함께 지울지·위로 올릴지): [{value, label, hint}] 를 둥근 단추 목록으로, value 가 기본(없으면 첫 것).
  // run — 확인을 누르면 창을 연 채로 실행한다(누른 단추 잠금 + busy 문구). 실패하면 창 안에 그 까닭을 보이고 다시 누를 수 있다.
  // 위험 동작은 처음 초점을 「취소」에 둔다(Enter 한 번에 지우지 않게, 빨간 단추에 초점 테두리가 겹쳐 보이지 않게).
  function askConfirm({title, message, ok, danger, choices, value, run, busy}, opener) {
    return new Promise(resolve => {
      const dialog = document.createElement('dialog');
      dialog.className = 'confirm-dialog';
      dialog.setAttribute('aria-labelledby', 'confirmDialogTitle');
      dialog.setAttribute('aria-describedby', 'confirmDialogText');
      dialog.innerHTML = '<h2 id="confirmDialogTitle"></h2><p id="confirmDialogText"></p><div class="confirm-choices" role="radiogroup" hidden></div>'
        + '<p class="confirm-error" role="alert" hidden></p>'
        + '<footer><button type="button" class="secondary" data-confirm-cancel>취소</button><button type="button" data-confirm-accept></button></footer>';
      dialog.querySelector('h2').textContent = title;
      dialog.querySelector('#confirmDialogText').textContent = message;
      const accept = dialog.querySelector('[data-confirm-accept]'), cancel = dialog.querySelector('[data-confirm-cancel]');
      const errBox = dialog.querySelector('.confirm-error'), box = dialog.querySelector('.confirm-choices');
      accept.textContent = ok;
      accept.classList.toggle('danger', danger);
      let picked = null, working = false, result = false;
      if (Array.isArray(choices) && choices.length) {
        const start = choices.some(c => c.value === value) ? value : choices[0].value;
        box.hidden = false;
        box.setAttribute('aria-label', title);
        choices.forEach((c, i) => {
          const lab = document.createElement('label'), input = document.createElement('input'), text = document.createElement('span');
          input.type = 'radio'; input.name = 'confirmChoice'; input.value = c.value; input.checked = c.value === start;
          input.addEventListener('change', () => { picked = c.value; });
          const b = document.createElement('b'); b.textContent = c.label; text.append(b);
          if (c.hint) { const s = document.createElement('small'); s.textContent = c.hint; text.append(s); }
          lab.append(input, text); box.append(lab);
        });
        picked = start;
      }
      cancel.addEventListener('click', () => { if (!working) dialog.close(); });
      accept.addEventListener('click', async () => {
        if (working) return;
        const value = picked == null ? true : picked;
        if (typeof run !== 'function') { result = value; dialog.close('accept'); return; }
        working = true; errBox.hidden = true;
        accept.disabled = cancel.disabled = true;
        dialog.querySelectorAll('.confirm-choices input').forEach(i => { i.disabled = true; });
        const label = accept.textContent; accept.textContent = busy || '처리 중…';
        try { result = (await run(value)) ?? value; working = false; dialog.close('accept'); }
        catch (e) {
          working = false; accept.disabled = cancel.disabled = false; accept.textContent = label;
          dialog.querySelectorAll('.confirm-choices input').forEach(i => { i.disabled = false; });
          errBox.textContent = (e && e.message) || '처리하지 못했습니다 — 잠시 뒤 다시 해 주세요'; errBox.hidden = false;
        }
      });
      dialog.addEventListener('cancel', event => { if (working) event.preventDefault(); });
      // 바깥(배경) 누름 = 취소 — 대화상자 상자 밖 좌표일 때만
      dialog.addEventListener('click', event => {
        if (event.target !== dialog || working) return;
        const r = dialog.getBoundingClientRect();
        if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();
      });
      dialog.addEventListener('close', () => {
        const yes = dialog.returnValue === 'accept';
        dialog.remove();
        if (opener?.isConnected) opener.focus({preventScroll: true});
        resolve(yes ? (result === false ? true : result) : false);
      }, {once: true});
      document.body.append(dialog);
      dialog.showModal();
      (danger ? cancel : accept).focus();
    });
  }
  // 폼이 아닌 동작(fetch)도 같은 확인창을 쓰게 — await window.platformConfirm('설명', {title, ok, danger, choices, run, busy})
  // 돌려주는 값: 취소 false, 확인 true(choices 가 있으면 고른 value, run 이 값을 돌려주면 그 값)
  window.platformConfirm = (message, o = {}) => askConfirm({title: o.title || '확인', message, ok: o.ok || '확인', danger: !!o.danger,
                                                            choices: o.choices, value: o.value, run: o.run, busy: o.busy}, document.activeElement);
  window.addEventListener('pageshow', () => { asking = false; sent.clear(); });
  document.addEventListener('submit', event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || event.defaultPrevented || form.hasAttribute('onsubmit')) return;
    if ('ownConfirm' in form.dataset) return;
    const action = new URL(form.action, location.href);
    if (action.origin !== location.origin) return;
    const isDelete = /\/delete\/?$/.test(action.pathname);
    if (!isDelete && !('confirm' in form.dataset)) return;
    // 확인은 한 번만 — 화면이 따로 붙인 같은 확인(data-confirm 직접 처리)까지 이어지지 않게 여기서 끊는다
    event.stopImmediatePropagation();
    if (accepted.has(form)) {
      // 확인을 마친 다시 제출 — 그대로 보낸다. 다른 처리기가 막지 않았으면 떠나는 동안의 거듭 제출을 막는다
      accepted.delete(form);
      sent.add(form);
      queueMicrotask(() => { if (event.defaultPrevented) sent.delete(form); });
      // 화면이 바뀌지 않는 제출(내려받기 등)이면 잠시 뒤 다시 누를 수 있게 푼다
      setTimeout(() => sent.delete(form), 8000);
      return;
    }
    event.preventDefault();
    if (asking || sent.has(form)) return;
    const submitter = event.submitter;
    const pick = name => submitter?.dataset[name] ?? form.dataset[name];
    const message = pick('confirm') || (/\/notes\//.test(action.pathname)
      ? '이 메모를 삭제할까요? 삭제 후에는 복구할 수 없습니다.'
      : '이 항목을 삭제할까요?');
    const danger = isDelete || pick('confirmDanger') !== undefined;
    // 제목은 누른 단추의 이름(무엇을 하려는지) — 글자 없는 단추면 aria-label·title, 그것도 없으면 일반 제목
    const label = submitter && (submitter.textContent.trim() || submitter.getAttribute('aria-label') || submitter.title || submitter.value);
    const title = pick('confirmTitle') || (label ? label.replace(/\s+/g, ' ').slice(0, 40) : '진행 확인');
    asking = true;
    askConfirm({title, message, ok: pick('confirmOk') || '확인', danger}, submitter || form.querySelector('button, [type=submit]'))
      .then(yes => {
        asking = false;
        if (!yes || !form.isConnected) return;
        accepted.add(form);
        if (submitter && submitter.form === form && !submitter.disabled) form.requestSubmit(submitter);
        else form.requestSubmit();
        accepted.delete(form);
      });
  }, true);
  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href]');
    if (!link || link.hasAttribute('download') || link.target || link.hasAttribute('data-force-navigation')) return;
    const target = new URL(link.href, location.href);
    if (target.origin !== location.origin || target.pathname !== location.pathname || target.search !== location.search || target.hash !== location.hash) return;
    event.preventDefault();
    document.querySelectorAll('.session-popover:popover-open').forEach(pop => pop.hidePopover());
  });
  document.querySelector('[data-page-back]')?.addEventListener('click', event => {
    // Direct visits and external referrers use the explicit parent link.
    if (document.referrer && history.length > 1) {
      const previous = new URL(document.referrer);
      if (previous.origin === location.origin && previous.href !== location.href) {
        event.preventDefault(); history.back();
      }
    }
  });
  const sidebar = document.getElementById('workspaceSidebar');
  const toggle = document.getElementById('navToggle');
  const close = document.getElementById('navClose');
  const content = document.querySelector('.content-col');
  const mobile = matchMedia('(max-width:1080px)');
  if (!sidebar || !toggle || !close) return;
  let desktopClosed = document.documentElement.classList.contains('sidebar-collapsed');
  let mobileOpen = false;
  function paint() {
    const open = mobile.matches ? mobileOpen : !desktopClosed;
    document.body.classList.toggle('nav-open', mobile.matches && open);
    document.documentElement.classList.toggle('sidebar-collapsed', desktopClosed);
    sidebar.inert = !open;
    sidebar.setAttribute('aria-hidden', String(!open));
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-controls', sidebar.id);
    toggle.setAttribute('aria-label', open ? '메뉴 접기' : '메뉴 열기');
    toggle.title = open ? '메뉴 접기' : '메뉴 열기';
    content.inert = mobile.matches && open;
  }
  function setOpen(open, focus=true) {
    if (mobile.matches) mobileOpen = open;
    else {
      desktopClosed = !open;
      try { localStorage.setItem('workspaceSidebarCollapsed', desktopClosed ? '1' : '0'); } catch (_) {}
    }
    paint();
    if (focus) (open ? close : toggle).focus();
  }
  toggle.addEventListener('click', () => setOpen(mobile.matches ? !mobileOpen : desktopClosed));
  close.addEventListener('click', () => setOpen(false));
  document.getElementById('navBackdrop').addEventListener('click', () => setOpen(false));
  sidebar.addEventListener('click', event => {
    if (mobile.matches && event.target.closest('a[href]')) setOpen(false, false);
  });
  document.addEventListener('keydown', event => {
    if (!mobile.matches || !mobileOpen || document.querySelector('dialog[open], .modal-back.open')) return;
    if (event.key === 'Escape') { event.preventDefault(); setOpen(false); }
    if (event.key !== 'Tab') return;
    const items = [...sidebar.querySelectorAll('a[href],button:not([disabled]),input:not([disabled])')]
      .filter(item => item.getClientRects().length);
    const first = items[0], last = items.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  mobile.addEventListener('change', () => {
    const active = document.activeElement;
    mobileOpen = false; paint();
    if (sidebar.inert && sidebar.contains(active)) toggle.focus();
  });
  paint();
  requestAnimationFrame(() => document.documentElement.classList.add('nav-ready'));
})();
