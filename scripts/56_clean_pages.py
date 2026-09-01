"""긁어온 페이지 텍스트에서 사이트 공통 보일러플레이트를 걷어낸다.

수집 페이지(40_crawl_ync 산출물)는 본문 앞뒤로 사이트 전역 메뉴·꼬리말이
수백 줄씩 붙는다. 여러 페이지에 똑같이 반복되는 줄은 본문이 아니라는 점을
이용해, 줄 단위 문서빈도(df)가 높은 줄을 제거하고 본문만 남긴다.

실행(맥): python scripts/56_clean_pages.py
산출: data/scraped/pages_clean/*.txt (헤더 2줄 + 정제 본문)
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

SRC = Path("data/scraped/pages")
DST = Path("data/scraped/pages_clean")
DF_CUT = 8          # 이 수 이상의 페이지에 등장하는 줄은 공통 틀로 본다
MIN_KEEP_CHARS = 2  # 너무 짧은 줄(장식)은 df와 무관하게 버린다


def main() -> None:
    files = sorted(SRC.glob("*.txt"))
    df: Counter[str] = Counter()
    bodies: dict[Path, list[str]] = {}
    for f in files:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        header, body = lines[:2], lines[2:]
        bodies[f] = header + [""] + body
        df.update({ln.strip() for ln in body if ln.strip()})

    boiler = {ln for ln, n in df.items() if n >= DF_CUT}
    print(f"페이지 {len(files)}건, 공통 틀로 판정된 줄 {len(boiler)}종")

    DST.mkdir(parents=True, exist_ok=True)
    kept_stats = []
    for f, lines in bodies.items():
        out: list[str] = lines[:3]  # 제목·출처 헤더는 유지
        for ln in lines[3:]:
            s = ln.strip()
            if not s or len(s) < MIN_KEEP_CHARS or s in boiler:
                continue
            out.append(s)
        content_chars = sum(len(x) for x in out[3:])
        (DST / f.name).write_text("\n".join(out) + "\n", encoding="utf-8")
        kept_stats.append((content_chars, f.name))

    kept_stats.sort(reverse=True)
    n_thin = sum(1 for c, _ in kept_stats if c < 300)
    print(f"정제 후 본문 300자 미만(사실상 빈 페이지): {n_thin}건")
    for c, name in kept_stats[:5]:
        print(f"  많음 {c:>6}자  {name}")


if __name__ == "__main__":
    main()
