// 논문 문서의 목차 쪽 번호 넣기(ADR-0072) — 쪽 나눔은 기안기만 안다. 목차 줄마다 그 절 칸으로 커서를 옮겨 쪽(인쇄 쪽 번호)을 재고,
// 서버(/hwp/{id}/paper-toc)가 필드 밖 목차 줄을 「제목 ····· 쪽」 으로 다시 쓴다. 다 재면 커서를 제자리로 돌리고 새 판을 연다.
// 다른 사람 화면은 저장 알림으로 새 판을 불러온다.
(function () {
  var btn = document.querySelector('[data-paper-toc]');
  if (!btn) return;
  function say(t, kind) {
    var m = document.querySelector('[data-msg]'), s = document.querySelector('[data-state]');
    if (m) m.textContent = t; if (s) s.setAttribute('data-kind', kind || '');
  }
  function pageAt(hwp, ctrl, field) {
    if (!hwp.moveTo(field, false)) return 0;
    try { var k = ctrl.KeyIndicator(); return (k && (k.prnpageno || k.pageno)) || 0; } catch (e) { return 0; }
  }
  btn.addEventListener('click', function () {
    var hwp = window.auraHwp, ctrl = window.auraCtrl, eng = window.auraCollab, url = '/hwp/' + btn.getAttribute('data-paper-toc') + '/paper-toc';
    if (!hwp || !ctrl || btn.disabled) return;
    btn.disabled = true;
    say('목차 쪽 번호를 재는 중', 'busy');
    var flushed = eng && eng.flush ? eng.flush() : Promise.resolve();
    flushed.then(function () {
      return fetch(url, {credentials: 'same-origin', cache: 'no-store'}).then(function (r) { if (!r.ok) throw new Error('목차 줄을 받지 못했습니다(' + r.status + ')'); return r.json(); });
    }).then(function (j) {
      var cur = hwp.getCursor(), pages = {}, n = 0;
      (j.fields || []).forEach(function (f) { var p = pageAt(hwp, ctrl, f); if (p > 0) { pages[f] = p; n++; } });
      hwp.restoreCursor(cur);
      if (!n) throw new Error('쪽을 잰 칸이 없습니다');
      say('목차에 쪽 번호를 넣는 중', 'busy');
      return fetch(url, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({pages: pages})})
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (b) { if (!r.ok) throw new Error(b.detail || '쓰지 못했습니다(' + r.status + ')'); return b; }); });
    }).then(function (b) {
      if (!b.changed) { say('목차 쪽 번호가 이미 맞습니다', 'ok'); btn.disabled = false; return; }
      say('목차 쪽 번호를 넣었습니다 · 새 판을 엽니다', 'ok');
      setTimeout(function () { location.reload(); }, 600);
    }).catch(function (e) { say(e.message || '목차 쪽 번호를 넣지 못했습니다', 'bad'); btn.disabled = false; });
  });
})();
