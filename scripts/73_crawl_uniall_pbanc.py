"""uniall.nrf.re.kr 사업공고(pbanc) 첨부 수집기 — 국고사업 공고 코퍼스.

eGovFrame 사이트라 목록이 AJAX(search.do)로 오고 상세(detail.do)에 첨부가
`/file/download.do?atchFileId=..&atchFileSeq=N`로 걸린다. 세션 쿠키 + Spring
CSRF 토큰으로 상세를 순회해 첨부를 받는다(브라우저 불필요).

브리프: 공개 공고, 파일럿 규모, data/ 아래(git 제외), 파인튜닝 아님(RAG/학습쌍
재료, ADR-0014). 개인정보는 인제스트 전 스크리닝.

사용:  python scripts/73_crawl_uniall_pbanc.py OUT_DIR [--max-id 30]
"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import ssl
import urllib.parse
import urllib.request
from datetime import date

BASE = "https://uniall.nrf.re.kr"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"


def _opener(insecure: bool):
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPSHandler(context=ctx))


def _fix_name(s: str) -> str:
    if not s:
        return s
    try:
        t = s.encode("latin1").decode("euc-kr")
        if re.search(r"[가-힣]", t):
            return t
    except Exception:
        pass
    return urllib.parse.unquote(s)


def _ext(data: bytes, hint: str) -> str:
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:2] == b"PK":
        return "hwpx" if hint.lower().endswith("hwpx") else "zip"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "hwp"
    return "bin"


def crawl(out_dir: str, max_id: int, insecure: bool) -> None:
    os.makedirs(out_dir, exist_ok=True)
    op = _opener(insecure)

    def req(url, data=None, ref=None):
        h = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}
        if ref:
            h["Referer"] = ref
        if data is not None:
            h["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            data = urllib.parse.urlencode(data, doseq=True).encode()
        return op.open(urllib.request.Request(url, data=data, headers=h), timeout=60)

    # 세션 + CSRF
    html = req(f"{BASE}/biz/pbanc/list.do").read().decode("utf-8", "replace")
    m = re.search(r'name="_csrf"\s+value="([^"]+)"', html)
    csrf = m.group(1) if m else None

    seen = set()
    for f in os.listdir(out_dir):
        p = os.path.join(out_dir, f)
        if os.path.isfile(p):
            seen.add(hashlib.sha256(open(p, "rb").read()).hexdigest())

    saved = 0
    for pid in range(1, max_id + 1):
        d = {"pbancId": str(pid)}
        if csrf:
            d["_csrf"] = csrf
        try:
            det = req(f"{BASE}/biz/pbanc/detail.do", data=d,
                      ref=f"{BASE}/biz/pbanc/list.do").read().decode("utf-8", "replace")
        except Exception as e:
            print(f"  pbancId {pid}: 상세 실패 {str(e)[:40]}")
            continue
        tm = re.search(r"<h[1-4][^>]*>(.*?)</h", det, re.S)
        title = re.sub(r"<[^>]+>|\s+", " ", tm.group(1)).strip()[:45] if tm else f"공고{pid}"
        links = re.findall(r"/file/download\.do\?atchFileId=([A-Za-z0-9_]+)&atchFileSeq=(\d+)", det)
        if not links:
            continue
        print(f"  pbancId {pid} [{title}] 첨부 {len(links)}")
        for fid, seq in links:
            url = f"{BASE}/file/download.do?atchFileId={fid}&atchFileSeq={seq}"
            try:
                r = req(url, ref=f"{BASE}/biz/pbanc/detail.do")
                cd = r.headers.get("Content-Disposition", "")
                data = r.read()
            except Exception as e:
                print(f"    seq{seq} 실패 {str(e)[:40]}")
                continue
            h = hashlib.sha256(data).hexdigest()
            if h in seen:
                continue
            seen.add(h)
            fm = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", cd)
            sname = _fix_name(fm.group(1)) if fm else ""
            ext = _ext(data, sname.lower())
            if ext == "bin":
                continue
            base = os.path.splitext(sname)[0] if sname else f"{fid}_{seq}"
            base = re.sub(r'[\\/:*?"<>|\r\n]+', "_", base).strip() or f"{fid}_{seq}"
            path = os.path.join(out_dir, f"{base}.{ext}")
            i = 1
            while os.path.exists(path):
                path = os.path.join(out_dir, f"{base}_{i}.{ext}")
                i += 1
            open(path, "wb").write(data)
            saved += 1
            print(f"    [OK] {os.path.basename(path)}  {len(data)//1024}KB")

    # 매니페스트
    rows = []
    for f in sorted(os.listdir(out_dir)):
        p = os.path.join(out_dir, f)
        if os.path.isfile(p) and f != "manifest.json":
            b = open(p, "rb").read()
            rows.append({"name": f, "ext": f.rsplit(".", 1)[-1].lower(),
                         "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()[:16]})
    json.dump({"collected_at": date.today().isoformat(), "source": "uniall.nrf.re.kr 사업공고",
               "count": len(rows), "files": rows},
              open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\n신규 {saved}건 저장 · 총 {len(rows)}건 (manifest 기록)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--max-id", type=int, default=30)
    ap.add_argument("--insecure", action="store_true")
    args = ap.parse_args()
    crawl(args.out_dir, args.max_id, args.insecure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
