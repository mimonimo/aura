/* 사람 고르기 — 입력칸 하나 + 칩. 여러 화면이 같이 쓰는 부품.
 *
 * 쓰는 법:
 *   <div data-people-picker data-name="leads" data-grade="lead" data-scope="dept"
 *        data-selected='[{"user":"kim","label":"김교수"}]' data-placeholder="이름·아이디·부서로 찾기"></div>
 *   <script src="/static/people-picker.js"></script>   (people-picker.css 도 함께)
 *   PeoplePicker.mountAll(root)  — data-people-picker 를 모두 붙인다(창을 다시 채울 때 PeoplePicker.set(el, 고른 사람들))
 *
 *   data-name     폼에 보낼 이름 — 고른 사람마다 <input type=hidden name=… value=아이디>
 *   data-grade    그 등급 이상만(clerk·lead·head, 비우면 모두) — 서버 /api/people 가 다시 거른다
 *   data-scope    dept 면 「우리 부서」가 기본으로 켜진다(「모든 부서」 칩으로 넓힘)
 *   data-single   한 사람만
 *   data-endpoint 찾기 주소(기본 /api/people) — 응답 {people:[{user,name,dept,grade_label}], depts:[…], my_dept}
 *   data-kind     dept 를 넣으면 부서도 고른다(값 「dept:부서」, 칩은 「부서(부서)」) — 그 부서와 상위 부서의 총괄 책임자들을 뜻한다
 * 고른 사람은 저장할 때 서버가 등급·부서를 다시 검사한다. 최근 고른 사람은 브라우저에 기억한다(막혀 있으면 건너뜀).
 */
(function () {
  var RECENT = 'pp.recent';
  function esc(t) { var d = document.createElement('div'); d.textContent = t == null ? '' : String(t); return d.innerHTML; }
  function recent() { try { return JSON.parse(localStorage.getItem(RECENT) || '[]') || []; } catch (e) { return []; } }
  function remember(p) {
    try {
      var r = recent().filter(function (x) { return x.user !== p.user; });
      r.unshift({user: p.user, name: p.name, dept: p.dept, grade_label: p.grade_label});
      localStorage.setItem(RECENT, JSON.stringify(r.slice(0, 8)));
    } catch (e) {}
  }
  function initial(n) { return (String(n || '?').trim()[0] || '?').toUpperCase(); }

  function mount(el) {
    if (el._pp) return el._pp;
    var name = el.dataset.name || 'people', grade = el.dataset.grade || '', single = el.hasAttribute('data-single');
    var endpoint = el.dataset.endpoint || '/api/people';
    var mineOnly = el.dataset.scope === 'dept', myDept = '';
    var sel = [];
    try { sel = JSON.parse(el.dataset.selected || '[]') || []; } catch (e) { sel = []; }
    el.classList.add('pp');
    el.innerHTML = '<div class="pp-box"><span class="pp-chips"></span><input type="text" class="pp-in" autocomplete="off" '
      + 'aria-autocomplete="list" role="combobox" aria-expanded="false" placeholder="' + esc(el.dataset.placeholder || '이름·아이디·부서로 찾기') + '"></div>'
      + '<div class="pp-pop" role="listbox" hidden></div><span class="pp-hidden"></span>';
    var box = el.querySelector('.pp-box'), chips = el.querySelector('.pp-chips'), input = el.querySelector('.pp-in');
    var pop = el.querySelector('.pp-pop'), hid = el.querySelector('.pp-hidden');
    var items = [], cur = -1, timer = null, seq = 0;

    function paint() {
      chips.innerHTML = sel.map(function (p, i) {
        return '<span class="pp-chip" title="' + esc([p.dept, p.grade_label].filter(Boolean).join(' · ')) + '">' + esc(p.label || p.name || p.user)
          + '<button type="button" aria-label="' + esc(p.label || p.user) + ' 빼기" data-i="' + i + '">×</button></span>';
      }).join('');
      hid.innerHTML = sel.map(function (p) { return '<input type="hidden" name="' + esc(name) + '" value="' + esc(p.user) + '">'; }).join('');
      input.placeholder = sel.length && single ? '' : (el.dataset.placeholder || '이름·아이디·부서로 찾기');
      el.dispatchEvent(new CustomEvent('pp:change', {bubbles: true, detail: sel.slice()}));
    }
    function close() { pop.hidden = true; input.setAttribute('aria-expanded', 'false'); cur = -1; }
    function add(p) {
      if (!sel.some(function (x) { return x.user === p.user; })) {
        if (single) sel = [];
        sel.push({user: p.user, label: p.kind === 'dept' ? p.name + '(부서)' : (p.name || p.user), dept: p.dept, grade_label: p.grade_label});
        remember(p);
      }
      input.value = ''; paint(); close(); input.focus();
    }
    function row(p, i) {
      return '<div class="pp-opt' + (i === cur ? ' on' : '') + '" role="option" data-i="' + i + '"><span class="pp-av">' + esc(initial(p.name)) + '</span>'
        + '<span class="pp-nm">' + esc(p.name) + '</span><span class="pp-meta">' + esc([p.dept || '부서 없음', p.grade_label].filter(Boolean).join(' · ')) + '</span></div>';
    }
    function render(list, q) {
      var taken = sel.map(function (x) { return x.user; });
      items = list.filter(function (p) { return taken.indexOf(p.user) < 0; });
      var h = '<div class="pp-quick">' + (myDept ? '<button type="button" class="pp-q' + (mineOnly ? ' on' : '') + '" data-q="mine">우리 부서</button>'
        + '<button type="button" class="pp-q' + (!mineOnly ? ' on' : '') + '" data-q="all">모든 부서</button>' : '')
        + (grade ? '<span class="pp-rule">' + esc({clerk: '행정 담당자 이상', lead: '사업 책임자 이상', head: '총괄 책임자'}[grade] || '') + '</span>' : '') + '</div>';
      if (!q) {
        var rc = recent().filter(function (p) { return items.some(function (x) { return x.user === p.user; }); }).slice(0, 4);
        if (rc.length) h += '<div class="pp-group">최근 고른 사람</div>' + rc.map(function (p) { return row(p, items.findIndex(function (x) { return x.user === p.user; })); }).join('');
      }
      if (!items.length) h += '<p class="pp-none">찾는 사람이 없습니다 · 등급이 맞는지 확인</p>';
      var last = null;
      items.forEach(function (p, i) {
        var d = p.dept || '부서 없음';
        if (d !== last) { h += '<div class="pp-group">' + esc(d) + '</div>'; last = d; }
        h += p.kind === 'dept' ? row({name: p.name, dept: '', grade_label: '부서 지정'}, i).replace('pp-av">', 'pp-av" style="border-radius:5px;">') : row(p, i);
      });
      pop.innerHTML = h; pop.hidden = false; input.setAttribute('aria-expanded', 'true');
    }
    function search() {
      var q = input.value.trim(), my = ++seq;
      var u = endpoint + '?limit=30&q=' + encodeURIComponent(q) + (grade ? '&grade=' + encodeURIComponent(grade) : '') + (el.dataset.kind ? '&kind=' + encodeURIComponent(el.dataset.kind) : '')
        + (mineOnly && myDept ? '&dept=' + encodeURIComponent(myDept) : '');
      fetch(u, {headers: {Accept: 'application/json'}}).then(function (r) { if (!r.ok) throw r; return r.json(); }).then(function (d) {
        if (my !== seq) return;
        if (!myDept && d.my_dept) { myDept = d.my_dept; if (mineOnly) { search(); return; } }
        var list = (d.depts || []).concat((d.people || []).slice().sort(function (a, b) { return (a.dept || '').localeCompare(b.dept || '') || a.name.localeCompare(b.name); }));
        cur = list.length ? 0 : -1; render(list, q);
      }).catch(function () { pop.innerHTML = '<p class="pp-none">사람 목록을 불러오지 못했습니다</p>'; pop.hidden = false; });
    }
    input.addEventListener('focus', search);
    input.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(search, 120); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault(); if (pop.hidden) { search(); return; }
        cur = Math.max(0, Math.min(items.length - 1, cur + (e.key === 'ArrowDown' ? 1 : -1)));
        pop.querySelectorAll('.pp-opt').forEach(function (o) { o.classList.toggle('on', +o.dataset.i === cur); if (+o.dataset.i === cur) o.scrollIntoView({block: 'nearest'}); });
      } else if (e.key === 'Enter') {
        if (!pop.hidden && items[cur]) { e.preventDefault(); e.stopPropagation(); add(items[cur]); }
      } else if (e.key === 'Backspace' && !input.value && sel.length) {
        sel.pop(); paint();
      } else if (e.key === 'Escape' && !pop.hidden) {
        e.stopPropagation(); close();
      }
    });
    pop.addEventListener('mousedown', function (e) { e.preventDefault(); });
    pop.addEventListener('click', function (e) {
      var q = e.target.closest('.pp-q');
      if (q) { mineOnly = q.dataset.q === 'mine'; search(); return; }
      var o = e.target.closest('.pp-opt'); if (o && items[+o.dataset.i]) add(items[+o.dataset.i]);
    });
    chips.addEventListener('click', function (e) {
      var b = e.target.closest('button[data-i]'); if (!b) return;
      sel.splice(+b.dataset.i, 1); paint(); input.focus();
    });
    box.addEventListener('click', function (e) { if (e.target === box || e.target === chips) input.focus(); });
    input.addEventListener('blur', function () { setTimeout(close, 120); });
    el._pp = {set: function (list) { sel = (list || []).map(function (p) { return {user: p.user, label: p.label || p.name || p.user, dept: p.dept, grade_label: p.grade_label}; }); paint(); },
              get: function () { return sel.slice(); }};
    paint();
    return el._pp;
  }
  window.PeoplePicker = {
    mount: mount,
    mountAll: function (root) { (root || document).querySelectorAll('[data-people-picker]').forEach(mount); },
    set: function (el, list) { mount(el).set(list); }
  };
  document.addEventListener('DOMContentLoaded', function () { window.PeoplePicker.mountAll(document); });
})();
