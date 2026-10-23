/* AURA 기안기 명령 실행기(2) — 대화의 에이전트가 넣은 기안기 기본 기능 명령(webhwp_native 허용 목록)을 편집 화면에서 실행한다.
 *
 * AuraHwpRunner.exec(cmd) → Promise<{ok, message, data, changed}>
 *   편집 화면의 명령 실행(hwp_edit.html 의 pollCmds·runCmds)이 /hwp/{id}/cmd?runner=2 로 받은 명령 하나를 넘기면,
 *   담당자 커서를 기억해 두고 단계를 실행한 뒤 커서를 되돌리고 결과를 돌려준다. 결과 보고(POST /hwp/{id}/cmd/{cid})·구조 잠금·저장은
 *   편집 화면이 지금처럼 한다(changed 가 false 면 — 읽기만 한 명령 — 저장하지 않아도 된다).
 * 편집 화면에 명령 실행이 없는 페이지는 <script src="/static/webhwp-agent-runner.js" data-poll="1"> 로 넣으면 스스로 묻고 보고한다
 * (이때 바꾼 뒤에는 편집 틀에 input 사건을 보내 자동 저장 흐름을 탄다).
 * 한컴 API 는 공동 편집 어댑터(webhwp-wrapper.js, window.auraHwp)로 부르고, 어댑터에 없는 읽기·그림 넣기·찾기만 여기서 부른다.
 * 서버가 허용 목록(webhwp_native.steps_ok)으로 거른 단계만 오지만, 여기서도 모르는 단계는 실행하지 않는다.
 */
(function () {
  'use strict';
  if (window.AuraHwpRunner) return;
  var POLL = 1500, QUIET = 1200, MM = 283.465;
  var m = /[?&]id=(\d+)/.exec(location.search);
  var fid = m ? m[1] : '';
  var base = '/hwp/' + fid + '/cmd';
  var busy = false, hw = null;
  var me = document.currentScript, selfPoll = !!(me && me.getAttribute('data-poll') === '1');

  function ctrl() { return window.auraCtrl || null; }
  function box() { return document.getElementById('hwpctrl') || document.querySelector('[data-hwpctrl]') || document.body; }
  // 어댑터 — 공동 편집 층이 만든 것(window.auraHwp)이 있으면 그것, 없으면 AuraHwpWrapper 로 새로, 그것도 없으면 얇은 대체
  function adapter() {
    if (hw) return hw;
    var c = ctrl();
    if (!c) return null;
    if (window.auraHwp && window.auraHwp.exec) hw = window.auraHwp;
    else if (window.AuraHwpWrapper) hw = window.AuraHwpWrapper.create(c, box());
    else hw = {
      run: function (a) { try { return c.Run(a); } catch (e) { return false; } },
      exec: function (name, items) {
        var a = c.CreateAction(name), s = a.CreateSet(); a.GetDefault(s);
        Object.keys(items || {}).forEach(function (k) {
          var parts = k.split('.'), t = s;
          for (var i = 0; i < parts.length - 1; i++) t = t.Item(parts[i]) || t.CreateItemSet(parts[i], parts[i]);
          t.SetItem(parts[parts.length - 1], items[k]);
        });
        return a.Execute(s);
      },
      insertText: function (t) { return this.exec('InsertText', {Text: String(t)}); },
      moveTo: function (n, sel) { try { return c.MoveToField(n, true, true, !!sel) !== false; } catch (e) { return false; } },
      getCursor: function () { try { return {pos: c.GetPos()}; } catch (e) { return null; } },
      restoreCursor: function (k) { if (k && k.pos) { try { c.SetPos(k.pos.list, k.pos.para, k.pos.pos); } catch (e) {} } },
      fields: function () { try { var s = c.GetFieldList(0, 0) || ''; return s ? s.split('\u0002').filter(Boolean) : []; } catch (e) { return []; } },
      currentField: function () { try { return c.GetCurFieldName(0) || ''; } catch (e) { return ''; } },
      isComposing: function () { return false; }, lastInputAt: function () { return 0; }
    };
    return hw;
  }

  // ── 읽기(어댑터에 없는 것) ──
  function item(set, k) { try { return set ? set.Item(k) : null; } catch (e) { return null; } }
  function hex(bgr) { if (typeof bgr !== 'number') return null; var r = bgr & 255, g = (bgr >> 8) & 255, b = (bgr >> 16) & 255; return '#' + [r, g, b].map(function (x) { return ('0' + x.toString(16)).slice(-2); }).join('').toUpperCase(); }
  function mm(v) { return typeof v === 'number' ? Math.round(v / MM * 10) / 10 : null; }
  function read(keys) {
    var c = ctrl(), h = adapter(), out = {};
    keys.forEach(function (k) {
      try {
        if (k === 'pos') out.pos = c.GetPos();
        else if (k === 'field') out.field = h.currentField ? h.currentField() : (c.GetCurFieldName(0) || '');
        else if (k === 'selection') { var t = c.GetTextFile('TEXT', 'saveblock'); out.selection = typeof t === 'string' ? t.replace(/\r\n/g, '\n').slice(0, 4000) : ''; }
        else if (k === 'char') { var cs = c.CharShape; out.char = {font: item(cs, 'FaceNameHangul'), font_latin: item(cs, 'FaceNameLatin'), pt: (item(cs, 'Height') || 0) / 100,
          bold: !!item(cs, 'Bold'), italic: !!item(cs, 'Italic'), underline: item(cs, 'UnderlineType'), color: hex(item(cs, 'TextColor')), shade: hex(item(cs, 'ShadeColor'))}; }
        else if (k === 'para') { var ps = c.ParaShape; out.para = {align: ['양쪽', '왼쪽', '오른쪽', '가운데', '배분', '나눔'][item(ps, 'AlignType')] || item(ps, 'AlignType'),
          line_spacing: item(ps, 'LineSpacing'), line_spacing_type: item(ps, 'LineSpacingType'), left_mm: mm(item(ps, 'LeftMargin')), right_mm: mm(item(ps, 'RightMargin')),
          indent_mm: mm(item(ps, 'Indentation')), before_pt: (item(ps, 'PrevSpacing') || 0) / 100, after_pt: (item(ps, 'NextSpacing') || 0) / 100}; }
        else if (k === 'heading') out.heading = c.GetHeadingString() || '';
        else if (k === 'pages') out.pages = c.PageCount;
        else if (k === 'table') { var p = c.ParentCtrl; out.table = !!(p && p.CtrlID === 'tbl'); }
        else if (k === 'fields') out.fields = (h.fields ? h.fields() : []).slice(0, 300);
        else if (k === 'text') { var all = c.GetTextFile('TEXT', ''); out.text = typeof all === 'string' ? all.replace(/\r\n/g, '\n').slice(0, 12000) : ''; }
      } catch (e) { out[k] = null; }
    });
    return out;
  }
  function inTable() { try { var p = ctrl().ParentCtrl; return !!(p && p.CtrlID === 'tbl'); } catch (e) { return false; } }

  // 문서 처음부터 n 번째 「글」 을 블록으로 — 기안기 찾기(ForwardFind)를 알림 창 없이
  function find(text, n) {
    var h = adapter();
    h.run('Cancel'); h.run('MoveDocBegin');
    for (var i = 0; i < n; i++) {
      if (h.exec('ForwardFind', {FindString: text, IgnoreMessage: 1, Direction: 0}) === false) return false;
    }
    return true;
  }

  function picture(cid, wmm, hmm) {
    return fetch(base + '/' + cid + '/asset', {credentials: 'same-origin', cache: 'no-store'})
      .then(function (r) { if (!r.ok) throw new Error('그림을 받지 못했습니다(' + r.status + ')'); return r.blob(); })
      .then(function (b) {
        // 기안기는 주소로 준 그림을 기안기 서버가 받아 오려 해 우리 주소로는 실패한다 — data: 주소로 넘긴다(2026-10-24 확인)
        return new Promise(function (res, rej) { var fr = new FileReader(); fr.onload = function () { res(fr.result); }; fr.onerror = rej; fr.readAsDataURL(b); });
      })
      .then(function (url) {
        return new Promise(function (res, rej) {
          var t = setTimeout(function () { res(); }, 8000);
          var ok = ctrl().InsertPicture(url, true, 1, false, false, 0, wmm, hmm, function () { clearTimeout(t); res(); });
          if (ok === false) { clearTimeout(t); rej(new Error('그림을 넣지 못했습니다')); }
        });
      });
  }

  var KINDS = {field: 1, find: 1, run: 1, exec: 1, text: 1, assert: 1, read: 1, picture: 1};
  // 단계를 차례로 — 그림만 기다림이 있어 Promise 로 잇는다. 결과 {changed, data}
  function runSteps(cmd) {
    var h = adapter(), data = null, changed = false, i = 0, steps = cmd.steps || [];
    function next() {
      if (i >= steps.length) return Promise.resolve({changed: changed, data: data});
      var st = steps[i++];
      if (!KINDS[st[0]]) return Promise.reject(new Error('모르는 단계'));
      if (st[0] === 'field') { if (!h.moveTo(st[1], !!st[2])) throw new Error('필드 「' + st[1] + '」 을 찾지 못했습니다'); }
      else if (st[0] === 'find') { if (!find(st[1], st[2])) throw new Error('「' + st[1] + '」 을 ' + st[2] + '번째까지 찾지 못했습니다'); }
      else if (st[0] === 'assert') { if (!inTable()) throw new Error('표 칸이 아닙니다'); }
      else if (st[0] === 'run') { if (h.run(st[1]) === false) throw new Error(st[1] + ' 동작이 실패했습니다'); if (!/^(Move|Cancel|TableCellBlock|Table(Right|Lower)Cell$|Select$)/.test(st[1])) changed = true; }
      else if (st[0] === 'exec') { if (h.exec(st[1], st[2]) === false) throw new Error(st[1] + ' 동작이 실패했습니다'); if (st[1] !== 'ForwardFind') changed = true; }
      else if (st[0] === 'text') { h.insertText(st[1]); changed = true; }
      else if (st[0] === 'read') { data = read(st[1]); }
      else if (st[0] === 'picture') { changed = true; return picture(cmd.id, st[1], st[2]).then(next); }
      return Promise.resolve().then(next);
    }
    return Promise.resolve().then(next);
  }

  function changedNotice(cmd) {
    try { var fr = box().querySelector('iframe'); if (fr && fr.contentDocument) fr.contentDocument.dispatchEvent(new Event('input', {bubbles: true})); } catch (e) {}
    try { window.dispatchEvent(new CustomEvent('aura-hwp-command', {detail: {id: cmd.id, action: cmd.action, label: cmd.label}})); } catch (e) {}
  }
  function say(text, kind) {
    var el = document.querySelector('[data-msg]'), st = document.querySelector('[data-state]');
    if (el) el.textContent = text; if (st) st.setAttribute('data-kind', kind || '');
  }
  function onlyReads(cmd) {
    return (cmd.steps || []).every(function (s) { return s[0] === 'read' || s[0] === 'field' || s[0] === 'find' || s[0] === 'assert' || (s[0] === 'run' && /^(Move|Cancel)/.test(s[1])); });
  }
  // 명령 하나 실행 — 커서를 지키고 결과를 돌려준다(보고는 부른 쪽이)
  function exec(cmd) {
    var h = adapter();
    if (!h) return Promise.resolve({ok: false, message: '기안기가 아직 준비되지 않았습니다', changed: false});
    var keep = h.getCursor ? h.getCursor() : null;
    return runSteps(cmd).then(function (r) { return {ok: true, message: '', data: r.data, changed: r.changed}; },
                              function (e) { return {ok: false, message: String(e && e.message || e), changed: true}; })
      .then(function (res) {
        try { h.run('Cancel'); } catch (e) {}
        if (keep && h.restoreCursor) h.restoreCursor(keep);           // 담당자 커서는 제자리로
        return res;
      });
  }

  function one(list) {
    var cmd = list.shift();
    if (!cmd) { busy = false; return; }
    var ro = onlyReads(cmd);
    if (!ro) say('AURA가 ' + cmd.label + ' 하는 중', 'busy');
    exec(cmd).then(function (res) {
      if (res.changed) changedNotice(cmd);
      if (!res.ok) say('AURA 명령이 안 됐습니다 · ' + res.message, 'bad');
      else if (!ro) say('AURA가 ' + cmd.label + ' 했습니다', 'ok');
      return fetch(base + '/' + cmd.id, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ok: res.ok, message: res.message, data: res.data || null})}).catch(function () {});
    }).then(function () { setTimeout(function () { one(list); }, 150); });
  }

  function poll() {
    if (!fid || busy || !ctrl()) return;
    var h = adapter();
    if (!h) return;
    if ((h.isComposing && h.isComposing()) || (h.lastInputAt && Date.now() - h.lastInputAt() < QUIET)) return;   // 담당자가 치는 중이면 기다린다
    busy = true;
    fetch(base + '?runner=2', {credentials: 'same-origin', cache: 'no-store'})
      .then(function (r) { return r.ok ? r.json() : {cmds: []}; })
      .then(function (j) { var list = j.cmds || []; if (!list.length) { busy = false; return; } one(list); })
      .catch(function () { busy = false; });
  }
  if (fid && selfPoll) setInterval(poll, POLL);
  window.AuraHwpRunner = {version: 2, exec: exec, onlyReads: onlyReads, read: read};
})();
