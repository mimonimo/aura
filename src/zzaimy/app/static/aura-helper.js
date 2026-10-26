/* 업무 도우미(templates/_aura_helper.html) — 묻기·지난 대화·옆 패널(좁은 폭은 아래 시트)과 도우미가 화면을 조작할 때 움직이는 AURA 커서.
   - 넓은 화면은 오른쪽 옆 패널 하나 — 열면 본문이 패널 폭만큼 좁아져 아무것도 가리지 않는다. 폭은 이 브라우저에 기억한다(localStorage auraHelper).
   - 페이지를 옮기면 저절로 다시 열지 않는다. AURA 커서가 화면을 옮겨 이어 하는 때만 연다(sessionStorage auraQueue).
   - 대화 번호는 탭마다(sessionStorage chatWidgetSession). 기록에서 고르면 그 대화를 이어 간다.
   - 도우미가 낸 단계(open_page steps)를 커서가 차례로 한다: 화면 이동 → 창 열기 → 칸 채우기 → (서버가 허락한 때만) 마지막 단추.
     한 일·못 한 일은 서버에 보고하고(/proposals/{id}/result) 대화의 「화면에서 한 일」 줄은 그 보고로만 생긴다. */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var fab = $("agFab"), panel = $("agPanel");
  if (!fab || !panel) return;
  var body = $("agBody"), input = $("agInput"), send = $("agSend"), jump = $("agJump");
  var quote = $("agQuote"), quoteText = $("agQuoteText"), menu = $("agMenu");
  var title = $("agTitle"), sub = $("agSub"), back = $("agBack");
  var chatView = $("agChatView"), histView = $("agHistView"), histList = $("agHistList"), histFind = $("agHistFind");
  var newBtn = $("agNew"), histBtn = $("agHist");
  var pageProject = panel.dataset.project ? Number(panel.dataset.project) : null;

  function store(kind, key, value) {
    try {
      var s = kind === "local" ? localStorage : sessionStorage;
      if (value === undefined) return s.getItem(key);
      if (value === null) s.removeItem(key); else s.setItem(key, value);
    } catch (e) { return null; }
    return null;
  }

  /* ---------- 자리: 넓은 화면은 오른쪽 옆 패널(본문이 그 폭만큼 좁아진다), 좁은 폭(640px 미만)은 아래 시트 ---------- */
  var root = document.documentElement;
  var ui = { w: 400 };
  try { var saved = JSON.parse(store("local", "auraHelper") || "null"); if (saved && (saved.side || saved.dw)) ui.w = Number(saved.side || saved.dw) || 400; } catch (e) {}
  function keep() { store("local", "auraHelper", JSON.stringify({ side: ui.w })); }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function narrow() { return innerWidth < 640; }
  function place() {
    var vw = root.clientWidth || innerWidth, sheet = narrow();
    panel.classList.toggle("ag-sheet", sheet);
    if (sheet) { panel.style.width = ""; root.classList.remove("ag-side"); return; }
    var w = Math.round(clamp(ui.w, 320, Math.max(320, Math.min(720, vw * 0.5))));
    panel.style.width = w + "px";
    root.style.setProperty("--ag-side-w", w + "px");
    root.classList.toggle("ag-side", isOpen());
  }
  function still(fn) {            // 처음 그리기·창 크기 바뀜은 움직임 없이(패널과 본문 모두)
    root.classList.add("ag-still"); fn(); void panel.offsetWidth;
    requestAnimationFrame(function () { requestAnimationFrame(function () { root.classList.remove("ag-still"); }); });
  }
  var resizeTimer;
  addEventListener("resize", function () { clearTimeout(resizeTimer); resizeTimer = setTimeout(function () { still(place); }, 60); });

  // 폭 조절 — 패널 왼쪽 가장자리를 끈다. 본문도 같이 따라 좁아지고 넓어진다
  panel.querySelectorAll("[data-rz]").forEach(function (h) {
    h.addEventListener("pointerdown", function (ev) {
      if (narrow()) return;
      ev.preventDefault(); try { h.setPointerCapture(ev.pointerId); } catch (e) {}
      var x0 = ev.clientX, w0 = panel.offsetWidth;
      root.classList.add("ag-dragging");
      function move(e) { ui.w = w0 + (x0 - e.clientX); place(); }
      function up() {
        removeEventListener("pointermove", move); removeEventListener("pointerup", up); removeEventListener("pointercancel", up);
        root.classList.remove("ag-dragging"); ui.w = panel.offsetWidth; keep();
      }
      addEventListener("pointermove", move); addEventListener("pointerup", up); addEventListener("pointercancel", up);
    });
  });

  /* ---------- 열고 닫기 ---------- */
  function isOpen() { return panel.classList.contains("open"); }
  function open(opts) {
    opts = opts || {};
    if (!isOpen()) {
      var go = function () {
        panel.inert = false; panel.setAttribute("aria-hidden", "false");
        panel.classList.add("open"); document.body.classList.add("ag-open");
        fab.setAttribute("aria-expanded", "true"); place();
      };
      if (opts.instant) still(go); else go();
    }
    showChat();
    if (sid) load(); else if (!body.children.length) render([]);
    if (!opts.noFocus) setTimeout(function () { if (isOpen()) input.focus({ preventScroll: true }); }, 200);
  }
  function close() {
    panel.classList.remove("open"); document.body.classList.remove("ag-open"); root.classList.remove("ag-side");
    panel.inert = true; panel.setAttribute("aria-hidden", "true");
    fab.setAttribute("aria-expanded", "false"); clearTimeout(pollTimer);
    fab.focus({ preventScroll: true });
  }
  still(place);
  fab.addEventListener("click", function () { if (isOpen()) close(); else open(); });
  $("agClose").addEventListener("click", close);
  panel.addEventListener("keydown", function (e) {
    if (e.key !== "Escape" || e.defaultPrevented) return;
    e.stopPropagation();
    if (!histView.hidden) showChat(); else close();
  });

  /* ---------- 대화 ---------- */
  var sid = store("session", "chatWidgetSession") || null;
  var waiting = false, posting = false, generation = 0, pollTimer, quoted = "", lastActions = [];
  var expectAuto = false;      // 이 탭에서 보낸 요청의 답일 때만 커서가 대신 한다(기록에서 연 옛 대화는 단추만)
  var stick = true;            // 맨 아래를 보고 있으면 새 글이 올 때 따라 내려간다
  function setSid(v) {
    if (sid !== v) { generation++; clearTimeout(pollTimer); }
    sid = v ? String(v) : null;
    store("session", "chatWidgetSession", sid);
    if (!sid) setSub("");
  }
  function setSub(t) { sub.textContent = t || ""; sub.hidden = !t; sub.title = t || ""; }
  document.addEventListener("chat-session-deleted", function (event) {
    if (String(sid) !== String(event.detail.id)) return;
    setSid(null); waiting = false; lastActions = []; render([]); setQuote(""); paint();
  });

  function nearBottom() { return body.scrollHeight - body.scrollTop - body.clientHeight < 48; }
  function follow(force) {
    if (force || stick) { body.scrollTop = body.scrollHeight; jump.hidden = true; }
    else jump.hidden = false;
  }
  body.addEventListener("scroll", function () { stick = nearBottom(); if (stick) jump.hidden = true; });
  jump.addEventListener("click", function () { stick = true; follow(true); });

  function grow() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 132) + "px"; }
  function paint() {
    var stop = waiting && !!sid;
    send.classList.toggle("stop", stop);
    send.disabled = posting || (!stop && !input.value.trim());
    send.title = stop ? "멈추기" : "보내기 (Enter)";
    send.setAttribute("aria-label", stop ? "멈추기" : "보내기");
    send.innerHTML = stop ? '<iconify-icon icon="solar:stop-bold"></iconify-icon>' : '<iconify-icon icon="solar:arrow-up-linear"></iconify-icon>';
    newBtn.disabled = posting;
  }

  // 모델이 붙이는 강조 기호는 화면에 그대로 드러내지 않는다
  function plain(text) {
    return String(text || "").replace(/\*\*(.+?)\*\*/g, "$1").replace(/(^|\s)\*(\S[^*]*?)\*(?=\s|$)/g, "$1$2").replace(/^#{1,6}\s+/gm, "");
  }
  // 답 속 화면 주소(/dev/rag#student-regs 등)는 누르면 그 화면으로 — 글은 textContent 로만 넣는다
  function linkify(el, text) {
    var re = /(^|[\s(「])(\/(?:dev|chat|graph|project|projects|settings|search|hwp|team|programs)(?:[\/#?][\w\/#?=&.-]*)?)/g, at = 0, m;
    while ((m = re.exec(text))) {
      var start = m.index + m[1].length;
      el.appendChild(document.createTextNode(text.slice(at, start)));
      var a = document.createElement("a"); a.href = m[2]; a.textContent = m[2];
      el.appendChild(a); at = start + m[2].length;
    }
    el.appendChild(document.createTextNode(text.slice(at)));
  }
  function bubble(role, text, cls) {
    var el = document.createElement("div");
    el.className = "ag-msg " + role + (cls ? " " + cls : "");
    if (role === "user") el.textContent = plain(text); else linkify(el, plain(text));
    body.appendChild(el);
    return el;
  }

  // 빈 화면 — 짧은 인사와 지금 화면에서 할 수 있는 일(안내 자료에서) 칩
  var suggestCache = {};
  function emptyState() {
    var box = document.createElement("div"); box.className = "ag-empty";
    var hi = document.createElement("p"); hi.className = "ag-hi"; hi.textContent = "무엇을 도와드릴까요?";
    var chips = document.createElement("div"); chips.className = "ag-chips";
    box.appendChild(hi); box.appendChild(chips); body.appendChild(box);
    var page = location.pathname;
    function fill(items) {
      chips.innerHTML = "";
      (items || []).forEach(function (q) {
        var b = document.createElement("button"); b.type = "button"; b.textContent = q;
        b.addEventListener("click", function () { input.value = q; grow(); paint(); submit(); });
        chips.appendChild(b);
      });
    }
    if (suggestCache[page]) fill(suggestCache[page]);
    else fetch("/api/helper/suggest?page=" + encodeURIComponent(page), { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : { items: [] }; })
      .then(function (d) { suggestCache[page] = d.items || []; if (chips.isConnected) fill(suggestCache[page]); })
      .catch(function () {});
  }
  function render(messages) {
    body.innerHTML = "";
    if (!messages || !messages.length) { emptyState(); return; }
    messages.forEach(function (m) { bubble(m.role === "user" ? "user" : "assistant", m.content); });
  }
  function waitBubble(label) {
    var el = body.querySelector(".ag-wait");
    if (!el) {
      el = document.createElement("div"); el.className = "ag-msg assistant ag-wait";
      el.innerHTML = '<span class="ag-dots"><i></i><i></i><i></i></span><span class="lbl"></span>';
      body.appendChild(el);
    }
    el.querySelector(".lbl").textContent = label || "답을 만드는 중";
    return el;
  }

  // 도우미가 낸 제안 — run 은 「이렇게 하겠습니다」 와 [실행](누르면 서버가 권한·대상을 다시 보고 실행), go 는 화면에서 할 단계.
  // go 에 act.now 가 있으면(담당자가 열기·찾기·칸 채우기를 시킨 때) 답이 뜨자마자 커서가 차례로 한다 — 여러 제안은 낸 차례대로 이어서, 한 번만(auraDone).
  // 마지막 단추(저장·지우기 등)는 서버가 허락한 단계(ok)만 누르고, 아니면 가리키기만 한 뒤 [실행](사람이 누르는 허락)을 둔다.
  function doneIds() { try { return JSON.parse(store("session", "auraDone") || "[]"); } catch (e) { return []; } }
  function markDone(id) { var d = doneIds(); if (d.indexOf(id) < 0) d.push(id); store("session", "auraDone", JSON.stringify(d.slice(-40))); }
  function segOf(p, mode) {   // mode: auto(서버 허락 그대로) · go(사람이 단추로 — 열기·칸 채우기는 함) · grant(사람이 [실행] — 마지막 단추까지) · point(가리키기만)
    var t = p.target || {}, a = p.act || {};
    var steps = (a.steps || []).map(function (x, i) {
      var ok = mode === "auto" ? !!x.ok : mode === "go" ? !!(x.ok || x.auto) : mode === "grant" ? !!(x.auto || x.final) : false;
      return { i: i, sel: x.sel, text: x.text || "", final: !!x.final, ok: ok, what: x.what || "" };
    });
    if (!steps.length && t.selector) steps = [{ i: -1, sel: t.selector, text: "", final: false, ok: false, what: p.label }];
    return { id: p.kind === "go" ? p.id : "", sid: sid, path: t.path || p.screen, screen: p.screen, anchor: t.anchor || "", label: p.label,
             here: !!a.here, steps: steps, at: 0, res: [] };
  }
  function drawProposals(list, allowAuto) {
    var queue = [];
    (list || []).forEach(function (p) {
      var t = p.target || {}, act = p.act || {}, steps = act.steps || [];
      var sameHere = p.kind === "go" && cursor.here(t);
      var box = document.createElement("div"); box.className = "ag-msg assistant ag-proposal";
      var plan = document.createElement("div"); plan.className = "ag-plan";
      plan.textContent = (p.kind === "go" ? "" : "이렇게 하겠습니다 — ") + p.plan;
      var row = document.createElement("div"); row.className = "ag-acts";
      if (p.kind !== "go") {
        var run = document.createElement("button"); run.type = "button"; run.textContent = "실행";
        run.addEventListener("click", function () {
          if (!sid) return;
          run.disabled = true; run.textContent = "실행 중…";
          if (cursor.here(t)) cursor.run([segOf(p, "point")]);     // 같은 화면이면 그 단추 자리를 가리키며 실행
          fetch("/chat/" + sid + "/proposals/" + encodeURIComponent(p.id) + "/run", { method: "POST" })
            .then(function (r) { return r.json().catch(function () { return { ok: false, error: r.status === 403 ? "관리자 계정만 할 수 있는 일입니다" : "실행 결과를 받지 못했습니다" }; }); })
            .then(function (d) {
              if (!d.ok && d.detail) d.error = d.detail;
              if (d.ok && d.go) { location.href = d.go; return; }
              if (!d.ok) run.textContent = d.error || "실행하지 못했습니다";
              load();
            })
            .catch(function () { run.disabled = false; run.textContent = "실행"; });
        });
        row.appendChild(run);
        var direct = document.createElement("a"); direct.href = p.screen; direct.textContent = "화면에서 직접";
        direct.addEventListener("click", function (ev) { ev.preventDefault(); cursor.run([segOf(p, "point")]); });
        row.appendChild(direct);
      } else {
        // 같은 화면이면 「○○ 열기」 대신 「여기서 하기」, 다른 화면이면 「○○ 열기」 — 사람이 누르면 열기·칸 채우기까지 한다
        var go = document.createElement("a"); go.href = p.screen;
        go.textContent = sameHere ? "여기서 하기" : p.label;
        go.addEventListener("click", function (ev) { ev.preventDefault(); markDone(p.id); cursor.run([segOf(p, "go")]); });
        var finalWait = steps.some(function (x) { return x.final && !x.ok; });
        if (finalWait) {      // 마지막 단추를 허락받지 못한 단계 — 사람이 [실행] 을 누르면 그 단추까지 누른다
          var grant = document.createElement("button"); grant.type = "button"; grant.textContent = "실행";
          grant.title = "마지막 단추(" + steps[steps.length - 1].what + ")까지 AURA 가 누릅니다";
          grant.addEventListener("click", function () { markDone(p.id); grant.disabled = true; cursor.run([segOf(p, "grant")]); });
          row.appendChild(grant);
        }
        if (!(sameHere && !steps.length)) row.appendChild(go);
      }
      box.appendChild(plan); if (row.children.length) box.appendChild(row); body.appendChild(box);
      if (allowAuto && p.kind === "go" && act.now && !p.reported && doneIds().indexOf(p.id) < 0) queue.push(p);
    });
    if (queue.length) {
      queue.forEach(function (p) { markDone(p.id); });
      setTimeout(function () { cursor.run(queue.map(function (p) { return segOf(p, "auto"); })); }, 450);
    }
  }
  // 이 화면에서 바로 시킬 수 있는 일 — 누를 때 실행한다
  function drawActions() {
    if (!lastActions.length) return;
    var wrap = document.createElement("div"); wrap.className = "ag-acts";
    lastActions.forEach(function (a) {
      var el;
      if (a.method === "post") {
        el = document.createElement("form"); el.method = "post"; el.action = a.url; el.style.margin = "0";
        var b = document.createElement("button"); b.type = "submit"; b.textContent = a.label; el.appendChild(b);
      } else { el = document.createElement("a"); el.href = a.url; el.textContent = a.label; }
      wrap.appendChild(el);
    });
    body.appendChild(wrap);
  }

  function load(first) {
    if (!sid || !isOpen()) return;
    clearTimeout(pollTimer);
    var asked = sid, token = generation;
    fetch("/chat/" + sid + "/messages", { cache: "no-store" })
      .then(function (r) { if (!r.ok || r.redirected) { var er = new Error(); er.status = r.status; throw er; } return r.json(); })
      .then(function (d) {
        if (token !== generation || asked !== sid) return;
        if (d.channel !== "helper" && (d.project_id || null) !== (pageProject || null)) {
          // 다른 프로젝트(또는 프로젝트 밖) 대화는 이 화면에서 이어 쓰지 않는다 — 이어 쓰기는 기록에서 고른다
          setSid(null); waiting = false; lastActions = []; render([]); paint(); return;
        }
        if (!Array.isArray(d.messages)) throw new Error();
        var keepPos = !stick ? body.scrollTop : null, h0 = body.scrollHeight;
        render(d.messages);
        var firstQ = d.messages.find(function (m) { return m.role === "user"; });
        setSub(d.title || (firstQ ? firstQ.content : ""));
        waiting = !!d.waiting;
        if (waiting) {
          status();
          pollTimer = setTimeout(function () { if (token === generation) load(); }, 1500);
        } else { drawProposals(d.proposals, expectAuto); expectAuto = false; drawActions(); }
        paint();
        if (keepPos !== null && !first) { body.scrollTop = keepPos; if (body.scrollHeight > h0 + 4) jump.hidden = false; } else follow(true);
      })
      .catch(function (error) {
        if (token !== generation) return;
        if (error.status === 403 || error.status === 404) { setSid(null); waiting = false; lastActions = []; render([]); paint(); return; }
        waitBubble("연결을 다시 확인하는 중");
        pollTimer = setTimeout(function () { if (token === generation) load(); }, 4000);
      });
  }
  // 답을 만드는 동안 지금 걸음(요청 확인·화면 안내 찾기 등)을 대기 말풍선에 보인다
  function status() {
    var el = waitBubble(), asked = sid;
    fetch("/chat/" + sid + "/status", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || asked !== sid || !el.isConnected) return;
        var steps = (d.progress && d.progress.steps) || [];
        if (steps.length) el.querySelector(".lbl").textContent = steps[steps.length - 1];
      }).catch(function () {});
  }

  function setQuote(text) {
    quoted = text || "";
    quoteText.textContent = quoted; quote.hidden = !quoted;
  }
  $("agQuoteClear").addEventListener("click", function () { setQuote(""); input.focus(); });

  function stopAnswer() {
    if (!sid) return;
    send.disabled = true;
    fetch("/chat/" + sid + "/stop", { method: "POST" })
      .then(function () { load(); }).catch(function () { paint(); });
  }
  function submit() {
    var q = input.value.trim();
    if (!q || waiting || posting) return;
    posting = true; waiting = true; expectAuto = true; paint();
    var sentQuote = quoted, token = generation;
    var fd = new FormData();
    fd.append("question", q);
    if (quoted) fd.append("context", quoted);
    fd.append("page", location.pathname);
    if (body.querySelector(".ag-empty")) body.innerHTML = "";
    var mine = bubble("user", quoted ? "「" + quoted.slice(0, 120) + "」\n" + q : q);
    var wait = waitBubble("요청 확인 중");
    stick = true; follow(true);
    input.value = ""; grow(); setQuote("");
    if (sid) fd.append("session_id", sid);
    fetch("/chat/ask", { method: "POST", body: fd })
      .then(function (r) { if (!r.ok || r.redirected) throw new Error(); return r.json(); })
      .then(function (d) {
        if (token !== generation) return;
        if (!d.ok) { var er = new Error(d.error || ""); er.said = d.error; throw er; }
        posting = false; setSid(d.session_id); lastActions = d.actions || []; load(true);
      })
      .catch(function (error) {
        if (token !== generation) return;
        posting = false; waiting = false;
        mine.remove(); wait.remove();
        // 답을 기다리는 동안 적은 다음 질문도 버리지 않는다
        input.value = q + (input.value ? "\n\n" + input.value : "");
        setQuote(sentQuote); grow(); paint();
        var er = bubble("assistant", error && error.said ? error.said + " — 입력은 그대로 두었습니다." :
          "보내지 못했습니다. 입력은 그대로 두었으니 잠시 뒤 다시 보내 주세요.", "ag-err");
        er.setAttribute("role", "status"); follow(true); input.focus();
      });
  }
  send.addEventListener("click", function () { if (send.classList.contains("stop")) stopAnswer(); else submit(); });
  input.addEventListener("input", function () { grow(); paint(); });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); submit(); }
  });
  newBtn.addEventListener("click", function () {
    if (posting) return;
    var go = function () {
      setSid(null); waiting = false; lastActions = []; showChat(); render([]); setQuote(""); input.value = ""; grow(); paint(); input.focus();
    };
    if (input.value.trim() && window.platformConfirm) {
      window.platformConfirm("적던 내용을 지우고 새 대화를 시작할까요?", { title: "새 대화", ok: "새 대화" }).then(function (y) { if (y) go(); });
    } else go();
  });

  /* ---------- 지난 대화 ---------- */
  function showChat() {
    histView.hidden = true; chatView.hidden = false; back.hidden = true;
    title.textContent = "업무 도우미"; histBtn.classList.remove("on");
    if (sid && sub.textContent) sub.hidden = false;
  }
  function showHist() {
    chatView.hidden = true; histView.hidden = false; back.hidden = false;
    title.textContent = "지난 대화"; sub.hidden = true; histBtn.classList.add("on");
    histFind.value = ""; loadHist(true);
    setTimeout(function () { histFind.focus(); }, 50);
  }
  histBtn.addEventListener("click", function () { if (histView.hidden) showHist(); else showChat(); });
  back.addEventListener("click", showChat);
  var histOffset = 0, histToken = 0, findTimer;
  histFind.addEventListener("input", function () { clearTimeout(findTimer); findTimer = setTimeout(function () { loadHist(true); }, 220); });
  function dayKey(d) { return d.getFullYear() * 10000 + d.getMonth() * 100 + d.getDate(); }
  function groupOf(d) {
    var now = new Date(), today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var diff = Math.floor((today - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
    if (diff <= 0) return "오늘";
    if (diff === 1) return "어제";
    if (diff < 7) return "지난 7일";
    if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth()) return "이번 달";
    return d.getFullYear() === now.getFullYear() ? (d.getMonth() + 1) + "월" : d.getFullYear() + "년 " + (d.getMonth() + 1) + "월";
  }
  function whenText(d) {
    var now = new Date();
    if (dayKey(d) === dayKey(now)) return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    return (d.getMonth() + 1) + "월 " + d.getDate() + "일";
  }
  function loadHist(reset) {
    if (reset) { histOffset = 0; histList.innerHTML = '<p class="ag-hnote">불러오는 중…</p>'; }
    var token = ++histToken, q = histFind.value.trim();
    fetch("/api/chat/sessions?channel=helper&scope=all&offset=" + histOffset + (q ? "&q=" + encodeURIComponent(q) : ""), { cache: "no-store" })
      .then(function (r) { if (!r.ok || r.redirected) throw new Error(); return r.json(); })
      .then(function (d) {
        if (token !== histToken) return;
        var list = ((d && d.sessions) || []).slice().sort(function (x, y) { return String(y.last_at || "").localeCompare(String(x.last_at || "")); });
        if (reset) histList.innerHTML = "";
        var more = histList.querySelector(".ag-hnext"); if (more) more.remove();
        if (!list.length && reset) {
          histList.innerHTML = '<p class="ag-hnote"></p>';
          histList.firstChild.textContent = q ? "「" + q + "」 에 맞는 대화가 없습니다." : "아직 나눈 대화가 없습니다.";
          return;
        }
        var lastGroup = histList.dataset.lastGroup || "";
        if (reset) lastGroup = "";
        list.forEach(function (row) {
          var d = new Date(row.last_at || row.created_at || Date.now());
          if (isNaN(d)) d = new Date();
          var g = groupOf(d);
          if (g !== lastGroup) {
            var h = document.createElement("div"); h.className = "ag-hgroup"; h.textContent = g; histList.appendChild(h); lastGroup = g;
          }
          histList.appendChild(histRow(row, d));
        });
        histList.dataset.lastGroup = lastGroup;
        histOffset += list.length;
        if (d.has_more) {
          var b = document.createElement("button"); b.type = "button"; b.className = "btn-ghost secondary ag-hnext"; b.textContent = "더 보기";
          b.addEventListener("click", function () { b.disabled = true; b.textContent = "불러오는 중…"; loadHist(false); });
          histList.appendChild(b);
        }
      })
      .catch(function () { if (token === histToken) histList.innerHTML = '<p class="ag-hnote">기록을 불러오지 못했습니다. 잠시 뒤 다시 열어 주세요.</p>'; });
  }
  function histRow(row, d) {
    var id = String(row.id), name = row.title || "이름 없는 대화";
    var el = document.createElement("div"); el.className = "ag-hrow"; el.setAttribute("role", "listitem");
    var cur = id === String(sid);
    if (cur) el.classList.add("cur");
    var openB = document.createElement("button"); openB.type = "button"; openB.className = "ag-hopen";
    openB.innerHTML = '<span class="t"></span>' + (cur ? '<span class="cur-tag">지금 대화</span>' : "") + '<span class="when"></span>';
    openB.querySelector(".t").textContent = name; openB.title = name;
    openB.querySelector(".when").textContent = whenText(d);
    if (cur) openB.setAttribute("aria-current", "true");
    openB.addEventListener("click", function () {
      setSid(id); lastActions = []; setSub(name); showChat(); body.innerHTML = ""; stick = true; load(true); input.focus();
    });
    var more = document.createElement("button"); more.type = "button"; more.className = "ag-hmore"; more.textContent = "⋯";
    more.setAttribute("aria-label", name + " 대화 메뉴"); more.setAttribute("aria-haspopup", "menu"); more.setAttribute("aria-expanded", "false");
    more.addEventListener("click", function () { rowMenu(el, more, id, name); });
    el.appendChild(openB); el.appendChild(more);
    return el;
  }
  // 행 … 메뉴 — 사이드바 대화 메뉴와 같은 꼴(session-popover)
  function rowMenu(row, opener, id, name) {
    document.querySelector(".session-popover")?.remove();
    var pop = document.createElement("div"); pop.className = "session-popover"; pop.setAttribute("popover", "auto"); pop.setAttribute("role", "menu");
    pop.innerHTML = '<button type="button" class="session-menu-action" data-rename role="menuitem"><iconify-icon icon="solar:pen-linear"></iconify-icon>이름 바꾸기</button>'
      + '<button type="button" class="session-delete-action" data-delete role="menuitem"><iconify-icon icon="solar:trash-bin-trash-linear"></iconify-icon>지우기</button>';
    document.body.appendChild(pop); pop.showPopover(); opener.setAttribute("aria-expanded", "true");
    var r = opener.getBoundingClientRect();
    pop.style.left = Math.max(8, Math.min(r.right - pop.offsetWidth, innerWidth - pop.offsetWidth - 8)) + "px";
    pop.style.top = Math.max(8, Math.min(r.bottom + 4, innerHeight - pop.offsetHeight - 8)) + "px";
    pop.querySelector("button").focus();
    pop.addEventListener("toggle", function (e) { if (e.newState === "closed") { pop.remove(); opener.setAttribute("aria-expanded", "false"); } });
    pop.querySelector("[data-rename]").addEventListener("click", function () { pop.hidePopover(); renameRow(row, id, name); });
    pop.querySelector("[data-delete]").addEventListener("click", function () {
      pop.hidePopover();
      var ask = window.platformConfirm ? window.platformConfirm("「" + name + "」 대화를 지울까요? 되돌릴 수 없습니다.", { title: "대화 지우기", ok: "지우기", danger: true })
                                       : Promise.resolve(confirm("대화를 지울까요?"));
      ask.then(function (yes) {
        if (!yes) return;
        opener.disabled = true; row.style.opacity = ".5";
        fetch("/api/chat/sessions/" + id, { method: "DELETE" })
          .then(function (res) {
            if (!res.ok || res.redirected) throw new Error(res.status === 409 ? "답이 끝난 뒤 지울 수 있습니다." : "지우지 못했습니다. 잠시 뒤 다시 해 주세요.");
            var prev = row.previousElementSibling, next = row.nextElementSibling;
            row.remove();
            if (prev && prev.classList.contains("ag-hgroup") && (!next || !next.classList.contains("ag-hrow"))) prev.remove();
            if (!histList.querySelector(".ag-hrow")) loadHist(true);
            document.dispatchEvent(new CustomEvent("chat-session-deleted", { detail: { id: id } }));
          })
          .catch(function (err) { opener.disabled = false; row.style.opacity = ""; alertRow(row, err.message); });
      });
    });
  }
  function alertRow(row, text) {
    var p = document.createElement("p"); p.className = "ag-hnote"; p.setAttribute("role", "alert"); p.style.margin = "2px 8px 6px"; p.style.textAlign = "left";
    p.textContent = text; row.after(p); setTimeout(function () { p.remove(); }, 4000);
  }
  function renameRow(row, id, name) {
    var form = document.createElement("form"); form.className = "ag-hrename";
    form.innerHTML = '<input required maxlength="80" aria-label="대화 이름"><button type="submit" class="btn">저장</button><button type="button" class="secondary">취소</button>';
    var field = form.querySelector("input"); field.value = name;
    var kept = Array.prototype.slice.call(row.childNodes);
    kept.forEach(function (n) { n.remove(); }); row.appendChild(form); field.focus(); field.select();
    function restore() { form.remove(); kept.forEach(function (n) { row.appendChild(n); }); }
    form.querySelector(".secondary").addEventListener("click", restore);
    field.addEventListener("keydown", function (e) { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); restore(); } });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var v = field.value.trim(); if (!v) return;
      form.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
      fetch("/api/chat/sessions/" + id, { method: "POST", body: new URLSearchParams({ title: v }) })
        .then(function (res) { if (!res.ok || res.redirected) throw new Error("이름을 바꾸지 못했습니다. 1~80자로 적어 주세요."); })
        .then(function () {
          restore(); var t = row.querySelector(".t"); t.textContent = v; row.querySelector(".ag-hopen").title = v;
          if (String(sid) === id) setSub(v);
        })
        .catch(function (err) { form.querySelectorAll("button").forEach(function (b) { b.disabled = false; }); alertRow(row, err.message); });
    });
  }

  /* ---------- AURA 커서 ---------- */
  // 도우미가 낸 단계를 화면에서 차례로 한다: 다른 화면이면 그 화면 메뉴를 눌러 옮겨 가고(남은 일은 sessionStorage auraQueue 에 두고 새 화면에서 잇는다),
  // 그 화면에서 창 열기 → 칸 채우기(입력·고르기) → (허락된 때만) 마지막 단추. 한 일·못 한 일은 서버에 보고해 대화에 한 줄로 남는다(완료 문구는 그 보고로만).
  var cursor = (function () {
    var QKEY = "auraQueue", el = null, ringOn = null, leaving = false;
    var calm = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    addEventListener("beforeunload", function () { leaving = true; });
    function pathOf(p) { try { return new URL(p || "/", location.href).pathname; } catch (e) { return p || ""; } }
    function visible(x) { var r = x.getBoundingClientRect(); return x.offsetParent !== null && r.width > 0 && r.height > 0; }
    function squash(t) { return String(t || "").replace(/[\s·「」()]+/g, "").toLowerCase(); }
    function isField(x) { return /^(INPUT|TEXTAREA)$/.test(x.tagName) && !/^(checkbox|radio|file|hidden|submit|button)$/i.test(x.type || ""); }
    // 요소 찾기 — 입력 칸·고르기 칸이 아니면서 text 가 있으면 같은 표시의 여럿 가운데 그 이름이 든 것(폴더·문서·부서·칩)
    function find(st) {
      var hit = null;
      try {
        var all = st.sel ? Array.prototype.filter.call(document.querySelectorAll(st.sel), function (x) { return visible(x) && !panel.contains(x); }) : [];
        if (st.text && all.length && !isField(all[0]) && all[0].tagName !== "SELECT") {
          var want = squash(st.text);
          hit = all.find(function (x) { return squash(x.textContent) === want; }) || all.find(function (x) { return squash(x.textContent).indexOf(want) >= 0; }) || null;
        } else hit = all[0] || null;
        if (!hit && !st.sel && st.anchor) hit = document.getElementById(st.anchor);
      } catch (e) { hit = null; }
      return hit;
    }
    function waitFor(st, cb) {       // 앞 단계가 연 창·메뉴가 그려질 때까지 잠깐 기다린다
      var t0 = Date.now();
      (function look() {
        var hit = find(st);
        if (hit || Date.now() - t0 > 3500) { cb(hit); return; }
        setTimeout(look, 150);
      })();
    }
    function make() {
      if (el && el.isConnected) return el;
      el = document.createElement("div"); el.className = "aura-cursor"; el.setAttribute("aria-hidden", "true");
      el.innerHTML = '<svg class="ac-svg" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.2 1.6 13.6 7.3c.6.3.5 1.1-.1 1.3l-4.4 1.2-2 4.3c-.3.6-1.1.5-1.3-.1L2.2 1.6Z" fill="#1B3B75" stroke="#fff" stroke-width="1.3" stroke-linejoin="round"/></svg><span class="aura-cursor-tag"><span class="ac-text">AURA</span></span>';
      var from = isOpen() ? panel.getBoundingClientRect() : fab.getBoundingClientRect();   // 처음 자리 — 열린 패널 왼쪽 아래(닫혔으면 둥근 단추)
      el.style.left = Math.round(from.left + (isOpen() ? 24 : from.width / 2)) + "px";
      el.style.top = Math.round(from.top + (isOpen() ? from.height - 90 : from.height / 2)) + "px";
      document.body.appendChild(el); void el.offsetWidth;
      return el;
    }
    function tag(text) { make().querySelector(".ac-text").textContent = text ? "AURA · " + text : "AURA"; doing(text); }
    // 대상으로 옮겨 가 누르는 모양을 보인 뒤 then(hit)
    function point(hit, label, then) {
      if (!hit) { if (then) then(null); return; }
      hit.scrollIntoView(calm ? { block: "nearest" } : { behavior: "smooth", block: "nearest" });
      var c = make(); c.classList.remove("leaving"); tag(label);
      var t0 = Date.now(), pressed = false;
      if (ringOn && ringOn !== hit) ringOn.classList.remove("aura-ring");
      ringOn = hit; clearTimeout(c._follow);
      (function track() {      // 0.1초마다 대상 자리를 다시 재어 따라간다(부드러운 스크롤·늦게 그려지는 목록)
        var r = hit.getBoundingClientRect(), age = Date.now() - t0;
        if (age > 600 && age < 2000 && (r.top < 0 || r.bottom > innerHeight)) { hit.scrollIntoView({ block: "center", behavior: "instant" }); r = hit.getBoundingClientRect(); }
        c.style.left = Math.round(r.left + Math.min(r.width / 2, 40)) + "px";
        c.style.top = Math.round(r.top + Math.min(r.height / 2, 18)) + "px";
        if (!pressed && age > (calm ? 0 : 900)) {
          pressed = true; hit.classList.add("aura-ring"); c.classList.add("press");
          setTimeout(function () { c.classList.remove("press"); if (then) then(hit); }, 380);
        }
        // 좁은 폭의 아래 시트가 대상을 덮으면 가리키는 동안만 시트를 옅게(누름 통과)
        var pr = panel.getBoundingClientRect();
        var over = isOpen() && !(r.right < pr.left || r.left > pr.right || r.bottom < pr.top || r.top > pr.bottom);
        panel.classList.toggle("ag-peek", !!over && age < 3200);
        if (age < 3200) { c._follow = setTimeout(track, 100); return; }
        panel.classList.remove("ag-peek"); hit.classList.remove("aura-ring");
      })();
    }
    function fade() { if (el) el.classList.add("leaving"); doing(""); }
    function nameOf(x) { return (x.getAttribute("aria-label") || x.textContent || x.placeholder || "").replace(/\s+/g, " ").trim().slice(0, 24); }

    // ---- 차례 ----
    function save(q) { store("session", QKEY, q && q.length ? JSON.stringify({ q: q, at: Date.now() }) : null); }
    function report(seg) {     // 이 제안의 결과를 서버에 — 대화에 「화면에서 한 일」 한 줄이 생긴다
      if (!seg.id || !seg.sid || !seg.res.length || seg.sent) return;
      seg.sent = true;
      fetch("/chat/" + seg.sid + "/proposals/" + encodeURIComponent(seg.id) + "/result", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ results: seg.res })
      }).then(function () { if (String(seg.sid) === String(sid) && isOpen()) load(); }).catch(function () {});
    }
    function stop(q, seg, code, why) {
      if (code) seg.res.push({ step: seg.steps[seg.at] ? seg.steps[seg.at].i : -1, result: code });
      if (why) tag(why);
      report(seg); save(null);
      setTimeout(fade, 1600);
    }
    function run(q) {
      if (!q || !q.length) { save(null); setTimeout(fade, 1200); return; }
      var seg = q[0];
      if (!seg.here && pathOf(seg.path) !== location.pathname) {      // 다른 화면 — 보이는 메뉴를 커서로 눌러 옮겨 간다
        seg.here = true; seg.res.push({ step: -1, result: "opened" }); save(q);
        var link = navLink(seg.screen);
        tag((seg.label || "화면") + " 여는 중");
        if (link) point(link, nameOf(link) + " 여는 중", function () { location.href = seg.screen; });
        else location.href = seg.screen;
        return;
      }
      seg.here = true;
      next(q, seg);
    }
    function next(q, seg) {
      if (seg.at >= seg.steps.length) {
        if (!seg.steps.length && seg.anchor) point(document.getElementById(seg.anchor), seg.label);
        report(seg); q.shift(); save(q); run(q); return;
      }
      var st = seg.steps[seg.at];
      waitFor(st, function (hit) {
        if (!hit) { stop(q, seg, "missing", (st.what || "가리킬 자리") + " — 찾지 못함"); return; }
        var field = isField(hit), pick = hit.tagName === "SELECT";
        var what = st.what || nameOf(hit), label;
        if (field) { what = what.replace(/ 채우기$/, ""); label = (/칸$/.test(what) ? what : what + " 칸") + "에 입력 중"; }
        else if (pick) label = what.replace(/ 고르기$/, "") + " 고르는 중";
        else label = "「" + (st.text || nameOf(hit)) + "」 누르는 중";
        point(hit, st.ok ? label : nameOf(hit) + " — 직접 눌러 주세요", function (h) {
          if (!st.ok) { stop(q, seg, st.final ? "pointed" : "blocked", ""); return; }
          if (h.disabled || h.getAttribute("aria-disabled") === "true") { stop(q, seg, "disabled", nameOf(h) + " — 지금은 누를 수 없음"); return; }
          if (field) { typeInto(h, st.text, function () { done(q, seg, "typed", h); }); return; }
          if (pick) { done(q, seg, choose(h, st.text) ? "picked" : "missing", h); return; }
          // 보안: 화면에서도 한 번 더 — 제출 단추·POST 폼 단추·마지막 단추 표시는 서버가 허락한 마지막 단계(final+ok)만 누른다
          var submitLike = h.matches("[data-aura-final], [type=submit], form button:not([type=button])") || (h.closest('form[method="post" i]') && h.matches("button, input"));
          if (submitLike && !st.final) { stop(q, seg, "blocked", nameOf(h) + " — 직접 눌러 주세요"); return; }
          if (!submitLike && !h.matches("[data-modal-open], a[href], [data-aura], [data-om-find], [data-org-add]")) { stop(q, seg, "blocked", nameOf(h) + " — 직접 눌러 주세요"); return; }
          seg.at++; seg.res.push({ step: st.i, result: "clicked" });
          save(q);                                   // 누른 단추가 화면을 옮기면 새 화면에서 남은 단계를 잇는다
          h.click();
          setTimeout(function () { if (leaving) return; next(q, seg); }, 500);
        });
      });
    }
    function done(q, seg, code, h) {
      seg.res.push({ step: seg.steps[seg.at].i, result: code });
      if (code === "missing") { stop(q, seg, "", "고를 항목을 찾지 못함"); return; }
      seg.at++; save(q);
      // 찾기 칸(GET 폼)이 마지막 단계면 찾기를 보낸다 — POST 폼은 보내지 않는다(마지막 단추는 따로)
      var f = h.form;
      if (code === "typed" && seg.at >= seg.steps.length && f && (f.getAttribute("method") || "get").toLowerCase() === "get" && !f.hasAttribute("data-tpl-search")) {
        report(seg); q.shift(); save(q);
        setTimeout(function () { if (f.requestSubmit) f.requestSubmit(); else f.submit(); }, 350);
        setTimeout(function () { if (!leaving) run(q); }, 900);
        return;
      }
      setTimeout(function () { next(q, seg); }, 250);
    }
    function typeInto(field, text, then) {
      field.focus(); field.value = "";
      var i = 0;
      (function step() {
        if (i < text.length) {
          field.value += text[i++]; field.dispatchEvent(new Event("input", { bubbles: true }));
          setTimeout(step, calm ? 0 : 55); return;
        }
        field.dispatchEvent(new Event("change", { bubbles: true }));
        then();
      })();
    }
    function choose(sel, text) {
      var want = squash(text), opts = Array.prototype.slice.call(sel.options || []);
      var o = opts.find(function (x) { return squash(x.textContent) === want; }) || opts.find(function (x) { return want && squash(x.textContent).indexOf(want) >= 0; });
      if (!o) return false;
      sel.value = o.value; sel.dispatchEvent(new Event("input", { bubbles: true })); sel.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }
    // 다른 화면 — 화면에 보이는 그 화면 메뉴(사이드바 등). 없으면 바로 옮겨 간다
    function navLink(url) {
      var want = new URL(url, location.href), best = null;
      document.querySelectorAll("a[href]").forEach(function (a) {
        if (best || panel.contains(a) || !visible(a)) return;
        var u; try { u = new URL(a.href, location.href); } catch (e) { return; }
        if (u.origin !== location.origin || u.pathname !== want.pathname) return;
        if (want.search && u.search !== want.search && !(want.pathname === "/" && u.search.indexOf("type=") >= 0)) return;
        best = a;
      });
      return best;
    }
    function resume() {
      var v = null;
      try { v = JSON.parse(store("session", QKEY) || "null"); } catch (e) { v = null; }
      save(null);
      if (!v || !Array.isArray(v.q) || !v.q.length || Date.now() - v.at > 20000) return false;
      setTimeout(function () { run(v.q); }, 450);
      return true;
    }
    return { run: run, resume: resume, here: function (t) { return !!t && pathOf(t.path) === location.pathname; } };
  })();
  // 커서가 하는 일을 패널 머리에도 — 끝나면 원래 부제(대화 제목)로
  var subBefore = null;
  function doing(text) {
    if (text) { if (subBefore === null) subBefore = sub.textContent; sub.textContent = "AURA · " + text; sub.hidden = false; sub.classList.add("ag-doing"); }
    else if (subBefore !== null) { setSub(subBefore); subBefore = null; sub.classList.remove("ag-doing"); }
  }

  /* ---------- 글을 고른 뒤 오른쪽 단추 — 고른 글이 있을 때만 우리 메뉴 ---------- */
  function selectedText() { var s = window.getSelection(); return s && s.toString().trim() ? s.toString().trim() : ""; }
  function hideMenu() { menu.classList.remove("open"); }
  document.addEventListener("contextmenu", function (e) {
    if (panel.contains(e.target) || menu.contains(e.target)) return;
    var t = selectedText();
    if (!t) { hideMenu(); return; }
    e.preventDefault();
    menu.style.left = Math.min(e.clientX, innerWidth - 190) + "px";
    menu.style.top = Math.min(e.clientY, innerHeight - 180) + "px";
    menu.classList.add("open");
    menu.dataset.text = t.length > 1200 ? t.slice(0, 1200) : t;
  });
  document.addEventListener("click", function (e) { if (!menu.contains(e.target)) hideMenu(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") hideMenu(); });
  $("agMenuAsk").addEventListener("click", function () { setQuote(menu.dataset.text || ""); hideMenu(); open(); });
  menu.querySelectorAll(".ag-menu-go[data-q]").forEach(function (b) {
    b.addEventListener("click", function () {
      setQuote(menu.dataset.text || ""); hideMenu(); open();
      input.value = b.dataset.q; grow(); paint(); submit();
    });
  });

  /* ---------- 시작 ---------- */
  paint();
  // 페이지를 옮겨도 저절로 다시 열지 않는다 — AURA 커서가 화면을 옮겨 이어 하는 때만 연다
  if (cursor.resume()) open({ instant: true, noFocus: true });
  else render([]);
})();
