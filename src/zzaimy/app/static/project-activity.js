// 프로젝트 진행 현황 대시보드 — 참여자 줄(사람·기간 거르기), 사용량 그림(날짜별 막대 SVG·사람별 비율 막대),
// 요청 기록(대화 묶음 펼치기/접기·찾기·더 보기), 작업 요약 카드(더 보기·요약 만들기). 값은 서버가 센 #paData 에서만 다시 묶는다.
(() => {
  const root = document.querySelector('.pa-root');
  if (!root) return;
  const pid = root.dataset.project;
  const num = n => Number(n || 0).toLocaleString('ko-KR');
  const $ = id => document.getElementById(id);
  const SVG = 'http://www.w3.org/2000/svg';
  let data = {records: [], names: {}, reqs: []};
  try { data = Object.assign(data, JSON.parse($('paData')?.textContent || '{}')); } catch (_) { /* 자료가 없으면 빈 그림 */ }

  const ymd = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const parse = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
  const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
  const today = () => ymd(new Date());

  const state = {who: '', days: parseInt($('paPeriod')?.value || '0', 10), metric: 'req', q: '', reqShown: 10, cardShown: 6};
  const since = () => (state.days ? ymd(addDays(new Date(), 1 - state.days)) : '');
  const inPeriod = day => !state.days || ((day || '') >= since() && (day || '') <= today());
  const periodLabel = () => (state.days ? `최근 ${state.days}일` : '전체 기간');

  // ── 참여자: 칩 줄과 「모두 보기」 창이 같은 거르기 하나를 쓴다
  const pickers = [...root.querySelectorAll('[data-pa-user]')];
  const whoBox = $('paWho');
  function pick(user) {
    state.who = state.who === user ? '' : user;
    pickers.forEach(x => x.setAttribute('aria-pressed', String(x.dataset.paUser === state.who)));
    if (whoBox) {
      whoBox.hidden = !state.who;
      whoBox.querySelector('[data-pa-who-name]').textContent = state.who ? (data.names[state.who] || state.who) : '';
    }
    const chip = root.querySelector(`.pa-person[data-pa-user="${CSS.escape(state.who)}"]`);
    chip?.scrollIntoView({block: 'nearest', inline: 'nearest'});
    state.reqShown = 10; state.cardShown = 6;
    render();
  }
  pickers.forEach(c => c.addEventListener('click', () => { pick(c.dataset.paUser); closePop(); }));
  root.querySelector('[data-pa-who-clear]')?.addEventListener('click', () => pick(state.who));

  const popBtn = $('paPeopleAll'), pop = $('paPeoplePop'), find = $('paPeopleFind');
  function closePop() { if (pop && !pop.hidden) { pop.hidden = true; popBtn.setAttribute('aria-expanded', 'false'); } }
  popBtn?.addEventListener('click', e => {
    e.stopPropagation();
    const open = pop.hidden;
    pop.hidden = !open; popBtn.setAttribute('aria-expanded', String(open));
    if (open) find?.focus();
  });
  document.addEventListener('click', e => { if (pop && !pop.hidden && !pop.contains(e.target)) closePop(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && pop && !pop.hidden) { closePop(); popBtn.focus(); } });
  find?.addEventListener('input', () => {
    const q = find.value.trim().toLowerCase();
    let n = 0;
    pop.querySelectorAll('.pa-prow').forEach(r => { const ok = !q || (r.dataset.find || '').includes(q); r.hidden = !ok; n += ok ? 1 : 0; });
    pop.querySelector('[data-pa-find-none]').hidden = n > 0;
  });
  $('paPeriod')?.addEventListener('change', e => { state.days = parseInt(e.target.value || '0', 10); state.reqShown = 10; render(); });

  // ── 사용량 그림
  root.querySelectorAll('[data-pa-metric]').forEach(b => b.addEventListener('click', () => {
    state.metric = b.dataset.paMetric;
    root.querySelectorAll('[data-pa-metric]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    drawUsage();
  }));

  function buckets() {
    // 기간 칸 — 최근 n일은 하루씩, 전체 기간은 처음 기록부터 오늘까지(9주가 넘으면 주 단위)
    let start;
    if (state.days) start = parse(since());
    else {
      const all = [...data.reqs.map(r => r[1]), ...data.records.map(r => r.day)].filter(Boolean).sort();
      start = parse(all.length ? all[0] : ymd(addDays(new Date(), -6)));
    }
    const end = parse(today());
    const span = Math.round((end - start) / 864e5) + 1;
    const weekly = span > 63;
    if (weekly) start = addDays(start, -((start.getDay() + 6) % 7));          // 월요일부터
    const out = [], idx = new Map();
    for (let d = new Date(start); d <= end; d = addDays(d, weekly ? 7 : 1)) {
      const b = {key: ymd(d), label: ymd(d).slice(5).replace('-', '.'), weekly, req: 0, answers: 0, tok: 0, calls: 0};
      idx.set(b.key, b); out.push(b);
      if (weekly) for (let i = 1; i < 7; i++) idx.set(ymd(addDays(d, i)), b);
    }
    return {list: out, at: day => idx.get(day)};
  }

  function niceMax(v, whole) {
    // 눈금 넷 — 한 칸이 1·2·2.5·5×10^k(요청 수처럼 정수인 값은 2.5 를 건너뛰고 1 이상)
    const raw = Math.max(v, whole ? 4 : 1) / 4, p = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(k => k * p).find(k => k >= raw && (!whole || (Number.isInteger(k) && k >= 1)));
    return step * 4;
  }
  const short = v => (v >= 1e6 ? `${+(v / 1e6).toFixed(1)}백만` : v >= 1e4 ? `${+(v / 1e4).toFixed(1)}만` : num(v));
  const svg = (tag, attrs, text) => {
    const e = document.createElementNS(SVG, tag);
    Object.entries(attrs || {}).forEach(([k, v]) => e.setAttribute(k, v));
    if (text != null) e.textContent = text;
    return e;
  };

  function drawDays(B) {
    const box = $('paDayChart');
    if (!box) return;
    const W = Math.max(240, box.clientWidth), H = box.clientHeight || 200;
    const vals = B.list.map(b => (state.metric === 'req' ? b.req : b.tok));
    const top = niceMax(Math.max(0, ...vals), state.metric === 'req');
    const ticks = [0, 1, 2, 3, 4].map(i => top * i / 4);
    const padL = Math.max(...ticks.map(t => short(t).length)) * 7 + 10, padB = 22, padT = 8, padR = 4;
    const pw = W - padL - padR, ph = H - padT - padB;
    const band = pw / B.list.length;
    const s = svg('svg', {viewBox: `0 0 ${W} ${H}`, width: W, height: H, 'aria-hidden': 'true'});
    ticks.forEach((t, i) => {
      const y = padT + ph - ph * i / 4;
      s.append(svg('line', {class: i ? 'gl' : 'base', x1: padL, x2: W - padR, y1: y, y2: y}));
      s.append(svg('text', {class: 'ax', x: padL - 8, y: y + 4, 'text-anchor': 'end'}, short(t)));
    });
    const step = Math.max(1, Math.ceil(44 / band));
    const tip = box.querySelector('.pa-tip') || Object.assign(document.createElement('div'), {className: 'pa-tip', hidden: true});
    B.list.forEach((b, i) => {
      const x0 = padL + band * i, v = vals[i];
      if (i % step === 0) s.append(svg('text', {class: 'ax', x: x0 + band / 2, y: H - 6, 'text-anchor': 'middle'}, b.label));
      const hit = svg('rect', {class: 'hit', x: x0, y: padT, width: band, height: ph, tabindex: '0',
        'aria-label': `${b.label} 요청 ${b.req} 입력 토큰 ${b.tok}`});
      s.append(hit);
      const bw = Math.min(28, Math.max(2, band * 0.68)), h = v ? Math.max(2, ph * v / top) : 0;
      const bx = x0 + (band - bw) / 2, by = padT + ph - h, r = Math.min(3, bw / 2, h);
      const bar = svg('path', {class: 'bar', d: h ? `M${bx},${by + h}V${by + r}Q${bx},${by} ${bx + r},${by}H${bx + bw - r}Q${bx + bw},${by} ${bx + bw},${by + r}V${by + h}Z` : ''});
      s.append(bar);
      const show = () => {
        tip.replaceChildren();
        const head = document.createElement('b');
        head.textContent = b.weekly ? `${b.label} 주` : b.key;
        tip.append(head, `요청 ${num(b.req)} · 답 ${num(b.answers)}`, document.createElement('br'), `입력 토큰 ${num(b.tok)} · 호출 ${num(b.calls)}`);
        tip.hidden = false;
        const tw = tip.offsetWidth, th = tip.offsetHeight;
        tip.style.left = `${Math.min(W - tw, Math.max(0, x0 + band / 2 - tw / 2))}px`;
        tip.style.top = `${Math.max(0, by - th - 8)}px`;
      };
      hit.addEventListener('mouseenter', show); hit.addEventListener('focus', show);
      hit.addEventListener('mouseleave', () => { tip.hidden = true; }); hit.addEventListener('blur', () => { tip.hidden = true; });
    });
    if (!vals.some(Boolean)) s.append(svg('text', {class: 'none', x: padL + pw / 2, y: padT + ph / 2, 'text-anchor': 'middle'}, '이 기간에는 기록이 없습니다'));
    tip.hidden = true;
    box.replaceChildren(s, tip);
    box.setAttribute('aria-label', `날짜별 ${state.metric === 'req' ? '요청 수' : '입력 토큰'} · ${periodLabel()}`);
  }

  function drawPeople(per) {
    const box = $('paPersonBars');
    if (!box) return;
    const key = state.metric === 'req' ? 'req' : 'tok';
    const rows = [...per.values()].filter(p => p[key] > 0).sort((a, b) => b[key] - a[key]);
    const total = rows.reduce((a, p) => a + p[key], 0);
    const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
    if (!rows.length) { box.replaceChildren(el('p', 'pa-empty', '이 기간에는 기록이 없습니다.')); return; }
    let shown = rows.slice(0, rows.length > 7 ? 6 : 7);
    if (state.who && !shown.some(p => p.user === state.who)) { const me = rows.find(p => p.user === state.who); if (me) shown = [...shown.slice(0, 5), me]; }
    const rest = rows.filter(p => !shown.includes(p));
    const lines = shown.map(p => ({name: data.names[p.user] || p.user || '알 수 없음', v: p[key], user: p.user, p}));
    if (rest.length) lines.push({name: `그 외 ${rest.length}명`, v: rest.reduce((a, p) => a + p[key], 0), user: null});
    const top = Math.max(...lines.map(l => l.v));
    box.replaceChildren(...lines.map(l => {
      const row = el('div', 'pa-pbar' + (state.who && l.user !== state.who ? ' dim' : ''));
      const track = el('span', 'pa-pbar-track'), fill = el('i');
      fill.style.width = `${(l.v / top * 100).toFixed(1)}%`;
      track.append(fill);
      if (l.p) row.title = `${l.name} · 요청 ${num(l.p.req)} · 답 ${num(l.p.answers)} · 입력 토큰 ${num(l.p.tok)} · 호출 ${num(l.p.calls)} · 줄임 ${num(l.p.trimmed)}`;
      row.append(el('span', 'pa-pbar-name', l.name), track, el('span', 'v', num(l.v)), el('span', 'p', `${Math.round(l.v / total * 100)}%`));
      return row;
    }));
  }

  function drawUsage() {
    const B = buckets(), per = new Map();
    const P = u => { if (!per.has(u)) per.set(u, {user: u, req: 0, answers: 0, tok: 0, calls: 0, trimmed: 0}); return per.get(u); };
    let req = 0, tok = 0;
    data.reqs.forEach(([u, day]) => {
      if (!inPeriod(day)) return;
      P(u).req++;
      if (state.who && u !== state.who) return;
      req++; const b = B.at(day); if (b) b.req++;
    });
    data.records.forEach(r => {
      if (!inPeriod(r.day)) return;
      const p = P(r.user); p.answers++; p.tok += r.prompt || 0; p.calls += r.calls || 0; p.trimmed += r.trimmed ? 1 : 0;
      if (state.who && r.user !== state.who) return;
      tok += r.prompt || 0; const b = B.at(r.day); if (b) { b.answers++; b.tok += r.prompt || 0; b.calls += r.calls || 0; }
    });
    drawDays(B);
    drawPeople(per);
    const sum = $('paUsageSum');
    if (sum) sum.textContent = `${periodLabel()} · 요청 ${num(req)} · 입력 토큰 ${num(tok)}`;
  }

  // ── 요청 기록: 대화 묶음, 사람·기간·찾기, 10개씩 더 보기
  const convs = [...root.querySelectorAll('.pa-conv')];
  const search = $('paSearch'), more = $('paReqMore'), none = $('paNoMatch'), count = $('paReqCount');
  convs.forEach(c => {
    const btn = c.querySelector('[data-pa-toggle]'), list = c.querySelector('.pa-conv-reqs');
    btn.addEventListener('click', () => {
      const open = list.hidden;
      c.dataset.open = open ? '1' : '';
      setOpen(c, open);
    });
  });
  function setOpen(c, open) {
    c.querySelector('.pa-conv-reqs').hidden = !open;
    const btn = c.querySelector('[data-pa-toggle]');
    btn.textContent = open ? '접기' : '펼치기';
    btn.setAttribute('aria-expanded', String(open));
  }
  search?.addEventListener('input', () => { state.q = search.value.trim().toLowerCase(); state.reqShown = 10; drawRequests(); });
  more?.addEventListener('click', () => { state.reqShown += 10; drawRequests(); });
  function drawRequests() {
    const q = state.q;
    let shownConv = 0, shownReq = 0, matched = 0;
    convs.forEach(c => {
      const titleHit = !!q && (c.dataset.title || '').includes(q);
      let n = 0, textHit = false;
      c.querySelectorAll('.pa-conv-reqs li').forEach(li => {
        const a = li.firstElementChild;
        const hit = !q || titleHit || (a.dataset.text || '').includes(q);
        const ok = (!state.who || c.dataset.user === state.who) && inPeriod(a.dataset.day) && hit;
        li.hidden = !ok; n += ok ? 1 : 0;
        if (ok && q && !titleHit) textHit = true;
      });
      c.querySelector('[data-pa-n]').textContent = String(n);
      const ok = n > 0;
      if (ok) { matched++; shownReq += n; }
      c.hidden = !ok || matched > state.reqShown;
      if (!c.hidden) shownConv++;
      setOpen(c, textHit || c.dataset.open === '1');               // 찾은 글이 요청 안에 있으면 펼쳐 보인다
    });
    if (none) none.hidden = !convs.length || matched > 0;
    if (more) { more.hidden = matched <= state.reqShown; more.textContent = `더 보기 (${matched - shownConv}개 남음)`; }
    if (count) count.textContent = `대화 ${matched} · 요청 ${shownReq}`;
  }

  // ── 작업 요약 카드: 사람 거르기, 6개씩
  const cards = [...root.querySelectorAll('[data-pa-summary]')];
  const cardMore = $('paCardMore');
  cardMore?.addEventListener('click', () => { state.cardShown += 6; drawCards(); });
  function drawCards() {
    const ok = cards.filter(c => !state.who || c.dataset.paSummary === state.who);
    cards.forEach(c => { c.hidden = true; });
    ok.slice(0, state.cardShown).forEach(c => { c.hidden = false; });
    if (cardMore) { cardMore.hidden = ok.length <= state.cardShown; cardMore.textContent = `더 보기 (${ok.length - state.cardShown}명 남음)`; }
  }

  function render() { drawUsage(); drawRequests(); drawCards(); }
  render();
  let lastW = $('paDayChart')?.clientWidth || 0;
  if (window.ResizeObserver && $('paDayChart')) new ResizeObserver(() => {
    const w = $('paDayChart').clientWidth;
    if (Math.abs(w - lastW) > 1) { lastW = w; drawUsage(); }
  }).observe($('paDayChart'));

  // ── 작업 요약 만들기
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
