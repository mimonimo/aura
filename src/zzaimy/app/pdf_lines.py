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


def scale_ocr_lines(
    payload: dict, page_sizes: dict[int, tuple[float, float]]
) -> list[dict]:
    """스캔 OCR 줄 좌표(middle.json 좌표계)를 PDF 포인트 좌표계로 변환한다.

    payload는 파이프라인이 저장한 {"page_sizes": {"1": [w,h]}, "lines": [...]}.
    실제 페이지 치수를 모르는 쪽의 줄은 버린다 (좌표를 보정할 수 없다).
    """
    sizes = {int(k): v for k, v in (payload.get("page_sizes") or {}).items()}
    out: list[dict] = []
    for ln in payload.get("lines") or []:
        pg = int(ln.get("page_no") or 0)
        if pg not in sizes or pg not in page_sizes:
            continue
        mw, mh = float(sizes[pg][0]), float(sizes[pg][1])
        pw, ph = page_sizes[pg]
        if mw <= 0 or mh <= 0:
            continue
        try:
            x0, y0, x1, y1 = (float(v) for v in str(ln["bbox"]).split(","))
        except (KeyError, ValueError):
            continue
        fx, fy = pw / mw, ph / mh
        out.append({
            **ln,
            "bbox": f"{x0 * fx:.1f},{y0 * fy:.1f},{x1 * fx:.1f},{y1 * fy:.1f}",
            "justify": True,
        })
    return out


def image_layout_from_lines(
    payload: dict,
) -> tuple[list[dict], dict[int, tuple[float, float]]]:
    """사진 문서 — 줄 좌표 payload를 배치 항목·페이지 치수로 변환.

    사진은 좌표계 변환이 필요 없다(배경 스캔본과 같은 픽셀 공간).
    """
    sizes = {
        int(k): (float(v[0]), float(v[1]))
        for k, v in (payload.get("page_sizes") or {}).items()
        if isinstance(v, (list, tuple)) and len(v) == 2
    }
    items = [
        {**ln, "justify": True}
        for ln in payload.get("lines") or []
        if int(ln.get("page_no") or 0) in sizes
    ]
    return items, sizes


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


def char_boxes(textpage) -> list[tuple[float, float, float, float]]:
    """글자층의 글자 상자 (left, bottom, right, top) — PDF 좌표. 공백·줄바꿈은 뺀다."""
    out = []
    for i in range(textpage.count_chars()):
        ch = textpage.get_text_range(i, 1)
        if not ch.strip():
            continue
        out.append(textpage.get_charbox(i))
    return out


def complete_line_rect(
    boxes: list[tuple[float, float, float, float]],
    rect: tuple[float, float, float, float],
    gap_ratio: float = 1.6,
) -> tuple[float, float, float, float]:
    """블록 상자에 걸친 줄을 끝까지 잇도록 상자를 좌우로 넓힌다.

    구조 추출기의 상자가 줄보다 좁으면 상자 안 글자만 떠서 줄 끝이 잘린다
    (실측 2026-09-20: '학과장회 통과일자 : 2022년…' → '학과장회 통'). 상자 안 글자와
    같은 줄에 있고 글자 간격이 글자 폭의 gap_ratio 배 안으로 이어지는 글자만 붙인다 —
    2단 편집의 단 사이처럼 넓은 틈은 넘지 않는다.
    rect 와 반환값은 (left, bottom, right, top).
    """
    L, B, R, T = rect
    inside = [b for b in boxes
              if L <= (b[0] + b[2]) / 2 <= R and B <= (b[1] + b[3]) / 2 <= T]
    if not inside:
        return rect
    nl, nr = L, R
    for seed in inside:
        cy = (seed[1] + seed[3]) / 2
        h = max(seed[3] - seed[1], 1.0)
        line = sorted((b for b in boxes if abs((b[1] + b[3]) / 2 - cy) <= h * 0.45),
                      key=lambda b: b[0])
        if not line:
            continue
        widths = sorted(b[2] - b[0] for b in line)
        gap_max = max(widths[len(widths) // 2], 1.0) * gap_ratio
        idx = next(i for i, b in enumerate(line) if b == seed)
        j = idx
        while j + 1 < len(line) and line[j + 1][0] - line[j][2] <= gap_max:
            j += 1
        k = idx
        while k - 1 >= 0 and line[k][0] - line[k - 1][2] <= gap_max:
            k -= 1
        nl, nr = min(nl, line[k][0]), max(nr, line[j][2])
    return (nl, B, nr, T)


def _bigrams(s: str) -> set[str]:
    t = "".join((s or "").split())
    return {t[i:i + 2] for i in range(len(t) - 1)}


def page_bbox_mappers(entries, textpage_of, sizes: dict, fits: dict) -> dict:
    """페이지별 좌표 변환 — 구조 추출기 bbox → PDF 좌표(왼쪽 위 원점, pt).

    MinerU 는 버전에 따라 bbox 를 렌더 픽셀로도, 가로·세로 각각 0~1000 정규화로도 준다.
    배율 하나로 나누면 정규화 좌표에서 세로가 어긋나 한 줄 위 글자를 떠 온다(실측 2026-09-20:
    '학과장회 통과일자 …' 칸에 윗줄 '무분장 규정'). 두 변환을 모두 해 보고, 원문 글자층에서
    뜬 글이 추출기 자신이 읽은 글과 더 닮은 쪽을 그 페이지에 쓴다. 동률이면 기존(배율) 방식.
    textpage_of(page_no) 는 pypdfium2 textpage 를 돌려준다.
    """
    by_page: dict[int, list] = {}
    for e in entries:
        if e.bbox and e.page_no in sizes:
            by_page.setdefault(e.page_no, []).append(e)
    out = {}
    for pg, ents in by_page.items():
        pw, ph = sizes[pg]
        fit = max(fits.get(pg, 1.0), 1.0)
        cands = {"fit": (lambda b, f=fit: tuple(v / f for v in b))}
        if all(max(e.bbox) <= 1000 for e in ents):
            cands["norm"] = (lambda b, w=pw, h=ph: (b[0] * w / 1000, b[1] * h / 1000,
                                                    b[2] * w / 1000, b[3] * h / 1000))
        if len(cands) == 1:
            out[pg] = cands["fit"]
            continue
        samples = [e for e in ents if e.kind in ("text", "heading") and len((e.text or "").strip()) >= 4][:12]
        tp = textpage_of(pg)
        best, best_score = "fit", -1.0
        for name, fn in cands.items():
            sc = 0.0
            for e in samples:
                x0, y0, x1, y1 = fn(e.bbox)
                got = tp.get_text_bounded(left=x0, bottom=ph - y1, right=x1, top=ph - y0) or ""
                want = _bigrams(e.text)
                sc += len(want & _bigrams(got)) / len(want) if want else 0.0
            if sc > best_score + 1e-9:
                best, best_score = name, sc
        out[pg] = cands[best]
    return out
