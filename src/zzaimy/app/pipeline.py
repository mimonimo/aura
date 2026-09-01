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
적합성 판단: (참고 기준이 있으면 자격요건·제출서류 항목별 충족/미충족/확인 필요)
확인 필요 사항: (누락 서류, 증빙 필요 항목, 기재 불일치)
검토 의견: (담당자가 참고할 정리. 최종 판단은 담당자 몫임을 전제로)

문서 본문:
{text}"""


_ADMISSION_PROMPT = """다음은 입학 관련 접수 서류에서 추출한 본문이다(개인정보는 마스킹됨).
입학 담당자를 위해 아래 형식으로 검토 의견을 작성하라. 지원자를 평가·서열화하지
말고, 제출 기준 충족 여부의 확인과 사실 정리만 다룬다.

서류 요약: (2~3문장)
기재 사항 정리: (항목별로. 서류에 적힌 것만)
기준 대조: (참고 규정·모집요강 기준이 주어진 경우 항목별 충족/미충족/확인 필요)
확인 필요 사항: (누락 서류, 증빙 필요, 기재 불일치)
검토 의견: (담당자가 참고할 정리. 최종 판단은 담당자 몫임을 전제로)

문서 본문:
{text}"""


def pick_review_prompt(doc_type: str) -> str:
    """문서 유형별 검토 프롬프트 — 행정 일반 / 채용 / 입학."""
    if doc_type == "recruit":
        return _RECRUIT_PROMPT
    if doc_type == "admission":
        return _ADMISSION_PROMPT
    return _GRANT_PROMPT


class DocumentProcessor:
    """실제 처리기. 테스트에서는 FakeProcessor로 대체된다."""

    def __init__(self) -> None:
        self._masker: PiiMasker | None = None

    # 파싱 결과 상한 — 인쇄용 PDF 등에서 파서가 비정상적으로 긴 텍스트를 뽑는
    # 사례가 실측됨(26p 문서에서 950만 자). 상한 초과분은 잘라내고 경고를 남긴다.
    MAX_TEXT_CHARS = 2_000_000

    def _parse(self, file_path: Path) -> str:
        text = self._parse_inner(file_path)
        if len(text) > self.MAX_TEXT_CHARS:
            log.warning(
                "%s: 파싱 텍스트 %d자 — 상한 %d자로 절단",
                file_path.name, len(text), self.MAX_TEXT_CHARS,
            )
            text = text[: self.MAX_TEXT_CHARS]
        return text

    def _parse_inner(self, file_path: Path) -> str:
        suffix = file_path.suffix.lower()
        if suffix in (".txt", ".md"):
            return file_path.read_text(encoding="utf-8", errors="replace")
        if suffix == ".hwp":
            return self._parse_hwp(file_path)
        if suffix == ".hwpx":
            return self._parse_hwpx(file_path)
        # PDF·이미지·오피스 문서는 docling이 처리 (OCR 자동 선택)
        from zzaimy.ingest.parsers.docling import DoclingParser

        parsed = DoclingParser().parse(file_path)
        text = "\n".join(p.text for p in parsed.pages)
        for t in parsed.tables:
            text += "\n" + "\n".join(
                " | ".join(c.text for c in t.cells if c.row == r) for r in range(t.n_rows)
            )
        return text

    @staticmethod
    def _parse_hwp(file_path: Path) -> str:
        """HWP 5.0 바이너리 — pyhwp의 hwp5txt CLI로 텍스트 추출."""
        import subprocess
        import sys

        cli = Path(sys.executable).parent / "hwp5txt"
        if not cli.exists():
            raise RuntimeError("hwp5txt가 없다. pip install pyhwp")
        proc = subprocess.run(
            [str(cli), str(file_path)], capture_output=True, text=True, timeout=300
        )
        if proc.returncode != 0:
            raise RuntimeError(f"hwp5txt 실패: {proc.stderr[-500:]}")
        return proc.stdout

    @staticmethod
    def _parse_hwpx(file_path: Path) -> str:
        """HWPX — zip 안의 섹션 XML에서 태그를 걷어내고 텍스트만 남긴다."""
        import re
        import zipfile

        parts: list[str] = []
        with zipfile.ZipFile(file_path) as zf:
            for name in sorted(zf.namelist()):
                if name.startswith("Contents/section") and name.endswith(".xml"):
                    xml = zf.read(name).decode("utf-8", errors="replace")
                    text = re.sub(r"<[^>]+>", "\n", xml)
                    parts.append(re.sub(r"\n{2,}", "\n", text).strip())
        return "\n\n".join(parts)

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

    def extract_text(self, file_path: Path) -> str:
        """채팅 첨부용 — 파싱 + PII 마스킹까지만 하고 결과 텍스트를 돌려준다."""
        raw = self._parse(file_path)
        if self._masker is None:
            self._masker = PiiMasker()
        masked, _ = self._masker.mask(RawDocument(doc_id="chat", text=raw))
        return masked.text

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

            if doc_type == "regulation":
                # 규정 등록 모드 — 검토 대신 조각화해 규정 저장소에 적재
                from zzaimy.app.regulations import split_regulation

                title = (doc or {}).get("filename", f"규정 {doc_id}")
                chunks = split_regulation(masked.text)
                db.add_regulation_chunks(
                    doc_id, title, chunks, sector=(doc or {}).get("sector", "common")
                )
                db.update_document(
                    doc_id,
                    status="reviewed",
                    masked_text=masked.text,
                    series=series.value if series else None,
                    ai_review=(
                        f"규정 등록 완료 — {len(chunks)}개 조각으로 분해되어 저장소에"
                        " 들어갔습니다. 이제 문서 검토 시 이 규정이 근거로 인용됩니다."
                    ),
                )
                return

            from zzaimy.app.regulations import compose_review_context

            related_id = (doc or {}).get("related_criteria_id")
            if related_id:
                # 담당자가 지정한 공고·기준을 우선 근거로 사용
                rel_chunks = db.chunks_for_docs([int(related_id)])
                parts, budget = [], 6000
                for c in rel_chunks:
                    piece = f"《{c['reg_title']} · {c['heading']}》\n{c['content'][:800]}"
                    if budget - len(piece) < 0:
                        break
                    budget -= len(piece)
                    parts.append(piece)
                reg_context = (
                    "[대상 공고·기준 — 이 기준으로 적합성을 판단하고 인용하라]\n\n"
                    + "\n\n".join(parts)
                )
            else:
                reg_context = compose_review_context(db, masked.text, sector=doc_type)
            review_input = masked.text
            if reg_context:
                review_input = f"{masked.text}\n\n{reg_context}"
            ai_review = self._review(review_input, doc_type)

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
