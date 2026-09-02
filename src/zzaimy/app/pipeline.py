"""접수 문서 처리 파이프라인 (플랫폼 v0.1).

인풋 문서: 파싱 → PII 마스킹 → 계열 분류 → 모델 검토 의견 생성 (LLM에는
마스킹본만 보낸다). 기준(regulation) 문서: 판단 근거이므로 마스킹 없이 원문을
조각화해 규정 저장소에 적재한다. 실패는 문서 상태(failed)와 error로 남긴다.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from zzaimy.app.db import Database
from zzaimy.ingest.pii import PiiMasker, RawDocument

if TYPE_CHECKING:
    from zzaimy.ingest.parsers.base import ParseResult
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


def _split_chunks(text: str, max_chunks: int = 400) -> list[dict]:
    """마스킹본을 문단 단위 조각으로 나눈다. ' | ' 줄이 과반이면 표 조각으로 표시."""
    chunks: list[dict] = []
    for para in text.split("\n\n"):
        p = para.strip()
        if len(p) < 40:
            continue
        lines = [ln for ln in p.splitlines() if ln.strip()]
        n_table = sum(1 for ln in lines if " | " in ln)
        kind = "table" if lines and n_table * 2 >= len(lines) else "text"
        chunks.append({"kind": kind, "content": p[:2000]})
        if len(chunks) >= max_chunks:
            break
    return chunks


def _guidance_block(db: Database, project: dict | None) -> str:
    """담당자 전역 지침 + 프로젝트 지침·메모를 검토 입력 뒤에 붙인다."""
    parts = []
    global_inst = db.get_setting("instructions").strip()
    if global_inst:
        parts.append(f"[담당자 지침 — 검토 의견 작성 시 따르라]\n{global_inst}")
    if project:
        p_inst = (project.get("instructions") or "").strip()
        if p_inst:
            parts.append(f"[프로젝트 「{project['name']}」 지침]\n{p_inst}")
        p_memo = (project.get("memo") or "").strip()
        if p_memo:
            parts.append(f"[프로젝트 메모 — 참고 맥락]\n{p_memo}")
        notes = db.list_project_notes(int(project["id"]))[:10]
        if notes:
            joined = "\n".join(f"- ({n['created_at'][:10]}) {n['content']}" for n in notes)
            parts.append(
                f"[프로젝트 「{project['name']}」 지침·메모 — 검토·작성 시 따르고 참고하라]"
                f"\n{joined}"
            )
    return ("\n\n" + "\n\n".join(parts)) if parts else ""


class DocumentProcessor:
    """실제 처리기. 테스트에서는 FakeProcessor로 대체된다."""

    def __init__(self) -> None:
        self._masker: PiiMasker | None = None
        self._last_images: list[tuple[int, Path]] = []
        self._last_parse_note = ""
        self._last_result: ParseResult | None = None  # 표 구조 보존용
        self._last_attrs: list[str] = []  # 손글씨·도장 등 문서 속성

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
        # 파싱 부산물(그림·표 구조·파싱 방식 메모)은 호출 사이에 남지 않게 초기화한다
        self._last_images = []
        self._last_parse_note = ""
        self._last_result = None
        self._last_attrs = []
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
        self._last_result = parsed
        text = self._result_to_text(parsed)

        # 스캔 문서 감지 — 페이지당 텍스트가 빈약하면 MinerU OCR로 재파싱한다.
        # MinerU(오픈소스, PaddleOCR 계열)는 표를 구조로, 그림을 파일로 뽑아준다
        n_pages = max(len(parsed.pages), 1)
        if len(text.strip()) < max(400, 60 * n_pages) and not os.environ.get(
            "ZZAIMY_NO_OCR_FALLBACK"
        ):
            ocr_text = self._parse_mineru(file_path)
            if ocr_text is not None and len(ocr_text.strip()) > len(text.strip()):
                return ocr_text
        return text

    def _parse_mineru(self, file_path: Path) -> str | None:
        """MinerU OCR 경로 — 실패해도 기본 파싱 결과로 진행할 수 있게 None을 준다."""
        import tempfile

        from zzaimy.ingest.parsers.mineru import MineruNotInstalled, MineruParser

        try:
            with tempfile.TemporaryDirectory(prefix="zz-mineru-") as tmp:
                parsed = MineruParser(method="ocr").parse(file_path, work_dir=Path(tmp))
                self._last_result = parsed
                text = self._result_to_text(parsed)
                # 그림은 임시 디렉터리가 사라지기 전에 밖으로 복사한다
                keep_dir = file_path.parent / f"{file_path.stem}_imgs"
                images: list[tuple[int, Path]] = []
                seen_hash: set[str] = set()
                for img in parsed.images:
                    if len(images) >= 20:
                        break
                    if not self._is_meaningful_image(img.path, seen_hash):
                        continue  # 체크박스·불릿 같은 장식 아이콘, 중복은 걸러낸다
                    keep_dir.mkdir(parents=True, exist_ok=True)
                    dest = keep_dir / img.path.name
                    shutil.copyfile(img.path, dest)
                    images.append((img.page_no, dest))
                self._last_images = images
                self._last_parse_note = (
                    f"스캔 문서 OCR 처리 (MinerU) · 표 {len(parsed.tables)}개"
                    f" · 그림 {len(images)}장"
                )
                log.info(
                    "%s: MinerU OCR 재파싱 — %d자, 표 %d, 그림 %d",
                    file_path.name, len(text), len(parsed.tables), len(images),
                )
                return text
        except MineruNotInstalled:
            log.warning("MinerU 미설치 — OCR 폴백 생략")
        except Exception as e:
            log.warning("MinerU OCR 실패(%s) — 기본 파싱 결과로 진행", type(e).__name__)
        return None

    _VLM_PROMPT = (
        "첫 줄에 반드시 `[[속성]] 손글씨:예/아니오, 도장:예/아니오, 표:예/아니오`"
        " 형식으로 문서 속성을 적어라. 둘째 줄부터 본문 전사를 시작한다.\n"
        "이 이미지는 행정 문서(스캔·사진)다. 보이는 내용을 읽기 순서대로 정확히"
        " 전사하라.\n- 큰 제목은 '## '로 시작하는 줄로\n- 굵거나 큰 글씨는"
        " **굵게**로\n- 표는 HTML <table>로: 병합 셀은 rowspan/colspan,"
        " 머리글(음영·강조된 행이나 열)은 <th>로 원본 구조 그대로\n"
        "- 날짜, 문서번호, 서명, 직인(도장)에 새겨진 글자도 보이는 대로 옮겨라"
        " (도장은 '(직인: ...)' 형태로)\n- 이미지에 없는 내용은 절대 지어내지 마라."
        " 읽을 수 없는 부분은 (판독 불가)로 표시하라."
    )

    def _vlm_transcribe(self, image_path: Path) -> str | None:
        """비전 모델(Qwen3.5)로 사진 속 문서 전사 — 손글씨·도장 문구까지 읽는다."""
        import base64

        try:
            from zzaimy.generate.client import VllmClient

            send_path = image_path
            if image_path.stat().st_size > 3_500_000:
                # 대용량 사진은 줄여서 보낸다 — 요청 한도 초과 방지
                from PIL import Image

                with Image.open(image_path) as im:
                    im.thumbnail((2000, 2000))
                    send_path = image_path.parent / f"{image_path.stem}_vlm.jpg"
                    im.convert("RGB").save(send_path, quality=88)
            mime = "image/png" if send_path.suffix.lower() == ".png" else "image/jpeg"
            b64 = base64.b64encode(send_path.read_bytes()).decode()
            client = VllmClient()
            resp = client.client.chat.completions.create(
                model=client.model,
                temperature=0.0,
                max_tokens=2500,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url",
                         "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        {"type": "text", "text": self._VLM_PROMPT},
                    ],
                }],
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            text = (resp.choices[0].message.content or "").strip()
            # 속성 줄 분리 — 하드케이스 분류용 (손글씨·도장 여부)
            first, _, rest = text.partition("\n")
            if first.startswith("[[속성]]"):
                attrs = []
                if "손글씨:예" in first:
                    attrs.append("손글씨")
                if "도장:예" in first:
                    attrs.append("도장")
                if attrs:
                    self._last_attrs = attrs
                text = rest.strip()
            return text or None
        except Exception as e:
            log.warning("비전 판독 실패(%s) — OCR 결과로 진행", type(e).__name__)
            return None

    @staticmethod
    def _md_to_chunks(md: str, mk, page_no: int = 1) -> list[dict]:
        """비전 판독 마크다운을 조각으로 — '## ' 제목, 파이프 표, 문단."""
        import json as _json

        out: list[dict] = []
        lines = md.splitlines()
        i = 0
        para: list[str] = []

        def flush_para() -> None:
            if para:
                blk = "\n".join(para).strip()
                if len(blk) >= 2:
                    out.append(
                        {"kind": "text", "page_no": page_no, "content": mk(blk)[:2000]}
                    )
                para.clear()

        while i < len(lines):
            ln = lines[i].strip()
            if ln.lower().startswith("<table"):
                flush_para()
                html_lines = []
                while i < len(lines):
                    html_lines.append(lines[i])
                    if "</table>" in lines[i].lower():
                        i += 1
                        break
                    i += 1
                try:
                    from zzaimy.ingest.parsers.html_table import parse_html_table

                    t = parse_html_table("\n".join(html_lines), page_no=page_no)
                    cells_json = [
                        [c.row, c.col, c.row_span, c.col_span,
                         1 if c.is_header else 0, mk(c.text)]
                        for c in t.cells
                    ]
                    out.append({
                        "kind": "table", "page_no": page_no,
                        "content": _json.dumps(
                            {"n_rows": t.n_rows, "n_cols": t.n_cols,
                             "cells": cells_json},
                            ensure_ascii=False,
                        ),
                    })
                except Exception:
                    out.append({
                        "kind": "text", "page_no": page_no,
                        "content": mk("\n".join(html_lines))[:2000],
                    })
                continue
            if ln.startswith("|") and ln.count("|") >= 2:
                flush_para()
                rows = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    if not all(set(c) <= set("-: ") for c in cells):  # 구분선 제외
                        rows.append(cells)
                    i += 1
                if rows:
                    n_cols = max(len(r) for r in rows)
                    cells_json = [
                        [ri, ci, 1, 1, 1 if ri == 0 else 0, mk(val)]
                        for ri, row in enumerate(rows)
                        for ci, val in enumerate(row)
                    ]
                    out.append({
                        "kind": "table", "page_no": page_no,
                        "content": _json.dumps(
                            {"n_rows": len(rows), "n_cols": n_cols, "cells": cells_json},
                            ensure_ascii=False,
                        ),
                    })
                continue
            if ln.startswith("#"):
                flush_para()
                out.append({
                    "kind": "heading", "page_no": page_no,
                    "content": mk(ln.lstrip("# ").strip())[:300],
                })
            elif not ln:
                flush_para()
            else:
                para.append(ln)
            i += 1
        flush_para()
        return out

    def _extract_stamps(self, image_path: Path) -> list[Path]:
        """빨간 직인(도장) 영역을 찾아 잘라낸다 — OpenCV 색 분리, 없으면 빈 목록."""
        try:
            import cv2
            import numpy as np

            img = cv2.imread(str(image_path))
            if img is None:
                return []
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            m1 = cv2.inRange(hsv, (0, 60, 60), (10, 255, 255))
            m2 = cv2.inRange(hsv, (160, 60, 60), (180, 255, 255))
            mask = cv2.dilate(m1 | m2, np.ones((9, 9), np.uint8))
            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            h_img, w_img = img.shape[:2]
            out_dir = image_path.parent / f"{image_path.stem}_imgs"
            saved: list[Path] = []
            for idx, c in enumerate(sorted(contours, key=cv2.contourArea, reverse=True)):
                x, y, w, h = cv2.boundingRect(c)
                area_ratio = (w * h) / float(w_img * h_img)
                # 도장 크기 범위(전체의 0.2~15%)와 형태(정사각형에 가까움)만
                if not (0.002 <= area_ratio <= 0.15 and 0.5 <= w / max(h, 1) <= 2.0):
                    continue
                pad = int(max(w, h) * 0.12)
                x0, y0 = max(0, x - pad), max(0, y - pad)
                x1, y1 = min(w_img, x + w + pad), min(h_img, y + h + pad)
                out_dir.mkdir(parents=True, exist_ok=True)
                dest = out_dir / f"stamp_{idx}.png"
                cv2.imwrite(str(dest), img[y0:y1, x0:x1])
                saved.append(dest)
                if len(saved) >= 3:
                    break
            return saved
        except Exception:
            return []

    _CORRECT_PROMPT = (
        "다음은 스캔 문서의 OCR 텍스트다. 오인식된 글자와 붙어버린 띄어쓰기만 고쳐라.\n"
        "규칙: 문장을 추가·삭제·요약하지 마라. 순서를 바꾸지 마라. 원문에 없는 내용을"
        " 만들지 마라. 확신이 없으면 그대로 둬라. 구분자 <<<>>> 는 그대로 유지하라.\n"
        "고친 전문만 출력하라.\n\n{text}"
    )

    def _llm_correct_chunks(self, chunks: list[dict]) -> bool:
        """MinerU 텍스트 조각의 OCR 오타를 LLM으로 교정한다 (마스킹 후 호출)."""
        targets = [c for c in chunks if c["kind"] in ("text", "heading")]
        fixed = self._correct_texts([c["content"] for c in targets])
        if fixed is None:
            return False
        for c, f in zip(targets, fixed):
            c["content"] = f[:2000]
        return True

    def _correct_texts(self, texts: list[str]) -> list[str] | None:
        """텍스트 목록의 OCR 오타 교정 — 실패·훼손 배치는 원문 유지, 전체 실패면 None.

        구분자 개수가 어긋나면 그 배치는 원문을 유지한다.
        """
        try:
            from zzaimy.generate.client import VllmClient

            client = VllmClient()
        except Exception:
            return None

        targets = [{"content": t} for t in texts]
        if not targets:
            return None
        SEP = "\n<<<>>>\n"
        batches: list[list[dict]] = [[]]
        size = 0
        for c in targets:
            if size + len(c["content"]) > 2600 and batches[-1]:
                batches.append([])
                size = 0
            batches[-1].append(c)
            size += len(c["content"])

        corrected_any = False
        for batch in batches:
            joined = SEP.join(c["content"] for c in batch)
            try:
                resp = client.client.chat.completions.create(
                    model=client.model,
                    temperature=0.0,
                    max_tokens=4000,
                    messages=[{
                        "role": "user",
                        "content": self._CORRECT_PROMPT.format(text=joined),
                    }],
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                )
                out = (resp.choices[0].message.content or "").strip()
                parts = [p.strip() for p in out.split("<<<>>>")]
                if len(parts) != len(batch):
                    continue  # 구분자 훼손 — 이 배치는 원문 유지
                for c, fixed in zip(batch, parts):
                    # 교정은 보수적으로 — 길이가 크게 변하면 지어낸 것으로 보고 버린다
                    if fixed and 0.6 <= len(fixed) / max(len(c["content"]), 1) <= 1.5:
                        c["content"] = fixed
                        corrected_any = True
            except Exception:
                continue
        return [c["content"] for c in targets] if corrected_any else None

    @staticmethod
    def _crop_document_region(image_path: Path) -> Path | None:
        """사진 속 문서(종이) 영역을 찾아 원근 보정해 펴낸다 — 실패하면 None."""
        try:
            import cv2
            import numpy as np

            img = cv2.imread(str(image_path))
            if img is None:
                return None
            h, w = img.shape[:2]
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150)
            edges = cv2.dilate(edges, np.ones((5, 5), np.uint8))
            contours, _ = cv2.findContours(
                edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            best = None
            for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
                peri = cv2.arcLength(c, True)
                approx = cv2.approxPolyDP(c, 0.02 * peri, True)
                if len(approx) == 4 and cv2.contourArea(approx) > 0.3 * w * h:
                    best = approx.reshape(4, 2).astype("float32")
                    break
            if best is None:
                return None
            # 문서가 화면 대부분이면 굳이 펴지 않는다
            if cv2.contourArea(best.astype("int32")) > 0.93 * w * h:
                return None
            ssum = best.sum(axis=1)
            diff = np.diff(best, axis=1).ravel()
            tl, br = best[ssum.argmin()], best[ssum.argmax()]
            tr, bl = best[diff.argmin()], best[diff.argmax()]
            wd = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
            ht = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
            m = cv2.getPerspectiveTransform(
                np.array([tl, tr, br, bl], dtype="float32"),
                np.array([[0, 0], [wd, 0], [wd, ht], [0, ht]], dtype="float32"),
            )
            warped = cv2.warpPerspective(img, m, (wd, ht))
            dest = image_path.parent / f"{image_path.stem}_docarea.png"
            cv2.imwrite(str(dest), warped)
            return dest
        except Exception:
            return None

    @staticmethod
    def _pdf_to_images(file_path: Path, max_pages: int = 4) -> list[tuple[int, Path]]:
        """작은 PDF를 쪽별 PNG로 — 비전 판독용. 조건 밖이거나 실패하면 빈 목록."""
        if file_path.suffix.lower() != ".pdf":
            return []
        try:
            import pypdfium2 as pdfium

            doc = pdfium.PdfDocument(str(file_path))
            try:
                if len(doc) == 0 or len(doc) > max_pages:
                    return []
                out_dir = file_path.parent / f"{file_path.stem}_pages"
                out_dir.mkdir(parents=True, exist_ok=True)
                pages: list[tuple[int, Path]] = []
                for i in range(len(doc)):
                    bmp = doc[i].render(scale=2.0)
                    im = bmp.to_pil()
                    # 비전 모델 요청 한도를 넘지 않게 긴 변 2000px로 제한
                    im.thumbnail((2000, 2000))
                    dest = out_dir / f"page_{i + 1}.jpg"
                    im.convert("RGB").save(dest, quality=88)
                    pages.append((i + 1, dest))
                return pages
            finally:
                doc.close()
        except Exception:
            return []

    @staticmethod
    def _is_meaningful_image(path: Path, seen_hash: set[str]) -> bool:
        """장식 아이콘(작은 그림)과 중복 그림을 걸러낸다."""
        import hashlib

        try:
            digest = hashlib.md5(path.read_bytes()).hexdigest()
            if digest in seen_hash:
                return False
            seen_hash.add(digest)
            from PIL import Image

            with Image.open(path) as im:
                w, h = im.size
            # 한 변 120px 미만이거나 넓이가 아이콘 수준이면 본문 그림이 아니다
            return min(w, h) >= 120 and w * h >= 40000
        except Exception:
            return True  # 판별 실패 시엔 남긴다

    def _mask_str(self, s: str) -> str:
        if self._masker is None:
            self._masker = PiiMasker()
        masked, _ = self._masker.mask(RawDocument(doc_id="chunk", text=s))
        return masked.text

    def _structured_chunks(
        self, do_mask: bool = True, max_chunks: int = 400
    ) -> list[dict] | None:
        """파서 구조(페이지·표)를 유지한 조각 목록 — 표는 병합 셀까지 JSON으로.

        구조 정보가 없으면(텍스트·HWP 경로) None을 주고 문단 분할로 폴백한다.
        """
        import json as _json
        from collections import defaultdict

        parsed = self._last_result
        if parsed is None or not parsed.pages:
            return None
        base_mk = self._mask_str if do_mask else (lambda s: s)
        spacing_needed = getattr(parsed, "parser", "") == "mineru"

        def mk(t: str) -> str:
            if spacing_needed:
                # OCR이 떨어뜨린 어절 공백 복원 후 마스킹 — 원본의 디지털 재구성
                from zzaimy.app.regulations import restore_spacing

                t = restore_spacing(t)
            return base_mk(t)

        entries = getattr(parsed, "entries", None)
        if entries:
            # 파서가 읽기 순서·좌표를 정식으로 넘긴 경우 — 마커 없이 그대로 쓴다
            out2: list[dict] = []
            for e in entries:
                bbox = ",".join(f"{v:.1f}" for v in e.bbox) if e.bbox else None
                if e.kind in ("text", "heading"):
                    if len(e.text) < 2:
                        continue
                    out2.append({
                        "kind": e.kind, "page_no": e.page_no,
                        "content": mk(e.text)[:2000], "bbox": bbox,
                    })
                elif e.kind == "table" and 0 <= e.ref < len(parsed.tables):
                    t = parsed.tables[e.ref]
                    cells = [
                        [c.row, c.col, c.row_span, c.col_span,
                         1 if c.is_header else 0, mk(c.text)]
                        for c in t.cells
                    ]
                    out2.append({
                        "kind": "table", "page_no": e.page_no, "bbox": bbox,
                        "content": _json.dumps(
                            {"n_rows": t.n_rows, "n_cols": t.n_cols, "cells": cells},
                            ensure_ascii=False,
                        ),
                    })
                elif e.kind == "image" and 0 <= e.ref < len(parsed.images):
                    out2.append({
                        "kind": "image", "page_no": e.page_no, "bbox": bbox,
                        "content": parsed.images[e.ref].path.name,
                    })
                if len(out2) >= max_chunks:
                    break
            return out2
        tables_by_page: dict[int, list] = defaultdict(list)
        for t in getattr(parsed, "tables", []):
            tables_by_page[t.page_no].append(t)

        out: list[dict] = []
        for pg in parsed.pages:
            # 파서가 남긴 블록 경계(빈 줄)를 그대로 따른다 — 원본 양식 보존.
            # 경계가 없으면(줄글 파서) 500자 단위로 묶는다
            blocks = [b.strip() for b in pg.text.split("\n\n") if b.strip()]
            if len(blocks) <= 1:
                merged, buf = [], ""
                for line in pg.text.splitlines():
                    ln = line.strip()
                    if not ln:
                        continue
                    buf += ("\n" if buf else "") + ln
                    if len(buf) >= 500:
                        merged.append(buf)
                        buf = ""
                if buf:
                    merged.append(buf)
                blocks = merged
            for blk in blocks:
                if blk.startswith("[[h]]"):
                    out.append({
                        "kind": "heading", "page_no": pg.page_no,
                        "content": mk(blk[5:].strip())[:300],
                    })
                elif blk.startswith("[[img]]"):
                    out.append({
                        "kind": "image", "page_no": pg.page_no,
                        "content": blk[7:].strip(),  # 추출 그림 파일명 (마스킹 불필요)
                    })
                elif len(blk) >= 2:
                    out.append(
                        {"kind": "text", "page_no": pg.page_no, "content": mk(blk)[:2000]}
                    )
            for t in tables_by_page.get(pg.page_no, []):
                cells = [
                    [c.row, c.col, c.row_span, c.col_span,
                     1 if c.is_header else 0, mk(c.text)]
                    for c in t.cells
                ]
                out.append({
                    "kind": "table", "page_no": pg.page_no,
                    "content": _json.dumps(
                        {"n_rows": t.n_rows, "n_cols": t.n_cols, "cells": cells},
                        ensure_ascii=False,
                    ),
                })
            if len(out) >= max_chunks:
                break
        return out[:max_chunks]

    @staticmethod
    def _result_to_text(parsed) -> str:
        text = "\n".join(p.text for p in parsed.pages)
        # 내부 마커는 본문·LLM 입력에 남기지 않는다
        text = re.sub(r"^\[\[img\]\].*$", "[그림]", text, flags=re.M)
        text = text.replace("[[h]]", "")
        for t in parsed.tables:
            # 표마다 빈 줄로 구분해야 조각 저장 시 표 단위로 나뉜다
            text += "\n\n" + "\n".join(
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
            doc = db.get_document(doc_id)
            doc_type = (doc or {}).get("doc_type", "auto")

            if doc_type == "ocr" and file_path.suffix.lower() in (
                ".pdf", ".png", ".jpg", ".jpeg",
            ):
                # 문서 추출 도구 — 명시적 OCR 요청이므로 MinerU를 바로 쓴다
                # (표는 구조로, 그림은 파일로). 실패 시 기본 파서로 진행
                self._last_images = []
                self._last_parse_note = ""
                raw_text = self._parse_mineru(file_path) or self._parse(file_path)
            else:
                raw_text = self._parse(file_path)

            series = classify_series(file_path.name)

            if doc_type == "regulation":
                # 규정 등록 모드 — 판단 근거이지 개인 문서가 아니므로 마스킹하지
                # 않고 원문 그대로 조각화해 규정 저장소에 적재한다.
                # 스캔본·사진도 문서 추출과 같은 고품질 경로(비전·오타 교정)를 탄다
                from zzaimy.app.regulations import split_regulation

                reg_vision_chunks: list[dict] | None = None
                vp = self._pdf_to_images(file_path, max_pages=4)
                if not vp and file_path.suffix.lower() in (".png", ".jpg", ".jpeg"):
                    area = self._crop_document_region(file_path)
                    vp = [(1, area or file_path)]
                if vp:
                    reg_mds: list[str] = []
                    vc: list[dict] = []
                    for pg_no, img_p in vp:
                        md = self._vlm_transcribe(img_p)
                        if md:
                            reg_mds.append(md)
                            vc += self._md_to_chunks(md, lambda x: x, page_no=pg_no)
                    if reg_mds:
                        raw_text = "\n\n".join(reg_mds)
                        reg_vision_chunks = vc
                        self._last_parse_note = "AI 비전 판독 (Qwen3.5)"

                title = (doc or {}).get("filename", f"규정 {doc_id}")
                chunks = split_regulation(raw_text)
                if reg_vision_chunks is None and self._last_parse_note.startswith("스캔"):
                    # MinerU 스캔 경로 — 공백 복원 후 오인식을 보수적으로 교정
                    from zzaimy.app.regulations import restore_spacing

                    chunks = [
                        type(c)(heading=c.heading, content=restore_spacing(c.content))
                        for c in chunks
                    ]
                    fixed = self._correct_texts([c.content for c in chunks])
                    if fixed:
                        chunks = [
                            type(c)(heading=c.heading, content=f[: len(f)])
                            for c, f in zip(chunks, fixed)
                        ]
                        self._last_parse_note += " · AI 오타 교정"
                db.add_regulation_chunks(
                    doc_id, title, chunks, sector=(doc or {}).get("sector", "common")
                )
                db.replace_doc_chunks(
                    doc_id,
                    reg_vision_chunks
                    or self._structured_chunks(do_mask=False)
                    or [
                        {"kind": "text", "content": c.content[:2000]}
                        for c in chunks if len(c.content) >= 2
                    ],
                )
                db.replace_doc_assets(
                    doc_id,
                    [
                        {"kind": "image", "page_no": pg, "path": str(p)}
                        for pg, p in self._last_images
                    ],
                )
                db.update_document(
                    doc_id,
                    status="reviewed",
                    masked_text=raw_text,
                    parse_note=self._last_parse_note or None,
                    series=series.value if series else None,
                    ai_review=(
                        "기준 등록 완료 — 이제 문서 검토와 채팅에서 이 문서가"
                        " 근거로 인용됩니다."
                    ),
                )
                return

            if doc_type == "ocr":
                # 문서 추출 도구 — 검토 없이 파싱·마스킹·구조화 저장까지만
                if self._masker is None:
                    self._masker = PiiMasker()

                parsed_chunks: list[dict] | None = None
                vlm_pages = self._pdf_to_images(file_path, max_pages=4)
                if vlm_pages:
                    # 4쪽 이하 PDF는 페이지째 비전 판독 (오인식이 훨씬 적다)
                    all_chunks: list[dict] = []
                    mds: list[str] = []
                    for pg_no, img_path in vlm_pages:
                        md = self._vlm_transcribe(img_path)
                        if md:
                            mds.append(md)
                            all_chunks += self._md_to_chunks(
                                md, self._mask_str, page_no=pg_no
                            )
                    if all_chunks:
                        parsed_chunks = all_chunks
                        n_t = sum(1 for c in parsed_chunks if c["kind"] == "table")
                        self._last_parse_note = (
                            f"AI 비전 판독 (Qwen3.5) · {len(vlm_pages)}쪽 · 표 {n_t}개"
                        )
                        raw_text = "\n\n".join(mds)
                if parsed_chunks is None and file_path.suffix.lower() in (
                    ".png", ".jpg", ".jpeg",
                ):
                    # 사진은 비전 모델(Qwen3.5) 판독이 우선 — 손글씨·도장 문구까지.
                    # 배경 속 문서(종이) 영역이 따로 있으면 자동으로 찾아 펴서 읽는다
                    doc_area = self._crop_document_region(file_path)
                    vlm_md = self._vlm_transcribe(doc_area or file_path)
                    if vlm_md:
                        parsed_chunks = self._md_to_chunks(vlm_md, self._mask_str)
                        n_t = sum(1 for c in parsed_chunks if c["kind"] == "table")
                        self._last_parse_note = (
                            f"AI 비전 판독 (Qwen3.5) · 표 {n_t}개"
                        )
                        if self._last_attrs:
                            self._last_parse_note += " · " + "·".join(self._last_attrs)
                        raw_text = vlm_md
                    # 빨간 직인(도장) 영역은 별도 이미지로 잘라 보관한다
                    for i, stamp in enumerate(self._extract_stamps(file_path)):
                        self._last_images.append((1, stamp))
                        if self._last_parse_note:
                            self._last_parse_note += " · 직인" if i == 0 else ""

                masked, _ = self._masker.mask(
                    RawDocument(doc_id=str(doc_id), text=raw_text)
                )
                if parsed_chunks is None:
                    parsed_chunks = self._structured_chunks() or _split_chunks(masked.text)
                    if self._llm_correct_chunks(parsed_chunks):
                        self._last_parse_note = (
                            (self._last_parse_note or "일반 파싱") + " · AI 오타 교정"
                        )
                db.replace_doc_chunks(doc_id, parsed_chunks)
                db.replace_doc_assets(
                    doc_id,
                    [
                        {"kind": "image", "page_no": pg, "path": str(p)}
                        for pg, p in self._last_images
                    ],
                )
                n_tables = sum(1 for c in parsed_chunks if c["kind"] == "table")
                db.update_document(
                    doc_id,
                    status="reviewed",
                    masked_text=masked.text,
                    parse_note=self._last_parse_note or None,
                    ai_review=(
                        f"추출 완료 — 본문 {len(parsed_chunks) - n_tables}조각, 표 {n_tables}개,"
                        f" 그림 {len(self._last_images)}장을 옮겼습니다."
                    ),
                )
                return

            # 인풋 문서 — 개인식별 정보가 들어올 수 있으므로 여기서만 마스킹한다
            if self._masker is None:
                self._masker = PiiMasker()
            masked, events = self._masker.mask(
                RawDocument(doc_id=str(doc_id), text=raw_text)
            )
            log.info("doc %d: PII %d건 마스킹", doc_id, len(events))

            # 파싱 결과 DB화 — 구조(페이지·표) 보존 조각, 없으면 문단 분할 (연관성·작성 재료)
            db.replace_doc_chunks(
                doc_id, self._structured_chunks() or _split_chunks(masked.text)
            )
            db.replace_doc_assets(
                doc_id,
                [
                    {"kind": "image", "page_no": pg, "path": str(p)}
                    for pg, p in self._last_images
                ],
            )

            from zzaimy.app.regulations import compose_review_context

            # 프로젝트 소속이면 프로젝트 지침·메모·연결 기준을 검토에 반영한다
            project = None
            if doc is not None and doc.get("project_id"):
                project = db.get_project(int(doc["project_id"]))
            project_criteria = (
                db.get_project_criteria_ids(project["id"]) if project else []
            )

            related_id = (doc or {}).get("related_criteria_id")
            if related_id or project_criteria:
                # 담당자가 지정한 공고·기준(문서 지정 우선, 없으면 프로젝트 연결분)
                use_ids = [int(related_id)] if related_id else project_criteria
                rel_chunks = db.chunks_for_docs(use_ids)
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
                # 연관성 기반 기준 추천 — 어떤 기준과 대조했는지 화면에 보여준다
                import json as _json

                from zzaimy.app.regulations import suggest_criteria_docs

                sugg = suggest_criteria_docs(db, masked.text, sector=doc_type)
                if sugg:
                    db.update_document(
                        doc_id,
                        suggested_criteria=_json.dumps(
                            [{"id": x["doc_id"], "title": x["title"]} for x in sugg],
                            ensure_ascii=False,
                        ),
                    )
            review_input = masked.text
            if reg_context:
                review_input = f"{masked.text}\n\n{reg_context}"
            review_input += _guidance_block(db, project)
            ai_review = self._review(review_input, doc_type)

            db.update_document(
                doc_id,
                status="reviewed",
                masked_text=masked.text,
                series=series.value if series else None,
                ai_review=ai_review,
                parse_note=self._last_parse_note or None,
            )
        except Exception as e:  # 실패도 기록이 남아야 화면에서 보인다
            log.exception("doc %d 처리 실패", doc_id)
            db.update_document(doc_id, status="failed", error=f"{type(e).__name__}: {e}")

    _ANALYZE_PROMPT = """다음은 문서에서 추출한 내용이다(제목·문단·표 순서 유지, 개인정보 마스킹됨).
행정 담당자를 위해 이 문서의 맥락을 분석하라.

문서 유형: (공문/공고/계획서/증명서/서식/기타 — 근거와 함께)
핵심 정보: (기관명, 날짜, 문서번호, 금액, 대상 등 — 추출 내용에 있는 것만)
표 요약: (표가 있으면 각 표가 무엇을 담는지 한 줄씩)
맥락 정리: (이 문서가 어떤 업무 흐름의 무엇인지 2~3문장)
활용 제안: (검토 기준으로 등록/특정 업무 접수/참고자료 중 무엇에 적합한지)

추출 내용에 없는 것은 추정하지 말고 "확인 필요"로 표시하라.

추출 내용:
{text}"""

    _SUMMARY_PROMPT = """다음은 기준 문서(규정·지침·공고문)에서 추출한 내용이다.
행정 담당자를 위해 요약하라.

문서 성격: (규정/지침/매뉴얼/공고문 — 한 줄)
무엇을 다루나: (2~3문장)
적용 대상·범위: (내용에 있는 것만)
핵심 조항·항목: (조항 번호나 항목명과 함께 5개 이내)
검토 활용 포인트: (문서 검토 시 이 기준에서 주로 대조하게 될 것 2~3개)

내용에 없는 것은 추정하지 말고 "확인 필요"로 표시하라.

추출 내용:
{text}"""

    def analyze(self, db: Database, doc_id: int) -> None:
        """추출된 조각을 근거로 문서 맥락 분석/요약 — OCR 도구·기준 문서 공용."""
        doc = db.get_document(doc_id)
        if doc is None:
            return
        try:
            chunks = db.list_doc_chunks(doc_id)
            parts: list[str] = []
            budget = 8000
            for c in chunks:
                if c["kind"] == "heading":
                    piece = f"\n## {c['content']}"
                elif c["kind"] == "table":
                    piece = f"\n[표] {c['content'][:800]}"
                elif c["kind"] == "image":
                    piece = "\n[그림]"
                else:
                    piece = f"\n{c['content']}"
                if budget - len(piece) < 0:
                    break
                budget -= len(piece)
                parts.append(piece)
            text = "".join(parts) or (doc.get("masked_text") or "")[:8000]

            from zzaimy.generate.client import VllmClient

            prompt = (
                self._SUMMARY_PROMPT
                if doc.get("doc_type") == "regulation"
                else self._ANALYZE_PROMPT
            )
            client = VllmClient()
            resp = client.client.chat.completions.create(
                model=client.model,
                temperature=0.2,
                max_tokens=1200,
                messages=[{
                    "role": "user",
                    "content": prompt.format(text=text),
                }],
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            analysis = (resp.choices[0].message.content or "").strip()
            db.update_document(doc_id, ai_review=analysis, coverage=None)
        except Exception as e:
            log.exception("doc %d 맥락 분석 실패", doc_id)
            db.update_document(
                doc_id, coverage=f"맥락 분석 실패: {type(e).__name__}"
            )

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
            project = (
                db.get_project(int(doc["project_id"])) if doc.get("project_id") else None
            )
            text += _guidance_block(db, project)
            ai_review = self._review(text, doc.get("doc_type", "auto"))
            # 재검토 완료 → 다시 판정 대기 상태로
            db.update_document(doc_id, status="reviewed", ai_review=ai_review, decision="pending")
        except Exception as e:
            log.exception("doc %d 재검토 실패", doc_id)
            db.update_document(doc_id, status="failed", error=f"{type(e).__name__}: {e}")
