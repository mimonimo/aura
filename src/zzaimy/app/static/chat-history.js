(() => {
  document.addEventListener('click', event => {
    if (!event.target.closest('[data-chat-history]')) return;
    if (document.querySelector('.session-manager')) return;
    const dialog = document.createElement('dialog');
    dialog.className = 'session-manager';
    dialog.innerHTML = '<header><h2>대화 기록</h2><button type="button" class="secondary" data-close>닫기</button></header><p class="muted">작은 챗봇과 전체 화면의 대화가 함께 저장됩니다.</p><form class="session-search"><input type="search" aria-label="대화 이름 또는 프로젝트 검색" placeholder="대화 이름·프로젝트 검색"><select aria-label="기록 구분"><option value="0">진행 중인 대화</option><option value="1">보관한 대화</option></select><button type="submit" class="secondary">검색</button></form><p role="status"></p><div class="session-results"></div><button type="button" class="secondary" data-more hidden>더 보기</button>';
    document.body.appendChild(dialog); dialog.showModal();
    dialog.querySelector('[data-close]').onclick = () => dialog.close();
    dialog.addEventListener('close', () => { token++; controller?.abort(); dialog.remove(); });
    const form = dialog.querySelector('form'), results = dialog.querySelector('.session-results');
    const status = dialog.querySelector('[role=status]'), more = dialog.querySelector('[data-more]');
    let offset = 0, token = 0, controller;
    async function load(append=false) {
      const current = ++token;
      controller?.abort(); controller = new AbortController();
      if (!append) { offset = 0; results.replaceChildren(); }
      more.disabled = true; status.textContent = '불러오는 중…';
      const archived = form.querySelector('select').value === '1';
      try {
        const query = new URLSearchParams({q:form.querySelector('input').value, archived:String(archived), offset:String(offset)});
        const response = await fetch('/api/chat/sessions?' + query, {cache:'no-store', signal:controller.signal});
        if (!response.ok || response.redirected) throw new Error(response.status === 404 ? '대화 기록 기능이 아직 서버에 반영되지 않았습니다. 왼쪽 최근 기록에서 대화를 열어 주세요.' : '기록을 불러오지 못했습니다. 검색을 눌러 다시 시도하세요.');
        const data = await response.json(); if (current !== token) return;
        for (const session of data.sessions) {
          const row = document.createElement('article'); row.className = 'session-row';
          const link = document.createElement('a'); link.href = '/chat/' + session.id; link.textContent = session.title || '이름 없는 대화'; link.title = link.textContent;
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
        status.textContent = offset ? (archived ? '보관한 대화는 복원할 수 있습니다.' : '최근 대화 순입니다. 보관해도 내용은 삭제되지 않습니다.') : '해당 대화가 없습니다.';
      } catch (error) { if(current===token && error.name !== 'AbortError') status.textContent = error.message || '기록을 불러오지 못했습니다. 검색을 눌러 다시 시도하세요.'; }
      finally { if(current===token) more.disabled = false; }
    }
    form.onsubmit = event => {event.preventDefault(); load();};
    form.querySelector('select').onchange = () => load();
    more.onclick = () => load(true); load();
  });
  document.addEventListener('chat-history-changed', async () => {
    try {
      const response = await fetch('/api/chat/sessions', {cache:'no-store'});
      if (!response.ok || response.redirected) return;
      const data = await response.json(), list = document.getElementById('chatSessionList');
      if (!list) return; list.replaceChildren();
      data.sessions.slice(0,12).forEach(session => {
        const link = document.createElement('a'); link.className = 'side-item'; link.href = '/chat/'+session.id; link.textContent = session.title; link.title = session.title;
        link.classList.toggle('active', location.pathname===link.getAttribute('href')); list.appendChild(link);
      });
      if (!data.sessions.length) list.textContent = '진행 중인 대화가 없습니다.';
    } catch (_) {}
  });
})();
