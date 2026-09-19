"""배점 커버리지 의미 판정(돋보임 규칙) — 모델 없이 결정론 벡터로 검증한다."""

from __future__ import annotations

import numpy as np

from zzaimy.verify.coverage import (
    SEMANTIC_MIN_PARAS, check_coverage,
)

# 문단 4개: 서로 직교 → 어떤 항목이 특정 문단에만 가까우면 '돋보임'이 크다
P = [
    "사업 추진에 필요한 비용은 인건비와 장비비로 산출하였다.",
    "추진 체계는 총괄단장 아래 세 개 팀으로 구성한다.",
    "연차별 목표 달성도는 취업률과 만족도로 측정한다.",
    "대학의 교육 철학과 비전을 소개하는 문단이다.",
]
_VEC = {
    P[0]: [1, 0, 0, 0], P[1]: [0, 1, 0, 0], P[2]: [0, 0, 1, 0], P[3]: [0, 0, 0, 1],
    # 예산 항목: 첫 문단에만 가깝다 → 반영(의미)
    "예산 편성의 적정성": [0.95, 0.1, 0.1, 0.1],
    # 국제 항목: 모든 문단과 똑같이 어중간 → 돋보임 0 → 누락
    "국제 교류 확대": [0.5, 0.5, 0.5, 0.5],
}


def fake_embed(texts):
    m = np.array([_VEC[t] for t in texts], dtype=float)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def _criteria():
    return [
        {"name": "예산 편성의 적정성", "points": 20, "keywords": ["예산 편성의 적정성"]},
        {"name": "추진 체계", "points": 10, "keywords": ["추진 체계"]},   # 키워드로 잡힌다
        {"name": "국제 교류 확대", "points": 10, "keywords": ["국제 교류 확대"]},
    ]


def test_standout_rule_covers_semantically_and_keeps_true_negative():
    draft = "\n\n".join(P)
    r = check_coverage(_criteria(), draft, embed_fn=fake_embed)
    names = {c.name for c in r.covered}
    assert names == {"예산 편성의 적정성", "추진 체계"}
    assert [c.name for c in r.missing] == ["국제 교류 확대"]
    assert r.method["추진 체계"] == "키워드"
    assert r.method["예산 편성의 적정성"].startswith("의미 +")
    assert r.covered_points == 30 and r.total_points == 40


def test_semantic_pass_skipped_when_too_few_paragraphs():
    draft = "\n\n".join(P[: SEMANTIC_MIN_PARAS - 1])
    r = check_coverage(_criteria(), draft, embed_fn=fake_embed)
    # 문단이 적어 평균이 무의미 → 키워드만: 예산 항목은 누락으로 남는다
    assert "예산 편성의 적정성" in {c.name for c in r.missing}
    assert r.method.get("예산 편성의 적정성") is None


def test_without_embed_fn_is_keyword_only():
    r = check_coverage(_criteria(), "\n\n".join(P))
    assert {c.name for c in r.covered} == {"추진 체계"}
    assert r.method == {"추진 체계": "키워드"}
