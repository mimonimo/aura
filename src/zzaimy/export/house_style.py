"""학교 표 서식의 기준값 — 한글 작업본(app.webhwp_style)과 구글 독스 작업본(ingest.gdocs)이 같은 값을 읽는다.

값은 학교 서식·문서 1,459건의 표 20,441개에서 캔 것이다. 머리줄·라벨 칸은 옅은 회색 음영(#E5E5E5)·가운데 정렬,
머리줄 글자는 굵게, 선은 바깥 0.4mm·안쪽 0.12mm 실선, 글자 10pt, 칸 여백 좌우 510·위아래 141(HWPUNIT), 행 최소 높이 1,900.
한 칸 상자(【작성방법】·※ 안내)는 안쪽 굵기 점선, 1pt 작은 회색 글. 도식은 표 칸 상자와 화살표 칸으로 그린다.

한글 쪽은 HWPUNIT·'0.4 mm' 글을 그대로 쓰고, 독스 쪽은 아래 변환(pt·rgb 0..1)을 거친다.
"""
from __future__ import annotations

import re

HOUSE = {"head_fill": "#E5E5E5", "label_fill": "#E5E5E5", "outer": "0.4 mm", "inner": "0.12 mm",
         "head_bottom": "SOLID", "font_pt": 10.0, "head_bold": True, "head_align": "CENTER", "body_align": ""}
CELL_MARGIN = {"left": 510, "right": 510, "top": 141, "bottom": 141}   # HWPUNIT — 좌우 약 1.8mm, 위아래 약 0.5mm
MIN_ROW_HEIGHT = 1900                                                   # HWPUNIT — 10pt 한 줄 + 여백(약 6.7mm)
BOX = {"dash": "DASH", "font_delta": -1.0, "color": "#555555", "align": "LEFT"}   # 한 칸 안내 상자

# 도식 — 첫 줄(상위·출발) 상자는 연파랑, 나머지는 연회색. 상자 칸:화살표 칸 폭 = 4:1
DIAGRAM_FILLS = ("#DFEAF5", "#F2F2F2")
DIAGRAM_BORDER = "0.12 mm"
DIAGRAM_WEIGHTS = (4, 1)
DIAGRAM_BOX_HEIGHT = 1800                                               # HWPUNIT
DIAGRAM_ARROW_HEIGHT = 900
DIAGRAM_MARGIN = 283                                                    # HWPUNIT(약 1mm) 사방
DIAGRAM_MAX_LINES = 12
DIAGRAM_FENCE = re.compile(r"^```\s*(도식|diagram|flow)")


def short_value(t: str) -> bool:
    """가운데에 둘 값 — 기호(●○◎-), 숫자·금액·비율·날짜, 여섯 글자 이하."""
    t = (t or "").strip()
    return bool(t) and (len(t) <= 6 or bool(re.fullmatch(r"[\d\s,.%~\-–()/:·년월일천백만억원명개건회점]+", t)))


def cell_roles(cells: list[dict]) -> dict[tuple[int, int], str]:
    """칸 목록({r, c, rs, cs, text, field}) → (행, 열) → 'head'·'label'·'body'·'box'.
    머리줄=첫 행이 모두 글이고 아래 행이 있을 때, 라벨=글 있는 칸 바로 오른쪽이 빈 칸·필드."""
    if not cells:
        return {}
    nrows = max(c["r"] + c["rs"] for c in cells)
    ncols = max(c["c"] + c["cs"] for c in cells)
    if ncols == 1:
        return {(c["r"], c["c"]): "box" for c in cells}
    at = {(c["r"], c["c"]): c for c in cells}
    first = [c for c in cells if c["r"] == 0]
    head_rows = 0
    if nrows >= 2 and first and all(c["text"] and not c["field"] for c in first) and len(first) >= 2:
        head_rows = max(c["rs"] for c in first)
        # 머리줄이 두 줄(합친 칸 아래 세부 머리)인 표 — 둘째 줄도 모두 글이고 그 아래 빈 줄이 있으면
        second = [c for c in cells if c["r"] == head_rows]
        if (head_rows == 1 and nrows >= 3 and second and all(c["text"] and not c["field"] for c in second)
                and any(c["cs"] > 1 for c in first) and len(second) < ncols):
            head_rows = 2
    out = {}
    for c in cells:
        if c["r"] < head_rows:
            out[(c["r"], c["c"])] = "head"
            continue
        right = at.get((c["r"], c["c"] + c["cs"]))
        below = at.get((c["r"] + c["rs"], c["c"]))
        is_label = bool(c["text"]) and not c["field"] and len(c["text"]) <= 20 and (
            (right is not None and (not right["text"] or right["field"]))
            or (c["c"] == 0 and not head_rows and ncols >= 2)
            # 표 안 소머리(아래 칸이 비었거나 필드이고 오른쪽도 글인 칸 — 「국비 | 지방비 | 대응자금」 줄)
            or (below is not None and (not below["text"] or below["field"]) and c["c"] > 0))
        out[(c["r"], c["c"])] = "label" if is_label else "body"
    return out


def grid_cells(rows: list[list[str]]) -> list[dict]:
    """글 격자(병합 없음) → cell_roles 가 받는 칸 목록."""
    n = max((len(r) for r in rows), default=0)
    return [{"r": ri, "c": ci, "rs": 1, "cs": 1, "field": False,
             "text": " ".join(str(r[ci]).split()) if ci < len(r) else ""}
            for ri, r in enumerate(rows) for ci in range(n)]


def diagram_lines(text: str) -> list[list[str]]:
    """```도식 블록 글 → 상자 줄 목록. 줄마다 「상자 → 상자」, 화살표만 있는 줄(↓)은 건너뛴다."""
    out = []
    for ln in str(text or "").split("\n"):
        boxes = [b.strip() for b in re.split(r"\s*(?:→|->|⇒|=>)\s*", ln.strip()) if b.strip()]
        if boxes and not re.fullmatch(r"[↓|]+", ln.strip()):
            out.append(boxes)
    return out[:DIAGRAM_MAX_LINES]


def diagram_grid(lines: list[list[str]]) -> dict:
    """상자 줄 → 도식 표의 격자. 열 = 상자 칸·화살표 칸 번갈아(2n-1), 줄 사이에 아래 화살표 행.
    rows 는 행마다 칸 목록 [{c, span, text, kind: box|arrow|blank, bold}], kind 가 box 인 칸만 선·음영이 있다."""
    n = max((len(b) for b in lines), default=1)
    cols = max(2 * n - 1, 1)
    rows: list[dict] = []
    for i, boxes in enumerate(lines):
        if i:
            rows.append({"kind": "arrow", "height": DIAGRAM_ARROW_HEIGHT,
                         "cells": [{"c": 0, "span": cols, "text": "↓", "kind": "arrow", "bold": False}]})
        fill = DIAGRAM_FILLS[0] if i == 0 else DIAGRAM_FILLS[1]
        cells: list[dict] = []
        if len(boxes) == 1:
            cells.append({"c": 0, "span": cols, "text": boxes[0], "kind": "box", "bold": True, "fill": fill})
        else:
            k = len(boxes)
            lead = n - k                                     # 상자 k 개를 가운데에 — 양옆 빈 칸(선 없음)
            c = 0
            if lead:
                cells.append({"c": 0, "span": lead, "text": "", "kind": "blank", "bold": False})
                c = lead
            for j, b in enumerate(boxes):
                cells.append({"c": c, "span": 1, "text": b, "kind": "box", "bold": i == 0, "fill": fill})
                c += 1
                if j < k - 1:
                    cells.append({"c": c, "span": 1, "text": "→", "kind": "arrow", "bold": False})
                    c += 1
            if c < cols:
                cells.append({"c": c, "span": cols - c, "text": "", "kind": "blank", "bold": False})
        rows.append({"kind": "boxes", "height": DIAGRAM_BOX_HEIGHT, "cells": cells})
    weights = [DIAGRAM_WEIGHTS[0] if c % 2 == 0 else DIAGRAM_WEIGHTS[1] for c in range(cols)]
    return {"cols": cols, "rows": rows, "weights": weights}


def split_diagram_blocks(text: str) -> list[tuple[str, str]]:
    """글 → [("text"|"diagram", 글)] — ```도식 … ``` 블록만 떼어 낸다(닫는 ``` 가 없으면 끝까지 도식)."""
    out: list[tuple[str, str]] = []
    buf: list[str] = []
    fence: list[str] | None = None
    for ln in str(text or "").replace("\r", "").split("\n"):
        s = ln.strip()
        if fence is not None:
            if s.startswith("```"):
                out.append(("diagram", "\n".join(fence)))
                fence = None
            else:
                fence.append(ln)
            continue
        if DIAGRAM_FENCE.match(s):
            if "\n".join(buf).strip():
                out.append(("text", "\n".join(buf).strip("\n")))
            buf, fence = [], []
            continue
        buf.append(ln)
    if fence:
        out.append(("diagram", "\n".join(fence)))
    if "\n".join(buf).strip():
        out.append(("text", "\n".join(buf).strip("\n")))
    return out


# ── 독스 단위 변환 ── 독스 API 는 길이를 pt, 색을 rgb 0..1 로 받는다
def mm(w: str | float) -> float:
    """'0.4 mm'·0.4 → 0.4"""
    try:
        return float(re.sub(r"[^\d.]", "", str(w)) or 0)
    except ValueError:
        return 0.0


def mm_to_pt(w: str | float) -> float:
    return round(mm(w) * 72 / 25.4, 3)


def hwpunit_to_pt(v: int | float) -> float:
    """HWPUNIT(1/7200 인치) → pt(1/72 인치) — 100 으로 나눈다."""
    return round(float(v) / 100, 3)


def rgb(hex_color: str) -> dict | None:
    """'#E5E5E5' → {'red': 0.898, …}. 'none'·빈 값이면 None."""
    m = re.fullmatch(r"#?([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})", str(hex_color or "").strip())
    if not m:
        return None
    r, g, b = (round(int(x, 16) / 255, 3) for x in m.groups())
    return {"red": r, "green": g, "blue": b}


def docs_house(opts: dict | None = None) -> dict:
    """독스 표 서식 값(pt·rgb) — HOUSE(와 덮을 값)에서 바꿔 만든다."""
    o = {**HOUSE, **(opts or {})}
    return {"head_rgb": rgb(o["head_fill"]), "label_rgb": rgb(o["label_fill"]),
            "outer_pt": mm_to_pt(o["outer"]), "inner_pt": mm_to_pt(o["inner"]),
            "font_pt": float(o["font_pt"]), "box_font_pt": float(o["font_pt"]) + BOX["font_delta"],
            "box_rgb": rgb(BOX["color"]), "box_dash": BOX["dash"], "box_align": BOX["align"],
            "head_bold": bool(o["head_bold"]), "head_align": o["head_align"], "body_align": o.get("body_align") or "",
            "padding": {k: hwpunit_to_pt(v) for k, v in CELL_MARGIN.items()},
            "min_row_pt": hwpunit_to_pt(MIN_ROW_HEIGHT),
            "diagram_border_pt": mm_to_pt(DIAGRAM_BORDER), "diagram_fills": [rgb(f) for f in DIAGRAM_FILLS],
            "diagram_padding_pt": hwpunit_to_pt(DIAGRAM_MARGIN),
            "diagram_box_pt": hwpunit_to_pt(DIAGRAM_BOX_HEIGHT), "diagram_arrow_pt": hwpunit_to_pt(DIAGRAM_ARROW_HEIGHT)}
