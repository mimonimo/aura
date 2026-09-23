(() => {
  document.addEventListener('click', event => {
    const opener = event.target.closest('[data-project-search]');
    if (!opener || document.querySelector('.project-search-dialog')) return;
    const dialog = document.createElement('dialog');
    dialog.className = 'project-search-dialog';
    dialog.setAttribute('aria-labelledby', 'projectSearchTitle');
    dialog.innerHTML = '<header><h2 id="projectSearchTitle">프로젝트 검색</h2><button type="button" class="secondary" data-close>닫기</button></header><form role="search"><input type="search" maxlength="200" aria-label="프로젝트명 또는 업무 분야" placeholder="프로젝트명 또는 업무 분야" autofocus><button class="secondary" type="submit">검색</button></form><p role="status" aria-live="polite"></p><div class="project-search-results"></div><button type="button" class="secondary" data-more hidden>더 보기</button>';
    const form = dialog.querySelector('form'), input = dialog.querySelector('input');
    const results = dialog.querySelector('.project-search-results'), status = dialog.querySelector('[role=status]');
    const more = dialog.querySelector('[data-more]');
    let controller, revision = 0, offset = 0, timer;
    dialog.querySelector('[data-close]').onclick = () => dialog.close();
    dialog.addEventListener('close', () => {
      clearTimeout(timer); revision++; controller?.abort(); dialog.remove();
      if (opener.isConnected) opener.focus({preventScroll:true});
    });
    async function load(append = false) {
      clearTimeout(timer);
      const current = ++revision;
      controller?.abort(); controller = new AbortController();
      if (!append) { offset = 0; results.replaceChildren(); more.hidden = true; }
      more.disabled = true; status.textContent = '검색 중…';
      try {
        const response = await fetch('/api/projects/search?' + new URLSearchParams({q:input.value.trim(),offset}), {cache:'no-store',signal:controller.signal});
        if (!response.ok || response.redirected) throw new Error('프로젝트를 불러오지 못했습니다. 다시 검색해 주세요.');
        const data = await response.json();
        if (current !== revision || !dialog.open) return;
        const fragment = document.createDocumentFragment();
        for (const project of data.projects) {
          const link = document.createElement('a'); link.href = '/project/' + project.id;
          const name = document.createElement('strong'); name.textContent = project.name;
          const meta = document.createElement('span');
          meta.textContent = project.sector_label + ' · 문서 ' + project.n_docs + ' · 대화 ' + project.n_chats;
          const date = document.createElement('small'); date.textContent = '최근 작업 ' + project.last_activity.slice(0,10);
          link.append(name, meta, date); fragment.append(link);
        }
        results.append(fragment); offset += data.projects.length;
        more.hidden = !data.has_more;
        status.textContent = offset ? '프로젝트 ' + offset + '개 · 최근 작업순' : (input.value.trim() ? '검색 결과가 없습니다.' : '등록된 프로젝트가 없습니다.');
      } catch (error) {
        if (current === revision && error.name !== 'AbortError') status.textContent = error.message;
      } finally { if (current === revision) more.disabled = false; }
    }
    form.onsubmit = event => { event.preventDefault(); load(); };
    input.addEventListener('input', event => {
      clearTimeout(timer); revision++; controller?.abort(); more.hidden = true;
      results.replaceChildren(); status.textContent = '검색 중…';
      if (!event.isComposing) timer = setTimeout(() => load(), 200);
    });
    input.addEventListener('compositionend', () => { clearTimeout(timer); timer = setTimeout(() => load(), 200); });
    more.onclick = () => load(true);
    document.body.append(dialog); dialog.showModal(); load();
  });
})();
