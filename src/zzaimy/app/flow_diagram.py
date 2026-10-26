"""처리 단계 한눈 그림(SVG) — 처리 흐름 화면 「처리 단계」 카드 맨 위에 둔다.

13단계를 글로만 늘어놓으면 「어디서 시작해 어디로 가는가」 가 안 보여, 단계를 네 구간(문서 들이기 · 찾을 준비 · 답하고 쓰기 · 학습)의
띠로 묶고 차례대로 화살표로 잇는다. 공동 편집 순서 그림(coedit_diagram)과 같은 결 — 구간마다 색, 단계마다 번호, 쉬운 말.
단계 이름·장비·상태는 process_flow.STAGES 에서 읽는다(그림만 따로 고치지 않게). 상자를 누르면 그 단계 설명으로 간다.
"""
from __future__ import annotations

from html import escape

W = 1060
PAD = 24
GAP = 36                      # 같은 줄 상자 사이(화살표 자리)
BOX_H = 58
BAND_H = 22 + BOX_H + 16      # 구간 이름 줄 + 상자 + 아래 여백
BAND_GAP = 30                 # 구간 사이(다음 줄로 꺾는 화살표 자리)
PER_ROW = 4

# 구간 — (이름, 첫 단계, 끝 단계, 바탕색, 진한 색). 색은 공동 편집 그림과 같은 갈래
PHASES = (
    ("문서 들이기", 1, 3, "#e8f0fe", "#2f5fb3"),
    ("찾을 준비", 4, 6, "#f1ecfb", "#6b4fb3"),
    ("답하고 쓰기", 7, 10, "#e6f4ea", "#2e7d4f"),
    ("학습·교체", 11, 13, "#fdf3e7", "#8a5a00"),
)


def _text_w(text: str, size: float) -> float:
    """대략의 글 폭 — 한글은 글자 크기만큼, 영문·숫자는 그 0.58, 빈칸은 0.3."""
    w = 0.0
    for ch in text:
        if ch == " ":
            w += size * 0.3
        elif ord(ch) < 0x1100:
            w += size * 0.58
        else:
            w += size
    return w


def _fit(text: str, width: float, size: float) -> str:
    if _text_w(text, size) <= width:
        return text
    while text and _text_w(text + "…", size) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def svg(stages=None) -> str:
    if stages is None:
        from zzaimy.app.process_flow import STAGES
        stages = STAGES
    by_n = {s["n"]: s for s in stages}
    box_w = (W - 2 * PAD - 32 - (PER_ROW - 1) * GAP) / PER_ROW
    h = PAD + len(PHASES) * BAND_H + (len(PHASES) - 1) * BAND_GAP + PAD
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" role="img" aria-label="처리 단계 한눈 그림" '
         'font-family="Pretendard, \'Apple SD Gothic Neo\', \'Malgun Gothic\', sans-serif">', '<defs>']
    for i, (*_rest, fg) in enumerate(PHASES):
        o.append(f'<marker id="fa{i}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
                 f'<path d="M0,0 L10,5 L0,10 z" fill="{fg}"/></marker>')
    o.append('</defs>')
    o.append(f'<rect width="{W}" height="{h}" fill="#ffffff"/>')
    rects: dict[int, tuple[float, float]] = {}
    links: list[str] = []
    for i, (name, a, b, bg, fg) in enumerate(PHASES):
        y0 = PAD + i * (BAND_H + BAND_GAP)
        o.append(f'<rect x="{PAD - 8}" y="{y0}" width="{W - 2 * PAD + 16}" height="{BAND_H}" rx="12" fill="{bg}" fill-opacity=".45" stroke="{fg}" stroke-opacity=".18"/>')
        o.append(f'<text x="{PAD + 8}" y="{y0 + 17}" font-size="12" font-weight="700" fill="{fg}">{escape(name)}</text>')
        ns = [n for n in range(a, b + 1) if n in by_n]
        by = y0 + 24
        for j, n in enumerate(ns):
            s = by_n[n]
            bx = PAD + 16 + j * (box_w + GAP)
            rects[n] = (bx, by)
            full = s["name"]
            main = full.split("(")[0].strip()
            title = full if _text_w(full, 13.5) <= box_w - 48 else main
            where = " · ".join(s.get("where") or [])
            part = s.get("state") != "운영 중"
            o.append(f'<a href="#stage-{n}" aria-label="{n}단계 {escape(full)}">')
            o.append(f'<rect x="{bx:.0f}" y="{by}" width="{box_w:.0f}" height="{BOX_H}" rx="10" fill="#ffffff" stroke="{fg}" stroke-width="1.4"/>')
            o.append(f'<circle cx="{bx + 22:.0f}" cy="{by + BOX_H / 2:.0f}" r="12" fill="{fg}"/>')
            o.append(f'<text x="{bx + 22:.0f}" y="{by + BOX_H / 2 + 4:.0f}" font-size="12" font-weight="700" fill="#ffffff" text-anchor="middle">{n}</text>')
            o.append(f'<text x="{bx + 44:.0f}" y="{by + 25}" font-size="13.5" font-weight="700" fill="#1b2a41">{escape(title)}</text>')
            sub = _fit(where, box_w - 52 - (54 if part else 0), 11)
            o.append(f'<text x="{bx + 44:.0f}" y="{by + 43}" font-size="11" fill="#5f6b7a">{escape(sub)}</text>')
            if part:
                o.append(f'<text x="{bx + box_w - 10:.0f}" y="{by + 43}" font-size="10.5" font-weight="700" fill="#8a5a00" text-anchor="end">{escape(s["state"])}</text>')
            o.append('</a>')
            if j:
                x1 = bx - GAP + 4
                links.append(f'<line x1="{x1:.0f}" y1="{by + BOX_H / 2:.0f}" x2="{bx - 4:.0f}" y2="{by + BOX_H / 2:.0f}" '
                             f'stroke="{fg}" stroke-width="1.8" marker-end="url(#fa{i})"/>')
        if i and ns and (a - 1) in rects:                    # 앞 구간 끝 상자 → 이 구간 첫 상자(아래로 꺾어서)
            px, py = rects[a - 1]
            sx, sy = px + box_w / 2, py + BOX_H
            tx, ty = rects[ns[0]][0] + box_w / 2, rects[ns[0]][1]
            mid = y0 - BAND_GAP / 2
            pfg = PHASES[i - 1][4]
            links.append(f'<polyline points="{sx:.0f},{sy + 2} {sx:.0f},{mid:.0f} {tx:.0f},{mid:.0f} {tx:.0f},{ty - 4}" fill="none" '
                         f'stroke="{pfg}" stroke-width="1.8" stroke-linejoin="round" marker-end="url(#fa{i - 1})"/>')
    o.extend(links)
    o.append("</svg>")
    return "\n".join(o)
