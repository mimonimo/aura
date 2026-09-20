"""괘선 직독 표 추출 — 디지털 PDF의 벡터 선·글자 좌표로 표를 복원한다.

원리는 pdf_lines(글자 좌표 직독)와 같은 계열의 정공법이다. 디지털 PDF의
표는 괘선(벡터 path)과 글자층이 원본 그대로 들어 있으므로, OCR로 근사할
이유가 없다:

  1. 페이지의 path 오브젝트에서 가로·세로 선분을 모은다
  2. 가까운 선분끼리 묶어 표 영역을 찾는다 (2x2 이상 격자만 표로 인정)
  3. 선 좌표를 군집화해 행·열 경계를 정하고, 인접 칸 사이에 실제 경계선이
     없으면 병합 셀로 합친다 (경계선 존재 여부 검사 — 근사 아님)
  4. 글자층의 글자를 좌표로 각 셀에 배치한다 (오인식 원천 차단)

스캔 PDF에는 괘선 path가 없으므로 자연히 빈 결과가 나오고, 그 경우
기존 MinerU 표를 그대로 쓴다 (호출부에서 처리).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

from zzaimy.ingest.parsers.base import ParsedTable, TableCell

log = logging.getLogger(__name__)

_PATH_TYPE = 2          # pdfium 오브젝트 유형: path
_LINE_MIN_LEN = 8.0     # 이보다 짧은 선분은 장식으로 보고 무시 (pt)
_LINE_MAX_THICK = 3.0   # 선분으로 인정하는 최대 두께 (pt)
_CLUSTER_TOL = 3.0      # 같은 경계로 묶는 좌표 허용 오차 (pt)
_REGION_GAP = 12.0      # 이 거리 안의 선분은 같은 표 영역 (pt)
_EDGE_COVER = 0.5       # 경계선이 변의 이 비율 이상을 덮어야 "선이 있다"
_LINE_TOL = 2.5         # 글자 줄 묶음 허용 오차 (pt)


def extract_tables(pdf_path: Path | str) -> list[ParsedTable]:
    """PDF 전체에서 괘선 기반 표 목록을 추출한다. 실패는 빈 목록."""
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(pdf_path))
        out: list[ParsedTable] = []
        for i in range(len(doc)):
            out.extend(_page_tables(doc[i], page_no=i + 1))
        return out
    except Exception as e:
        log.warning("괘선 표 추출 실패 (%s: %s)", type(e).__name__, e)
        return []


def _bigram_jaccard(a, b) -> float:
    """두 표의 셀 텍스트 유사도 — 공백 제거 글자 2그램 자카드.

    같은 표라면 좌표 관행(렌더 배율·원점)이 달라도 내용은 같다. OCR이
    공백을 떨어뜨려도 글자 2그램은 살아남으므로 어절 비교보다 튼튼하다.
    """
    def grams(t) -> set[str]:
        s = "".join(c.text for c in t.cells)
        s = "".join(s.split())
        return {s[i:i + 2] for i in range(len(s) - 1)}

    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def swap_tables(
    entries, tables: list, lattice_tables: list, fits: dict[int, float],
    mappers: dict | None = None,
) -> tuple[list, int]:
    """MinerU 표 목록을 같은 자리의 괘선 표로 교체한다.

    매칭은 두 신호 중 큰 쪽: ① bbox 겹침 비율(같은 좌표 관행일 때),
    ② 셀 내용 2그램 유사도(좌표 관행이 달라도 같은 표를 알아본다 —
    MinerU 레이아웃 bbox와 벡터 좌표의 원점·배율이 페이지에 따라 어긋난
    실측 사례 대응). 어느 쪽으로도 확신이 없으면 교체하지 않는다(fail-closed).
    fits는 entry bbox의 페이지별 렌더 배율(pdf pt 환산용).
    반환: (교체된 표 목록, 교체 건수).
    """
    lattice_by_page: dict[int, list] = {}
    for lt in lattice_tables:
        lattice_by_page.setdefault(lt.page_no, []).append(lt)
    new_tables = list(tables)
    taken: set[tuple[int, int]] = set()  # (page_no, 후보 인덱스)
    n_swapped = 0
    for e in entries:
        if getattr(e, "kind", "") != "table":
            continue
        if not (0 <= e.ref < len(new_tables)):
            continue
        cands = lattice_by_page.get(e.page_no, [])
        best, best_ratio = None, 0.0
        fit = max(fits.get(e.page_no, 1.0), 1.0)
        if e.bbox and mappers and e.page_no in mappers:
            ebox = tuple(mappers[e.page_no](e.bbox))     # 페이지별로 확인한 좌표 관행
        else:
            ebox = tuple(v / fit for v in e.bbox) if e.bbox else None
        for k, lt in enumerate(cands):
            if (e.page_no, k) in taken or lt.bbox is None:
                continue
            if ebox is None:
                best, best_ratio = k, 1.0  # bbox 없으면 순서 매칭
                break
            ix = max(0.0, min(ebox[2], lt.bbox[2]) - max(ebox[0], lt.bbox[0]))
            iy = max(0.0, min(ebox[3], lt.bbox[3]) - max(ebox[1], lt.bbox[1]))
            area = (lt.bbox[2] - lt.bbox[0]) * (lt.bbox[3] - lt.bbox[1])
            ratio = (ix * iy) / area if area > 0 else 0.0
            # 좌표 관행이 어긋나도 내용이 같으면 같은 표다
            ratio = max(ratio, _bigram_jaccard(lt, new_tables[e.ref]))
            if ratio > best_ratio:
                best, best_ratio = k, ratio
        if best is not None and best_ratio >= 0.3:
            taken.add((e.page_no, best))
            new_tables[e.ref] = cands[best]
            n_swapped += 1
    return new_tables, n_swapped


def _page_tables(page, page_no: int) -> list[ParsedTable]:
    hs, vs = _segments(page)
    if len(hs) < 2 or len(vs) < 2:
        return []
    ph = page.get_size()[1]
    chars = _page_chars(page)
    tables = []
    for region_hs, region_vs in _regions(hs, vs):
        t = _build_table(region_hs, region_vs, chars, page_no, ph)
        if t is not None:
            tables.append(t)
    return tables


def _segments(page) -> tuple[list, list]:
    """path 오브젝트 → 가로/세로 선분. 채워진 사각형은 테두리 4선으로 푼다.

    선분 형식: 가로 (x0, x1, y) · 세로 (y0, y1, x)
    """
    hs, vs = [], []
    for obj in page.get_objects():
        if obj.type != _PATH_TYPE:
            continue
        try:
            left, bottom, right, top = obj.get_bounds()
        except Exception:
            continue
        w, h = right - left, top - bottom
        if w >= _LINE_MIN_LEN and h <= _LINE_MAX_THICK:
            hs.append((left, right, (bottom + top) / 2))
        elif h >= _LINE_MIN_LEN and w <= _LINE_MAX_THICK:
            vs.append((bottom, top, (left + right) / 2))
        elif w >= _LINE_MIN_LEN and h >= _LINE_MIN_LEN:
            hs.append((left, right, bottom))
            hs.append((left, right, top))
            vs.append((bottom, top, left))
            vs.append((bottom, top, right))
    return hs, vs


def _regions(hs: list, vs: list):
    """선분들을 근접 여부로 묶어 표 영역별 (가로선, 세로선) 그룹을 낸다."""
    segs = [("h", s) for s in hs] + [("v", s) for s in vs]
    boxes = []
    for kind, s in segs:
        if kind == "h":
            x0, x1, y = s
            boxes.append((x0, y, x1, y))
        else:
            y0, y1, x = s
            boxes.append((x, y0, x, y1))
    parent = list(range(len(segs)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        parent[find(a)] = find(b)

    def near(b1, b2):
        return not (
            b1[2] + _REGION_GAP < b2[0] or b2[2] + _REGION_GAP < b1[0]
            or b1[3] + _REGION_GAP < b2[1] or b2[3] + _REGION_GAP < b1[1]
        )

    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            if near(boxes[i], boxes[j]):
                union(i, j)

    groups: dict[int, tuple[list, list]] = defaultdict(lambda: ([], []))
    for i, (kind, s) in enumerate(segs):
        g = groups[find(i)]
        (g[0] if kind == "h" else g[1]).append(s)

    out: list[tuple[list, list]] = []
    for g in groups.values():
        if len(g[0]) < 2 or len(g[1]) < 2:
            continue
        out.extend(_split_stacked(g[0], g[1]))
    return out


def _split_stacked(hs: list, vs: list) -> list[tuple[list, list]]:
    """가까이 붙은 별개의 표를 나눈다.

    표의 몸통은 세로선이 지탱한다. 그래서 세로선이 하나도 걸치지 않는 높이 구간이
    있으면 그 위아래는 서로 다른 표다. 쪽 안에서 표 둘이 조금 떨어져 나란히 놓인
    경우(제목 표와 내용 표처럼) 근접만으로 묶으면 한 격자로 합쳐져 열 경계가
    뒤섞이고, 어느 쪽 열 폭도 원본과 맞지 않게 된다.
    """
    spans = sorted((min(y0, y1), max(y0, y1)) for y0, y1, _ in vs)
    if not spans:
        return [(hs, vs)]
    bands: list[list[float]] = [[spans[0][0], spans[0][1]]]
    for lo, hi in spans[1:]:
        if lo - bands[-1][1] <= _REGION_GAP:      # 이어져 있으면 같은 몸통
            bands[-1][1] = max(bands[-1][1], hi)
        else:
            bands.append([lo, hi])
    if len(bands) < 2:
        return [(hs, vs)]

    out: list[tuple[list, list]] = []
    for lo, hi in bands:
        band_vs = [v for v in vs if min(v[0], v[1]) >= lo - _CLUSTER_TOL
                   and max(v[0], v[1]) <= hi + _CLUSTER_TOL]
        band_hs = [h for h in hs if lo - _REGION_GAP <= h[2] <= hi + _REGION_GAP]
        if len(band_hs) >= 2 and len(band_vs) >= 2:
            out.append((band_hs, band_vs))
    return out or [(hs, vs)]


def _cluster(vals: list[float]) -> list[float]:
    out: list[list[float]] = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= _CLUSTER_TOL:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


def _page_chars(page) -> list[tuple[float, float, float, str, float]]:
    """글자층 → (중심x, 중심y, 높이, 글자, 반폭) 목록. 반폭은 낱말 간격(공백)을 되살리는 데 쓴다."""
    tp = page.get_textpage()
    try:
        chars = []
        for i in range(tp.count_chars()):
            ch = tp.get_text_range(i, 1)
            if not ch or not ch.strip():
                continue
            # '넉넉한 상자'(글꼴 진행 폭·높이)를 쓴다. 잉크 상자는 ① 라틴 글자 사이에도 틈이 있어 'HEAD' 가
            # 'H EAD' 로 갈라지고 ② 마침표처럼 글줄 아래에만 잉크가 있는 글자의 중심이 내려가 다른 줄로
            # 묶인다('2026. 09. 28.' → '2026 09 28' + '. . .'). 넉넉한 상자면 둘 다 생기지 않는다.
            try:
                left, bottom, right, top = tp.get_charbox(i, loose=True)
            except TypeError:
                left, bottom, right, top = tp.get_charbox(i)
            chars.append(((left + right) / 2, (bottom + top) / 2, top - bottom, ch, (right - left) / 2))
        return chars
    finally:
        tp.close()


def _build_table(hs, vs, chars, page_no: int, page_h: float) -> ParsedTable | None:
    ys = _cluster([y for *_, y in hs])          # 오름차순 (PDF 좌표, 아래→위)
    xs = _cluster([x for *_, x in vs])
    if len(ys) < 3 or len(xs) < 2:
        # 행이 2줄은 돼야 표다 (경계 3개) — 밑줄 장식 오탐 방지
        return None
    n_rows, n_cols = len(ys) - 1, len(xs) - 1
    rows_top = list(reversed(ys))               # 위→아래

    def h_covered(x0: float, x1: float, y: float) -> bool:
        need = (x1 - x0) * _EDGE_COVER
        got = 0.0
        for sx0, sx1, sy in hs:
            if abs(sy - y) <= _CLUSTER_TOL:
                got += max(0.0, min(sx1, x1) - max(sx0, x0))
        return got >= need

    def v_covered(y0: float, y1: float, x: float) -> bool:
        need = (y1 - y0) * _EDGE_COVER
        got = 0.0
        for sy0, sy1, sx in vs:
            if abs(sx - x) <= _CLUSTER_TOL:
                got += max(0.0, min(sy1, y1) - max(sy0, y0))
        return got >= need

    # 병합 감지 — 인접 칸 사이 경계선이 없으면 같은 셀
    cell_parent = list(range(n_rows * n_cols))

    def find(a):
        while cell_parent[a] != a:
            cell_parent[a] = cell_parent[cell_parent[a]]
            a = cell_parent[a]
        return a

    for r in range(n_rows):
        y_top, y_bot = rows_top[r], rows_top[r + 1]
        for c in range(n_cols):
            idx = r * n_cols + c
            if c + 1 < n_cols and not v_covered(y_bot, y_top, xs[c + 1]):
                cell_parent[find(idx)] = find(idx + 1)
            if r + 1 < n_rows and not h_covered(xs[c], xs[c + 1], rows_top[r + 1]):
                cell_parent[find(idx)] = find(idx + n_cols)

    members: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for r in range(n_rows):
        for c in range(n_cols):
            members[find(r * n_cols + c)].append((r, c))

    cells = []
    for cell_list in members.values():
        rs = [r for r, _ in cell_list]
        cs = [c for _, c in cell_list]
        r0, c0 = min(rs), min(cs)
        x0, x1 = xs[c0], xs[max(cs) + 1]
        y_top, y_bot = rows_top[r0], rows_top[max(rs) + 1]
        text = _cell_text(chars, x0, x1, y_bot, y_top)
        cells.append(TableCell(
            row=r0, col=c0, text=text,
            row_span=max(rs) - r0 + 1, col_span=max(cs) - c0 + 1,
        ))
    cells.sort(key=lambda c: (c.row, c.col))

    total_w = xs[-1] - xs[0]
    col_w = tuple(
        round((xs[i + 1] - xs[i]) / total_w, 4) for i in range(n_cols)
    )
    bbox = (xs[0], page_h - ys[-1], xs[-1], page_h - ys[0])  # y0=위 기준
    return ParsedTable(
        page_no=page_no, n_rows=n_rows, n_cols=n_cols,
        cells=tuple(cells), col_w=col_w, bbox=bbox,
    )


# 같은 줄 두 글자 사이 틈이 글자 높이의 이 비율보다 크면 낱말 사이(공백)로 본다. 한글 본문의 자간은
# 보통 높이의 0~5%, 낱말 간격은 25~33% 안팎이다. PDF 는 공백을 글자 대신 위치 간격으로 두는 일이 많아
# 글자만 이어 붙이면 '도제학교운영지원신규기업발굴' 처럼 낱말이 붙는다(실측 2026-09-20 채용 공고 표).
_WORD_GAP = 0.15


def _cell_text(chars, x0, x1, y0, y1) -> str:
    """셀 사각형 안의 글자를 줄 단위로 묶어 원문 그대로 잇는다 — 낱말 간격은 공백으로 되살린다."""
    inside = [
        (cx, cy, h, ch, hw) for cx, cy, h, ch, hw in chars
        if x0 <= cx <= x1 and y0 <= cy <= y1
    ]
    if not inside:
        return ""
    lines: list[list[tuple]] = []
    for item in sorted(inside, key=lambda t: -t[1]):
        if lines and abs(lines[-1][0][1] - item[1]) <= _LINE_TOL:
            lines[-1].append(item)
        else:
            lines.append([item])
    parts = []
    for line in lines:
        out, prev = [], None
        for cx, _cy, h, ch, hw in sorted(line, key=lambda t: t[0]):
            if prev is not None and (cx - hw) - (prev[0] + prev[1]) > _WORD_GAP * max(h, 1.0):
                out.append(" ")
            out.append(ch)
            prev = (cx, hw)
        parts.append("".join(out))
    return "\n".join(" ".join(p.split()) for p in parts if p.strip())
