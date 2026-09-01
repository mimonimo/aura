"""교내 공개 웹 자산 수집기 (YNC LLM 지식 자산 확보).

학교 공식 사이트의 공개 게시판·콘텐츠 페이지를 돌며 본문 텍스트와
첨부파일(PDF/HWP 등)을 모은다. 내부망 인증이 필요한 영역은 건드리지 않는다.

예의 규칙: 요청 간격 유지, 페이지·파일 수 상한, 같은 도메인만.
산출물은 data/scraped/ (gitignore) 아래에 쌓인다.

사용: python scripts/40_crawl_ync.py --max-pages 300 --max-files 120
"""

from __future__ import annotations

import argparse
import html as html_mod
import re
import time
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

ALLOWED_HOSTS = {"www.ync.ac.kr", "onestop.ync.ac.kr", "iacf.ync.ac.kr"}
SEEDS = [
    "https://www.ync.ac.kr/kor/Main.do",
    "https://www.ync.ac.kr/kor/CMS/Contents/Contents.do?mCode=MN281",  # 학칙·학사 규정
    "https://www.ync.ac.kr/kor/CMS/Board/Board.do?mCode=MN144",  # 예·결산 공고
    "https://www.ync.ac.kr/kor/CMS/Board/Board.do?mCode=MN213",  # 채용 공고
    "https://www.ync.ac.kr/kor/CMS/Board/Board.do?mCode=MN213&page=2",
    "https://www.ync.ac.kr/kor/CMS/Board/Board.do?mCode=MN213&page=3",
    "https://iacf.ync.ac.kr/document/law",  # 산단 규정
    "https://onestop.ync.ac.kr/oneStop/index.jsp",
]
FOLLOW_PATTERNS = (
    "/kor/CMS/Board/Board.do",
    "/kor/CMS/Contents/Contents.do",
    "/oneStop/",
    "/download/",
    "/document/",
)
FILE_EXTS = (".pdf", ".hwp", ".hwpx", ".xlsx", ".xls", ".docx")
UA = {"User-Agent": "Mozilla/5.0 (YNC-capstone internal crawler; contact: admin)"}
MAX_FILE_MB = 20


def fetch(url: str, timeout: int = 15) -> bytes | None:
    """curl 경유 수집 — 학교 서버가 중간 인증서를 안 내려줘 파이썬 기본 검증이
    실패한다. curl은 OS 신뢰 저장소로 정상 검증하므로 검증을 끄지 않고 해결."""
    import subprocess

    try:
        proc = subprocess.run(
            ["curl", "-sL", "--max-time", str(timeout), "-A", UA["User-Agent"], url],
            capture_output=True,
            timeout=timeout + 5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    return proc.stdout


def page_text(raw: str) -> tuple[str, str]:
    title = ""
    m = re.search(r"<title>([^<]+)", raw)
    if m:
        title = html_mod.unescape(m.group(1)).strip()
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S)
    body = re.sub(r"<[^>]+>", "\n", body)
    body = html_mod.unescape(body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n", body).strip()
    return title, body


def safe_name(url: str) -> str:
    return re.sub(r"[^A-Za-z0-9가-힣._-]+", "_", url.split("//", 1)[-1])[:150]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("data/scraped"))
    ap.add_argument("--max-pages", type=int, default=300)
    ap.add_argument("--max-files", type=int, default=120)
    ap.add_argument("--delay", type=float, default=0.4)
    args = ap.parse_args()

    pages_dir = args.out / "pages"
    files_dir = args.out / "files"
    pages_dir.mkdir(parents=True, exist_ok=True)
    files_dir.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    queue = deque(SEEDS)
    n_pages = n_files = 0

    while queue and n_pages < args.max_pages:
        url = queue.popleft()
        norm = url.split("#", 1)[0]
        if norm in seen:
            continue
        seen.add(norm)
        host = urllib.parse.urlparse(norm).netloc
        if host not in ALLOWED_HOSTS:
            continue

        time.sleep(args.delay)
        data = fetch(norm)
        if data is None:
            continue

        path = urllib.parse.urlparse(norm).path.lower()
        if path.endswith(FILE_EXTS):
            if n_files < args.max_files and len(data) <= MAX_FILE_MB * 1024 * 1024:
                (files_dir / safe_name(norm)).write_bytes(data)
                n_files += 1
                print(f"[파일 {n_files}] {norm}")
            continue

        raw = data.decode("utf-8", errors="replace")
        title, text = page_text(raw)
        if len(text) > 200:  # 빈 껍데기 페이지 제외
            out = pages_dir / (safe_name(norm) + ".txt")
            out.write_text(f"# {title}\n# 출처: {norm}\n\n{text}", encoding="utf-8")
            n_pages += 1
            print(f"[페이지 {n_pages}] {title[:50]} — {norm[:90]}")

        # 링크 수집
        for href in re.findall(r'href="([^"]+)"', raw):
            href = html_mod.unescape(href)
            absu = urllib.parse.urljoin(norm, href)
            p = urllib.parse.urlparse(absu)
            if p.netloc not in ALLOWED_HOSTS:
                continue
            if p.path.lower().endswith(FILE_EXTS) or any(
                pat in p.path for pat in FOLLOW_PATTERNS
            ):
                if absu.split("#", 1)[0] not in seen:
                    queue.append(absu)
        # 첨부 다운로드 링크 (게시판)
        for q in re.findall(r'href="(/kor/ajx_json/UploadMgr/downloadRun\.do[^"]+)"', raw):
            absu = urllib.parse.urljoin(norm, html_mod.unescape(q))
            if absu not in seen and n_files < args.max_files:
                seen.add(absu)
                time.sleep(args.delay)
                blob = fetch(absu)
                if blob and len(blob) <= MAX_FILE_MB * 1024 * 1024:
                    (files_dir / safe_name(absu)).write_bytes(blob)
                    n_files += 1
                    print(f"[첨부 {n_files}] {absu[:90]}")

    print(f"완료: 페이지 {n_pages}건, 파일 {n_files}건 → {args.out}")


if __name__ == "__main__":
    main()
