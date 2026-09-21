(() => {
  document.addEventListener('submit', event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || event.defaultPrevented || form.hasAttribute('onsubmit')) return;
    const action = new URL(form.action, location.href);
    if (action.origin !== location.origin || !/\/delete\/?$/.test(action.pathname)) return;
    const message = form.dataset.confirm || (/\/notes\//.test(action.pathname)
      ? '이 메모를 삭제할까요? 삭제 후에는 복구할 수 없습니다.'
      : /\/installer\//.test(action.pathname)
        ? '설치파일을 삭제할까요? 삭제하면 다운로드할 수 없습니다.'
        : '이 항목을 삭제할까요?');
    if (!window.confirm(message)) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href]');
    if (!link || link.hasAttribute('download') || link.target || link.hasAttribute('data-force-navigation')) return;
    const target = new URL(link.href, location.href);
    if (target.origin !== location.origin || target.pathname !== location.pathname || target.search !== location.search || target.hash !== location.hash) return;
    event.preventDefault();
    document.querySelectorAll('.session-popover:popover-open').forEach(pop => pop.hidePopover());
  });
  document.querySelector('[data-page-back]')?.addEventListener('click', event => {
    // Direct visits and external referrers use the explicit parent link.
    if (document.referrer && history.length > 1) {
      const previous = new URL(document.referrer);
      if (previous.origin === location.origin && previous.href !== location.href) {
        event.preventDefault(); history.back();
      }
    }
  });
  const sidebar = document.getElementById('workspaceSidebar');
  const toggle = document.getElementById('navToggle');
  const close = document.getElementById('navClose');
  const content = document.querySelector('.content-col');
  const mobile = matchMedia('(max-width:1080px)');
  if (!sidebar || !toggle || !close) return;
  let desktopClosed = document.documentElement.classList.contains('sidebar-collapsed');
  let mobileOpen = false;
  function paint() {
    const open = mobile.matches ? mobileOpen : !desktopClosed;
    document.body.classList.toggle('nav-open', mobile.matches && open);
    document.documentElement.classList.toggle('sidebar-collapsed', desktopClosed);
    sidebar.inert = !open;
    sidebar.setAttribute('aria-hidden', String(!open));
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-controls', sidebar.id);
    toggle.setAttribute('aria-label', open ? '메뉴 접기' : '메뉴 열기');
    toggle.title = open ? '메뉴 접기' : '메뉴 열기';
    content.inert = mobile.matches && open;
  }
  function setOpen(open, focus=true) {
    if (mobile.matches) mobileOpen = open;
    else {
      desktopClosed = !open;
      try { localStorage.setItem('workspaceSidebarCollapsed', desktopClosed ? '1' : '0'); } catch (_) {}
    }
    paint();
    if (focus) (open ? close : toggle).focus();
  }
  toggle.addEventListener('click', () => setOpen(mobile.matches ? !mobileOpen : desktopClosed));
  close.addEventListener('click', () => setOpen(false));
  document.getElementById('navBackdrop').addEventListener('click', () => setOpen(false));
  sidebar.addEventListener('click', event => {
    if (mobile.matches && event.target.closest('a[href]')) setOpen(false, false);
  });
  document.addEventListener('keydown', event => {
    if (!mobile.matches || !mobileOpen || document.querySelector('dialog[open], .modal-back.open')) return;
    if (event.key === 'Escape') { event.preventDefault(); setOpen(false); }
    if (event.key !== 'Tab') return;
    const items = [...sidebar.querySelectorAll('a[href],button:not([disabled]),input:not([disabled])')]
      .filter(item => item.getClientRects().length);
    const first = items[0], last = items.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  mobile.addEventListener('change', () => {
    const active = document.activeElement;
    mobileOpen = false; paint();
    if (sidebar.inert && sidebar.contains(active)) toggle.focus();
  });
  paint();
  requestAnimationFrame(() => document.documentElement.classList.add('nav-ready'));
})();
