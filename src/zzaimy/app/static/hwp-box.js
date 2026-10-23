// 문서함(/hwp) — 즐겨찾기 별, 구글 드라이브 위치(왼쪽 목록·오른쪽 행), 양식으로 시작 창, 사본 만들기(곳 고르기), 올리기, 그 자리 지우기, 알림 토스트
(function () {
  'use strict';
  var box = document.querySelector('[data-hwp-box]');
  if (!box) return;

  function el(tag, cls, text) { var x = document.createElement(tag); if (cls) x.className = cls; if (text != null) x.textContent = text; return x; }
  function icon(name) { var i = document.createElement('iconify-icon'); i.setAttribute('icon', name); i.setAttribute('aria-hidden', 'true'); return i; }
  function skeleton(n) { var s = el('div', 'hb-skel'); s.setAttribute('aria-label', '불러오는 중'); for (var k = 0; k < (n || 6); k++) s.append(el('i')); return s; }
  function getJSON(url) {
    return fetch(url, {credentials: 'same-origin'}).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) { var e = new Error(j.detail || ('불러오지 못했습니다 (' + r.status + ')')); e.status = r.status; throw e; }
        return j;
      });
    });
  }

  function postJSON(url, body) {
    return fetch(url, {method: 'POST', body: body, credentials: 'same-origin', headers: {Accept: 'application/json'}}).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) { var e = new Error(j.detail || ('하지 못했습니다 (' + r.status + ')')); e.status = r.status; throw e; }
        return j;
      });
    });
  }

  // 알림 — 위에 큰 띠 대신 아래쪽 작은 토스트, 잠시 뒤 사라진다(잘못된 것은 조금 더 오래). 주소의 ok·err 는 지워 새로 고쳐도 다시 뜨지 않게
  var toasts = document.querySelector('[data-toasts]');
  function fade(t) {
    setTimeout(function () { t.classList.add('out'); setTimeout(function () { t.remove(); }, 400); }, t.classList.contains('err') ? 7000 : 3500);
  }
  function toast(text, err) {
    if (!toasts) return;
    var t = el('p', 'hb-toast' + (err ? ' err' : ''), text); t.setAttribute('role', err ? 'alert' : 'status');
    toasts.append(t); fade(t);
  }
  if (toasts) toasts.querySelectorAll('.hb-toast').forEach(fade);
  try {
    var u = new URL(location.href);
    if (u.searchParams.has('ok') || u.searchParams.has('err')) {
      u.searchParams.delete('ok'); u.searchParams.delete('err');
      history.replaceState(history.state, '', u.pathname + (u.search || '') + u.hash);
    }
  } catch (e) {}

  // 지우기 — 한 번 묻고, 화면을 다시 불러오지 않고 그 줄만 뺀다(부서 문서 수도 하나 줄인다)
  document.querySelectorAll('form[data-delete]').forEach(function (f) {
    f.addEventListener('submit', function (e) {
      e.preventDefault();
      if (!window.confirm(f.dataset.confirm)) return;
      var row = f.closest('.hb-row'), b = f.querySelector('button');
      if (b) b.disabled = true;
      postJSON(f.action, new FormData(f)).then(function (j) {
        var list = row && row.parentNode;
        if (row) row.remove();
        var cur = box.querySelector('.hb-place[data-place=dept][aria-current=page] .n');
        if (cur && /^\d+$/.test(cur.textContent)) cur.textContent = String(Math.max(0, +cur.textContent - 1));
        if (list && !list.querySelector('.hb-row')) {
          list.replaceChildren();
          var p = el('p', 'hb-empty', '이 폴더는 비어 있습니다.'); p.append(el('br'), '새 문서를 쓰거나 한글 파일을 올리세요.'); list.append(p);
        }
        toast(j.message || '휴지통으로 옮겼습니다');
      }).catch(function (err) { if (b) b.disabled = false; toast(err.message, true); });
    });
  });

  // 여러 개 고르기 — 줄 앞 칸(마우스를 올리면 보인다)·Shift 로 범위·머리줄 「모두 선택」, 위 띠에서 한꺼번에 휴지통으로
  var selbar = box.querySelector('[data-selbar]'), pickAll = box.querySelector('[data-pick-all]'), lastPick = -1;
  function picks() { return Array.prototype.slice.call(box.querySelectorAll('[data-dept-list] input[data-pick]')); }
  function paintPicks() {
    var all = picks(), on = all.filter(function (c) { return c.checked; });
    if (selbar) { selbar.hidden = !on.length; selbar.querySelector('[data-sel-count]').textContent = on.length + '개 선택'; }
    var list = box.querySelector('[data-dept-list]'); if (list) list.classList.toggle('picking', on.length > 0);
    all.forEach(function (c) { var r = c.closest('.hb-row'); if (r) r.classList.toggle('picked', c.checked); });
    if (pickAll) { pickAll.checked = on.length > 0 && on.length === all.length; pickAll.indeterminate = on.length > 0 && on.length < all.length; }
  }
  picks().forEach(function (c, i) {
    c.addEventListener('click', function (e) {
      var all = picks(), k = all.indexOf(c);
      if (e.shiftKey && lastPick >= 0) { var a = Math.min(lastPick, k), b = Math.max(lastPick, k); all.slice(a, b + 1).forEach(function (x) { x.checked = c.checked; }); }
      lastPick = k; paintPicks();
    });
  });
  if (pickAll) pickAll.addEventListener('change', function () { picks().forEach(function (c) { c.checked = pickAll.checked; }); paintPicks(); });
  if (selbar) {
    selbar.querySelector('[data-sel-clear]').addEventListener('click', function () { picks().forEach(function (c) { c.checked = false; }); paintPicks(); });
    selbar.querySelector('[data-sel-delete]').addEventListener('click', function () {
      var ids = picks().filter(function (c) { return c.checked; }).map(function (c) { return c.value; });
      if (!ids.length || !window.confirm(ids.length + '개 문서를 지울까요? 부서 휴지통으로 옮기며 관리자가 되살릴 수 있습니다.')) return;
      var btn = this; btn.disabled = true;
      fetch('/hwp/delete-many', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', Accept: 'application/json'}, body: JSON.stringify({ids: ids})})
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.detail || '지우지 못했습니다'); return j; }); })
        .then(function (j) {
          var list = box.querySelector('[data-dept-list]');
          (j.deleted || []).forEach(function (id) { var r = list.querySelector('.hb-row[data-id="' + String(id).replace(/[^0-9]/g, '') + '"]'); if (r) r.remove(); });
          var cur = box.querySelector('.hb-place[data-place=dept][aria-current=page] .n');
          if (cur && /^\d+$/.test(cur.textContent)) cur.textContent = String(Math.max(0, +cur.textContent - (j.deleted || []).length));
          if (!list.querySelector('.hb-row')) {
            list.replaceChildren();
            var p = el('p', 'hb-empty', '이 폴더는 비어 있습니다.'); p.append(el('br'), '새 문서를 쓰거나 한글 파일을 올리세요.'); list.append(p);
          }
          if ((j.deleted || []).length) toast((j.deleted.length) + '개를 휴지통으로 옮겼습니다');
          if ((j.rejected || []).length) toast((j.rejected.length) + '개는 지우지 못했습니다 — ' + (j.rejected[0].why || ''), true);
          paintPicks();
        })
        .catch(function (err) { toast(err.message, true); })
        .then(function () { btn.disabled = false; });
    });
  }

  // 사본 만들기 — 공용 위치 창(AuraFinder.save)에서 부서 폴더와 이름을 고른다. 처음 여는 곳은 내 부서에서 마지막으로 일한 폴더
  function copyDoc(id, name) {
    var stem = String(name || '').replace(/\.(hwpx|hwp)$/i, '').replace(/ 공통 양식$/, '');
    if (!window.AuraFinder || typeof window.AuraFinder.save !== 'function') { toast('위치 고르기 창을 불러오지 못했습니다. 새로 고침한 뒤 다시 해 주세요', true); return; }
    window.AuraFinder.save({
      title: '사본을 어디에 만들까요', saveLabel: '사본 만들기', name: stem, accept: 'hwp',
      at: {dept: box.dataset.copyDept || '', folder: box.dataset.copyFolder || ''},
      note: '사본은 고른 곳에 두고 기안기로 엽니다', projects: true,
      onSave: function (at) {
        if (at.target === 'drive') return Promise.reject(new Error('사본은 부서 폴더·내 폴더·프로젝트에만 만들 수 있습니다 — 왼쪽에서 고르세요'));
        var body = new FormData(); body.append('name', at.name);
        if (at.target === 'project') body.append('project', at.project_id);
        else { body.append('dept', at.dept); body.append('folder', at.folder || ''); }
        return postJSON('/hwp/' + encodeURIComponent(id) + '/copy', body).then(function (j) { location.href = j.url; return j; });
      }
    });
  }
  document.querySelectorAll('form[data-copy]').forEach(function (f) {
    f.addEventListener('submit', function (e) { e.preventDefault(); copyDoc(f.dataset.copy, f.dataset.name); });
  });

  // 즐겨찾기 별 — 화면을 다시 불러오지 않고 켜고 끈다(같은 폴더의 별은 함께), 왼쪽 「즐겨찾기」 는 받은 목록으로 다시 그린다
  var favSec = box.querySelector('[data-fav-sec]'), favList = box.querySelector('[data-fav-list]');
  function favKey(f) { return f.elements.dept.value + '\n' + f.elements.folder.value; }
  function drawFavs(list) {
    if (!favList) return;
    favList.replaceChildren();
    (list || []).forEach(function (x) {
      var a = el('a', 'hb-place'); a.href = x.href; a.dataset.place = 'fav'; a.title = x.where || x.dept;
      if (x.dept === box.dataset.dept && x.folder === box.dataset.folder) { a.classList.add('on'); a.setAttribute('aria-current', 'page'); }
      a.append(icon('solar:star-linear'), el('span', 't', x.name));
      if (x.where) a.append(el('span', 'n', x.dept));
      favList.append(a);
    });
    if (favSec) favSec.hidden = !(list || []).length;
  }
  function setStar(f, on) {
    var b = f.querySelector('.hb-star'), i = b && b.querySelector('iconify-icon');
    f.elements.on.value = on ? '0' : '1';
    if (!b) return;
    b.classList.toggle('on', on); b.setAttribute('aria-pressed', String(on));
    b.title = on ? '즐겨찾기에서 빼기' : '즐겨찾기에 넣기';
    if (i) i.setAttribute('icon', on ? 'solar:star-bold' : 'solar:star-linear');
  }
  function bindFav(f) {
    f.addEventListener('submit', function (e) {
      e.preventDefault();
      var b = f.querySelector('button'); if (b) b.disabled = true;
      fetch(f.action, {method: 'POST', body: new FormData(f), credentials: 'same-origin', headers: {Accept: 'application/json'}})
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.detail || '즐겨찾기를 바꾸지 못했습니다'); return j; }); })
        .then(function (j) {
          var k = favKey(f);
          box.querySelectorAll('form[data-fav]').forEach(function (g) { if (favKey(g) === k) setStar(g, !!j.on); });
          drawFavs(j.favorites);
        })
        .catch(function (err) { toast(err.message, true); })
        .then(function () { if (b) b.disabled = false; });
    });
  }
  box.querySelectorAll('form[data-fav]').forEach(bindFav);
  function makeStar(dept, folder, label) {                  // 새로 만든 폴더 줄의 별(서버가 그리는 것과 같은 꼴)
    var f = el('form', 'hb-fav'); f.action = '/hwp/favorite'; f.method = 'post'; f.setAttribute('data-fav', '');
    [['dept', dept], ['folder', folder], ['on', '1']].forEach(function (kv) { var i = el('input'); i.type = 'hidden'; i.name = kv[0]; i.value = kv[1]; f.append(i); });
    var b = el('button', 'hb-star'); b.type = 'submit'; b.setAttribute('aria-pressed', 'false'); b.setAttribute('aria-label', '「' + label + '」 즐겨찾기'); b.title = '즐겨찾기에 넣기';
    b.append(icon('solar:star-linear')); f.append(b); bindFav(f);
    return f;
  }

  // 새 폴더·폴더 이름 바꾸기 — 창 없이 목록 줄에서 바로(파인더처럼). Enter·칸 밖 누르기 = 저장, Esc = 그만두기, 잘못은 그 줄에
  var dList0 = box.querySelector('[data-dept-list]');
  function folderNames() { return Array.prototype.map.call(dList0.querySelectorAll('.hb-row[data-kind=folder] .hb-name'), function (a) { return a.textContent; }); }
  function freeName() {
    var have = folderNames(), n = '새 폴더', k = 2;
    while (have.indexOf(n) >= 0) n = '새 폴더 ' + (k++);
    return n;
  }
  function editName(row, value, commit, cancel) {
    var nameEl = row.querySelector('.hb-name'), err = row.querySelector('.hb-meta');
    var inp = el('input', 'hb-input hb-rename'); inp.type = 'text'; inp.value = value; inp.maxLength = 60; inp.setAttribute('aria-label', '폴더 이름');
    if (nameEl) nameEl.hidden = true;
    row.querySelector('.hb-cell').insertBefore(inp, nameEl || null);
    row.classList.add('editing');
    var busy = false, over = false;
    function close() { over = true; inp.remove(); row.classList.remove('editing'); if (nameEl) nameEl.hidden = false; }
    function go() {
      if (busy || over) return;
      var v = inp.value.trim();
      if (!v) { close(); cancel(); return; }
      busy = true; inp.disabled = true;
      commit(v).then(function (j) { close(); return j; }, function (e) {
        busy = false; inp.disabled = false; inp.focus(); inp.select();
        if (err) { err.textContent = e.message; err.classList.add('hb-err'); }
      });
    }
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); go(); }
      else if (e.key === 'Escape') { e.preventDefault(); close(); cancel(); }
    });
    inp.addEventListener('input', function () { if (err && err.classList.contains('hb-err')) { err.textContent = ''; err.classList.remove('hb-err'); } });
    inp.addEventListener('blur', function () { setTimeout(go, 0); });
    setTimeout(function () { inp.focus(); inp.select(); }, 0);
  }
  function folderForm(extra) {
    var b = new FormData(); b.append('dept', box.dataset.dept || '');
    Object.keys(extra).forEach(function (k) { b.append(k, extra[k]); });
    return b;
  }
  function renameRow(row) {
    var a = row.querySelector('.hb-name'), meta = row.querySelector('.hb-meta'), keep = meta ? meta.textContent : '';
    editName(row, a.textContent, function (v) {
      if (v === a.textContent) return Promise.resolve({});
      return postJSON('/hwp/folder-rename', folderForm({folder: row.dataset.path, name: v})).then(function (j) {
        a.textContent = j.name; a.href = j.href; row.dataset.path = j.path;
        var f = row.querySelector('form[data-fav]'); if (f) f.elements.folder.value = j.path;
        if (meta) meta.textContent = keep;
        toast('「' + j.name + '」 으로 이름을 바꿨습니다');
        return j;
      });
    }, function () { if (meta) { meta.textContent = keep; meta.classList.remove('hb-err'); } });
  }
  function bindFolderRow(row) {                             // 이름 바꾸기는 줄의 연필 단추(두 번 눌러 바꾸기도 그대로 둔다)
    var b = row.querySelector('[data-rename-folder]');
    if (b) b.addEventListener('click', function () { renameRow(row); });
    if (dList0 && dList0.dataset.mayMake) row.addEventListener('dblclick', function (e) { if (!e.target.closest('a,button,input,form')) renameRow(row); });
  }
  function iconBtn(ic, label) { var b = el('button', 'hb-ib'); b.type = 'button'; b.title = label; b.setAttribute('aria-label', label); b.append(icon(ic)); return b; }
  if (dList0) dList0.querySelectorAll('.hb-row[data-kind=folder]').forEach(bindFolderRow);
  var nf = box.querySelector('[data-new-folder]');
  if (nf && dList0) nf.addEventListener('click', function () {
    if (dList0.querySelector('.hb-row.editing')) return;
    var empty = dList0.querySelector('.hb-empty'); if (empty) empty.remove();
    var head = dList0.querySelector('.hb-head');
    if (!head) {
      head = el('div', 'hb-head'); head.setAttribute('aria-hidden', 'true');
      ['', '이름', '수정일', '수정한 사람', ''].forEach(function (t) { head.append(el('span', '', t)); });
      dList0.prepend(head);
    }
    var row = el('div', 'hb-row'); row.dataset.kind = 'folder'; row.setAttribute('role', 'listitem');
    row.append(icon('solar:folder-bold'), el('span', 'hb-cell'), el('span', 'hb-meta'), el('span', 'hb-meta'), el('div', 'hb-act'));
    head.after(row);
    editName(row, freeName(), function (v) {
      return postJSON('/hwp/folder', folderForm({folder: box.dataset.folder || '', name: v})).then(function (j) {
        var a = el('a', 'hb-name', j.name); a.href = j.href;
        var cell = row.querySelector('.hb-cell'), cnt = el('span', 'hb-count', '0개'); cnt.title = '문서 0개';
        cell.prepend(a); cell.append(cnt); row.dataset.path = j.path;
        var metas = row.querySelectorAll('.hb-meta'); metas[0].textContent = ''; metas[0].classList.remove('hb-err');
        var act = row.querySelector('.hb-act'), rb = iconBtn('solar:pen-linear', '이름 바꾸기'); rb.setAttribute('data-rename-folder', '');
        act.append(rb, makeStar(box.dataset.dept || '', j.path, j.name));
        bindFolderRow(row);
        toast('「' + j.name + '」 폴더를 만들었습니다');
        return j;
      });
    }, function () {
      row.remove();
      if (!dList0.querySelector('.hb-row')) {
        dList0.replaceChildren();
        var p = el('p', 'hb-empty', '이 폴더는 비어 있습니다.'); p.append(el('br'), '새 문서를 쓰거나 한글 파일을 올리세요.'); dList0.append(p);
      }
    });
  });

  // 올리기 — 파일을 고르면 바로 보낸다
  var up = document.querySelector('[data-upload]'), upInput = up && up.querySelector('input[type=file]');
  var pick = document.querySelector('[data-pick-file]');
  if (pick && upInput) {
    pick.addEventListener('click', function () { upInput.click(); });
    upInput.addEventListener('change', function () { if (upInput.files.length) up.submit(); });
  }

  // 창을 열면 글 넣는 칸에 바로 커서
  document.querySelectorAll('[data-modal-open]').forEach(function (b) {
    b.addEventListener('click', function () {
      var m = document.querySelector(b.dataset.modalOpen), f = m && m.querySelector('.hb-input');
      if (f) setTimeout(function () { f.focus(); }, 30);
    });
  });

  // ---- 구글 드라이브 ----
  var deptView = box.querySelector('[data-dept-view]'), driveView = box.querySelector('[data-drive-view]');
  var dList = box.querySelector('[data-drive-list]'), dPath = box.querySelector('[data-drive-path]');
  var dBack = box.querySelector('[data-drive-back]');
  var dImport = box.querySelector('[data-drive-import]'), places = box.querySelector('[data-drive-places]');
  var hint = box.querySelector('[data-drive-hint]');
  var loc = null, hist = [], token = 0;

  function mark(key) {
    box.querySelectorAll('.hb-place').forEach(function (p) {
      if (p.matches('[data-drive-place]')) p.classList.toggle('on', key != null && p.dataset.drivePlace === key);
      else p.classList.toggle('on', key == null && p.getAttribute('aria-current') === 'page');
    });
  }
  function noAccount() { if (hint) hint.hidden = false; }

  function drawPath(trail) {
    dPath.replaceChildren();
    trail.forEach(function (t, i) {
      if (i) dPath.append(el('span', 'hb-sep', '›'));
      if (i === trail.length - 1) { dPath.append(el('b', '', t.name)); return; }
      var b = el('button', '', t.name); b.type = 'button';
      b.onclick = function () { go({drive: loc.drive, folder: t.id, shared: !!t.shared, key: loc.key, trail: trail.slice(0, i + 1)}, true); };
      dPath.append(b);
    });
  }
  function fileRow(f) {
    var r = el('div', 'hb-row'); r.dataset.kind = 'file'; r.setAttribute('role', 'listitem');
    var act = el('div', 'hb-act'), b = el('button', 'hb-btn', '가져오기'); b.type = 'button';   // 문서함 지금 폴더로 받아 기안기로 연다
    b.prepend(icon('solar:download-minimalistic-linear'));
    b.title = '‘' + (dImport.dataset.where || '') + '’에 받아 기안기로 엽니다';
    b.onclick = function () {
      toast('「' + f.name + '」 받는 중…');
      dList.querySelectorAll('button').forEach(function (x) { x.disabled = true; });
      dImport.elements.file_id.value = f.id; dImport.submit();
    };
    act.append(b);
    r.append(icon('solar:document-text-linear'), el('span', 'hb-name', f.name), el('span', 'hb-meta', (f.modifiedTime || '').slice(0, 10)), act);
    return r;
  }
  function folderRow(f) {
    var r = el('button', 'hb-row'); r.type = 'button'; r.dataset.kind = 'folder'; r.setAttribute('role', 'listitem');
    r.append(icon('solar:folder-bold'), el('span', 'hb-name', f.name), el('span', 'hb-meta'));
    r.onclick = function () { go({drive: loc.drive, folder: f.id, key: loc.key, trail: loc.trail.concat([{id: f.id, name: f.name}])}, true); };
    return r;
  }
  function go(next, push) {
    if (push && loc) hist.push(loc);
    loc = next;
    deptView.hidden = true; driveView.hidden = false;
    dBack.disabled = !hist.length;
    mark(next.key != null ? next.key : next.drive);
    drawPath(next.trail);
    dList.replaceChildren(skeleton(7));
    var mine = ++token;
    getJSON('/api/hwp/drive/browse?drive=' + encodeURIComponent(next.drive || '') + '&folder=' + encodeURIComponent(next.folder || '') + (next.shared ? '&shared=1' : ''))
      .then(function (j) {
        if (mine !== token) return;
        dList.replaceChildren();
        var folders = j.folders || [], files = j.files || [];
        if (folders.length || files.length) {
          var h = el('div', 'hb-head'); h.setAttribute('aria-hidden', 'true');
          h.append(el('span'), el('span', '', '이름'), el('span', '', '수정일'), el('span'));
          dList.append(h);
        }
        folders.forEach(function (f) { dList.append(folderRow(f)); });
        files.forEach(function (f) { dList.append(fileRow(f)); });
        if (!folders.length && !files.length) dList.append(el('p', 'hb-empty', '이 폴더에 한글 파일이 없습니다.'));
        if (j.drives) addDrives(j.drives, j.dept_drives);
      })
      .catch(function (e) {
        if (mine !== token) return;
        dList.replaceChildren();
        var p = el('p', 'hb-empty');
        if (e.status === 409) {
          noAccount();
          p.append('학교 구글 계정을 연결하면 드라이브도 여기서 봅니다.', document.createElement('br'));
          var a = el('a', '', '설정에서 연결하기'); a.href = '/settings'; p.append(a);
        } else { p.textContent = e.message; }
        dList.append(p);
      });
  }
  var drivesShown = false;
  function place(key, ic, name, open) {
    var b = el('button', 'hb-place'); b.type = 'button'; b.dataset.drivePlace = key;
    b.append(icon(ic), el('span', 't', name)); b.title = name;
    b.onclick = function () { hist = []; open(); };
    places.append(b);
  }
  // 왼쪽 구글 드라이브 — 내 드라이브(위에 고정), 공유 문서함, 내 부서 드라이브, 공유 드라이브
  function addDrives(list, deptDrives) {
    if (drivesShown || !places) return;
    drivesShown = true;
    place('shared', 'solar:users-group-two-rounded-linear', '공유 문서함', function () {
      go({drive: '', folder: '', shared: true, key: 'shared', trail: [{id: '', name: '공유 문서함', shared: true}]}, false);
    });
    (deptDrives || []).forEach(function (d) {
      place('dept:' + d.dept, 'solar:folder-cloud-linear', d.name, function () {
        go({drive: '', folder: d.folder, key: 'dept:' + d.dept, trail: [{id: d.folder, name: d.name}]}, false);
      });
    });
    (list || []).forEach(function (d) {
      place(d.id, 'solar:server-square-linear', d.name, function () { go({drive: d.id, folder: '', trail: [{id: '', name: d.name}]}, false); });
    });
    if (loc) mark(loc.key != null ? loc.key : loc.drive);
  }
  function openMyDrive() { hist = []; go({drive: '', folder: '', trail: [{id: '', name: '내 드라이브'}]}, false); }
  if (driveView) {
    var my = box.querySelector('[data-drive-place=""]');
    if (my) my.onclick = openMyDrive;
    // 드라이브에서 가져오기는 왼쪽 「구글 드라이브」 에서 — 파일 줄의 「가져오기」 가 지금 폴더로 받아 기안기로 연다
    dBack.onclick = function () { var l = hist.pop(); if (l) go(l, false); };
    // 공유 문서함·부서 드라이브·공유 드라이브는 왼쪽 목록에 미리 — 계정이 없으면 한 줄 안내만
    getJSON('/api/hwp/drive/browse').then(function (j) { addDrives(j.drives || [], j.dept_drives || []); })
      .catch(function (e) { if (e.status === 409) noAccount(); });
  }

  // ---- 양식으로 시작 ----
  var tList = document.querySelector('[data-tpl-list]');
  var tSearch = document.querySelector('[data-tpl-search]'), tOpen = document.querySelector('[data-tpl-open]');
  var groups = null, sel = -1;
  function groupName(g) {
    var s = String(g || '');
    if (s === '공통 양식') return '공통 양식';
    if (s.indexOf('공통 양식/') === 0) return s.slice(6);
    if (s.indexOf('지난 사업 문서/') === 0) return '지난 사업 · ' + s.slice(9);
    return s;
  }
  function items() { return Array.prototype.slice.call(tList.querySelectorAll('.hb-tpl-item')); }
  function choose(i) {
    var all = items(); if (!all.length) { sel = -1; return; }
    sel = Math.max(0, Math.min(all.length - 1, i));
    all.forEach(function (b, k) { b.classList.toggle('on', k === sel); b.setAttribute('aria-selected', String(k === sel)); });
    all[sel].scrollIntoView({block: 'nearest'});
  }
  function start(x) {                                       // 양식 창을 닫고 복사할 곳을 묻는다
    var close = document.querySelector('#hwTpl [data-modal-close]');
    if (close) close.click();
    copyDoc(x.id, x.name);
  }
  function drawTpl(word) {
    tList.replaceChildren(); sel = -1;
    var w = (word || '').trim().toLowerCase(), n = 0;
    (groups || []).forEach(function (g) {
      var hit = (g.items || []).filter(function (x) { return !w || x.name.toLowerCase().indexOf(w) >= 0; });
      if (!hit.length) return;
      tList.append(el('p', 'hb-tpl-group', groupName(g.group)));
      hit.forEach(function (x) {
        var b = el('button', 'hb-tpl-item'); b.type = 'button'; b.setAttribute('role', 'option'); b.tabIndex = -1;
        b.append(icon('solar:document-text-linear'), el('span', '', x.name.replace(/ 공통 양식$/, '')));   // 묶음 이름에 이미 있는 말은 뺀다
        b.onclick = function () { start(x); };
        b.onmousemove = function () { var k = items().indexOf(b); if (k !== sel) choose(k); };
        tList.append(b); n++;
      });
    });
    if (!n) tList.append(el('p', 'hb-empty', w ? '맞는 양식이 없습니다.' : '쓸 수 있는 양식이 없습니다.'));
    else if (w) choose(0);
  }
  if (tOpen && tList) {
    tOpen.addEventListener('click', function () {
      if (groups) { drawTpl(tSearch.elements.q.value); return; }
      tList.replaceChildren(skeleton(8));
      getJSON('/api/hwp/templates').then(function (j) { groups = j.groups || []; drawTpl(tSearch.elements.q.value); })
        .catch(function (e) { tList.replaceChildren(el('p', 'hb-empty', e.message)); });
    });
    tSearch.addEventListener('input', function () { if (groups) drawTpl(tSearch.elements.q.value); });
    tSearch.addEventListener('submit', function (e) { e.preventDefault(); });
    tSearch.elements.q.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); choose(sel + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); choose(sel - 1); }
      else if (e.key === 'Enter') { e.preventDefault(); var all = items(); if (all[sel]) all[sel].click(); }
    });
  }
})();
