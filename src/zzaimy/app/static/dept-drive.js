/* 부서 드라이브 — 부서가 함께 쓸 구글 계정을 연결하는 작은 카드(templates/_dept_drive.html)와 부서 폴더 위치 고르기 창.
   카드: <div data-dept-drive data-dept="부서"> 를 찾아 GET /team/drive?dept= 로 상태를 받아 그린다.
   위치 고르기: AuraDrivePicker.open({dept, folderName}) → Promise 취소 null, 고르면 {id, path} — 파인더 꼴(aura-finder.css)로
   왼쪽 내 드라이브·공유 드라이브, 가운데 폴더 목록(한 번 눌러 고르기, 두 번 누르거나 Enter 로 들어가기), 위 경로·뒤로·찾기.
   폴더 목록은 부서 계정으로 GET /team/drive/folders(서버가 권한을 다시 본다). 모든 이름은 textContent 로만 넣는다. */
(function () {
  'use strict';
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const icon = name => { const i = document.createElement('iconify-icon'); i.setAttribute('icon', name); i.setAttribute('aria-hidden', 'true'); return i; };
  async function getJSON(url) {
    const r = await fetch(url, { headers: { Accept: 'application/json' }, credentials: 'same-origin', cache: 'no-store' });
    let j = null; try { j = await r.json(); } catch (_) {}
    if (!r.ok) { const e = new Error((j && (j.detail || j.msg)) || '불러오지 못했습니다'); e.status = r.status; throw e; }
    return j;
  }
  async function postForm(url, data) {
    const body = new URLSearchParams(data);
    const r = await fetch(url, { method: 'POST', body, headers: { Accept: 'application/json' }, credentials: 'same-origin' });
    let j = null; try { j = await r.json(); } catch (_) {}
    if (!r.ok || !j || j.ok === false) throw new Error((j && (j.msg || j.detail)) || '처리하지 못했습니다');
    return j;
  }

  // ---------------------------------------------------------------- 위치 고르기 창
  function openPicker(opts) {
    return new Promise(resolve => {
      const d = el('dialog', 'aura-finder dd-picker'); d.setAttribute('aria-label', '부서 폴더 위치');
      d.innerHTML = '<header><h2></h2><form class="af-search" role="search"><iconify-icon icon="solar:magnifer-linear" aria-hidden="true"></iconify-icon><input type="search" name="q" placeholder="이 위치에서 폴더 찾기" aria-label="이 위치에서 폴더 찾기"></form><button type="button" class="af-x" aria-label="닫기">×</button></header>'
        + '<div class="af-body"><nav class="af-side" aria-label="위치"></nav><section class="af-main"><div class="af-bar"><button type="button" class="af-back" aria-label="뒤로" disabled>‹</button><div class="af-path"></div></div><div class="af-list" role="listbox" aria-label="폴더"></div></section></div>'
        + '<footer><div class="af-sel"><span class="af-sel-main"></span><span class="af-sel-where"></span></div><span class="af-msg" role="status"></span><button type="button" class="af-btn" data-cancel>취소</button><button type="button" class="af-btn primary" data-ok disabled>여기로 옮기기</button></footer>';
      d.querySelector('h2').textContent = opts.title || '부서 폴더 위치';
      document.body.append(d); d.showModal();
      const side = d.querySelector('.af-side'), list = d.querySelector('.af-list'), path = d.querySelector('.af-path'), back = d.querySelector('.af-back');
      const selMain = d.querySelector('.af-sel-main'), selWhere = d.querySelector('.af-sel-where'), msg = d.querySelector('.af-msg'), ok = d.querySelector('[data-ok]');
      const q = d.querySelector('[name=q]');
      let done = false, trail = [], current = null, picked = null, stamp = 0, inShared = false, next = '';
      const finish = v => { if (done) return; done = true; resolve(v); d.close(); };
      d.querySelector('.af-x').onclick = () => finish(null); d.querySelector('[data-cancel]').onclick = () => finish(null);
      d.addEventListener('close', () => { if (!done) { done = true; resolve(null); } d.remove(); });
      d.addEventListener('click', e => { if (e.target === d) finish(null); });
      const target = () => picked || (current && { id: current.id, name: current.name, writable: current.writable, trail: trail.slice() });
      function paint() {
        const t = target();
        const names = t ? (picked ? [...trail.map(x => x.name), picked.name] : trail.map(x => x.name)) : [];
        selMain.replaceChildren(); selMain.classList.toggle('is-hint', !t);
        if (!t) { selMain.textContent = '옮길 위치를 고르세요'; selWhere.textContent = ''; ok.disabled = true; return; }
        selMain.append(icon('solar:folder-bold'), el('b', null, '선택: ' + names.join(' / ')));
        selWhere.textContent = t.writable ? '여기로 ' + (opts.folderName || '부서') + ' 폴더를 옮깁니다' : '이 폴더에는 쓸 권한이 없습니다';
        ok.disabled = !t.writable;
        msg.textContent = inShared ? '공유 드라이브는 그 드라이브 구성원 모두가 봅니다' : '';
      }
      const busy = t => list.replaceChildren(el('p', 'af-empty', t));
      function crumbs() {
        path.replaceChildren();
        trail.forEach((p, i) => {
          if (i) path.append(el('span', 'af-sep', '›'));
          const b = el('button', null, p.name); b.type = 'button'; b.disabled = i === trail.length - 1;
          b.onclick = () => { trail = trail.slice(0, i + 1); load(); };
          path.append(b);
        });
        back.disabled = trail.length < 2;
      }
      const rows = () => [...list.querySelectorAll('.af-row')];
      function folderRow(f) {
        const r = el('div', 'af-row'); r.dataset.kind = 'folder'; r.setAttribute('role', 'option'); r.tabIndex = 0; r.setAttribute('aria-selected', 'false');
        r.append(el('span', 'af-check'), icon('solar:folder-bold'), el('span', 'af-name', f.name), el('span', 'af-meta', f.writable ? '' : '보기만'));
        const select = () => { rows().forEach(x => { x.classList.remove('on'); x.setAttribute('aria-selected', 'false'); }); r.classList.add('on'); r.setAttribute('aria-selected', 'true'); picked = { id: f.id, name: f.name, writable: !!f.writable }; paint(); };
        const enter = () => { trail.push({ id: f.id, name: f.name }); load(); };
        r.onclick = select; r.ondblclick = enter;
        r.onkeydown = e => {
          if (e.key === 'Enter') { e.preventDefault(); enter(); }
          else if (e.key === ' ') { e.preventDefault(); select(); }
          else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); const all = rows(), n = all[all.indexOf(r) + (e.key === 'ArrowDown' ? 1 : -1)]; if (n) { n.focus(); n.click(); } }
          else if (e.key === 'Backspace' && trail.length > 1) { e.preventDefault(); trail.pop(); load(); }
        };
        list.append(r);
      }
      async function load(more) {
        const my = ++stamp, here = trail[trail.length - 1];
        picked = null; if (!more) { current = null; busy('불러오는 중…'); next = ''; }
        crumbs(); paint();
        const p = new URLSearchParams({ dept: opts.dept, parent: here.id, q: q.value.trim(), token: more ? next : '' });
        let j; try { j = await getJSON('/team/drive/folders?' + p); } catch (e) { if (my === stamp) busy(e.message); return; }
        if (my !== stamp || !d.open) return;
        if (!more) list.replaceChildren();
        current = j.current ? { id: j.current.id, name: here.name, writable: !!j.current.writable } : null;
        (j.items || []).forEach(folderRow);
        next = j.next || '';
        if (next) { const m = el('button', 'af-btn', '더 보기'); m.type = 'button'; m.style.margin = '8px auto'; m.style.display = 'block'; m.onclick = () => { m.remove(); load(true); }; list.append(m); }
        if (!list.children.length) busy(q.value.trim() ? '찾는 폴더가 없습니다' : '아래 폴더가 없습니다 · 여기로 옮길 수 있습니다');
        paint();
      }
      function place(label, ic, id, shared) {
        const b = el('button', 'af-place'); b.type = 'button'; b.append(icon(ic), el('span', null, label)); b.title = label;
        b.onclick = () => { side.querySelectorAll('.af-place').forEach(x => x.classList.toggle('on', x === b)); inShared = shared; q.value = ''; trail = [{ id, name: label }]; load(); };
        side.append(b); return b;
      }
      const mine = place('내 드라이브', 'solar:folder-with-files-linear', 'root', false);
      side.append(el('div', 'af-group', '공유 드라이브'));
      const sharedHint = el('p', 'af-note', '불러오는 중…'); side.append(sharedHint);
      getJSON('/team/drive/folders?' + new URLSearchParams({ dept: opts.dept, parent: 'drives' })).then(j => {
        sharedHint.remove();
        const ds = j.items || [];
        if (!ds.length) side.append(el('p', 'af-note', '없음'));
        ds.forEach(x => place(x.name, 'solar:server-square-linear', x.id, true));
      }).catch(() => { sharedHint.textContent = '불러오지 못했습니다'; });
      back.onclick = () => { if (trail.length > 1) { trail.pop(); load(); } };
      d.querySelector('.af-search').addEventListener('submit', e => { e.preventDefault(); load(); });
      ok.onclick = () => { const t = target(); if (t && t.writable) finish({ id: t.id, path: (picked ? [...trail.map(x => x.name), picked.name] : trail.map(x => x.name)).join(' / ') }); };
      mine.click();
    });
  }
  window.AuraDrivePicker = { open: openPicker };

  // ---------------------------------------------------------------- 부서 쪽 카드
  function confirmBox(text, okLabel) {
    return new Promise(resolve => {
      const d = el('dialog', 'dd-confirm'); d.setAttribute('aria-label', '확인');
      const p = el('p', null, text), row = el('div', 'dd-confirm-row');
      const no = el('button', 'secondary', '취소'), yes = el('button', null, okLabel || '확인');
      no.type = yes.type = 'button'; row.append(no, yes); d.append(p, row); document.body.append(d); d.showModal(); no.focus();
      const end = v => { resolve(v); d.close(); };
      no.onclick = () => end(false); yes.onclick = () => end(true);
      d.addEventListener('close', () => { d.remove(); resolve(false); });
    });
  }
  window.AuraDriveConfirm = confirmBox;

  function backHere() {
    const u = new URL(location.href); u.searchParams.delete('ok'); u.searchParams.delete('err');
    return u.pathname + u.search;
  }

  function render(box, s) {
    box.replaceChildren();
    const head = el('div', 'dd-head'); head.append(icon('solar:folder-cloud-linear'), el('b', null, '부서 드라이브'));
    box.append(head);
    const body = el('div', 'dd-body'); box.append(body);
    const line = (t, cls) => { const p = el('p', cls || 'dd-line', t); body.append(p); return p; };
    if (s.connected) {
      const a = el('a', 'mini-btn'); a.href = 'https://drive.google.com/drive/folders/' + encodeURIComponent(s.folder); a.target = '_blank'; a.rel = 'noopener';
      a.append(icon('solar:square-top-down-linear'), document.createTextNode(s.folder_name)); body.append(a);
      line((s.shared ? '공용 계정 ' : '개인 계정 ') + s.account + ' · 접근 ' + s.granted + '명' + (s.unlinked ? ' · 구글 미연결 ' + s.unlinked + '명' : ''));
      if (s.personal) line('개인 계정으로 연결돼 있습니다 — 부서가 함께 쓸 계정으로 바꾸기를 권합니다', 'dd-line dd-warn');
      if (s.error) line(s.error, 'dd-line dd-bad');
    } else {
      line('연결 전', 'dd-line muted');
    }
    const msg = el('p', 'dd-msg'); msg.setAttribute('role', 'status'); box.append(msg);
    if (!s.can_manage) return;
    const acts = el('div', 'dd-acts'); box.append(acts);
    const btn = (label, cls, fn) => { const b = el('button', cls || 'secondary', label); b.type = 'button'; b.onclick = fn; acts.append(b); return b; };
    const run = async (url, data, b) => {
      msg.textContent = ''; msg.classList.remove('bad'); if (b) b.disabled = true;
      try {
        const j = await postForm(url, { dept: s.dept, back: backHere(), ...(data || {}) });
        if (j.drive) render(box, j.drive);
        box.querySelector('.dd-msg').textContent = j.msg || '';
      }
      catch (e) { msg.textContent = e.message; msg.classList.add('bad'); if (b) b.disabled = false; }
    };
    const connect = () => {
      // 구글 계정 고르기 화면으로 간다(폼 그대로 — 되돌아오면 이 화면으로)
      const f = el('form'); f.method = 'post'; f.action = '/team/drive/connect'; f.hidden = true;
      [['dept', s.dept], ['back', backHere()]].forEach(([k, v]) => { const i = el('input'); i.type = 'hidden'; i.name = k; i.value = v; f.append(i); });
      document.body.append(f); f.submit();
    };
    if (!s.app_ready) { line('구글 앱 등록 전이라 아직 연결할 수 없습니다', 'dd-line muted'); return; }
    if (!s.connected) {
      btn('구글 드라이브 연결', 'dd-primary', connect);
      line('부서가 함께 쓸 학교 계정(' + (s.domain ? '@' + s.domain : '학교 계정') + ', 부서 공용 메일 권장)으로 로그인합니다', 'dd-line dd-note');
      return;
    }
    btn(s.personal ? '공용 계정으로 바꾸기' : '다시 연결', 'secondary', connect);
    btn('위치 바꾸기', 'secondary', async () => {
      const got = await openPicker({ dept: s.dept, folderName: s.folder_name, title: s.dept + ' 부서 폴더 위치' });
      if (got) run('/team/drive/move', { parent: got.id });
    });
    const sync = btn('권한 맞추기', 'secondary', () => run('/team/drive/sync', {}, sync));
    sync.title = '부서 사람이 바뀌었거나 구글을 새로 연결했을 때 — 보통은 저절로 맞춥니다';
    btn('연결 끊기', 'dd-danger', async () => {
      if (await confirmBox(s.dept + ' 부서 드라이브 연결을 끊을까요? 부서 사람의 폴더 권한을 거두고, 폴더와 문서는 ' + s.account + ' 드라이브에 남습니다.', '연결 끊기')) run('/team/drive/disconnect');
    });
  }

  async function mount(box) {
    const dept = box.dataset.dept || '';
    if (!dept) { box.hidden = true; return; }
    box.textContent = '부서 드라이브 불러오는 중…';
    try { render(box, await getJSON('/team/drive?dept=' + encodeURIComponent(dept))); }
    catch (e) { if (e.status === 403) box.hidden = true; else box.textContent = e.message; }
  }
  const start = () => document.querySelectorAll('[data-dept-drive]').forEach(mount);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
  window.AuraDeptDrive = { mount, render };
})();
