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
    r"학사|규정|장학|등록금|수강|성적|졸업|휴학|복학|증명서|채용|입학|예산|결산|편입|전과"
)
# 판단 기준이 될 수 없는 홍보·소개·시설성 페이지 (2026-09-02 품질 정리에서 확대)
DROP = re.compile(
    r"홍보대사|체험단|컬처데이|이벤트|SNS|유튜브|페이스북|인스타"
    r"|홍보영상|CF|신문광고|언론에서|교가|캐릭터|상징물|개교50주년|디자인메뉴얼"
    r"|총장|인사말|비전|창학|교육_목표|중장기발전|조직도|연혁|찾아오시는길|캠퍼스"
    r"|알림마당|공지사항|자주하는_질문|질문과_답변|건의사항|구내식당|입찰공고"
    r"|업체등록|이메일무단수집|학생회|동아리|활동_및_모집|복지_편의|자매결연"
    r"|해외연수|적립금|업무추진비|이사회|평의원회|자체평가|정보공개|등록금심의"
    r"|예_결산공고|주요성과|국제적_수준|든든한_장학제도|^영남이공대학교|^학과|^CI"
)
MIN_PAGE_CHARS = 800
FILE_EXTS = (".pdf", ".hwp", ".hwpx")


def _peek_text(f: Path) -> str:
    """파일 첫 부분 텍스트 미리보기 — 해시 파일명 대신 내용으로 선별·명명한다."""
    try:
        if f.suffix.lower() == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(f))
            return (reader.pages[0].extract_text() or "")[:600]
        if f.suffix.lower() == ".hwp":
            import subprocess
            import sys

            cli = Path(sys.executable).parent / "hwp5txt"
            out = subprocess.run([str(cli), str(f)], capture_output=True, text=True, timeout=60)
            return out.stdout[:600]
    except Exception:
        return ""
    return ""


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
    ap.add_argument("--pages-dir", default=str(PAGES), help="페이지 텍스트 디렉터리 (56 정제본 지정 가능)")
    ap.add_argument("--pages-only", action="store_true", help="파일(FILES) 등록은 건너뛴다")
    ap.add_argument("--min-chars", type=int, default=MIN_PAGE_CHARS)
    args = ap.parse_args()
    pages_dir = Path(args.pages_dir)

    pw = os.environ.get("ZZAIMY_PASSWORD", "")
    if not pw and not args.dry_run:
        raise SystemExit("ZZAIMY_PASSWORD 환경변수가 필요하다")

    existing = api(pw, [f"{BASE}/criteria"]) if not args.dry_run else ""

    candidates: list[tuple[Path, str, str]] = []  # (경로, 표시이름, 섹터)

    for f in sorted(pages_dir.glob("*.txt")):
        text = f.read_text(encoding="utf-8", errors="replace")
        title_line = text.splitlines()[0].lstrip("# ").strip() if text else ""
        title = title_line.split(">")[0].strip() or f.stem
        if len(text) < args.min_chars or not KEEP_PAGE.search(title + text[:2000]):
            continue
        name = re.sub(r"[^\w가-힣]+", "_", title)[:60] + ".txt"
        candidates.append((f, name, sector_of(title)))

    for f in [] if args.pages_only else sorted(FILES.iterdir()):
        if f.suffix.lower() not in FILE_EXTS or f.stat().st_size < 10_000:
            continue
        peek = _peek_text(f)
        if not peek or not KEEP_PAGE.search(peek):
            continue  # 내용을 못 읽거나 무관한 파일은 제외
        title = re.sub(r"\s+", " ", peek).strip()[:50]
        name = re.sub(r"[^\w가-힣]+", "_", title)[:60] + f.suffix.lower()
        candidates.append((f, name, sector_of(peek[:300])))

    # 이름 중복 제거(첫 항목 유지) + 노이즈 배제
    seen: set[str] = set()
    uniq = []
    for path, name, sector in candidates:
        if name in seen or DROP.search(name):
            continue
        seen.add(name)
        uniq.append((path, name, sector))
    candidates = uniq[: args.max_items]
    print(f"등록 후보 {len(candidates)}건 (중복·노이즈 제거 후)")

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
