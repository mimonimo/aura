"""사업 분류 — 사업 카드와 문서별 사업·연차·갈래(ADR-0048 0항, 사용자 2026-10-01 "각 사업별 분류도 잘 되어야 함").

사업 이름은 코드에 넣지 않는다. 문서 제목·표지·앞머리에서 사업명(…지원사업·…육성사업 등)과 괄호 약칭(…(LINC 3.0))을 뽑고,
한 문서 안에서 '긴 이름(약칭)'·'약칭(긴 이름)'으로 함께 쓰인 표기를 같은 사업으로 묶는다(다른 이름은 문서가 알려 준다).
문서는 제목(무게 3)·앞머리(무게 1) 언급으로 점수를 매겨 사업을 고르고, 근거가 약하거나 엇갈리면 검토 대기로 둔다.
폴더 경로를 알면(DGX 원본은 사업별 폴더) 경로 언급도 무게 3 으로 더한다.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from zzaimy.graph.entities import _GENERIC, _PROGRAM, acronyms, clean_title, program_key

# 버전이 붙은 영문 약칭(LINC 3.0·LINC3.0·HiVE 2) — 사업명 옆 괄호가 없어도 제목에 홀로 쓰인다
_VERSIONED = re.compile(r"(?<![A-Za-z])([A-Z][A-Za-z+]{1,9})\s?(\d(?:\.\d)?)(?![\d.])")
_PAIR = re.compile(r"([가-힣A-Za-z0-9·()+ ]{4,40}?(?:사업|사업단))\s*\(([A-Za-z][A-Za-z0-9+. ]{1,14})\)")
_ROUND = re.compile(r"([1-9])\s*차\s*년도")
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*(?:년|학년도|\.)")
_EVAL_RESULT = re.compile(r"평가\s*(?:결과|의견)|종합\s*의견")
HEAD_CHARS = 4000
AUTO_MIN = 0.6          # 1등 점수 몫이 이보다 낮으면 검토 대기


def _acr(s: str) -> str:
    return re.sub(r"[\s.]", "", s or "").upper()


@dataclass
class ProgramCard:
    key: str                                   # 대표 키(program_key)
    names: Counter = field(default_factory=Counter)
    acrs: Counter = field(default_factory=Counter)

    @property
    def name(self) -> str:
        return self.names.most_common(1)[0][0] if self.names else (self.acrs.most_common(1)[0][0] if self.acrs else self.key)

    @property
    def node_id(self) -> str:
        a = self.acrs.most_common(1)[0][0] if self.acrs else ""
        return "program:" + (re.sub(r"[^0-9a-z가-힣]+", "", a.lower()) or self.key)

    def surfaces(self) -> set[str]:
        return {re.sub(r"\s+", "", n) for n in self.names} | {_acr(a) for a in self.acrs}


def _mentions(text: str) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    """(사업명들, 약칭들, (긴 이름, 약칭) 짝들)."""
    names = [m.group(0) for m in _PROGRAM.finditer(text or "") if m.group(0) not in _GENERIC]
    pairs = [(m.group(1).strip(), m.group(2).strip()) for m in _PAIR.finditer(text or "")]
    acrs = [f"{m.group(1)}{m.group(2)}" for m in _VERSIONED.finditer(text or "")]
    for n in names:
        acrs += acronyms(n)
    return names, acrs, pairs


def build_cards(docs: list[dict]) -> list[ProgramCard]:
    """문서들에서 사업 카드를 만든다. docs = [{id, filename, head, path?}] — 같은 사업의 다른 표기를 문서 속 짝으로 묶는다."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    names: dict[str, Counter] = defaultdict(Counter)
    acr_seen: dict[str, Counter] = defaultdict(Counter)
    for d in docs:
        text = f"{clean_title(d.get('filename') or '')}\n{d.get('path') or ''}\n{(d.get('head') or '')[:HEAD_CHARS]}"
        ns, acs, pairs = _mentions(text)
        for n in ns:
            k = "n:" + program_key(n)
            find(k)
            names[k][n.strip()] += 1
        for a in acs:
            k = "a:" + _acr(a)
            find(k)
            acr_seen[k][a.strip()] += 1
        for long_, short in pairs:                              # 문서가 알려 주는 다른 이름
            kn, ka = "n:" + program_key(long_), "a:" + _acr(short)
            names[kn][long_] += 1
            acr_seen[ka][short] += 1
            union(kn, ka)
    groups: dict[str, ProgramCard] = {}
    for k in list(parent):
        root = find(k)
        card = groups.setdefault(root, ProgramCard(key=root.split(":", 1)[1]))
        if k.startswith("n:"):
            card.names.update(names[k])
        else:
            card.acrs.update(acr_seen[k])
    for card in groups.values():
        if card.names:
            card.key = program_key(card.names.most_common(1)[0][0])
    return list(groups.values())


@dataclass
class Assignment:
    doc_id: int
    program: str = ""                # 노드 id
    program_name: str = ""
    year: int | None = None
    round: int | None = None
    kind: str = ""
    kind_reason: str = ""
    share: float = 0.0
    status: str = "review"           # auto | review
    evidence: list[str] = field(default_factory=list)


def classify(docs: list[dict], cards: list[ProgramCard]) -> list[Assignment]:
    """문서마다 사업·연차·갈래를 정한다. 제목·경로 언급 무게 3, 앞머리 1. 1등 몫이 AUTO_MIN 미만이면 검토 대기."""
    from zzaimy.app.doc_routing import guess_kind

    out = []
    for d in docs:
        title = clean_title(d.get("filename") or "")
        head = (d.get("head") or "")[:HEAD_CHARS]
        flat_title = re.sub(r"\s+", "", f"{title} {d.get('path') or ''}").upper()
        flat_head = re.sub(r"\s+", "", head).upper()
        scores: Counter = Counter()
        why: dict[str, list[str]] = defaultdict(list)
        for c in cards:
            for s in c.surfaces():
                su = s.upper()
                if len(su) < 3:
                    continue
                if su in flat_title:
                    scores[c.node_id] += 3
                    why[c.node_id].append(f"제목·경로에 「{s}」")
                n = flat_head.count(su)
                if n:
                    scores[c.node_id] += min(n, 5)
                    why[c.node_id].append(f"앞머리에 「{s}」 {n}회")
        a = Assignment(doc_id=int(d["id"]))
        if scores:
            best, top = scores.most_common(1)[0]
            card = next(c for c in cards if c.node_id == best)
            a.program, a.program_name = best, card.name
            a.share = round(top / sum(scores.values()), 2)
            a.status = "auto" if a.share >= AUTO_MIN and top >= 3 else "review"
            a.evidence = why[best][:4]
        else:
            a.evidence = ["사업명 언급을 찾지 못함"]
        m = _ROUND.search(title) or _ROUND.search(head[:600])
        a.round = int(m.group(1)) if m else None
        y = _YEAR.search(title) or _YEAR.search(head[:600])
        a.year = int(y.group(1)) if y else None
        if _EVAL_RESULT.search(title):                          # 평가 '기준'이 아니라 평가 '결과·의견'
            a.kind, a.kind_reason = "evaluation", "제목에 평가 결과·종합의견"
        else:
            a.kind, a.kind_reason = guess_kind(d.get("filename") or "", head)
        out.append(a)
    return out
