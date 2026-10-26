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
    var listeners = {edit: [], caret: [], activity: [], commit: []};
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
      var t = nl(text);
      // 기안기는 빈 글 넣기(PutFieldText "")를 말없이 무시한다(실측 — 남이 칸을 비워도 내 화면엔 옛 글이 남았다). 빈 글은 칸 글을 골라 지운다
      if (!t) { var cur = read(name); if (!cur) return true; if (applyDelta(name, cur, 0, cur.length, '') && !read(name)) return true; }
      try { ctrl.PutFieldText(name, t.replace(/\n/g, '\r\n')); return true; } catch (e) { return false; }
    }
    // 칸 안 일부만 고치기 — text0(지금 칸 글, \n 줄바꿈)에서 at 부터 del 글자를 지우고 ins 를 넣는다(UTF-16 자리).
    // 칸 처음 자리(MoveToField)에서 문단·자리를 세어 SelectText+Delete, SetPos+InsertText(줄바꿈은 \r\n — 문단 나눔)로.
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
          exec('InsertText', {Text: ins.replace(/\n/g, '\r\n')});   // \r\n 은 문단 나눔(실측) — 한 번에 넣어 다시 배치도 한 번
        }
        return true;
      } catch (e) { return false; }
    }
    // 양식 보호 모드(EditMode 2)에서 「양식 모드에서 편집 가능」 속성(1)을 켜고 끈다 — 끈 칸은 입력이 들어가지 않는다
    // 처음(모름)은 편집 가능으로 본다 — 필드는 「양식 모드에서 편집 가능」 으로 만들어져 있어, 열 때 942칸 모두에 속성을 다시 쓰던 0.5초를 뺀다.
    // 지금 커서 칸은 부르는 쪽이 force 로 다시 맞춘다
    function setEditable(name, on, force) {
      on = !!on;
      if ((editable[name] === on || (editable[name] === undefined && on)) && !force) return;
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
    // 화면만 위아래로 굴리기(커서는 그대로) — 따라가기가 쓴다. 세로 스크롤 값 1 = 화면 1/75px(실측, 위 restoreCursor 와 같은 값). 양수면 아래로
    function scrollBy(dy) {
      if (!dy || Math.abs(dy) < 1) return false;
      try {
        var s = ctrl.ScrollPosInfo, m = s.m_Set.m_SetItemMap;
        if (typeof m['28675'] === 'number') { m['28675'] = Math.max(0, m['28675'] + dy * 75); ctrl.ScrollPosInfo = s; return true; }
      } catch (e) {}
      try { var v = doc().getElementById('hcwoViewScroll'); if (v) { v.scrollTop += dy; return true; } } catch (e) {}   // 내부 값이 없으면(제품 판이 바뀜) 화면 막대로
      return false;
    }
    var undoLocked = false;
    // 남의 고침을 넣으면 기안기 되돌리기 기록에 들어가 내 Ctrl+Z 가 남의 글을 지웠다(실측) — 넣은 뒤에는 되돌리기를 잠그고, 내가 고치면 푼다
    function lockUndo(on) {
      on = !!on;
      if (undoLocked === on) return;
      try { ctrl.LockCommand('Undo', on); ctrl.LockCommand('Redo', on); undoLocked = on; } catch (e) {}
    }
    // 내 커서가 있는 쪽(1부터) — 기안기 KeyIndicator, 칸 자리 재기 없이 바로. 같이 있는 사람 목록에 「N쪽」 으로
    function caretPage() { try { var k = ctrl.KeyIndicator(); return k && k.result ? (k.prnpageno || 0) : 0; } catch (e) { return 0; } }
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
      // 툴바 관문 — 공동 편집 층이 정한 판단(toolbarGuard)이 참이면 기안기 서식 단추·목록([data-command])의 누름을 통째로 막는다(누름·뗌·클릭 모두)
      var tbBlocked = false;
      ['pointerdown', 'mousedown', 'mouseup', 'click'].forEach(function (t) {
        d.addEventListener(t, function (e) {
          var cmd = e.target && e.target.closest && e.target.closest('[data-command]');
          if (!cmd || !toolbarGuard) return;
          if (t === 'pointerdown') tbBlocked = !!toolbarGuard(e);
          if (tbBlocked) { e.preventDefault(); e.stopImmediatePropagation(); }
        }, true);
      });
      d.addEventListener('pointerdown', function (e) { abortProbe(); lastInput = Date.now(); fire('activity', e); }, true);
      // 글자 크기 칸 화살표 — 늘 글자마다 지금 크기에서 1pt 씩(기안기 CharShapeHeightIncrease/Decrease). 기안기 화살표는 칸에 보이는 수(크기가 섞이면
      // 비거나 첫 글자 크기)에서 세어 고른 글을 모두 한 크기로 맞췄다 — 섞인 크기가 0 부터·한 크기로 바뀌었다(10-26 녹화). 크기가 하나면 결과는 같다.
      // 수를 쳐 넣는 것은 그대로 「그 크기로 맞추기」
      var sizeArrow = function (e) {
        var t = e.target, up = t && t.closest && t.closest('.font_size .spinner_upper_arrow'), dn = !up && t && t.closest && t.closest('.font_size .spinner_lower_arrow');
        if (!up && !dn) return;
        e.preventDefault(); e.stopImmediatePropagation();
        if (e.type === 'pointerdown') { try { ctrl.Run(up ? 'CharShapeHeightIncrease' : 'CharShapeHeightDecrease'); } catch (x) {} fire('activity', {type: 'pointerdown', target: t}); }
      };
      ['pointerdown', 'mousedown', 'mouseup', 'click'].forEach(function (t) { d.addEventListener(t, sizeArrow, true); });
      d.addEventListener('compositionstart', function () { abortProbe(); composing = true; lastInput = Date.now(); }, true);
      d.addEventListener('compositionupdate', function () { composing = true; lastInput = Date.now(); fire('edit', {composing: true}); }, true);
      d.addEventListener('compositionend', function () { composing = false; lastInput = Date.now(); fire('edit', {composing: false}); }, true);
      // 글자 하나가 확정된 뒤 — 거품 단계(기안기가 그 글자를 문서에 넣은 다음)라 이때는 칸을 읽어도 조합이 끊기지 않는다.
      // 한글은 다음 글자를 치는 순간 새 조합이 시작돼 「조합 중」 이 쉬지 않고 이어지므로, 확정 글을 여기서 바로 보내야 늦지 않는다
      d.addEventListener('compositionend', function () { fire('commit', {}); }, false);
      ['input', 'paste', 'cut', 'drop'].forEach(function (t) {
        d.addEventListener(t, function () { lastInput = Date.now(); fire('edit', {composing: composing}); }, true);
      });
      d.addEventListener('keyup', function (e) {
        var k = e.key || '';
        if (k === 'Backspace' || k === 'Delete' || k === 'Enter' || k === 'Tab' || ((k.length === 1 || k === 'Process') && !e.ctrlKey && !e.metaKey)) fire('edit', {composing: composing});
        else if ((e.ctrlKey || e.metaKey) && /^[vVxXzZyY]$/.test(k)) { lastInput = Date.now(); fire('edit', {composing: composing}); }   // 붙여넣기·잘라내기·되돌리기 — 기안기는 문서 안 복사를 자체 클립보드로 해 paste 사건이 오지 않는다
        fire('caret', e);
      }, true);
      d.addEventListener('pointerup', function (e) { setTimeout(function () { fire('caret', e); }, 0); }, true);
      d.addEventListener('wheel', function (e) { fire('activity', e); }, {capture: true, passive: true});
      d.addEventListener('scroll', function (e) { fire('activity', e); }, true);
      // 스크롤 방향 고정 — 트랙패드로 위아래를 쓸면 손가락이 조금만 비스듬해도 좌우 움직임이 섞이고, 관성 스크롤로 쌓여 문서가 옆으로 밀렸다
      // (쪽이 칸보다 조금 넓거나 가로로 긴 쪽이 낀 문서). 가로 스크롤을 막지 않고, 한 번 쓸기(휠 사건이 180ms 넘게 끊기기 전까지)의 첫 방향이
      // 위아래면 그 쓸기 동안만 좌우 흔들림을 되돌린다. 옆으로 쓸기·Shift+휠·가로 막대 끌기·커서 따라 옮기기는 그대로다(맥 기본 앱과 같은 방식)
      var gest = {axis: '', at: 0, left: 0};
      d.addEventListener('wheel', function (e) {
        var now = Date.now(), v = d.getElementById('hcwoViewScroll');
        if (now - gest.at > 180) {
          gest.axis = (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY) * 1.2) ? 'x' : 'y';
          gest.left = v ? v.scrollLeft : 0;
        }
        gest.at = now;
      }, {capture: true, passive: true});
      d.addEventListener('scroll', function (e) {
        var v = e.target;
        if (!v || v.id !== 'hcwoViewScroll' || gest.axis !== 'y' || Date.now() - gest.at > 400) return;
        if (v.scrollLeft !== gest.left) v.scrollLeft = gest.left;
      }, true);
      return true;
    }

    // 절 필드를 서식째(기안기 JSON 블록) 꺼내고 넣기 — 시험 기능(M3). 커서는 되돌린다
    // 선택 영역 — 칸을 꺼내고 넣는 동안 커서뿐 아니라 사용자가 잡아 둔 선택(굵게 하려고 고른 글)도 되살린다(10-26 서식 실시간)
    function keepSel() { try { return ctrl.SelectionMode ? ctrl.GetSelectedPos() : null; } catch (e) { return null; } }
    function restoreSel(sel) {
      if (!sel) return;
      try { ctrl.SetPos(sel.slist, sel.spara, sel.spos); ctrl.SelectText(sel.spara, sel.spos, sel.epara, sel.epos); } catch (e) {}
    }
    // ── 일반 편집 모드(10-26) — 양식 모드가 해 주던 일을 대신 ──
    // 줄 끝·문단 앞을 누르면 커서가 칸 바깥(칸 끝 표시 뒤·칸 시작 표시 앞)에 선다. 같은 문단 안에서 한 칸 옆으로 옮겨 칸 안이면 그리로
    function before(a, b) { return a.para < b.para || (a.para === b.para && a.pos < b.pos); }
    function snapIntoField() {
      try { if (ctrl.SelectionMode) return currentField(); } catch (e) {}          // 고르는 중(Shift+화살표·끌기)에는 옮기지 않는다
      var p0; try { p0 = ctrl.GetPos(); } catch (e) { return ''; }
      var f = currentField();
      if (f) {
        // 칸 이름이 나와도 칸 시작 표시 앞·끝 표시 뒤일 수 있다(빈 줄 왼쪽 끝을 누르면 시작 표시 앞 — 친 글이 칸 밖에 들어갔다, 10-26 실측).
        // 그 칸의 처음·끝 자리와 견줘 밖이면 안쪽 끝으로 옮긴다
        try {
          ctrl.MoveToField(f, true, true, false); var s0 = ctrl.GetPos();
          ctrl.MoveToField(f, true, false, false); var e0 = ctrl.GetPos();
          if (s0.list === p0.list && before(p0, s0)) ctrl.SetPos(s0.list, s0.para, s0.pos);
          else if (e0.list === p0.list && before(e0, p0)) ctrl.SetPos(e0.list, e0.para, e0.pos);
          else ctrl.SetPos(p0.list, p0.para, p0.pos);
        } catch (e) { try { ctrl.SetPos(p0.list, p0.para, p0.pos); } catch (x) {} }
        return f;
      }
      var ways = ['MoveLeft', 'MoveRight'];
      for (var i = 0; i < ways.length; i++) {
        try {
          ctrl.SetPos(p0.list, p0.para, p0.pos); ctrl.Run(ways[i]);
          var p1 = ctrl.GetPos();
          if (p1.list === p0.list && p1.para === p0.para) { f = ctrl.GetCurFieldName(0) || ''; if (f) return f; }
        } catch (e) {}
      }
      try { ctrl.SetPos(p0.list, p0.para, p0.pos); } catch (e) {}
      return '';
    }
    // 커서가 칸 글의 몇 번째 글자 앞에 있는지 — {field, at, len}. 칸 처음에서 Backspace·칸 끝에서 Delete 는 칸 표시를 지우므로 막는 데 쓴다
    function caretInField() {
      var c = getCursor(); if (!c || !c.rel) return null;
      var t = read(c.rel.field) || '', lines = t.split('\n'), at = 0;
      for (var i = 0; i < c.rel.dp && i < lines.length; i++) at += lines[i].length + 1;
      at += c.rel.pos;
      return {field: c.rel.field, at: at, len: t.length};
    }
    // 선택 영역 양 끝이 든 칸 — [시작 칸, 끝 칸] 또는 null(선택 없음). 선택은 되살린다
    function selectionFields() {
      var sel = keepSel(); if (!sel) return null;
      var a = '', b = '';
      try {
        // 끝이 칸 사이(칸 끝 표시 뒤·시작 표시 앞)에 걸리면 이름이 비어 나온다 — 시작은 한 칸 오른쪽, 끝은 한 칸 왼쪽(선택 안쪽)으로 옮겨 다시 묻는다
        ctrl.SetPos(sel.slist, sel.spara, sel.spos); a = ctrl.GetCurFieldName(0) || '';
        if (!a) { ctrl.Run('MoveRight'); a = ctrl.GetCurFieldName(0) || ''; }
        ctrl.SetPos(sel.elist, sel.epara, sel.epos); b = ctrl.GetCurFieldName(0) || '';
        if (!b) { ctrl.Run('MoveLeft'); b = ctrl.GetCurFieldName(0) || ''; }
      } catch (e) {}
      restoreSel(sel);
      return [a, b];
    }
    var toolbarGuard = null;
    function exportField(name) {
      return new Promise(function (resolve) {
        var keep = getCursor(true), sel = keepSel();
        var back = function () { restoreCursor(keep); restoreSel(sel); };
        try { ctrl.MoveToField(name, true, true, true); } catch (e) { resolve(null); return; }
        try { ctrl.GetTextFile('JSON', 'saveblock', function (d) { back(); resolve(typeof d === 'string' ? d : null); }); }
        catch (e) { back(); resolve(null); }
      });
    }
    // 서식째 넣기 — 기안기 JSON 블록(시험 기능) 또는 서버 참여자(AURA·논문 작성기)가 보낸 hwpx 조각 {"fmt":"hwpx","b64":…}.
    // hwpx 조각은 SetTextFile(base64, 'HWPX', 'insertfile') 로 그 칸 내용을 바꾼다(표·꾸밈 스타일·캡션째, 다시 넣으면 바뀜 — ADR-0072 실측)
    function importField(name, json) {
      var fmt = 'JSON', body = json;
      try { var j = typeof json === 'string' && json.charAt(0) === '{' && json.indexOf('"fmt"') > 0 ? JSON.parse(json) : null; if (j && j.fmt === 'hwpx' && j.b64) { fmt = 'HWPX'; body = j.b64; } } catch (e) {}
      return new Promise(function (resolve) {
        var keep = getCursor(), sel = keepSel(), t = setTimeout(function () { restoreCursor(keep); restoreSel(sel); resolve(false); }, 8000);
        var relock = editable[name] === false;
        if (relock) setEditable(name, true, true);                // 남이 쥔 칸은 잠겨 있다 — 넣는 동안만 푼다
        var done = function (ok) { clearTimeout(t); if (relock) setEditable(name, false, true); restoreCursor(keep); restoreSel(sel); resolve(ok); };
        try { ctrl.MoveToField(name, true, true, true); } catch (e) { done(false); return; }
        try { ctrl.SetTextFile(body, fmt, 'insertfile', function (r) { done(!(r && r.result === false)); }); }
        catch (e) { done(false); }
      });
    }
    function isHwpxRich(json) { return typeof json === 'string' && /^\{"fmt":\s*"hwpx"/.test(json); }
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
      setToolbarGuard: function (fn) { toolbarGuard = fn; }, setFormMode: setFormMode, formMode: formMode, snapIntoField: snapIntoField, caretInField: caretInField, selectionFields: selectionFields, setReadOnly: setReadOnly, currentField: currentField,
      getCursor: getCursor, restoreCursor: restoreCursor, lockUndo: lockUndo, caretPage: caretPage, undoLocked: function () { return undoLocked; }, moveTo: moveTo, caretRect: caretRect, scrollBy: scrollBy,
      fieldGeom: fieldGeom, toScreen: toScreen, bodyLeft: bodyLeft, unitPx: unitPx, geomReady: geomReady,
      onLocalEdit: function (cb) { on('edit', cb); }, onCaret: function (cb) { on('caret', cb); }, onActivity: function (cb) { on('activity', cb); }, onCommit: function (cb) { on('commit', cb); },
      attach: attach, isProbing: function () { return !!probing; },
      isComposing: function () { return composing && Date.now() - lastInput < 3000; },     // 조합 끝 사건을 놓쳐도 3초 뒤엔 풀린다
      lastInputAt: function () { return lastInput; }, exportField: exportField, importField: importField, isHwpxRich: isHwpxRich, open: open, hwpx: hwpx,
      run: run, exec: exec, insertText: function (t) { return exec('InsertText', {Text: String(t)}); },
      resetEditable: function () { editable = {}; },
      inspect: inspect, findNth: findNth, insertPicture: insertPicture, inTable: inTable
    };
  }
  window.AuraHwpWrapper = {create: create, nl: nl};
})();
