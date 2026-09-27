"""작업본의 절마다 완성본(합본·지난 계획서)의 같은 절을 찾아 27B 에게 줄 맥락을 고른다 — 절 번호·제목 정렬.

사용자 지시 2026-09-27: 합본은 양식과 같은 목차(1.1, 1.2, 2.1 …)를 가진 완성본(정답지)이다. 스크립트로 베끼는 것이 아니라
27B 가 맥락을 읽고 쓰게 한다 — 여기서는 그 맥락(같은 절의 글·표)을 고르는 일만 한다. 낱말 겹침으로 조각 여섯 개를 고르던
방식은 절의 내용을 통째로 놓쳤다.

흐름: 작업본 절 구조(gdocs.outline) + 완성본 조각(doc_chunks, 쪽·순서) → 줄 단위 흐름(items) → 절 제목 맞추기(anchor 두 단계)
→ 절마다 글(정리)·표(행렬) 넣기. 특정 문서에 맞춘 규칙은 없다: 쪽 머리말·꼬리말은 여러 쪽에 반복되는 짧은 줄로, 제목은 번호와
글자 유사도로 찾는다.
"""

from __future__ import annotations

import difflib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field

# 한글 기호 글꼴의 사설 영역·특수 글리프 → 보통 글자
_GLYPHS = {"": "·", "ž": "·", "･": "·", "‧": "·", "à": "→"}
_BULLET_RE = re.compile(r"^(q|§|Ø|ü|v|w|n|l|u|Ÿ|Ü|¡|Ø)\s+")          # Wingdings 글머리표가 글자로 남은 것
_BULLET_MAP = {"q": "○", "§": "-", "Ø": "-", "ü": "-", "v": "▪", "w": "▪", "n": "▪", "l": "·", "u": "◆", "Ÿ": "·", "Ü": "-", "¡": "·"}
_NUM_RE = re.compile(r"^\s*((?:\d+\.)+\d*)\s*")                    # 1. / 1.1. / 2.1.1.
_SUBNUM_RE = re.compile(r"^\s*(\d+\)|\(\d+\)|[가-하]\.|[가-하]\)|\d+\.)\s*")
_PAGE_NO_RE = re.compile(r"^\s*(-\s*)?\d{1,3}(\s*-)?\s*$")
_BOLD_RE = re.compile(r"\*\*(.*?)\*\*")
_KEEP_RE = re.compile(r"[^0-9a-z가-힣]")

RUNNING_MIN_PAGES = 8        # 이만큼의 서로 다른 쪽에 나오는 짧은 줄은 쪽 머리말·꼬리말·구호다
RUNNING_MAX_LEN = 40
MATCH_RATIO = 0.8


def norm(s: str) -> str:
    s = (s or "").lower()
    for k, v in _GLYPHS.items():
        s = s.replace(k, v)
    return _KEEP_RE.sub("", s)


def split_number(heading: str) -> tuple[str, str]:
    """'2.1. 사업 추진목표' → ('2.1', '사업 추진목표'). 번호가 없으면 ('', 제목)."""
    m = _NUM_RE.match(heading or "")
    if m:
        return m.group(1).rstrip("."), (heading[m.end():] or "").strip()
    m = _SUBNUM_RE.match(heading or "")
    if m:
        return m.group(1).rstrip(".)").lstrip("("), (heading[m.end():] or "").strip()
    return "", (heading or "").strip()


def title_like(a: str, b: str) -> bool:
    """제목 글자가 충분히 닮았는가(체제/체계 같은 한 글자 차이·기호 차이는 같은 제목)."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if na == nb or na.startswith(nb) or nb.startswith(na):
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= MATCH_RATIO


@dataclass
class Item:
    kind: str            # text | table
    text: str            # text 줄 하나, table 이면 JSON 원문
    page: int
    pos: int = 0


@dataclass
class Span:
    section: dict
    start: int = -1          # items 인덱스(제목 줄 다음)
    end: int = -1
    anchor: bool = False
    pages: list[int] = field(default_factory=list)


def stream(chunks: list[dict]) -> list[Item]:
    """조각(쪽·순서) → 줄 흐름. 표 조각은 한 항목."""
    ordered = sorted(chunks, key=lambda c: (int(c.get("page_no") or 0), int(c.get("seq") or c.get("id") or 0)))
    items: list[Item] = []
    for ch in ordered:
        page = int(ch.get("page_no") or 0)
        content = ch.get("content") or ""
        if (ch.get("kind") or "") == "table":
            items.append(Item("table", content, page))
            continue
        for ln in content.replace("\r", "\n").split("\n"):
            if ln.strip():
                items.append(Item("text", ln.rstrip(), page))
    for i, it in enumerate(items):
        it.pos = i
    return items


def running_lines(items: list[Item]) -> set[str]:
    """여러 쪽에 되풀이되는 짧은 줄(쪽 머리말·꼬리말·구호·직인 표시) — 내용이 아니다."""
    pages: dict[str, set[int]] = defaultdict(set)
    for it in items:
        if it.kind != "text":
            continue
        k = norm(it.text)
        if k and len(it.text.strip()) <= RUNNING_MAX_LEN:
            pages[k].add(it.page)
    return {k for k, ps in pages.items() if len(ps) >= RUNNING_MIN_PAGES}


def clean_line(line: str) -> str:
    s = line.strip()
    for k, v in _GLYPHS.items():
        s = s.replace(k, v)
    s = _BOLD_RE.sub(r"\1", s)
    m = _BULLET_RE.match(s)
    if m:
        s = _BULLET_MAP.get(m.group(1), "-") + " " + s[m.end():]
    return s.strip()


def table_rows(raw: str) -> list[list[str]]:
    """표 조각(JSON: n_rows·n_cols·cells[r, c, rowspan, colspan, is_header, text]) → 행렬. 병합 셀은 첫 자리에만 글."""
    try:
        data = json.loads(raw)
    except Exception:
        rows = [[c.strip() for c in ln.split(" | ")] for ln in raw.replace("\r", "\n").split("\n") if ln.strip()]
        return [r for r in rows if any(r)]
    n_rows, n_cols = int(data.get("n_rows") or 0), int(data.get("n_cols") or 0)
    if not n_rows or not n_cols:
        return []
    grid = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    for cell in data.get("cells", []):
        try:
            r, c, text = int(cell[0]), int(cell[1]), str(cell[5] if len(cell) > 5 else cell[-1])
        except Exception:
            continue
        if 0 <= r < n_rows and 0 <= c < n_cols:
            grid[r][c] = clean_line(text.replace("\n", " "))
    return [row for row in grid if any(row)]


def _heading_at(item: Item, heading: str) -> bool:
    if item.kind != "text":
        return False
    num, title = split_number(heading)
    inum, ititle = split_number(item.text)
    if num and inum:
        if inum != num:
            return False
        return title_like(ititle, title) or not ititle
    if num and not inum:
        # 완성본 쪽 줄에 번호가 없으면(소제목 '강점(S)') 제목 글자만 견준다
        return title_like(ititle or item.text, title) and abs(len(norm(item.text)) - len(norm(title))) <= 6
    # 번호 없는 제목(【요약서】·'강점(S)')은 줄 전체로 견준다
    return title_like(item.text, heading) and abs(len(norm(item.text)) - len(norm(heading))) <= 6


def align(sections: list[dict], items: list[Item]) -> list[Span]:
    """작업본 절 → 완성본 줄 범위. 1단계: 번호가 두 마디 이상인 제목(1.1, 2.1.1)을 앞에서부터 차례로 고정점으로.
    2단계: 나머지 제목은 바로 앞 고정점의 범위 안에서 찾는다(못 찾으면 그 절은 비워 두고 부모 절에 내용이 남는다)."""
    spans = [Span(section=s) for s in sections if s.get("index", 0) > 0 and (s.get("heading") or "").strip()]
    anchors = [sp for sp in spans if "." in split_number(sp.section["heading"])[0]]
    pos = 0
    for sp in anchors:
        for j in range(pos, len(items)):
            if _heading_at(items[j], sp.section["heading"]):
                sp.start, sp.anchor = j + 1, True
                pos = j + 1
                break
    matched = [sp for sp in anchors if sp.start >= 0]
    for k, sp in enumerate(matched):
        sp.end = matched[k + 1].start - 1 if k + 1 < len(matched) else len(items)
    # 2단계 — 고정점 사이의 소제목
    for i, sp in enumerate(spans):
        if sp.anchor or sp.start >= 0:
            continue
        parent = next((a for a in reversed(spans[:i]) if a.anchor and a.start >= 0), None)
        if parent is None:
            continue
        lo, hi = parent.start, parent.end
        # 같은 부모 아래 앞서 맞춘 소제목이 있으면 그 뒤부터
        prev = [x for x in spans[:i] if x.start >= 0 and not x.anchor and parent.start <= x.start <= parent.end]
        if prev:
            lo = max(lo, prev[-1].start)
        for j in range(lo, min(hi, len(items))):
            if _heading_at(items[j], sp.section["heading"]):
                sp.start = j + 1
                break
    # 소제목의 끝은 같은 부모 안의 다음 맞춘 제목 앞
    for i, sp in enumerate(spans):
        if sp.start < 0 or sp.anchor:
            continue
        nxt = next((x.start - 1 for x in spans[i + 1:] if x.start >= 0), len(items))
        sp.end = min(nxt, next((a.end for a in reversed(spans[:i]) if a.anchor and a.start >= 0), len(items)))
    # 부모 절의 범위에서 소제목이 가져간 부분을 뺀다(부모에는 소제목 앞까지만)
    for i, sp in enumerate(spans):
        if not sp.anchor or sp.start < 0:
            continue
        kids = [x for x in spans[i + 1:] if x.start >= 0 and not x.anchor and sp.start <= x.start <= sp.end]
        if kids:
            sp.end = min(sp.end, kids[0].start - 1)
    for sp in spans:
        if sp.start >= 0:
            sp.pages = sorted({items[j].page for j in range(sp.start, max(sp.start, sp.end)) if j < len(items)})
    return spans


def render(span: Span, items: list[Item], running: set[str]) -> list[tuple[str, object]]:
    """범위의 줄 → [('text', 문단들 글), ('table', 행렬), ...] 순서대로. 쪽 머리말·번호·제목 줄은 뺀다."""
    out: list[tuple[str, object]] = []
    buf: list[str] = []

    def flush():
        if buf:
            out.append(("text", "\n".join(buf)))
            buf.clear()

    heading_key = norm(span.section.get("heading") or "")
    for j in range(max(span.start, 0), min(span.end, len(items))):
        it = items[j]
        if it.kind == "table":
            rows = table_rows(it.text)
            if rows:
                flush()
                out.append(("table", rows))
            continue
        s = it.text
        k = norm(s)
        if not k or k in running or _PAGE_NO_RE.match(s):
            continue
        if heading_key and (k == heading_key or k == norm(split_number(s)[1]) and norm(split_number(span.section["heading"])[1]) == k):
            continue
        buf.append(clean_line(s))
    flush()
    return out


def plan(sections: list[dict], chunks: list[dict]) -> list[dict]:
    """채우기 계획 — 절마다 {index, heading, pages, parts:[('text', 글)|('table', 행렬)], chars, tables}. 못 맞춘 절은 parts 빈 채로."""
    items = stream(chunks)
    running = running_lines(items)
    spans = align(sections, items)
    out = []
    for sp in spans:
        parts = render(sp, items, running) if sp.start >= 0 else []
        out.append({"index": sp.section["index"], "heading": sp.section["heading"], "pages": sp.pages, "matched": sp.start >= 0,
                    "parts": parts, "chars": sum(len(p) for k, p in parts if k == "text"),
                    "tables": sum(1 for k, _ in parts if k == "table")})
    return out


def is_unfilled(section: dict) -> bool:
    """절에 아직 본문이 없는가 — 작성방법 상자(표)만 있거나 아무것도 없다."""
    return int(section.get("table_end") or 0) > int(section.get("end") or 0) - 1 or int(section.get("chars") or 0) == 0


def context_for(section: dict, plan_: list[dict], budget: int = 6000) -> str:
    """절 하나에 줄 참고 맥락 — 같은 절의 글과 표(칸은 ' | ')를 글자 예산 안에서."""
    entry = next((e for e in plan_ if e["index"] == section.get("index") and e["heading"] == section.get("heading")), None)
    if entry is None or not entry["parts"]:
        return ""
    out: list[str] = []
    used = 0
    for kind, payload in entry["parts"]:
        block = str(payload) if kind == "text" else "\n".join(" | ".join(r) for r in payload)
        if used + len(block) > budget:
            block = block[: max(0, budget - used)]
        if block:
            out.append(block)
            used += len(block)
        if used >= budget:
            break
    return "\n\n".join(out)
