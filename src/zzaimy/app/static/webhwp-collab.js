/* 웹 한글 기안기 칸 단위 공동 편집 — 동기화 엔진(AuraCollab.engine)과 화면(AuraCollab.ui). ADR-0070.
 * 기안기 API 는 부르지 않는다 — 모두 어댑터(webhwp-wrapper.js)를 거친다.
 *
 * 엔진: 칸 임대(커서가 든 칸), 보낼 줄(outbox: 칸마다 마지막 글, seq·base, IndexedDB 에도), 받을 줄(inbox: 입력이 멈추면 넣기),
 * 알림 흐름(SSE /hwp/{id}/events), 판 번호, 30초 지문 대조, 충돌 사본, 저장 상태.
 * 서버가 정답이다 — 받은 글은 판 번호가 앞선 것만 넣고, 보낸 글은 서버 답(ack)을 받아야 보낼 줄에서 지운다.
 */
(function () {
  'use strict';
  var STRUCT = '';
  var BAD = /[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]|[\ud800-\udbff](?![\udc00-\udfff])|(^|[^\ud800-\udbff])[\udc00-\udfff]/g;
  function norm(s) { return String(s == null ? '' : s).replace(/\r\n/g, '\n').replace(/\r/g, '\n').replace(BAD, '$1').slice(0, 50000); }
  var CRC = (function () { var t = new Uint32Array(256); for (var n = 0; n < 256; n++) { var c = n; for (var k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
  function fp(s) {                       // 서버 webhwp_coedit.fingerprint 와 같은 셈(UTF-8 CRC32)
    var b = new TextEncoder().encode(norm(s)), c = 0xFFFFFFFF;
    for (var i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 0xFF] ^ (c >>> 8);
    return ('0000000' + ((c ^ 0xFFFFFFFF) >>> 0).toString(16)).slice(-8);
  }
  function now() { return Date.now(); }
  function clientId() {
    try { var v = sessionStorage.getItem('aura-coedit-client'); if (v) return v; } catch (e) {}
    var id = 'c' + Math.random().toString(36).slice(2, 10) + now().toString(36);
    try { sessionStorage.setItem('aura-coedit-client', id); } catch (e) {}
    return id;
  }

  // ── IndexedDB 보낼 줄 — 창이 닫히거나 끊겨도 다음에 열 때 다시 보낸다 ──
  function store() {
    var dbp = null;
    function open() {
      if (dbp) return dbp;
      dbp = new Promise(function (res) {
        try {
          var r = indexedDB.open('aura-coedit', 1);
          r.onupgradeneeded = function () { r.result.createObjectStore('outbox', {keyPath: 'key'}); r.result.createObjectStore('conflicts', {keyPath: 'key'}); };
          r.onsuccess = function () { res(r.result); };
          r.onerror = function () { res(null); };
        } catch (e) { res(null); }
      });
      return dbp;
    }
    function tx(name, mode, fn) {
      return open().then(function (db) {
        if (!db) return null;
        return new Promise(function (res) {
          try { var t = db.transaction(name, mode), s = t.objectStore(name), out = fn(s); t.oncomplete = function () { res(out && out.result !== undefined ? out.result : null); }; t.onerror = function () { res(null); }; }
          catch (e) { res(null); }
        });
      });
    }
    return {
      put: function (name, v) { return tx(name, 'readwrite', function (s) { return s.put(v); }); },
      del: function (name, key) { return tx(name, 'readwrite', function (s) { return s.delete(key); }); },
      all: function (name) { return tx(name, 'readonly', function (s) { return s.getAll(); }).then(function (x) { return x || []; }); }
    };
  }

  function engine(o) {
    var hwp = o.hwp, fid = o.fid, me = o.me, base = '/hwp/' + fid, client = clientId(), idb = store();
    var conf = {lease: 300, grace: 30, warn: 60, hash_check: 30, throttle_ms: 300, pause_ms: 300, presence_ttl: 30, rich: 0, aura_ttl: 20};
    var names = {}, known = {}, locks = {}, people = {}, mine = {};
    var outbox = {}, dirty = {}, inflight = false, flushTimer = null, lastSend = 0, seqN = 0;
    var inbox = {}, applyTimer = null, applying = false;
    var online = true, retryTimer = null, retryMs = 1000, es = null, lastId = 0, readonly = !!o.readonly;
    var fieldList = [], fieldSet = {}, curField = '', lastTouch = 0, lastPresence = 0, waiters = [], started = false;
    var handlers = {}, aura = {};
    function on(k, fn) { (handlers[k] = handlers[k] || []).push(fn); }
    function emit(k) { var a = [].slice.call(arguments, 1); (handlers[k] || []).forEach(function (fn) { try { fn.apply(null, a); } catch (e) { console.warn(e); } }); }
    function nameOf(u) { return u === 'AURA' ? 'AURA' : (names[u] || u); }
    function learn(d) { ['by', 'user', 'holder'].forEach(function (k) { if (d[k] && d[k + '_name']) names[d[k]] = d[k + '_name']; }); }
    function nextSeq() { seqN = Math.max(seqN + 1, now()); return seqN; }
    function post(path, body, extra) {
      return fetch(base + path, Object.assign({method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}, extra || {}))
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) { var e = new Error(j.detail || ('요청 실패(' + r.status + ')')); e.status = r.status; throw e; } return j; }); });
    }
    var fieldsOff = false;
    var fieldsMode = function () { return fieldList.length > 0 && !readonly && !fieldsOff; };
    function alive(lk) { return lk && lk.until * 1000 > now(); }
    // 남이 쥔 칸 — 다른 사람, 또는 같은 사람의 다른 창(창 번호가 다름)
    function heldByOther(f) { var lk = locks[f]; return alive(lk) && (lk.user !== me || (!!lk.client && lk.client !== client)); }
    function isMine(f) { var lk = locks[f]; return !!mine[f] && (!lk || (lk.user === me && (!lk.client || lk.client === client))); }
    function pending() { return Object.keys(outbox).length + Object.keys(dirty).length + (inflight ? 1 : 0); }
    function status() {
      var n = pending();
      if (!online) emit('status', 'offline', n ? '오프라인 — 연결되면 저장' : '오프라인');
      else if (n) emit('status', 'saving', '저장 중');
      else emit('status', 'saved', '저장됨');
      if (!n && online) { var w = waiters; waiters = []; w.forEach(function (fn) { fn(); }); }
    }

    // ── 편집 가능한 칸 — 남이 임대를 쥔 칸만 잠근다(빈 칸은 바로 쓸 수 있게 두고 임대는 뒤따라 잡는다) ──
    var editTimer = null;
    function applyEditable() {
      if (!fieldsMode()) return;
      if (hwp.isComposing() || hwp.isProbing()) {        // 조합 중에 칸 속성을 바꾸면 조합이 끊긴다(실측) — 조합이 끝나면
        clearTimeout(editTimer); editTimer = setTimeout(applyEditable, 120); return;
      }
      fieldList.forEach(function (f) { hwp.setEditable(f, !heldByOther(f)); });
    }

    // ── 내 고침 → 보낼 줄 ──
    function collect() {
      Object.keys(dirty).forEach(function (f) {
        delete dirty[f];
        var text = hwp.read(f);
        if (text === null) return;
        text = norm(text);
        var e = outbox[f], k = known[f];
        if (e ? e.text === text : (k && k.text === text)) return;
        var op = {key: fid + '|' + client + '|' + f, fid: fid, client: client, seq: nextSeq(), field: f, base: k ? k.v : 0, text: text, at: now()};
        outbox[f] = op;
        idb.put('outbox', op);
      });
    }
    function scheduleFlush() {
      if (flushTimer) return;
      flushTimer = setTimeout(flush, Math.max(0, lastSend + conf.throttle_ms - now()));
    }
    // 한글 조합 중에는 칸 글을 읽지 않는다 — 기안기는 조합 중에 글을 읽으면 조합을 끊어 자모가 따로 박힌다(실측). 조합이 끝나면 다시 부른다
    function flush() {
      flushTimer = null;
      if (hwp.isComposing()) { flushTimer = setTimeout(flush, 60); return; }
      collect(); send();
    }
    function send() {
      var ops = Object.keys(outbox).map(function (f) { return outbox[f]; });
      if (inflight) return;
      if (!ops.length) { status(); return; }
      inflight = true; lastSend = now(); status();
      var bySeq = {};
      ops.forEach(function (op) { bySeq[op.seq] = op; });
      post('/ops', {client: client, ops: ops.map(function (op) { var x = {seq: op.seq, field: op.field, base: op.base, text: op.text}; if (op.rich) x.rich = op.rich; return x; })})
        .then(function (j) {
          inflight = false; online = true; retryMs = 1000;
          (j.results || []).forEach(function (r) {
            var op = bySeq[r.seq], f = r.field;
            if (!op) return;
            if (outbox[f] && outbox[f].seq === r.seq) { delete outbox[f]; idb.del('outbox', op.key); }
            if (r.ok) {
              if (!known[f] || r.version >= known[f].v) known[f] = {v: r.version, text: op.text};
              if (conf.rich && RICH.test(f) && !op.rich) { richDirty[f] = true; scheduleRich(); }
              if (r.until && (!locks[f] || !heldByOther(f))) { mine[f] = r.until; locks[f] = {user: me, client: client, until: r.until}; }
            } else if (r.conflict !== undefined) {
              var copy = {key: fid + '|c' + r.conflict + '|' + op.seq, fid: fid, field: f, text: op.text, reason: r.reason, by: r.by_name || r.by || '', at: now(), id: r.conflict};
              idb.put('conflicts', copy);
              emit('conflict', copy);
              known[f] = {v: r.version, text: r.text};
              inbox[f] = {field: f, version: r.version, text: r.text, force: true};
              scheduleApply();
            }
          });
          if (Object.keys(outbox).length || Object.keys(dirty).length) scheduleFlush(); else status();
        })
        .catch(function (e) {
          inflight = false;
          if (e.status && e.status < 500 && e.status !== 408 && e.status !== 429) {   // 받지 않는 요청(권한 등) — 다시 보내도 같다. 보낼 줄은 남겨 둔다
            emit('error', e.message); online = true; status(); return;
          }
          online = false; status();
          clearTimeout(retryTimer);
          retryTimer = setTimeout(function () { send(); }, retryMs);
          retryMs = Math.min(retryMs * 2, 10000);
        });
    }
    // ── 서식째 주고받기(시험 기능, 설정 rich=1) — 절 필드만. 글은 위 길로 먼저 보내고, 입력이 1초 멈추면 기안기 JSON 블록을 따로 보낸다 ──
    var RICH = /^절:/, richDirty = {}, richTimer = null, lastRich = {};
    function scheduleRich() { clearTimeout(richTimer); richTimer = setTimeout(doRich, 1000); }
    function doRich() {
      if (hwp.isComposing() || hwp.isProbing() || now() - hwp.lastInputAt() < 1000 || pending()) { scheduleRich(); return; }
      var fs = Object.keys(richDirty);
      richDirty = {};
      (function next() {
        var f = fs.shift();
        if (!f) return;
        if (heldByOther(f)) { next(); return; }
        hwp.exportField(f).then(function (json) {
          if (json && json !== lastRich[f]) {
            lastRich[f] = json;
            var k = known[f], op = {key: fid + '|' + client + '|' + f, fid: fid, client: client, seq: nextSeq(), field: f, base: k ? k.v : 0,
                                    text: norm(hwp.read(f)), rich: json, at: now()};
            outbox[f] = op; idb.put('outbox', op); send();
          }
          next();
        });
      })();
    }
    function flushNow() {
      return new Promise(function (resolve) {
        clearTimeout(flushTimer); flushTimer = null;
        if (!hwp.isComposing()) collect(); else scheduleFlush();
        if (!pending()) { resolve(); return; }
        waiters.push(resolve); send();
      });
    }

    // ── 받은 글 → 입력이 멈추면 넣기(한글 조합이 깨지지 않게) ──
    function scheduleApply(delay) {
      if (applyTimer) return;
      applyTimer = setTimeout(applyInbox, delay || 0);
    }
    function applyInbox() {
      applyTimer = null;
      var keys = Object.keys(inbox);
      if (!keys.length) return;
      var idle = now() - hwp.lastInputAt();
      if (hwp.isComposing() || hwp.isProbing() || idle < conf.pause_ms) { scheduleApply(Math.max(30, conf.pause_ms - idle + 20)); return; }
      var cur = null, rich = [];
      keys.forEach(function (f) {
        var d = inbox[f]; delete inbox[f];
        if (!fieldSet[f]) { known[f] = {v: d.version, text: d.text}; return; }
        if (!d.force && known[f] && d.version <= known[f].v) return;
        if (dirty[f]) collect();                               // 내 고침을 먼저 보낼 줄에 — 서버가 충돌 사본으로 남긴다
        var local = hwp.read(f);
        if (d.rich && conf.rich) { rich.push(d); known[f] = {v: d.version, text: d.text}; return; }
        if (local !== null && norm(local) !== d.text) {
          cur = cur || hwp.getCursor();
          applying = true;
          hwp.write(f, d.text);
          applying = false;
        }
        known[f] = {v: d.version, text: d.text};
        emit('applied', f, d);
      });
      if (cur) hwp.restoreCursor(cur);
      if (Object.keys(outbox).length) scheduleFlush();
      (function next() {                                // 서식째 받은 절 — 기안기 JSON 블록으로 그 칸을 바꾼다. 안 되면 글만
        var d = rich.shift();
        if (!d) return;
        applying = true;
        hwp.importField(d.field, d.rich).then(function (ok) {
          if (!ok || norm(hwp.read(d.field)) !== d.text) hwp.write(d.field, d.text);
          applying = false; emit('applied', d.field, d); next();
        });
      })();
    }

    // ── 임대 ──
    // 임대는 칸에 커서를 둘 때가 아니라 처음 고칠 때 잡는다(구글 문서처럼 — 눌러 보기만 하면 남을 막지 않는다).
    // 서버는 이 창이 쥔 다른 칸을 함께 놓는다(한 창에 한 칸)
    var acquiring = {};
    function acquire(f, quiet) {
      if (acquiring[f]) return acquiring[f];
      acquiring[f] = post('/lease', {field: f, client: client, action: 'acquire'}).then(function (r) {
        if (r.ok) {
          Object.keys(mine).forEach(function (g) { if (g !== f) { delete mine[g]; if (locks[g] && locks[g].user === me && locks[g].client === client) delete locks[g]; } });
          mine[f] = r.until; locks[f] = {user: me, client: client, until: r.until, since: r.since};
          if (curField !== f && !hwp.isComposing()) setTimeout(function () { if (curField !== f) letGo(f, true); }, 0);   // 답을 받기 전에 이미 다른 칸으로 옮겼다
        } else {
          names[r.by] = r.by_name || names[r.by]; locks[f] = {user: r.by, client: r.client || '', until: r.until};
          if (!quiet) emit('blocked', f, holderLabel(f));
          if (dirty[f]) flush();
        }
        applyEditable(); emit('locks', locks);
        return r;
      }).catch(function () { return {ok: false}; }).then(function (r) { delete acquiring[f]; return r; });
      return acquiring[f];
    }
    function holderLabel(f) { var lk = locks[f]; if (!lk) return ''; return nameOf(lk.user); }
    function letGo(f, now_) {
      if (!mine[f]) return Promise.resolve();
      return flushNow().then(function () {
        return post('/lease', {field: f, client: client, action: now_ ? 'release' : 'grace'}).then(function () {
          if (now_) { delete mine[f]; if (locks[f] && locks[f].user === me && (!locks[f].client || locks[f].client === client)) delete locks[f]; applyEditable(); emit('locks', locks); }
        }).catch(function () {});
      });
    }
    function extend() {
      lastTouch = now();
      return post('/lease', {action: 'touch', client: client}).then(function (r) {
        Object.keys(r.until || {}).forEach(function (f) { mine[f] = r.until[f]; if (locks[f]) locks[f].until = r.until[f]; });
        emit('lease-ok');
      }).catch(function () {});
    }
    function checkCaret() {
      if (!started || hwp.isProbing() || hwp.isComposing()) return;
      var f = hwp.currentField();
      if (f === curField) return;
      var prev = curField;
      curField = f;
      presence(true);
      if (!fieldsMode()) return;
      if (prev && mine[prev]) letGo(prev, true);              // 다른 칸으로 옮기면 보낼 것을 보내고 바로 놓는다(한 사람 한 칸)
      if (f && fieldSet[f]) {
        hwp.setEditable(f, !heldByOther(f), true);             // 지금 칸은 상태를 다시 맞춘다(오래된 속성이 남아 막히지 않게)
        if (heldByOther(f)) {
          emit('blocked', f, holderLabel(f));
          if (locks[f].user !== me) acquire(f, true);         // 기다리는 사람으로 알린다(같은 사람 다른 창은 빼앗지 않는다)
        }
      }
      emit('caret', f);
    }
    function presence(force) {
      if (!force && now() - lastPresence < 8000) return;
      lastPresence = now();
      post('/presence', {client: client, field: curField}).then(function (j) {
        if (!j || !j.people) return;                  // 서버가 아는 같이 있는 사람으로 맞춘다(가만히 있는 사람도 남게)
        var seen = {};
        j.people.forEach(function (p) {
          if (p.name) names[p.user] = p.name;
          if (p.client === client && p.user === me) return;
          var k = p.user + '\u001f' + p.client; seen[k] = 1;
          people[k] = {user: p.user, field: p.field, at: now()};
        });
        Object.keys(people).forEach(function (k) { if (!seen[k]) delete people[k]; });
        emit('people', peopleList());
      }).catch(function () {});
    }

    // ── 알림 흐름 ──
    function connect() {
      if (es) es.close();
      es = new EventSource(base + '/events' + (lastId ? '?last=' + lastId : ''));
      var reopened = false;
      es.onopen = function () { if (reopened) { online = true; resync(false); send(); } reopened = false; };
      es.onerror = function () { reopened = true; if (es.readyState === 2) setTimeout(connect, 2000); };
      function h(kind, fn) { es.addEventListener(kind, function (ev) { lastId = Math.max(lastId, +ev.lastEventId || 0); var d = {}; try { d = JSON.parse(ev.data); } catch (e) {} learn(d); fn(d); }); }
      h('field-updated', function (d) {
        if (d.client === client && d.by === me) return;    // 내 창이 보낸 것(창 번호만으로는 남의 글을 놓칠 수 있어 사람도 본다)
        if (known[d.field] && d.version <= known[d.field].v) return;
        d.receivedAt = now();
        inbox[d.field] = d;
        emit('remote', d);
        scheduleApply();
      });
      h('lock-changed', function (d) {
        if (d.field === STRUCT) { emit('struct', d.user || ''); return; }
        if (d.user) {
          locks[d.field] = {user: d.user, client: d.client || '', until: d.until};
          if (mine[d.field] && heldByOther(d.field)) delete mine[d.field];      // 같은 사람 다른 창이 가져갔다
        } else {
          var was = locks[d.field];
          delete locks[d.field];
          if (d.released_by === me && (!d.client || d.client === client)) delete mine[d.field];
          if (curField === d.field && was && was.user !== me) emit('freed', d.field);   // 기다리던 칸이 풀렸다 — 이제 쓸 수 있다
        }
        applyEditable(); emit('locks', locks);
      });
      h('lock-wanted', function (d) { if (d.holder === me) emit('wanted', d.field, nameOf(d.by)); });
      h('presence', function (d) {
        var k = d.user + '\u001f' + d.client;          // 같이 보는 사람은 사람+창으로 가른다(서버도 그렇게 묶는다)
        if (d.left) delete people[k];
        else if (!(d.client === client && d.user === me)) people[k] = {user: d.user, field: d.field, at: now()};
        emit('people', peopleList());
      });
      h('doc-saved', function (d) { if (d.by !== me) emit('doc-saved', d); });
      h('aura', function (d) { setAura(d.field ? d : {}, d.ttl); });
    }
    var auraTimer = null;
    function setAura(a, ttl) {                       // AURA 작성 중 표시 — 마지막 쓰기 뒤 aura_ttl 초 지나면 저절로 지운다
      clearTimeout(auraTimer);
      aura = a && a.field ? a : {};
      if (aura.field) auraTimer = setTimeout(function () { aura = {}; emit('aura', aura); }, Math.max(1, ttl || conf.aura_ttl || 20) * 1000);
      emit('aura', aura);
    }
    function peopleList() {
      var by = {}, t = now() - conf.presence_ttl * 1000;
      Object.keys(people).forEach(function (c) { var p = people[c]; if (p.at >= t && p.user !== me) by[p.user] = {user: p.user, name: nameOf(p.user), field: p.field}; });
      return Object.keys(by).map(function (u) { return by[u]; });
    }

    // ── 처음·다시 이어질 때 — 서버 상태로 맞추기, 지문 대조 ──
    function resync(first) {
      if (!first && (hwp.isComposing() || now() - hwp.lastInputAt() < conf.pause_ms)) {   // 입력 중이면 조금 뒤에
        return new Promise(function (r) { setTimeout(function () { r(resync(false)); }, 1000); });
      }
      return fetch(base + '/coedit', {credentials: 'same-origin', cache: 'no-store'}).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }).then(function (s) {
        if (!first && hwp.isComposing()) { setTimeout(function () { resync(false); }, 800); return s; }
        online = true;
        Object.assign(names, s.names || {});
        Object.assign(conf, s.conf || {});
        readonly = !!s.readonly; fieldsOff = !!s.fields_off;
        if (first) { lastId = s.last || 0; setAura(s.aura || {}, s.aura && s.aura.at ? (conf.aura_ttl || 20) - ((s.now || now() / 1000) - s.aura.at) : 0); }
        var skew = (s.now || now() / 1000) - now() / 1000;
        locks = {};
        Object.keys(s.locks || {}).forEach(function (f) {
          var lk = s.locks[f]; lk.until -= skew;
          if (f === STRUCT) { emit('struct', lk.user); return; }
          locks[f] = lk;
          if (lk.user === me && (!lk.client || lk.client === client) && mine[f] === undefined) mine[f] = lk.until;
        });
        var t = now();
        (s.people || []).forEach(function (p) { if (!(p.client === client && p.user === me)) people[p.user + '\u001f' + p.client] = {user: p.user, field: p.field, at: t}; });
        var srv = s.fields || {}, local = hwp.readMany(fieldList.filter(function (f) { return srv[f]; }));
        fieldList.forEach(function (f) {
          var x = srv[f];
          if (!x) { if (!known[f]) known[f] = {v: 0, text: norm(hwp.read(f))}; return; }
          var k = known[f];
          if (!k || x.version > k.v) inbox[f] = {field: f, version: x.version, text: x.text, rich: x.rich, force: true};
          else if (x.version === k.v && !dirty[f] && !outbox[f] && fp(local[f]) !== x.fp) inbox[f] = {field: f, version: x.version, text: x.text, force: true};
        });
        applyEditable(); emit('locks', locks); emit('people', peopleList());
        scheduleApply(first ? 0 : undefined);
        status();
        return s;
      }).catch(function () { online = false; status(); });
    }
    function replay() {                                  // 지난번에 못 보낸 것(같은 브라우저) — 원래 client·seq 그대로 다시 보내 한 번만 반영
      return idb.all('outbox').then(function (rows) {
        var mineRows = rows.filter(function (r) { return r.fid === fid; });
        var groups = {};
        mineRows.forEach(function (r) { (groups[r.client] = groups[r.client] || []).push(r); });
        return Promise.all(Object.keys(groups).map(function (c) {
          if (c === client) { groups[c].forEach(function (r) { if (!outbox[r.field] || outbox[r.field].seq < r.seq) outbox[r.field] = r; }); return null; }
          return post('/ops', {client: c, ops: groups[c].map(function (r) { return {seq: r.seq, field: r.field, base: r.base, text: r.text}; })}).then(function (j) {
            (j.results || []).forEach(function (res, i) {
              var r = groups[c][i];
              if (!r) return;
              idb.del('outbox', r.key);
              if (res && res.ok === false && res.conflict !== undefined) emit('conflict', {field: r.field, text: r.text, reason: res.reason, by: res.by_name || res.by || ''});
            });
          }).catch(function () {});
        })).then(function () { send(); });
      });
    }

    function start() {
      hwp.attach();
      fieldList = readonly ? [] : hwp.fields();
      fieldSet = {}; fieldList.forEach(function (f) { fieldSet[f] = true; });
      hwp.onLocalEdit(function (ev) {
        if (applying || !fieldsMode()) return;
        // 조합 중에는 기안기에 아무것도 묻지 않는다(커서 칸 이름을 물어도 조합이 끊긴다 — 실측) — 마지막으로 안 커서 칸을 쓴다
        var f = ev && ev.composing ? curField : hwp.currentField();
        if (f !== curField && !(ev && ev.composing)) checkCaret();      // 커서 칸이 바뀐 것을 먼저 반영(앞 칸을 놓고 이 칸을 잡게)
        if (!f || !fieldSet[f]) { emit('outside-edit'); return; }
        if (heldByOther(f)) return;
        if (!mine[f]) acquire(f);                               // 처음 고칠 때 임대를 잡는다
        dirty[f] = true; status(); scheduleFlush();
        if (mine[f] && now() - lastTouch > 15000 && mine[f] * 1000 - now() < (conf.lease - 15) * 1000) extend();
      });
      hwp.onCaret(function () {
        checkCaret();
        // 서식 단추·Ctrl+B 같은 고침은 입력 사건이 없다 — 서식째 모드면 내 절 칸을 다시 재 본다(바뀐 것이 없으면 보내지 않는다)
        if (conf.rich && RICH.test(curField) && mine[curField]) { richDirty[curField] = true; scheduleRich(); }
      });
      hwp.onActivity(function () { if (curField && mine[curField] && now() - lastTouch > 20000) extend(); });
      setInterval(checkCaret, 700);
      setInterval(function () { presence(false); }, 5000);
      setInterval(function () { resync(false); }, Math.max(5, conf.hash_check) * 1000);
      setInterval(function () {                                   // 임대 시계 — 곧 풀리면 알리고, 지난 것은 지운다
        var t = now(), changed = false;
        Object.keys(locks).forEach(function (f) {
          if (alive(locks[f])) return;
          var was = locks[f].user; delete locks[f]; changed = true;
          if (f === curField && was !== me) emit('freed', f);
        });
        Object.keys(mine).forEach(function (f) {
          var left = mine[f] * 1000 - t;
          if (left <= 0) { delete mine[f]; emit('lease-ok'); }        // 쉬는 동안 풀렸다 — 다시 고치면 저절로 다시 잡는다(안내 없음)
          else if (f === curField && left < conf.warn * 1000) emit('lease-warn', f, Math.ceil(left / 1000));
        });
        if (changed) { applyEditable(); emit('locks', locks); }
      }, 1000);
      window.addEventListener('online', function () { online = true; send(); resync(false); });
      window.addEventListener('pagehide', function () {
        collect();
        var ops = Object.keys(outbox).map(function (f) { var x = outbox[f]; return {seq: x.seq, field: x.field, base: x.base, text: x.text}; });
        if (ops.length) { try { fetch(base + '/ops', {method: 'POST', keepalive: true, credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({client: client, ops: ops})}); } catch (e) {} }
        try { navigator.sendBeacon(base + '/leave?client=' + encodeURIComponent(client), new Blob([JSON.stringify({client: client})], {type: 'application/json'})); } catch (e) {}
      });
      return resync(true).then(function () { if (fieldsMode()) hwp.setFormMode(true); started = true; connect(); presence(true); checkCaret(); return replay(); });
    }
    // 새 판을 다시 연 뒤 — 필드 목록·편집 가능·서버 칸 글을 다시 맞춘다
    function reopened() {
      hwp.attach(); hwp.resetEditable();
      fieldList = readonly ? [] : hwp.fields();
      fieldSet = {}; fieldList.forEach(function (f) { fieldSet[f] = true; });
      known = {};
      if (fieldsMode()) hwp.setFormMode(!o.structureMode || !o.structureMode());
      return resync(true).then(function () { applyInbox(); });
    }
    return {
      start: start, on: on, emitLocal: emit, flush: flushNow, extend: extend, release: function (f) { return letGo(f || curField, true); },
      handover: function (f) { return letGo(f, true); }, reopened: reopened, resync: resync, applyEditable: applyEditable,
      client: client, fieldsMode: fieldsMode, hasField: function (f) { return !!fieldSet[f]; }, heldByOther: heldByOther, current: function () { return curField; }, locks: function () { return locks; },
      people: peopleList, nameOf: nameOf, mine: function () { return mine; }, known: function () { return known; },
      pending: pending, aura: function () { return aura; }, setReadOnly: function (v) { readonly = !!v; },
      conflicts: function () { return idb.all('conflicts').then(function (r) { return r.filter(function (x) { return x.fid === fid; }).sort(function (a, b) { return b.at - a.at; }); }); }
    };
  }

  // ── 화면 — 머리줄 아바타·칸 테두리·이름표·안내 띠 ──
  var PALETTE = ['#2563EB', '#16A34A', '#D97706', '#DB2777', '#7C3AED', '#0891B2', '#DC2626', '#4D7C0F'];
  function colorOf(u) { if (u === 'AURA') return '#1B3B75'; var h = 0; for (var i = 0; i < u.length; i++) h = (h * 31 + u.charCodeAt(i)) >>> 0; return PALETTE[h % PALETTE.length]; }
  function where(f) { return f ? f.replace(/^(절|칸):/, '').replace(/^표\d+:/, '표 · ') : ''; }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  function ui(eng, hwp, o) {
    var avatars = o.avatars, bar = o.bar, layer = el('div', 'co-layer'), marks = {}, geo = {}, geoAt = 0, geoWant = '';
    document.body.appendChild(layer);
    layer.setAttribute('aria-hidden', 'true');
    var MAX = 4, auraState = {}, popOpen = false, hideT = null;
    // 남이 쥔 칸 — 다른 사람, 또는 내 다른 창. 내 이 창이 쥔 칸은 표시하지 않는다
    function holderOf(f) {
      var lk = eng.locks()[f];
      if (!lk || lk.until * 1000 <= Date.now()) return null;
      if (lk.user !== o.me) return {user: lk.user, label: eng.nameOf(lk.user)};
      if (lk.client && lk.client !== eng.client) return {user: lk.user, label: eng.nameOf(lk.user), self: true};
      return null;
    }
    function auraLabel() {                             // AURA 는 시킨 사람에게 딸린다 — 「AURA · 김가람」
      return 'AURA' + (auraState.by ? ' · ' + eng.nameOf(auraState.by) : '');
    }
    function heldBy(u) {                               // 그 사람이 지금 쥔 칸(쓰는 중인 곳) — 알림이 늦어도 임대로 안다
      var L = eng.locks(), out = '';
      Object.keys(L).forEach(function (f) { var lk = L[f]; if (f && lk && lk.user === u && lk.until * 1000 > Date.now()) out = f; });
      return out;
    }
    function drawAvatars() {
      var list = eng.people().slice();
      if (auraState.field) list.unshift({user: 'AURA', name: auraLabel(), field: auraState.field});
      avatars.innerHTML = '';
      var myName = o.meName || eng.nameOf(o.me), meChip = el('span', 'co-av me', '나');   // 맨 끝 「나」 동그라미 — 올리면 「나 · 이름」
      meChip.style.setProperty('--c', colorOf(o.me)); meChip.title = '나 · ' + myName; meChip.setAttribute('aria-label', '나 · ' + myName);
      list.slice(0, MAX).forEach(function (p) {
        var b = el('button', 'co-av' + (p.user === 'AURA' ? ' aura' : ''));
        b.type = 'button';
        b.style.setProperty('--c', colorOf(p.user));
        b.textContent = p.user === 'AURA' ? 'A' : (p.name || p.user).slice(0, 1);
        var hf = p.user === 'AURA' ? p.field : heldBy(p.user); if (hf) p.field = hf;
        var editing = !!hf;
        var label = (p.name || p.user) + (p.field ? ' · ' + where(p.field) + (editing ? ' 쓰는 중' : ' 보는 중') : ' · 문서 보는 중');
        b.title = label; b.setAttribute('aria-label', label + (p.field && eng.hasField(p.field) ? ' — 눌러서 그 칸으로' : ''));
        if (editing) b.classList.add('editing');
        b.onclick = function () { if (p.field && eng.hasField(p.field)) hwp.moveTo(p.field, false); };
        avatars.appendChild(b);
      });
      if (list.length > MAX) {
        var m = el('button', 'co-av more', '+' + (list.length - MAX));
        m.type = 'button'; m.title = '같이 있는 사람 모두 보기';
        m.onclick = function (e) { e.stopPropagation(); popOpen = !popOpen; pop.hidden = !popOpen; };
        avatars.appendChild(m);
      }
      avatars.appendChild(meChip);
      // 같이 있는 사람 목록 — 동그라미 위에 마우스를 올리거나 「+N」 을 누르면. 줄을 누르면 그 사람 칸으로
      var pop = el('div', 'co-people'); pop.hidden = !popOpen;
      [{user: o.me, name: '나 · ' + myName, me: true}].concat(list).forEach(function (p) {
        var r = el('div', 'co-people-row'), d = el('span', 'dot', p.me ? '나' : p.user === 'AURA' ? 'A' : (p.name || p.user).slice(0, 1));
        d.style.setProperty('--c', colorOf(p.user));
        var hf2 = p.me ? '' : p.user === 'AURA' ? p.field : heldBy(p.user); if (hf2) p.field = hf2;
        var editing = !!hf2;
        r.appendChild(d); r.appendChild(el('span', '', p.name || p.user));
        r.appendChild(el('small', '', p.me ? '' : p.field ? where(p.field) + (editing ? ' 쓰는 중' : ' 보는 중') : '보는 중'));
        if (!p.me && p.field && eng.hasField(p.field)) r.onclick = function () { hwp.moveTo(p.field, false); popOpen = false; pop.hidden = true; };
        pop.appendChild(r);
      });
      avatars.appendChild(pop);
      avatars.onmouseenter = function () { clearTimeout(hideT); popOpen = true; pop.hidden = false; };
      avatars.onmouseleave = function () { hideT = setTimeout(function () { popOpen = false; var p2 = avatars.querySelector('.co-people'); if (p2) p2.hidden = true; }, 250); };
    }
    // 그릴 것: 남이 쥔 칸(테두리 + 왼쪽 여백 이름표), AURA 가 쓰는 칸, 남의 커서가 있는 칸(옅은 세로줄만)
    function wanted() {
      var out = {};
      Object.keys(eng.locks()).forEach(function (f) { var h = holderOf(f); if (h && eng.hasField(f)) out[f] = {kind: 'hold', user: h.user, label: h.label}; });
      if (auraState.field && eng.hasField(auraState.field) && !out[auraState.field]) out[auraState.field] = {kind: 'hold', user: 'AURA', label: auraLabel()};
      // 남의 커서만 있는 칸(보는 중)은 문서 위에 그리지 않는다 — 재려면 커서를 그 칸에 넣어야 해서 빈 칸 안내 글이 지워졌다(10-24). 아바타 풀이말로만 보인다
      return out;
    }
    // 쪽 좌표 재기 — 기안기 내부 커서 모델로 같은 작업 단위 안에서(화면 커서·스크롤은 그대로). 입력·조합 중이면 미룬다
    function measure(force) {
      var want = Object.keys(wanted()), key = want.join('\u0002');
      if (!want.length) { geo = {}; geoWant = ''; return; }
      if (!force && key === geoWant && Date.now() - geoAt < 4000) return;
      if (hwp.isComposing() || Date.now() - hwp.lastInputAt() < 1500) return;   // 누르거나 치는 중에는 재지 않는다 — 재는 동안 커서를 옮겼다 되돌려 막 누른 자리를 덮었다
      want = want.filter(function (f) { var t = hwp.read(f); return t != null && t !== ''; });   // 빈 칸은 재지 않는다(커서가 들어가면 기안기 안내 글이 지워진다)
      if (!want.length) { geo = {}; geoWant = key; geoAt = Date.now(); return; }
      var g = hwp.fieldGeom(want);
      if (!g) return;
      geo = g; geoAt = Date.now(); geoWant = key;
    }
    function mark(f) {
      var m = marks[f];
      if (!m) {
        m = marks[f] = {box: el('div', 'co-box'), caret: el('div', 'co-caret'), flag: el('div', 'co-flag'), block: el('div', 'co-block')};
        m.flag.appendChild(el('span', 'co-flag-t'));
        // 남이 쓰는 칸을 누르면 기안기가 커서를 앞 칸으로 돌려놓아 친 글이 엉뚱한 칸에 들어갔다(10-24 녹화) — 칸 위에 투명 막을 두어 누름을 받고 알린다
        m.block.addEventListener('pointerdown', function (e) {
          e.preventDefault(); e.stopPropagation();
          var h = holderOf(f);
          eng.emitLocal('blocked', f, h ? h.label : '다른 사람');
          try { var fr = document.querySelector('iframe'); if (fr) fr.blur(); if (document.activeElement) document.activeElement.blur(); } catch (x) {}
        });
        layer.appendChild(m.box); layer.appendChild(m.block); layer.appendChild(m.caret); layer.appendChild(m.flag);
      }
      return m;
    }
    // 구글 독스처럼 — 그 사람이 쓰는 칸은 옅은 색 바탕, 끝에 색 세로줄(커서), 그 위에 이름 깃발. 글자는 가리지 않는다(바탕은 반투명)
    function render() {
      var want = wanted(), seen = {}, view = null;
      Object.keys(want).forEach(function (f) {
        var g = geo[f], w = want[f];
        if (!g) return;
        var a = hwp.toScreen(g.page, g.x, g.y), b = hwp.toScreen(g.page2, g.x2, g.y2);
        if (!a || !b) return;
        view = a.view;
        var h1 = Math.max(10, hwp.unitPx(g.h)), h2 = Math.max(10, hwp.unitPx(g.h2)), m = mark(f), c = colorOf(w.user);
        seen[f] = 1;
        var top = a.y, bottom = b.y + h2, single = g.page === g.page2 && Math.abs(b.y - a.y) < 2;
        var vis = bottom > view.top && top < view.bottom;
        m.box.hidden = m.caret.hidden = m.flag.hidden = m.block.hidden = !vis;
        if (!vis) return;
        [m.box, m.caret, m.flag].forEach(function (x) { x.style.setProperty('--c', c); });
        var L = layer.getBoundingClientRect();
        m.box.title = w.label + ' · ' + where(f);
        if (single) {                                   // 한 줄 — 칸 글 위에 옅은 바탕
          m.box.className = 'co-box hl';
          m.box.style.left = (a.x - 1 - L.left) + 'px'; m.box.style.top = (top - L.top) + 'px';
          m.box.style.width = Math.max(4, b.x - a.x + 2) + 'px'; m.box.style.height = h1 + 'px';
        } else {                                        // 여러 줄 — 칸 왼쪽에 옅은 막대(글 위를 덮지 않게)
          m.box.className = 'co-box multi';
          m.box.style.left = (a.x - 7 - L.left) + 'px'; m.box.style.top = (top - L.top) + 'px';
          m.box.style.width = '2px'; m.box.style.height = Math.max(h1, bottom - top) + 'px';
        }
        m.block.hidden = false;                         // 누름 막 — 칸 글 자리 전체(여러 줄이면 종이 글 폭까지)
        var bl = a.x - 4, br = single ? Math.max(b.x, a.x + 8) + 4 : view.right - (a.x - view.left) + 4;
        m.block.style.left = (bl - L.left) + 'px'; m.block.style.top = (top - 2 - L.top) + 'px';
        m.block.style.width = Math.max(12, br - bl) + 'px'; m.block.style.height = (Math.max(h1, bottom - top) + 4) + 'px';
        m.block.title = w.label + ' 님이 쓰는 중';
        // 커서 — 칸 끝(그 사람이 이어 쓰는 자리)
        m.caret.style.left = (b.x - L.left) + 'px'; m.caret.style.top = (b.y - L.top) + 'px'; m.caret.style.height = h2 + 'px';
        // 이름 깃발 — 커서 바로 위. 종이 오른쪽 끝을 넘으면 왼쪽으로 붙인다
        // 이름표 — 커서가 있는 줄 높이에, 종이 여백(글이 없는 곳) 중 커서에 가까운 쪽에. 글 위에는 놓지 않는다. 여백이 좁으면 머리 글자 동그라미
        var tt = m.flag.firstChild, edge = hwp.toScreen(g.page2, hwp.bodyLeft(), g.y2), bodyL = edge ? edge.x : view.left + 40;
        var bodyR = view.right - (bodyL - view.left);          // 좌우 여백이 같다고 본다
        var leftSide = (b.x - bodyL) < (bodyR - b.x);
        tt.textContent = w.label;
        m.flag.className = 'co-flag ' + (leftSide ? 'left' : 'right');
        m.flag.title = w.label + ' · ' + where(f) + (w.user === 'AURA' ? '' : ' 쓰는 중');
        var room = leftSide ? bodyL - view.left - 10 : view.right - bodyR - 10;
        if (m.flag.offsetWidth > room) { m.flag.classList.add('dot'); tt.textContent = w.user === 'AURA' ? 'A' : w.label.slice(0, 1); }
        var fw = m.flag.offsetWidth, fh = m.flag.offsetHeight;
        var fx = leftSide ? bodyL - 6 - fw : bodyR + 6;
        m.flag.style.left = (fx - L.left) + 'px'; m.flag.style.top = (b.y + Math.max(0, (h2 - fh) / 2) - L.top) + 'px';
      });
      Object.keys(marks).forEach(function (f) { if (!seen[f]) { marks[f].box.remove(); marks[f].caret.remove(); marks[f].flag.remove(); marks[f].block.remove(); delete marks[f]; } });
      if (view) {                                       // 종이 칸 밖(머리줄·스크롤 막대)에는 그리지 않는다
        layer.style.left = view.left + 'px'; layer.style.top = view.top + 'px';
        layer.style.width = (view.right - view.left) + 'px'; layer.style.height = (view.bottom - view.top) + 'px';
      }
    }
    function tick() { if (hwp.isComposing()) return; try { measure(false); render(); } catch (e) {} }   // 한글 조합 중에는 손대지 않는다
    setInterval(tick, 250);                              // 화면 좌표는 매번 다시(스크롤·확대·창 크기), 쪽 좌표는 바뀔 때·4초마다
    function soon() { geoWant = ''; setTimeout(tick, 0); }
    eng.on('locks', function () { drawAvatars(); soon(); });
    eng.on('people', function () { drawAvatars(); soon(); });
    eng.on('aura', function (a) { auraState = a || {}; drawAvatars(); soon(); });
    eng.on('applied', soon);
    hwp.onLocalEdit(function () { geoWant = ''; });

    // 안내 띠 — 한 번에 하나, 단추는 키보드로도(Esc 로 닫기)
    var barKind = '', barTimer = null;
    function show(kind, text, buttons, ms) {               // ms 를 주면 그 뒤 저절로 닫힌다
      barKind = kind;
      clearTimeout(barTimer);
      bar.innerHTML = '';
      bar.appendChild(el('span', 'co-bar-text', text));
      (buttons || []).forEach(function (b) { var x = el('button', 'co-btn', b[0]); x.type = 'button'; x.onclick = b[1]; bar.appendChild(x); });
      var close = el('button', 'co-btn ghost', '닫기'); close.type = 'button'; close.onclick = function () { hide(); }; bar.appendChild(close);
      bar.hidden = false;
      if (ms) barTimer = setTimeout(function () { hide(kind); }, ms);
    }
    function hide(kind) { if (typeof kind === 'string' && kind !== barKind) return; clearTimeout(barTimer); bar.hidden = true; barKind = ''; }
    bar.addEventListener('keydown', function (e) { if (e.key === 'Escape') { e.stopPropagation(); hide(); } });
    eng.on('lease-warn', function (f, left) {
      if (barKind !== 'warn' && barKind) return;
      show('warn', '「' + where(f) + '」 칸을 ' + left + '초 동안 고치지 않으면 다른 사람도 쓸 수 있게 풀려요', [['계속 쓰기', function () { eng.extend(); hide('warn'); }]]);
    });
    eng.on('lease-ok', function () { hide('warn'); });
    eng.on('blocked', function (f, who) {
      show('blocked', '「' + where(f) + '」 칸은 ' + who + ' 님이 쓰는 중이에요. 끝나면 바로 쓸 수 있어요', null, 6000);
    });
    eng.on('caret', function (f) { if (barKind === 'blocked' && !holderOf(f)) hide('blocked'); if (barKind === 'warn') hide('warn'); });
    eng.on('freed', function (f) { if (eng.current() === f) show('free', '「' + where(f) + '」 칸이 비었어요. 이제 쓸 수 있어요', null, 3000); });
    var wantedField = '';
    eng.on('locks', function () { var lk = eng.locks()[wantedField]; if (barKind === 'wanted' && (!lk || lk.user !== o.me || lk.client !== eng.client)) hide('wanted'); });
    eng.on('wanted', function (f, who) {
      wantedField = f;
      show('wanted', who + ' 님이 「' + where(f) + '」 칸을 쓰려고 기다려요', [['넘겨주기', function () { eng.handover(f); hide('wanted'); }]], 15000);
    });
    eng.on('conflict', function (c) {
      show('conflict', '「' + where(c.field) + '」 칸에 쓴 글이 ' + (c.by ? c.by + ' 님의 글과 ' : '') + '겹쳐 내 글은 사본으로 따로 두었어요', [['내 글 보기', function () { openConflicts(); }]]);
    });
    var panel = null;
    function openConflicts() {
      if (panel) { panel.remove(); panel = null; }
      eng.conflicts().then(function (list) {
        panel = el('div', 'co-panel'); panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', '충돌 사본');
        panel.appendChild(el('div', 'co-panel-h', '충돌로 따로 둔 내 글'));
        if (!list.length) panel.appendChild(el('div', 'co-panel-empty', '따로 둔 글이 없습니다'));
        list.slice(0, 20).forEach(function (c) {
          var row = el('div', 'co-panel-row');
          row.appendChild(el('div', 'co-panel-f', where(c.field) + ' · ' + new Date(c.at).toLocaleTimeString('ko-KR', {hour: '2-digit', minute: '2-digit'})));
          row.appendChild(el('pre', 'co-panel-t', c.text));
          var cp = el('button', 'co-btn', '복사'); cp.type = 'button';
          cp.onclick = function () { try { navigator.clipboard.writeText(c.text); cp.textContent = '복사됨'; } catch (e) {} };
          row.appendChild(cp); panel.appendChild(row);
        });
        var x = el('button', 'co-btn ghost', '닫기'); x.type = 'button'; x.onclick = function () { panel.remove(); panel = null; }; panel.appendChild(x);
        panel.addEventListener('keydown', function (e) { if (e.key === 'Escape') { panel.remove(); panel = null; } });
        document.body.appendChild(panel); x.focus();
      });
    }
    return {drawAvatars: drawAvatars, refresh: soon, show: show, hide: hide, openConflicts: openConflicts, colorOf: colorOf};
  }

  window.AuraCollab = {engine: engine, ui: ui, fp: fp, norm: norm, colorOf: colorOf};
})();
