(() => {
  const root = document.getElementById('projectWorkspace');
  if (!root) return;
  const tabs = [...root.querySelectorAll('[data-project-tab]')];
  const key = 'projectTab:' + location.pathname;
  function activate(id, focus=false) {
    if (!tabs.some(tab => tab.dataset.projectTab === id)) return;
    tabs.forEach(tab => {
      const active = tab.dataset.projectTab === id;
      tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;
      document.getElementById(tab.dataset.projectTab).hidden = !active;
      if (active && focus) tab.focus();
    });
    try { sessionStorage.setItem(key, id); } catch (_) {}
  }
  tabs.forEach((tab,index) => {
    tab.addEventListener('click', () => activate(tab.dataset.projectTab));
    tab.addEventListener('keydown', event => {
      const next = event.key === 'ArrowRight' ? (index+1)%tabs.length : event.key === 'ArrowLeft' ? (index+tabs.length-1)%tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length-1 : -1;
      if (next>=0) { event.preventDefault(); activate(tabs[next].dataset.projectTab, true); }
    });
  });
  try { activate(sessionStorage.getItem(key) || 'projectConversations'); } catch (_) {}
  // 탭을 옮겨도 작성 중인 지침과 질문은 DOM에 그대로 남긴다.
  const fields = [...root.querySelectorAll('textarea, input[name=question]:not([type=hidden]), input[type=file]')];
  const initial = new Map(fields.map(field => [field, field.value]));
  let submittingForm = null;
  root.addEventListener('submit', event => {
    if (event.defaultPrevented) return;
    submittingForm = event.target;
    queueMicrotask(() => { if (event.defaultPrevented) submittingForm = null; });
  });
  window.addEventListener('pageshow', () => { submittingForm = null; });
  window.addEventListener('beforeunload', event => {
    if (fields.some(field => field.isConnected && !field.disabled && field.form !== submittingForm &&
      (field.type === 'file' ? field.files.length > 0 : field.value !== initial.get(field)))) {
      event.preventDefault(); event.returnValue = '';
    }
  });
})();
