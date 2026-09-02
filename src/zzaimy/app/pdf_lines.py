"""디지털 PDF 정밀 줄 추출 — 글자 좌표·색·굵기를 원본에서 그대로 읽는다.

블록 bbox 사각형으로 텍스트를 떠오면 경계 글자가 잘리거나 겹치는 블록이
중복된다. 여기서는 글자 하나하나의 실제 좌표(charbox)를 읽어 줄 단위로
묶으므로 잘림·중복·위치 오차가 원리적으로 없다 — "SVG처럼 요소 그대로".

복원 뷰(원본 배치)와 검색 가능 PDF가 이 결과를 쓴다. 스캔 문서에는
텍스트 레이어가 없으므로 적용되지 않는다 (그쪽은 OCR 경로).
"""

from __future__ import annotations

import ctypes
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# 같은 줄 판정: 베이스라인 차이가 글자 높이의 이 비율 이내
_LINE_TOL = 0.5


def _char_style(tp_raw, i: int) -> tuple[tuple[int, int, int] | None, bool]:
    """글자 i의 채움색과 굵기. 실패하면 (None, False)."""
    import pypdfium2.raw as pdfium_c

    color = None
    bold = False
    try:
        r = ctypes.c_uint()
        g = ctypes.c_uint()
        b = ctypes.c_uint()
        a = ctypes.c_uint()
        if pdfium_c.FPDFText_GetFillColor(
            tp_raw, i, ctypes.byref(r), ctypes.byref(g), ctypes.byref(b), ctypes.byref(a)
        ):
            color = (int(r.value), int(g.value), int(b.value))
    except Exception:
        pass
    try:
        w = pdfium_c.FPDFText_GetFontWeight(tp_raw, i)
        bold = w >= 600
    except Exception:
        pass
    if not bold:
        # HWP 계열 PDF는 weight 대신 폰트 이름·ForceBold 플래그로 굵기를 남긴다
        try:
            buf = ctypes.create_string_buffer(128)
            flags = ctypes.c_int()
            n = pdfium_c.FPDFText_GetFontInfo(
                tp_raw, i, buf, 128, ctypes.byref(flags)
            )
            name = buf.raw[: max(int(n) - 1, 0)].decode("utf-8", errors="ignore")
            bold = bool(flags.value & (1 << 18)) or "bold" in name.lower()
        except Exception:
            pass
    return color, bold


def pdf_line_boxes(
    file_path: Path, max_pages: int = 120
) -> dict[int, list[dict]]:
    """페이지별 줄 상자 목록. 각 항목: content, bbox(top 기준), color, bold, size."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(file_path))
    pages: dict[int, list[dict]] = {}
    try:
        for pi in range(min(len(doc), max_pages)):
            page = doc[pi]
            ph = page.get_height()
            tp = page.get_textpage()
            try:
                n = tp.count_chars()
                runs: list[dict] = []
                cur: dict | None = None

                def flush() -> None:
                    nonlocal cur
                    if cur and cur["text"].strip() and cur["x1"] > cur["x0"]:
                        runs.append(cur)
                    cur = None

                for i in range(n):
                    ch = tp.get_text_range(i, 1)
                    if ch in ("\r", "\n", ""):
                        flush()
                        continue
                    try:
                        left, bottom, right, top = tp.get_charbox(i)
                    except Exception:
                        continue
                    if right - left <= 0 and not ch.isspace():
                        continue
                    color, bold = _char_style(tp.raw, i)
                    same = (
                        cur is not None
                        and abs(bottom - cur["b"]) <= _LINE_TOL * max(top - bottom, cur["h"], 1)
                        and cur["color"] == color
                        and cur["bold"] == bold
                        and left >= cur["x0"] - 2
                    )
                    if same and cur is not None:
                        cur["text"] += ch
                        if not ch.isspace():
                            cur["x0"] = min(cur["x0"], left)
                            cur["x1"] = max(cur["x1"], right)
                            cur["t"] = max(cur["t"], top)
                            cur["b"] = min(cur["b"], bottom)
                            cur["h"] = max(cur["h"], top - bottom)
                    else:
                        flush()
                        if ch.isspace():
                            continue
                        cur = {
                            "text": ch, "x0": left, "x1": right,
                            "b": bottom, "t": top, "h": top - bottom,
                            "color": color, "bold": bold,
                        }
                flush()

                # 조각을 병합하지 않는다 — 2단 목차·표에서 서로 다른 단을
                # 같은 줄로 이어붙이는 사고가 났다. 각 조각은 원본 좌표 그대로
                # 두고, 렌더 단계에서 글자 간격으로 자기 박스 폭만 채운다
                runs.sort(key=lambda r: (-round(r["b"]), r["x0"]))

                items = []
                for rn in runs:
                    items.append({
                        "kind": "text",
                        "content": rn["text"].rstrip(),
                        "bbox": f"{rn['x0']:.1f},{ph - rn['t']:.1f},"
                                f"{rn['x1']:.1f},{ph - rn['b']:.1f}",
                        "page_no": pi + 1,
                        "color": rn["color"],
                        "bold": rn["bold"],
                        "justify": True,  # 렌더에서 글자 간격으로 박스 폭을 채운다
                    })
                if items:
                    pages[pi + 1] = items
            finally:
                tp.close()
    except Exception:
        log.warning("정밀 줄 추출 실패", exc_info=True)
        return {}
    finally:
        doc.close()
    return pages
