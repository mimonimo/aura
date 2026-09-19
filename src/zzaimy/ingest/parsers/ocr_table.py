"""OCR 표의 열 폭 비율 인출 — 괘선이 없는 스캔·사진 표를 위한 정공법.

디지털 PDF는 lattice(괘선 직독)가, HWPX/HWP5는 원본 문서의 셀 폭이 열 폭을
준다. 스캔본·사진의 표에는 벡터 괘선도 원본 폭 메타도 없어, MinerU는 표의
구조(행·열·병합)만 HTML로 주고 열 폭 정보는 남기지 않는다 — 그래서 화면에
균등 폭으로 그려진다.

이 모듈은 표 영역 안에 놓인 OCR 글자줄의 x 좌표 분포에서 열과 열 사이의
빈 세로 간격(거터)을 찾아 열 경계를 인출한다. 원리는 pdf_lines·lattice와
같은 계열이다 — 없는 값을 지어내지 않고, 이미 읽은 글자 좌표에서만 유도한다.

한 줄의 글자 사이 빈틈은 다른 줄의 글자가 같은 x를 덮으므로, 모든 줄을 겹쳐
투영하면 진짜 열 경계(어느 줄도 글자가 없는 세로 띠)만 남는다. 인출한 경계
개수가 인식된 열 개수(n_cols)와 맞지 않거나 신뢰도가 낮으면 빈 값을 돌려
균등 폭으로 안전하게 폴백한다(fail-closed).
"""

from __future__ import annotations


def _merge_intervals(spans: list[tuple[float, float]]) -> list[list[float]]:
    """겹치는 [a, b] 구간들을 합친다 — 정렬 후 선형 병합."""
    merged: list[list[float]] = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            if b > merged[-1][1]:
                merged[-1][1] = b
        else:
            merged.append([a, b])
    return merged


def derive_col_widths(
    x0: float,
    x1: float,
    line_spans: list[tuple[float, float]],
    n_cols: int,
    *,
    min_lines: int = 3,
    min_gutter_frac: float = 0.01,
    min_col_frac: float = 0.02,
) -> tuple[float, ...]:
    """표 영역 안 글자줄 x 분포에서 열 폭 비율(합=1)을 인출한다.

    x0·x1은 표의 좌·우 경계, line_spans는 그 안에 놓인 글자줄들의 (x_left,
    x_right), n_cols는 인식된 열 개수다. 열 경계를 신뢰도 있게 못 찾으면 빈
    튜플을 돌려 호출부가 균등 폭으로 폴백하게 한다.

    - min_lines: 투영이 의미를 가지려면 필요한 최소 글자줄 수.
    - min_gutter_frac: 열 사이 간격으로 인정할 최소 폭(표 폭 대비 비율).
      글자 사이 빈틈이 아니라 열 경계임을 가르는 하한.
    - min_col_frac: 한 열의 최소 폭 비율. 이보다 좁은 열이 나오면 경계가
      잡음이라 보고 폴백한다.
    """
    tw = x1 - x0
    if n_cols < 2 or tw <= 0:
        return ()

    spans: list[tuple[float, float]] = []
    for lx0, lx1 in line_spans:
        a, b = max(x0, min(lx0, lx1)), min(x1, max(lx0, lx1))
        if b - a > 0:
            spans.append((a, b))
    # 열마다 글자 근거가 있어야 투영이 경계를 가른다 — 근거가 얕으면 폴백한다
    # (실측: 3~8줄짜리 치우친 표에서 확신 있게 틀린 비율이 나왔다)
    if len(spans) < max(min_lines, 2 * n_cols):
        return ()

    covered = _merge_intervals(spans)
    if len(covered) < 2:
        return ()

    # 덮인 구간 사이의 빈 세로 띠(거터) — 표 안쪽 경계 후보
    gutters: list[tuple[float, float]] = []  # (폭, 중심)
    for left, right in zip(covered, covered[1:]):
        gap = right[0] - left[1]
        if gap > 0:
            gutters.append((gap, (left[1] + right[0]) / 2.0))

    min_gw = max(min_gutter_frac * tw, 1.0)
    gutters = [g for g in gutters if g[0] >= min_gw]
    if len(gutters) < n_cols - 1:
        return ()

    # 가장 뚜렷한(넓은) n_cols-1 개를 열 경계로 — 진짜 열 경계가 가장 깨끗하다.
    # 후보가 남아돌면(빈틈이 여럿) 고른 경계가 나머지보다 뚜렷이 넓어야 한다 —
    # 폭이 엇비슷해 모호하면 어느 게 열 경계인지 못 가른 것이니 폴백한다.
    gutters.sort(reverse=True)
    if len(gutters) > n_cols - 1:
        picked_min = gutters[n_cols - 2][0]
        next_w = gutters[n_cols - 1][0]
        if picked_min < 1.3 * next_w:
            return ()
    centers = sorted(c for _w, c in gutters[: n_cols - 1])

    bounds = [x0, *centers, x1]
    widths: list[float] = []
    for lo, hi in zip(bounds, bounds[1:]):
        w = (hi - lo) / tw
        if w < min_col_frac:
            return ()
        widths.append(round(w, 4))
    if len(widths) != n_cols:
        return ()

    # 반올림 오차는 마지막 열에서 흡수해 합을 정확히 1.0으로
    widths[-1] = round(1.0 - sum(widths[:-1]), 4)
    if widths[-1] <= 0:
        return ()
    return tuple(widths)
