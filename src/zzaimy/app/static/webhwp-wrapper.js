/* 웹 한글 기안기 어댑터 — 한컴 WebHwpCtrl API 를 부르는 곳은 여기 하나다(ADR-0070).
 * 공동 편집 층(webhwp-collab.js)·편집 화면(hwp_edit.html)·AURA 명령 실행은 이 작은 인터페이스만 쓴다.
 * 제품 판이 바뀌어 API 가 달라지면 이 파일만 고친다.
 *
 * AuraHwpWrapper.create(ctrl, box) → {
 *   fields(), read(name), readMany(names), write(name, text), setEditable(name, on), setFormMode(on), formMode(),
 *   setReadOnly(on), currentField(), getCursor(), restoreCursor(c), moveTo(name, select), caretRect(),
 *   measureField(name) → Promise<{x, y, h, x2, y2} | null>, onLocalEdit(cb), onCaret(cb), onActivity(cb), attach(),
 *   isComposing(), lastInputAt(), exportField(name) → Promise<json>, importField(name, json) → Promise<bool>,
 *   open(blob, name) → Promise, hwpx() → Promise<Uint8Array>, run(action), exec(action, items), insertText(text) }
 * 글은 언제나 \n 줄바꿈으로 주고받는다(기안기는 \r\n).
 */
(function () {
  'use strict';
  var SEP = '\u0002';
  function nl(s) { return String(s == null ? '' : s).replace(/\r\n/g, '\n').replace(/\r/g, '\n'); }
  function b64ToBytes(s) { var bin = atob(s), out = new Uint8Array(bin.length); for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i); return out; }

  function create(ctrl, box) {
    var editable = {};                 // 필드 → 지금 편집 가능 여부(같은 값으로 다시 부르지 않게)
    var listeners = {edit: [], caret: [], activity: []};
    var composing = false, lastInput = 0, attachedDoc = null, probing = null;

    function frame() { return box.querySelector('iframe'); }
    function doc() { try { return frame().contentDocument; } catch (e) { return null; } }
    function fire(kind, ev) { listeners[kind].forEach(function (cb) { try { cb(ev); } catch (e) { console.warn(e); } }); }

    function fields() {
      try { var s = ctrl.GetFieldList(0, 0) || ''; return s ? s.split(SEP).filter(Boolean) : []; } catch (e) { return []; }
    }
    function read(name) { try { return nl(ctrl.GetFieldText(name)); } catch (e) { return null; } }
    function readMany(names) {
      var out = {};
      if (!names.length) return out;
      try {
        var parts = String(ctrl.GetFieldText(names.join(SEP)) || '').split(SEP);
        names.forEach(function (n, i) { out[n] = nl(parts[i] || ''); });
      } catch (e) { names.forEach(function (n) { out[n] = read(n); }); }
      return out;
    }
    function write(name, text) {
      try { ctrl.PutFieldText(name, nl(text).replace(/\n/g, '\r\n')); return true; } catch (e) { return false; }
    }
    // 양식 보호 모드(EditMode 2)에서 「양식 모드에서 편집 가능」 속성(1)을 켜고 끈다 — 끈 칸은 입력이 들어가지 않는다
    function setEditable(name, on) {
      on = !!on;
      if (editable[name] === on) return;
      try { ctrl.ModifyFieldProperties(name, on ? 0 : 1, on ? 1 : 0); editable[name] = on; } catch (e) {}
    }
    function setFormMode(on) { try { ctrl.EditMode = on ? 2 : 1; } catch (e) {} }
    function formMode() { try { return ctrl.EditMode === 2; } catch (e) { return false; } }
    function setReadOnly(on) { try { ctrl.ReadOnlyMode = !!on; } catch (e) {} try { if (on) ctrl.EditMode = 0; } catch (e) {} }
    function currentField() { if (probing) return probing.field0; try { return ctrl.GetCurFieldName(0) || ''; } catch (e) { return ''; } }
    function scrollItems() { try { var m = ctrl.ScrollPosInfo.m_Set.m_SetItemMap, o = {}; Object.keys(m).forEach(function (k) { o[k] = m[k]; }); return o; } catch (e) { return null; } }
    function getCursor() { try { return {pos: ctrl.GetPos(), scroll: scrollItems()}; } catch (e) { return null; } }
    function restoreCursor(c) {
      if (!c || !c.pos) return;
      try { ctrl.SetPos(c.pos.list, c.pos.para, c.pos.pos); } catch (e) {}
      if (c.scroll) { try { var s = ctrl.ScrollPosInfo; Object.keys(c.scroll).forEach(function (k) { s.m_Set.m_SetItemMap[k] = c.scroll[k]; }); ctrl.ScrollPosInfo = s; } catch (e) {} }
    }
    function moveTo(name, select) { try { return ctrl.MoveToField(name, true, true, !!select) !== false; } catch (e) { return false; } }
    function caretRect() {
      try {
        var fr = frame(), el = fr.contentDocument.getElementById('HWP_CURSOR_VIEW');
        if (!el) return null;
        var r = el.getBoundingClientRect(), o = fr.getBoundingClientRect();
        if (!r.height) return null;
        return {x: o.left + r.left, y: o.top + r.top, h: r.height, top: o.top, bottom: o.top + o.height, left: o.left, right: o.left + o.width};
      } catch (e) { return null; }
    }
    // 다른 사람 칸의 자리 재기 — 커서를 그 칸 처음·끝으로 옮겨 커서 자리를 읽고 바로 되돌린다(화면 이동 없이).
    // 사용자가 그사이 키·마우스를 쓰면 그 자리에서 곧바로 되돌려 입력이 엉뚱한 칸에 들어가지 않게 한다
    function measureField(name) {
      return new Promise(function (resolve) {
        if (probing || composing) { resolve(null); return; }
        var d = doc(), cv = d && d.getElementById('HWP_CURSOR_VIEW');
        var keep = getCursor();
        if (!keep) { resolve(null); return; }
        probing = {keep: keep, aborted: false, field0: (function () { try { return ctrl.GetCurFieldName(0) || ''; } catch (e) { return ''; } })()};
        var vis = cv ? cv.style.visibility : '';
        if (cv) cv.style.visibility = 'hidden';
        var done = function (val) { if (cv) cv.style.visibility = vis; var p = probing; probing = null; if (p && !p.aborted) restoreCursor(p.keep); resolve(val); };
        try { ctrl.MoveToField(name, true, true, false); } catch (e) { done(null); return; }
        setTimeout(function () {
          if (!probing || probing.aborted) { done(null); return; }
          var a = caretRect();
          try { ctrl.MoveToField(name, true, false, false); } catch (e) {}
          setTimeout(function () {
            if (!probing || probing.aborted) { done(null); return; }
            var b = caretRect();
            done(a ? {x: a.x, y: a.y, h: a.h, x2: b ? b.x : a.x, y2: b ? b.y : a.y, top: a.top, bottom: a.bottom, left: a.left, right: a.right} : null);
          }, 0);
        }, 0);
      });
    }
    function abortProbe() { if (probing && !probing.aborted) { probing.aborted = true; restoreCursor(probing.keep); } }

    // 편집 틀(같은 출처)의 입력 사건 — 기안기는 문서 바뀜 사건을 주지 않아 키·조합·붙여넣기로 「내가 고쳤다」 를 안다
    function attach() {
      var d = doc();
      if (!d || d === attachedDoc) return !!d;
      attachedDoc = d;
      d.addEventListener('keydown', function (e) { abortProbe(); lastInput = Date.now(); fire('activity', e); }, true);
      d.addEventListener('pointerdown', function (e) { abortProbe(); fire('activity', e); }, true);
      d.addEventListener('compositionstart', function () { abortProbe(); composing = true; lastInput = Date.now(); }, true);
      d.addEventListener('compositionupdate', function () { composing = true; lastInput = Date.now(); fire('edit', {composing: true}); }, true);
      d.addEventListener('compositionend', function () { composing = false; lastInput = Date.now(); fire('edit', {composing: false}); }, true);
      ['input', 'paste', 'cut', 'drop'].forEach(function (t) {
        d.addEventListener(t, function () { lastInput = Date.now(); fire('edit', {composing: composing}); }, true);
      });
      d.addEventListener('keyup', function (e) {
        var k = e.key || '';
        if (k === 'Backspace' || k === 'Delete' || k === 'Enter' || k === 'Tab' || ((k.length === 1 || k === 'Process') && !e.ctrlKey && !e.metaKey)) fire('edit', {composing: composing});
        fire('caret', e);
      }, true);
      d.addEventListener('pointerup', function (e) { setTimeout(function () { fire('caret', e); }, 0); }, true);
      d.addEventListener('wheel', function (e) { fire('activity', e); }, {capture: true, passive: true});
      d.addEventListener('scroll', function (e) { fire('activity', e); }, true);
      return true;
    }

    // 절 필드를 서식째(기안기 JSON 블록) 꺼내고 넣기 — 시험 기능(M3). 커서는 되돌린다
    function exportField(name) {
      return new Promise(function (resolve) {
        var keep = getCursor();
        try { ctrl.MoveToField(name, true, true, true); } catch (e) { resolve(null); return; }
        try { ctrl.GetTextFile('JSON', 'saveblock', function (d) { restoreCursor(keep); resolve(typeof d === 'string' ? d : null); }); }
        catch (e) { restoreCursor(keep); resolve(null); }
      });
    }
    function importField(name, json) {
      return new Promise(function (resolve) {
        var keep = getCursor(), t = setTimeout(function () { restoreCursor(keep); resolve(false); }, 5000);
        try { ctrl.MoveToField(name, true, true, true); } catch (e) { clearTimeout(t); resolve(false); return; }
        try { ctrl.SetTextFile(json, 'JSON', 'insertfile', function (r) { clearTimeout(t); restoreCursor(keep); resolve(!(r && r.result === false)); }); }
        catch (e) { clearTimeout(t); restoreCursor(keep); resolve(false); }
      });
    }
    function open(blob, name) {
      return new Promise(function (resolve, reject) {
        try {
          ctrl.Open(new File([blob], name), '', '', function (res) {
            editable = {}; attachedDoc = null;
            if (res && res.result === false) reject(new Error('문서를 열지 못했습니다')); else resolve(res);
          });
        } catch (e) { reject(e); }
      });
    }
    function hwpx() {
      return new Promise(function (resolve) { ctrl.GetTextFile('HWPX', '', function (data) { resolve((typeof data === 'string') ? b64ToBytes(data) : data); }); });
    }
    function run(action) { try { return ctrl.Run(action); } catch (e) { return false; } }
    function exec(name, items) {
      var a = ctrl.CreateAction(name);
      if (!a) throw new Error(name + ' 동작이 없습니다');
      var s = a.CreateSet(); a.GetDefault(s);
      Object.keys(items || {}).forEach(function (k) {
        var parts = k.split('.'), t = s;
        for (var i = 0; i < parts.length - 1; i++) t = t.Item(parts[i]) || t.CreateItemSet(parts[i], parts[i]);
        t.SetItem(parts[parts.length - 1], items[k]);
      });
      return a.Execute(s);
    }
    // ── 명령 실행기(webhwp-agent-runner.js)가 쓰는 읽기·찾기·그림 — 기안기 API 는 여기서만 ──
    var MM = 283.465;
    function item(set, k) { try { return set ? set.Item(k) : null; } catch (e) { return null; } }
    function hex(bgr) { if (typeof bgr !== 'number') return null; var r = bgr & 255, g = (bgr >> 8) & 255, b = (bgr >> 16) & 255; return '#' + [r, g, b].map(function (x) { return ('0' + x.toString(16)).slice(-2); }).join('').toUpperCase(); }
    function mm(v) { return typeof v === 'number' ? Math.round(v / MM * 10) / 10 : null; }
    function inTable() { try { var p = ctrl.ParentCtrl; return !!(p && p.CtrlID === 'tbl'); } catch (e) { return false; } }
    function inspect(keys) {
      var out = {};
      (keys || []).forEach(function (k) {
        try {
          if (k === 'pos') out.pos = ctrl.GetPos();
          else if (k === 'field') out.field = currentField();
          else if (k === 'selection') { var t = ctrl.GetTextFile('TEXT', 'saveblock'); out.selection = typeof t === 'string' ? nl(t).slice(0, 4000) : ''; }
          else if (k === 'char') { var cs = ctrl.CharShape; out.char = {font: item(cs, 'FaceNameHangul'), font_latin: item(cs, 'FaceNameLatin'), pt: (item(cs, 'Height') || 0) / 100,
            bold: !!item(cs, 'Bold'), italic: !!item(cs, 'Italic'), underline: item(cs, 'UnderlineType'), color: hex(item(cs, 'TextColor')), shade: hex(item(cs, 'ShadeColor'))}; }
          else if (k === 'para') { var ps = ctrl.ParaShape; out.para = {align: ['양쪽', '왼쪽', '오른쪽', '가운데', '배분', '나눔'][item(ps, 'AlignType')] || item(ps, 'AlignType'),
            line_spacing: item(ps, 'LineSpacing'), line_spacing_type: item(ps, 'LineSpacingType'), left_mm: mm(item(ps, 'LeftMargin')), right_mm: mm(item(ps, 'RightMargin')),
            indent_mm: mm(item(ps, 'Indentation')), before_pt: (item(ps, 'PrevSpacing') || 0) / 100, after_pt: (item(ps, 'NextSpacing') || 0) / 100}; }
          else if (k === 'heading') out.heading = ctrl.GetHeadingString() || '';
          else if (k === 'pages') out.pages = ctrl.PageCount;
          else if (k === 'table') out.table = inTable();
          else if (k === 'fields') out.fields = fields().slice(0, 300);
          else if (k === 'text') { var all = ctrl.GetTextFile('TEXT', ''); out.text = typeof all === 'string' ? nl(all).slice(0, 12000) : ''; }
        } catch (e) { out[k] = null; }
      });
      return out;
    }
    // 문서 처음부터 n 번째 「글」 을 블록으로 — 기안기 찾기(ForwardFind)를 알림 창 없이
    function findNth(text, n) {
      run('Cancel'); run('MoveDocBegin');
      for (var i = 0; i < n; i++) { if (exec('ForwardFind', {FindString: text, IgnoreMessage: 1, Direction: 0}) === false) return false; }
      return true;
    }
    // 그림 넣기 — data: 주소로(기안기는 주소로 준 그림을 기안기 서버가 받으러 가 우리 주소로는 실패한다)
    function insertPicture(url, wmm, hmm) {
      return new Promise(function (res, rej) {
        var t = setTimeout(function () { res(); }, 8000);
        var ok = ctrl.InsertPicture(url, true, 1, false, false, 0, wmm, hmm, function () { clearTimeout(t); res(); });
        if (ok === false) { clearTimeout(t); rej(new Error('그림을 넣지 못했습니다')); }
      });
    }
    function on(kind, cb) { listeners[kind].push(cb); }
    return {
      ctrl: ctrl, fields: fields, read: read, readMany: readMany, write: write, setEditable: setEditable,
      setFormMode: setFormMode, formMode: formMode, setReadOnly: setReadOnly, currentField: currentField,
      getCursor: getCursor, restoreCursor: restoreCursor, moveTo: moveTo, caretRect: caretRect, measureField: measureField,
      onLocalEdit: function (cb) { on('edit', cb); }, onCaret: function (cb) { on('caret', cb); }, onActivity: function (cb) { on('activity', cb); },
      attach: attach, isProbing: function () { return !!probing; },
      isComposing: function () { return composing && Date.now() - lastInput < 3000; },     // 조합 끝 사건을 놓쳐도 3초 뒤엔 풀린다
      lastInputAt: function () { return lastInput; }, exportField: exportField, importField: importField, open: open, hwpx: hwpx,
      run: run, exec: exec, insertText: function (t) { return exec('InsertText', {Text: String(t)}); },
      resetEditable: function () { editable = {}; },
      inspect: inspect, findNth: findNth, insertPicture: insertPicture, inTable: inTable
    };
  }
  window.AuraHwpWrapper = {create: create, nl: nl};
})();
