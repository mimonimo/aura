"""접수 문서 처리 파이프라인 (플랫폼 v0.1).

파싱 → PII 마스킹 → 계열 분류 → 모델 검토 의견 생성.
각 단계는 기존 모듈을 조립한 것이고, 실패는 문서 상태(failed)와 error로 남긴다.
LLM에는 마스킹본만 보낸다.
"""

from __future__ import annotations

import logging
from pathlib import Path

from zzaimy.app.db import Database
from zzaimy.ingest.pii import PiiMasker, RawDocument
from zzaimy.ingest.schema import classify_series

log = logging.getLogger(__name__)

_GRANT_PROMPT = """다음은 교내 행정 문서에서 추출한 본문이다(개인정보는 마스킹됨).
행정 담당자를 위해 아래 형식으로 검토 의견을 작성하라.

요약: (2~3문장)
핵심 정보: (항목별로)
형식 점검: (누락되거나 확인이 필요한 부분)
검토 의견: (담당자가 참고할 종합 의견. 최종 판단은 담당자 몫임을 전제로)

문서 본문:
{text}"""

_RECRUIT_PROMPT = """다음은 채용 접수 서류에서 추출한 본문이다(개인정보는 마스킹됨).
채용 담당자를 위해 아래 형식으로 검토 의견을 작성하라. 지원자를 평가·서열화하지
말고, 서류에 적힌 사실의 정리와 확인 필요 사항만 다룬다.

지원자 서류 요약: (2~3문장)
기재된 학력·경력·자격: (항목별로. 서류에 적힌 것만)
확인 필요 사항: (누락 서류, 증빙 필요 항목, 기재 불일치)
검토 의견: (담당자가 참고할 정리. 최종 판단은 담당자 몫임을 전제로)

문서 본문:
{text}"""


def pick_review_prompt(doc_type: str) -> str:
    """문서 유형별 검토 프롬프트. auto·grant는 행정 일반, recruit는 채용."""
    return _RECRUIT_PROMPT if doc_type == "recruit" else _GRANT_PROMPT


class DocumentProcessor:
    """실제 처리기. 테스트에서는 FakeProcessor로 대체된다."""

    def __init__(self) -> None:
        self._masker: PiiMasker | None = None

    def _parse(self, file_path: Path) -> str:
        suffix = file_path.suffix.lower()
        if suffix in (".txt", ".md"):
            return file_path.read_text(encoding="utf-8", errors="replace")
        # PDF·이미지·오피스 문서는 docling이 처리 (OCR 자동 선택)
        from zzaimy.ingest.parsers.docling import DoclingParser

        parsed = DoclingParser().parse(file_path)
        text = "\n".join(p.text for p in parsed.pages)
        for t in parsed.tables:
            text += "\n" + "\n".join(
                " | ".join(c.text for c in t.cells if c.row == r) for r in range(t.n_rows)
            )
        return text

    def _review(self, masked_text: str, doc_type: str) -> str:
        from zzaimy.generate.client import VllmClient

        prompt = pick_review_prompt(doc_type)
        client = VllmClient()
        resp = client.client.chat.completions.create(
            model=client.model,
            messages=[{"role": "user", "content": prompt.format(text=masked_text[:8000])}],
            temperature=0.2,
            max_tokens=1024,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        return (resp.choices[0].message.content or "").strip()

    def process(self, db: Database, doc_id: int, file_path: Path) -> None:
        db.update_document(doc_id, status="processing")
        try:
            raw_text = self._parse(file_path)

            if self._masker is None:
                self._masker = PiiMasker()
            masked, events = self._masker.mask(
                RawDocument(doc_id=str(doc_id), text=raw_text)
            )
            log.info("doc %d: PII %d건 마스킹", doc_id, len(events))

            series = classify_series(file_path.name)
            doc = db.get_document(doc_id)
            doc_type = (doc or {}).get("doc_type", "auto")
            ai_review = self._review(masked.text, doc_type)

            db.update_document(
                doc_id,
                status="reviewed",
                masked_text=masked.text,
                series=series.value if series else None,
                ai_review=ai_review,
            )
        except Exception as e:  # 실패도 기록이 남아야 화면에서 보인다
            log.exception("doc %d 처리 실패", doc_id)
            db.update_document(doc_id, status="failed", error=f"{type(e).__name__}: {e}")

    def reprocess(self, db: Database, doc_id: int) -> None:
        """담당자 재검토 요청 — 남긴 의견을 반영해 검토 의견을 다시 생성한다."""
        doc = db.get_document(doc_id)
        if doc is None or not doc.get("masked_text"):
            return
        try:
            opinions = "\n".join(f"- {r['opinion']}" for r in db.get_reviews(doc_id))
            text = doc["masked_text"]
            if opinions:
                text += f"\n\n[담당자 보완 요청사항 — 재검토 시 반드시 반영하라]\n{opinions}"
            ai_review = self._review(text, doc.get("doc_type", "auto"))
            # 재검토 완료 → 다시 판정 대기 상태로
            db.update_document(doc_id, status="reviewed", ai_review=ai_review, decision="pending")
        except Exception as e:
            log.exception("doc %d 재검토 실패", doc_id)
            db.update_document(doc_id, status="failed", error=f"{type(e).__name__}: {e}")
