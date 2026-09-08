"""개체 추출 1차 패스 — 결정론 (지식 그래프 2단계, ADR-0010).

문서에서 사업·기관·연도 개체를 규칙으로 추출한다. 특정 문서에 맞춘 예외가
아니라 한국어 행정문서의 일반 형태(고유 접미사·연도 표기)만 쓰므로 하드코딩
금지 원칙과 충돌하지 않는다. LLM 정제 패스(2차)는 이 위에 얹는다 — 1차가
결정론이어야 재실행 가능하고 GPU 없이 돌며 결과를 검증할 수 있다.

과잉 추출을 피하는 쪽으로 보수적으로 잡는다: 짧고 흔한 접미사(부·과·팀)는
오탐이 많아 제외하고, 판별력 있는 복합 접미사만 쓴다.
"""

from __future__ import annotations

import re
from collections import Counter

# 연도 — "2026년" 형태만 (숫자 단독은 수치와 혼동)
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*년")

# 기관 — 판별력 있는 복합 접미사. 이름부는 한글·영문·숫자 2~18자
_ORG = re.compile(
    r"[가-힣A-Za-z0-9]{2,18}"
    r"(?:사업단|위원회|지원센터|훈련센터|평가원|진흥원|연구원|장학재단|재단|"
    r"공단|공사|본부|대학교)"
)

# 사업·제도 — 명명된 사업류. 이름부가 있어야 하고 일반어 단독은 제외
_PROGRAM = re.compile(
    r"[가-힣A-Za-z0-9()]{2,24}(?:지원사업|육성사업|혁신사업|훈련과정|장학금|전형)"
)

# 일반어 — 이름이 아니라 범주 그 자체 (개체로 세지 않는다)
_GENERIC = {
    "지원사업", "육성사업", "혁신사업", "국고지원사업", "재정지원사업",
    "장학금", "전형", "훈련과정", "대학교", "본부", "재단", "위원회",
}


def extract_mentions(text: str) -> Counter:
    """텍스트에서 (이름, 유형) 언급 횟수를 센다."""
    out: Counter = Counter()
    for m in _YEAR.finditer(text):
        out[(f"{m.group(1)}년", "year")] += 1
    for pattern, kind in ((_ORG, "org"), (_PROGRAM, "program")):
        for m in pattern.finditer(text):
            name = m.group().strip()
            if name in _GENERIC:
                continue
            out[(name, kind)] += 1
    return out


def extract_doc_entities(db, min_mentions: int = 1) -> dict[str, int]:
    """전체 문서의 개체 언급을 추출해 DB에 교체 저장한다.

    기준 문서는 규정 조각에서, 접수 문서는 마스킹 본문에서 읽는다.
    반환: 요약 집계 (문서 수·개체 언급 수).
    """
    n_docs = 0
    n_links = 0
    for d in db.list_documents():
        if d.get("doc_type") == "ocr":
            continue
        if d.get("doc_type") == "regulation":
            parts = [c["content"] for c in db.chunks_for_docs([d["id"]])]
            parts.append(d.get("filename") or "")
            text = "\n".join(parts)
        else:
            text = d.get("masked_text") or ""
        if not text.strip():
            continue
        counts = extract_mentions(text)
        mentions = [
            (name, kind, n) for (name, kind), n in counts.items()
            if n >= min_mentions
        ]
        db.replace_doc_entities(d["id"], mentions)
        n_docs += 1
        n_links += len(mentions)
    return {"docs": n_docs, "links": n_links}
