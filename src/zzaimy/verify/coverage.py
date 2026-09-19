"""검증기 2종 — 배점 커버리지 (W1-W2 TASK-08, 브리프 6장 4번).

공고의 배점 항목별로 초안 반영 여부를 기계적으로 체크하고 누락을 보고한다.
1차 판정은 키워드 매칭(결정론). 임베딩 유사도 보조는 검색 스택이 서면 추가하되,
최종 판단은 항상 사람 몫으로 리포트만 낸다 (architecture §5).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Criterion:
    name: str
    points: int
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class CoverageReport:
    total_points: int
    covered_points: int
    covered: list[Criterion]
    missing: list[Criterion]
    method: dict[str, str] = field(default_factory=dict)   # 항목명 → 판정 근거


# 의미 판정 — 절대 임계값이 아니라 '돋보임(standout)' 규칙.
# 실측(KURE-v1, CPU, 2026-09-14): 배점 항목 문구는 짧아서 문단과의 코사인이 0.3~0.6에
# 몰리고 관련/무관이 절대값으로 겹친다(관련 최소 .464 < 무관 최대 .473). 크로스인코더
# (리랭커)도 같은 입력엔 로짓이 0 근처로 눌려 판별값이 안 나온다. 반면 '그 항목의 최고
# 문단 − 문단 평균'은 관련 .134~.181, 무관 .061로 갈린다 → 항목·초안마다 스스로
# 정규화되는 상대 규칙을 쓴다. 판정 근거(method)를 리포트에 남겨 담당자가 '의미'와
# '키워드'를 구분해 본다. 실제 초안·공고 쌍 보정은 미확인(합성 표본 기준).
SEMANTIC_MARGIN = 0.10      # 돋보임 폭(최고 − 평균) 하한 — 실측 .061(무관)·.134(관련) 사이
SEMANTIC_FLOOR = 0.40       # 최고 코사인 하한 — 전부 낮은데 하나만 덜 낮은 경우 차단
SEMANTIC_MIN_PARAS = 4      # 평균을 낼 문단이 이보다 적으면 의미 판정 생략(키워드만)


def _paragraphs(draft: str) -> list[str]:
    return [p.strip() for p in draft.split("\n\n") if len(p.strip()) >= 20]


def check_coverage(
    criteria: list[dict], draft: str, *, embed_fn=None,
) -> CoverageReport:
    """배점 항목 목록(dict: name/points/keywords)과 초안을 대조한다.

    1차: 키워드 포함(결정론·설명 가능). 2차: 키워드가 없는 항목만 학습 임베딩으로
    초안 문단과 의미 대조(embed_fn 주입 시). 키워드 하나 안 들어갔다고 누락으로
    찍거나, 단어만 있다고 반영으로 치는 짜맞추기를 줄인다. embed_fn이 없으면 1차만.
    """
    covered: list[Criterion] = []
    missing: list[Criterion] = []
    method: dict[str, str] = {}
    pending: list[Criterion] = []
    for c in criteria:
        crit = Criterion(name=c["name"], points=int(c["points"]), keywords=tuple(c["keywords"]))
        if draft and any(kw in draft for kw in crit.keywords):
            covered.append(crit)
            method[crit.name] = "키워드"
        else:
            pending.append(crit)

    paras = _paragraphs(draft) if draft else []
    if pending and len(paras) >= SEMANTIC_MIN_PARAS and embed_fn is not None:
        try:
            crit_texts = [
                " ".join(dict.fromkeys((c.name, *c.keywords))) for c in pending
            ]
            cv = embed_fn(crit_texts)
            pv = embed_fn(paras)
            if cv is not None and pv is not None:
                sims = cv @ pv.T                      # (항목 × 문단) 코사인
                for i, crit in enumerate(pending):
                    row = sims[i]
                    best = float(row.max())
                    margin = best - float(row.mean())  # 이 항목에 돋보이는 문단이 있나
                    if best >= SEMANTIC_FLOOR and margin >= SEMANTIC_MARGIN:
                        covered.append(crit)
                        method[crit.name] = f"의미 +{margin:.2f}"
                    else:
                        missing.append(crit)
                pending = []
        except Exception:
            pass                                      # 의미 판정 실패 → 1차 결과만
    missing.extend(pending)
    return CoverageReport(
        total_points=sum(c.points for c in covered) + sum(c.points for c in missing),
        covered_points=sum(c.points for c in covered),
        covered=covered,
        missing=missing,
        method=method,
    )
