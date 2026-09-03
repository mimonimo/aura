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

_kiwi = None
_noun_cache: dict[int, frozenset[str]] = {}


# 교내·행정 도메인 용어 사전 — 형태소 분석기가 쪼개지 않게 통단어로 등록.
# 실물 문서가 들어오면 자주 등장하는 용어를 여기에 계속 추가한다 (리서치 G영역 과제).
_USER_WORDS = [
    "산학협력단", "전공심화과정", "일학습병행", "공동훈련센터", "지역밀착형",
    "재정지원사업", "국고사업", "평가위원", "열람등급", "휴학원", "복학원",
    "학사경고", "계절학기", "편입학", "산업체위탁교육", "혁신지원사업",
    "취업규칙", "임용내규", "사무분장", "지식재산권", "메이커스페이스",
    "영남이공대학교", "결과보고서", "사업계획서", "공고문", "모집요강",
]


def _get_kiwi():
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi

        _kiwi = Kiwi()
        for w in _USER_WORDS:
            _kiwi.add_user_word(w, "NNP")
    return _kiwi


def extract_nouns(text: str) -> frozenset[str]:
    """형태소 분석으로 명사만 추출 — 조사·어미에 흔들리지 않는 검색 키."""
    try:
        kiwi = _get_kiwi()
        return frozenset(
            t.form
            for t in kiwi.tokenize(text[:4000])
            if t.tag.startswith("NN") and len(t.form) >= 2
        )
    except Exception:
        return frozenset(_TOKEN.findall(text[:4000]))


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


def restore_spacing(text: str) -> str:
    """OCR이 떨어뜨린 어절 공백을 Kiwi로 복원한다.

    이미 공백이 정상인 텍스트(공백 비율 8% 이상)는 건드리지 않는다 —
    원본 양식의 디지털 재구성이 목적이지 재작성이 아니다.
    """
    t = text.strip()
    if len(t) < 20:
        return text
    ratio = t.count(" ") / len(t)
    if ratio >= 0.08:
        return text
    try:
        kiwi = _get_kiwi()
        fixed = kiwi.space(t)
        return fixed if fixed else text
    except Exception:
        return text


def find_relevant(
    db: Database, query_text: str, top_k: int = 3, min_overlap: int = 2,
    sector: str | None = None,
) -> list[dict]:
    """검토 대상 텍스트와 명사가 겹치는 규정 조각 top-k (Kiwi 형태소 기반).

    점수 = 겹친 명사의 길이 합 (긴 명사가 더 정보량이 크다). 조각 명사는
    프로세스 내 캐시로 재계산을 피한다.
    """
    import math

    query = extract_nouns(query_text)
    if not query:
        return []
    chunks = db.list_regulation_chunks(sector=sector)
    for chunk in chunks:
        cid = chunk["id"]
        if cid not in _noun_cache:
            _noun_cache[cid] = extract_nouns(chunk["content"])
    # 희소성 가중치 — 어디에나 나오는 명사(기준·처리 등)는 정보량이 낮다
    n = max(len(chunks), 1)
    df = {t: sum(1 for c in chunks if t in _noun_cache[c["id"]]) for t in query}
    idf = {t: math.log(1 + n / (1 + df[t])) for t in query}

    # 희귀 명사(전체 조각의 10% 이하에서만 등장)가 질의의 실질 주제다 —
    # "휴학"이 "기준·처리" 같은 범용 명사에 밀리지 않게 1순위 정렬키로 쓴다
    rare_cut = max(3, int(n * 0.1))
    scored: list[tuple[int, float, int, dict]] = []
    for chunk in chunks:
        matched = query & _noun_cache[chunk["id"]]
        if len(matched) >= min_overlap:
            rare_hits = sum(1 for t in matched if df[t] <= rare_cut)
            score = sum(min(len(t), 4) * idf[t] for t in matched)
            scored.append((rare_hits, score, len(matched), chunk))
    scored.sort(key=lambda x: (-x[0], -x[1], -x[2]))
    lexical_ids = [c["id"] for _, _, _, c in scored]

    # 임베딩(KURE) 랭킹과 RRF 융합 — 임베딩이 비활성이면 키위 단독
    from zzaimy.app.embed_search import embed_search, rrf_merge

    allowed = {c["id"] for c in chunks}
    dense_ids = [cid for cid, _ in embed_search(query_text, top_k=12) if cid in allowed]
    by_id = {c["id"]: c for c in chunks}
    # 가중 근거: 2026-09-04 스윕(docs/retrieval-weight-sweep.md) — w_a 0.2~0.5
    # 고원(MRR .692~.693), 동가중(1.0)은 .647로 손해. 고원 중앙값 0.4 채택
    merged = rrf_merge(lexical_ids[:12], dense_ids, w_a=0.4, w_b=1.0)
    candidates = [by_id[cid] for cid in merged if cid in by_id][:10]
    # 크로스인코더 재정렬 — 표본 실측 R@1 +0.133 (docs/rerank-baseline.md)
    from zzaimy.app.rerank import rerank_chunks

    return rerank_chunks(query_text, candidates)[:top_k]


def suggest_criteria_docs(
    db: Database, masked_text: str, sector: str | None = None, top_k: int = 3
) -> list[dict]:
    """검토 대상과 연관성 높은 기준 '문서' 추천 — 조각 점수를 문서로 집계한다."""
    hits = find_relevant(db, masked_text, top_k=12, sector=sector)
    agg: dict[int, dict] = {}
    for rank, h in enumerate(hits):
        d = agg.setdefault(
            h["doc_id"], {"doc_id": h["doc_id"], "title": h["reg_title"], "score": 0.0}
        )
        d["score"] += 1.0 / (rank + 1)  # 상위 조각일수록 가중
    ranked = sorted(agg.values(), key=lambda d: -d["score"])
    return ranked[:top_k]


def compose_review_context(db: Database, masked_text: str, sector: str | None = None) -> str:
    """검토 프롬프트에 붙일 '참고 규정' 블록. 섹터 전용 + 공통 기준만 후보."""
    hits = find_relevant(db, masked_text, sector=sector)
    if not hits:
        return ""
    lines = ["[참고 규정 — 검토 의견에서 관련 조항을 근거로 인용하라]"]
    for h in hits:
        lines.append(f"《{h['reg_title']} · {h['heading']}》\n{h['content'][:600]}")
    return "\n\n".join(lines)
