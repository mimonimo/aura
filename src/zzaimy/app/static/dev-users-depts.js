/* 사용자·계정 화면의 부서 표 — 맨 위 부서 아래로 하위 부서 펼치기·접기(보는 사람마다 기억), 찾기, 상위 부서 창, 부서 사람 창,
   동작(지우기·상위 부서 저장·연결 끊기·권한 맞추기·부서 추가)은 새로고침 없이 fetch 로 보내고(Accept: application/json → {ok, msg})
   부서 카드만 다시 받아 바꾼다(스크롤·펼침 유지). 이름은 모두 textContent 로만 넣는다. */
(function () {
  'use strict';
  const KEY = 'aura:dev-users:open-depts';
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => [...(root || document).querySelectorAll(sel)];
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const card = () => $('#depts [data-dept-card]');
  let opened = new Set();
  try { opened = new Set(JSON.parse(localStorage.getItem(KEY) || '[]')); } catch (_) {}
  const remember = () => { try { localStorage.setItem(KEY, JSON.stringify([...opened])); } catch (_) {} };
  let data = { depts: [], tops: [], people: {} };
  function readData() { try { data = JSON.parse($('[data-dept-json]', card()).textContent); } catch (_) {} }

  function openModal(id) { const m = $(id); if (m) m.classList.add('open'); return m; }
  function closeModal(m) { if (m) m.classList.remove('open'); }
  function toast(text, bad) {
    const t = $('.ua-toast'); if (!t) return;
    t.textContent = text; t.classList.toggle('bad', !!bad); t.hidden = false;
    clearTimeout(toast.timer); toast.timer = setTimeout(() => { t.hidden = true; }, 4000);
  }

  // ---------------- 펼치기·접기·찾기
  function paintTree() {
    const q = ($('[data-dept-find]', card()) || { value: '' }).value.trim().toLowerCase();
    const rows = $$('tr[data-dept-row]', card());
    const hit = r => !q || r.dataset.deptRow.toLowerCase().includes(q);
    const tops = rows.filter(r => !r.dataset.top);
    let shown = 0;
    tops.forEach(t => {
      const name = t.dataset.deptRow, kids = rows.filter(r => r.dataset.top === name);
      const kidHit = kids.some(hit), open = opened.has(name) || (q && kidHit);
      t.hidden = !(hit(t) || kidHit);
      kids.forEach(k => { k.hidden = t.hidden || !open || (q && !hit(k) && !hit(t)); });
      const b = $('[data-tree-toggle]', t);
      if (b) { b.textContent = open ? '접기' : '펼치기 · ' + b.dataset.kids; b.setAttribute('aria-expanded', open ? 'true' : 'false'); }
      shown += t.hidden ? 0 : 1;
    });
    const none = $('[data-dept-none]', card()); if (none) none.hidden = !!shown || !tops.length;
    const all = $('[data-tree-all]', card());
    if (all) all.textContent = tops.some(t => $('[data-tree-toggle]', t) && !opened.has(t.dataset.deptRow)) ? '모두 펼치기' : '모두 접기';
  }

  // ---------------- 보내기와 카드 새로 받기
  async function post(url, fields) {
    const r = await fetch(url, { method: 'POST', body: new URLSearchParams(fields), headers: { Accept: 'application/json' }, credentials: 'same-origin' });
    let j = null; try { j = await r.json(); } catch (_) {}
    if (!r.ok || !j || j.ok === false) throw new Error((j && (j.msg || j.detail)) || '처리하지 못했습니다(' + r.status + ')');
    return j;
  }
  async function refresh() {
    const y = window.scrollY;
    const r = await fetch('/dev/users', { credentials: 'same-origin', cache: 'no-store' });
    if (!r.ok) return;
    const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    const fresh = doc.querySelector('#depts [data-dept-card]');
    const q = ($('[data-dept-find]', card()) || { value: '' }).value;
    if (fresh) card().replaceWith(fresh);
    const f = $('[data-dept-find]', card()); if (f) f.value = q;
    readData(); paintTree(); window.scrollTo(0, y);
  }
  async function act(form) {
    const row = form.closest('tr'), msg = row && $('.ua-rowmsg', row), btn = $('button[type=submit]', form);
    if (msg) msg.textContent = '';
    if (btn) btn.disabled = true;
    try { const j = await post(form.action, Object.fromEntries(new FormData(form))); toast(j.msg || '저장했습니다'); await refresh(); }
    catch (e) { if (msg) msg.textContent = e.message; else toast(e.message, true); if (btn) btn.disabled = false; }
  }
  function confirmBox(text, okLabel) {
    return new Promise(resolve => {
      const m = openModal('#uaConfirm'); if (!m) { resolve(true); return; }
      $('[data-cf-text]', m).textContent = text;
      const ok = $('[data-cf-ok]', m); ok.textContent = okLabel || '확인'; ok.focus();
      const done = v => { m.removeEventListener('click', onClose, true); ok.onclick = null; closeModal(m); resolve(v); };
      const onClose = e => { if (e.target === m || e.target.closest('[data-modal-close]')) done(false); };
      m.addEventListener('click', onClose, true);
      ok.onclick = () => done(true);
    });
  }

  // ---------------- 상위 부서 창
  const pp = { dept: '', value: '', onPick: null, focus: -1 };
  function ppPaint() {
    const m = $('#parentPick'), list = $('[data-pp-list]', m), q = $('[data-pp-find]', m).value.trim().toLowerCase();
    list.replaceChildren();
    const opt = (name, label) => {
      const b = el('button', 'ua-opt'); b.type = 'button'; b.setAttribute('role', 'option'); b.dataset.value = name;
      b.setAttribute('aria-selected', name === pp.value ? 'true' : 'false'); b.append(el('span', null, label || name));
      if (name === pp.value) b.append(el('span', 'ua-sub', '지금'));
      b.onclick = () => { pp.value = name; ppPaint(); };
      b.ondblclick = () => { pp.value = name; ppSave(); };
      list.append(b);
    };
    if (!q) opt('', '없음(맨 위)');
    const cand = data.depts.filter(d => d !== pp.dept && (!q || d.toLowerCase().includes(q)));
    const tops = cand.filter(d => data.tops.includes(d)), rest = cand.filter(d => !data.tops.includes(d));
    if (tops.length) { list.append(el('h3', null, '처·단')); tops.forEach(d => opt(d)); }
    if (rest.length) { list.append(el('h3', null, '그 밖의 부서')); rest.forEach(d => opt(d)); }
    if (!cand.length && q) list.append(el('p', 'muted', '찾는 부서가 없습니다'));
    pp.focus = -1;
  }
  function ppOpen(dept, value, onPick) {
    pp.dept = dept; pp.value = value || ''; pp.onPick = onPick || null;
    const m = openModal('#parentPick');
    $('[data-pp-title]', m).textContent = dept ? dept + ' 상위 부서' : '상위 부서';
    $('[data-pp-msg]', m).textContent = '';
    const f = $('[data-pp-find]', m); f.value = ''; ppPaint(); f.focus();
  }
  async function ppSave() {
    const m = $('#parentPick'), msg = $('[data-pp-msg]', m), save = $('[data-pp-save]', m);
    if (pp.onPick) { pp.onPick(pp.value); closeModal(m); return; }
    save.disabled = true; msg.textContent = '';
    try {
      const j = await post('/dev/users/dept-parent', { dept: pp.dept, parent: pp.value });
      if (pp.value) opened.add(pp.value), remember();
      closeModal(m); toast(j.msg || '저장했습니다'); await refresh();
    } catch (e) { msg.textContent = e.message; }
    save.disabled = false;
  }
  function ppKeys(e) {
    const opts = $$('#parentPick .ua-opt');
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); if (!opts.length) return;
      pp.focus = Math.max(0, Math.min(opts.length - 1, pp.focus + (e.key === 'ArrowDown' ? 1 : -1)));
      opts.forEach((o, i) => o.classList.toggle('focus', i === pp.focus)); opts[pp.focus].scrollIntoView({ block: 'nearest' });
      pp.value = opts[pp.focus].dataset.value; opts.forEach(o => o.setAttribute('aria-selected', o.dataset.value === pp.value ? 'true' : 'false'));
    } else if (e.key === 'Enter') { e.preventDefault(); ppSave(); }
  }

  // ---------------- 부서 사람 창
  function pvOpen(dept) {
    const m = openModal('#peopleView'), f = $('[data-pv-find]', m);
    $('[data-pv-title]', m).textContent = dept + ' 사람 ' + (data.people[dept] || []).length + '명';
    $('[data-pv-team]', m).href = '/team?dept=' + encodeURIComponent(dept);
    f.value = ''; f.dataset.dept = dept; pvPaint(); f.focus();
  }
  function pvPaint() {
    const m = $('#peopleView'), f = $('[data-pv-find]', m), list = $('[data-pv-list]', m), q = f.value.trim().toLowerCase();
    const rows = (data.people[f.dataset.dept] || []).filter(p => !q || (p.name + ' ' + p.uid).toLowerCase().includes(q));
    list.replaceChildren();
    if (!rows.length) { list.append(el('p', 'muted', q ? '찾는 사람이 없습니다' : '아직 사람이 없습니다')); return; }
    rows.forEach(p => {
      const r = el('div', 'ua-person'), who = el('span');
      who.append(el('b', null, p.name || p.uid), el('span', 'ua-badge', p.primary ? '소속' : '겸직'));
      r.append(who, el('span', 'ua-access' + (p.access === '권한 있음' ? ' ok' : ''), p.access),
               el('span', 'ua-sub', p.uid + (p.grade ? ' · ' + p.grade : '')));
      list.append(r);
    });
  }

  // ---------------- 이벤트(위임 — 카드를 새로 받아도 그대로)
  document.addEventListener('click', e => {
    const t = e.target.closest('[data-tree-toggle],[data-tree-all],[data-parent-open],[data-people-open],[data-ua-open],[data-create-parent],[data-pp-save]');
    if (!t) return;
    if (t.matches('[data-tree-toggle]')) { const n = t.dataset.treeToggle; opened.has(n) ? opened.delete(n) : opened.add(n); remember(); paintTree(); }
    else if (t.matches('[data-tree-all]')) {
      const names = $$('[data-tree-toggle]', card()).map(b => b.dataset.treeToggle), allOpen = names.every(n => opened.has(n));
      names.forEach(n => allOpen ? opened.delete(n) : opened.add(n)); remember(); paintTree();
    }
    else if (t.matches('[data-parent-open]')) ppOpen(t.dataset.parentOpen, t.dataset.parent);
    else if (t.matches('[data-people-open]')) pvOpen(t.dataset.peopleOpen);
    else if (t.matches('[data-ua-open]')) { const m = openModal(t.dataset.uaOpen); const i = m && $('input[type=text]', m); if (i) i.focus(); }
    else if (t.matches('[data-create-parent]')) ppOpen('', t.dataset.parent, v => { t.dataset.parent = v; t.textContent = v || '없음'; });
    else if (t.matches('[data-pp-save]')) ppSave();
  });
  document.addEventListener('input', e => {
    if (e.target.matches('[data-dept-find]')) paintTree();
    else if (e.target.matches('[data-pp-find]')) ppPaint();
    else if (e.target.matches('[data-pv-find]')) pvPaint();
  });
  document.addEventListener('keydown', e => { if (e.target.matches('[data-pp-find]')) ppKeys(e); });
  document.addEventListener('submit', async e => {
    const f = e.target;
    if (f.matches('[data-ua-ajax]')) {
      e.preventDefault(); e.stopImmediatePropagation();
      if (f.dataset.uaConfirm && !(await confirmBox(f.dataset.uaConfirm, f.dataset.uaOk))) return;
      act(f);
    } else if (f.matches('[data-dept-create]')) {
      e.preventDefault(); e.stopImmediatePropagation();
      const msg = $('[data-create-msg]', f), pb = $('[data-create-parent]', f), btn = $('button[type=submit]', f);
      msg.textContent = ''; btn.disabled = true;
      try {
        const j = await post(f.action, { action: 'create', name: f.elements.name.value });
        let note = j.msg || '만들었습니다';
        if (pb.dataset.parent) {
          try { await post('/dev/users/dept-parent', { dept: j.name || f.elements.name.value, parent: pb.dataset.parent }); opened.add(pb.dataset.parent); remember(); }
          catch (err) { note += ' · 상위 부서는 정하지 못했습니다(' + err.message + ')'; }
        }
        f.reset(); pb.dataset.parent = ''; pb.textContent = '없음';
        closeModal(f.closest('.modal-back')); toast(note); await refresh();
      } catch (err) { msg.textContent = err.message; }
      btn.disabled = false;
    }
  }, true);

  function start() { if (!card()) return; readData(); paintTree(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
