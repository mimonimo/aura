/* 웹 한글 기안기 어댑터 — 한컴 WebHwpCtrl API 를 부르는 곳은 여기 하나다(ADR-0070).
 * 공동 편집 층(webhwp-collab.js)·편집 화면(hwp_edit.html)·AURA 명령 실행은 이 작은 인터페이스만 쓴다.
 * 제품 판이 바뀌어 API 가 달라지면 이 파일만 고친다.
 *
 * AuraHwpWrapper.create(ctrl, box) → {
 *   fields(), read(name), readMany(names), write(name, text), applyDelta(name, text0, at, del, ins), setEditable(name, on), setFormMode(on), formMode(),
 *   setReadOnly(on), currentField(), getCursor(), restoreCursor(c), moveTo(name, select), caretRect(),
 *   fieldGeom(names) → {이름: {page,x,y,h,page2,x2,y2,h2}} | null(쪽 좌표), toScreen(page,x,y), bodyLeft(), unitPx(v), onLocalEdit(cb), onCaret(cb), onActivity(cb), attach(),
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
    // 칸 안 일부만 고치기 — text0(지금 칸 글, \n 줄바꿈)에서 at 부터 del 글자를 지우고 ins 를 넣는다(UTF-16 자리).
    // 칸 처음 자리(MoveToField)에서 문단·자리를 세어 SelectText+Delete, SetPos+InsertText(줄바꿈은 BreakPara)로.
    // 451쪽 문서 5만 자 칸에서 넣기 4.7ms·지우기 3.3ms(통째 쓰기 2,118ms). 대리 쌍·탭이 든 글은 자리 셈을 믿을 수 없어 false(부르는 쪽이 통째 쓰기로)
    function applyDelta(name, text0, at, del, ins) {
      if (/[\ud800-\udfff\t]/.test(text0) || /[\ud800-\udfff\t]/.test(ins)) return false;
      // 남이 쥔 칸은 양식 모드에서 잠겨 있어 문단 나누기·지우기가 듣지 않는다(글 넣기는 든다, 실측) — 고치는 동안만 풀었다 다시 잠근다
      var relock = editable[name] === false;
      if (relock) setEditable(name, true, true);
      try { return applyDelta0(name, text0, at, del, ins); } finally { if (relock) setEditable(name, false, true); }
    }
    function applyDelta0(name, text0, at, del, ins) {
      try {
        if (ctrl.MoveToField(name, true, true, false) === false) return false;
        var s0 = ctrl.GetPos();
        var at2 = function (k) { var ls = text0.slice(0, k).split('\n'), dp = ls.length - 1; return {list: s0.list, para: s0.para + dp, pos: dp ? ls[dp].length : s0.pos + ls[0].length}; };
        if (del > 0) { var a = at2(at), b = at2(at + del); ctrl.SetPos(a.list, a.para, a.pos); if (ctrl.SelectText(a.para, a.pos, b.para, b.pos) === false) return false; run('Delete'); }
        if (ins) {
          var p = at2(at); ctrl.SetPos(p.list, p.para, p.pos);
          ins.split('\n').forEach(function (t, i) { if (i) run('BreakPara'); if (t) exec('InsertText', {Text: t}); });
        }
        return true;
      } catch (e) { return false; }
    }
    // 양식 보호 모드(EditMode 2)에서 「양식 모드에서 편집 가능」 속성(1)을 켜고 끈다 — 끈 칸은 입력이 들어가지 않는다
    function setEditable(name, on, force) {
      on = !!on;
      if (editable[name] === on && !force) return;
      try { ctrl.ModifyFieldProperties(name, on ? 0 : 1, on ? 1 : 0); editable[name] = on; } catch (e) {}
    }
    function setFormMode(on) { try { ctrl.EditMode = on ? 2 : 1; } catch (e) {} }
    function formMode() { try { return ctrl.EditMode === 2; } catch (e) { return false; } }
    function setReadOnly(on) { try { ctrl.ReadOnlyMode = !!on; } catch (e) {} try { if (on) ctrl.EditMode = 0; } catch (e) {} }
    function currentField() { if (probing) return probing.field0; try { return ctrl.GetCurFieldName(0) || ''; } catch (e) { return ''; } }
    function scrollItems() { try { var m = ctrl.ScrollPosInfo.m_Set.m_SetItemMap, o = {}; Object.keys(m).forEach(function (k) { o[k] = m[k]; }); return o; } catch (e) { return null; } }
    // 커서 저장·되돌리기 — 문단 번호(GetPos)는 문서 처음부터 세므로, 위쪽 칸의 문단 수가 바뀌면(남이 줄을 넣거나 AURA 가 절을 씀) 엉뚱한 칸으로 갔다
    // (451쪽 실측: AURA 전체 초안 뒤 네 번 중 네 번). 그래서 커서가 든 칸 처음에서 센 자리(칸 기준)로 저장하고 그 칸 처음에서 다시 센다.
    // abs 면 문단 번호 그대로(같은 작업 단위 안에서 바로 되돌릴 때 — 칸 자리 재기). 화면은 커서가 보이던 높이를 지키도록 스크롤을 맞춘다
    function caretY() { var A = app(); try { var k = A.hwpCaret.KNi(), p = toScreen(k.page, k.x, k.y); return p ? p.y : null; } catch (e) { return null; } }
    function getCursor(abs) {
      var c;
      try { c = {pos: ctrl.GetPos(), scroll: scrollItems()}; } catch (e) { return null; }
      if (abs) return c;
      try {
        var f = ctrl.GetCurFieldName(0) || '';
        if (f && ctrl.MoveToField(f, true, true, false) !== false) {
          var s0 = ctrl.GetPos();
          if (s0.list === c.pos.list && c.pos.para >= s0.para) c.rel = {field: f, dp: c.pos.para - s0.para, pos: c.pos.para === s0.para ? c.pos.pos - s0.pos : c.pos.pos};
          ctrl.SetPos(c.pos.list, c.pos.para, c.pos.pos);
        }
      } catch (e) {}
      c.y = caretY();
      return c;
    }
    function restoreCursor(c) {
      if (!c || !c.pos) return;
      var done = false;
      if (c.rel) {
        try {
          if (ctrl.MoveToField(c.rel.field, true, true, false) !== false) {
            var s0 = ctrl.GetPos();
            ctrl.SetPos(s0.list, s0.para + c.rel.dp, c.rel.dp ? c.rel.pos : s0.pos + c.rel.pos); done = true;
          }
        } catch (e) {}
      }
      if (!done) { try { ctrl.SetPos(c.pos.list, c.pos.para, c.pos.pos); } catch (e) {} }
      if (c.scroll) {                         // 바뀐 때만 되돌린다(같은 값을 다시 넣으면 기안기가 화면을 다시 그린다)
        try {
          var s = ctrl.ScrollPosInfo, m = s.m_Set.m_SetItemMap, diff = false;
          Object.keys(c.scroll).forEach(function (k) { if (m[k] !== c.scroll[k]) { m[k] = c.scroll[k]; diff = true; } });
          if (diff) ctrl.ScrollPosInfo = s;
        } catch (e) {}
      }
      // 커서가 보이던 높이 지키기 — 위쪽 글이 늘거나 줄어 커서가 밀렸으면 그만큼 스크롤(세로 스크롤 값 1 = 화면 1/75px, 실측)
      if (c.y != null && c.rel) {
        try {
          var y1 = caretY(), fr = frame().getBoundingClientRect();
          if (y1 != null && Math.abs(y1 - c.y) > 2 && c.y > fr.top && c.y < fr.bottom) {
            var s2 = ctrl.ScrollPosInfo, m2 = s2.m_Set.m_SetItemMap;
            if (typeof m2['28675'] === 'number') { m2['28675'] = Math.max(0, m2['28675'] + (y1 - c.y) * 75); ctrl.ScrollPosInfo = s2; }
          }
        } catch (e) {}
      }
    }
    var undoLocked = false;
    // 남의 고침을 넣으면 기안기 되돌리기 기록에 들어가 내 Ctrl+Z 가 남의 글을 지웠다(실측) — 넣은 뒤에는 되돌리기를 잠그고, 내가 고치면 푼다
    function lockUndo(on) {
      on = !!on;
      if (undoLocked === on) return;
      try { ctrl.LockCommand('Undo', on); ctrl.LockCommand('Redo', on); undoLocked = on; } catch (e) {}
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
    // ── 칸 자리 재기(커서·화면을 건드리지 않고) ──
    // 기안기 화면 커서(HWP_CURSOR_VIEW)는 그리기 차례에 늦게 옮겨지고, 가려지거나 초점이 없는 창에서는 아예 늦어 그것으로 재면
    // 엉뚱한 자리가 나왔다(10-24 실측). 그래서 기안기 내부의 커서 모델(HwpApp.hwpCaret — 쪽·쪽 안 좌표)과 화면 변환(HwpApp.hxe.CalcCaretPos)을
    // 같은 작업 단위 안에서 읽는다: 커서를 칸 처음·끝으로 옮겨 쪽 좌표를 읽고 곧바로 되돌린다 — 그사이 그리기가 없어 화면에는 아무 일도 없다.
    // 내부가 없으면(제품 판이 바뀜) null — 틀린 자리를 그리느니 그리지 않는다.
    function app() { try { return frame().contentWindow.HwpApp || null; } catch (e) { return null; } }
    function geomReady() { var A = app(); return !!(A && A.hwpCaret && typeof A.hwpCaret.KNi === 'function' && A.hxe && typeof A.hxe.CalcCaretPos === 'function'); }
    function hasSelection() {
      try { var s = ctrl.GetSelectedPos(); return !!(s && (s.slist !== s.elist || s.spara !== s.epara || s.spos !== s.epos) && s.slist !== undefined); } catch (e) { return false; }
    }
    function fieldGeom(names) {
      var A = app(), out = {};
      if (!geomReady() || composing && Date.now() - lastInput < 3000 || probing || hasSelection()) return null;
      var keep = getCursor(true);
      if (!keep) return null;
      probing = {field0: (function () { try { return ctrl.GetCurFieldName(0) || ''; } catch (e) { return ''; } })()};
      try {
        names.forEach(function (n) {
          try {
            if (ctrl.MoveToField(n, true, true, false) === false) return;
            var a = A.hwpCaret.KNi();
            ctrl.MoveToField(n, true, false, false);
            var b = A.hwpCaret.KNi();
            if (a && b && typeof a.y === 'number') out[n] = {page: a.page, x: a.x, y: a.y, h: a.h, page2: b.page, x2: b.x, y2: b.y, h2: b.h};
          } catch (e) {}
        });
      } finally {
        restoreCursor(keep);
        probing = null;
      }
      return out;
    }
    var leftMargin = null;
    function bodyLeft() {                     // 쪽 왼쪽 여백(HWPUNIT) — 이름표를 글 밖 여백에 두려고
      if (leftMargin !== null) return leftMargin;
      if (composing && Date.now() - lastInput < 3000) return 0;   // 조합 중에는 기안기에 묻지 않는다(조합이 끊긴다) — 다음에 다시
      try { var a = ctrl.CreateAction('PageSetup'), s = a.CreateSet(); a.GetDefault(s); var pd = s.Item('PageDef'); leftMargin = (pd.Item('LeftMargin') || 0) + (pd.Item('GutterLen') || 0); }
      catch (e) { leftMargin = 0; }
      return leftMargin;
    }
    // 쪽 좌표 → 편집 화면(바깥 문서) 좌표. 보이는 종이 칸(view)도 함께
    function toScreen(page, x, y) {
      var A = app();
      if (!A) return null;
      try {
        var fr = frame(), d = fr.contentDocument, pc = d.getElementById('PaperCanvas') || d.getElementById('EditCanvas');
        var o = fr.getBoundingClientRect(), c = pc.getBoundingClientRect(), p = A.hxe.CalcCaretPos(page, x, y), z = A.hxe.GetCurrentZoom ? A.hxe.GetCurrentZoom() : 1;
        if (!p || p.x < -1e5) return null;
        return {x: o.left + c.left + p.x, y: o.top + c.top + p.y, zoom: z, view: {left: o.left + c.left, top: o.top + c.top, right: o.left + c.right, bottom: o.top + c.bottom}};
      } catch (e) { return null; }
    }
    // 쪽 좌표 길이(HWPUNIT) → 화면 px
    function unitPx(v) { var A = app(); try { var z = A.hxe.GetCurrentZoom(); return v / 7200 * 96 * z; } catch (e) { return 0; } }
    function abortProbe() {}

    // 편집 틀(같은 출처)의 입력 사건 — 기안기는 문서 바뀜 사건을 주지 않아 키·조합·붙여넣기로 「내가 고쳤다」 를 안다
    function attach() {
      var d = doc();
      if (!d || d === attachedDoc) return !!d;
      attachedDoc = d;
      d.addEventListener('keydown', function (e) { abortProbe(); lastInput = Date.now(); fire('activity', e); }, true);
      d.addEventListener('pointerdown', function (e) { abortProbe(); lastInput = Date.now(); fire('activity', e); }, true);
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
        var keep = getCursor(true);
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
      ctrl: ctrl, fields: fields, read: read, readMany: readMany, write: write, applyDelta: applyDelta, setEditable: setEditable,
      setFormMode: setFormMode, formMode: formMode, setReadOnly: setReadOnly, currentField: currentField,
      getCursor: getCursor, restoreCursor: restoreCursor, lockUndo: lockUndo, undoLocked: function () { return undoLocked; }, moveTo: moveTo, caretRect: caretRect,
      fieldGeom: fieldGeom, toScreen: toScreen, bodyLeft: bodyLeft, unitPx: unitPx, geomReady: geomReady,
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
