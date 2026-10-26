/* 떠 있는 업무 도우미(templates/_aura_helper.html) — 묻기·지난 대화·창 모양(떠 있는 창 / 오른쪽 붙이기 / 아래 시트)과
   도우미가 화면을 조작할 때 움직이는 AURA 커서.
   - 창 모양·크기·열림은 이 브라우저에 기억한다(localStorage auraHelper). 페이지를 옮겨도 열린 채 이어진다.
   - 대화 번호는 탭마다(sessionStorage chatWidgetSession). 기록에서 고르면 그 대화를 이어 간다.
   - 도우미가 「바로 해 달라」 는 화면 열기(open_page now)를 내면 커서가 사이드바 메뉴를 눌러 그 화면으로 가고, 그 화면에서
     가리킬 요소를 누르거나(창 열기) 찾기 칸에 말을 넣어 찾는다. 자료를 바꾸는 일은 [실행] 을 사람이 눌러야 한다. */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var fab = $("agFab"), panel = $("agPanel");
  if (!fab || !panel) return;
  var body = $("agBody"), input = $("agInput"), send = $("agSend"), jump = $("agJump");
  var quote = $("agQuote"), quoteText = $("agQuoteText"), menu = $("agMenu");
  var title = $("agTitle"), sub = $("agSub"), back = $("agBack");
  var chatView = $("agChatView"), histView = $("agHistView"), histList = $("agHistList"), histFind = $("agHistFind");
  var dockBtn = $("agDock"), newBtn = $("agNew"), histBtn = $("agHist");
  var pageProject = panel.dataset.project ? Number(panel.dataset.project) : null;

  function store(kind, key, value) {
    try {
      var s = kind === "local" ? localStorage : sessionStorage;
      if (value === undefined) return s.getItem(key);
      if (value === null) s.removeItem(key); else s.setItem(key, value);
    } catch (e) { return null; }
    return null;
  }

  /* ---------- 창 모양: float(떠 있는 창) · dock(오른쪽 붙이기) · sheet(좁은 폭 아래 시트) ---------- */
  var ui = { open: false, mode: "float", w: 400, h: 600, dw: 440 };
  try { var saved = JSON.parse(store("local", "auraHelper") || "null"); if (saved) for (var k in ui) if (k in saved) ui[k] = saved[k]; } catch (e) {}
  function keep() { store("local", "auraHelper", JSON.stringify(ui)); }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function narrow() { return innerWidth < 640; }
  function shape() { return narrow() ? "sheet" : ui.mode === "dock" ? "dock" : "float"; }
  function place() {
    var vw = document.documentElement.clientWidth || innerWidth, vh = innerHeight, m = shape(), r;
    if (m === "sheet") r = { l: 0, t: Math.round(vh * 0.1), w: vw, h: vh - Math.round(vh * 0.1) };
    else if (m === "dock") { var dw = clamp(ui.dw, 360, Math.max(360, Math.min(760, vw * 0.6))); r = { l: vw - dw, t: 0, w: dw, h: vh }; }
    else {
      var w = clamp(ui.w, 340, Math.max(340, vw - 32)), h = clamp(ui.h, 380, Math.max(380, vh - 32));
      r = { l: vw - 22 - w, t: vh - 22 - h, w: w, h: h };
    }
    panel.style.left = r.l + "px"; panel.style.top = r.t + "px"; panel.style.width = r.w + "px"; panel.style.height = r.h + "px";
    panel.classList.toggle("ag-docked", m === "dock");
    panel.classList.toggle("ag-sheet", m === "sheet");
    var docked = m === "dock";
    dockBtn.setAttribute("aria-pressed", docked ? "true" : "false");
    dockBtn.dataset.tip = docked ? "떠 있는 창으로" : "오른쪽에 붙이기";
    dockBtn.setAttribute("aria-label", dockBtn.dataset.tip);
  }
  function still(fn) {            // 처음 그리기·창 크기 바뀜은 움직임 없이
    panel.classList.add("ag-still"); fn(); void panel.offsetWidth;
    requestAnimationFrame(function () { panel.classList.remove("ag-still"); });
  }
  still(place);
  var resizeTimer;
  addEventListener("resize", function () { clearTimeout(resizeTimer); resizeTimer = setTimeout(function () { still(place); }, 60); });
  dockBtn.addEventListener("click", function () { ui.mode = ui.mode === "dock" ? "float" : "dock"; keep(); place(); });

  // 크기 조절 — 떠 있는 창은 오른쪽 아래를 붙박고 왼쪽·위로 늘린다, 붙인 창은 폭만
  panel.querySelectorAll("[data-rz]").forEach(function (h) {
    h.addEventListener("pointerdown", function (ev) {
      if (shape() === "sheet") return;
      ev.preventDefault(); try { h.setPointerCapture(ev.pointerId); } catch (e) {}
      var how = h.dataset.rz, x0 = ev.clientX, y0 = ev.clientY, w0 = panel.offsetWidth, h0 = panel.offsetHeight, dock = shape() === "dock";
      panel.classList.add("ag-dragging"); document.body.classList.add("ag-resizing");
      function move(e) {
        if (how !== "t") { if (dock) ui.dw = w0 + (x0 - e.clientX); else ui.w = w0 + (x0 - e.clientX); }
        if (how !== "l" && !dock) ui.h = h0 + (y0 - e.clientY);
        place();
      }
      function up() {
        removeEventListener("pointermove", move); removeEventListener("pointerup", up); removeEventListener("pointercancel", up);
        panel.classList.remove("ag-dragging"); document.body.classList.remove("ag-resizing");
        if (dock) ui.dw = panel.offsetWidth; else { ui.w = panel.offsetWidth; ui.h = panel.offsetHeight; }
        keep();
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
        fab.setAttribute("aria-expanded", "true");
      };
      if (opts.instant) still(go); else go();
      ui.open = true; keep();
    }
    showChat();
    if (sid) load(); else if (!body.children.length) render([]);
    if (!opts.noFocus) setTimeout(function () { if (isOpen()) input.focus({ preventScroll: true }); }, 200);
  }
  function close() {
    panel.classList.remove("open"); document.body.classList.remove("ag-open");
    panel.inert = true; panel.setAttribute("aria-hidden", "true");
    fab.setAttribute("aria-expanded", "false"); clearTimeout(pollTimer);
    ui.open = false; keep(); fab.focus({ preventScroll: true });
  }
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

  // 도우미가 낸 제안 — run 은 「이렇게 하겠습니다」 와 [실행](누르면 서버가 권한·대상을 다시 보고 실행), go 는 화면 열기.
  // go 에 act.now 가 있으면(담당자가 열기·누르기·찾기를 시킨 때) 답이 뜨자마자 커서가 대신 한다 — 한 번만(auraDone)
  function doneIds() { try { return JSON.parse(store("session", "auraDone") || "[]"); } catch (e) { return []; } }
  function markDone(id) { var d = doneIds(); if (d.indexOf(id) < 0) d.push(id); store("session", "auraDone", JSON.stringify(d.slice(-40))); }
  function drawProposals(list, allowAuto) {
    var auto = null;
    (list || []).forEach(function (p) {
      var t = p.target || {}, act = p.act || {};
      var box = document.createElement("div"); box.className = "ag-msg assistant ag-proposal";
      box.dataset.targetPath = t.path || ""; box.dataset.targetSelector = t.selector || "";
      var plan = document.createElement("div"); plan.className = "ag-plan";
      plan.textContent = (p.kind === "go" ? "" : "이렇게 하겠습니다 — ") + p.plan;
      var row = document.createElement("div"); row.className = "ag-acts";
      var go = document.createElement("a"); go.href = p.screen;
      go.textContent = p.kind === "go" ? p.label : "화면에서 직접";
      go.addEventListener("click", function (ev) {
        ev.preventDefault();
        if (p.kind === "go") { markDone(p.id); perform(p); return; }
        if (cursor.here(t)) cursor.point(t, p.label); else { cursor.later(t, p.label); location.href = p.screen; }
      });
      if (p.kind !== "go") {
        var run = document.createElement("button"); run.type = "button"; run.textContent = "실행";
        run.addEventListener("click", function () {
          if (!sid) return;
          run.disabled = true; run.textContent = "실행 중…";
          if (cursor.here(t)) cursor.point(t, p.label);     // 같은 화면이면 그 단추 자리를 가리키며 실행
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
      }
      row.appendChild(go);
      box.appendChild(plan); box.appendChild(row); body.appendChild(box);
      if (allowAuto && p.kind === "go" && act.now && !auto && doneIds().indexOf(p.id) < 0) auto = p;
    });
    if (auto) { markDone(auto.id); setTimeout(function () { perform(auto); }, 500); }
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
  // 도우미가 가리키는 화면 요소로 커서가 움직여 누른다. 다른 화면이면 사이드바 메뉴를 눌러 옮겨 간 뒤 그 화면에서 이어 한다
  var cursor = (function () {
    var KEY = "auraPoint", el = null, ringOn = null;
    var calm = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    function pathOf(p) { try { return new URL(p || "/", location.href).pathname; } catch (e) { return p || ""; } }
    function visible(x) { var r = x.getBoundingClientRect(); return x.offsetParent !== null && r.width > 0 && r.height > 0; }
    function find(t) {
      var hit = null;
      try {
        if (t.selector) hit = Array.prototype.find.call(document.querySelectorAll(t.selector), visible) || null;
        if (!hit && t.anchor) hit = document.getElementById(t.anchor);
      } catch (e) { hit = null; }
      return hit;
    }
    function make() {
      if (el && el.isConnected) return el;
      el = document.createElement("div"); el.className = "aura-cursor"; el.setAttribute("aria-hidden", "true");
      el.innerHTML = '<svg class="ac-svg" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.2 1.6 13.6 7.3c.6.3.5 1.1-.1 1.3l-4.4 1.2-2 4.3c-.3.6-1.1.5-1.3-.1L2.2 1.6Z" fill="#1B3B75" stroke="#fff" stroke-width="1.3" stroke-linejoin="round"/></svg><span class="aura-cursor-tag"><span class="ac-text">AURA</span></span>';
      // 처음 자리 — 열린 도우미 창 왼쪽 아래(창이 닫혔으면 둥근 단추)
      var from = isOpen() ? panel.getBoundingClientRect() : fab.getBoundingClientRect();
      el.style.left = Math.round(from.left + (isOpen() ? 24 : from.width / 2)) + "px";
      el.style.top = Math.round(from.top + (isOpen() ? from.height - 90 : from.height / 2)) + "px";
      document.body.appendChild(el); void el.offsetWidth;
      return el;
    }
    // 대상으로 옮겨 가 누르는 모양을 보인 뒤 then(hit) — 대상이 없으면 바로 then(null)
    function point(t, label, then, hit) {
      hit = hit || find(t || {});
      if (!hit) { if (then) then(null); return; }
      if (!calm) hit.scrollIntoView({ behavior: "smooth", block: "nearest" }); else hit.scrollIntoView({ block: "nearest" });
      var c = make(); c.classList.remove("leaving");
      c.querySelector(".ac-text").textContent = label ? "AURA · " + label : "AURA";
      // 0.1초마다 대상 자리를 다시 재어 따라간다 — 부드러운 스크롤 중이거나 목록이 늦게 그려져 밀려도 대상 위에 있다
      var t0 = Date.now(), pressed = false;
      if (ringOn && ringOn !== hit) ringOn.classList.remove("aura-ring");
      ringOn = hit; clearTimeout(c._follow);
      (function track() {
        var r = hit.getBoundingClientRect(), age = Date.now() - t0;
        if (age > 600 && age < 2000 && (r.top < 0 || r.bottom > innerHeight)) { hit.scrollIntoView({ block: "center", behavior: "instant" }); r = hit.getBoundingClientRect(); }
        c.style.left = Math.round(r.left + Math.min(r.width / 2, 40)) + "px";
        c.style.top = Math.round(r.top + Math.min(r.height / 2, 18)) + "px";
        if (!pressed && age > (calm ? 0 : 900)) {
          pressed = true; hit.classList.add("aura-ring"); c.classList.add("press");
          setTimeout(function () { c.classList.remove("press"); if (then) then(hit); }, 380);
        }
        // 도우미 창이 대상을 덮으면 가리키는 동안만 창을 비켜 보이게(옅게·누름 통과)
        var pr = panel.getBoundingClientRect();
        var over = isOpen() && !(r.right < pr.left || r.left > pr.right || r.bottom < pr.top || r.top > pr.bottom);
        panel.classList.toggle("ag-peek", !!over && age < 3200);
        if (age < 3200) { c._follow = setTimeout(track, 100); return; }
        panel.classList.remove("ag-peek");
        hit.classList.remove("aura-ring"); c.classList.add("leaving");
      })();
    }
    function later(t, label, act) { store("session", KEY, JSON.stringify({ t: t, label: label || "", act: act || null, at: Date.now() })); }
    function resume() {
      var v = null;
      try { v = JSON.parse(store("session", KEY) || "null"); } catch (e) { v = null; }
      store("session", KEY, null);
      if (!v || !v.t || Date.now() - v.at > 20000 || pathOf(v.t.path) !== location.pathname) return false;
      setTimeout(function () { act(v.t, v.label, v.act); }, 450);
      return true;
    }
    // 그 화면에서 할 일 — 입력 칸이면 말을 넣어 찾고, 단추·링크면 누른다(act.now 일 때만). 아니면 가리키기만
    function nameOf(x) { return (x.getAttribute("aria-label") || x.textContent || x.placeholder || "").replace(/\s+/g, " ").trim().slice(0, 24); }
    function act(t, label, a) {
      a = a || {};
      var hit0 = find(t || {});
      if (hit0 && a.now && !a.text) label = nameOf(hit0) || label;     // 누를 단추 이름을 커서 꼬리표에
      point(t, label, function (hit) {
        if (!hit) { if (a.now) note("가리킬 자리를 이 화면에서 찾지 못했습니다."); return; }
        if (!a.now) return;
        if (hit.disabled || hit.getAttribute("aria-disabled") === "true") {
          make().querySelector(".ac-text").textContent = "AURA · 지금은 누를 수 없음";
          note("「" + nameOf(hit) + "」 단추가 지금 꺼져 있어 누르지 못했습니다" + (hit.title ? " — " + hit.title : "") + ".");
          return;
        }
        if (a.text && /^(INPUT|TEXTAREA)$/.test(hit.tagName)) { typeInto(hit, a.text); return; }
        // 보안: 서버가 고른 요소라도 화면에서 한 번 더 — 창 열기·링크·입력 칸·표시한 단추만 누른다(폼 제출 단추 등은 가리키기만)
        if (!hit.matches('[data-modal-open], a[href], input, [data-aura]') || hit.closest('form[method="post" i]')) { note("이 단추는 직접 눌러 주세요."); return; }
        hit.click();
      });
    }
    function typeInto(field, text) {
      field.focus(); field.value = "";
      var i = 0;
      (function next() {
        if (i < text.length) {
          field.value += text[i++]; field.dispatchEvent(new Event("input", { bubbles: true }));
          setTimeout(next, calm ? 0 : 55); return;
        }
        setTimeout(function () {
          var f = field.form;
          if (f) { if (f.requestSubmit) f.requestSubmit(); else f.submit(); }
        }, 450);
      })();
    }
    // 다른 화면 — 화면에 보이는 그 화면 메뉴(사이드바 등)를 커서로 누르고 옮겨 간다. 메뉴가 안 보이면 바로 옮겨 간다
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
    function go(p) {
      var t = p.target || {}, a = p.act || {};
      var label = p.label || "";
      if (pathOf(t.path || p.screen) === location.pathname) {
        if (t.selector || t.anchor) act(t, a.text ? "「" + a.text + "」 찾기" : label, a);
        return;
      }
      if (t.selector || t.anchor) later(t, a.text ? "「" + a.text + "」 찾기" : "", a);
      var link = navLink(p.screen);
      if (link) point({}, nameOf(link) || label, function () { location.href = p.screen; }, link);
      else location.href = p.screen;
    }
    return { point: point, later: later, resume: resume, go: go,
             here: function (t) { return !!t && pathOf(t.path) === location.pathname; } };
  })();
  function perform(p) { cursor.go(p); }
  // 커서가 하지 못한 일은 대화에 한 줄로 남긴다(화면에만 — 서버 기록은 바꾸지 않는다)
  function note(text) {
    if (!isOpen()) return;
    var el = bubble("assistant", text, "ag-err"); el.setAttribute("role", "status"); follow(true);
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
  var resumed = cursor.resume();
  // 지난번에 열어 둔 채 옮겨 왔으면 그대로 연다(좁은 폭은 화면을 가리므로 AURA 가 옮겨 온 때만)
  if (ui.open && (!narrow() || resumed)) open({ instant: true, noFocus: true });
  else render([]);
})();
