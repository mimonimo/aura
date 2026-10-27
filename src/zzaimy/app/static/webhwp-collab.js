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
  // 자르지 않는다 — 예전 50,000자 자르기는 30쪽 넘는 절에서 칸 끝 글을 말없이 버렸다(ADR-0070). 상한은 서버 설정 field_max 로 보내기 전에 알린다
  function norm(s) { return String(s == null ? '' : s).replace(/\r\n/g, '\n').replace(/\r/g, '\n').replace(BAD, '$1'); }
  var CRC = (function () { var t = new Uint32Array(256); for (var n = 0; n < 256; n++) { var c = n; for (var k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
  function fp(s) {                       // 서버 webhwp_coedit.fingerprint 와 같은 셈(UTF-8 CRC32)
    var b = new TextEncoder().encode(norm(s)), c = 0xFFFFFFFF;
    for (var i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 0xFF] ^ (c >>> 8);
    return ('0000000' + ((c ^ 0xFFFFFFFF) >>> 0).toString(16)).slice(-8);
  }
  // ── 바뀐 부분(delta) — 앞뒤 같은 부분을 뺀 가운데. 서버(파이썬)는 글자(코드 포인트)로 세므로 자리는 코드 포인트로 주고받는다 ──
  var SUR = /[\ud800-\udfff]/;
  function diffText(a, b) {
    var n = Math.min(a.length, b.length), i = 0, j = 0;
    while (i < n && a.charCodeAt(i) === b.charCodeAt(i)) i++;
    while (j < n - i && a.charCodeAt(a.length - 1 - j) === b.charCodeAt(b.length - 1 - j)) j++;
    if (i > 0 && /[\ud800-\udbff]/.test(a.charAt(i - 1))) i--;                 // 대리 쌍을 가르지 않게
    if (j > 0 && /[\udc00-\udfff]/.test(a.charAt(a.length - j))) j--;
    return {at: i, del: a.length - i - j, ins: b.slice(i, b.length - j)};
  }
  function cpLen(s) { return SUR.test(s) ? Array.from(s).length : s.length; }
  function u16At(s, cp) { if (!SUR.test(s)) return cp; var i = 0, k = 0; while (k < cp && i < s.length) { var c = s.charCodeAt(i); i += (c >= 0xD800 && c <= 0xDBFF) ? 2 : 1; k++; } return i; }
  // 받은 바뀐 부분을 글에 — 자리·길이가 글 밖이면(잘못된 알림) null, 부르는 쪽이 칸 글 전체를 다시 받는다
  function applyStr(s, d) {
    if (typeof d.ins !== 'string' || !(d.at >= 0) || !(d.del >= 0) || d.at + d.del > cpLen(s)) return null;
    var a = u16At(s, d.at), b = u16At(s.slice(a), d.del) + a; return s.slice(0, a) + d.ins + s.slice(b);
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
    var conf = {lease: 300, grace: 30, warn: 60, hash_check: 30, throttle_ms: 300, pause_ms: 300, presence_ttl: 30, rich: 1, aura_ttl: 20, form_mode: 0, field_max: 1000000};
    var tooLong = {};
    var names = {}, known = {}, locks = {}, people = {}, mine = {};
    var outbox = {}, dirty = {}, inflight = false, flushTimer = null, lastSend = 0, seqN = 0;
    var lastEdit = {f: "", at: 0}, typedAt = 0;                        // 마지막으로 고친 칸 — 확정 때 바로 보내기·빈 칸 경계 처리에 쓴다
    var inbox = {}, applyTimer = null, applying = false;
    var settled = {};   // 지문이 달라 서버 글로 바로잡은 칸의 판 — 같은 판은 한 번만(표·특수 문자로 넣어도 지문이 끝내 안 맞는 칸을 30초마다 다시 넣어 화면이 얼던 것, 10-27 긴 양식)
    var online = true, retryTimer = null, retryMs = 1000, es = null, lastId = 0, readonly = !!o.readonly;
    var fieldList = [], fieldSet = {}, curField = '', lastTouch = 0, lastPresence = 0, waiters = [], started = false;
    var reloading = false;   // 새 판을 다시 여는 동안(suspend → reopened) — 칸 수가 바뀐 것을 칸 밖 고침으로 잘못 알아채지 않게(10-26 양식 불러오기 뒤 저절로 문서 잠금)
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
    // 내 다른 창이 쥔 칸도 남의 칸이다 — 다만 그 창이 닫히거나 멈춰(같이 있는 사람 신호가 끊김) 살아 있지 않으면 이 창이 이어받는다.
    // 예전에는 갑자기 닫힌 창의 임대가 남아 같은 사람의 새 창도 그 칸을 5분 동안 못 썼다
    var myWindows = null;                      // 서버가 아는 내 살아 있는 창 번호(presence 답) — 모르면(null) 막아 둔다
    function otherWindowGone(c) { return myWindows !== null && !myWindows[c]; }
    function heldByOther(f) {
      var lk = locks[f];
      if (!alive(lk)) return false;
      if (lk.user !== me) return true;
      return !!lk.client && lk.client !== client && !otherWindowGone(lk.client);
    }
    function learnWindows(list) { if (!list) return; myWindows = {}; list.forEach(function (w) { if (w.user === me) myWindows[w.client] = 1; }); }
    function isMine(f) { var lk = locks[f]; return !!mine[f] && (!lk || (lk.user === me && (!lk.client || lk.client === client))); }
    function pending() { return Object.keys(outbox).length + Object.keys(dirty).length + (inflight ? 1 : 0); }
    // 연결 끊김 — 배포로 서버가 잠깐 내려가면(10~20초) 「오프라인」 이 떠 동기화가 망가진 줄 알았다(10-26). 처음 OFF_GRACE 동안은
    // 「다시 연결하는 중」 으로만 보이고, 그사이 몇 초마다 서버를 다시 찔러 보고(probe) 닿으면 못 보낸 글을 보내 「저장됨」 으로 돌아온다
    var offSince = 0, offTimer = null, OFF_GRACE = 20000;
    function probe() {
      if (online) return;
      resync(false, true).then(function () { if (online && pending() && !inflight) send(); });
    }
    function status() {
      var n = pending();
      clearTimeout(offTimer);
      if (!online) {
        if (!offSince) offSince = now();
        var long = now() - offSince > OFF_GRACE;
        if (long) emit('status', 'offline', n ? '오프라인 — 연결되면 저장' : '오프라인 — 다시 연결하는 중');
        else emit('status', 'reconnecting', '다시 연결하는 중');
        offTimer = setTimeout(probe, long ? 5000 : 2000);
        return;
      }
      offSince = 0;
      if (n) emit('status', 'saving', '저장 중');
      else emit('status', 'saved', '저장됨');
      if (!n) { var w = waiters; waiters = []; w.forEach(function (fn) { fn(); }); }
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
        if (text.length > conf.field_max) {                    // 상한을 넘는 글은 보내지 않고 알린다(잘라 보내면 끝 글이 사라진다)
          if (!tooLong[f]) { tooLong[f] = 1; emit('error', '「' + f.replace(/^(절|칸):/, '') + '」 칸은 ' + conf.field_max.toLocaleString() + '자까지 같이 쓸 수 있어요 — 넘는 동안은 저장되지 않아요'); }
          return;
        }
        delete tooLong[f];
        var e = outbox[f], k = known[f];
        if (e ? e.text === text : (k && k.text === text)) {
          // 글자를 쳤는데 이 칸 글이 그대로 — 친 글이 칸 밖(칸 시작 표시 앞 등)에 들어갔다. 칸 길로는 못 보내므로 잠시 뒤 다시 보고 그래도 같으면
          // 구조 고침(전체 저장 — 서버가 그 글을 칸으로 감쌈)으로 넘긴다(10-26 실측: 빈 줄 왼쪽 끝에 친 글이 다른 화면에 가지 않았다)
          if (now() - typedAt < 1500 && !e) {
            var f0 = f, t0 = text;
            setTimeout(function () {
              if (hwp.isComposing() || dirty[f0] || outbox[f0] || applying) return;
              var t1 = hwp.read(f0);
              if (t1 !== null && norm(t1) === t0 && known[f0] && known[f0].text === t0) emit('outside-edit');
            }, 600);
          }
          return;
        }
        touched[f] = 1;
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
      // 조합 중에는 칸을 읽지 않지만(자모가 깨진다), 확정 때 이미 담아 둔 글은 기다리지 않고 보낸다 — 한글은 쉬지 않고 치면 조합이 끝없이 이어진다
      if (hwp.isComposing()) { send(); flushTimer = setTimeout(flush, 60); return; }
      collect(); send();
    }
    function send() {
      var ops = Object.keys(outbox).map(function (f) { return outbox[f]; });
      if (inflight) return;
      if (!ops.length) { status(); return; }
      inflight = true; lastSend = now(); status();
      var bySeq = {};
      ops.forEach(function (op) { bySeq[op.seq] = op; });
      // 보내는 것은 바뀐 부분만 — 서버가 안 받으면(판 어긋남·지문 다름) 그 칸만 전체 글로 다시(op.full)
      post('/ops', {client: client, ops: ops.map(function (op) {
        var k = known[op.field];
        if (!op.full && !op.rich && k && typeof k.text === 'string') {
          var d = diffText(k.text, op.text);
          if (d.del + d.ins.length < op.text.length) {
            var pre = k.text.slice(0, d.at);
            return {seq: op.seq, field: op.field, base: k.v, at: cpLen(pre), del: cpLen(k.text.slice(d.at, d.at + d.del)), ins: d.ins, fp: fp(op.text)};
          }
        }
        var x = {seq: op.seq, field: op.field, base: op.base, text: op.text}; if (op.rich) x.rich = op.rich; return x;
      })})
        .then(function (j) {
          inflight = false; online = true; retryMs = 1000;
          (j.results || []).forEach(function (r) {
            var op = bySeq[r.seq], f = r.field;
            if (!op) return;
            if (outbox[f] && outbox[f].seq === r.seq) { delete outbox[f]; idb.del('outbox', op.key); }
            if (r.ok) {
              if (!known[f] || r.version >= known[f].v) known[f] = {v: r.version, text: op.text};
              if (op.rich) { lastRich[f] = richSig(op.rich); emit('rich-sent', f); }   // 서식째 보냄 — 화면이 파일에도 조용히 저장한다
              if (r.until && (!locks[f] || !heldByOther(f))) { mine[f] = r.until; locks[f] = {user: me, client: client, until: r.until}; }
            } else if (r.reason === 'delta') {               // 바뀐 부분을 안 받았다 — 같은 글을 전체로 다시(더 새 글이 줄에 있으면 그것을 전체로)
              if (outbox[f]) outbox[f].full = true;
              else { outbox[f] = Object.assign({}, op, {seq: nextSeq(), full: true}); idb.put('outbox', outbox[f]); }
            } else if (r.reason === 'too_long' || r.reason === 'too_big') {
              emit('error', r.reason === 'too_long' ? '칸 글이 ' + (r.max || conf.field_max).toLocaleString() + '자를 넘어 저장하지 않았어요' : '이 문서의 칸 글 합계가 상한을 넘어 저장하지 않았어요');
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
    // 칸 밖 고침처럼 보일 때 — 바로 구조 저장(전체 저장 → 모든 화면 새 판)으로 넘기지 않는다. 한글 조합 중에는 칸 이름을 묻지 않아 마지막으로 안 칸이
    // 비어 있으면 칸 밖으로 잘못 잡혔다(10-26 녹화 15:45 — 빈 줄에 한글을 치자 문서 틀 고치기로 넘어가 다른 화면이 계속 다시 불러옴).
    // 0.4초 뒤(조합이 끝난 뒤) 칸 수가 바뀌었거나 정말 칸 밖일 때만 구조 고침으로, 칸 안이면 그 칸의 고침으로
    var outsideT = null;
    function checkOutside() {
      clearTimeout(outsideT);
      outsideT = setTimeout(function () {
        if (reloading) return;
        if (hwp.isComposing()) { checkOutside(); return; }
        if (hwp.fields().length !== fieldList.length) { emit('outside-edit'); return; }
        var f = hwp.snapIntoField ? hwp.snapIntoField() : hwp.currentField();
        if (f && fieldSet[f]) {
          if (f !== curField) checkCaret();
          if (!heldByOther(f)) { if (!mine[f]) acquire(f); dirty[f] = true; lastEdit = {f: f, at: now()}; status(); scheduleFlush(); }
          return;
        }
        emit('outside-edit');
      }, 400);
    }
    // 양식 모드(칸 안만, 설정 form_mode=1)인가 — 기본은 일반 편집 모드(칸 넘는 드래그·서식째 복사, 10-26). 문서 전체가 칸으로 감싸져 있어 가능하다
    function formOnly() { return conf.form_mode > 0; }
    function normalMode() { return fieldsMode() && !hwp.formMode() && !(o.structureMode && o.structureMode()); }
    // 일반 편집 모드의 키 관문 — 양식 모드가 막아 주던 것을 대신 막는다. 칸 바깥에서 치려 하면 칸 안으로 옮기고, 남이 쓰는 칸·칸 표시를 지우는
    // 키(칸 처음 Backspace, 칸 끝 Delete)는 막고, 여러 칸에 걸친 고침(선택이 칸을 넘음)은 화면에 넘겨 구조 고침으로 돌린다
    function guardKey(e) {
      var k = e.key || '', mod = e.ctrlKey || e.metaKey;
      var edit = (!mod && (k.length === 1 || /^(Backspace|Delete|Enter|Tab)$/.test(k))) || (mod && /^[vVxX]$/.test(k));
      if (!edit || applying) return;
      var stop = function () { e.preventDefault(); e.stopImmediatePropagation(); };
      var sel = hwp.selectionFields ? hwp.selectionFields() : null;
      // 여러 칸에 걸친 고침·칸 바깥 고침은 편집기가 그대로 하게 두고 화면이 곧바로 문서 틀 고치기(구조 저장)로 돌린다
      if (sel && (sel[0] !== sel[1] || !sel[0])) { emit('cross-edit', {key: k, mod: mod}); return; }
      var f = hwp.currentField();                                      // 커서 자리 맞추기는 누름·이동 때 했다(onCaret) — 글자마다 하지 않는다
      if (!f || !fieldSet[f]) { checkOutside(); return; }            // 칸 밖처럼 보여도 바로 구조 고침으로 넘기지 않고 고친 뒤 다시 본다
      if (heldByOther(f) && locks[f].user !== me) { stop(); emit('blocked', f); acquire(f, true); return; }
      if (!sel && !mod && (k === 'Backspace' || k === 'Delete')) {
        var c = hwp.caretInField ? hwp.caretInField() : null;
        if (c && c.field === f && ((k === 'Backspace' && c.at <= 0) || (k === 'Delete' && c.at >= c.len))) stop();   // 칸 표시를 지우지 않게(이웃 문단과 잇기는 문서 틀 고치기에서)
      }
    }
    // ── 서식째 주고받기(설정 rich, 기본 켬 10-26) — 글은 위 길(바뀐 부분)로, 서식 고침(툴바·단축키·글자 모양 창)은 그 칸을 기안기 JSON 블록째 ──
    // 서식만 바꾸면 글이 그대로라 위 길로는 아무것도 가지 않았다. 서식을 바꿀 수 있는 손놀림(편집 화면 밖 누름 — 툴바·메뉴·창, Ctrl/⌘ 키) 뒤
    // 손이 0.7초 멈추면 지금 칸을 꺼내 지난번 것과 견주고, 다르면 보낸다(누름 한 번에 서식이 안 바뀌었으면 보내지 않는다)
    var RICH = /./, richDirty = {}, richTimer = null, lastRich = {};
    function richSig(json) { return String(json || '').replace(/"\d{4}-\d\d-\d\dT[\d:.]+Z"/g, '""'); }   // 꺼낼 때마다 바뀌는 시각은 빼고 견준다
    function scheduleRich() { clearTimeout(richTimer); richTimer = setTimeout(doRich, 700); }
    function doRich() {
      if (hwp.isComposing() || hwp.isProbing() || now() - hwp.lastInputAt() < 700 || pending()) { scheduleRich(); return; }
      var fs = Object.keys(richDirty);
      richDirty = {};
      // 붙여넣기·끌어 놓기로 칸 표시까지 들어오면(칸째 복사) 칸 목록이 바뀐다 — 칸 길로는 못 보내므로 구조 고침(화면이 문서 전체 저장)으로
      var nowList = hwp.fields(), seen = {}, dup = nowList.some(function (f) { if (seen[f]) return true; seen[f] = 1; return false; });
      if (reloading) return;
      if (!o.structureMode || !o.structureMode()) {
        if (dup || nowList.length !== fieldList.length) { emit('outside-edit'); return; }
      }
      (function next() {
        var f = fs.shift();
        if (!f) return;
        if (heldByOther(f)) { next(); return; }
        hwp.exportField(f).then(function (json) {                       // 남이 쥔 칸은 위에서 건너뛴다(서버도 남이 쥔 칸의 JSON 블록은 받지 않는다)
          if (json && richSig(json) !== lastRich[f]) {
            lastRich[f] = richSig(json);
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
    // 받은 칸이 많을 때(AURA 가 216절을 한꺼번에 씀) 한 작업에서 다 넣으면 451쪽 문서에서 화면이 30~70초 멈췄다 — 한 번에 SLICE_MS 만 넣고 쉬며,
    // 입력이 생기면 멈춘다. 보이는 칸(화면이 알려 주는 순서, 없으면 내 커서 칸에서 가까운 칸)부터 넣는다
    var SLICE_MS = 30, rankOf = null, fieldIdx = {};
    function order(keys) {
      var me_ = fieldIdx[curField] || 0;
      var r = rankOf || function (f) { return Math.abs((fieldIdx[f] === undefined ? 1e9 : fieldIdx[f]) - me_); };
      return keys.sort(function (a, b) { return r(a) - r(b); });
    }
    function stepsFor(d, local) {                     // 넣을 바뀐 부분 — 알림에 실려 왔으면 그것, 전체 글로 왔으면 내 글과 견줘서 하나
      var k = known[d.field];
      if (!k || norm(local) !== k.text) return null;
      if (d.steps && d.steps.length && k.v === d.from) return d.steps;
      var x = diffText(k.text, d.text);
      // 칸 대부분을 바꾸는 짧은 칸(AURA 가 절을 새로 씀)은 통째 쓰기가 더 싸다(451쪽 3,000자 칸: 통째 180ms, 지우고 넣기 211~250ms)
      if (x.del > k.text.length / 2 && k.text.length < 20000) return null;
      return [{at: cpLen(k.text.slice(0, x.at)), del: cpLen(k.text.slice(x.at, x.at + x.del)), ins: x.ins}];
    }
    function applyInbox() {
      applyTimer = null;
      var keys = Object.keys(inbox);
      if (!keys.length) return;
      var idle = now() - hwp.lastInputAt();
      if (hwp.isComposing() || hwp.isProbing() || idle < conf.pause_ms) { scheduleApply(Math.max(30, conf.pause_ms - idle + 20)); return; }
      var cur = null, viewAt = null, rich = [], t0 = performance.now(), wrote = false;
      order(keys);
      for (var i = 0; i < keys.length; i++) {
        if (i && performance.now() - t0 > SLICE_MS) break;          // 나머지는 다음 조각에
        var f = keys[i], d = inbox[f]; delete inbox[f];
        if (!fieldSet[f]) { known[f] = {v: d.version, text: d.text}; continue; }
        if (!d.force && known[f] && d.version <= known[f].v) continue;
        if (dirty[f]) collect();                               // 내 고침을 먼저 보낼 줄에 — 서버가 충돌 사본으로 남긴다
        var local = hwp.read(f);
        // 서식째 — 기안기 JSON 블록(설정 rich 일 때)이나 서버 참여자의 hwpx 조각(언제나). 조각을 넣은 칸은 화면 글이 서버 글과 조금 달라도
        // (표 글·캡션) 지문 대조로 글만 다시 쓰지 않는다(서식이 지워진다) — 그 칸을 다음에 글로 고칠 때까지
        // hwpx 조각은 서버가 검사해 trusted 로 표시한 알림, 또는 서버 칸 상태(검사를 거친 것만 남는다)에서 온 것만
        var hx = d.rich && hwp.isHwpxRich && hwp.isHwpxRich(d.rich) && (d.trusted === true || d.fromState === true);
        if (d.rich && hwp.isHwpxRich && hwp.isHwpxRich(d.rich) && !hx) d.rich = null;
        if (d.rich && (conf.rich || hx)) { rich.push(d); known[f] = {v: d.version, text: d.text}; if (hx) richField[f] = d.version; else delete richField[f]; continue; }
        delete richField[f];
        if (local !== null && norm(local) !== d.text) {
          if (!cur) { cur = hwp.getCursor(); viewAt = hwp.viewTop ? hwp.viewTop() : null; }
          applying = true; wrote = true;
          // 바뀐 부분만 그 자리에(통째 쓰기는 칸 전체를 다시 그려 451쪽 문서의 5만 자 절에서 2~4초 걸렸다). 내 화면 글이 바뀌기 전 글과 같을 때만,
          // 넣은 뒤 글이 안 맞으면 전체 쓰기로
          var okd = false, steps = hwp.applyDelta ? stepsFor(d, local) : null;
          if (steps) {
            var t = known[f].text;
            okd = steps.every(function (st) { var a = u16At(t, st.at), b = u16At(t.slice(a), st.del) + a, ok = hwp.applyDelta(f, t, a, b - a, st.ins); t = t.slice(0, a) + st.ins + t.slice(b); return ok; });
            if (okd) { var after = hwp.read(f); okd = after !== null && norm(after) === d.text; }
          }
          if (!okd) hwp.write(f, d.text);
          applying = false;
        }
        known[f] = {v: d.version, text: d.text}; touched[f] = 1;
        emit('applied', f, d);
      }
      if (cur) { hwp.restoreCursor(cur); if (hwp.holdView) hwp.holdView(viewAt); }   // 남의 글 넣은 뒤 보던 화면 그대로(깜빡임 막기)
      if (wrote && hwp.lockUndo) hwp.lockUndo(true);
      if (Object.keys(inbox).length) { clearTimeout(applyTimer); applyTimer = setTimeout(applyInbox, 0); }   // 남은 것은 쉬었다가(입력이 끼면 위에서 미룬다)
      if (Object.keys(outbox).length) scheduleFlush();
      (function next() {                                // 서식째 받은 절 — 기안기 JSON 블록으로 그 칸을 바꾼다. 안 되면 글만
        var d = rich.shift();
        if (!d) return;
        applying = true;
        if (!hwp.isHwpxRich(d.rich)) lastRich[d.field] = richSig(d.rich);
        hwp.importField(d.field, d.rich).then(function (ok) {
          var hx2 = hwp.isHwpxRich && hwp.isHwpxRich(d.rich);
          if (!ok || (!hx2 && norm(hwp.read(d.field)) !== d.text)) { hwp.write(d.field, d.text); delete richField[d.field]; }
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
          emit('blocked', f, holderLabel(f));                 // 보러 오기만 한 것(사람 목록에서 그 자리로 옮기기 포함)은 쥔 사람에게 알리지 않는다 — 쓰려고 할 때만(아래 onActivity)
        }
      }
      emit('caret', f);
    }
    var lastPage = 0;
    function presence(force) {
      if (!force && now() - lastPresence < 8000) return;
      lastPresence = now();
      var page = hwp.isComposing() || !hwp.caretPage ? lastPage : (lastPage = hwp.caretPage());   // 쪽 번호(조합 중에는 묻지 않는다)
      post('/presence', {client: client, field: curField, page: page}).then(function (j) {
        if (!j || !j.people) return;                  // 서버가 아는 같이 있는 사람으로 맞춘다(가만히 있는 사람도 남게)
        learnWindows(j.windows);
        var seen = {};
        j.people.forEach(function (p) {
          if (p.name) names[p.user] = p.name;
          if (p.client === client && p.user === me) return;
          var k = p.user + '\u001f' + p.client; seen[k] = 1;
          people[k] = {user: p.user, field: p.field, page: p.page || 0, at: now()};
        });
        Object.keys(people).forEach(function (k) { if (!seen[k]) delete people[k]; });
        emit('people', peopleList());
      }).catch(function () {});
    }

    // ── 알림 흐름 ──
    // 연결이 조용히 죽으면(망이 잠깐 끊긴 뒤 반쯤 열린 채) EventSource 는 오류를 내지 않는다 — 서버가 BEAT 마다 보내는 beat 알림이
    // STALE_MS 동안 하나도 오지 않으면 새로 붙고 서버 정답을 다시 맞춘다(놓친 「문서 저장」 은 last 뒤 알림으로 다시 온다)
    var heardAt = now(), STALE_MS = 45000, watch = null;
    function connect(again) {
      if (es) es.close();
      es = new EventSource(base + '/events' + (lastId ? '?last=' + lastId : ''));
      var reopened = !!again;
      heardAt = now();
      es.onopen = function () { heardAt = now(); if (reopened) { online = true; status(); resync(false); send(); } reopened = false; };
      es.onerror = function () { reopened = true; if (es.readyState === 2) setTimeout(function () { connect(true); }, 2000); };
      es.addEventListener('beat', function () { heardAt = now(); });
      if (!watch) watch = setInterval(function () { if (es && now() - heardAt > STALE_MS) connect(true); }, 5000);
      function h(kind, fn) { es.addEventListener(kind, function (ev) { heardAt = now(); lastId = Math.max(lastId, +ev.lastEventId || 0); var d = {}; try { d = JSON.parse(ev.data); } catch (e) {} learn(d); fn(d); }); }
      h('field-updated', function (d) {
        if (d.client === client && d.by === me) return;    // 내 창이 보낸 것(창 번호만으로는 남의 글을 놓칠 수 있어 사람도 본다)
        var f = d.field, k = known[f];
        if (k && d.version <= k.v) return;
        d.receivedAt = now();
        if (d.ins !== undefined) {                          // 바뀐 부분 — 이어지는 판이면 받을 글을 만들어 두고, 넣을 때는 그 자리만 고친다
          var q = inbox[f], baseText = q && q.version === d.base ? q.text : (!q && k && k.v === d.base ? k.text : null);
          if (baseText === null) { fetchFields([f]); return; }       // 판이 끊겼다 — 칸 글 전체를 받는다
          var text = applyStr(baseText, d);
          if (text === null || fp(text) !== d.fp) { fetchFields([f]); return; }
          var steps = q ? (q.steps || null) : [];
          if (steps) steps = steps.concat([{at: d.at, del: d.del, ins: d.ins}]);
          inbox[f] = {field: f, version: d.version, text: text, by: d.by, client: d.client, receivedAt: d.receivedAt, steps: steps, from: q ? q.from : k.v};
        } else inbox[f] = d;
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
        else if (!(d.client === client && d.user === me)) people[k] = {user: d.user, field: d.field, page: d.page || 0, at: now()};
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
      Object.keys(people).forEach(function (c) { var p = people[c]; if (p.at >= t && p.user !== me) by[p.user] = {user: p.user, name: nameOf(p.user), field: p.field, page: p.page || 0}; });
      return Object.keys(by).map(function (u) { return by[u]; });
    }

    function lightCheck() {
      return fetch(base + '/coedit?light=1', {credentials: 'same-origin', cache: 'no-store'}).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }).then(function (s) {
        if (hwp.isComposing()) return s;
        online = true;
        var srv = s.fields || {}, chk = checkSet(), need = [];
        var names_ = fieldList.filter(function (f) { var x = srv[f], k = known[f]; return x && k && x.version === k.v && chk[f] && !dirty[f] && !outbox[f]; });
        var local = hwp.readMany(names_);
        fieldList.forEach(function (f) {
          var x = srv[f], k = known[f];
          if (!x) return;
          if (!k || x.version > k.v) need.push(f);
          else if (local[f] !== undefined && richField[f] !== x.version && settled[f] !== x.version && fp(local[f]) !== x.fp) { settled[f] = x.version; need.push(f); }
        });
        if (need.length) fetchFields(need);
        if (s.windows) learnWindows(s.windows);
        status();
        return s;
      }).catch(function () { online = false; status(); });
    }
    // 고른 칸의 글 전체를 서버에서 — 바뀐 부분 알림이 끊겼거나 지문이 안 맞을 때
    var fetching = {};
    function fetchFields(list) {
      list = list.filter(function (f) { return !fetching[f]; });
      if (!list.length) return;
      list.forEach(function (f) { fetching[f] = 1; });
      fetch(base + '/coedit?' + list.map(function (f) { return 'field=' + encodeURIComponent(f); }).join('&'), {credentials: 'same-origin', cache: 'no-store'})
        .then(function (r) { return r.json(); }).then(function (j) {
          Object.keys(j.fields || {}).forEach(function (f) {
            var x = j.fields[f];
            if (!known[f] || x.version > known[f].v || (x.version === known[f].v && fp(known[f].text) !== x.fp)) inbox[f] = {field: f, version: x.version, text: x.text, rich: x.rich, fromState: true, force: true};
          });
          scheduleApply();
        }).catch(function () {}).then(function () { list.forEach(function (f) { delete fetching[f]; }); });
    }

    // ── 처음·다시 이어질 때 — 서버 상태로 맞추기, 지문 대조 ──
    // 30초 지문 대조는 가볍게(light) — 칸 글 없이 판·지문만 받고(451쪽에서 전체가 758KB), 내 쪽 지문은 그사이 바뀐 칸과 돌아가며 고른 몇 칸만 센다
    // (모든 칸을 읽으면 451쪽 218칸에 0.3~0.5초). 판이 앞서거나 지문이 다른 칸만 글을 받는다. 처음·다시 이어질 때는 전체
    var touched = {}, rot = 0, richField = {};
    function checkSet() {
      var out = touched, n = Math.max(20, Math.ceil(fieldList.length / 10));
      touched = {};
      for (var i = 0; i < n && fieldList.length; i++) { out[fieldList[rot % fieldList.length]] = 1; rot++; }
      return out;
    }
    function resync(first, light) {
      if (!first && (hwp.isComposing() || now() - hwp.lastInputAt() < conf.pause_ms)) {   // 입력 중이면 조금 뒤에
        return new Promise(function (r) { setTimeout(function () { r(resync(false, light)); }, 1000); });
      }
      if (light && !first) return lightCheck();
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
        (s.people || []).forEach(function (p) { if (!(p.client === client && p.user === me)) people[p.user + '\u001f' + p.client] = {user: p.user, field: p.field, page: p.page || 0, at: t}; });
        if (s.windows) learnWindows(s.windows);
        // 칸 글은 한 번에 읽는다(942칸을 하나씩 읽으면 열 때 0.3초)
        var srv = s.fields || {}, local = hwp.readMany(fieldList.filter(function (f) { return srv[f] || !known[f]; }));
        fieldList.forEach(function (f) {
          var x = srv[f];
          if (!x) { if (!known[f]) known[f] = {v: 0, text: norm(local[f])}; return; }
          var k = known[f];
          if (!k || x.version > k.v) inbox[f] = {field: f, version: x.version, text: x.text, rich: x.rich, fromState: true, force: true};
          else if (x.version === k.v && !dirty[f] && !outbox[f] && richField[f] !== x.version && settled[f] !== x.version && fp(local[f]) !== x.fp) { settled[f] = x.version; inbox[f] = {field: f, version: x.version, text: x.text, force: true}; }
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
      fieldSet = {}; fieldIdx = {}; fieldList.forEach(function (f, i) { fieldSet[f] = true; fieldIdx[f] = i; });
      hwp.onLocalEdit(function (ev) {
        if (applying || !fieldsMode()) return;
        if (ev && ev.composing) typedAt = now();
        if (!(ev && ev.composing) && hwp.lockUndo) hwp.lockUndo(false);   // 내가 고치면 되돌리기를 다시 연다(내 고침이 맨 위)
        // 조합 중에는 기안기에 아무것도 묻지 않는다(커서 칸 이름을 물어도 조합이 끊긴다 — 실측) — 마지막으로 안 커서 칸을 쓴다
        var f = ev && ev.composing ? curField : hwp.currentField();
        // 칸을 끝까지 지워 비우면 커서가 칸 경계에 걸려 기안기가 칸 이름을 빈 글로 준다 — 방금 고치던 칸의 고침으로 본다
        // (전에는 칸 밖 고침으로 잡혀 30초 지문 대조 때에야 다른 화면에서 지워졌다)
        if (!f && lastEdit.f && now() - lastEdit.at < 5000) f = lastEdit.f;
        if (f !== curField && !(ev && ev.composing)) checkCaret();      // 커서 칸이 바뀐 것을 먼저 반영(앞 칸을 놓고 이 칸을 잡게)
        if (!f || !fieldSet[f]) { checkOutside(); return; }
        if (heldByOther(f)) return;
        if (!mine[f]) acquire(f);                               // 처음 고칠 때 임대를 잡는다
        dirty[f] = true; lastEdit = {f: f, at: now()}; status(); scheduleFlush();
        if (mine[f] && now() - lastTouch > 15000 && mine[f] * 1000 - now() < (conf.lease - 15) * 1000) extend();
      });
      if (hwp.onCommit) hwp.onCommit(function () {           // 글자 하나 확정 — 그 칸만 바로 읽어 보낸다(조합이 끝난 이 순간만 읽어도 안전)
        if (applying || !fieldsMode()) return;
        var f = lastEdit.f;
        if (!f || !dirty[f] || heldByOther(f)) return;
        collect(); send();
      });
      hwp.onCaret(function (ev) {
        // 누르거나 이동 키로 커서가 옮겨졌을 때만 — 칸 바깥(칸 시작 표시 앞·끝 표시 뒤)에 선 커서를 칸 안으로. 글자 칠 때마다 하면 무겁다
        var moved = !ev || ev.type === 'pointerup' || /^(Arrow|Home|End|Page)/.test(ev.key || '');
        if (moved && normalMode() && !applying && !hwp.isComposing() && hwp.snapIntoField) hwp.snapIntoField();
        checkCaret();
      });
      // 툴바 관문(일반 편집 모드) — 고른 범위에 남이 쓰는 칸이 들어 있으면 기안기 서식 단추·목록을 막고 그 칸 이름표를 흔든다(남의 칸 서식을 바꾸지 않게)
      if (hwp.setToolbarGuard) hwp.setToolbarGuard(function (e) {
        if (!normalMode()) return false;
        var sel = hwp.selectionFields ? hwp.selectionFields() : null, range = [];
        if (sel && sel[0] && sel[1] && fieldIdx[sel[0]] !== undefined && fieldIdx[sel[1]] !== undefined) {
          for (var q = Math.min(fieldIdx[sel[0]], fieldIdx[sel[1]]); q <= Math.max(fieldIdx[sel[0]], fieldIdx[sel[1]]); q++) range.push(fieldList[q]);
        } else if (curField) range.push(curField);
        var taken = range.filter(function (g) { return heldByOther(g) && locks[g] && locks[g].user !== me; });
        if (taken.length && e.type === 'pointerdown') taken.forEach(function (g) { emit('blocked', g); });
        return taken.length > 0;
      });
      hwp.onActivity(function (e) {
        if (e && e.type === 'keydown' && !e.ctrlKey && !e.metaKey && ((e.key || '').length === 1 || e.key === 'Process')) typedAt = now();   // 글자 치기(지우기·이동 키 빼고)
        if (e && e.type === 'keydown' && normalMode() && !hwp.isComposing()) guardKey(e);
        if (curField && mine[curField] && now() - lastTouch > 20000) extend();
        // 서식을 바꿀 수 있는 손놀림 — 편집 화면(쪽) 밖 누름(툴바·메뉴·글자 모양 창)이나 Ctrl/⌘ 조합 키. 입력 사건이 없어 따로 잰다
        if (conf.rich && e && fieldsMode()) {
          var view = e.target && e.target.ownerDocument && e.target.ownerDocument.getElementById('hcwoViewScroll');
          var outside = e.type === 'pointerdown' && !(view && view.contains(e.target));
          var combo = e.type === 'keydown' && (e.ctrlKey || e.metaKey) && !/^(Control|Meta|Shift|Alt|c|C|a|A|s|S|f|F|p|P)$/.test(e.key || '');
          if (outside || combo) {
            if (curField && fieldSet[curField] && !heldByOther(curField)) richDirty[curField] = true;   // 선택 끝이 칸 사이면 지금 칸이 비어 있다 — 아래 선택 범위로
            // 선택이 여러 칸에 걸쳐 있으면 그 사이 칸을 모두 — 커서 칸 하나만 보내 나머지 칸 서식이 다른 화면에 가지 않았다(10-26 녹화)
            var sel = hwp.selectionFields ? hwp.selectionFields() : null;
            if (sel && sel[0] && sel[1] && fieldIdx[sel[0]] !== undefined && fieldIdx[sel[1]] !== undefined) {
              var i0 = Math.min(fieldIdx[sel[0]], fieldIdx[sel[1]]), i1 = Math.max(fieldIdx[sel[0]], fieldIdx[sel[1]]);
              for (var q = i0; q <= i1 && q - i0 < 400; q++) richDirty[fieldList[q]] = true;
            }
            scheduleRich();
          }
        }
        // 남이 쥔 칸에서 글을 치려 하면 그때 기다리는 사람으로 알린다(같은 사람 다른 창은 빼앗지 않는다)
        if (e && e.type === 'keydown' && !e.metaKey && !e.ctrlKey && e.key && (e.key.length === 1 || /^(Backspace|Delete|Enter|Process)$/.test(e.key))
            && curField && fieldSet[curField] && fieldsMode() && heldByOther(curField) && locks[curField].user !== me) acquire(curField, true);
      });
      setInterval(checkCaret, 700);
      setInterval(function () { presence(false); }, 5000);
      setInterval(function () { resync(false, true); }, Math.max(5, conf.hash_check) * 1000);
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
      // 안전장치(10-26) — 사건 없이 바뀐 것 알아채기. 기안기의 문서 안 붙여넣기·끌어 놓기·서식 창은 입력 사건을 내지 않을 때가 있다.
      // 1.5초마다(조합·받아 넣는 중 빼고) 칸 수가 바뀌었으면 구조 고침으로, 지금 칸의 글이 아는 글과 다르면 그 칸을 보낸다(가벼운 두 번 묻기)
      setInterval(function () {
        if (!started || reloading || applying || !fieldsMode() || hwp.isComposing() || hwp.isProbing() || (o.structureMode && o.structureMode())) return;
        var n = hwp.fields().length;
        if (n !== fieldList.length) { emit('outside-edit'); return; }
        var f = curField;
        if (!f || !fieldSet[f] || dirty[f] || outbox[f] || inbox[f] || heldByOther(f)) return;   // 받을 글이 기다리는 칸은 건드리지 않는다(남의 새 글을 옛 글로 덮지 않게)
        var t = hwp.read(f), k = known[f];
        if (t !== null && k && norm(t) !== k.text) {
          if (!mine[f]) acquire(f);
          dirty[f] = true; lastEdit = {f: f, at: now()}; scheduleFlush();
          if (conf.rich) { richDirty[f] = true; scheduleRich(); }
        }
      }, 1500);
      return resync(true).then(function () { if (fieldsMode()) hwp.setFormMode(formOnly()); started = true; connect(); presence(true); checkCaret(); return replay(); });
    }
    // 새 판을 다시 연 뒤 — 필드 목록·편집 가능·서버 칸 글을 다시 맞춘다
    function suspend() { reloading = true; }
    function reopened() {
      reloading = false;
      hwp.attach(); hwp.resetEditable();
      fieldList = readonly ? [] : hwp.fields();
      fieldSet = {}; fieldIdx = {}; fieldList.forEach(function (f, i) { fieldSet[f] = true; fieldIdx[f] = i; });
      known = {}; settled = {};
      if (fieldsMode()) hwp.setFormMode(formOnly() && (!o.structureMode || !o.structureMode()));
      return resync(true).then(function () { applyInbox(); });
    }
    return {
      start: start, on: on, emitLocal: emit, flush: flushNow, extend: extend, release: function (f) { return letGo(f || curField, true); },
      handover: function (f) { return letGo(f, true); }, reopened: reopened, suspend: suspend, prioritize: function (fn) { rankOf = fn; },
      indexOf: function (f) { return fieldIdx[f] === undefined ? 1e9 : fieldIdx[f]; }, resync: resync, applyEditable: applyEditable,
      client: client, fieldsMode: fieldsMode, hasField: function (f) { return !!fieldSet[f]; }, heldByOther: heldByOther, current: function () { return curField; }, locks: function () { return locks; },
      people: peopleList, nameOf: nameOf, mine: function () { return mine; }, known: function () { return known; },
      pending: pending, aura: function () { return aura; }, formOnly: formOnly, setReadOnly: function (v) { readonly = !!v; },
      conflicts: function () { return idb.all('conflicts').then(function (r) { return r.filter(function (x) { return x.fid === fid; }).sort(function (a, b) { return b.at - a.at; }); }); }
    };
  }

  // ── 화면 — 머리줄 아바타·칸 테두리·이름표·안내 띠 ──
  var PALETTE = ['#2563EB', '#16A34A', '#D97706', '#DB2777', '#7C3AED', '#0891B2', '#DC2626', '#4D7C0F'];
  function colorOf(u) { if (u === 'AURA') return '#1B3B75'; var h = 0; for (var i = 0; i < u.length; i++) h = (h * 31 + u.charCodeAt(i)) >>> 0; return PALETTE[h % PALETTE.length]; }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  // AURA 는 시킨 사람에게 딸린다 — 여러 사람이 AURA 를 부르면 「AURA」 만으로는 누구 일인지 몰랐다(10-26 시연). 이름은 알림의 by_name·처음 상태의 names 로 안다
  function auraBy(eng, a) { var b = a && a.by; return b && b !== 'AURA' ? eng.nameOf(b) : ''; }
  function auraLabelOf(eng, a) { var n = auraBy(eng, a); return 'AURA' + (n ? ' (' + n + ' 요청)' : ''); }
  function auraText(eng, a) {                          // 머리줄 상태 — 「AURA가 김가람 님 요청으로 「4. 향후 계획」 쓰는 중」
    if (!a || !a.field) return '';
    var n = auraBy(eng, a);
    return 'AURA가 ' + (n ? n + ' 님 요청으로 ' : '') + '쓰는 중';      // 어디인지는 그 칸의 잠금 표시(이름표·테두리)로 — 칸 이름은 실 문서에서 읽을 수 없다(10-26)
  }

  function ui(eng, hwp, o) {
    var avatars = o.avatars, bar = o.bar, layer = el('div', 'co-layer'), marks = {}, geo = {}, geoAt = 0, geoWant = '';
    document.body.appendChild(layer);
    layer.setAttribute('aria-hidden', 'true');
    var MAX = 4, auraState = {}, popOpen = false, hideT = null;
    // 남이 쥔 칸 — 다른 사람, 또는 내 다른 창. 내 이 창이 쥔 칸은 표시하지 않는다
    function holderOf(f) {
      var lk = eng.locks()[f];
      if (!lk || lk.until * 1000 <= Date.now() || !eng.heldByOther(f)) return null;   // 닫힌 내 옛 창의 칸은 남의 칸이 아니다
      if (lk.user !== o.me) return {user: lk.user, label: eng.nameOf(lk.user)};
      if (lk.client && lk.client !== eng.client) return {user: lk.user, label: eng.nameOf(lk.user), self: true};
      return null;
    }
    function auraLabel() { return auraLabelOf(eng, auraState); }   // 「AURA (김가람 요청)」
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
        var label = (p.name || p.user) + (p.page ? ' · ' + p.page + '쪽' : '') + (p.field && editing ? ' · 쓰는 중' : ' · 보는 중');
        var on = following === p.user;
        b.title = label + (on ? ' — 눌러서 따라가기 그만' : ' — 눌러서 따라가기');
        b.setAttribute('aria-label', b.title); b.setAttribute('aria-pressed', on ? 'true' : 'false');
        if (editing) b.classList.add('editing');
        if (on) b.classList.add('following');
        b.onclick = function () { toggleFollow(p.user); };
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
        // 「쓰는 중 · N쪽 · 절」 — 큰 문서에서 어디쯤인지 쪽으로 먼저 보인다. 누르면 그 칸으로
        var sm = el('small', '', p.me ? '' : (editing ? '쓰는 중' : '보는 중') + (p.page ? ' · ' + p.page + '쪽' : ''));
        r.appendChild(sm);
        if (!p.me) r.onclick = function () { toggleFollow(p.user); popOpen = false; pop.hidden = true; };   // 줄을 눌러도 따라가기(아바타와 같다)
        if (!p.me && following === p.user) r.classList.add('following');
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
    // 쪽 좌표 재기 — 기안기 내부 커서 모델로 같은 작업 단위 안에서(화면 커서·스크롤은 그대로). 입력·조합 중이면 미룬다.
    // 큰 문서에서는 재기 한 번이 칸마다 커서를 두 번 옮겨 451쪽·20칸에 46~65ms였다 — 이제 칸마다 자리를 기억해 두고 한 번에 한 칸만 잰다:
    // 처음 보는 칸(내 커서 칸에서 가까운 칸부터), 그다음 화면 근처 칸 중 바뀌었을 수 있는 칸(앞쪽 칸 글이 바뀜·4초 지남). 화면 밖 칸은 화면에 들어올 때 잰다
    var stale = {}, staleAt = {};
    function nearView(g) {
      var a = hwp.toScreen(g.page, g.x, g.y);
      if (!a) return false;
      var h = a.view.bottom - a.view.top;
      return a.y > a.view.top - h && a.y < a.view.bottom + h;
    }
    function measure(force) {
      var want = Object.keys(wanted()), ft = followField();
      if (ft && want.indexOf(ft) < 0) want.push(ft);          // 따라가는 사람이 보기만 하는 칸도 자리를 안다(그리지는 않는다)
      Object.keys(geo).forEach(function (f) { if (want.indexOf(f) < 0) delete geo[f]; });
      if (!want.length) { geoWant = ''; return; }
      if (hwp.isComposing() || Date.now() - hwp.lastInputAt() < 1500) return;   // 누르거나 치는 중에는 재지 않는다 — 재는 동안 커서를 옮겼다 되돌려 막 누른 자리를 덮었다
      var t = Date.now(), kn = eng.known();
      var empty = function (f) { var k = kn[f]; if (k && typeof k.text === 'string') return k.text === ''; var x = hwp.read(f); return x == null || x === ''; };   // 빈 칸은 재지 않는다(커서가 들어가면 기안기 안내 글이 지워진다)
      var cur = eng.current(), idx = eng.indexOf ? eng.indexOf : function () { return 0; }, me_ = idx(cur);
      // AURA 를 따라가는 칸은 비어 있어도 잰다 — 빈 칸을 건너뛰면 AURA 가 쓰기 시작한 먼 쪽 절의 자리를 몰라 화면이 따라가지 못했다(10-27 운영 2340).
      // 빈 칸 안내 글이 지워지는 것은 곧 AURA 글로 채워질 칸이라 괜찮다
      var auraFt = following === 'AURA' ? ft : '';
      var pick = want.filter(function (f) { return !geo[f] && (!empty(f) || f === auraFt); }).sort(function (a, b) { return (b === ft) - (a === ft) || Math.abs(idx(a) - me_) - Math.abs(idx(b) - me_); })[0];
      if (!pick && ft && geo[ft] && (stale[ft] || t - (staleAt[ft] || 0) > 1500) && (!empty(ft) || ft === auraFt)) pick = ft;   // 따라가는 칸은 자주(그 사람이 써서 줄이 늘면 커서가 내려간다)
      if (!pick) pick = want.filter(function (f) { return geo[f] && (stale[f] || t - (staleAt[f] || 0) > 4000 || force) && nearView(geo[f]) && !empty(f); })[0];
      if (!pick) return;
      var g = hwp.fieldGeom([pick]);
      if (!g) return;
      if (g[pick]) geo[pick] = g[pick];
      delete stale[pick]; staleAt[pick] = t; geoAt = t;
    }
    // 칸 글이 바뀌면 그 칸과 뒤쪽 칸 자리가 밀렸을 수 있다
    function invalidateFrom(f) {
      var idx = eng.indexOf ? eng.indexOf : function () { return 0; }, i = f ? idx(f) : -1;
      Object.keys(geo).forEach(function (g) { if (!f || idx(g) >= i) stale[g] = 1; });
    }
    function mark(f) {
      var m = marks[f];
      if (!m) {
        m = marks[f] = {box: el('div', 'co-box'), caret: el('div', 'co-caret'), flag: el('div', 'co-flag'), block: el('div', 'co-block')};
        m.flag.appendChild(el('span', 'co-flag-t'));
        // 남이 쓰는 칸을 누르면 기안기가 커서를 앞 칸으로 돌려놓아 친 글이 엉뚱한 칸에 들어갔다(10-24 녹화) — 칸 위에 투명 막을 두어 누름을 받고 알린다
        m.block.addEventListener('pointerdown', function (e) {
          e.preventDefault(); e.stopPropagation();
          stopFollow();
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
        if (g.page !== g.page2) {                       // 쪽을 넘은 칸 — 표시는 커서가 있는 쪽의 글 자리 위에서 시작(쪽 사이 회색 여백을 가로지르지 않게, 10-27)
          var pt = hwp.toScreen(g.page2, g.x2, hwp.bodyTop ? hwp.bodyTop() : 0);
          if (pt) top = Math.min(pt.y, b.y);
        }
        var vis = bottom > view.top && top < view.bottom;
        m.box.hidden = m.caret.hidden = m.flag.hidden = m.block.hidden = !vis;
        if (!vis) return;
        [m.box, m.caret, m.flag].forEach(function (x) { x.style.setProperty('--c', c); });
        var L = layer.getBoundingClientRect();
        m.box.title = w.label + ' 님이 쓰는 중';
        if (single) {                                   // 한 줄 — 칸 글 위에 옅은 바탕
          m.box.className = 'co-box hl';
          m.box.style.left = (a.x - 1 - L.left) + 'px'; m.box.style.top = (top - L.top) + 'px';
          m.box.style.width = Math.max(4, b.x - a.x + 2) + 'px'; m.box.style.height = h1 + 'px';
        } else {                                        // 여러 줄 — 칸 왼쪽에 옅은 막대(글 위를 덮지 않게)
          m.box.className = 'co-box multi';
          m.box.style.left = (a.x - 7 - L.left) + 'px'; m.box.style.top = (top - L.top) + 'px';
          m.box.style.width = '2px'; m.box.style.height = Math.max(h1, bottom - top) + 'px';
        }
        m.block.hidden = !hwp.formMode();               // 누름 막 — 칸 글 자리 전체(여러 줄이면 종이 글 폭까지). 일반 편집 모드에서는 걷는다 —
                                                        // 고침은 키 관문·툴바 관문이 막고, 막이 있으면 남의 칸을 걸친 드래그·복사까지 막혔다(10-26)
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
        m.flag.title = w.label + ' 님이 쓰는 중';
        var room = leftSide ? bodyL - view.left - 10 : view.right - bodyR - 10;
        if (m.flag.offsetWidth > room) {                // 여백이 좁다 — 사람은 머리 글자 동그라미, AURA 는 「AURA」 그대로 작게(「A」 만으로는 무엇인지 몰랐다, 10-26)
          if (w.user === 'AURA') { m.flag.classList.add('tight'); tt.textContent = 'AURA'; }
          else { m.flag.classList.add('dot'); tt.textContent = w.label.slice(0, 1); }
        }
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
    function tick() { if (hwp.isComposing()) return; try { measure(false); render(); followStep(); } catch (e) {} }   // 한글 조합 중에는 손대지 않는다
    setInterval(tick, 250);                              // 화면 좌표는 매번 다시(스크롤·확대·창 크기), 쪽 좌표는 바뀔 때·4초마다
    // 스크롤하는 동안은 화면 그리기마다 다시 놓는다 — 0.25초 주기만으로는 이름표·테두리가 글을 늦게 따라와 엉뚱한 줄에 잠깐 붙었다(10-26 녹화).
    // 자리 계산은 편집기에 지금 화면 좌표를 묻는 것이라(쪽 좌표는 그대로) 가볍다. 관성 스크롤 끝자락도 잡게 멈춘 뒤 한 번 더
    var rafOn = false, settleT = null;
    function onScrollDraw() {
      if (!rafOn) { rafOn = true; requestAnimationFrame(function () { rafOn = false; try { render(); } catch (e) {} }); }
      clearTimeout(settleT); settleT = setTimeout(function () { try { render(); } catch (e) {} }, 80);
    }
    hwp.onActivity(function (e) { if (e && (e.type === 'scroll' || e.type === 'wheel')) onScrollDraw(); });
    window.addEventListener('resize', onScrollDraw);
    function soon() { setTimeout(tick, 0); }
    eng.on('locks', function () { drawAvatars(); soon(); });
    eng.on('people', function () { drawAvatars(); soon(); });
    // AURA 가 쓰기 시작하면 가만히 보고 있던 화면은 저절로 그 자리로(안내 띠 없이) — 다 쓰거나 내가 손대면 멈춘다(10-26 시연: 쓰는 절이 화면 밖이었다)
    var autoAura = false;
    eng.on('aura', function (a) {
      auraState = a || {};
      if (auraState.field && !following && Date.now() - hwp.lastInputAt() > 3000) { following = 'AURA'; followAt = 0; autoAura = true; }
      if (following === 'AURA' && !auraState.field) { if (autoAura) { following = ''; autoAura = false; } else stopFollow('AURA가 다 써서 따라가기를 멈췄어요'); }
      else if (following === 'AURA' && barKind === 'follow') showFollow();
      drawAvatars(); soon();
    });
    eng.on('people', function () { if (following && following !== 'AURA' && !personOf(following)) stopFollow(eng.nameOf(following) + ' 님이 나가서 따라가기를 멈췄어요'); });
    eng.on('applied', function (f) { invalidateFrom(f); soon(); });
    hwp.onLocalEdit(function () { invalidateFrom(eng.current()); });
    // 보이는 칸부터 받아 넣게 — 자리를 아는 칸은 화면 근처면 앞으로
    if (eng.prioritize) eng.prioritize(function (f) { var g = geo[f]; return g && nearView(g) ? -1 : (eng.indexOf ? Math.abs(eng.indexOf(f) - eng.indexOf(eng.current())) : 0); });

    // ── 따라가기 ── 머리줄 아바타(또는 같이 있는 사람 줄)를 누르면 그 사람이 있는 칸이 늘 화면에 보이게 따라간다(10-26 시연 요청).
    // 화면만 굴리고 내 커서는 옮기지 않는다 — 커서를 옮기면 내 칸 임대가 풀리고 남이 쓰는 칸에서는 「쓰는 중」 안내가 떴다.
    // 자리는 칸 자리 재기(geo)로, 아직 모르는 칸(빈 칸 등)은 그 사람이 있는 쪽으로. 내가 치거나 문서를 누르거나 직접 굴리면 바로 멈춘다
    var following = '', followAt = 0;
    function personOf(u) { var l = eng.people(); for (var i = 0; i < l.length; i++) if (l[i].user === u) return l[i]; return null; }
    function followName(u) { return u === 'AURA' ? auraLabel() : eng.nameOf(u); }
    function followField() {
      if (!following) return '';
      if (following === 'AURA') return auraState.field && eng.hasField(auraState.field) ? auraState.field : '';
      var p = personOf(following), f = heldBy(following) || (p && p.field) || '';
      return f && eng.hasField(f) ? f : '';
    }
    function showFollow() { show('follow', followName(following) + ' 님 따라가는 중', [['그만', function () { stopFollow(); }]]); }
    function stopFollow(why) {
      if (!following) return;
      following = ''; autoAura = false;
      if (barKind === 'follow') hide('follow');
      if (why) show('follow-end', why, null, 3000);
      drawAvatars();
    }
    function toggleFollow(u) {
      if (following === u) { stopFollow(); return; }
      if (u !== 'AURA' && !personOf(u)) return;
      following = u; followAt = 0; autoAura = false;
      showFollow(); drawAvatars(); soon();
    }
    function followStep() {
      if (!following || Date.now() - followAt < 500 || Date.now() - hwp.lastInputAt() < 1500) return;
      var f = followField(), g = f && geo[f], y = null, view = null, a;
      if (g) { a = hwp.toScreen(g.page2, g.x2, g.y2); if (a) { y = a.y; view = a.view; } }      // 칸 끝 — 그 사람이 이어 쓰는 자리
      if (y === null) {                                                                      // 자리를 아직 모른다 — 그 사람이 있는 쪽 머리로
        var p = following === 'AURA' ? null : personOf(following);
        if (p && p.page) { a = hwp.toScreen(p.page, 0, 0); if (a) { y = a.y; view = a.view; } }
      }
      if (y === null || !view) return;
      var h = view.bottom - view.top;
      if (y > view.top + h * 0.15 && y < view.bottom - h * 0.25) return;                     // 이미 잘 보인다 — 굴리지 않는다(화면이 떨리지 않게)
      followAt = Date.now();
      if (hwp.scrollBy) hwp.scrollBy(y - (view.top + h * 0.35));          // 쪽 좌표(geo)는 굴려도 그대로라 다시 재지 않는다
    }
    hwp.onActivity(function (e) {                         // 내가 손대면 멈춘다 — 굴리기(wheel)·누르기·키. 따라가며 굴린 화면의 scroll 사건은 빼고
      if (following && e && (e.type === 'keydown' || e.type === 'pointerdown' || e.type === 'wheel')) stopFollow();
    });

    // 안내 띠 — 한 번에 하나, 단추는 키보드로도(Esc 로 닫기)
    var barKind = '', barTimer = null;
    function show(kind, text, buttons, ms) {               // ms 를 주면 그 뒤 저절로 닫힌다
      barKind = kind;
      clearTimeout(barTimer);
      bar.innerHTML = '';
      bar.appendChild(el('span', 'co-bar-text', text));
      (buttons || []).forEach(function (b) { var x = el('button', 'co-btn', b[0]); x.type = 'button'; x.onclick = b[1]; bar.appendChild(x); });
      if (kind !== 'follow') { var close = el('button', 'co-btn ghost', '×'); close.setAttribute('aria-label', '닫기'); close.type = 'button'; close.onclick = function () { hide(); }; bar.appendChild(close); }   // 따라가기는 「그만」 하나
      bar.hidden = false;
      if (ms) barTimer = setTimeout(function () { hide(kind); }, ms);
    }
    function hide(kind) {
      if (typeof kind === 'string' && kind !== barKind) return;
      var was = barKind;
      clearTimeout(barTimer); bar.hidden = true; barKind = '';
      if (was === 'follow') { if (following) stopFollow(); }   // 따라가기 띠를 닫으면 따라가기도 멈춘다
      else if (following) showFollow();                         // 다른 안내가 잠깐 덮었다 — 끝나면 따라가기 띠를 다시
    }
    bar.addEventListener('keydown', function (e) { if (e.key === 'Escape') { e.stopPropagation(); hide(); } });
    eng.on('lease-warn', function (f, left) {
      if (barKind !== 'warn' && barKind && barKind !== 'follow') return;   // 따라가기 띠는 덮어도 된다(끝나면 다시 뜬다)
      show('warn', left + '초 안에 이어 쓰지 않으면 이 칸이 다른 사람에게 풀려요', [['계속 쓰기', function () { eng.extend(); hide('warn'); }]]);
    });
    eng.on('lease-ok', function () { hide('warn'); });
    // 남이 쓰는 칸에 글을 치려 하면 — 알림 띠 대신 그 칸 이름표만 잠깐 흔든다(칸에 이미 이름표·테두리가 있다.
    // 띠는 문서를 밀고 칸을 오갈 때마다 켜졌다 꺼졌다 했다, 10-26)
    eng.on('blocked', function (f) {
      var m = marks[f]; if (!m || !m.flag) return;
      m.flag.classList.remove('nudge'); void m.flag.offsetWidth; m.flag.classList.add('nudge');
    });
    eng.on('caret', function (f) { if (barKind === 'blocked' && !holderOf(f)) hide('blocked'); if (barKind === 'warn') hide('warn'); });
    eng.on('freed', function () {});                    // 칸이 비면 이름표가 사라지는 것으로 충분하다(알림 띠 없음)
    var wantedField = '';
    eng.on('locks', function () { var lk = eng.locks()[wantedField]; if (barKind === 'wanted' && (!lk || lk.user !== o.me || lk.client !== eng.client)) hide('wanted'); });
    eng.on('wanted', function (f, who) {
      wantedField = f;
      show('wanted', who + ' 님이 지금 내 칸을 쓰려고 기다려요', [['넘겨주기', function () { eng.handover(f); hide('wanted'); }]], 15000);
    });
    eng.on('conflict', function (c) {
      show('conflict', '쓴 글이 ' + (c.by ? c.by + ' 님의 글과 ' : '') + '겹쳐 내 글은 사본으로 따로 두었어요', [['내 글 보기', function () { openConflicts(); }]]);
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
          row.appendChild(el('div', 'co-panel-f', new Date(c.at).toLocaleTimeString('ko-KR', {hour: '2-digit', minute: '2-digit'})));
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
    return {drawAvatars: drawAvatars, refresh: soon, show: show, hide: hide, openConflicts: openConflicts, colorOf: colorOf,
      follow: toggleFollow, stopFollow: stopFollow, following: function () { return following; }};
  }

  window.AuraCollab = {engine: engine, ui: ui, fp: fp, norm: norm, colorOf: colorOf, auraText: auraText, auraLabel: auraLabelOf};
})();
