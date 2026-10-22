// 문서함(/hwp) — 구글 드라이브 위치(왼쪽 목록·오른쪽 행), 양식으로 시작 창, 올리기, 지우기 확인
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

  // 지우기 같은 되돌리기 어려운 일은 한 번 묻는다
  document.querySelectorAll('form[data-confirm]').forEach(function (f) {
    f.addEventListener('submit', function (e) { if (!window.confirm(f.dataset.confirm)) e.preventDefault(); });
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
  var dBack = box.querySelector('[data-drive-back]'), dNote = box.querySelector('[data-drive-note]');
  var dImport = box.querySelector('[data-drive-import]'), places = box.querySelector('[data-drive-places]');
  var hint = box.querySelector('[data-drive-hint]');
  var noteText = dNote ? dNote.textContent : '';
  var loc = null, hist = [], token = 0;

  function mark(key) {
    box.querySelectorAll('.hb-place').forEach(function (p) {
      var on = key != null && p.matches('[data-drive-place]') && p.dataset.drivePlace === key;
      p.classList.toggle('on', on);
      if (p.matches('[data-place=dept]')) { p.classList.toggle('on', key == null && p.getAttribute('aria-current') === 'page'); }
    });
  }
  function noAccount() { if (hint) hint.hidden = false; }
  function say(text, err) { if (!dNote) return; dNote.textContent = text; if (err) dNote.setAttribute('data-err', ''); else dNote.removeAttribute('data-err'); }

  function drawPath(trail) {
    dPath.replaceChildren();
    trail.forEach(function (t, i) {
      if (i) dPath.append(el('span', 'hb-sep', '›'));
      if (i === trail.length - 1) { dPath.append(el('b', '', t.name)); return; }
      var b = el('button', '', t.name); b.type = 'button';
      b.onclick = function () { go({drive: loc.drive, folder: t.id, trail: trail.slice(0, i + 1)}, true); };
      dPath.append(b);
    });
  }
  function fileRow(f) {
    var r = el('button', 'hb-row'); r.type = 'button'; r.dataset.kind = 'file'; r.setAttribute('role', 'listitem');
    r.append(icon('solar:document-text-linear'), el('span', 'hb-name', f.name), el('span', 'hb-meta', (f.modifiedTime || '').slice(0, 10)));
    r.title = '받아서 기안기로 열기';
    r.onclick = function () {
      say('「' + f.name + '」 받는 중…');
      dList.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
      dImport.elements.file_id.value = f.id; dImport.submit();
    };
    return r;
  }
  function folderRow(f) {
    var r = el('button', 'hb-row'); r.type = 'button'; r.dataset.kind = 'folder'; r.setAttribute('role', 'listitem');
    r.append(icon('solar:folder-bold'), el('span', 'hb-name', f.name), el('span', 'hb-meta', '폴더'));
    r.onclick = function () { go({drive: loc.drive, folder: f.id, trail: loc.trail.concat([{id: f.id, name: f.name}])}, true); };
    return r;
  }
  function go(next, push) {
    if (push && loc) hist.push(loc);
    loc = next;
    deptView.hidden = true; driveView.hidden = false;
    dBack.disabled = !hist.length;
    mark(next.drive);
    drawPath(next.trail);
    say(noteText);
    dList.replaceChildren(skeleton(7));
    var mine = ++token;
    getJSON('/api/hwp/drive/browse?drive=' + encodeURIComponent(next.drive || '') + '&folder=' + encodeURIComponent(next.folder || ''))
      .then(function (j) {
        if (mine !== token) return;
        dList.replaceChildren();
        var folders = j.folders || [], files = j.files || [];
        if (folders.length || files.length) {
          var h = el('div', 'hb-head'); h.setAttribute('aria-hidden', 'true');
          h.append(el('span'), el('span', '', '이름'), el('span', '', '고친 때'));
          dList.append(h);
        }
        folders.forEach(function (f) { dList.append(folderRow(f)); });
        files.forEach(function (f) { dList.append(fileRow(f)); });
        if (!folders.length && !files.length) dList.append(el('p', 'hb-empty', '이 폴더에 한글 파일이 없습니다.'));
        if (j.drives) addDrives(j.drives);
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
  function addDrives(list) {
    if (drivesShown || !places) return;
    drivesShown = true;
    list.forEach(function (d) {
      var b = el('button', 'hb-place'); b.type = 'button'; b.dataset.drivePlace = d.id;
      b.append(icon('solar:users-group-rounded-linear'), el('span', 't', d.name));
      b.onclick = function () { hist = []; go({drive: d.id, folder: '', trail: [{id: '', name: d.name}]}, false); };
      places.append(b);
    });
    if (loc) mark(loc.drive);
  }
  function openMyDrive() { hist = []; go({drive: '', folder: '', trail: [{id: '', name: '내 드라이브'}]}, false); }
  if (driveView) {
    var my = box.querySelector('[data-drive-place=""]');
    if (my) my.onclick = openMyDrive;
    // 「드라이브에서 가져오기」 — 공용 파일 창(AuraFinder)을 드라이브에서 연다. 드라이브 파일은 지금 폴더로 받아 기안기로,
    // 부서 폴더 파일은 그 자리의 파일을 바로 연다(창 모듈이 없으면 예전처럼 오른쪽에 드라이브 목록)
    var opener = document.querySelector('[data-drive-open]');
    if (opener) opener.onclick = function () {
      if (!window.AuraFinder || typeof window.AuraFinder.open !== 'function') { openMyDrive(); return; }
      window.AuraFinder.open({title: '드라이브에서 가져오기', accept: 'hwp', openLabel: '가져오기', start: 'drive',
                              at: {dept: box.dataset.dept || '', folder: box.dataset.folder || ''}, note: noteText})
        .then(function (got) {
          var it = got && got[0];
          if (!it) return;
          if (it.source === 'drive') { dImport.elements.file_id.value = it.drive_id; dImport.submit(); return; }
          location.href = box.dataset.editor ? '/hwp/' + encodeURIComponent(it.id) + '/edit' : '/hwp/' + encodeURIComponent(it.id) + '/file';
        });
    };
    dBack.onclick = function () { var l = hist.pop(); if (l) go(l, false); };
    // 공유 드라이브는 왼쪽 목록에 미리 — 계정이 없으면 한 줄 안내만
    getJSON('/api/hwp/drive/browse').then(function (j) { addDrives(j.drives || []); })
      .catch(function (e) { if (e.status === 409) noAccount(); });
  }

  // ---- 양식으로 시작 ----
  var tList = document.querySelector('[data-tpl-list]'), tCopy = document.querySelector('[data-tpl-copy]');
  var tSearch = document.querySelector('[data-tpl-search]'), tOpen = document.querySelector('[data-tpl-open]');
  var groups = null, sel = -1;
  function groupName(g) {
    var s = String(g || '');
    if (s === '공통 양식') return '공통 양식';
    if (s.indexOf('공통 양식/') === 0) return s.slice(6);
    if (s.indexOf('양식/') === 0) return '학교 양식 · ' + s.slice(3);
    return s;
  }
  function items() { return Array.prototype.slice.call(tList.querySelectorAll('.hb-tpl-item')); }
  function choose(i) {
    var all = items(); if (!all.length) { sel = -1; return; }
    sel = Math.max(0, Math.min(all.length - 1, i));
    all.forEach(function (b, k) { b.classList.toggle('on', k === sel); b.setAttribute('aria-selected', String(k === sel)); });
    all[sel].scrollIntoView({block: 'nearest'});
  }
  function start(x) {
    tList.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
    tCopy.action = '/hwp/' + encodeURIComponent(x.id) + '/copy'; tCopy.submit();
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
