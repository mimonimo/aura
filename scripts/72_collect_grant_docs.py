"""공개 국고사업 문서(계획서·서식·공고) 수집기 — 파일럿 RAG 코퍼스용.

공개된 .pdf/.hwp/.hwpx/.zip을 받아 중복(sha256) 제거, 파일명 복구(EUC-KR),
매직바이트로 형식 판정, manifest.json 갱신까지 한다. "신규 문서 들어오면
RAG 보강"(사용자 요구)의 무인 수집 단계에 재사용한다.

주의(브리프): 파일럿 우선(20~30건 규모). 수집물은 data/ 아래(git 제외)에만
둔다. 개인정보는 인제스트 이전에 스크리닝한다(절대규칙 3). 파인튜닝은 아니고
RAG 코퍼스·학습쌍 재료 준비다(절대규칙 2·8, ADR-0014).

사용:
  python scripts/72_collect_grant_docs.py OUT_DIR URL [URL...]
  python scripts/72_collect_grant_docs.py OUT_DIR @urls.txt
  # 텍스트 추출 readiness까지: --extract (pdf=pypdf, hwp/hwpx=ingest.hwp_text)

JS/차단 포털(uniall.nrf 등)은 이 도구로 못 긁는다 — 브라우저 자동화나 상세
URL 직접 제공이 필요하다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import date

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126"


def _ctx(insecure: bool = False) -> ssl.SSLContext:
    """기본은 TLS 검증 ON. 인증서 체인이 불완전한 일부 기관 사이트에 한해
    --insecure로 명시적으로 검증을 끈다(MITM 위험 — 신뢰하는 사이트에만)."""
    c = ssl.create_default_context()
    if insecure:
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
    return c


def _magic_ext(data: bytes, hint: str = "") -> str:
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:2] == b"PK":
        return "hwpx" if hint.lower().endswith("hwpx") else "zip"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "hwp"
    return "bin"


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


def _download(url: str, ctx: ssl.SSLContext) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=90, context=ctx) as r:
        cd = r.headers.get("Content-Disposition", "")
    with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": _UA}),
            timeout=90, context=ctx) as r:
        data = r.read()
    m = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", cd)
    return data, (_fix_name(m.group(1)) if m else "")


def collect(out_dir: str, urls: list[str], insecure: bool = False) -> list[dict]:
    os.makedirs(out_dir, exist_ok=True)
    ctx = _ctx(insecure)
    seen = set()
    for f in os.listdir(out_dir):
        p = os.path.join(out_dir, f)
        if os.path.isfile(p):
            seen.add(hashlib.sha256(open(p, "rb").read()).hexdigest())
    saved: list[dict] = []
    for line in urls:
        url, _, name = line.partition("::")
        try:
            data, sname = _download(url, ctx)
        except Exception as e:
            print(f"  [실패] {url[:60]} :: {str(e)[:50]}")
            continue
        h = hashlib.sha256(data).hexdigest()
        if h in seen:
            print(f"  [중복] {name or sname or url[:40]}")
            continue
        seen.add(h)
        ext = _magic_ext(data, sname)
        if ext == "bin":
            print(f"  [형식불명 스킵] {url[:60]}")
            continue
        base = name or (os.path.splitext(sname)[0] if sname else h[:12])
        base = re.sub(r'[\\/:*?"<>|\r\n]+', "_", base).strip() or h[:12]
        path = os.path.join(out_dir, f"{base}.{ext}")
        i = 1
        while os.path.exists(path):
            path = os.path.join(out_dir, f"{base}_{i}.{ext}")
            i += 1
        open(path, "wb").write(data)
        print(f"  [OK] {os.path.basename(path)}  {len(data) // 1024}KB")
        saved.append({"name": os.path.basename(path), "ext": ext,
                      "bytes": len(data), "sha256": h[:16], "url": url})
    return saved


def write_manifest(out_dir: str) -> None:
    rows = []
    for f in sorted(os.listdir(out_dir)):
        p = os.path.join(out_dir, f)
        if not os.path.isfile(p) or f == "manifest.json":
            continue
        b = open(p, "rb").read()
        rows.append({"name": f, "ext": f.rsplit(".", 1)[-1].lower(),
                     "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()[:16]})
    json.dump({"collected_at": date.today().isoformat(), "count": len(rows),
               "files": rows},
              open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"manifest.json: {len(rows)}건")


def main() -> int:
    ap = argparse.ArgumentParser(description="공개 국고사업 문서 수집기")
    ap.add_argument("out_dir")
    ap.add_argument("urls", nargs="+", help="URL 또는 @urls.txt (한 줄당 URL[::이름])")
    ap.add_argument("--extract", action="store_true",
                    help="수집 후 텍스트 추출 readiness 출력")
    ap.add_argument("--insecure", action="store_true",
                    help="TLS 검증 끔 — 인증서 체인 불완전한 신뢰 기관 사이트에만")
    args = ap.parse_args()

    urls: list[str] = []
    for a in args.urls:
        if a.startswith("@"):
            urls += [ln.strip() for ln in open(a[1:]) if ln.strip()]
        else:
            urls.append(a)
    collect(args.out_dir, urls, insecure=args.insecure)
    write_manifest(args.out_dir)

    if args.extract:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
        from zzaimy.ingest.hwp_text import extract_text  # noqa
        try:
            from pypdf import PdfReader
        except ImportError:
            PdfReader = None
        for f in sorted(os.listdir(args.out_dir)):
            p = os.path.join(args.out_dir, f)
            ext = f.rsplit(".", 1)[-1].lower()
            if not os.path.isfile(p) or ext not in ("pdf", "hwp", "hwpx"):
                continue
            try:
                if ext == "pdf" and PdfReader:
                    t = "\n".join((pg.extract_text() or "")
                                  for pg in PdfReader(p).pages)
                elif ext in ("hwp", "hwpx"):
                    t = extract_text(p)
                else:
                    t = ""
            except Exception as e:
                print(f"  [추출ERR] {f[:40]} :: {str(e)[:40]}")
                continue
            n = len((t or "").strip())
            print(f"  {ext:4} {n:>7}자  {'OK' if n >= 200 else 'OCR?/빈양식'}  {f[:48]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
