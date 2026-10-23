/* 조직도 — /api/org 를 읽어 다이어그램(맨 위 「학교」 → 처·단 상자 → 그 아래 팀·센터 상자, 선으로 잇기)을 그린다.
 * 상자를 누르면 가운데 창에서 자세히 보고 고친다(이름 바꾸기·옮기기·지우기·총괄 책임자·부서 더하기·구성원 보기).
 * 고칠 수 있는 상자는 다른 처·단 상자(또는 맨 위 「학교」)로 끌어 놓아 옮긴다.
 * 고치기 단추는 서버가 준 권한(can_edit·can_hold·admin)대로만 보이고, 서버가 매번 다시 검사한다(403).
 * 글자는 모두 textContent 로 넣는다. 접힘·크기는 브라우저에 기억한다(막혀 있으면 건너뜀).
 */
(function () {
  var root = document.getElementById('orgChart');
  if (!root) return;
  var FOLD_KEY = 'oc.folded', ZOOM_KEY = 'oc.zoom';
  var data = null, words = [], aliasHits = [], showSugg = false, dragName = '', toastTimer = null;
  var hitIdx = 0, zoom = 0, firstDraw = true;
  var team = {all: false, depts: []};
  try { team = JSON.parse(root.getAttribute('data-team') || '{}') || team; } catch (e) {}
  var folded = {};
  try { folded = JSON.parse(localStorage.getItem(FOLD_KEY) || '{}') || {}; } catch (e) { folded = {}; }
  try { zoom = parseFloat(localStorage.getItem(ZOOM_KEY) || '0') || 0; } catch (e) { zoom = 0; }
  function saveFold() { try { localStorage.setItem(FOLD_KEY, JSON.stringify(folded)); } catch (e) {} }
  var view = root.querySelector('[data-od-view]'), stage = root.querySelector('[data-od-stage]');

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

  // ------------------------------------------------------------ 서버
  function load() {
    return fetch('/api/org', {headers: {Accept: 'application/json'}}).then(function (r) {
      if (!r.ok) throw new Error('조직도를 불러오지 못했습니다');
      return r.json();
    }).then(function (d) { data = d; render(); }).catch(function (e) {
      stage.replaceChildren(h('p', {class: 'oc-none', text: e.message}));
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
      var ub = undoOf && undoOf.body(j);
      toast(j.msg || '저장했습니다', ub ? function () { act(undoOf.path, ub).catch(function () {}); } : null);
      return load().then(function () { return j; });
    }).catch(function (e) { toast(e.message, null, true); throw e; });
  }
  function move(dept, parent) {
    return act('/org/move', {dept: dept, parent: parent}, {path: '/org/move', body: function (j) { return j.undo; }});
  }

  // ------------------------------------------------------------ 찾기
  function tops() { return (data && data.tops) || []; }
  function findDept(name) {
    var hit = null;
    tops().forEach(function (t) { if (t.name === name) hit = {node: t, top: null}; (t.children || []).forEach(function (c) { if (c.name === name) hit = {node: c, top: t}; }); });
    ((data && data.loose) || []).forEach(function (c) { if (c.name === name) hit = {node: c, top: null, loose: true}; });
    return hit;
  }
  function matches(n) {
    if (!words.length) return false;
    if (aliasHits.indexOf(n.name) >= 0) return true;
    var hay = [n.name].concat(n.aliases || []).join(' ').toLowerCase();
    return words.every(function (w) { return hay.indexOf(w) >= 0; });
  }
  function nameEl(n) {
    var el = h('span', {class: 'od-name', title: n.aliases && n.aliases.length ? n.name + ' · 다른 이름: ' + n.aliases.join(', ') : n.name});
    var w = words.length ? words[0] : '';
    var i = w ? n.name.toLowerCase().indexOf(w) : -1;
    if (i < 0) { el.textContent = n.name; return el; }
    el.append(n.name.slice(0, i), h('mark', {class: 'oc-hit', text: n.name.slice(i, i + w.length)}), n.name.slice(i + w.length));
    return el;
  }
  function canTeam(name) { return !!(team.all || (team.depts || []).indexOf(name) >= 0); }
  function people(list, max) {
    var names = (list || []).map(function (p) { return p.name; });
    return names.slice(0, max).join(', ') + (names.length > max ? ' 외 ' + (names.length - max) + '명' : '');
  }

  // ------------------------------------------------------------ 다이어그램
  function box(n, where) {
    var hit = matches(n);
    var drag = !!n.can_edit;
    var heads = people(n.heads, 2);
    var b = h('div', {class: 'od-box' + (where.top ? ' top' : '') + (hit ? ' hit' : '') + (words.length && !hit ? ' dim' : ''),
      'data-name': n.name, tabindex: '0', role: 'button', 'aria-label': n.name + ' 자세히', draggable: drag ? 'true' : null}, [
      h('span', {class: 'od-l1'}, [nameEl(n), n.kind ? h('span', {class: 'od-kind', text: n.kind}) : null]),
      h('span', {class: 'od-l2'}, [h('span', {text: n.n + '명' + (n.n_extra ? ' · 겸직 ' + n.n_extra : '')}),
        heads ? h('span', {class: 'od-head', title: '총괄 책임자', text: '총괄 ' + heads}) : null]),
      n.via ? h('span', {class: 'od-via', text: n.via + ' 아래'}) : null]);
    b.addEventListener('click', function () { openCard(n); });
    b.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openCard(n); } });
    if (drag) {
      b.addEventListener('dragstart', function (e) {
        dragName = n.name; b.classList.add('lifted'); root.classList.add('dragging');
        try { e.dataTransfer.setData('text/plain', n.name); e.dataTransfer.effectAllowed = 'move'; } catch (x) {}
      });
      b.addEventListener('dragend', function () { dragName = ''; b.classList.remove('lifted'); root.classList.remove('dragging');
        root.querySelectorAll('.over').forEach(function (x) { x.classList.remove('over'); }); });
    }
    return b;
  }
  function dropTarget(el, parent, can) {
    if (!can) return;
    el.addEventListener('dragover', function (e) {
      if (!dragName) return;
      var cur = findDept(dragName);
      if (cur && ((cur.top && cur.top.name === parent) || (!cur.top && !cur.loose && !parent) || dragName === parent)) return;
      e.preventDefault(); try { e.dataTransfer.dropEffect = 'move'; } catch (x) {}
      el.classList.add('over');
    });
    el.addEventListener('dragleave', function (e) { if (!el.contains(e.relatedTarget)) el.classList.remove('over'); });
    el.addEventListener('drop', function (e) {
      e.preventDefault(); el.classList.remove('over');
      var name = dragName; dragName = ''; root.classList.remove('dragging');
      if (name && name !== parent) move(name, parent).catch(function () {});
    });
  }
  function isFolded(t) { return !!folded[t.name] && !(t.children || []).some(matches); }   // 찾은 부서가 아래에 있으면 펼쳐 보인다
  function foldBtn(t) {
    var isFold = isFolded(t), kids = t.children || [];
    return h('button', {type: 'button', class: 'od-fold', 'aria-expanded': isFold ? 'false' : 'true', 'aria-label': t.name + (isFold ? ' 펼치기' : ' 접기'),
      text: isFold ? '펼치기 · ' + kids.length : '접기',
      onclick: function () { if (folded[t.name]) delete folded[t.name]; else folded[t.name] = true; saveFold(); render(); }});
  }
  function column(t) {
    var kids = t.children || [];
    var isFold = isFolded(t);
    var top = box(t, {top: true});
    dropTarget(top, t.name, t.can_hold);
    var col = h('div', {class: 'od-col'}, [top]);
    if (kids.length) {
      col.appendChild(h('div', {class: 'od-sub' + (isFold ? ' shut' : '')}, [foldBtn(t)]));
      if (!isFold) col.appendChild(h('div', {class: 'od-kids', role: 'group', 'aria-label': t.name + ' 아래'},
        kids.map(function (c) { return h('div', {class: 'od-k'}, [box(c, {})]); })));
    }
    return col;
  }
  function looseGroup(list) {
    if (!list.length) return null;
    return h('section', {class: 'od-loose', 'aria-label': '상위 없는 부서'}, [
      h('p', {class: 'od-loose-h', text: '상위 없는 부서 · ' + list.length + '개'}),
      h('div', {class: 'od-loose-list'}, list.map(function (c) { return box(c, {}); }))]);
  }
  function render() {
    if (!data) return;
    var ts = tops();
    var out = [];
    var lg = looseGroup(data.loose || []);
    if (lg) out.push(lg);
    if (ts.length) {
      var rootBox = h('div', {class: 'od-root', 'aria-label': '학교 전체'}, [h('span', {class: 'od-name', text: '학교'}), h('span', {class: 'od-l2', text: '처·단 ' + ts.length + '개'})]);
      dropTarget(rootBox, '', data.can_top);
      out.push(h('div', {class: 'od-tree'}, [rootBox, h('div', {class: 'od-stem', 'aria-hidden': 'true'}),
        h('div', {class: 'od-row' + (ts.length === 1 ? ' one' : '')}, ts.map(column))]));
    }
    if (!out.length) out.push(h('p', {class: 'oc-none', text: '아직 부서가 없습니다'}));
    var keep = {l: view.scrollLeft, t: view.scrollTop, y: window.scrollY, focus: document.activeElement && document.activeElement.closest && document.activeElement.closest('[data-name]')};
    var focusName = keep.focus && stage.contains(keep.focus) ? keep.focus.getAttribute('data-name') : '';
    stage.replaceChildren.apply(stage, out);
    view.scrollLeft = keep.l; view.scrollTop = keep.t;                 // 고친 뒤 다시 그려도 보던 자리·크기·접힘 그대로
    if (Math.abs(window.scrollY - keep.y) > 1) window.scrollTo(0, keep.y);
    if (focusName) { var fe = stage.querySelector('[data-name="' + CSS.escape(focusName) + '"]'); if (fe) fe.focus({preventScroll: true}); }

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
        text: s.dept + ' → ' + s.parent, onclick: function () { move(s.dept, s.parent).catch(function () {}); }}));
    });
    var allBtn = root.querySelector('[data-oc-foldall]');
    var withKids = ts.filter(function (t) { return (t.children || []).length; });
    var anyOpen = withKids.some(function (t) { return !folded[t.name]; });
    allBtn.textContent = anyOpen ? '모두 접기' : '모두 펼치기';
    allBtn.hidden = !withKids.length;
    var found = root.querySelector('[data-oc-found]');
    var hits = stage.querySelectorAll('.od-box.hit');
    found.textContent = words.length ? (hits.length ? hits.length + '개' : '없음') : '';
    if (firstDraw) { firstDraw = false; applyZoom(zoom || null); }
  }
  function jumpTo(i) {
    var hits = stage.querySelectorAll('.od-box.hit');
    if (!hits.length) return;
    hitIdx = ((i % hits.length) + hits.length) % hits.length;
    hits.forEach(function (x, k) { x.classList.toggle('cur', k === hitIdx); });
    var el = hits[hitIdx], vr = view.getBoundingClientRect(), er = el.getBoundingClientRect();
    view.scrollTo({left: view.scrollLeft + er.left - vr.left - (vr.width - er.width) / 2,
      top: view.scrollTop + er.top - vr.top - (vr.height - er.height) / 2, behavior: 'smooth'});
  }

  // ------------------------------------------------------------ 크기·움직이기
  function applyZoom(z) {
    if (!z) {                                               // 맞추기 — 화면 너비에 들어가게(너무 작아지지 않게)
      stage.style.zoom = 1;
      var cs = getComputedStyle(view);
      var w = stage.scrollWidth, avail = view.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - 4;
      z = w > avail ? Math.max(0.4, avail / w) : 1;
      zoom = 0;
    } else zoom = z;
    z = Math.max(0.4, Math.min(1.6, z));
    stage.style.zoom = z;
    stage.setAttribute('data-zoom', String(z));
    root.querySelector('.oc-zv').textContent = Math.round(z * 100) + '%';
    try { if (zoom) localStorage.setItem(ZOOM_KEY, String(zoom)); else localStorage.removeItem(ZOOM_KEY); } catch (e) {}
  }
  function curZoom() { return parseFloat(stage.getAttribute('data-zoom') || '1') || 1; }
  root.querySelectorAll('[data-oc-zoom]').forEach(function (b) {
    b.addEventListener('click', function () {
      var v = b.getAttribute('data-oc-zoom');
      if (v === 'fit') applyZoom(null);
      else applyZoom(Math.round((curZoom() + (+v) * 0.1) * 10) / 10);
    });
  });
  view.addEventListener('wheel', function (e) {                // Ctrl(⌘)+휠로 크기
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    applyZoom(Math.round((curZoom() + (e.deltaY < 0 ? 0.1 : -0.1)) * 10) / 10);
  }, {passive: false});
  var pan = null;                                           // 빈 곳을 잡고 끌면 화면이 움직인다(마우스)
  view.addEventListener('pointerdown', function (e) {
    if (e.pointerType !== 'mouse' || e.button !== 0 || e.target.closest('.od-box, .od-root, button, a, input')) return;
    pan = {x: e.clientX, y: e.clientY, l: view.scrollLeft, t: view.scrollTop};
    view.classList.add('panning');
  });
  window.addEventListener('pointermove', function (e) {
    if (!pan) return;
    view.scrollLeft = pan.l - (e.clientX - pan.x); view.scrollTop = pan.t - (e.clientY - pan.y);
  });
  window.addEventListener('pointerup', function () { if (pan) { pan = null; view.classList.remove('panning'); } });

  // ------------------------------------------------------------ 창
  function dialog(title, build, okText, okClass) {
    var dlg = h('dialog', {class: 'oc-dlg', 'aria-label': title});
    var err = h('p', {class: 'oc-err', role: 'alert'});
    var ok = okText ? h('button', {type: 'submit', class: 'oc-btn ' + (okClass || 'primary'), text: okText}) : null;
    var form = h('form', {method: 'dialog'}, [h('h2', {text: title})]);
    var api = {err: err, ok: ok, dlg: dlg, form: form, close: function () { dlg.close(); }, submit: null};
    build(form, api);
    form.append(err, h('div', {class: 'oc-acts'}, [h('button', {type: 'button', class: 'oc-btn', text: ok ? '취소' : '닫기', onclick: function () { dlg.close(); }}), ok]));
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      if (!ok || !api.submit || ok.disabled) return;
      ok.disabled = true; err.textContent = '';
      Promise.resolve(api.submit()).then(function () { dlg.close(); }).catch(function (x) { err.textContent = x && x.message || '저장하지 못했습니다'; ok.disabled = false; });
    });
    dlg.appendChild(form);
    dlg.addEventListener('close', function () { dlg.remove(); if (api.onclose) api.onclose(); });
    dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });     // 바깥(어두운 곳)을 누르면 닫기
    document.body.appendChild(dlg);
    dlg.showModal();
    var first = form.querySelector('input'); if (first) first.focus();
    return api;
  }
  // 상자를 누르면 — 자세히와 할 수 있는 일
  function openCard(n) {
    var cur = findDept(n.name) || {};
    var isTop = !cur.top && !cur.loose;
    var here = cur.top ? cur.top.name + ' 아래' : (cur.loose ? '상위 없음' : '처·단');
    var back = function () { var el = stage.querySelector('[data-name="' + CSS.escape(n.name) + '"]'); if (el) el.focus({preventScroll: true}); };
    var then = null;                                       // 창을 닫고 이어서 열 창
    var api = dialog(n.name, function (form, api) {
      form.querySelector('h2').after(h('p', {class: 'od-where', text: [n.kind, here].filter(Boolean).join(' · ')}));
      var facts = h('dl', {class: 'oc-facts'}, [h('dt', {text: '사람'}), h('dd', {text: '소속 ' + n.n + '명' + (n.n_extra ? ' · 겸직 ' + n.n_extra + '명' : '')})]);
      var headDd = h('dd', {class: 'od-heads'});
      (n.heads || []).forEach(function (p) {
        var c = h('span', {class: 'oc-chip head', title: p.extra ? '겸직' : ''}, [p.name]);
        if (data.admin && isTop && p.user) c.appendChild(h('button', {type: 'button', 'aria-label': p.name + ' 총괄 책임자에서 빼기', text: '×',
          onclick: function () { api.close(); act('/org/head', {dept: n.name, user: p.user, on: false}).catch(function () {}); }}));
        headDd.appendChild(c);
      });
      if (!(n.heads || []).length) headDd.appendChild(h('span', {class: 'od-none', text: '없음'}));
      facts.append(h('dt', {text: '총괄 책임자'}), headDd);
      if (cur.node && isTop) facts.append(h('dt', {text: '아래 부서'}), h('dd', {text: (cur.node.children || []).length + '개'}));
      form.appendChild(facts);
      var acts = h('div', {class: 'od-acts'});
      function btn(label, fn, cls) { acts.appendChild(h('button', {type: 'button', class: 'oc-btn' + (cls ? ' ' + cls : ''), text: label, onclick: fn})); }
      if (canTeam(n.name)) acts.appendChild(h('a', {class: 'oc-btn', href: '/team?dept=' + encodeURIComponent(n.name), text: '구성원 보기'}));
      if (data.admin && isTop) btn('총괄 책임자 정하기', function () { showPicker(form, api, n.name); });
      if (n.can_edit) {
        btn('이름 바꾸기', function () { then = function () { openRename(n); }; api.close(); });
        btn('옮기기', function () { then = function () { openMove(n); }; api.close(); });
        btn('지우기', function () { then = function () { openDelete(n); }; api.close(); }, 'danger-soft');
      }
      if (acts.childNodes.length) form.appendChild(acts);
    });
    api.onclose = function () { if (then) then(); else back(); };
    var f = api.form.querySelector('.od-acts .oc-btn'); if (f) f.focus();
  }
  function showPicker(form, api, dept) {
    if (form.querySelector('.od-pick')) { form.querySelector('.od-pick input').focus(); return; }
    var box = h('div', {class: 'od-pick'}, [
      h('p', {text: '소속이 다르면 겸직으로 정합니다'}),
      h('div', {'data-people-picker': true, 'data-pass': true, 'data-purpose': 'share', 'data-name': 'head', 'data-placeholder': '이름·아이디·부서로 찾기'})]);
    form.querySelector('.od-acts').after(box);
    var el = box.querySelector('[data-people-picker]');
    if (window.PeoplePicker) window.PeoplePicker.mount(el);
    el.addEventListener('pp:pick', function (e) { api.close(); act('/org/head', {dept: dept, user: e.detail.user, on: true}).catch(function () {}); });
    var input = box.querySelector('input'); if (input) input.focus();
  }
  function openRename(n) {
    dialog('이름 바꾸기', function (form, api) {
      var input = h('input', {type: 'text', value: n.name, 'aria-label': '새 이름', maxlength: '30'});
      form.append(h('label', {}, ['새 이름', input]), h('p', {text: '계정·문서·사업 관리의 부서 이름도 함께 바뀝니다'}));
      api.submit = function () {
        var v = input.value.trim().replace(/\s+/g, ' ');
        if (!v) return Promise.reject(new Error('새 이름을 적어 주세요'));
        if (v === n.name) return Promise.resolve();
        return act('/org/rename', {dept: n.name, name: v}, {path: '/org/rename', body: function (j) { return j.undo; }});
      };
      setTimeout(function () { input.select(); }, 0);
    }, '바꾸기');
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
        return h('div', {class: 'oc-opt' + (i === cur ? ' on' : '') + (c.kid ? ' kid' : ''), role: 'option', 'data-i': i, onmousedown: function (e) { e.preventDefault(); choose(i); }},
          [h('span', {text: c.label}), c.sub ? h('small', {text: c.sub}) : null]);
      }));
      pop.hidden = !list.length; input.setAttribute('aria-expanded', list.length ? 'true' : 'false');
    }
    function choose(i) { var c = items[i]; if (!c) return; picked = c; input.value = c.label; pop.hidden = true; if (opts.onpick) opts.onpick(c); }
    function filter() {
      var q = input.value.trim().toLowerCase();
      if (picked && input.value !== picked.label) picked = null;
      if (picked) q = '';
      var local = choices.filter(function (c) { return !q || (c.label + ' ' + (c.sub || '')).toLowerCase().indexOf(q) >= 0; }).slice(0, 60);
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
        h('dt', {text: '부서 RAG 문서'}), h('dd', {text: (n.docs || 0) + '건' + (n.docs ? ' — 공통 RAG 로 돌아갑니다' : '')})]);
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

  // ------------------------------------------------------------ 위 막대
  var find = root.querySelector('[data-oc-find]'), findTimer = null, findSeq = 0;
  function onFind(jump) {
    words = find.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    render();
    hitIdx = 0;
    if (jump && words.length) jumpTo(0);
  }
  find.addEventListener('input', function () {
    aliasHits = [];
    onFind(true);
    clearTimeout(findTimer);
    var q = find.value.trim();
    if (!q) return;
    findTimer = setTimeout(function () {                    // 부서 사전 — 옛 이름·줄임말로도 찾는다
      var my = ++findSeq;
      fetch('/api/depts?limit=10&q=' + encodeURIComponent(q), {headers: {Accept: 'application/json'}}).then(function (r) { return r.ok ? r.json() : {items: []}; })
        .then(function (d) { if (my !== findSeq) return; aliasHits = (d.items || []).map(function (x) { return x.name; }); onFind(false); }).catch(function () {});
    }, 180);
  });
  find.addEventListener('keydown', function (e) {           // Enter — 다음 찾은 상자로, Shift+Enter — 앞으로
    if (e.key === 'Enter') { e.preventDefault(); jumpTo(hitIdx + (e.shiftKey ? -1 : 1)); }
  });
  root.querySelector('[data-oc-sugg-btn]').addEventListener('click', function () { showSugg = !showSugg; render(); });
  var addBtn = root.querySelector('[data-oc-add]');
  if (addBtn) addBtn.addEventListener('click', function () { openCreate(null); });
  root.querySelector('[data-oc-foldall]').addEventListener('click', function () {
    var withKids = tops().filter(function (t) { return (t.children || []).length; });
    var anyOpen = withKids.some(function (t) { return !folded[t.name]; });
    folded = {};
    if (anyOpen) withKids.forEach(function (t) { folded[t.name] = true; });
    saveFold(); render();
  });
  load();
})();
