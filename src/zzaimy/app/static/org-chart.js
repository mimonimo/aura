/* 조직 관리(/team) — 왼쪽 부서 트리(맨 위 「전체 사용자」, 세로 들여쓰기·찾기·접기·끌어 옮기기), 오른쪽 고른 것의 상세.
 *   부서: 「구성원」(체크 → 작업 줄: 옮기기·등급 바꾸기·빼기, 사람 추가, 줄 끝 … 메뉴) · 「부서 설정」(총괄 책임자·문서 수·최소 등급·
 *         이름 바꾸기·상위 부서 옮기기·하위 부서 추가·지우기·부서 드라이브)
 *   전체 사용자(관리자): 계정 목록(찾기·역할·부서·사용 여부 거르기·쪽 나누기, 체크 → 역할·부서 옮기기·사용 중지·다시 사용, 계정 추가·편집)
 * 자료: GET /api/org · /api/org/members · /api/org/accounts. 고치기: /org/move·rename·delete·create·head, POST /api/org/members,
 *       /dev/users/bulk, /dev/rag/min-grade, /org/rag-lend. 단추는 서버가 준 권한(can_edit·can_hold·team·admin)대로만 보이고 서버가 매번 다시 본다.
 * 확인창은 공통(window.platformConfirm), 입력 창은 같은 꼴(confirm-dialog)의 formDialog 하나. 글자는 모두 textContent 로 넣는다.
 */
(function () {
  var root = document.getElementById('orgAdmin');
  if (!root) return;
  var OPEN_KEY = 'om.open', PAGE = 20;
  var tree = root.querySelector('[data-om-tree]'), main = root.querySelector('[data-om-main]');
  var findBox = root.querySelector('[data-om-find]'), addTop = root.querySelector('[data-org-add]'), topDrop = root.querySelector('[data-om-topdrop]');
  var canAccounts = !!root.getAttribute('data-accounts');
  var roles = {};
  try { roles = JSON.parse(root.getAttribute('data-roles') || '{}') || {}; } catch (e) { roles = {}; }
  var data = null, byName = {}, kids = {}, roots = [], words = [], aliasHits = [], dragName = '';
  var sel = {kind: '', name: ''}, tab = '';
  var open = {};
  try { open = JSON.parse(localStorage.getItem(OPEN_KEY) || '{}') || {}; } catch (e) { open = {}; }
  function saveOpen() { try { localStorage.setItem(OPEN_KEY, JSON.stringify(open)); } catch (e) {} }

  function h(tag, attrs, kids_) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k === 'text') el.textContent = v;
      else if (k === 'class') el.className = v;
      else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    });
    (kids_ || []).forEach(function (c) { if (c != null && c !== false) el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return el;
  }
  function icon(name) { return h('iconify-icon', {icon: name, 'aria-hidden': 'true'}); }

  // ------------------------------------------------------------ 서버
  function readJSON(r, fallback) {
    return r.json().catch(function () { return {}; }).then(function (j) {
      if (!r.ok || j.ok === false) throw new Error(j.msg || j.error || (typeof j.detail === 'string' ? j.detail : '') || fallback);
      return j;
    });
  }
  function getJSON(url) {
    return fetch(url, {headers: {Accept: 'application/json'}, credentials: 'same-origin', cache: 'no-store'})
      .then(function (r) { return readJSON(r, '불러오지 못했습니다 — 잠시 뒤 다시 해 주세요'); });
  }
  function postJSON(path, body) {
    return fetch(path, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', Accept: 'application/json'}, body: JSON.stringify(body)})
      .then(function (r) { return readJSON(r, '저장하지 못했습니다 — 잠시 뒤 다시 해 주세요'); });
  }
  function postForm(path, fields) {
    var body = new URLSearchParams();
    Object.keys(fields).forEach(function (k) { [].concat(fields[k]).forEach(function (v) { body.append(k, v); }); });
    return fetch(path, {method: 'POST', credentials: 'same-origin', headers: {Accept: 'application/json'}, body: body})
      .then(function (r) { return readJSON(r, '저장하지 못했습니다 — 잠시 뒤 다시 해 주세요'); });
  }
  var toastTimer = null;
  function toast(msg, undo, isErr) {
    var old = document.querySelector('.oc-toast'); if (old) old.remove();
    clearTimeout(toastTimer);
    var t = h('div', {class: 'oc-toast' + (isErr ? ' err' : ''), role: isErr ? 'alert' : 'status'}, [h('span', {text: msg})]);
    if (undo) t.appendChild(h('button', {type: 'button', text: '되돌리기', onclick: function () { t.remove(); undo(); }}));
    document.body.appendChild(t);
    function arm() { clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.remove(); }, undo ? 8000 : 4500); }
    t.addEventListener('mouseenter', function () { clearTimeout(toastTimer); });
    t.addEventListener('mouseleave', arm);
    arm();
  }
  function confirmBox(message, o) {
    if (window.platformConfirm) return window.platformConfirm(message, o);
    var yes = window.confirm(message);
    if (!yes) return Promise.resolve(false);
    var v = o && o.choices && o.choices.length ? o.choices[0].value : true;
    return o && o.run ? Promise.resolve(o.run(v)).then(function () { return v; }) : Promise.resolve(v);
  }

  function load() {
    return getJSON('/api/org').then(function (d) {
      data = d; byName = {}; kids = {}; roots = [];
      (d.nodes || []).forEach(function (n) { byName[n.name] = n; });
      (d.nodes || []).forEach(function (n) {
        if (n.parent && byName[n.parent]) (kids[n.parent] = kids[n.parent] || []).push(n.name); else roots.push(n.name);
      });
      Object.keys(kids).forEach(function (k) { kids[k].sort(cmp); });
      roots.sort(function (a, b) { return ((kids[b] || []).length > 0) - ((kids[a] || []).length > 0) || cmp(a, b); });
      if (addTop) addTop.hidden = !d.can_top;
      if (sel.kind === 'dept' && !byName[sel.name]) sel = {kind: '', name: ''};
      renderTree();
    }).catch(function (e) { tree.replaceChildren(h('p', {class: 'om-none', text: e.message})); });
  }
  function cmp(a, b) { return a.localeCompare(b, 'ko'); }
  function ancestors(name) {
    var out = [], seen = {}, cur = byName[name] && byName[name].parent;
    while (cur && byName[cur] && !seen[cur]) { out.unshift(cur); seen[cur] = 1; cur = byName[cur].parent; }
    return out;
  }
  function descendants(name) {
    var out = [], todo = (kids[name] || []).slice();
    while (todo.length) { var c = todo.shift(); if (out.indexOf(c) < 0) { out.push(c); todo = todo.concat(kids[c] || []); } }
    return out;
  }

  // ------------------------------------------------------------ 트리
  function matches(n) {
    if (!words.length) return false;
    if (aliasHits.indexOf(n.name) >= 0) return true;
    var hay = [n.name].concat(n.aliases || []).join(' ').toLowerCase();
    return words.every(function (w) { return hay.indexOf(w) >= 0; });
  }
  function visibleSet() {
    if (!words.length) return null;
    var vis = {};
    Object.keys(byName).forEach(function (k) {
      if (matches(byName[k])) { vis[k] = 2; ancestors(k).forEach(function (a) { if (!vis[a]) vis[a] = 1; }); }
    });
    return vis;
  }
  function nameEl(n) {
    var el = h('span', {class: 'om-name', title: n.aliases && n.aliases.length ? n.name + ' · 다른 이름: ' + n.aliases.join(', ') : n.name});
    var w = words.length ? words[0] : '', i = w ? n.name.toLowerCase().indexOf(w) : -1;
    if (i < 0) { el.textContent = n.name; return el; }
    el.append(n.name.slice(0, i), h('mark', {class: 'oc-hit', text: n.name.slice(i, i + w.length)}), n.name.slice(i + w.length));
    return el;
  }
  function renderTree() {
    if (!data) return;
    var vis = visibleSet(), out = [], focusName = document.activeElement && document.activeElement.getAttribute && document.activeElement.getAttribute('data-node');
    if (canAccounts) {
      out.push(h('div', {class: 'om-node om-all' + (sel.kind === 'all' ? ' on' : ''), role: 'treeitem', 'aria-level': '1', 'aria-selected': sel.kind === 'all' ? 'true' : 'false',
        tabindex: '-1', 'data-node': '*', onclick: function () { choose({kind: 'all'}); }},
        [icon('solar:users-group-rounded-linear'), h('span', {class: 'om-name', text: '전체 사용자'})]));
    }
    function walk(name, depth) {
      if (vis && !vis[name]) return;
      var n = byName[name], ks = kids[name] || [];
      var shown = vis ? ks.filter(function (k) { return vis[k]; }) : ks;
      var isOpen = shown.length && (vis ? true : !!open[name]);
      // 부서 줄은 AURA 커서가 이름으로 고른다 — 선택자 [data-aura="team-dept"](platform_guide 「부서 고르기」)
      var row = h('div', {class: 'om-node' + (sel.kind === 'dept' && sel.name === name ? ' on' : '') + (vis && vis[name] === 2 ? ' hit' : ''),
        role: 'treeitem', 'aria-level': String(depth + 1), 'aria-selected': sel.kind === 'dept' && sel.name === name ? 'true' : 'false',
        'aria-expanded': ks.length ? (isOpen ? 'true' : 'false') : null, tabindex: '-1', 'data-node': name, 'data-aura': 'team-dept', style: '--d:' + depth,
        draggable: n.can_edit ? 'true' : null});
      if (ks.length) {
        row.appendChild(h('button', {type: 'button', class: 'om-fold', 'aria-label': name + (isOpen ? ' 접기' : ' 펼치기'), tabindex: '-1',
          text: isOpen ? '－' : '＋', onclick: function (e) { e.stopPropagation(); toggle(name); }}));
      } else row.appendChild(h('span', {class: 'om-fold-sp', 'aria-hidden': 'true'}));
      row.append(nameEl(n), h('small', {class: 'om-n', text: String(n.n || 0)}));
      row.addEventListener('click', function () { choose({kind: 'dept', name: name}); });
      dragRow(row, n);
      out.push(row);
      if (isOpen) shown.forEach(function (k) { walk(k, depth + 1); });
    }
    roots.forEach(function (r) { walk(r, 0); });
    if (!roots.length) out.push(h('p', {class: 'om-none', text: '아직 부서가 없습니다'}));
    else if (vis && !Object.keys(vis).length) out.push(h('p', {class: 'om-none', text: '찾는 부서가 없습니다'}));
    tree.replaceChildren.apply(tree, out);
    var cur = tree.querySelector('.om-node.on') || tree.querySelector('.om-node');
    if (cur) cur.tabIndex = 0;
    if (focusName) { var fe = tree.querySelector('[data-node="' + CSS.escape(focusName) + '"]'); if (fe) { fe.tabIndex = 0; fe.focus({preventScroll: true}); } }
  }
  function toggle(name, force) {
    var want = force == null ? !open[name] : force;
    if (want) open[name] = 1; else delete open[name];
    saveOpen(); renderTree();
  }
  tree.addEventListener('keydown', function (e) {
    var rows = Array.prototype.slice.call(tree.querySelectorAll('.om-node')), cur = document.activeElement, i = rows.indexOf(cur);
    if (i < 0) return;
    var name = cur.getAttribute('data-node');
    function go(j) { if (rows[j]) { rows.forEach(function (r) { r.tabIndex = -1; }); rows[j].tabIndex = 0; rows[j].focus(); } }
    if (e.key === 'ArrowDown') { e.preventDefault(); go(i + 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); go(i - 1); }
    else if (e.key === 'ArrowRight' && name !== '*' && (kids[name] || []).length && !open[name]) { e.preventDefault(); toggle(name, true); }
    else if (e.key === 'ArrowLeft' && name !== '*') {
      e.preventDefault();
      if (open[name] && (kids[name] || []).length) toggle(name, false);
      else { var p = byName[name] && byName[name].parent; if (p) { var pe = tree.querySelector('[data-node="' + CSS.escape(p) + '"]'); if (pe) go(rows.indexOf(pe)); } }
    }
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); cur.click(); }
  });

  // 끌어 옮기기 — 고칠 수 있는 부서를 놓을 수 있는 부서(can_hold) 위로, 또는 맨 위 자리로
  function dragRow(row, n) {
    if (n.can_edit) {
      row.addEventListener('dragstart', function (e) {
        dragName = n.name; row.classList.add('lifted'); root.classList.add('dragging');
        if (data.can_top && n.parent) topDrop.hidden = false;
        try { e.dataTransfer.setData('text/plain', n.name); e.dataTransfer.effectAllowed = 'move'; } catch (x) {}
      });
      row.addEventListener('dragend', endDrag);
    }
    row.addEventListener('dragover', function (e) {
      if (!dragName || !n.can_hold || dragName === n.name || byName[dragName].parent === n.name || descendants(dragName).indexOf(n.name) >= 0) return;
      e.preventDefault(); try { e.dataTransfer.dropEffect = 'move'; } catch (x) {}
      row.classList.add('over');
    });
    row.addEventListener('dragleave', function () { row.classList.remove('over'); });
    row.addEventListener('drop', function (e) {
      e.preventDefault(); row.classList.remove('over');
      var name = dragName; endDrag();
      if (name && name !== n.name) move(name, n.name).catch(function () {});
    });
  }
  function endDrag() {
    dragName = ''; root.classList.remove('dragging'); topDrop.hidden = true;
    tree.querySelectorAll('.lifted, .over').forEach(function (x) { x.classList.remove('lifted'); x.classList.remove('over'); });
  }
  topDrop.addEventListener('dragover', function (e) { if (dragName) { e.preventDefault(); topDrop.classList.add('over'); } });
  topDrop.addEventListener('dragleave', function () { topDrop.classList.remove('over'); });
  topDrop.addEventListener('drop', function (e) { e.preventDefault(); topDrop.classList.remove('over'); var name = dragName; endDrag(); if (name) move(name, '').catch(function () {}); });

  function act(promise, undoOf) {
    return promise.then(function (j) {
      var ub = undoOf && j.undo;
      toast(j.msg || '저장했습니다', ub ? function () { postJSON(undoOf, j.undo).then(function (k) { toast(k.msg || '되돌렸습니다'); return load().then(renderMain); })
        .catch(function (x) { toast(x.message, null, true); }); } : null);
      return load().then(function () { renderMain(); return j; });
    });
  }
  function move(dept, parent) {
    return act(postJSON('/org/move', {dept: dept, parent: parent}), '/org/move').catch(function (e) { toast(e.message, null, true); throw e; });
  }

  // ------------------------------------------------------------ 고르기·주소
  function choose(s) {
    if (s.kind === 'dept') ancestors(s.name).forEach(function (a) { open[a] = 1; });
    if (s.kind !== sel.kind || s.name !== sel.name) tab = '';
    sel = s; saveOpen();
    try {
      var u = new URL(location.href);
      ['dept', 'view', 'tab', 'ok', 'err'].forEach(function (k) { u.searchParams.delete(k); });
      if (s.kind === 'dept') u.searchParams.set('dept', s.name); else if (s.kind === 'all') u.searchParams.set('view', 'all');
      history.replaceState(history.state, '', u.pathname + u.search);
    } catch (e) {}
    renderTree(); renderMain();
  }
  function renderMain() {
    closeMenus();
    if (sel.kind === 'all') return renderAccounts();
    if (sel.kind === 'dept' && byName[sel.name]) return renderDept(byName[sel.name]);
    main.replaceChildren(h('div', {class: 'om-empty'}, [h('p', {text: roots.length ? '왼쪽에서 부서를 고르세요' : '아직 부서가 없습니다'})]));
  }

  // ------------------------------------------------------------ 공용 부품 — … 메뉴, 작업 줄, 입력 창, 부서 고르기
  function closeMenus(except) {
    document.querySelectorAll('.row-menu-list:not([hidden])').forEach(function (m) { if (m !== except) { m.hidden = true; m.previousSibling.setAttribute('aria-expanded', 'false'); } });
  }
  document.addEventListener('mousedown', function (e) { if (!e.target.closest('.row-menu')) closeMenus(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeMenus(); });
  window.addEventListener('scroll', function () { closeMenus(); }, true);       // 띄운 메뉴가 줄과 떨어지지 않게 — 스크롤하면 닫는다
  window.addEventListener('resize', function () { closeMenus(); });
  function rowMenu(label, items) {
    var list = h('div', {class: 'row-menu-list', role: 'menu', hidden: true});
    var btn = h('button', {type: 'button', 'aria-label': label + ' 메뉴', 'aria-haspopup': 'menu', 'aria-expanded': 'false', text: '…'});
    items.filter(Boolean).forEach(function (it) {
      list.appendChild(h('button', {type: 'button', role: 'menuitem', class: it.danger ? 'danger-text' : null, disabled: it.disabled ? true : null, title: it.title || null,
        text: it.label, onclick: function () { list.hidden = true; btn.setAttribute('aria-expanded', 'false'); it.run(); }}));
    });
    btn.addEventListener('click', function (e) {
      var show = list.hidden;
      closeMenus(list);
      list.hidden = !show; btn.setAttribute('aria-expanded', show ? 'true' : 'false');
      if (show) {                                          // 표 상자(가로 스크롤)에 잘리지 않게 화면 기준 자리에 겹쳐 띄운다
        var b = btn.getBoundingClientRect(), hgt = list.offsetHeight;
        list.style.position = 'fixed';
        list.style.right = Math.max(8, document.documentElement.clientWidth - b.right) + 'px';
        list.style.top = (b.bottom + 4 + hgt > window.innerHeight - 8 ? Math.max(8, b.top - 4 - hgt) : b.bottom + 4) + 'px';
        if (!e.detail) { var f = list.querySelector('button:not([disabled])'); if (f) f.focus(); }   // 키보드로 열었을 때만 첫 항목에 초점
      }
    });
    return h('span', {class: 'row-menu'}, [btn, list]);
  }
  // 작업 줄 — 고른 것이 있으면 목록 위 같은 자리에 겹쳐 뜬다(목록이 밀리지 않게 자리는 늘 잡혀 있다)
  function bulkBar(unit, actions, onClear) {
    var n = h('span', {class: 'bulk-n'});
    var bar = h('div', {class: 'bulk-bar', role: 'toolbar', 'aria-label': '고른 것에 할 일', hidden: true}, [n]);
    actions.forEach(function (a) { bar.appendChild(h('button', {type: 'button', class: 'btn-ghost' + (a.danger ? ' danger-text' : ''), text: a.label, onclick: a.run})); });
    bar.append(h('span', {class: 'bulk-gap'}), h('button', {type: 'button', class: 'btn-ghost bulk-clear', text: '선택 해제', onclick: onClear}));
    return {el: bar, set: function (k) { n.textContent = k + unit + ' 선택'; bar.hidden = !k; }};
  }
  // 입력 창 — 공통 확인창과 같은 꼴(confirm-dialog). submit 은 Promise — 도는 동안 단추 잠금, 실패하면 창 안에 까닭
  function formDialog(title, build, okText, submit, danger) {
    var dlg = h('dialog', {class: 'confirm-dialog om-dlg', 'aria-label': title});
    var err = h('p', {class: 'confirm-error', role: 'alert', hidden: true});
    var ok = h('button', {type: 'submit', class: danger ? 'danger' : null, text: okText});
    var cancel = h('button', {type: 'button', class: 'secondary', text: '취소', onclick: function () { if (!busy) dlg.close(); }});
    var form = h('form', {method: 'dialog', class: 'om-form'}, [h('h2', {text: title})]);
    var busy = false;
    build(form);
    form.append(err, h('footer', {}, [cancel, ok]));
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      if (busy) return;
      busy = true; ok.disabled = cancel.disabled = true; err.hidden = true;
      var label = ok.textContent; ok.textContent = '처리 중…';
      Promise.resolve().then(submit).then(function () { busy = false; dlg.close(); })
        .catch(function (x) { busy = false; ok.disabled = cancel.disabled = false; ok.textContent = label; err.textContent = x && x.message || '처리하지 못했습니다'; err.hidden = false; });
    });
    dlg.addEventListener('cancel', function (e) { if (busy) e.preventDefault(); });
    dlg.addEventListener('click', function (e) { if (e.target === dlg && !busy) dlg.close(); });
    dlg.addEventListener('close', function () { dlg.remove(); });
    dlg.appendChild(form);
    document.body.appendChild(dlg);
    dlg.showModal();
    var first = form.querySelector('input:not([type=hidden]), select'); if (first) first.focus();
    return dlg;
  }
  // 부서 고르기 칸 — 찾기 + 트리 꼴 목록(들여쓰기). opts: {only: [허용 부서], except: [뺄 부서], none: '맨 위' 같은 빈 값 항목}
  function deptPicker(opts) {
    opts = opts || {};
    var q = h('input', {type: 'search', placeholder: '부서 이름으로 찾기', 'aria-label': '부서 찾기', autocomplete: 'off'});
    var list = h('div', {class: 'om-pick-list', role: 'listbox', 'aria-label': '부서'});
    var picked = null;
    function allowed(name) { return (!opts.only || opts.only.indexOf(name) >= 0) && (!opts.except || opts.except.indexOf(name) < 0); }
    function paint() {
      var w = q.value.trim().toLowerCase();
      list.replaceChildren();
      var items = [];
      if (opts.none != null && !w) items.push({value: '', label: opts.none, depth: 0});
      (function walk(names, depth) {
        names.forEach(function (name) {
          var n = byName[name];
          if (allowed(name) && (!w || (name + ' ' + (n.aliases || []).join(' ')).toLowerCase().indexOf(w) >= 0)) items.push({value: name, label: name, depth: w ? 0 : depth, sub: w ? ancestors(name).join(' › ') : ''});
          walk(kids[name] || [], depth + 1);
        });
      })(roots, 0);
      items.forEach(function (it) {
        var o = h('button', {type: 'button', role: 'option', class: 'om-pick-opt' + (picked === it.value ? ' on' : ''), style: '--d:' + it.depth,
          'aria-selected': picked === it.value ? 'true' : 'false', onclick: function () { picked = it.value; paint(); }},
          [h('span', {text: it.label}), it.sub ? h('small', {text: it.sub}) : null]);
        list.appendChild(o);
      });
      if (!items.length) list.appendChild(h('p', {class: 'om-none', text: '고를 수 있는 부서가 없습니다'}));
    }
    q.addEventListener('input', paint);
    q.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); var f = list.querySelector('.om-pick-opt'); if (f) f.click(); } });
    paint();
    return {el: h('div', {class: 'om-pick'}, [q, list]), value: function () { return picked; }};
  }
  function pickDept(title, okText, opts, run) {
    var dp = deptPicker(opts);
    formDialog(title, function (form) { form.appendChild(dp.el); }, okText, function () {
      if (dp.value() === null) throw new Error('목록에서 부서를 골라 주세요');
      return run(dp.value());
    });
  }

  // ------------------------------------------------------------ 부서 상세
  function renderDept(n) {
    var path = ancestors(n.name);
    var head = h('header', {class: 'om-head'}, [
      h('div', {class: 'om-title'}, [h('h2', {text: n.name}),
        h('p', {class: 'om-path', text: [path.length ? path.join(' › ') : '맨 위', n.kind].filter(Boolean).join(' · ')})]),
      h('p', {class: 'om-facts', text: '소속 ' + (n.n || 0) + '명' + (n.n_extra ? ' · 겸직 ' + n.n_extra + '명' : '') + ((kids[n.name] || []).length ? ' · 아래 부서 ' + kids[n.name].length + '개' : '')
        + (n.docs != null ? ' · 부서 RAG 문서 ' + n.docs + '건' : '')})]);
    var tabs = [];
    if (n.team) tabs.push(['members', '구성원']);
    tabs.push(['settings', n.can_edit || n.can_hold || data.admin ? '부서 설정' : '부서 정보']);
    if (!tab || !tabs.some(function (t) { return t[0] === tab; })) tab = tabs[0][0];
    var bar = h('div', {class: 'om-tabs', role: 'tablist', 'aria-label': n.name});
    var panel = h('div', {class: 'om-panel', role: 'tabpanel'});
    tabs.forEach(function (t) {
      bar.appendChild(h('button', {type: 'button', role: 'tab', class: 'om-tab', 'aria-selected': tab === t[0] ? 'true' : 'false', text: t[1],
        onclick: function () { if (tab !== t[0]) { tab = t[0]; renderMain(); } }}));
    });
    main.replaceChildren(head, bar, panel);
    if (tab === 'members') renderMembers(n, panel); else renderSettings(n, panel);
  }

  // 구성원
  function renderMembers(n, panel) {
    panel.replaceChildren(h('p', {class: 'om-none', text: '불러오는 중'}));
    getJSON('/api/org/members?dept=' + encodeURIComponent(n.name)).then(function (m) {
      if (!sel.name || sel.name !== n.name || tab !== 'members') return;
      var chosen = {}, q = '';
      var find = h('input', {type: 'search', placeholder: '이름·아이디로 찾기', 'aria-label': '구성원 찾기', autocomplete: 'off'});
      var tools = h('div', {class: 'control-bar'}, [find]);
      if (m.can_place) tools.appendChild(h('button', {type: 'button', class: 'btn-ghost', onclick: function () { addPeople(n, m); }},
        [icon('solar:user-plus-linear'), '사람 추가']));
      var acts = [];
      if (m.can_place) acts.push({label: '옮기기', run: function () { moveMembers(n, m, ids()); }});
      if (m.actor_grade !== 'lead') acts.push({label: '등급 바꾸기', run: function () { gradeMembers(n, m, ids()); }});
      if (m.can_place) acts.push({label: '빼기', danger: true, run: function () { removeMembers(n, m, ids()); }});
      var bb = bulkBar('명', acts, function () { chosen = {}; paint(); });
      var lt = h('div', {class: 'list-tools control-row'}, [tools, bb.el]);
      var all = h('input', {type: 'checkbox', 'aria-label': '보이는 사람 모두 고르기'});
      var body = h('tbody');
      var table = h('table', {class: 'om-table'}, [h('thead', {}, [h('tr', {}, [h('th', {class: 'pick-cell'}, [all]), h('th', {text: '사람'}),
        h('th', {text: '등급'}), h('th', {class: 'wide', text: '추가 권한'}), h('th', {})])]), body]);
      var none = h('p', {class: 'om-none', hidden: true});
      function ids() { return Object.keys(chosen); }
      function shown() {
        var ws = q.toLowerCase().split(/\s+/).filter(Boolean);
        return m.rows.filter(function (r) { var k = (r.name + ' ' + r.uid).toLowerCase(); return ws.every(function (w) { return k.indexOf(w) >= 0; }); });
      }
      function paint() {
        var rows = shown();
        body.replaceChildren.apply(body, rows.map(function (r) {
          var cb = h('input', {type: 'checkbox', 'aria-label': r.name + ' 고르기', disabled: r.editable || m.can_place ? null : true});
          cb.checked = !!chosen[r.uid];
          cb.addEventListener('change', function () { if (cb.checked) chosen[r.uid] = 1; else delete chosen[r.uid]; sync(); });
          var perms = h('td', {class: 'wide'}, r.perms.map(function (k) { return h('span', {class: 'om-tag', text: m.perms[k] || k}); }));
          var items = [
            r.grade_editable ? {label: '등급 바꾸기', run: function () { gradeMembers(n, m, [r.uid]); }} : null,
            r.editable ? {label: '추가 권한', run: function () { permsDialog(n, m, r); }} : null,
            m.can_place && r.primary ? {label: '다른 부서로 옮기기', run: function () { moveMembers(n, m, [r.uid]); }} : null,
            m.can_place ? {label: r.primary ? '이 부서에서 빼기' : '겸직 풀기', danger: true, disabled: r.primary && !m.can_primary,
              title: r.primary && !m.can_primary ? '소속을 비우는 것은 관리자가 합니다 — 다른 부서로 옮기세요' : '', run: function () { removeMembers(n, m, [r.uid]); }} : null];
          return h('tr', {'data-uid': r.uid}, [h('td', {class: 'pick-cell'}, [cb]),
            h('td', {}, [h('span', {class: 'om-who', text: r.name}), h('small', {class: 'om-sub', text: r.uid + (r.home ? ' · 겸직(소속 ' + r.home + ')' : '')})]),
            h('td', {class: 'om-nw', text: r.grade_label}), perms,
            h('td', {class: 'om-act'}, [items.some(Boolean) ? rowMenu(r.name, items) : null])]);
        }));
        none.hidden = !!rows.length;
        none.textContent = m.rows.length ? '찾는 사람이 없습니다' : '이 부서에 사용 중인 교직원 계정이 없습니다';
        sync();
      }
      function sync() {
        var vis = shown(), k = ids().length;
        all.checked = vis.length > 0 && vis.every(function (r) { return chosen[r.uid]; });
        all.indeterminate = !all.checked && vis.some(function (r) { return chosen[r.uid]; });
        bb.set(k);
      }
      all.addEventListener('change', function () { shown().forEach(function (r) { if (all.checked) chosen[r.uid] = 1; else delete chosen[r.uid]; }); paint(); });
      find.addEventListener('input', function () { q = find.value.trim(); paint(); });
      panel.replaceChildren(lt, h('div', {class: 'table-scroll'}, [table]), none);
      paint();
    }).catch(function (e) { panel.replaceChildren(h('p', {class: 'om-none', text: e.message})); });
  }
  function memberOp(n, op, uids, value) {
    return postJSON('/api/org/members', {dept: n.name, op: op, uids: uids, value: value}).then(function (j) {
      toast(j.msg); return load().then(renderMain);
    });
  }
  function moveMembers(n, m, uids) {
    if (!uids.length) return;
    pickDept(uids.length + '명 옮기기', '옮기기', {only: m.targets.length ? m.targets : null, except: [n.name]}, function (to) { return memberOp(n, 'move', uids, to); });
  }
  function gradeMembers(n, m, uids) {
    if (!uids.length) return;
    var choices = Object.keys(m.grades).map(function (k) { return {value: k, label: m.grades[k]}; });
    var one = uids.length === 1 ? m.rows.filter(function (r) { return r.uid === uids[0]; })[0] : null;
    confirmBox((one ? one.name + ' 님' : uids.length + '명') + '의 「' + n.name + '」 등급을 바꿉니다.', {title: '등급 바꾸기', ok: '바꾸기', choices: choices,
      value: one ? one.grade : undefined, busy: '바꾸는 중…',
      run: function (v) { return memberOp(n, 'grade', uids, v); }});
  }
  function removeMembers(n, m, uids) {
    if (!uids.length) return;
    confirmBox(uids.length + '명을 「' + n.name + '」 에서 뺍니다. 겸직이면 이 부서 겸직만 풀리고, 소속이면 소속 부서가 비워집니다.',
      {title: '구성원 빼기', ok: '빼기', danger: true, busy: '빼는 중…', run: function () { return memberOp(n, 'remove', uids); }});
  }
  function permsDialog(n, m, r) {
    var boxes = [];
    formDialog(r.name + ' 추가 권한', function (form) {
      var wrap = h('div', {class: 'confirm-choices'});
      Object.keys(m.perms).forEach(function (k) {
        var ok = m.grantable.indexOf(k) >= 0;
        var cb = h('input', {type: 'checkbox', value: k, disabled: ok ? null : true}); cb.checked = r.perms.indexOf(k) >= 0;
        boxes.push(cb);
        wrap.appendChild(h('label', {}, [cb, h('span', {}, [h('b', {text: m.perms[k]}), ok ? null : h('small', {text: '총괄 책임자가 정합니다'})])]));
      });
      form.appendChild(wrap);
    }, '저장', function () {
      return memberOp(n, 'perms', [r.uid], boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; }));
    });
  }
  function addPeople(n, m) {
    var picked = [], chips, mode = 'extra';
    formDialog('「' + n.name + '」 에 사람 추가', function (form) {
      var pp = h('div', {'data-people-picker': true, 'data-pass': true, 'data-purpose': 'share', 'data-placeholder': '이름·아이디·부서로 찾기'});
      chips = h('div', {class: 'om-chips'});
      form.append(pp, chips);
      if (m.can_primary) {
        var wrap = h('div', {class: 'confirm-choices', role: 'radiogroup', 'aria-label': '넣는 방식'});
        [['extra', '겸직으로', '소속 부서는 그대로 두고 이 부서 문서도 찾고 작업합니다'], ['primary', '소속으로 옮겨 오기', '지금 소속 부서에서 이 부서로 옮깁니다']].forEach(function (c, i) {
          var r = h('input', {type: 'radio', name: 'omAddMode', value: c[0]}); r.checked = !i;
          r.addEventListener('change', function () { mode = c[0]; });
          wrap.appendChild(h('label', {}, [r, h('span', {}, [h('b', {text: c[1]}), h('small', {text: c[2]})])]));
        });
        form.appendChild(wrap);
      } else form.appendChild(h('p', {class: 'om-note', text: '겸직으로 넣습니다 — 소속 부서는 그대로입니다'}));
      if (window.PeoplePicker) window.PeoplePicker.mount(pp);
      pp.ppTaken = function () { return picked.map(function (p) { return p.user; }).concat(m.rows.map(function (r) { return r.uid; })); };
      pp.addEventListener('pp:pick', function (e) {
        if (picked.some(function (p) { return p.user === e.detail.user; })) return;
        picked.push(e.detail); paintChips();
      });
      function paintChips() {
        chips.replaceChildren.apply(chips, picked.map(function (p, i) {
          return h('span', {class: 'oc-chip'}, [p.name || p.user, h('button', {type: 'button', 'aria-label': (p.name || p.user) + ' 빼기', text: '×',
            onclick: function () { picked.splice(i, 1); paintChips(); }})]);
        }));
      }
    }, '추가', function () {
      if (!picked.length) throw new Error('넣을 사람을 골라 주세요');
      return memberOp(n, 'add', picked.map(function (p) { return p.user; }), mode);
    });
  }

  // 부서 설정
  function renderSettings(n, panel) {
    var isRoot = !n.parent;
    var facts = h('dl', {class: 'oc-facts'});
    var headDd = h('dd', {class: 'od-heads'});
    (n.heads || []).forEach(function (p) {
      var c = h('span', {class: 'oc-chip head', title: p.extra ? '겸직' : ''}, [p.name]);
      if (data.admin && p.user) c.appendChild(h('button', {type: 'button', 'aria-label': p.name + ' 총괄 책임자에서 빼기', text: '×', onclick: function () {
        confirmBox(p.name + ' 님을 「' + n.name + '」 총괄 책임자에서 뺍니다.', {title: '총괄 책임자 빼기', ok: '빼기', danger: true, busy: '빼는 중…',
          run: function () { return act(postJSON('/org/head', {dept: n.name, user: p.user, on: false})); }});
      }}));
      headDd.appendChild(c);
    });
    if (!(n.heads || []).length) headDd.appendChild(h('span', {class: 'od-none', text: '없음'}));
    if (data.admin && isRoot) headDd.appendChild(h('button', {type: 'button', class: 'mini-btn', text: '총괄 책임자 정하기', onclick: function () { pickHead(n); }}));
    facts.append(h('dt', {text: '총괄 책임자'}), headDd);
    if ((n.leads || []).length) facts.append(h('dt', {text: '사업 책임자'}), h('dd', {text: n.leads.map(function (p) { return p.name; }).join(', ')}));
    if (n.docs != null) facts.append(h('dt', {text: '부서 RAG 문서'}), h('dd', {}, [h('a', {href: '/?type=all&scope=' + encodeURIComponent(n.name), text: n.docs + '건'})]));
    if (data.admin) {
      var sel_ = h('select', {'aria-label': '부서 RAG 최소 등급'});
      [['', '걸지 않음'], ['clerk', '행정 담당자 이상'], ['lead', '사업 책임자 이상'], ['head', '총괄 책임자 이상']].forEach(function (o) {
        var op = h('option', {value: o[0], text: o[1]}); op.selected = (n.min_grade || '') === o[0]; sel_.appendChild(op);
      });
      sel_.addEventListener('change', function () {
        sel_.disabled = true;
        postForm('/dev/rag/min-grade', {space: 'dept:' + n.name, grade: sel_.value}).then(function (j) { toast(j.msg); n.min_grade = sel_.value; })
          .catch(function (e) { toast(e.message, null, true); sel_.value = n.min_grade || ''; }).then(function () { sel_.disabled = false; });
      });
      facts.append(h('dt', {text: '최소 등급'}), h('dd', {}, [sel_]));
    }
    if (n.can_lend && window.PeoplePicker) {                // 다른 부서에 열기 — 관리자·이 부서 총괄 책임자(서버가 다시 본다, /org/rag-lend)
      var lend = h('div', {'data-people-picker': true, 'data-endpoint': '/org/rag-lend/targets/' + encodeURIComponent(n.name),
                           'data-placeholder': '부서 이름으로 찾기', 'aria-label': '열어 줄 부서'});
      var chipsOf = function (list) { return (list || []).map(function (d) { return {user: d, label: d, dept: '부서'}; }); };
      window.PeoplePicker.set(lend, chipsOf(n.lend_to));
      var lendBusy = false;
      lend.addEventListener('pp:change', function (e) {
        var want = e.detail.map(function (p) { return p.user; });
        if (lendBusy || want.join('\t') === (n.lend_to || []).join('\t')) return;
        lendBusy = true; lend.style.pointerEvents = 'none'; lend.style.opacity = '.6';
        postJSON('/org/rag-lend', {dept: n.name, to: want}).then(function (j) { n.lend_to = j.to; toast(j.msg); })
          .catch(function (x) { toast(x.message, null, true); })
          .then(function () { lendBusy = false; lend.style.pointerEvents = ''; lend.style.opacity = ''; window.PeoplePicker.set(lend, chipsOf(n.lend_to)); });
      });
      facts.append(h('dt', {text: '다른 부서에 열기'}), h('dd', {}, [lend]));
    }
    var acts = h('div', {class: 'om-acts'});
    function btn(label, fn, cls) { acts.appendChild(h('button', {type: 'button', class: 'btn-ghost' + (cls ? ' ' + cls : ''), text: label, onclick: fn})); }
    if (n.can_edit) btn('이름 바꾸기', function () { openRename(n); });
    if (n.can_edit) btn('상위 부서 옮기기', function () { openMove(n); });
    if (n.can_hold) btn('하위 부서 추가', function () { openCreate(n.name); });
    if (n.can_edit) btn('지우기', function () { openDelete(n); }, 'danger-text');
    var parts = [h('section', {class: 'om-sec'}, [h('h3', {text: '개요'}), facts])];
    if (acts.childNodes.length) parts.push(h('section', {class: 'om-sec'}, [h('h3', {text: '부서 고치기'}), acts]));
    var drive = h('div', {class: 'dd-card', 'data-dept-drive': true, 'data-dept': n.name, 'aria-live': 'polite'});
    var driveSec = h('section', {class: 'om-sec', hidden: true}, [drive]);
    parts.push(driveSec);
    panel.replaceChildren.apply(panel, parts);
    if (window.AuraDeptDrive) {                              // 볼 수 없는 부서면 상자가 스스로 숨는다(403)
      driveSec.hidden = false;
      window.AuraDeptDrive.mount(drive).then(function () { driveSec.hidden = drive.hidden; });
    }
  }
  function pickHead(n) {
    formDialog('「' + n.name + '」 총괄 책임자 정하기', function (form) {
      var pp = h('div', {'data-people-picker': true, 'data-pass': true, 'data-purpose': 'share', 'data-placeholder': '이름·아이디·부서로 찾기'});
      form.append(h('p', {class: 'om-note', text: '소속이 다르면 겸직으로 정합니다'}), pp);
      if (window.PeoplePicker) window.PeoplePicker.mount(pp);
      pp.addEventListener('pp:pick', function (e) {
        var dlg = form.closest('dialog'); dlg.close();
        act(postJSON('/org/head', {dept: n.name, user: e.detail.user, on: true})).catch(function (x) { toast(x.message, null, true); });
      });
    }, '닫기', function () {});
  }
  function openRename(n) {
    var input = h('input', {type: 'text', value: n.name, 'aria-label': '새 이름', maxlength: '30'});
    formDialog('이름 바꾸기', function (form) {
      form.append(h('label', {class: 'om-label'}, ['새 이름', input]), h('p', {class: 'om-note', text: '계정·문서·사업 관리의 부서 이름도 함께 바뀝니다'}));
      setTimeout(function () { input.select(); }, 0);
    }, '바꾸기', function () {
      var v = input.value.trim().replace(/\s+/g, ' ');
      if (!v) throw new Error('새 이름을 적어 주세요');
      if (v === n.name) return;
      return act(postJSON('/org/rename', {dept: n.name, name: v}), '/org/rename').then(function (j) { if (j.name) choose({kind: 'dept', name: j.name}); });
    });
  }
  function openMove(n) {
    var only = Object.keys(byName).filter(function (k) { return byName[k].can_hold; });
    pickDept('「' + n.name + '」 상위 부서 옮기기', '옮기기',
      {only: only, except: [n.name, n.parent].concat(descendants(n.name)), none: data.can_top && n.parent ? '맨 위(상위 없음)' : null},
      function (to) { return move(n.name, to); });
  }
  function deptSuggest(q) {
    return getJSON('/api/depts?limit=8&kind=creatable&q=' + encodeURIComponent(q)).then(function (d) { return d.items || []; }).catch(function () { return []; });
  }
  function openCreate(parent) {
    var input = h('input', {type: 'text', 'aria-label': '부서 이름', maxlength: '30', placeholder: '예: 학생복지팀', autocomplete: 'off', list: 'omDeptSuggest'});
    var dl = h('datalist', {id: 'omDeptSuggest'});
    var t = null;
    input.addEventListener('input', function () {
      clearTimeout(t); var q = input.value.trim(); if (!q) return;
      t = setTimeout(function () { deptSuggest(q).then(function (items) { dl.replaceChildren.apply(dl, items.map(function (it) { return h('option', {value: it.name, text: it.line && it.line !== it.name ? it.line : (it.kind_label || '')}); })); }); }, 160);
    });
    formDialog(parent ? '「' + parent + '」 아래에 부서 추가' : '맨 위에 부서 추가', function (form) {
      form.append(h('label', {class: 'om-label'}, ['부서 이름', input, dl]));
    }, '추가', function () {
      var name = input.value.trim();
      if (!name) throw new Error('부서 이름을 적어 주세요');
      if (parent) open[parent] = 1;
      return act(postJSON('/org/create', {name: name, parent: parent || ''})).then(function (j) { if (j.name) choose({kind: 'dept', name: j.name}); });
    });
  }
  function openDelete(n) {
    var ks = kids[n.name] || [], desc = descendants(n.name);
    var people = desc.filter(function (d) { return byName[d].n; });
    if (n.n) { toast('「' + n.name + '」 에 소속 ' + n.n + '명이 있어 지울 수 없습니다 — 먼저 사람을 다른 부서로 옮기세요', null, true); return; }
    var where = n.parent ? '「' + n.parent + '」 아래' : '맨 위';
    var msg = '「' + n.name + '」 을(를) 지웁니다. 부서 RAG 문서 ' + (n.docs || 0) + '건은 공통 RAG 로 돌아가고, 겸직은 풀립니다. 되돌릴 수 없습니다.';
    var o = {title: '부서 지우기', ok: '지우기', danger: true, busy: '지우는 중…', run: function (v) {
      return postJSON('/org/delete', {dept: n.name, kids: v === true ? 'lift' : v}).then(function (j) {
        toast(j.msg);
        var next = n.parent || '';
        sel = next ? {kind: 'dept', name: next} : {kind: canAccounts ? 'all' : '', name: ''};
        return load().then(function () { choose(sel); });
      });
    }};
    if (ks.length) o.choices = [
      {value: 'lift', label: '아래 부서 ' + ks.length + '개를 위로 올리기', hint: ks.slice(0, 3).join(', ') + (ks.length > 3 ? ' 등' : '') + ' — ' + where + '로 옮겨 그대로 둡니다'},
      {value: 'with', label: '아래 부서도 함께 지우기', hint: '아래 부서 ' + desc.length + '개를 모두 지웁니다' + (people.length ? ' · 사람이 있는 아래 부서 ' + people.length + '개가 있어 지금은 할 수 없습니다' : '')}];
    confirmBox(msg, o);
  }
  if (addTop) addTop.addEventListener('click', function () { openCreate(''); });

  // ------------------------------------------------------------ 전체 사용자(관리자)
  var acc = {rows: [], q: '', role: '', dept: '', state: 'on', page: 0, chosen: {}};
  function renderAccounts() {
    main.replaceChildren(h('p', {class: 'om-none', text: '불러오는 중'}));
    getJSON('/api/org/accounts').then(function (j) {
      if (sel.kind !== 'all') return;
      acc.rows = j.users || []; acc.me = j.me;
      Object.keys(acc.chosen).forEach(function (u) { if (!acc.rows.some(function (r) { return r.user === u; })) delete acc.chosen[u]; });
      paintAccounts();
    }).catch(function (e) { main.replaceChildren(h('p', {class: 'om-none', text: e.message})); });
  }
  function paintAccounts() {
    var on = acc.rows.filter(function (r) { return r.active; }).length;
    var head = h('header', {class: 'om-head'}, [h('div', {class: 'om-title'}, [h('h2', {text: '전체 사용자'}),
      h('p', {class: 'om-path', text: '계정 ' + acc.rows.length + '개 · 사용 중 ' + on + '명'})]),
      h('button', {type: 'button', onclick: function () { window.omUserEdit && window.omUserEdit(null); }}, [icon('solar:user-plus-linear'), '계정 추가'])]);
    var find = h('input', {type: 'search', placeholder: '이름·아이디 찾기', 'aria-label': '계정 찾기', autocomplete: 'off', value: acc.q});
    var role = h('select', {'aria-label': '역할로 거르기'}, [h('option', {value: '', text: '모든 역할'})]);
    var seen = {};
    acc.rows.forEach(function (r) { if (!seen[r.role]) { seen[r.role] = 1; role.appendChild(h('option', {value: r.role, text: r.role_label || r.role})); } });
    role.value = acc.role;
    var dept = h('select', {'aria-label': '부서로 거르기'}, [h('option', {value: '', text: '모든 부서'}), h('option', {value: '-', text: '부서 없음'})]);
    (function walk(names, depth) { names.forEach(function (nm) { dept.appendChild(h('option', {value: nm, text: '　'.repeat(depth) + nm})); walk(kids[nm] || [], depth + 1); }); })(roots, 0);
    dept.value = acc.dept;
    var state = h('select', {'aria-label': '사용 여부로 거르기'}, [h('option', {value: 'on', text: '사용 중'}), h('option', {value: 'off', text: '사용 중지'}), h('option', {value: '', text: '모두'})]);
    state.value = acc.state;
    var bb = bulkBar('명', [
      {label: '역할 바꾸기', run: function () { bulkRole(ids()); }},
      {label: '부서 옮기기', run: function () { bulkDept(ids()); }},
      {label: '사용 중지', danger: true, run: function () { bulkActive(ids(), false); }},
      {label: '다시 사용', run: function () { bulkActive(ids(), true); }}], function () { acc.chosen = {}; paint(); });
    var lt = h('div', {class: 'list-tools control-row'}, [h('div', {class: 'control-bar'}, [find, role, dept, state]), bb.el]);
    var all = h('input', {type: 'checkbox', 'aria-label': '이 쪽 모두 고르기'});
    var body = h('tbody');
    var table = h('table', {class: 'om-table'}, [h('thead', {}, [h('tr', {}, [h('th', {class: 'pick-cell'}, [all]), h('th', {text: '계정'}), h('th', {text: '역할'}),
      h('th', {text: '부서'}), h('th', {class: 'wide', text: '문서 작업 계정'}), h('th', {})])]), body]);
    var none = h('p', {class: 'om-none', hidden: true, text: '찾는 계정이 없습니다'});
    var pager = h('div', {class: 'pager'});
    function ids() { return Object.keys(acc.chosen); }
    function filtered() {
      var ws = acc.q.toLowerCase().split(/\s+/).filter(Boolean);
      return acc.rows.filter(function (r) {
        var k = (r.user + ' ' + (r.name || '')).toLowerCase();
        return ws.every(function (w) { return k.indexOf(w) >= 0; }) && (!acc.role || r.role === acc.role)
          && (!acc.dept || (acc.dept === '-' ? !r.dept : r.dept === acc.dept || (r.extra || []).indexOf(acc.dept) >= 0))
          && (!acc.state || (acc.state === 'on') === !!r.active);
      });
    }
    function paint() {
      var rows = filtered(), pages = Math.max(1, Math.ceil(rows.length / PAGE));
      acc.page = Math.min(acc.page, pages - 1);
      var part = rows.slice(acc.page * PAGE, acc.page * PAGE + PAGE);
      body.replaceChildren.apply(body, part.map(function (r) {
        var cb = h('input', {type: 'checkbox', 'aria-label': r.user + ' 고르기'}); cb.checked = !!acc.chosen[r.user];
        cb.addEventListener('change', function () { if (cb.checked) acc.chosen[r.user] = 1; else delete acc.chosen[r.user]; sync(part); });
        var who = h('td', {}, [h('button', {type: 'button', class: 'om-link', text: r.name || r.user, onclick: function () { window.omUserEdit(r); }}),
          h('small', {class: 'om-sub', text: r.user + (r.user === acc.me ? ' · 나' : '') + (r.active ? '' : ' · 사용 중지')})]);
        var roleTd = h('td', {}, [h('span', {class: 'om-nw', text: (r.role_label || r.role) + (r.grade_label ? ' · ' + r.grade_label : '')})].concat(
          [(r.perms || []).length ? h('span', {class: 'om-tag', text: '추가 권한 ' + r.perms.length}) : null]));
        var deptTd = h('td', {}, [r.dept ? h('span', {text: r.dept}) : h('span', {class: 'om-muted', text: '없음'}),
          (r.extra || []).length ? h('small', {class: 'om-sub', text: '겸직 ' + r.extra.join(', ')}) : null]);
        var gTd = h('td', {class: 'wide'}, [r.effective ? h('span', {text: r.effective}) : h('span', {class: 'om-muted', text: r.bound ? r.bound : '연결 전'}),
          h('small', {class: 'om-sub', text: r.effective ? r.via : (r.bound ? '재연결 필요' : '')})]);
        return h('tr', {class: r.active ? '' : 'off'}, [h('td', {class: 'pick-cell'}, [cb]), who, roleTd, deptTd, gTd,
          h('td', {class: 'om-act'}, [rowMenu(r.user, [{label: '편집', run: function () { window.omUserEdit(r); }},
            r.active ? {label: '사용 중지', danger: true, disabled: r.user === acc.me, run: function () { bulkActive([r.user], false); }}
                     : {label: '다시 사용', run: function () { bulkActive([r.user], true); }}])])]);
      }));
      none.hidden = !!rows.length;
      pager.replaceChildren(
        h('button', {type: 'button', class: 'btn-ghost', text: '이전', disabled: acc.page ? null : true, onclick: function () { acc.page--; paint(); }}),
        h('span', {text: (acc.page + 1) + ' / ' + pages}),
        h('button', {type: 'button', class: 'btn-ghost', text: '다음', disabled: acc.page < pages - 1 ? null : true, onclick: function () { acc.page++; paint(); }}),
        h('span', {class: 'pager-note', text: rows.length + '개 중 ' + (rows.length ? acc.page * PAGE + 1 : 0) + '–' + Math.min(rows.length, acc.page * PAGE + PAGE)}));
      pager.hidden = rows.length <= PAGE;
      sync(part);
    }
    function sync(part) {
      all.checked = part.length > 0 && part.every(function (r) { return acc.chosen[r.user]; });
      all.indeterminate = !all.checked && part.some(function (r) { return acc.chosen[r.user]; });
      bb.set(ids().length);
    }
    all.addEventListener('change', function () {
      var rows = filtered().slice(acc.page * PAGE, acc.page * PAGE + PAGE);
      rows.forEach(function (r) { if (all.checked) acc.chosen[r.user] = 1; else delete acc.chosen[r.user]; }); paint();
    });
    find.addEventListener('input', function () { acc.q = find.value.trim(); acc.page = 0; paint(); });
    [[role, 'role'], [dept, 'dept'], [state, 'state']].forEach(function (p) { p[0].addEventListener('change', function () { acc[p[1]] = p[0].value; acc.page = 0; paint(); }); });
    main.replaceChildren(head, lt, h('div', {class: 'table-scroll'}, [table]), none, pager);
    paint();
  }
  function bulk(op, uids, value) {
    return postForm('/dev/users/bulk', {op: op, value: value || '', uids: uids}).then(function (j) {
      toast(j.msg); acc.chosen = {}; return load().then(renderAccounts);
    });
  }
  function bulkRole(uids) {
    if (!uids.length) return;
    var choices = Object.keys(roles).map(function (k) { return {value: k, label: roles[k]}; });
    var one = uids.length === 1 ? acc.rows.filter(function (r) { return r.user === uids[0]; })[0] : null;
    confirmBox((one ? (one.name || one.user) + ' 님' : uids.length + '명') + '의 역할을 바꿉니다.', {title: '역할 바꾸기', ok: '바꾸기', choices: choices,
      value: one ? one.role : undefined, busy: '바꾸는 중…', run: function (v) { return bulk('role', uids, v); }});
  }
  function bulkDept(uids) {
    if (!uids.length) return;
    pickDept(uids.length + '명 부서 옮기기', '옮기기', {none: '부서 없음(공통 RAG만)'}, function (to) { return bulk('dept', uids, to); });
  }
  function bulkActive(uids, on) {
    if (!uids.length) return;
    if (on) return bulk('activate', uids).catch(function (e) { toast(e.message, null, true); });
    confirmBox(uids.length + '명의 로그인을 막습니다. 계정과 자료는 지우지 않으며 다시 사용으로 되돌릴 수 있습니다.',
      {title: '사용 중지', ok: '사용 중지', danger: true, busy: '바꾸는 중…', run: function () { return bulk('suspend', uids); }});
  }

  // ------------------------------------------------------------ 찾기·시작
  var findTimer = null, findSeq = 0;
  findBox.addEventListener('input', function () {
    aliasHits = [];
    words = findBox.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    renderTree();
    clearTimeout(findTimer);
    var q = findBox.value.trim();
    if (!q) return;
    findTimer = setTimeout(function () {                    // 부서 사전 — 옛 이름·줄임말로도 찾는다
      var my = ++findSeq;
      getJSON('/api/depts?limit=10&q=' + encodeURIComponent(q)).then(function (d) {
        if (my !== findSeq) return; aliasHits = (d.items || []).map(function (x) { return x.name; }); renderTree();
      }).catch(function () {});
    }, 180);
  });
  findBox.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); var f = tree.querySelector('.om-node.hit'); if (f) f.click(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); var r = tree.querySelector('.om-node'); if (r) { r.tabIndex = 0; r.focus(); } }
  });
  load().then(function () {
    var want = root.getAttribute('data-start-dept'), view = root.getAttribute('data-start-view');
    if (want && byName[want]) choose({kind: 'dept', name: want});
    else if (view === 'all' && canAccounts) choose({kind: 'all'});
    else if (canAccounts) choose({kind: 'all'});
    else {
      var mine = (data.nodes || []).filter(function (n) { return n.team; })[0] || byName[roots[0]];
      if (mine) choose({kind: 'dept', name: mine.name}); else renderMain();
    }
  });
})();
