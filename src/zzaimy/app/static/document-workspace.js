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
