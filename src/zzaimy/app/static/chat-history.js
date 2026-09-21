(() => {
  const sidebarList = document.getElementById('chatSessionList');
  function addSessionMenus() {
    sidebarList?.querySelectorAll('a[href^="/chat/"]').forEach(link => {
      const id = link.getAttribute('href').split('/').pop();
      if (!/^\d+$/.test(id)) return;
      let row = link.closest('.side-session-row');
      if (!row) { row = document.createElement('div'); row.className = 'side-session-row'; link.before(row); row.append(link); }
      const menu = row.querySelector('.side-session-menu') || document.createElement('button');
      if (menu.dataset.ready) return;
      menu.dataset.ready = 'true'; menu.disabled = false;
      menu.type = 'button'; menu.className = 'side-session-menu';
      menu.textContent = '⋯'; menu.setAttribute('aria-label', (link.title || link.textContent) + ' 대화 메뉴');
      menu.setAttribute('aria-haspopup', 'dialog');
      menu.onclick = () => {
        document.querySelector('.session-popover')?.remove();
        const pop = document.createElement('div'); pop.className = 'session-popover'; pop.setAttribute('popover','auto'); pop.setAttribute('role','dialog'); pop.setAttribute('aria-label','대화 관리');
        pop.innerHTML = '<button type="button" class="session-delete-action" data-delete><iconify-icon icon="solar:trash-bin-trash-linear"></iconify-icon>삭제</button>';
        document.body.append(pop); pop.showPopover();
        menu.setAttribute('aria-expanded','true');
        const rect = menu.getBoundingClientRect();
        function position() {
          pop.style.left = Math.max(8, Math.min(rect.right - pop.offsetWidth, innerWidth - pop.offsetWidth - 8)) + 'px';
          pop.style.top = Math.max(8, Math.min(rect.bottom + 4, innerHeight - pop.offsetHeight - 8)) + 'px';
        }
        position();
        pop.querySelector('button').focus();
        pop.addEventListener('keydown', e => {
          if(e.key === 'Escape') { e.preventDefault(); pop.hidePopover(); if(menu.isConnected) menu.focus(); }
        });
        pop.addEventListener('toggle', e => { if (e.newState === 'closed') {pop.remove(); menu.setAttribute('aria-expanded','false');} });
        pop.querySelector('[data-delete]').onclick = () => {
          pop.classList.add('confirming');
          pop.innerHTML = '<p class="session-delete-title"></p><p>이 대화와 수정 이력을 삭제할까요? 복구할 수 없습니다. 프로젝트와 등록 문서, 첨부 원본 파일은 유지됩니다.</p><div><button type="button" class="secondary" data-cancel>취소</button><button type="button" data-confirm>삭제</button></div><p role="alert"></p>';
          pop.querySelector('.session-delete-title').textContent = link.title || link.textContent;
          position();
          pop.querySelector('[data-cancel]').onclick = () => {pop.hidePopover(); if(menu.isConnected) menu.focus();};
          pop.querySelector('[data-cancel]').focus();
          pop.querySelector('[data-confirm]').onclick = async () => {
            pop.querySelectorAll('button').forEach(b => b.disabled = true);
            try {
              const response = await fetch('/api/chat/sessions/' + id, {method:'DELETE'});
              if (!response.ok || response.redirected) throw new Error(response.status === 409 ? '답변이 끝난 뒤 삭제해 주세요.' : '삭제하지 못했습니다. 다시 시도해 주세요.');
              try { if(sessionStorage.getItem('agentSession') === id) sessionStorage.removeItem('agentSession'); sessionStorage.removeItem('chatCriteria:' + id); } catch (_) {}
              document.dispatchEvent(new CustomEvent('chat-session-deleted', {detail:{id}}));
              if(location.pathname === '/chat/' + id) { location.replace('/chat'); return; }
              document.querySelectorAll('a[href="/chat/' + id + '"]').forEach(a => a.remove());
              row.remove(); pop.hidePopover();
              document.querySelector('[data-chat-history]')?.focus();
              document.dispatchEvent(new CustomEvent('chat-history-changed'));
            } catch (error) { pop.querySelector('[role=alert]').textContent = error.message; pop.querySelectorAll('button').forEach(b => b.disabled = false); }
          };
        };
      };
      row.append(menu);
    });
  }
  if (sidebarList) { addSessionMenus(); new MutationObserver(addSessionMenus).observe(sidebarList, {childList:true}); }
  document.addEventListener('click', event => {
    const opener = event.target.closest('[data-chat-history]');
    if (!opener) return;
    if (document.querySelector('.session-manager')) return;
    const dialog = document.createElement('dialog');
    dialog.className = 'session-manager';
    dialog.setAttribute('aria-label', '대화 기록 검색');
    dialog.innerHTML = '<header><h2>대화 검색</h2><button type="button" class="secondary" data-close>닫기</button></header><form class="session-search"><input type="search" aria-label="대화 이름 또는 프로젝트 검색" placeholder="대화 이름, 프로젝트 검색"><button type="submit" class="secondary">검색</button><div class="session-filters"><label>상태<select aria-label="기록 구분"><option value="0">진행 중</option><option value="1">보관됨</option></select></label><label>검색 범위<select aria-label="찾을 범위" data-scope><option value="title">이름·프로젝트·사업</option><option value="all">대화 내용 포함</option></select></label></div></form><p role="status"></p><div class="session-results"></div><button type="button" class="secondary" data-more hidden>더 보기</button>';
    document.body.appendChild(dialog); dialog.showModal();
    dialog.querySelector('[data-close]').onclick = () => dialog.close();
    let previewController;
    dialog.addEventListener('close', () => { token++; controller?.abort(); previewController?.abort(); dialog.remove(); if(opener.isConnected) opener.focus({preventScroll:true}); });
    const form = dialog.querySelector('form'), results = dialog.querySelector('.session-results');
    const status = dialog.querySelector('[role=status]'), more = dialog.querySelector('[data-more]');
    let offset = 0, token = 0, controller;
    async function preview(session) {
      previewController?.abort();
      const request = new AbortController(); previewController = request;
      const scroll = results.scrollTop;
      const controls = [form, status, results, more];
      const visibility = controls.map(node => node.hidden);
      controls.forEach(node => node.hidden = true);
      const pane = document.createElement('section'); pane.className = 'session-preview';
      const toolbar = document.createElement('div'); toolbar.className = 'session-preview-toolbar';
      const back = document.createElement('button'); back.type = 'button'; back.className = 'secondary'; back.textContent = '검색 결과로';
      const full = document.createElement('a'); full.className = 'btn-ghost'; full.href = '/chat/' + session.id; full.textContent = '전체 업무 대화로 열기';
      toolbar.append(back, full);
      const title = document.createElement('h3'); title.textContent = session.title || '이름 없는 대화'; title.tabIndex = -1;
      const content = document.createElement('div'); content.className = 'session-preview-messages';
      const notice = document.createElement('p'); notice.setAttribute('role','status'); notice.textContent = '대화 내용을 불러오는 중…';
      content.append(notice); pane.append(toolbar, title, content); dialog.append(pane); title.focus();
      back.onclick = () => {
        request.abort(); pane.remove();
        controls.forEach((node,index) => node.hidden = visibility[index]); results.scrollTop = scroll;
        [...results.querySelectorAll('a')].find(node => node.getAttribute('href') === '/chat/' + session.id)?.focus({preventScroll:true});
      };
      try {
        const response = await fetch('/chat/' + session.id + '/messages', {cache:'no-store', signal:request.signal});
        if (!response.ok || response.redirected) throw new Error('대화를 불러오지 못했습니다. 권한 또는 삭제 여부를 확인해 주세요.');
        const data = await response.json();
        if (request.signal.aborted || !pane.isConnected) return;
        if (!Array.isArray(data.messages)) throw new Error('대화 내용을 확인할 수 없습니다.');
        const fragment = document.createDocumentFragment();
        for (const message of data.messages) {
          if (!['user','assistant'].includes(message.role)) continue;
          const turn = document.createElement('article'); turn.className = 'session-preview-turn';
          const author = document.createElement('strong'); author.textContent = message.role === 'user' ? '나' : '업무 에이전트';
          const text = document.createElement('p'); text.textContent = message.content || '';
          turn.append(author,text); fragment.append(turn);
        }
        notice.textContent = data.waiting ? '답변 작성 중인 대화입니다. 현재 저장된 내용만 표시합니다.' : (fragment.childNodes.length ? '저장된 대화 내용입니다.' : '저장된 메시지가 없습니다.');
        content.append(fragment);
      } catch (error) {
        if (error.name !== 'AbortError') notice.textContent = error.message || '불러오지 못했습니다. 검색 결과로 돌아가 다시 시도해 주세요.';
      }
    }
    async function load(append=false) {
      const current = ++token;
      controller?.abort(); controller = new AbortController();
      if (!append) { offset = 0; results.replaceChildren(); }
      more.disabled = true; status.textContent = '불러오는 중…';
      const archived = form.querySelector('select').value === '1';
      try {
        const query = new URLSearchParams({q:form.querySelector('input').value, archived:String(archived), offset:String(offset), scope:(form.querySelector('[data-scope]')?.value || 'title')});
        const response = await fetch('/api/chat/sessions?' + query, {cache:'no-store', signal:controller.signal});
        if (!response.ok || response.redirected) throw new Error(response.status === 404 ? '대화 기록 기능이 아직 서버에 반영되지 않았습니다. 왼쪽 최근 기록에서 대화를 열어 주세요.' : '기록을 불러오지 못했습니다. 검색을 눌러 다시 시도하세요.');
        const data = await response.json(); if (current !== token) return;
        for (const session of data.sessions) {
          const row = document.createElement('article'); row.className = 'session-row';
          const link = document.createElement('a'); link.href = '/chat/' + session.id; link.textContent = session.title || '이름 없는 대화'; link.title = link.textContent;
          link.addEventListener('click', event => {
            if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            event.preventDefault(); preview(session);
          });
          const meta = document.createElement('p'); meta.className = 'muted'; meta.textContent = (session.project_name || session.topic || '일반 대화') + ' · ' + session.created_at.slice(0,10);
          const actions = document.createElement('div'); actions.className = 'session-actions';
          const rename = document.createElement('button'); rename.type = 'button'; rename.className = 'secondary'; rename.textContent = '이름 변경';
          const archive = document.createElement('button'); archive.type = 'button'; archive.className = 'secondary'; archive.textContent = archived ? '복원' : '보관';
          const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'secondary'; remove.textContent = '삭제';
          remove.onclick = () => {
            if (row.querySelector('.session-delete')) return;
            const confirm = document.createElement('div'); confirm.className = 'session-rename session-delete';
            const warning = document.createElement('p'); warning.textContent = '“' + (session.title || '이름 없는 대화') + '” 대화와 수정 이력을 삭제할까요? 복구할 수 없습니다. 프로젝트와 등록 문서는 유지됩니다. 첨부 원본 파일은 삭제되지 않습니다.';
            const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'secondary'; cancel.textContent = '취소';
            const accept = document.createElement('button'); accept.type = 'button'; accept.textContent = '대화 삭제';
            const error = document.createElement('p'); error.setAttribute('role','alert');
            actions.hidden = true; confirm.append(warning,cancel,accept,error); row.append(confirm); cancel.focus();
            cancel.onclick = () => { confirm.remove(); actions.hidden = false; remove.focus(); };
            accept.onclick = async () => {
              cancel.disabled = accept.disabled = true;
              try {
                const response = await fetch('/api/chat/sessions/' + session.id, {method:'DELETE'});
                if (!response.ok || response.redirected) {
                  throw new Error(response.status === 409 ? '답변이 끝난 뒤 삭제해 주세요.' : '삭제하지 못했습니다. 잠시 후 다시 시도하세요.');
                }
                try {
                  if (sessionStorage.getItem('agentSession') === String(session.id)) sessionStorage.removeItem('agentSession');
                  sessionStorage.removeItem('chatCriteria:' + session.id);
                } catch (_) {}
                document.dispatchEvent(new CustomEvent('chat-session-deleted', {detail:{id:session.id}}));
                document.dispatchEvent(new CustomEvent('chat-history-changed'));
                if (location.pathname === '/chat/' + session.id) location.replace('/chat');
                else {
                  document.querySelectorAll('a[href="/chat/' + session.id + '"]').forEach(link => link.remove());
                  await load();
                  dialog.querySelector('[data-close]').focus();
                }
              } catch (failure) { error.textContent = failure.message; cancel.disabled = accept.disabled = false; }
            };
          };
          async function update(values) {
            rename.disabled = archive.disabled = true;
            try {
              const response = await fetch('/api/chat/sessions/' + session.id, {method:'POST', body:new URLSearchParams(values)});
              if (!response.ok || response.redirected) throw new Error();
              document.dispatchEvent(new CustomEvent('chat-history-changed'));
              await load();
            } catch (_) { status.textContent = '변경하지 못했습니다. 잠시 후 다시 시도하세요.'; }
            finally { rename.disabled = archive.disabled = false; }
          }
          rename.onclick = () => {
            if (row.querySelector('.session-rename')) return;
            const edit = document.createElement('form'); edit.className = 'session-rename';
            edit.innerHTML = '<input required maxlength="80" aria-label="대화 이름"><button type="submit">저장</button><button type="button" class="secondary">취소</button><p role="status"></p>';
            const field = edit.querySelector('input'); field.value = session.title;
            link.hidden = actions.hidden = true; row.prepend(edit); field.focus(); field.select();
            edit.querySelector('[type=button]').onclick = () => {edit.remove(); link.hidden = actions.hidden = false; rename.focus();};
            edit.onsubmit = async event => {
              event.preventDefault();
              if (!field.value.trim()) {edit.querySelector('[role=status]').textContent = '대화 이름을 입력하세요.'; return;}
              edit.querySelectorAll('button').forEach(button => button.disabled = true);
              await update({title:field.value.trim()});
              edit.querySelectorAll('button').forEach(button => button.disabled = false);
            };
          };
          archive.onclick = () => update({archived:String(!archived)});
          actions.append(rename, archive, remove); row.append(link, meta, actions); results.appendChild(row);
        }
        offset += data.sessions.length; more.hidden = !data.has_more;
        status.textContent = offset ? (archived ? '보관한 대화' : '최근 대화') + ' · ' + offset + '개' : '검색 결과가 없습니다.';
      } catch (error) { if(current===token && error.name !== 'AbortError') status.textContent = error.message || '기록을 불러오지 못했습니다. 검색을 눌러 다시 시도하세요.'; }
      finally { if(current===token) more.disabled = false; }
    }
    form.onsubmit = event => {event.preventDefault(); load();};
    form.querySelectorAll('select').forEach(select => { select.onchange = () => load(); });
    more.onclick = () => load(true); load();
  });
  let sidebarRevision = 0;
  document.addEventListener('chat-history-changed', async () => {
    const revision = ++sidebarRevision;
    try {
      const response = await fetch('/api/chat/sessions', {cache:'no-store'});
      if (!response.ok || response.redirected) return;
      const data = await response.json(), list = document.getElementById('chatSessionList');
      if (!list || revision !== sidebarRevision) return;
      const fragment = document.createDocumentFragment();
      data.sessions.slice(0,12).forEach(session => {
        const link = document.createElement('a'); link.className = 'side-item'; link.href = '/chat/'+session.id; link.title = session.title;
        const icon = document.createElement('iconify-icon'); icon.setAttribute('icon','solar:chat-line-linear'); icon.setAttribute('aria-hidden','true');
        const title = document.createElement('span'); title.className = 'side-session-title'; title.textContent = session.title;
        link.append(icon, title);
        link.classList.toggle('active', location.pathname===link.getAttribute('href')); fragment.appendChild(link);
      });
      list.replaceChildren(fragment);
      if (!data.sessions.length) list.textContent = '진행 중인 대화가 없습니다.';
    } catch (_) {}
  });
})();
