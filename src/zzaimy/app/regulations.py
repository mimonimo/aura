"""규정 저장소 — 외부/내부 관리 규정을 근거로 검토하기 위한 계층.

규정 문서(학칙·훈령·사업 운영 매뉴얼 등)를 조각 단위로 저장하고, 검토 대상
문서와 관련 있는 조각을 찾아 검토 프롬프트에 근거로 넣는다.
검색은 키워드 겹침 점수(v1) — P3에서 벡터 검색으로 교체한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from zzaimy.app.db import Database

# 조문형 규정의 분할 지점: 제N조 / 제N장 / 제N절
_ARTICLE = re.compile(r"(?=제\d+[조장절])")
_TOKEN = re.compile(r"[가-힣A-Za-z0-9]{2,}")
_CHUNK_SIZE = 700


@dataclass(frozen=True)
class RegulationChunk:
    heading: str
    content: str


def _heading_of(text: str) -> str:
    first = text.strip().splitlines()[0].strip()
    return first[:60]


def split_regulation(text: str) -> list[RegulationChunk]:
    """조문형이면 제N조 단위로, 아니면 문단 묶음(약 700자)으로 나눈다."""
    text = text.strip()
    if not text:
        return []

    parts = [p.strip() for p in _ARTICLE.split(text) if p.strip()]
    if len(parts) >= 3:  # 조문 구조가 실제로 있다고 판단
        return [RegulationChunk(heading=_heading_of(p), content=p) for p in parts]

    def looks_like_heading(para: str) -> bool:
        first = para.splitlines()[0].strip()
        return len(first) <= 25 and not first.endswith(("다.", "함.", "음.", "."))

    chunks: list[RegulationChunk] = []
    buf = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        # 제목형 문단(짧은 첫 줄)에서 새 조각 시작 — 매뉴얼의 장·절 경계
        if buf and (looks_like_heading(para) or len(buf) + len(para) > _CHUNK_SIZE):
            chunks.append(RegulationChunk(heading=_heading_of(buf), content=buf))
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(RegulationChunk(heading=_heading_of(buf), content=buf))
    return chunks


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text))


def find_relevant(
    db: Database, query_text: str, top_k: int = 3, min_overlap: int = 2
) -> list[dict]:
    """검토 대상 텍스트와 키워드가 겹치는 규정 조각 top-k.

    한국어 조사 때문에 토큰 완전일치는 빗나가기 쉬워, 질의 토큰이 조각 본문에
    부분문자열로 등장하는 개수로 센다. (형태소 기반 검색은 P3에서 Kiwi로 교체)
    """
    query = _tokens(query_text)
    scored: list[tuple[int, dict]] = []
    for chunk in db.list_regulation_chunks():
        content = chunk["content"]
        overlap = sum(1 for t in query if t in content)
        if overlap >= min_overlap:
            scored.append((overlap, chunk))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:top_k]]


def compose_review_context(db: Database, masked_text: str) -> str:
    """검토 프롬프트에 붙일 '참고 규정' 블록. 관련 규정이 없으면 빈 문자열."""
    hits = find_relevant(db, masked_text)
    if not hits:
        return ""
    lines = ["[참고 규정 — 검토 의견에서 관련 조항을 근거로 인용하라]"]
    for h in hits:
        lines.append(f"《{h['reg_title']} · {h['heading']}》\n{h['content'][:600]}")
    return "\n\n".join(lines)
