/* 큰 자료 나눠 올리기 — 웹 서버(nginx)는 한 요청 본문을 한도(client_max_body_size)까지만 받는다. 스캔 PDF 여러 건(합계 550MB)을
   한 번에 보내면 한도에 걸려 요청이 통째로 막히고 화면에는 아무 안내도 없었다(11-09 TF 시험). 파일을 한도 안 묶음으로 나눠
   차례로 보낸다. 한 파일이 한도를 넘으면 미리 알린다. 한도는 서버가 base.html 에 window.AURA_UPLOAD_MAX_MB 로 알려 준다. */
(function () {
  function maxBytes() {
    var mb = Number(window.AURA_UPLOAD_MAX_MB) || 480;
    return mb * 1048576;
  }
  function mb(n) { return (n / 1048576).toFixed(n >= 104857600 ? 0 : 1) + "MB"; }
  // 파일 목록 → 묶음 목록(한 묶음 합계가 한도 안). 한 파일이 한도를 넘으면 too 에 담는다
  function batches(files) {
    var cap = maxBytes(), out = [], cur = [], sum = 0, too = [];
    Array.from(files || []).forEach(function (f) {
      var n = f.size || 0;
      if (n > cap) { too.push(f); return; }
      if (cur.length && sum + n > cap) { out.push(cur); cur = []; sum = 0; }
      cur.push(f); sum += n;
    });
    if (cur.length) out.push(cur);
    return {batches: out, tooBig: too, cap: cap};
  }
  function total(files) { return Array.from(files || []).reduce(function (a, f) { return a + (f.size || 0); }, 0); }
  // 묶음 하나를 /project/{id}/bundle 로 — onProgress(보낸 바이트) 를 부르고 JSON(criteria·intake·skipped)을 돌려준다
  function sendBatch(url, files, onProgress) {
    return new Promise(function (resolve, reject) {
      var fd = new FormData();
      files.forEach(function (f) { fd.append("file", f); });
      var xhr = new XMLHttpRequest();
      xhr.open("POST", url);
      xhr.setRequestHeader("Accept", "application/json");
      xhr.upload.addEventListener("progress", function (e) { if (e.lengthComputable && onProgress) onProgress(e.loaded, e.total); });
      xhr.addEventListener("load", function () {
        var d = null; try { d = JSON.parse(xhr.responseText); } catch (_) {}
        if (xhr.status === 200 && d) return resolve(d);
        if (xhr.status === 413) return reject(new Error("한 번에 올릴 수 있는 크기(" + mb(maxBytes()) + ")를 넘었습니다. 파일을 나눠 올려 주세요"));
        reject(new Error((d && d.detail) || ("서버가 받지 못했습니다(" + xhr.status + ")")));
      });
      xhr.addEventListener("error", function () { reject(new Error("연결이 끊겨 올리지 못했습니다")); });
      xhr.addEventListener("abort", function () { reject(new Error("올리기를 멈췄습니다")); });
      xhr.send(fd);
      if (onProgress) onProgress.xhr = xhr;
    });
  }
  window.AuraUpload = {batches: batches, total: total, sendBatch: sendBatch, maxBytes: maxBytes, mb: mb};
})();
