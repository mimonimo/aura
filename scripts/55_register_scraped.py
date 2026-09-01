"""수집한 교내 문서를 선별해 기준 저장소에 일괄 등록한다.

data/scraped/(40_crawl_ync 산출물)에서 쓸만한 페이지·파일만 골라
플랫폼 API로 등록한다. 한 건씩 처리 완료를 기다렸다가 다음으로 넘어간다
(서버 파싱 부하 제어). 이미 같은 이름이 등록돼 있으면 건너뛴다.

실행(맥): ZZAIMY_PASSWORD=... python scripts/55_register_scraped.py [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
from pathlib import Path

BASE = "https://211.170.162.109:8800"
PAGES = Path("data/scraped/pages")
FILES = Path("data/scraped/files")

# 제목·파일명 키워드 → 섹터
SECTOR_RULES = [
    (re.compile(r"채용|임용|응시|모집공고"), "recruit"),
    (re.compile(r"입학|모집요강|전형"), "admission"),
    (re.compile(r"예산|결산|재정"), "grant"),
]
KEEP_PAGE = re.compile(
    r"학사|규정|안내|장학|등록금|수강|성적|졸업|휴학|복학|증명|채용|입학|예산|결산"
)
MIN_PAGE_CHARS = 800
FILE_EXTS = (".pdf", ".hwp", ".hwpx")


def sector_of(name: str) -> str:
    for pat, sec in SECTOR_RULES:
        if pat.search(name):
            return sec
    return "common"


def api(pw: str, args: list[str]) -> str:
    out = subprocess.run(
        ["curl", "-sk", "-u", f"zzaimy:{pw}", "--max-time", "60", *args],
        capture_output=True, text=True, timeout=90,
    )
    return out.stdout


def wait_done(pw: str, prev_processing: int = 0) -> None:
    """처리 중 문서가 없어질 때까지 대기 (최대 5분)."""
    for _ in range(30):
        page = api(pw, [f"{BASE}/criteria"])
        if page.count("처리 중") <= prev_processing:
            return
        time.sleep(10)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-items", type=int, default=60)
    args = ap.parse_args()

    pw = os.environ.get("ZZAIMY_PASSWORD", "")
    if not pw and not args.dry_run:
        raise SystemExit("ZZAIMY_PASSWORD 환경변수가 필요하다")

    existing = api(pw, [f"{BASE}/criteria"]) if not args.dry_run else ""

    candidates: list[tuple[Path, str, str]] = []  # (경로, 표시이름, 섹터)

    for f in sorted(PAGES.glob("*.txt")):
        text = f.read_text(encoding="utf-8", errors="replace")
        title_line = text.splitlines()[0].lstrip("# ").strip() if text else ""
        title = title_line.split(">")[0].strip() or f.stem
        if len(text) < MIN_PAGE_CHARS or not KEEP_PAGE.search(title + text[:2000]):
            continue
        name = re.sub(r"[^\w가-힣]+", "_", title)[:60] + ".txt"
        candidates.append((f, name, sector_of(title)))

    for f in sorted(FILES.iterdir()):
        if f.suffix.lower() not in FILE_EXTS or f.stat().st_size < 10_000:
            continue
        name = f.name if len(f.name) < 80 else f.name[-80:]
        candidates.append((f, name, sector_of(f.name)))

    candidates = candidates[: args.max_items]
    print(f"등록 후보 {len(candidates)}건")

    done = skip = fail = 0
    for path, name, sector in candidates:
        if name in existing:
            skip += 1
            continue
        print(f"[{sector}] {name}", flush=True)
        if args.dry_run:
            continue
        out = subprocess.run(
            ["curl", "-sk", "-u", f"zzaimy:{pw}", "-o", "/dev/null", "-w", "%{http_code}",
             "-F", f"sector={sector}", "-F", f"file=@{path};filename={name}",
             f"{BASE}/criteria/upload"],
            capture_output=True, text=True, timeout=120,
        )
        if out.stdout.strip() == "303":
            done += 1
            wait_done(pw)
        else:
            fail += 1
            print(f"  실패: HTTP {out.stdout.strip()}", flush=True)
    print(f"완료 {done} / 중복 건너뜀 {skip} / 실패 {fail}")


if __name__ == "__main__":
    main()
