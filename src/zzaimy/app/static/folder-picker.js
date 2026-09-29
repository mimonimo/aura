(() => {
  const modal = document.querySelector('#deptEdit');
  if (!modal) return;
  const el = key => modal.querySelector(`[data-folder-${key}]`);
  let path = [], current = null, next = '', serial = 0;
  async function load(more = false) {
    const stamp = ++serial;
    current = null;
    el('select').disabled = true;
    el('more').hidden = true;
    el('connect').hidden = true;
    el('status').textContent = '폴더를 불러오는 중…';
    el('path').textContent = path.map(x => x.name).join(' / ');
    el('back').disabled = path.length < 2;
    if (!more) el('list').replaceChildren();
    const params = new URLSearchParams({parent: path.at(-1).id, q: el('search').value, token: more ? next : ''});
    try {
      const response = await fetch('/dev/google/folders?' + params);
      const data = await response.json();
      if (stamp !== serial) return;
      if (!response.ok) {
        el('connect').hidden = !data.connect;
        throw new Error(data.error || '폴더를 불러오지 못했습니다.');
      }
      current = data.current;
      next = data.next;
      el('more').hidden = !next;
      el('select').disabled = !current?.writable;
      el('status').textContent = data.items.length ? (current && !current.writable ? '읽기 전용 위치입니다. 하위 폴더는 열어볼 수 있습니다.' : '') : '이 위치에 표시할 폴더가 없습니다.';
      for (const item of data.items) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = item.name + (item.writable === false ? ' · 읽기 전용' : '') + ' ›';
        button.addEventListener('click', () => {path.push(item); el('search').value = ''; load();});
        el('list').append(button);
      }
    } catch (error) {
      if (stamp === serial) el('status').textContent = error.message || '목록을 불러오지 못했습니다. 다시 시도해 주세요.';
    }
  }
  function home(id) {
    path = [{id, name: id === 'root' ? '내 드라이브' : '공유 드라이브'}];
    el('search').value = '';
    load();
  }
  window.openDepartmentFolders = () => home('root');
  modal.querySelectorAll('[data-folder-home]').forEach(b => b.addEventListener('click', () => home(b.dataset.folderHome)));
  el('back').addEventListener('click', () => {if (path.length > 1) {path.pop(); el('search').value = ''; load();}});
  el('find').addEventListener('click', () => load());
  el('search').addEventListener('keydown', e => {if (e.key === 'Enter') {e.preventDefault(); load();}});
  el('more').addEventListener('click', () => load(true));
  el('select').addEventListener('click', () => {
    if (!current?.writable) return;
    modal.querySelector('[name=root]').value = current.id;
    el('selection').textContent = '저장 위치: ' + path.map(x => x.name).join(' / ');
  });
  el('clear').addEventListener('click', () => {
    modal.querySelector('[name=root]').value = '';
    el('selection').textContent = '저장 위치: 각자 내 드라이브';
  });
})();
