(() => {
  function revealSettings() {
    if (['#googleApp', '#googleAccounts'].includes(location.hash)) {
      const section = document.querySelector(location.hash);
      const details = section?.closest('details');
      if (details) { details.open = true; section.scrollIntoView({block:'nearest'}); }
    }
  }
  window.addEventListener('hashchange', revealSettings);
  revealSettings();
  const appForm = document.getElementById('googleAppForm'), edit = document.querySelector('[data-app-edit]');
  const appDialog = document.getElementById('googleAppDialog');
  if (appDialog && appForm && edit) {
    edit.addEventListener('click', () => {
      if (!appDialog.open) appDialog.showModal();
    });
    appDialog.querySelectorAll('[data-app-close]').forEach(button => {
      button.addEventListener('click', () => appDialog.close());
    });
    appDialog.addEventListener('close', () => {
      appForm.reset();
      edit.focus({preventScroll:true});
    });
  }
  function confirmChange(message, opener) {
    return new Promise(resolve => {
      const dialog = document.createElement('dialog'); dialog.className = 'gd-confirm';
      dialog.setAttribute('aria-labelledby','gdConfirmTitle');
      dialog.innerHTML = '<h2 id="gdConfirmTitle">변경 확인</h2><p></p><footer><button type="button" class="secondary" data-cancel>취소</button><button type="button" data-accept>확인</button></footer>';
      dialog.querySelector('p').textContent = message;
      dialog.querySelector('[data-cancel]').onclick = () => dialog.close();
      dialog.querySelector('[data-accept]').onclick = () => dialog.close('accept');
      dialog.addEventListener('close', () => { const accepted = dialog.returnValue === 'accept'; dialog.remove(); opener?.focus(); resolve(accepted); }, {once:true});
      document.body.append(dialog); dialog.showModal(); dialog.querySelector('[data-cancel]').focus();
    });
  }
  document.querySelector('[data-use-answer]')?.addEventListener('click', async event => {
    const draft = document.getElementById('gdDraft'), answer = document.getElementById('gdAnswer').textContent;
    if (draft.value.trim() && draft.value !== answer && !await confirmChange('작성 중인 내용을 답변으로 바꿀까요?', event.currentTarget)) return;
    draft.value = answer; draft.focus(); draft.scrollIntoView({block:'nearest'});
  });
  document.querySelectorAll('form[data-confirm]').forEach(form => {
    let confirmed = false, pending = false;
    form.addEventListener('submit', async event => {
      if (confirmed) return;
      event.preventDefault(); if (pending) return; pending = true;
      const accepted = await confirmChange(form.dataset.confirm, event.submitter);
      pending = false;
      if (accepted) { confirmed = true; form.requestSubmit(event.submitter || undefined); confirmed = false; }
    });
  });
})();
