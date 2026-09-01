"""검색 스텁 (W1-W2 TASK-08) — 인터페이스만 진짜, 내용은 합성.

실제 하이브리드 검색(RRF+리랭킹)은 P3에서 이 인터페이스 뒤로 들어온다.
반환 형태(근거 + 출처)는 실제와 동일하게 유지한다 — 출처 없는 근거는 없다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Evidence:
    """인출된 근거 한 건. 출처 없는 Evidence는 만들 수 없다."""

    text: str
    source_doc: str
    source_page: int


class StubRetriever:
    """합성 실적 카드를 돌려주는 스텁. 시그니처는 실제 검색기와 동일하게 유지."""

    _SYNTHETIC = [
        Evidence(
            text="2023년 합성역량강화사업 참여인원 120명, 만족도 4.2점 달성",
            source_doc="합성_결과보고서_2023.pdf",
            source_page=12,
        ),
        Evidence(
            text="산학협력 협약 기관 35개소, 공동 프로그램 14건 운영",
            source_doc="합성_결과보고서_2023.pdf",
            source_page=27,
        ),
        Evidence(
            text="취업 지원 프로그램 수료율 91.5%",
            source_doc="합성_프로그램계획서_2024.pdf",
            source_page=3,
        ),
    ]

    def search(self, query: str, *, user_access_levels: set[str], top_k: int = 5) -> list[Evidence]:
        # 열람 등급 인자는 지금부터 필수다 (절대 규칙 4) — 스텁에서도 시그니처로 강제
        return self._SYNTHETIC[:top_k]
