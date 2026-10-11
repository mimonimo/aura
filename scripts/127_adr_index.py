#!/usr/bin/env python3
"""ADR 색인(docs/decisions/README.md 의 표)을 파일에서 만든다 — 손으로 적으면 줄이 빠지기 쉽다.

각 ADR 의 첫 제목 줄(# NNNN 제목)과 머리의 '상태'·'날짜' 줄을 읽는다. 표만 바꾸고 나머지 글은 둔다.
번호가 겹치는 ADR 도 찾는다 — 같은 번호의 다른 결정은 「ADR-00NN」 참조가 어느 쪽인지 모르게 만든다.
이미 여기저기서 그 번호로 불리는 옛 겹침은 고쳐 쓰지 않고(기록은 새 번호로만) KNOWN_DUPLICATES 에 둔다.
사용: python scripts/127_adr_index.py [--check]   # --check 는 바꿀 것이 있거나 새 번호 겹침이 있으면 1 로 끝난다(회귀 점검용)
      python scripts/127_adr_index.py --next    # 다음에 쓸 빈 번호
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "docs" / "decisions"
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# 이미 두 결정이 같은 번호로 불리고 있는 옛 겹침 — 다시 번호를 매기면 문서·커밋의 참조가 끊기므로 그대로 둔다
KNOWN_DUPLICATES = {"0037", "0052"}


def adr_files(d: Path = D) -> list[Path]:
    return sorted(p for p in d.glob("0*.md") if not p.name.startswith("0000"))


def duplicates(files: list[Path]) -> dict[str, list[str]]:
    """번호 → 그 번호를 쓰는 파일 이름들(둘 이상인 것만)."""
    seen: dict[str, list[str]] = {}
    for f in files:
        seen.setdefault(f.stem[:4], []).append(f.name)
    return {n: names for n, names in seen.items() if len(names) > 1}


def new_duplicates(files: list[Path]) -> dict[str, list[str]]:
    return {n: names for n, names in duplicates(files).items() if n not in KNOWN_DUPLICATES}


def next_number(files: list[Path]) -> str:
    nums = [int(f.stem[:4]) for f in files if f.stem[:4].isdigit()]
    return f"{(max(nums) + 1) if nums else 1:04d}"


def row(f: Path) -> str:
    lines = f.read_text(encoding="utf-8").splitlines()
    title = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), f.stem)
    title = re.sub(r"^(ADR-)?\d{4}\.?\s*[·—:-]?\s*", "", title)
    status = date = ""
    for ln in lines[:12]:
        t = ln.strip().lstrip("-> ").replace("**", "")
        if t.startswith("상태"):
            status = t.split(":", 1)[-1].strip()
            m = DATE.search(status)
            if not date and m:
                date = m.group(0)
            status = re.split(r"\s*[(（·]", status, maxsplit=1)[0].strip()
        elif t.startswith("날짜") and not date:
            m = DATE.search(t)
            date = m.group(0) if m else ""
        elif "날짜:" in t and not date:
            m = DATE.search(t.split("날짜:", 1)[1])
            date = m.group(0) if m else ""
    return f"| {f.stem[:4]} | {title} | {status or '—'} | {date or '—'} |"


def main() -> int:
    files = adr_files()
    if "--next" in sys.argv:
        print(next_number(files))
        return 0
    dup = new_duplicates(files)
    for n, names in dup.items():
        print(f"ADR 번호 겹침 {n}: {', '.join(names)} — 새로 쓴 쪽을 {next_number(files)} 로 바꾸세요")
    table = ["| # | 제목 | 상태 | 날짜 |", "|---|---|---|---|"] + [row(f) for f in files]
    readme = D / "README.md"
    s = readme.read_text(encoding="utf-8")
    head, _, rest = s.partition("## 색인")
    tail = rest.split("\n\n", 2)
    after = ""
    # 색인 표 뒤에 다른 절이 있으면 남긴다
    m = re.search(r"\n(## .+)$", rest, re.S)
    if m and not m.group(1).startswith("## 색인"):
        after = "\n" + m.group(1)
    new = head + "## 색인\n\n" + "\n".join(table) + "\n" + after
    if new == s:
        print("색인 최신 —", len(files), "건")
        return 1 if dup and "--check" in sys.argv else 0
    if "--check" in sys.argv:
        print("색인이 낡았습니다 — python scripts/127_adr_index.py 로 갱신")
        return 1
    readme.write_text(new, encoding="utf-8")
    print("색인 갱신 —", len(files), "건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
