"""조각의 제목 계층(breadcrumb) — kordoc 의 RAG 청크 설계(제목 경로를 청크마다 붙임)를 본받아 우리 조각에 읽을 때 계산한다(ADR-0033 7항).

저장 구조는 바꾸지 않는다: doc_chunks 는 문서 순서대로 heading 조각을 갖고 있으므로, 앞선 heading 들로 경로를 만든다.
번호 모양으로 깊이를 정한다 — Ⅰ. (1) · 1. (2) · 1.1. (3) · 1.1.1. (4) · 1) 가. (5) · 번호 없음(현재 깊이 + 1, 잎).
"""

from __future__ import annotations

import re

_ROMAN = re.compile(r"^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVX]+\s*[.．]")
_DOTTED = re.compile(r"^\s*(\d+(?:\.\d+)*)[.．]?(?=\s|$)")
_SUB = re.compile(r"^\s*(?:\(?\d+\)|[가-힣][.)]|[a-z][.)])\s")
_BOX = re.compile(r"^\s*【[^】]{1,40}】")
MAX_DEPTH = 6


def level_of(heading: str) -> int:
    h = (heading or "").strip().replace("**", "")
    if _ROMAN.match(h):
        return 1
    m = _DOTTED.match(h)
    if m:
        return min(1 + m.group(1).count(".") + 1, MAX_DEPTH)      # 1. → 2, 1.1. → 3
    if _SUB.match(h):
        return 5
    if _BOX.match(h):
        return 2
    return 0                                                        # 번호 없음 — 잎


def attach_paths(chunks: list[dict]) -> list[dict]:
    """조각마다 path(제목 목록)·path_text('Ⅰ > 1.1 > 강점') 를 붙여 돌려준다(원본 dict 를 복사). heading 조각은 자기 자신을 포함한 경로."""
    stack: list[tuple[int, str]] = []
    out: list[dict] = []
    for ch in sorted(chunks, key=lambda c: (int(c.get("page_no") or 0), int(c.get("seq") or c.get("id") or 0))):
        c = dict(ch)
        if (c.get("kind") or "") == "heading":
            text = " ".join(str(c.get("content") or "").replace("**", "").split())[:80]
            lvl = level_of(text)
            if lvl == 0:
                lvl = min(len(stack) + 1, MAX_DEPTH)
                stack = [s for s in stack if s[0] < lvl]
            else:
                stack = [s for s in stack if s[0] < lvl]
            stack.append((lvl, text))
        c["path"] = [t for _l, t in stack]
        c["path_text"] = " > ".join(c["path"])
        out.append(c)
    return out
