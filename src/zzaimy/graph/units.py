"""사업별 일의 단위 — 한 사업의 여러 문서(연차별 계획서·실적보고서)에 되풀이되는 절을 하나의 단위 노드로 묶는다.

사업마다 일의 단위가 다르다(docs/notes/2026-10-01-business-analysis.md): LINC3.0 은 평가지표 항목(「가족회사 운영 및 활성화
실적의 적정성」), LINC+ 는 장(「사회맞춤형 교육과정」), RISE 는 단위과제. 그 이름을 코드에 넣지 않고, 사업 안에서 둘 이상의 문서에
같은 제목(번호를 뗀)으로 나오는 절을 단위로 본다. 절 번호는 연차마다 바뀌므로(15-2 → 18-2) 제목으로 묶는다.

단위가 생기면 계획(N) ↔ 실적(N) ↔ 다음 계획(N+1)이 단위 하나를 거쳐 이어진다 — 실적보고서의 「차년도 사업계획」 절처럼
같은 연차 짝으로는 잇지 않는 절도 같은 단위에 들어간다(그래프 11차 반복).

빼는 것: 한 문서 안에서 세 번 이상 되풀이되는 흔한 제목(사례마다 나오는 「1. 추진배경 및 개요」), 번호 붙은 사례(「우수사례 1」 —
사례 번호는 문서마다 바뀐다), 짧은 제목(낱말 하나).
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from zzaimy.graph.sections import _INSTANCE, _NUM, Section, title_key

MIN_KEY = 6


@dataclass
class Unit:
    key: str
    label: str
    members: list[tuple[int, str]] = field(default_factory=list)      # (문서 id, 절 경로)
    docs: set[int] = field(default_factory=set)

    def node_id(self, program_id: str) -> str:
        h = hashlib.sha1(self.key.encode("utf-8")).hexdigest()[:10]
        return f"unit:{program_id.split(':', 1)[1]}:{h}"


# 파일 이름의 일 단위 코드(단위과제 2-3) — 앞머리 순번(「01-2_」)은 뗀 뒤에 본다(scripts/161 과 같은 규칙)
_UNIT_CODE = re.compile(r"(?<![\d.\-])\[?(\d{1,2}-\d{1,2})\]?(?![\d.\-])")


def doc_code(filename: str) -> str:
    stem = re.sub(r"^[\d\s_.\-]+(?=[^\d\s_.\-])", "", re.sub(r"\.[A-Za-z0-9]+$", "", filename or ""))
    m = _UNIT_CODE.search(stem)
    return m.group(1).lstrip("0") if m else ""


def build(docs: list[tuple[int, list[Section]]], min_docs: int = 2, codes: dict[int, str] | None = None) -> list[Unit]:
    """docs = [(문서 id, 절 목록)] — 한 사업의 문서들. 둘 이상의 문서에 나오는 절 제목마다 단위 하나.

    codes = {문서 id: 일 단위 코드} — 문서가 단위과제 하나씩을 다루면(RISE 과제계획서 1-1·2-3 …) 서식 제목(「과제 배경 및 목표」)이
    모든 과제 문서에 되풀이된다. 그때는 코드가 단위다: 「2-3」 단위 아래로 그 코드 문서들의 같은 제목을 묶는다(코드 없는 문서는 그대로)."""
    codes = codes or {}
    groups: dict[str, Unit] = {}
    titles: dict[str, Counter] = defaultdict(Counter)
    for did, secs in docs:
        per_doc = Counter(title_key(s.title) for s in secs)
        code = codes.get(did, "")
        if code:
            u = groups.setdefault(f"#{code}", Unit(key=f"#{code}", label=""))
            u.docs.add(did)
            titles[f"#{code}"][f"단위과제 {code}"] += 1
        for s in secs:
            k = title_key(s.title)
            if len(k) < MIN_KEY or per_doc[k] >= 3 or _INSTANCE.search(s.title):
                continue
            k = f"#{code}/{k}" if code else k
            u = groups.setdefault(k, Unit(key=k, label=""))
            u.members.append((did, s.path))
            u.docs.add(did)
            titles[k][" ".join(_NUM.sub("", s.title).split())] += 1
    out = []
    for k, u in groups.items():
        if len(u.docs) < min_docs:
            continue
        u.label = titles[k].most_common(1)[0][0][:120]
        out.append(u)
    out.sort(key=lambda u: (-len(u.docs), u.label))
    return out


def label_clean(title: str) -> str:
    return re.sub(r"\s+", " ", _NUM.sub("", title or "")).strip()
