(() => {
  const root = document.getElementById('documentWorkspace');
  if (!root) return;
  const fields = [...root.querySelectorAll('textarea')];
  const initial = new Map(fields.map(field => [field, field.value]));
  const dirty = () => fields.some(field => field.value !== initial.get(field));
  let submitting = false;
  const opinion = document.getElementById('reviewOpinion');
  root.addEventListener('input', () => {
    const hint = document.getElementById('opinionUnsaved');
    if (hint && opinion) hint.hidden = opinion.value === initial.get(opinion);
  });
  window.addEventListener('beforeunload', event => {
    if (dirty() && !submitting) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  // 다른 폼으로 이동하면 아직 저장하지 않은 의견/작성 항목이 사라진다.
  window.addEventListener('submit', async event => {
    if (!root.contains(event.target)) return;
    if (event.defaultPrevented) return;
    const form = event.target;
    if (opinion && form === opinion.form) {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (form.dataset.saving === 'true') return;
      const status = document.getElementById('opinionSaveStatus');
      const value = opinion.value;
      if (!value.trim()) {
        status.textContent = '의견을 입력해 주세요.';
        status.hidden = false;
        opinion.focus();
        return;
      }
      form.dataset.saving = 'true';
      const button = form.querySelector('button[type="submit"]');
      button.disabled = true;
      button.textContent = '저장 중…';
      try {
        const response = await fetch(form.action, {method:'POST', body:new FormData(form)});
        if (!response.ok || new URL(response.url).pathname !== window.location.pathname) throw new Error('save failed');
        initial.set(opinion, value);
        document.getElementById('opinionUnsaved').hidden = opinion.value === value;
        status.textContent = '의견을 저장했습니다. 재검토 또는 재작성을 진행할 수 있습니다.';
        if (!dirty()) { submitting = true; window.location.reload(); }
      } catch (_) {
        status.textContent = '저장 여부를 확인하지 못했습니다. 입력은 유지됩니다. 연결 상태를 확인해 주세요.';
      } finally {
        status.hidden = false;
        form.dataset.saving = 'false';
        button.disabled = false;
        button.textContent = '의견 저장';
      }
      return;
    }
    const outside = fields.find(field => field.form !== form && field.value !== initial.get(field));
    if (outside) {
      event.preventDefault();
      event.stopImmediatePropagation();
      const details = outside.closest('details');
      if (details) details.open = true;
      outside.focus();
      outside.scrollIntoView({block:'center'});
      window.alert('작성 중인 내용을 먼저 저장하거나 원래대로 되돌린 뒤 진행해 주세요.');
      return;
    }
    if (submitting) { event.preventDefault(); return; }
    submitting = true;
    queueMicrotask(() => { if (event.defaultPrevented) submitting = false; });
  }, true);
  window.addEventListener('pageshow', () => { submitting = false; });
  const refresh = document.getElementById('documentRefresh');
  if (refresh) refresh.addEventListener('click', () => {
    if (!dirty() || window.confirm('저장하지 않은 내용이 사라집니다. 새 결과를 불러올까요?')) {
      submitting = true;
      window.location.reload();
    }
  });
  if (root.dataset.poll !== 'true') return;
  async function poll() {
    try {
      const response = await fetch(root.dataset.statusUrl, {cache:'no-store'});
      if (!response.ok || response.redirected) throw new Error('status unavailable');
      const state = await response.json();
      if (typeof state.processing !== 'boolean' || typeof state.drafting !== 'boolean') throw new Error('invalid status');
      if (!state.processing && !state.drafting) {
        if (dirty() || submitting) document.getElementById('documentUpdate').hidden = false;
        else window.location.reload();
        return;
      }
    } catch (_) { /* 일시적인 연결 실패 시 입력을 유지하고 다시 확인한다. */ }
    window.setTimeout(poll, 4000);
  }
  window.setTimeout(poll, 4000);
})();

// 문서 보기 — 원문·본문·표·그림 탭, 본문 목차, 문서 안 검색
(() => {
  const viewer = document.getElementById('docViewer');
  if (!viewer) return;
  const tabs = [...viewer.querySelectorAll('.doctabs [data-view]')];
  const panes = {
    pdf: viewer.querySelector('[data-pane="pdf"]'),
    extract: viewer.querySelector('[data-pane="extract"]'),
  };
  const body = document.getElementById('extractBody');
  const nav = document.getElementById('extractNav');
  const frame = document.getElementById('docPdf');
  const pdfUrl = viewer.dataset.pdf;
  const blocks = body ? [...body.querySelectorAll('[data-kind]:not([data-kind="page"])')] : [];

  function show(view) {
    tabs.forEach(t => t.setAttribute('aria-selected', String(t.dataset.view === view)));
    if (panes.pdf) panes.pdf.hidden = view !== 'pdf';
    if (panes.extract) panes.extract.hidden = view === 'pdf';
    if (body && view !== 'pdf') body.dataset.show = view;
    if (nav) nav.hidden = view !== 'text' || !nav.childElementCount;
  }
  tabs.forEach(t => t.addEventListener('click', () => show(t.dataset.view)));

  // 본문 목차 — 제목 조각과 '제N장', '제N조(…)'로 시작하는 문단을 차례로 모은다
  const CHAPTER = /^\s*(제\s*\d+\s*(?:장|절)(?:\s*[가-힣 ]{1,20})?|부\s*칙)/;
  const ARTICLE = /^\s*(제\s*\d+\s*조(?:의\s*\d+)?\s*\([^)]{1,30}\))/;
  if (body && nav) {
    // 조문 구조(장·조·부칙)가 있으면 그것만 목차로 — 추출기가 붙인 소제목에는 문장 조각이 섞인다
    const entries = [];
    blocks.forEach((el, i) => {
      if (el.dataset.kind !== 'text') return;
      const text = (el.textContent || '').trim();
      let m = text.match(ARTICLE);
      if (m) { entries.push({ el, i, label: m[1], depth: 2, legal: true }); return; }
      m = text.match(CHAPTER);
      if (m) { entries.push({ el, i, label: m[1], depth: 1, legal: true }); return; }
      if (el.tagName === 'H4') entries.push({ el, i, label: text, depth: 1, legal: false });
    });
    const legal = entries.filter(e => e.legal);
    const seen = new Set();
    (legal.length >= 3 ? legal : entries).forEach(({ el, i, label, depth }) => {
      label = label.replace(/\s+/g, ' ').trim();
      if (!label || label.length > 40) return;
      const key = label.replace(/\s+/g, '');
      if (seen.has(key)) return;           // 같은 조문이 두 번 추출된 경우 한 번만
      seen.add(key);
      el.id = el.id || `xb${i}`;
      const a = document.createElement('a');
      a.href = `#${el.id}`;
      a.textContent = label;
      a.className = `depth${depth}`;
      a.addEventListener('click', ev => {
        ev.preventDefault();
        el.scrollIntoView({ block: 'start', behavior: 'smooth' });
      });
      nav.appendChild(a);
    });
    if (nav.childElementCount < 3) nav.replaceChildren();
    if (panes.extract && nav.childElementCount) panes.extract.classList.add('with-nav');
  }
  show(tabs.find(t => t.getAttribute('aria-selected') === 'true')?.dataset.view || 'text');

  // 문서 안 검색 — 추출이 낱말 사이에 공백을 끼우는 일이 있어 공백을 무시하고 찾는다
  const input = document.getElementById('docSearch');
  const box = document.getElementById('docSearchResults');
  const count = document.getElementById('docSearchCount');
  if (!input || !box) return;
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const html = s => s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  let timer = 0;

  function goToBlock(el, q) {
    show(el.dataset.kind === 'table' && tabs.some(t => t.dataset.view === 'table') ? 'table' : 'text');
    blocks.forEach(b => b.classList.remove('hit'));
    el.classList.add('hit');
    el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }
  function goToPage(page, q) {
    if (!frame || !pdfUrl) return;
    show('pdf');
    // 크롬 뷰어는 #page 로 쪽을 옮긴다. #search 는 지원하는 뷰어에서만 쓰인다
    frame.src = `${pdfUrl}#page=${page}&search=${encodeURIComponent(q)}`;
  }

  function run() {
    const q = input.value.trim();
    if (q.replace(/\s+/g, '').length < 2) {
      box.hidden = true; box.replaceChildren(); count.textContent = '';
      return;
    }
    const rx = new RegExp(q.replace(/\s+/g, '').split('').map(esc).join('\\s*'), 'i');
    const hits = [];
    blocks.forEach(el => {
      const text = el.textContent || '';
      const m = text.match(rx);
      if (m) hits.push({ el, text, at: m.index, len: m[0].length });
    });
    count.textContent = hits.length ? `${hits.length}곳` : '없음';
    box.replaceChildren();
    hits.slice(0, 60).forEach(h => {
      const from = Math.max(0, h.at - 30);
      const snip = (from ? '…' : '') + html(h.text.slice(from, h.at)) + '<mark>'
        + html(h.text.slice(h.at, h.at + h.len)) + '</mark>'
        + html(h.text.slice(h.at + h.len, h.at + h.len + 50)) + '…';
      const row = document.createElement('div');
      row.className = 'docsearch-hit';
      const page = h.el.dataset.page;
      row.innerHTML = `<span class="pg">${page ? `${html(page)}쪽` : ''}${h.el.dataset.kind === 'table' ? ' 표' : ''}</span>`
        + `<span class="snip">${snip.replace(/\s+/g, ' ')}</span>`;
      const toText = document.createElement('button');
      toText.type = 'button'; toText.className = 'btn-ghost'; toText.textContent = '본문';
      toText.addEventListener('click', () => goToBlock(h.el, q));
      row.appendChild(toText);
      if (frame && page) {
        const toPdf = document.createElement('button');
        toPdf.type = 'button'; toPdf.className = 'btn-ghost'; toPdf.textContent = '원문';
        toPdf.addEventListener('click', () => goToPage(page, q));
        row.appendChild(toPdf);
      }
      box.appendChild(row);
    });
    box.hidden = !hits.length;
  }
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(run, 180); });
  // 주소로 상태를 넘긴다 — #view=text, #q=검색어 (링크로 같은 화면을 공유)
  const params = new URLSearchParams(location.hash.slice(1));
  if (params.get('view') && tabs.some(t => t.dataset.view === params.get('view'))) show(params.get('view'));
  if (params.get('q')) { input.value = params.get('q'); run(); }
  input.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      const first = box.querySelector('.docsearch-hit button');
      if (first) first.click();
    } else if (ev.key === 'Escape') {
      input.value = ''; run();
    }
  });
})();
