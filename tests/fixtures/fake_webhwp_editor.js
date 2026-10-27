/* 시험용 가짜 웹 한글 기안기(WebHwpCtrl) — tests/coedit_view_browser.mjs 가 쓴다. 실제 기안기 서버 없이 어댑터(webhwp-wrapper.js)·공동 편집 층이
 * 부르는 API 만 흉내 낸다. 실측(ADR-0070)으로 알려진 기안기 성질을 그대로 둔다:
 *  - 글은 쪽으로 나뉘어 배치된다(쪽 높이·여백·줄 높이·한 줄 글자 수 고정, 배율 ZOOM).
 *  - 세로 스크롤은 두 곳: 화면 막대(#hcwoViewScroll.scrollTop, px)와 내부 값(ScrollPosInfo 28675, HWPUNIT = px × 75 / 배율).
 *    화면 막대가 굴면(scroll 사건) 내부 값을 따라 맞추고, 내부 값을 넣으면 화면 막대를 바로 옮긴다.
 *  - CalcCaretPos(쪽 좌표 → 종이 화면 px)는 내부 값으로 센다.
 *  - API 로 커서를 옮기면(SetPos·MoveToField·글 넣기·지우기) 다음 장면(rAF)에서 커서가 화면 밖이면 화면을 커서 쪽으로 끌어온다.
 */
(function () {
  'use strict';
  var Q = new URLSearchParams(location.search);
  var ZOOM = +(Q.get('zoom') || 0.7), K = ZOOM * 96 / 7200;          // HWPUNIT → px
  var PAGE_H = 84188, TOP = 5668, BODY_H = 72000, LH = 2400, CPL = 45, LEFT = 8504, CW = 900, GAP = 10;
  var paras = [], caret = {list: 0, para: 0, pos: 0}, sel = null, S = 0, lay = null, editMode = 1;
  var view = document.getElementById('hcwoViewScroll'), spacer = document.getElementById('spacer');

  function load(doc) {               // doc = [[칸 이름, [문단 글…]], …]
    paras = [];
    doc.forEach(function (f) { f[1].forEach(function (t) { paras.push({text: t, field: f[0]}); }); });
    relayout();
  }
  function relayout() {
    var page = 1, y = TOP, out = [];
    paras.forEach(function (p) {
      var n = Math.max(1, Math.ceil(p.text.length / CPL)), lines = [];
      for (var i = 0; i < n; i++) {
        if (y + LH > TOP + BODY_H) { page++; y = TOP; }
        lines.push({page: page, y: y}); y += LH;
      }
      out.push(lines);
    });
    lay = out;
    spacer.style.height = Math.ceil(pageTop(page + 1) + 200) + 'px';
  }
  function pageTop(p) { return (p - 1) * (PAGE_H * K + GAP); }
  function at(pos) {                  // 자리 → 쪽 좌표
    var L = lay[pos.para] || lay[lay.length - 1], li = Math.min(L.length - 1, Math.floor(pos.pos / CPL));
    return {page: L[li].page, x: LEFT + (pos.pos % CPL) * 1000, y: L[li].y, h: LH};
  }
  function docPx(page, y) { return pageTop(page) + y * K; }
  function fieldRange(name) {
    var a = -1, b = -1;
    paras.forEach(function (p, i) { if (p.field === name) { if (a < 0) a = i; b = i; } });
    return a < 0 ? null : [a, b];
  }
  function names() { var seen = {}, out = []; paras.forEach(function (p) { if (p.field && !seen[p.field]) { seen[p.field] = 1; out.push(p.field); } }); return out; }
  function fieldText(name) { var r = fieldRange(name); if (!r) return ''; return paras.slice(r[0], r[1] + 1).map(function (p) { return p.text; }).join('\r\n'); }

  // 화면 막대 ↔ 내부 값
  view.addEventListener('scroll', function () { S = Math.round(view.scrollTop / K); });
  var revealQ = false;
  function moved() {
    if (revealQ) return;
    revealQ = true;
    requestAnimationFrame(function () {
      revealQ = false;
      var c = at(caret), cy = docPx(c.page, c.y), st = view.scrollTop, vh = view.clientHeight;
      if (cy < st || cy + LH * K > st + vh) view.scrollTop = Math.max(0, cy - vh * 0.3);
    });
  }
  function insert(text) {             // 커서 자리에 글(\r\n 은 문단 나눔)
    var p = paras[caret.para], parts = text.split('\r\n'), head = p.text.slice(0, caret.pos), tail = p.text.slice(caret.pos);
    if (parts.length === 1) { p.text = head + parts[0] + tail; caret.pos += parts[0].length; }
    else {
      p.text = head + parts[0];
      var add = parts.slice(1).map(function (t) { return {text: t, field: p.field}; });
      add[add.length - 1].text += tail;
      paras.splice.apply(paras, [caret.para + 1, 0].concat(add));
      caret = {list: 0, para: caret.para + add.length, pos: parts[parts.length - 1].length};
    }
    relayout();
  }
  function del(a, b) {                // a·b = 자리(a 앞), 같은 칸 안
    var pa = paras[a.para], pb = paras[b.para];
    pa.text = pa.text.slice(0, a.pos) + pb.text.slice(b.pos);
    paras.splice(a.para + 1, b.para - a.para);
    caret = {list: 0, para: a.para, pos: a.pos};
    relayout();
  }
  function action(name) {
    var items = {};
    var set = {SetItem: function (k, v) { items[k] = v; }, Item: function (k) { return items[k]; }, CreateItemSet: function () { return set; }};
    return {CreateSet: function () { return set; }, GetDefault: function () {}, Execute: function () { if (name === 'InsertText') { insert(String(items.Text || '')); moved(); return true; } return false; }};
  }
  var ctrl = {
    GetFieldList: function () { return names().join('\u0002'); },
    GetFieldText: function (n) { return String(n).split('\u0002').map(fieldText).join('\u0002'); },
    PutFieldText: function (n, t) {
      var r = fieldRange(n); if (!r) return false;
      var add = String(t).split('\r\n').map(function (x) { return {text: x, field: n}; });
      paras.splice.apply(paras, [r[0], r[1] - r[0] + 1].concat(add));
      if (caret.para > r[1]) caret.para += add.length - (r[1] - r[0] + 1);
      else if (caret.para >= r[0]) caret = {list: 0, para: r[0], pos: 0};
      relayout(); return true;
    },
    MoveToField: function (n, text, start) {
      var r = fieldRange(n); if (!r) return false;
      caret = start ? {list: 0, para: r[0], pos: 0} : {list: 0, para: r[1], pos: paras[r[1]].text.length};
      sel = null; moved(); return true;
    },
    GetCurFieldName: function () { var p = paras[caret.para]; return p ? p.field : ''; },
    GetPos: function () { return {list: caret.list, para: caret.para, pos: caret.pos}; },
    SetPos: function (list, para, pos) {
      if (list !== 0 || para < 0 || para >= paras.length) return false;
      caret = {list: 0, para: para, pos: Math.max(0, Math.min(pos, paras[para].text.length))}; sel = null; moved(); return true;
    },
    SelectText: function (sp, spos, ep, epos) { sel = {a: {para: sp, pos: spos}, b: {para: ep, pos: epos}}; return true; },
    Run: function (a) { if (a === 'Delete' && sel) { del(sel.a, sel.b); sel = null; moved(); return true; } return true; },
    CreateAction: action,
    ModifyFieldProperties: function () {}, LockCommand: function () {},
    KeyIndicator: function () { return {result: true, prnpageno: at(caret).page}; },
    GetSelectedPos: function () { return null; }, SelectionMode: 0,
    GetTextFile: function () { return ''; },
    get EditMode() { return editMode; }, set EditMode(v) { editMode = v; },
    ReadOnlyMode: false,
    get ScrollPosInfo() { var m = {'28675': S, '28674': 0}; return {m_Set: {m_SetItemMap: m}}; },
    set ScrollPosInfo(s) { var v = s.m_Set.m_SetItemMap['28675']; if (typeof v === 'number') { S = v; view.scrollTop = S * K; } }
  };
  window.HwpApp = {
    hwpCaret: {KNi: function () { return at(caret); }},
    hxe: {CalcCaretPos: function (page, x, y) { return {x: x * K, y: docPx(page, y) - S * K}; }, GetCurrentZoom: function () { return ZOOM; }}
  };
  // ── 시험이 쓰는 손(사람이 치는 것처럼 — 문서를 고치고 입력 사건을 낸다, API 가 아니라 끌어오기 없음) ──
  function key(type, k) { document.dispatchEvent(new KeyboardEvent(type, {key: k, bubbles: true, cancelable: true})); }
  window.fake = {
    ctrl: ctrl, load: load,
    put: function (field, dp, pos) { var r = fieldRange(field); caret = {list: 0, para: r[0] + dp, pos: pos}; },
    endOf: function (field, dp) { var r = fieldRange(field); caret = {list: 0, para: r[0] + dp, pos: paras[r[0] + dp].text.length}; },
    type: function (ch) {
      key('keydown', ch);
      if (ch === 'Enter') insert('\r\n');
      else if (ch === 'Backspace') {
        if (caret.pos > 0) del({para: caret.para, pos: caret.pos - 1}, caret);
        else if (caret.para > 0 && paras[caret.para - 1].field === paras[caret.para].field) del({para: caret.para - 1, pos: paras[caret.para - 1].text.length}, caret);
      } else insert(ch);
      key('keyup', ch);
    },
    caretY: function () { var c = at(caret); return docPx(c.page, c.y) - view.scrollTop; },             // 종이 칸 안 화면 높이(px)
    paraY: function (field, dp) { var r = fieldRange(field), L = lay[r[0] + (dp || 0)][0]; return docPx(L.page, L.y) - view.scrollTop; },
    scrollTo: function (px) { view.scrollTop = px; S = Math.round(px / K); },
    top: function () { return view.scrollTop; },
    field: fieldText
  };
})();
