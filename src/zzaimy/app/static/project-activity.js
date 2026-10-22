// 프로젝트 진행 현황 — 하위 탭 전환(주소 #해시로 기억), 요청 기록 사람 칩·찾기, 작업 요약 만들기
(() => {
  const root = document.querySelector('.pa-root');
  if (!root) return;
  const pid = root.dataset.project;
  const tabs = [...root.querySelectorAll('[data-pa-tab]')];
  function show(name, focus) {
    if (!tabs.some(t => t.dataset.paTab === name)) name = 'people';
    tabs.forEach(t => {
      const on = t.dataset.paTab === name;
      t.setAttribute('aria-selected', String(on)); t.tabIndex = on ? 0 : -1;
      const panel = document.getElementById('pa-' + t.dataset.paTab);
      if (panel) panel.hidden = !on;
      if (on && focus) t.focus();
    });
  }
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => { history.replaceState(null, '', '#' + t.dataset.paTab); show(t.dataset.paTab); });
    t.addEventListener('keydown', e => {
      const n = e.key === 'ArrowRight' ? (i + 1) % tabs.length : e.key === 'ArrowLeft' ? (i + tabs.length - 1) % tabs.length : -1;
      if (n >= 0) { e.preventDefault(); history.replaceState(null, '', '#' + tabs[n].dataset.paTab); show(tabs[n].dataset.paTab, true); }
    });
  });
  window.addEventListener('hashchange', () => show(location.hash.slice(1)));
  show(location.hash.slice(1));

  // 요청 기록 — 사람 칩 하나와 찾기 칸
  const chips = [...root.querySelectorAll('[data-pa-user]')];
  const search = document.getElementById('paSearch');
  const rows = [...root.querySelectorAll('.pa-req')];
  const none = document.getElementById('paNoMatch');
  let who = '';
  function filter() {
    const q = (search?.value || '').trim().toLowerCase();
    let shown = 0;
    rows.forEach(r => {
      const ok = (!who || r.dataset.user === who) && (!q || (r.dataset.text || '').includes(q));
      r.hidden = !ok; if (ok) shown++;
    });
    if (none) none.hidden = !rows.length || shown > 0;
  }
  chips.forEach(c => c.addEventListener('click', () => {
    who = c.dataset.paUser;
    chips.forEach(x => x.setAttribute('aria-pressed', String(x === c)));
    filter();
  }));
  search?.addEventListener('input', filter);

  // 작업 요약
  root.querySelectorAll('[data-pa-make]').forEach(btn => btn.addEventListener('click', async () => {
    const card = btn.closest('[data-pa-summary]');
    const body = card.querySelector('[data-pa-text]');
    const at = card.querySelector('[data-pa-at]');
    const label = btn.textContent;
    btn.disabled = true; btn.textContent = '요약하는 중…';
    try {
      const r = await fetch(`/api/project/${pid}/activity/summary`, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({user: btn.dataset.paMake}),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        body.classList.remove('err'); body.textContent = d.text;
        at.textContent = `${d.at} 요약 · 요청 ${d.n}건 기준`;
        btn.textContent = '다시 요약';
      } else {
        body.classList.add('err'); body.textContent = d.error || d.detail || '요약을 만들지 못했습니다.';
        btn.textContent = label;
      }
    } catch (_) {
      body.classList.add('err'); body.textContent = '서버에 닿지 않아 요약을 만들지 못했습니다.';
      btn.textContent = label;
    } finally { btn.disabled = false; }
  }));
})();
