// 프로젝트 진행 현황 대시보드 — 참여자 카드로 요청 기록 거르기, 찾기, 사용량 사람별·날짜별·기간, 작업 요약 만들기
(() => {
  const root = document.querySelector('.pa-root');
  if (!root) return;
  const pid = root.dataset.project;
  const num = n => Number(n || 0).toLocaleString('ko-KR');

  // 요청 기록 — 사람(참여자 카드) 하나와 찾기 칸. 날짜 머리는 그 날 보이는 줄이 있을 때만
  const people = [...root.querySelectorAll('.pa-person[data-pa-user]')];
  const search = document.getElementById('paSearch');
  const rows = [...root.querySelectorAll('.pa-req')];
  const days = [...root.querySelectorAll('[data-pa-day]')];
  const none = document.getElementById('paNoMatch');
  const count = document.getElementById('paReqCount');
  const whoBox = document.getElementById('paWho');
  let who = '';
  function filter() {
    const q = (search?.value || '').trim().toLowerCase();
    let shown = 0;
    const seen = new Set();
    rows.forEach(r => {
      const ok = (!who || r.dataset.user === who) && (!q || (r.dataset.text || '').includes(q));
      r.hidden = !ok;
      if (ok) { shown++; seen.add(r.dataset.day); }
    });
    days.forEach(d => { d.hidden = !seen.has(d.dataset.paDay); });
    if (none) none.hidden = !rows.length || shown > 0;
    if (count) count.textContent = (who || q) ? `${shown} / ${rows.length}` : String(rows.length);
  }
  function pick(user) {
    who = who === user ? '' : user;
    people.forEach(x => x.setAttribute('aria-pressed', String(x.dataset.paUser === who)));
    if (whoBox) {
      whoBox.hidden = !who;
      const p = people.find(x => x.dataset.paUser === who);
      whoBox.querySelector('[data-pa-who-name]').textContent = p ? p.querySelector('.pa-who').textContent : '';
    }
    filter();
  }
  people.forEach(c => c.addEventListener('click', () => pick(c.dataset.paUser)));
  root.querySelector('[data-pa-who-clear]')?.addEventListener('click', () => pick(who));
  search?.addEventListener('input', filter);

  // 사용량 — 답 하나씩 기록(data-records)을 사람별·날짜별로, 기간(최근 n일)으로 다시 묶어 그린다
  const box = document.getElementById('paUsage');
  const period = document.getElementById('paPeriod');
  const segs = [...root.querySelectorAll('[data-pa-by]')];
  let recs = [], names = {};
  try { recs = JSON.parse(box?.dataset.records || '[]'); names = JSON.parse(box?.dataset.names || '{}'); } catch (_) { recs = []; }
  let by = 'user';
  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function drawUsage() {
    if (!box || !recs.length) return;
    const n = parseInt(period?.value || '0', 10);
    let since = '';
    if (n) { const d = new Date(); d.setDate(d.getDate() - n + 1); since = d.toISOString().slice(0, 10); }
    const acc = new Map();
    recs.filter(r => !since || (r.day || '') >= since).forEach(r => {
      const k = by === 'user' ? r.user : (r.day || '날짜 없음');
      const a = acc.get(k) || {key: k, answers: 0, calls: 0, prompt: 0, trimmed: 0};
      a.answers++; a.calls += r.calls || 0; a.prompt += r.prompt || 0; a.trimmed += r.trimmed ? 1 : 0;
      acc.set(k, a);
    });
    const list = [...acc.values()].sort(by === 'user' ? (a, b) => b.prompt - a.prompt : (a, b) => (a.key < b.key ? 1 : -1));
    const top = Math.max(0, ...list.map(a => a.prompt));
    const head = el('div', 'pa-urow pa-uhead');
    [by === 'user' ? '사람' : '날짜', '입력 합'].forEach(t => head.append(el('span', null, t)));
    ['답', '호출', '줄임'].forEach(t => head.append(el('span', 'n', t)));
    const out = [head];
    list.forEach(a => {
      const row = el('div', 'pa-urow');
      row.append(el('span', 'pa-uname', by === 'user' ? (names[a.key] || a.key) : a.key));
      const cell = el('span', 'pa-bar-cell'), bar = el('span', 'pa-bar'), fill = el('i');
      fill.style.width = (top ? (a.prompt / top * 100).toFixed(1) : 0) + '%';
      bar.append(fill); cell.append(bar, el('span', 'pa-sub', num(a.prompt))); row.append(cell);
      [a.answers, a.calls, a.trimmed].forEach(v => row.append(el('span', 'n', String(v))));
      out.push(row);
    });
    if (!list.length) out.push(el('p', 'pa-empty', '이 기간에는 기록된 사용량이 없습니다.'));
    box.replaceChildren(...out);
  }
  segs.forEach(b => b.addEventListener('click', () => {
    by = b.dataset.paBy;
    segs.forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    drawUsage();
  }));
  period?.addEventListener('change', drawUsage);

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
