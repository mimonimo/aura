(() => {
  document.addEventListener('click', event => {
    const opener = event.target.closest('[data-project-search]');
    if (!opener || document.querySelector('.project-search-dialog')) return;
    const dialog = document.createElement('dialog');
    dialog.className = 'project-search-dialog';
    dialog.setAttribute('aria-labelledby', 'projectSearchTitle');
    dialog.innerHTML = '<header><h2 id="projectSearchTitle">진행 중 프로젝트 찾기</h2><button type="button" class="dialog-close" data-close aria-label="닫기" title="닫기"><iconify-icon icon="solar:close-circle-linear"></iconify-icon></button></header><form role="search"><input type="search" maxlength="200" aria-label="프로젝트 이름 또는 업무" placeholder="프로젝트 이름 또는 업무" autofocus><button class="secondary" type="submit">찾기</button></form><p role="status" aria-live="polite"></p><div class="project-search-results"></div><button type="button" class="secondary" data-more hidden>더 보기</button><a class="project-search-past" href="/projects/archived">지난 사업에서 찾기</a>';
    const form = dialog.querySelector('form'), input = dialog.querySelector('input');
    const results = dialog.querySelector('.project-search-results'), status = dialog.querySelector('[role=status]');
    const more = dialog.querySelector('[data-more]'), past = dialog.querySelector('.project-search-past');
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
      if (!append) { offset = 0; more.hidden = true; }      // 지난 결과는 새 결과가 올 때까지 둔다 — 창이 줄었다 늘지 않게
      more.disabled = true; status.textContent = '검색 중…';
      try {
        const response = await fetch('/api/projects/search?' + new URLSearchParams({q:input.value.trim(),offset}), {cache:'no-store',signal:controller.signal});
        if (!response.ok || response.redirected) throw new Error('프로젝트를 불러오지 못했습니다. 다시 검색해 주세요.');
        const data = await response.json();
        if (current !== revision || !dialog.open) return;
        if (!append) results.replaceChildren();
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
        const term = input.value.trim();
        status.textContent = offset ? (term ? '「' + term + '」 진행 중 프로젝트 ' : '진행 중 프로젝트 ') + offset + '개 · 최근 작업순'
          : (term ? '진행 중 프로젝트에 「' + term + '」가 없습니다. 지난 사업에서도 찾아보세요.' : '진행 중인 프로젝트가 없습니다.');
        // 같은 말로 지난 사업(보관된 사업 목록)에서 찾기 — 0건이면 더 눈에 띄게
        past.href = '/projects/archived' + (term ? '?' + new URLSearchParams({q:term}) : '');
        past.classList.toggle('is-strong', !offset && !!term);
      } catch (error) {
        if (current === revision && error.name !== 'AbortError') status.textContent = error.message;
      } finally { if (current === revision) more.disabled = false; }
    }
    form.onsubmit = event => { event.preventDefault(); load(); };
    input.addEventListener('input', event => {
      clearTimeout(timer);
      // 입력을 지우면 기다리지 않고 바로 최근 작업순 목록으로. 결과 칸 높이는 CSS 최소 높이로 고정해 창이 뛰지 않는다
      if (!input.value.trim()) { load(); return; }
      if (!event.isComposing) timer = setTimeout(() => load(), 200);
    });
    input.addEventListener('compositionend', () => { clearTimeout(timer); timer = setTimeout(() => load(), 200); });
    more.onclick = () => load(true);
    document.body.append(dialog); dialog.showModal(); load();
  });
})();
