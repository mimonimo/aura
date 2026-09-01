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

_REVIEW_PROMPT = """다음은 교내 행정 문서에서 추출한 본문이다(개인정보는 마스킹됨).
행정 담당자를 위해 아래 형식으로 검토 의견을 작성하라.

요약: (2~3문장)
핵심 정보: (항목별로)
형식 점검: (누락되거나 확인이 필요한 부분)
검토 의견: (담당자가 참고할 종합 의견. 최종 판단은 담당자 몫임을 전제로)

문서 본문:
{text}"""


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

    def _review(self, masked_text: str) -> str:
        from zzaimy.generate.client import VllmClient

        client = VllmClient()
        resp = client.client.chat.completions.create(
            model=client.model,
            messages=[{"role": "user", "content": _REVIEW_PROMPT.format(text=masked_text[:8000])}],
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
            ai_review = self._review(masked.text)

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
