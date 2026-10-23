/* 조직도 — /api/org 를 읽어 처·단 카드와 팀·센터 줄을 그리고, 옮기기(끌어 놓기·메뉴)·이름 바꾸기·지우기·더하기·총괄 책임자 지정을 한다.
 * 고치기 단추는 서버가 준 권한(can_edit·can_hold·admin)대로만 보이고, 서버가 매번 다시 검사한다(403).
 * 글자는 모두 textContent 로 넣는다. 접힘 상태는 브라우저에 기억한다(막혀 있으면 건너뜀).
 */
(function () {
  var root = document.getElementById('orgChart');
  if (!root) return;
  var FOLD_KEY = 'oc.folded';
  var data = null, words = [], aliasHits = [], showSugg = false, dragName = '', toastTimer = null;
  var folded = {};
  try { folded = JSON.parse(localStorage.getItem(FOLD_KEY) || '{}') || {}; } catch (e) { folded = {}; }
  function saveFold() { try { localStorage.setItem(FOLD_KEY, JSON.stringify(folded)); } catch (e) {} }

  function h(tag, attrs, kids) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k === 'text') el.textContent = v;
      else if (k === 'class') el.className = v;
      else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    });
    (kids || []).forEach(function (c) { if (c != null && c !== false) el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return el;
  }
  function icon(name) { return h('iconify-icon', {icon: name, 'aria-hidden': 'true'}); }

  // ------------------------------------------------------------ 서버
  function load() {
    return fetch('/api/org', {headers: {Accept: 'application/json'}}).then(function (r) {
      if (!r.ok) throw new Error('조직도를 불러오지 못했습니다');
      return r.json();
    }).then(function (d) { data = d; render(); }).catch(function (e) {
      root.querySelector('[data-oc-list]').replaceChildren(h('p', {class: 'oc-none', text: e.message}));
    });
  }
  function post(path, body) {
    return fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json', Accept: 'application/json'}, body: JSON.stringify(body)})
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok || j.ok === false) throw new Error(j.error || j.detail || '저장하지 못했습니다');
          return j;
        });
      });
  }
  function toast(msg, undo, isErr) {
    var old = document.querySelector('.oc-toast'); if (old) old.remove();
    clearTimeout(toastTimer);
    var t = h('div', {class: 'oc-toast' + (isErr ? ' err' : ''), role: isErr ? 'alert' : 'status'}, [h('span', {text: msg})]);
    if (undo) t.appendChild(h('button', {type: 'button', text: '되돌리기', onclick: function () { t.remove(); undo(); }}));
    document.body.appendChild(t);
    function arm() { clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.remove(); }, undo ? 8000 : 4500); }
    t.addEventListener('mouseenter', function () { clearTimeout(toastTimer); });   // 손이 올라가 있는 동안은 닫지 않는다
    t.addEventListener('mouseleave', arm);
    arm();
  }
  function act(path, body, undoOf) {
    return post(path, body).then(function (j) {
      toast(j.msg || '저장했습니다', undoOf ? function () { act(undoOf.path, undoOf.body(j)).then(function () {}); } : null);
      return load().then(function () { return j; });
    }).catch(function (e) { toast(e.message, null, true); throw e; });
  }
  function move(dept, parent) {
    return act('/org/move', {dept: dept, parent: parent}, {path: '/org/move', body: function (j) { return j.undo; }});
  }

  // ------------------------------------------------------------ 그리기
  function tops() { return (data && data.tops) || []; }
  function findDept(name) {
    var hit = null;
    tops().forEach(function (t) { if (t.name === name) hit = {node: t, top: null}; (t.children || []).forEach(function (c) { if (c.name === name) hit = {node: c, top: t}; }); });
    ((data && data.loose) || []).forEach(function (c) { if (c.name === name) hit = {node: c, top: null, loose: true}; });
    return hit;
  }
  function matches(n) {
    if (!words.length) return true;
    if (aliasHits.indexOf(n.name) >= 0) return true;
    var hay = [n.name].concat(n.aliases || []).join(' ').toLowerCase();
    return words.every(function (w) { return hay.indexOf(w) >= 0; });
  }
  function nameEl(n) {
    var el = h('span', {class: 'oc-name', title: n.aliases && n.aliases.length ? '다른 이름: ' + n.aliases.join(', ') : n.name});
    var w = words.length ? words[0] : '';
    var i = w ? n.name.toLowerCase().indexOf(w) : -1;
    if (i < 0) { el.textContent = n.name; return el; }
    el.append(n.name.slice(0, i), h('mark', {class: 'oc-hit', text: n.name.slice(i, i + w.length)}), n.name.slice(i + w.length));
    return el;
  }
  function chips(n, isTop) {
    var box = h('span', {class: 'oc-chips'});
    (n.heads || []).forEach(function (p) {
      var c = h('span', {class: 'oc-chip head', title: '총괄 책임자' + (p.extra ? ' · 겸직' : '')}, ['총괄 ' + p.name]);
      if (data.admin && isTop) c.appendChild(h('button', {type: 'button', 'aria-label': p.name + ' 총괄 책임자에서 빼기', text: '×',
        onclick: function () { act('/org/head', {dept: n.name, user: p.user, on: false}); }}));
      box.appendChild(c);
    });
    if (data.admin && isTop) box.appendChild(h('button', {type: 'button', class: 'oc-chip add', text: (n.heads || []).length ? '+ 총괄 책임자' : '총괄 책임자 정하기',
      onclick: function (e) { openPicker(e.currentTarget, n.name); }}));
    var leads = n.leads || [];
    leads.slice(0, 3).forEach(function (p) { box.appendChild(h('span', {class: 'oc-chip', title: '사업 책임자' + (p.extra ? ' · 겸직' : '')}, ['사업 ' + p.name])); });
    if (leads.length > 3) box.appendChild(h('span', {class: 'oc-chip', title: leads.slice(3).map(function (p) { return p.name; }).join(', '), text: '외 ' + (leads.length - 3) + '명'}));
    return box;
  }
  function count(n) {
    return h('span', {class: 'oc-count', title: '소속 ' + n.n + '명' + (n.n_extra ? ' · 겸직 ' + n.n_extra + '명' : '') + ' · 부서 RAG 문서 ' + n.docs + '건',
      text: n.n + '명' + (n.n_extra ? ' · 겸직 ' + n.n_extra : '')});
  }
  function menuBtn(n, opts) {
    var items = [];
    if (opts.canHold) items.push(['이 아래에 부서 더하기', function () { openCreate(n.name); }]);
    if (n.can_edit) {
      items.push(['이름 바꾸기', function () { startRename(n); }]);
      items.push(['옮기기', function () { openMove(n); }]);
      items.push(null);
      items.push(['지우기', function () { openDelete(n); }, 'danger']);
    }
    if (!items.length) return null;
    return h('button', {type: 'button', class: 'oc-icon', 'aria-haspopup': 'menu', 'aria-label': n.name + ' 메뉴', title: '메뉴',
      onclick: function (e) { openMenu(e.currentTarget, items); }}, [icon('solar:menu-dots-bold')]);
  }
  function row(n, top) {
    var drag = !!n.can_edit;
    var r = h('div', {class: 'oc-row', 'data-name': n.name, draggable: drag ? 'true' : null, role: 'listitem'}, [
      h('span', {class: 'oc-grip', 'aria-hidden': 'true'}, [icon('solar:hamburger-menu-linear')]),
      h('span', {class: 'oc-title'}, [nameEl(n)]),
      n.kind ? h('span', {class: 'oc-kind', text: n.kind}) : null,
      n.via ? h('span', {class: 'oc-via', text: n.via + ' 아래'}) : null,
      count(n), chips(n, false), h('span', {class: 'oc-gap'}), menuBtn(n, {})]);
    if (drag) {
      r.addEventListener('dragstart', function (e) {
        dragName = n.name; r.classList.add('lifted'); root.classList.add('dragging');
        try { e.dataTransfer.setData('text/plain', n.name); e.dataTransfer.effectAllowed = 'move'; } catch (x) {}
      });
      r.addEventListener('dragend', function () { dragName = ''; r.classList.remove('lifted'); root.classList.remove('dragging');
        root.querySelectorAll('.over').forEach(function (x) { x.classList.remove('over'); }); });
    }
    return r;
  }
  function dropTarget(el, parent, can) {
    if (!can) return;
    el.addEventListener('dragover', function (e) {
      if (!dragName || (typeof can === 'function' && !can())) return;
      var cur = findDept(dragName);
      if (cur && ((cur.top && cur.top.name === parent) || (!cur.top && !cur.loose && !parent) || dragName === parent)) return;
      e.preventDefault(); try { e.dataTransfer.dropEffect = 'move'; } catch (x) {}
      if (!el.classList.contains('over')) {
        el.classList.add('over');
        var slot = el.querySelector('.oc-slot'); if (slot) slot.textContent = dragName + ' — 여기에 놓기';
      }
    });
    el.addEventListener('dragleave', function (e) { if (!el.contains(e.relatedTarget)) el.classList.remove('over'); });
    el.addEventListener('drop', function (e) {
      e.preventDefault(); el.classList.remove('over');
      var name = dragName; dragName = ''; root.classList.remove('dragging');
      if (name && name !== parent) move(name, parent);
    });
  }
  function card(t) {
    var kids = (t.children || []);
    var selfHit = matches(t);
    var shownKids = kids.filter(function (c) { return selfHit || matches(c); });
    if (words.length && !selfHit && !shownKids.length) return null;
    var isFold = !words.length && !!folded[t.name];
    var body = h('div', {class: 'oc-body', role: 'list', 'aria-label': t.name + ' 아래 부서'});
    shownKids.forEach(function (c) { body.appendChild(row(c, t)); });
    if (!kids.length) body.appendChild(h('p', {class: 'oc-empty', text: t.can_hold ? '아래 부서가 없습니다 · 팀·센터를 여기로 끌어 놓을 수 있습니다' : '아래 부서가 없습니다'}));
    body.appendChild(h('div', {class: 'oc-slot', 'aria-hidden': 'true'}));
    var foldBtn = h('button', {type: 'button', class: 'oc-fold', 'aria-expanded': isFold ? 'false' : 'true', text: isFold ? '펼치기' : '접기',
      onclick: function () { folded[t.name] = !folded[t.name]; if (!folded[t.name]) delete folded[t.name]; saveFold(); render(); }});
    var c = h('section', {class: 'oc-card' + (isFold ? ' folded' : ''), 'data-name': t.name, 'aria-label': t.name}, [
      h('div', {class: 'oc-head'}, [
        h('span', {class: 'oc-title'}, [nameEl(t), t.kind ? h('span', {class: 'oc-kind', text: t.kind}) : null]),
        count(t), h('span', {class: 'oc-count', text: '· 아래 ' + kids.length + '개'}), chips(t, true), h('span', {class: 'oc-gap'}),
        foldBtn, menuBtn(t, {canHold: t.can_hold})]),
      body]);
    dropTarget(c, t.name, t.can_hold);
    return c;
  }
  function looseCard(list) {
    var shown = list.filter(matches);
    if (!list.length || (words.length && !shown.length)) return null;
    var body = h('div', {class: 'oc-body', role: 'list', 'aria-label': '상위 없는 부서'});
    shown.forEach(function (c) { body.appendChild(row(c, null)); });
    return h('section', {class: 'oc-card loose', 'aria-label': '상위 없는 부서'}, [
      h('div', {class: 'oc-head'}, [h('span', {class: 'oc-title'}, [h('span', {class: 'oc-name', text: '상위 없는 부서'})]),
        h('span', {class: 'oc-count', text: list.length + '개 · 처·단 카드로 끌어 놓거나 ⋯ 메뉴의 옮기기로 정리'})]),
      body]);
  }
  function render() {
    if (!data) return;
    var list = root.querySelector('[data-oc-list]');
    var out = [];
    var lc = looseCard(data.loose || []);
    if (lc) out.push(lc);
    tops().forEach(function (t) { var c = card(t); if (c) out.push(c); });
    if (!out.length) out.push(h('p', {class: 'oc-none', text: words.length ? '찾는 부서가 없습니다' : '아직 부서가 없습니다'}));
    list.replaceChildren.apply(list, out);
    var add = root.querySelector('[data-oc-add]');
    if (add) add.hidden = !data.can_any;
    var sb = root.querySelector('[data-oc-sugg-btn]'), sg = root.querySelector('[data-oc-sugg]');
    var sug = data.suggestions || [];
    sb.hidden = !sug.length; sb.textContent = '규정 제안 보기 ' + sug.length;
    sb.setAttribute('aria-pressed', showSugg ? 'true' : 'false');
    sg.hidden = !showSugg || !sug.length;
    sg.replaceChildren(h('p', {text: '직제·사무분장 규정에서 뽑은 상위와 지금 자리가 다른 부서 · 누르면 그 처·단 아래로 옮깁니다'}));
    sug.forEach(function (s) {
      sg.appendChild(h('button', {type: 'button', disabled: s.can ? null : true, title: s.can ? '' : '고칠 권한이 없습니다',
        text: s.dept + ' → ' + s.parent, onclick: function () { move(s.dept, s.parent); }}));
    });
    var allBtn = root.querySelector('[data-oc-foldall]');
    var anyOpen = tops().some(function (t) { return !folded[t.name]; });
    allBtn.textContent = anyOpen ? '모두 접기' : '모두 펼치기';
    allBtn.hidden = !tops().length;
  }

  // ------------------------------------------------------------ 메뉴·창
  function closeFloat() { document.querySelectorAll('.oc-menu, .oc-pick').forEach(function (m) { m.remove(); }); }
  function place(el, anchor) {
    var r = anchor.getBoundingClientRect();
    document.body.appendChild(el);
    var w = el.offsetWidth, hgt = el.offsetHeight;
    var left = Math.min(Math.max(8, r.right - w), window.innerWidth - w - 8);
    var top = r.bottom + 4 + hgt > window.innerHeight - 8 ? Math.max(8, r.top - hgt - 4) : r.bottom + 4;
    el.style.left = left + 'px'; el.style.top = top + 'px';
  }
  function openMenu(anchor, items) {
    closeFloat();
    var m = h('div', {class: 'oc-menu', role: 'menu'});
    items.forEach(function (it) {
      if (!it) { m.appendChild(h('hr')); return; }
      m.appendChild(h('button', {type: 'button', role: 'menuitem', class: it[2] || '', text: it[0], onclick: function () { closeFloat(); it[1](); }}));
    });
    place(m, anchor);
    var btns = m.querySelectorAll('button');
    btns[0].focus();
    m.addEventListener('keydown', function (e) {
      var i = Array.prototype.indexOf.call(btns, document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); btns[(i + 1) % btns.length].focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); btns[(i - 1 + btns.length) % btns.length].focus(); }
      else if (e.key === 'Escape') { e.preventDefault(); closeFloat(); anchor.focus(); }
      else if (e.key === 'Tab') { closeFloat(); }
    });
  }
  document.addEventListener('mousedown', function (e) { if (!e.target.closest('.oc-menu, .oc-pick, .pp-pop')) closeFloat(); });

  function startRename(n) {
    var el = root.querySelector('[data-name="' + CSS.escape(n.name) + '"]');
    if (!el) return;
    var title = el.querySelector('.oc-title');
    var input = h('input', {type: 'text', value: n.name, 'aria-label': n.name + ' 새 이름', maxlength: '30'});
    var done = false;
    function finish(save) {
      if (done) return; done = true;
      var v = input.value.trim().replace(/\s+/g, ' ');
      if (!save || !v || v === n.name) { render(); return; }
      act('/org/rename', {dept: n.name, name: v}, {path: '/org/rename', body: function (j) { return j.undo; }}).catch(function () {});
    }
    var box = h('span', {class: 'oc-rename'}, [input,
      h('button', {type: 'button', class: 'oc-btn primary', text: '저장', onmousedown: function (e) { e.preventDefault(); }, onclick: function () { finish(true); }}),
      h('button', {type: 'button', class: 'oc-btn', text: '취소', onmousedown: function (e) { e.preventDefault(); }, onclick: function () { finish(false); }})]);
    title.replaceWith(box);
    el.removeAttribute('draggable');
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); finish(true); }
      else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
    });
    input.addEventListener('blur', function () { setTimeout(function () { if (!done) finish(false); }, 150); });
    input.focus(); input.select();
  }

  function dialog(title, build, okText, okClass) {
    var dlg = h('dialog', {class: 'oc-dlg', 'aria-label': title});
    var err = h('p', {class: 'oc-err', role: 'alert'});
    var ok = h('button', {type: 'submit', class: 'oc-btn ' + (okClass || 'primary'), text: okText});
    var form = h('form', {method: 'dialog'}, [h('h2', {text: title})]);
    var api = {err: err, ok: ok, close: function () { dlg.close(); }, submit: null};
    build(form, api);
    form.append(err, h('div', {class: 'oc-acts'}, [h('button', {type: 'button', class: 'oc-btn', text: '취소', onclick: function () { dlg.close(); }}), ok]));
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      if (!api.submit || ok.disabled) return;
      ok.disabled = true; err.textContent = '';
      Promise.resolve(api.submit()).then(function () { dlg.close(); }).catch(function (x) { err.textContent = x && x.message || '저장하지 못했습니다'; ok.disabled = false; });
    });
    dlg.appendChild(form);
    dlg.addEventListener('close', function () { dlg.remove(); });
    document.body.appendChild(dlg);
    dlg.showModal();
    var first = form.querySelector('input'); if (first) first.focus();
    return api;
  }
  // 고르기 칸 — 목록(choices: [{value, label, sub}])에서 이름으로 거르고 화살표·Enter 로 고른다. fetcher 가 있으면 그 결과로
  function combo(labelText, choices, opts) {
    opts = opts || {};
    var input = h('input', {type: 'text', role: 'combobox', 'aria-expanded': 'false', 'aria-autocomplete': 'list', autocomplete: 'off',
      placeholder: opts.placeholder || '', value: opts.value || ''});
    var pop = h('div', {class: 'oc-opts', role: 'listbox', hidden: true});
    var items = [], cur = -1, picked = opts.picked || null, seq = 0;
    function show(list) {
      items = list; cur = list.length ? 0 : -1;
      pop.replaceChildren.apply(pop, list.map(function (c, i) {
        return h('div', {class: 'oc-opt' + (i === cur ? ' on' : ''), role: 'option', 'data-i': i, onmousedown: function (e) { e.preventDefault(); choose(i); }},
          [h('span', {text: c.label}), c.sub ? h('small', {text: c.sub}) : null]);
      }));
      pop.hidden = !list.length; input.setAttribute('aria-expanded', list.length ? 'true' : 'false');
    }
    function choose(i) { var c = items[i]; if (!c) return; picked = c; input.value = c.label; pop.hidden = true; if (opts.onpick) opts.onpick(c); }
    function filter() {
      var q = input.value.trim().toLowerCase();
      if (picked && input.value !== picked.label) picked = null;
      var local = choices.filter(function (c) { return !q || (c.label + ' ' + (c.sub || '')).toLowerCase().indexOf(q) >= 0; }).slice(0, 30);
      if (!opts.fetcher || !q) { show(local); return; }
      var my = ++seq;
      opts.fetcher(q).then(function (extra) { if (my === seq) show(local.concat(extra.filter(function (x) { return !local.some(function (y) { return y.value === x.value; }); })).slice(0, 30)); })
        .catch(function () { if (my === seq) show(local); });
    }
    input.addEventListener('input', filter);
    input.addEventListener('focus', filter);
    input.addEventListener('blur', function () { setTimeout(function () { pop.hidden = true; input.setAttribute('aria-expanded', 'false'); }, 120); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault(); if (pop.hidden) { filter(); return; }
        cur = Math.max(0, Math.min(items.length - 1, cur + (e.key === 'ArrowDown' ? 1 : -1)));
        pop.querySelectorAll('.oc-opt').forEach(function (o) { o.classList.toggle('on', +o.dataset.i === cur); if (+o.dataset.i === cur) o.scrollIntoView({block: 'nearest'}); });
      } else if (e.key === 'Enter' && !pop.hidden && items[cur]) { e.preventDefault(); choose(cur); }
      else if (e.key === 'Escape' && !pop.hidden) { e.preventDefault(); e.stopPropagation(); pop.hidden = true; }
    });
    var wrap = h('label', {}, [labelText, h('span', {class: 'oc-combo'}, [input, pop])]);
    return {el: wrap, input: input, value: function () { return picked ? picked.value : null; }, text: function () { return input.value.trim(); }};
  }
  function topChoices(except) {
    var out = [];
    if (data.can_top) out.push({value: '', label: '맨 위(처·단)로', sub: '처·단이 됩니다'});
    tops().forEach(function (t) { if (t.can_hold && t.name !== except) out.push({value: t.name, label: t.name, sub: (t.kind || '') + ' · 아래 ' + (t.children || []).length + '개'}); });
    return out;
  }
  function openMove(n) {
    var cur = findDept(n.name);
    dialog('「' + n.name + '」 옮기기', function (form, api) {
      var here = cur && cur.top ? cur.top.name : (cur && cur.loose ? '상위 없음' : '맨 위(처·단)');
      form.appendChild(h('p', {text: '지금 자리: ' + here}));
      var cb = combo('옮길 처·단', topChoices(n.name).filter(function (c) { return !(cur && cur.top && c.value === cur.top.name); }), {placeholder: '처·단 이름으로 찾기'});
      form.appendChild(cb.el);
      api.submit = function () {
        var v = cb.value();
        if (v === null) return Promise.reject(new Error('목록에서 처·단을 골라 주세요'));
        return move(n.name, v);
      };
    }, '옮기기');
  }
  function openDelete(n) {
    var cur = findDept(n.name);
    var kids = cur && !cur.top && !cur.loose ? (cur.node.children || []).length : 0;
    dialog('「' + n.name + '」 지우기', function (form, api) {
      var facts = h('dl', {class: 'oc-facts'}, [
        h('dt', {text: '소속 사람'}), h('dd', {class: n.n ? 'warn' : '', text: n.n + '명' + (n.n ? ' — 먼저 사람을 다른 부서로 옮기세요' : '')}),
        h('dt', {text: '부서 RAG 문서'}), h('dd', {text: n.docs + '건' + (n.docs ? ' — 공통 RAG 로 돌아갑니다' : '')})]);
      if (n.n_extra) facts.append(h('dt', {text: '겸직'}), h('dd', {text: n.n_extra + '명 — 이 부서 겸직이 풀립니다'}));
      if (kids) facts.append(h('dt', {text: '아래 부서'}), h('dd', {text: kids + '개 — 상위 없는 부서로 남습니다'}));
      form.appendChild(facts);
      form.appendChild(h('p', {text: '지우면 되돌릴 수 없습니다. 같은 이름으로 다시 만들어도 옛 겸직 등급·위계는 되살아나지 않습니다.'}));
      if (n.n) api.ok.disabled = true;
      api.submit = function () { return act('/org/delete', {dept: n.name}); };
    }, '지우기', 'danger');
  }
  function deptSuggest(q) {
    return fetch('/api/depts?limit=8&kind=creatable&q=' + encodeURIComponent(q), {headers: {Accept: 'application/json'}})
      .then(function (r) { if (!r.ok) throw r; return r.json(); })
      .then(function (d) { return (d.items || []).map(function (it) { return {value: it.name, label: it.name, sub: it.line !== it.name ? it.line : it.kind_label, parent: it.parent}; }); });
  }
  function openCreate(parent) {
    dialog('부서 더하기', function (form, api) {
      var pc = topChoices('');
      var start = parent != null ? pc.filter(function (c) { return c.value === parent; })[0] : null;
      var pcb = combo('어디 아래에', pc, {placeholder: '처·단 이름으로 찾기', value: start ? start.label : '', picked: start});
      var ncb = combo('부서 이름', [], {placeholder: '예: 학생복지팀 — 직제 규정의 이름이 함께 나옵니다', fetcher: deptSuggest, onpick: function (c) {
        if (pcb.value() === null && c.parent) {                // 사전의 상위가 처·단으로 있으면 그 자리를 먼저 채운다
          var hit = pc.filter(function (x) { return x.value === c.parent; })[0];
          if (hit) { pcb.input.value = hit.label; pcb.input.dispatchEvent(new Event('input')); }
        }
      }});
      form.append(ncb.el, pcb.el);
      api.submit = function () {
        var name = ncb.text(), p = pcb.value();
        if (!name) return Promise.reject(new Error('부서 이름을 적어 주세요'));
        if (p === null) {
          var typed = pcb.text(), hit = pc.filter(function (x) { return x.label === typed; })[0];
          if (!hit) return Promise.reject(new Error('「어디 아래에」 는 목록에서 골라 주세요'));
          p = hit.value;
        }
        return act('/org/create', {name: name, parent: p});
      };
    }, '더하기');
  }
  function openPicker(anchor, dept) {
    closeFloat();
    var box = h('div', {class: 'oc-pick', role: 'dialog', 'aria-label': dept + ' 총괄 책임자 고르기'}, [
      h('p', {text: dept + ' 총괄 책임자 · 소속이 다르면 겸직으로 정합니다'}),
      h('div', {'data-people-picker': true, 'data-pass': true, 'data-purpose': 'share', 'data-name': 'head', 'data-placeholder': '이름·아이디·부서로 찾기'})]);
    place(box, anchor);
    var el = box.querySelector('[data-people-picker]');
    if (window.PeoplePicker) window.PeoplePicker.mount(el);
    el.addEventListener('pp:pick', function (e) { closeFloat(); act('/org/head', {dept: dept, user: e.detail.user, on: true}); });
    var input = box.querySelector('input'); if (input) input.focus();
    box.addEventListener('keydown', function (e) { if (e.key === 'Escape') { closeFloat(); anchor.focus(); } });
  }

  // ------------------------------------------------------------ 위 막대
  var find = root.querySelector('[data-oc-find]'), findTimer = null, findSeq = 0;
  find.addEventListener('input', function () {
    words = find.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    aliasHits = [];
    render();
    clearTimeout(findTimer);
    var q = find.value.trim();
    if (!q) return;
    findTimer = setTimeout(function () {                    // 부서 사전 — 옛 이름·줄임말로도 찾는다
      var my = ++findSeq;
      fetch('/api/depts?limit=10&q=' + encodeURIComponent(q), {headers: {Accept: 'application/json'}}).then(function (r) { return r.ok ? r.json() : {items: []}; })
        .then(function (d) { if (my !== findSeq) return; aliasHits = (d.items || []).map(function (x) { return x.name; }); render(); }).catch(function () {});
    }, 180);
  });
  root.querySelector('[data-oc-sugg-btn]').addEventListener('click', function () { showSugg = !showSugg; render(); });
  var addBtn = root.querySelector('[data-oc-add]');
  if (addBtn) addBtn.addEventListener('click', function () { openCreate(null); });
  root.querySelector('[data-oc-foldall]').addEventListener('click', function () {
    var anyOpen = tops().some(function (t) { return !folded[t.name]; });
    folded = {};
    if (anyOpen) tops().forEach(function (t) { folded[t.name] = true; });
    saveFold(); render();
  });
  dropTarget(root.querySelector('[data-oc-topzone]'), '', function () { return data && data.can_top; });
  load();
})();
