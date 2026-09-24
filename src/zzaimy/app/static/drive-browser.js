(() => {
  const root = document.querySelector('.drive-browser');
  if (!root) return;
  const get = name => root.querySelector('[data-drive-' + name + ']');
  const account = get('account'), path = get('path'), files = get('files'), status = get('status');
  const more = get('more'), retry = get('retry'), external = get('open');
  let trail = [{id:'root', name:'내 드라이브'}], generation = 0, next = '', failedPage = '';
  async function load(page = '') {
    const version = ++generation;
    more.hidden = retry.hidden = true;
    files.setAttribute('aria-busy', 'true');
    status.textContent = '폴더를 불러오는 중…';
    if (!page) files.replaceChildren();
    path.replaceChildren();
    trail.forEach((folder, index) => {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'secondary'; button.textContent = folder.name;
      if (index === trail.length - 1) button.setAttribute('aria-current', 'location');
      button.onclick = () => { trail = trail.slice(0, index + 1); load(); };
      path.append(button);
    });
    const folder = trail.at(-1).id;
    external.href = 'https://drive.google.com/drive/' + (folder === 'root' ? 'my-drive' : 'folders/' + encodeURIComponent(folder)) + '?authuser=' + encodeURIComponent(account.value);
    try {
      const response = await fetch('/api/chat-documents/browse?' + new URLSearchParams({account:account.value, folder, page}), {cache:'no-store'});
      if (!response.ok || response.redirected) throw new Error('폴더를 불러오지 못했습니다. 계정 연결과 접근 권한을 확인해 주세요.');
      const data = await response.json();
      if (version !== generation) return;
      data.files.forEach(file => {
        const isFolder = file.mimeType === 'application/vnd.google-apps.folder';
        const item = document.createElement(isFolder ? 'button' : 'a');
        item.className = 'drive-browser-item';
        const icon = document.createElement('iconify-icon');
        icon.setAttribute('icon', isFolder ? 'solar:folder-linear' : 'solar:document-text-linear');
        icon.setAttribute('aria-hidden', 'true');
        const name = document.createElement('span'); name.textContent = file.name;
        const kind = document.createElement('small'); kind.textContent = isFolder ? '폴더' : 'Drive에서 열기 ↗';
        item.append(icon, name, kind);
        if (isFolder) { item.type = 'button'; item.onclick = () => { trail.push({id:file.id, name:file.name}); load(); }; }
        else { item.href = 'https://drive.google.com/file/d/' + encodeURIComponent(file.id) + '/view?authuser=' + encodeURIComponent(account.value); item.target = '_blank'; item.rel = 'noopener'; }
        files.append(item);
      });
      next = data.next || ''; more.hidden = !next;
      status.textContent = files.children.length ? files.children.length + '개 표시' : '이 폴더에 파일이 없습니다.';
    } catch (error) {
      if (version !== generation) return;
      status.textContent = error.message; failedPage = page; retry.hidden = false;
    } finally { if (version === generation) files.setAttribute('aria-busy', 'false'); }
  }
  more.onclick = () => load(next);
  retry.onclick = () => load(failedPage);
  account.onchange = () => { trail = [{id:'root', name:'내 드라이브'}]; load(); };
  load();
})();
