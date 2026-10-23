(() => {
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
  // data-confirm 폼은 공용 확인창(workspace-navigation.js)이 묻는다 — 이 화면이 따로 묻지 않는다
})();
