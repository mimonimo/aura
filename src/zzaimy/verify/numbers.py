"""검증기 1종 — 수치 대조 (W1-W2 TASK-08, 브리프 5.3).

생성 초안의 모든 수치는 인출된 근거에 존재해야 한다. 결정론적으로 판정하며
LLM 판단을 쓰지 않는다. 이 모듈은 Writer 학습 데이터 정제기와 공유된다
(model-plan §2 — 학습과 서빙이 같은 기준으로 수치를 판정).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 천단위 콤마 허용, 소수 허용. 예: 1,234 / 4.2 / 120
_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
# 목록 마커("1. ", "2) ")는 구조 숫자 — 수치가 아니다
_LIST_MARKER = re.compile(r"(?:^|\n)\s*\d+[.)]\s")


def extract_numbers(text: str) -> set[str]:
    """텍스트에서 수치를 정규형(콤마 제거)으로 추출한다."""
    cleaned = _LIST_MARKER.sub("\n", text)
    return {m.group().replace(",", "") for m in _NUMBER.finditer(cleaned)}


@dataclass(frozen=True)
class NumberAudit:
    ok: bool
    violations: list[str]  # 근거 없는 수치들 (정규형)


def verify_numbers(draft: str, evidence_texts: list[str]) -> NumberAudit:
    """초안의 수치가 전부 근거 텍스트에 존재하는지 대조한다."""
    allowed: set[str] = set()
    for ev in evidence_texts:
        allowed |= extract_numbers(ev)
    violations = sorted(extract_numbers(draft) - allowed)
    return NumberAudit(ok=not violations, violations=violations)
