/* 한글 편집기 큰 파일(엔진 wasm·수식 글꼴 묶음 .data)을 브라우저 저장소(Cache Storage)에 두고 꺼내 쓴다.
   운영 서버 인증서가 자체 서명이라 크롬이 이 사이트 응답을 HTTP 캐시에 아예 담지 않는다 — 문서를 열 때마다 24MB(압축본)를
   새로 받아 느린 연결에서는 편집기가 뜨지 않았다(11-10 논문 문서). Cache Storage 는 인증서와 상관없이 동작한다.
   판 번호(?13.82…)가 붙은 /webhwpctrl/wasm/ 아래 .wasm·.data GET 만 다루고, 나머지는 손대지 않는다.
   저장소가 없거나 실패하면 원래 받기로 그대로 넘긴다. editor-prefetch.js 가 남는 시간에 미리 채운다(auraEditorCache.warm). */
(function () {
  if (window.auraEditorCache) return;
  var RE = /^\/webhwpctrl\/wasm\/[\w.-]+\.(wasm|data)\?[0-9][0-9.]{0,31}$/;
  var NAME = 'aura-editor';
  var origFetch = window.fetch && window.fetch.bind(window);
  var ok = !!(window.caches && origFetch && window.isSecureContext !== false);
  function keyOf(u) {
    try { var a = new URL(String(u), location.href); } catch (e) { return null; }
    if (a.origin !== location.origin) return null;
    var k = a.pathname + a.search;
    return RE.test(k) ? k : null;
  }
  function store(c, key, r) {
    // 압축 해제된 본문만 담는다(머리는 형식만) — 꺼낼 때 다시 풀지 않게
    var type = r.headers.get('content-type') || (/\.wasm\?/.test(key) ? 'application/wasm' : 'application/octet-stream');
    return r.blob().then(function (b) {
      var clean = c.keys().then(function (ks) {      // 같은 파일의 옛 판은 지운다
        var path = key.split('?')[0];
        return Promise.all(ks.map(function (q) { var k = keyOf(q.url); return k && k !== key && k.split('?')[0] === path ? c.delete(q) : null; }));
      }).catch(function () {});
      var put = c.put(key, new Response(b, {headers: {'Content-Type': type, 'Content-Length': String(b.size)}})).catch(function () {});
      return Promise.all([clean, put]).then(function () { return new Response(b, {status: 200, headers: {'Content-Type': type}}); });
    });
  }
  function get(key) {
    return caches.open(NAME).then(function (c) {
      return c.match(key).then(function (hit) {
        if (hit) return hit;
        return origFetch(key, {credentials: 'same-origin'}).then(function (r) { return r.ok ? store(c, key, r) : r; });
      });
    });
  }
  function warm(key) {
    return caches.open(NAME).then(function (c) {
      return c.match(key).then(function (hit) { return hit ? null : get(key); });
    });
  }
  window.auraEditorCache = {ok: ok, keyOf: keyOf, warm: ok ? warm : function () { return Promise.resolve(null); }};
  if (!ok) return;

  window.fetch = function (input, init) {
    var url = typeof input === 'string' ? input : (input && input.url);
    var method = (init && init.method) || (input && typeof input === 'object' && input.method) || 'GET';
    var key = String(method).toUpperCase() === 'GET' ? keyOf(url) : null;
    if (!key) return origFetch(input, init);
    return get(key).catch(function () { return origFetch(input, init); });
  };

  // 수식 글꼴 묶음은 XMLHttpRequest(arraybuffer)로 받는다 — 저장소에 있으면 응답을 채워 load 를 알린다
  var X = window.XMLHttpRequest && window.XMLHttpRequest.prototype;
  if (!X) return;
  var oOpen = X.open, oSend = X.send;
  X.open = function (method, url, async) {
    this.__auraKey = (String(method).toUpperCase() === 'GET' && async !== false) ? keyOf(url) : null;
    return oOpen.apply(this, arguments);
  };
  X.send = function () {
    var x = this, key = x.__auraKey, args = arguments;
    if (!key || (x.responseType && x.responseType !== 'arraybuffer')) return oSend.apply(x, args);
    get(key).then(function (r) {
      if (!r.ok) throw new Error('status ' + r.status);
      return r.arrayBuffer();
    }).then(function (buf) {
      var n = buf.byteLength;
      function def(k, v) { Object.defineProperty(x, k, {value: v, configurable: true}); }
      def('readyState', 4); def('status', 200); def('statusText', 'OK'); def('response', buf);
      def('responseURL', location.origin + key);
      x.getResponseHeader = function (h) { return /content-length/i.test(h) ? String(n) : null; };
      x.getAllResponseHeaders = function () { return 'content-length: ' + n + '\r\n'; };
      var pe = function (t) { return new ProgressEvent(t, {lengthComputable: true, loaded: n, total: n}); };
      x.dispatchEvent(pe('progress'));
      x.dispatchEvent(new Event('readystatechange'));
      x.dispatchEvent(pe('load'));
      x.dispatchEvent(pe('loadend'));
    }).catch(function () { oSend.apply(x, args); });
  };
})();
