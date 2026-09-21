(() => {
  document.querySelectorAll('.dx-search').forEach(form => {
    const input = form.querySelector('[name=q]');
    if (input && !input.hasAttribute('aria-label')) input.setAttribute('aria-label', '자료 검색');
    if (input?.value || form.querySelector('[name=type]')?.value) {
      const reset = document.createElement('a');
      reset.className = 'btn-ghost'; reset.textContent = '초기화';
      reset.href = '/dev/db?' + new URLSearchParams({tab: form.querySelector('[name=tab]').value});
      form.append(reset);
    }
  });
  document.querySelectorAll('tr[data-href]').forEach(row => {
    row.addEventListener('click', event => {
      if (event.target.closest('a,button,input') || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const link = row.querySelector('a[href]');
      if (link) link.click();
    });
    const link = row.querySelector('a[href]');
    if (link && document.getElementById('dxDetail') && matchMedia('(max-width:1200px)').matches) link.hash = 'dxDetail';
    if (row.classList.contains('on')) link?.setAttribute('aria-current', 'true');
  });
  if (typeof HTMLDialogElement === 'undefined') return;
  const dialog = document.createElement('dialog'); dialog.className = 'dx-reader';
  dialog.setAttribute('aria-labelledby', 'dxReaderTitle');
  const head = document.createElement('div'); head.className = 'dx-reader-head';
  const title = document.createElement('h2'); title.id = 'dxReaderTitle'; title.textContent = '자료 내용';
  const close = document.createElement('button'); close.type = 'button'; close.className = 'secondary'; close.textContent = '닫기';
  const body = document.createElement('pre'); body.tabIndex = 0; body.setAttribute('aria-label', '자료 본문');
  head.append(title, close); dialog.append(head, body); document.body.append(dialog);
  let opener;
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => opener?.focus());
  document.querySelectorAll('details.dx-more.can').forEach(detail => {
    const source = detail.querySelector('pre');
    if (!source) return;
    const button = document.createElement('button'); button.type = 'button'; button.className = 'dx-preview-button';
    button.setAttribute('aria-haspopup', 'dialog');
    const preview = document.createElement('span'); preview.textContent = detail.querySelector('.p')?.textContent || '내용';
    const action = document.createElement('small'); action.textContent = '내용 보기';
    button.append(preview, action);
    button.addEventListener('click', () => {
      opener = button;
      const heading = detail.closest('tr')?.querySelector('.head')?.textContent.trim();
      title.textContent = heading && heading !== '—' ? heading : '자료 내용';
      body.textContent = source.textContent;
      dialog.showModal(); body.scrollTop = 0;
    });
    detail.before(button); detail.hidden = true;
  });
})();
