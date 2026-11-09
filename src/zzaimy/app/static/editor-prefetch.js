/* 한글 편집기 파일 미리 받기 — 교직원 화면이 쉴 때 편집기 wasm·스크립트를 받아 브라우저 캐시에 둔다.
   처음 한글 문서를 열 때 수십 MB 를 기다리지 않게(11-10 실측: 차가운 캐시로 처음 열기 12~90초, 따뜻하면 3초).
   판(version)마다 한 번만, 하나씩 차례로(다른 일을 막지 않게), 편집기 화면 안에서는 하지 않는다. */
(function () {
  if (window.top !== window.self) return;
  if (/^\/webhwpctrl\//.test(location.pathname)) return;
  var KEY = 'aura-editor-prefetch';
  function idle(fn) { (window.requestIdleCallback || function (f) { return setTimeout(f, 1500); })(fn, {timeout: 8000}); }
  function done(v) { try { localStorage.setItem(KEY, v); } catch (e) {} }
  function seen() { try { return localStorage.getItem(KEY) || ''; } catch (e) { return ''; } }
  function run() {
    fetch('/webhwp-prefetch', {credentials: 'same-origin'}).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
      if (!j || !j.version || !j.urls || !j.urls.length || seen() === j.version) return;
      var i = 0;
      (function next() {
        if (i >= j.urls.length) { done(j.version); return; }
        var u = j.urls[i++];
        fetch(u, {credentials: 'same-origin', priority: 'low'}).then(function (r) { return r.arrayBuffer(); })
          .catch(function () {}).then(function () { idle(next); });
      })();
    }).catch(function () {});
  }
  if (document.readyState === 'complete') idle(run); else window.addEventListener('load', function () { idle(run); });
})();
