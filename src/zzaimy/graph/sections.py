"""절 트리 — 저장된 제목 조각으로 문서의 목차를 세우고 조각을 절에 매단다(ADR-0048 1·2항).

처리기가 남긴 제목 조각(kind=heading)의 번호 모양으로 깊이를 정한다(chunk_path.level_of — Ⅰ. · 1. · 1.1. · 가. …). 절 id 는 문서 안
차례 경로(예: 2.1.3 — 실제 번호가 아니라 깊이별 순번)라 같은 문서 판본에서 고정이다. 조각 자르기를 절 단위로 바꾸기 전에도
'이 조각은 어느 절의 것인가'를 저장된 순서로 정한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from zzaimy.app.chunk_path import level_of

_NUM = re.compile(r"^\s*([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|\d+(?:[.-]\d+)*|[가-하]|\(\d+\)|\d+\))[.．)]?\s*")


@dataclass
class Section:
    path: str                     # 깊이별 순번 경로 '2.1.3'
    title: str
    level: int
    seq: int                      # 제목 조각의 seq
    chunks: list[int] = field(default_factory=list)   # 매달린 조각 seq
    parent: str = ""


def title_key(title: str) -> str:
    """절 제목 대조 키 — 번호를 떼고 공백·기호를 지운다(계획서와 보고서의 같은 절을 잇는 '식별자 일치')."""
    t = _NUM.sub("", title or "")
    return re.sub(r"[^0-9A-Za-z가-힣]", "", t)


def build(chunks: list[dict]) -> list[Section]:
    """chunks = doc_chunks(seq 순). 제목 조각이 없으면 빈 목록."""
    sections: list[Section] = []
    stack: list[Section] = []
    counters: dict[tuple[str, int], int] = {}
    for c in sorted(chunks, key=lambda c: c["seq"]):
        if c.get("kind") == "heading":
            title = " ".join(str(c.get("content") or "").split())[:200]
            lvl = level_of(title)
            while stack and stack[-1].level >= lvl:
                stack.pop()
            parent = stack[-1].path if stack else ""
            n = counters.get((parent, lvl), 0) + 1
            counters[(parent, lvl)] = n
            sec = Section(path=f"{parent}.{n}" if parent else str(n), title=title, level=lvl, seq=int(c["seq"]), parent=parent)
            sections.append(sec)
            stack.append(sec)
        elif stack:
            stack[-1].chunks.append(int(c["seq"]))
    return sections


def align(a: list[Section], b: list[Section], min_key: int = 4) -> list[tuple[Section, Section, str]]:
    """두 문서의 같은 절 짝 — 제목이 같고, 제목이 문서 안에서 반복되면 상위 절 제목까지 같아야 한다(한 절은 짝 하나).
    '4. 기대효과 및 향후 과제'처럼 과제마다 되풀이되는 제목이 서로 다른 과제끼리 이어지던 것(실측 2026-10-01: 계획↔실적 절 연결의
    83% 가 모호)을 막는다. 돌려주는 것: (a 절, b 절, 근거 설명)."""
    def index(secs: list[Section]):
        by_path = {x.path: x for x in secs}

        def ancestors(x: Section) -> tuple[str, ...]:
            out, cur = [], x.parent
            while cur and len(out) < 3:
                par = by_path.get(cur)
                if not par:
                    break
                out.append(title_key(par.title))
                cur = par.parent
            return tuple(out)
        groups: dict[str, list[tuple[Section, tuple[str, ...]]]] = {}
        for x in secs:
            k = title_key(x.title)
            if len(k) >= min_key:
                groups.setdefault(k, []).append((x, ancestors(x)))
        return groups
    ga, gb = index(a), index(b)
    pairs, used_b = [], set()
    for k, xs in ga.items():
        ys = gb.get(k)
        if not ys:
            continue
        if len(xs) == 1 and len(ys) == 1:
            x, y = xs[0][0], ys[0][0]
            pairs.append((x, y, "제목이 두 문서에서 하나뿐"))
            used_b.add(id(y))
            continue
        for x, ax in xs:
            def depth(ay: tuple[str, ...]) -> int:
                n = 0
                for p_, q_ in zip(ax, ay):
                    if p_ != q_:
                        break
                    n += 1
                return n
            scored = sorted(((depth(ay), y) for y, ay in ys if id(y) not in used_b), key=lambda t: -t[0])
            if not scored or scored[0][0] == 0 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
                continue                                         # 상위 절이 안 맞거나 같은 점수 후보가 여럿 — 잇지 않는다
            y = scored[0][1]
            used_b.add(id(y))
            pairs.append((x, y, f"제목과 상위 절 {scored[0][0]}단계가 같음"))
    return pairs
