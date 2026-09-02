"""vLLM 호출 계층 (W1-W2 TASK-08).

공고 스키마 추출(guided_json — vLLM 구조화 출력으로 형식 보장)과
섹션 초안 생성. 수치는 근거에 있는 것만 쓰도록 프롬프트로 지시하되,
**최종 보증은 프롬프트가 아니라 검증기가 한다** (브리프 5.3 — 구조 수준 강제).
"""

from __future__ import annotations

import os

from openai import OpenAI

from zzaimy.generate.schema import AnnouncementSchema
from zzaimy.retrieve.stub import Evidence

_SCHEMA_PROMPT = """다음은 국고사업 공고문 본문이다. 공고에서 아래 정보를 추출해 JSON으로 답하라.
- title: 사업명
- sections: 계획서에 써야 할 목차. 공고에 작성 항목·제출 목차가 있으면 항목별로
  나눠라(하나로 뭉치지 마라). 목차가 없으면 사업 개요/추진 계획/성과 관리/예산처럼
  공고 내용에서 유추되는 3~6개 항목으로 나눠라
- criteria: 평가지표. 반드시 배점표·심사기준에 적힌 것만 쓰고, points는 그 표의
  배점 숫자만 넣어라. 사업비·지원 금액·인원수 같은 수치를 배점으로 넣지 마라.
  배점표가 아예 없으면 criteria는 빈 배열로 둬라
- budget_limit_krw: 예산 상한 (원 단위 정수, 명시 없으면 null)

공고 본문에 없는 내용은 만들지 마라.

공고 본문:
{announcement}"""

_SECTION_PROMPT = """너는 전문대학의 국고사업 계획서 작성을 돕는 조력자다.
아래 섹션 초안을 한국어 공문서 문체로 작성하라.

[섹션] {section_name}
[요구사항] {requirements}
[평가지표] {criteria}
[방향성] {direction}

[인출된 근거 — 수치는 반드시 여기 있는 것만 쓰고, 근거 없는 수치 자리는 (근거 없음)으로 둘 것]
{evidence}

섹션 본문만 출력하라. 3~5문단."""


def _strip_fences(text: str) -> str:
    """모델이 JSON을 ```json 펜스로 감싸는 경우의 방어적 제거."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()


class VllmClient:
    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.client = OpenAI(
            base_url=base_url or os.environ.get("VLLM_BASE_URL", "http://localhost:8000/v1"),
            api_key=os.environ.get("VLLM_API_KEY", "dummy"),
        )
        self.model = model or self.client.models.list().data[0].id

    def extract_schema(self, announcement_text: str) -> AnnouncementSchema:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "user", "content": _SCHEMA_PROMPT.format(announcement=announcement_text)}
            ],
            temperature=0.0,
            max_tokens=2048,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "AnnouncementSchema",
                    "schema": AnnouncementSchema.model_json_schema(),
                },
            },
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        return AnnouncementSchema.model_validate_json(
            _strip_fences(resp.choices[0].message.content or "")
        )

    def generate_section(
        self,
        section_name: str,
        requirements: str,
        criteria_text: str,
        direction: str,
        evidence: list[Evidence],
    ) -> str:
        evidence_block = "\n".join(
            f"- {e.text} (출처: {e.source_doc} p.{e.source_page})" for e in evidence
        )
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": _SECTION_PROMPT.format(
                        section_name=section_name,
                        requirements=requirements,
                        criteria=criteria_text,
                        direction=direction,
                        evidence=evidence_block,
                    ),
                }
            ],
            temperature=0.3,
            max_tokens=1024,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        return (resp.choices[0].message.content or "").strip()
