"""검색 가능한 PDF 생성 — 원본 PDF 위에 보이지 않는 OCR 텍스트 레이어를 입힌다.

스캐너 앱·ocrmypdf와 같은 원리지만, 텍스트는 우리 파이프라인(MinerU·비전 판독)의
인식 결과를 쓴다. 좌표(bbox)가 있는 조각은 원본 위치에, 없는 조각은 페이지
여백에 보이지 않게 깔린다 — 어느 쪽이든 선택·검색이 된다.
"""

from __future__ import annotations

import io
import json
from collections import defaultdict
from pathlib import Path

_FONT = "HYSMyeongJo-Medium"  # reportlab 내장 한국어 CID 폰트 (파일 불필요)


def _register_font() -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    try:
        pdfmetrics.getFont(_FONT)
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont(_FONT))


def _chunk_text(c: dict) -> str:
    if c["kind"] == "table":
        try:
            data = json.loads(c["content"])
            return "\n".join(
                " ".join(str(cell[5]) for cell in data["cells"] if int(cell[0]) == r)
                for r in range(int(data["n_rows"]))
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return c["content"]
    if c["kind"] == "image":
        return ""
    return c["content"]


def build_searchable_pdf(original: Path, chunks: list[dict]) -> bytes | None:
    """원본 PDF + 보이지 않는 텍스트 레이어. 실패하면 None (원본은 건드리지 않는다)."""
    try:
        from pypdf import PdfReader, PdfWriter
        from reportlab.pdfgen import canvas as rl_canvas

        _register_font()
        reader = PdfReader(str(original))
        writer = PdfWriter()

        by_page: dict[int, list[dict]] = defaultdict(list)
        for c in chunks:
            by_page[int(c.get("page_no") or 1)].append(c)

        # bbox 좌표계 추정: 페이지별 bbox 최대치가 페이지 크기를 넘으면 비율 보정
        for idx, page in enumerate(reader.pages, start=1):
            pw = float(page.mediabox.width)
            ph = float(page.mediabox.height)
            items = by_page.get(idx, [])

            buf = io.BytesIO()
            cv = rl_canvas.Canvas(buf, pagesize=(pw, ph))
            xs = [
                float(c["bbox"].split(",")[2])
                for c in items if c.get("bbox")
            ]
            ys = [
                float(c["bbox"].split(",")[3])
                for c in items if c.get("bbox")
            ]
            # 렌더 픽셀 좌표계면 균일 축소 (가로·세로 따로 늘리지 않는다)
            fit = max(
                (max(xs) / pw) if xs else 1.0,
                (max(ys) / ph) if ys else 1.0,
                1.0,
            )
            sx = sy = (1.0 / fit) if fit > 1.08 else 1.0

            margin_y = ph - 20
            for c in items:
                text = _chunk_text(c)
                if not text.strip():
                    continue
                lines = [ln for ln in text.splitlines() if ln.strip()]
                if c.get("bbox"):
                    x0, y0, x1, y1 = (float(v) for v in c["bbox"].split(","))
                    x0, x1 = x0 * sx, x1 * sx
                    y0, y1 = y0 * sy, y1 * sy
                    box_h = max(y1 - y0, 6.0)
                    size = max(4.0, min(14.0, box_h / max(len(lines), 1) * 0.82))
                    top = ph - y0  # PDF는 원점이 왼쪽 아래
                else:
                    x0, size, top = 24.0, 4.0, margin_y
                    margin_y -= size * (len(lines) + 1)
                t = cv.beginText()
                t.setTextRenderMode(3)  # 보이지 않는 텍스트 — 검색·선택 전용
                t.setFont(_FONT, size)
                t.setTextOrigin(x0, top - size)
                for ln in lines:
                    t.textLine(ln)
                cv.drawText(t)
            cv.showPage()
            cv.save()
            buf.seek(0)
            overlay = PdfReader(buf).pages[0]
            page.merge_page(overlay)
            writer.add_page(page)

        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception:
        return None


def _enhance_page(img):
    """스캔 페이지 화질 보정 — 대비(CLAHE)·선명도. 실패하면 원본 그대로."""
    try:
        import cv2
        import numpy as np

        arr = np.array(img.convert("RGB"))[:, :, ::-1]
        lab = cv2.cvtColor(arr, cv2.COLOR_BGR2LAB)
        lab[:, :, 0] = cv2.createCLAHE(
            clipLimit=2.0, tileGridSize=(8, 8)
        ).apply(lab[:, :, 0])
        arr = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
        blur = cv2.GaussianBlur(arr, (0, 0), 1.2)
        arr = cv2.addWeighted(arr, 1.35, blur, -0.35, 0)
        from PIL import Image

        return Image.fromarray(arr[:, :, ::-1])
    except Exception:
        return img


def build_restored_scan_pdf(
    original: Path,
    lines: list[dict],
    page_sizes: dict[int, tuple[float, float]],
    enhance: bool = True,
    scale: float = 2.0,
) -> bytes | None:
    """스캔 PDF → 보정(업스케일) 페이지 이미지 + 투명 텍스트 레이어.

    크롬 내장 뷰어에서 원본 모습 그대로 보이면서 드래그 복사·검색이 된다.
    좌표는 PDF 포인트 기준의 줄 항목(scale_ocr_lines 결과)을 쓴다.
    """
    try:
        from collections import defaultdict

        import pypdfium2 as pdfium
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas as rl_canvas

        _register_font()
        by_page: dict[int, list[dict]] = defaultdict(list)
        for ln in lines:
            by_page[int(ln.get("page_no") or 1)].append(ln)

        doc = pdfium.PdfDocument(str(original))
        buf = io.BytesIO()
        cv = None
        try:
            for i in range(len(doc)):
                page = doc[i]
                pw, ph = page.get_width(), page.get_height()
                if cv is None:
                    cv = rl_canvas.Canvas(buf, pagesize=(pw, ph))
                else:
                    cv.setPageSize((pw, ph))
                # 페이지 폭 1300px 상한 — 과대 해상도는 뷰어 스크롤을 끊기게 한다
                eff_scale = min(scale, 1300.0 / max(pw, 1.0))
                img = page.render(scale=eff_scale).to_pil()
                if enhance:
                    img = _enhance_page(img)
                jpg = io.BytesIO()  # 무손실 임베드는 페이지당 2MB+ — JPEG로 압축
                img.convert("RGB").save(jpg, format="JPEG", quality=78)
                jpg.seek(0)
                cv.drawImage(
                    ImageReader(jpg), 0, 0, width=pw, height=ph
                )
                for ln in by_page.get(i + 1, []):
                    text = str(ln.get("content") or "").strip()
                    if not text:
                        continue
                    try:
                        x0, y0, x1, y1 = (
                            float(v) for v in str(ln["bbox"]).split(",")
                        )
                    except (KeyError, ValueError):
                        continue
                    size = max(4.0, min(18.0, (y1 - y0) * 0.82))
                    t = cv.beginText()
                    t.setTextRenderMode(3)  # 보이지 않는 텍스트
                    t.setFont(_FONT, size)
                    t.setTextOrigin(x0, ph - y1 + (y1 - y0 - size) / 2)
                    t.textLine(text)
                    cv.drawText(t)
                cv.showPage()
            if cv is not None:
                cv.save()
        finally:
            doc.close()
        return buf.getvalue() if cv is not None else None
    except Exception:
        return None


def build_restored_photo_pdf(
    image_path: Path, lines_payload: dict, full_text: str | None = None
) -> bytes | None:
    """사진 → 한 장짜리 복원 PDF (보정 스캔 + 위치 맞춘 투명 레이어).

    손글씨처럼 좌표 OCR이 몇 줄밖에 못 잡는 사진은 비전 판독 전문(full_text)을
    여백 투명 레이어로 함께 깔아 전체 복사·검색이 되게 한다.
    """
    try:
        from PIL import Image
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas as rl_canvas

        _register_font()
        with Image.open(image_path) as im:
            w, h = im.size
        s = min(595.0 / w, 842.0 / h)
        pw, ph = w * s, h * s
        # 줄 좌표는 middle.json의 page_size 좌표계 — 이미지와 다르면 따로 보정
        ps = (lines_payload.get("page_sizes") or {}).get("1")
        if ps and float(ps[0]) > 0 and float(ps[1]) > 0:
            sx, sy = pw / float(ps[0]), ph / float(ps[1])
        else:
            sx = sy = s

        buf = io.BytesIO()
        cv = rl_canvas.Canvas(buf, pagesize=(pw, ph))
        cv.drawImage(ImageReader(str(image_path)), 0, 0, width=pw, height=ph)
        for ln in lines_payload.get("lines") or []:
            text = str(ln.get("content") or "").strip()
            if not text:
                continue
            try:
                x0, y0, x1, y1 = (float(v) for v in str(ln["bbox"]).split(","))
            except (KeyError, ValueError):
                continue
            x0, y0, x1, y1 = x0 * sx, y0 * sy, x1 * sx, y1 * sy
            size = max(4.0, min(18.0, (y1 - y0) * 0.82))
            t = cv.beginText()
            t.setTextRenderMode(3)
            t.setFont(_FONT, size)
            t.setTextOrigin(x0, ph - y1 + (y1 - y0 - size) / 2)
            t.textLine(text)
            cv.drawText(t)
        covered = sum(
            len(str(ln.get("content") or "")) for ln in lines_payload.get("lines") or []
        )
        if full_text and len(full_text.strip()) > covered * 2:
            # 좌표 레이어가 빈약하면 전문을 여백에 깔아 전체 복사·검색 보장
            t = cv.beginText()
            t.setTextRenderMode(3)
            t.setFont(_FONT, 4)
            t.setTextOrigin(4, ph - 8)
            for line in full_text.splitlines():
                if line.strip():
                    t.textLine(line[:200])
            cv.drawText(t)
        cv.showPage()
        cv.save()
        return buf.getvalue()
    except Exception:
        return None


def build_scan_pdf(image_path: Path, chunks: list[dict]) -> bytes | None:
    """사진·스캔 이미지 → 한 장짜리 검색 가능한 PDF (보정 스캔본 + 텍스트 레이어)."""
    try:
        from PIL import Image
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas as rl_canvas

        _register_font()
        with Image.open(image_path) as im:
            w, h = im.size
        scale = min(595.0 / w, 842.0 / h)  # A4 안에 맞춘다
        pw, ph = w * scale, h * scale

        buf = io.BytesIO()
        cv = rl_canvas.Canvas(buf, pagesize=(pw, ph))
        cv.drawImage(ImageReader(str(image_path)), 0, 0, width=pw, height=ph)
        t = cv.beginText()
        t.setTextRenderMode(3)
        t.setFont(_FONT, 6)
        t.setTextOrigin(10, ph - 16)
        for c in chunks:
            for ln in _chunk_text(c).splitlines():
                if ln.strip():
                    t.textLine(ln)
        cv.drawText(t)
        cv.showPage()
        cv.save()
        return buf.getvalue()
    except Exception:
        return None
