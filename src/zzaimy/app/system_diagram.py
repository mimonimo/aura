"""시스템 구성도(SVG) — 장비·연결 목록(process_flow.HOSTS·LINKS)에서 그린다.

손으로 그린 그림은 장비·역할이 바뀌어도 그대로 남아 옛 모습이 됐다(구글 독스 협업·「학습본 준비 중」). 이제 그림도 같은 목록에서
만들어 개발 현황 「시스템 구성」·논문·발표가 늘 같은 내용을 쓴다. 자리는 장비 이름으로 정하고, 자리가 없는 새 장비는 맨 아래 줄에 붙는다.
그림 파일 static/topology.svg 도 이 함수의 결과다(인수인계 문서가 링크) — 장비 목록을 바꾸면 다시 쓴다:
  PYTHONPATH=src python -c "from zzaimy.app import system_diagram as s; open('src/zzaimy/app/static/topology.svg','w').write(s.svg()+'\\n')"
시험(test_dev_flow)이 파일과 함수 결과가 다르면 막는다.
"""
from __future__ import annotations

from html import escape

W, H = 1100, 790
CHIP_H, CHIP_GAP = 24, 8

# 장비 이름 → (왼쪽, 위, 폭). 높이는 구성 요소 칩 수로 정한다. 사용자(위) → 플랫폼(가운데) → 모델·학습(아래)
POS = {
    "교직원 브라우저": (40, 30, 340),
    "구글 드라이브·독스": (720, 30, 340),
    "운영 VM": (250, 215, 600),
    "토르 02": (40, 500, 330),
    "토르 03": (385, 500, 330),
    "DGX": (730, 500, 330),
    "원본 보관 PC": (730, 670, 330),
}
EXTRA_HOSTS = {"구글 드라이브·독스": {"role": "보조 경로(드라이브 가져오기·독스 편집)", "spec": "계정을 이은 사용자만"}}
GROUP = {"토르 02·03": ("토르 02", "토르 03")}   # 연결 목록의 묶음 이름 → 받는 상자들. 같은 줄 옆 상자라 곧은 선이 짧아 글이 겹친다 — 상자 아래로 잇는 선으로
MAIN = "운영 VM"
DASHED = {"구글 드라이브·독스"}
# 장비 갈래 색(바탕, 진한 색) — 처리 흐름의 단계 그림·공동 편집 그림과 같은 색 결. 화살표는 보내는 장비의 색
KIND = {"교직원 브라우저": "user", "구글 드라이브·독스": "user", "운영 VM": "platform",
        "토르 02": "serve", "토르 03": "serve", "DGX": "data", "원본 보관 PC": "data"}
COLOR = {"user": ("#e8f0fe", "#2f5fb3"), "platform": ("#f1ecfb", "#6b4fb3"),
         "serve": ("#e6f4ea", "#2e7d4f"), "data": ("#fdf3e7", "#8a5a00")}
LEGEND = (("user", "사용자 쪽"), ("platform", "운영 플랫폼"), ("serve", "모델 서빙"), ("data", "원본·학습"))


def _color(name: str) -> tuple[str, str]:
    return COLOR[KIND.get(name, "platform")]


def _cols(n: int) -> int:
    return 3 if n > 4 else max(1, n)


def _box_h(h: dict) -> int:
    parts = h.get("parts") or []
    rows = (len(parts) + _cols(len(parts)) - 1) // _cols(len(parts)) if parts else 0
    return (44 + rows * (CHIP_H + CHIP_GAP) + 22) if parts else 86   # 칩이 있으면 설명 줄 없이 칩으로


def _short(what: str) -> str:
    s = what.split(" — ")[0].split("(")[0].strip()
    return s if len(s) <= 32 else s[:31] + "…"


def _edge(a: tuple, b: tuple, offset: float) -> tuple[float, float, float, float]:
    """두 상자(x, y, w, h) 가운데를 잇는 선을 테두리에서 끊는다. offset 은 같은 두 상자 사이 둘째 선을 옆으로 비킨다."""
    ax, ay = a[0] + a[2] / 2, a[1] + a[3] / 2
    bx, by = b[0] + b[2] / 2, b[1] + b[3] / 2
    dx, dy = bx - ax, by - ay
    n = max((dx * dx + dy * dy) ** 0.5, 1.0)
    px, py = -dy / n * offset, dx / n * offset

    def cut(cx, cy, tx, ty, w, h):
        sx = (w / 2) / abs(tx) if tx else 1e9
        sy = (h / 2) / abs(ty) if ty else 1e9
        s = min(sx, sy)
        return cx + tx * s, cy + ty * s

    x1, y1 = cut(ax + px, ay + py, dx, dy, a[2], a[3])
    x2, y2 = cut(bx + px, by + py, -dx, -dy, b[2], b[3])
    return x1, y1, x2, y2


def svg() -> str:
    from zzaimy.app.process_flow import HOSTS, LINKS
    hosts = {h["name"]: h for h in HOSTS} | {k: {"name": k, **v} for k, v in EXTRA_HOSTS.items()}
    pos = dict(POS)
    x = 40
    for name in hosts:                                    # 자리가 없는 새 장비 — 맨 아래 줄
        if name not in pos:
            pos[name] = (x, 670, 330)
            x += 345
    rect = {n: (pos[n][0], pos[n][1], pos[n][2], _box_h(hosts[n])) for n in hosts}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" role="img" aria-label="AURA 시스템 구성도" '
           'font-family="Pretendard, \'Apple SD Gothic Neo\', \'Malgun Gothic\', sans-serif">',
           '<defs>' + "".join(f'<marker id="arr-{k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
                              f'<path d="M0,0 L10,5 L0,10 z" fill="{fg}"/></marker>' for k, (_bg, fg) in COLOR.items()) + '</defs>',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
    buses = [(a, GROUP[b], what) for a, b, what in LINKS if b in GROUP and a in rect and all(t in rect for t in GROUP[b])]
    links = [l for l in LINKS if l[0] in rect and l[1] in rect]
    total: dict[frozenset, int] = {}
    for a, b, _ in links:
        total[frozenset((a, b))] = total.get(frozenset((a, b)), 0) + 1
    seen: dict[frozenset, int] = {}
    labels = []
    for a, b, what in links:
        key = frozenset((a, b))
        k = seen.get(key, 0)
        seen[key] = k + 1
        off = (k - (total[key] - 1) / 2) * 34 * (1 if a < b else -1)   # 같은 두 장비 사이 선이 여럿이면 나란히(방향이 반대여도 같은 기준)
        x1, y1, x2, y2 = _edge(rect[a], rect[b], off)
        dash = ' stroke-dasharray="5 4"' if a in DASHED or b in DASHED else ""
        fg, kind = _color(a)[1], KIND.get(a, "platform")
        out.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" stroke="{fg}" stroke-width="1.8"{dash} marker-end="url(#arr-{kind})"/>')
        f = 0.5 if total[key] == 1 else (0.38 if k == 0 else 0.62)
        labels.append((x1 + (x2 - x1) * f, y1 + (y2 - y1) * f, _short(what)))
    for a, targets, what in buses:                         # 묶음으로 가는 선 — 보낸 상자 아래 → 받는 상자들 아래를 잇는 가로줄 → 위로 화살표
        ax, ay, aw, ah = rect[a]
        y = max(rect[t][1] + rect[t][3] for t in (a, *targets)) + 26
        sx = ax + 30
        xs = [rect[t][0] + rect[t][2] / 2 for t in targets]
        fg, kind = _color(a)[1], KIND.get(a, "platform")
        out.append(f'<polyline points="{sx:.0f},{ay + ah:.0f} {sx:.0f},{y:.0f} {min(xs):.0f},{y:.0f}" fill="none" stroke="{fg}" stroke-width="1.8"/>')
        for t, tx in zip(targets, xs):
            out.append(f'<line x1="{tx:.0f}" y1="{y:.0f}" x2="{tx:.0f}" y2="{rect[t][1] + rect[t][3]:.0f}" stroke="{fg}" stroke-width="1.8" marker-end="url(#arr-{kind})"/>')
        labels.append(((min(xs) + sx) / 2, y + 20, _short(what)))
    for name, h in hosts.items():
        x0, y0, bw, bh = rect[name]
        main = name == MAIN
        bg, fg = _color(name)
        dash = ' stroke-dasharray="5 4"' if name in DASHED else ""
        out.append(f'<rect x="{x0}" y="{y0}" width="{bw}" height="{bh}" rx="12" fill="{bg}" fill-opacity="{1 if main else .6}" '
                   f'stroke="{fg}" stroke-opacity="{.7 if main else .45}" stroke-width="{1.8 if main else 1.3}"{dash}/>')
        out.append(f'<text x="{x0 + 16}" y="{y0 + 27}" font-size="15.5" font-weight="700" fill="{fg}">{escape(name)}</text>')
        parts = h.get("parts") or []
        if not parts:                                     # 칩이 없는 상자만 한 줄 설명
            role = h.get("role", "")
            role = role if len(role) <= bw // 11 else role[: bw // 11 - 1] + "…"
            out.append(f'<text x="{x0 + 16}" y="{y0 + 48}" font-size="11.5" fill="#4b5768">{escape(role)}</text>')
        cols = _cols(len(parts))
        cw = (bw - 32 - (cols - 1) * CHIP_GAP) / cols if parts else 0
        for i, part in enumerate(parts):
            cx = x0 + 16 + (i % cols) * (cw + CHIP_GAP)
            cy = y0 + 40 + (i // cols) * (CHIP_H + CHIP_GAP)
            out.append(f'<rect x="{cx:.0f}" y="{cy}" width="{cw:.0f}" height="{CHIP_H}" rx="6" fill="#ffffff" stroke="{fg}" stroke-opacity=".3"/>')
            out.append(f'<text x="{cx + cw / 2:.0f}" y="{cy + 16}" font-size="11.5" fill="#1b2a41" text-anchor="middle">{escape(part)}</text>')
        if h.get("spec"):
            out.append(f'<text x="{x0 + 16}" y="{y0 + bh - 10}" font-size="10.5" fill="#5f6b7a">{escape(h["spec"])}</text>')
    for mx, my, label in labels:                          # 글은 맨 위에 — 상자에 가리지 않게
        out.append(f'<text x="{mx:.0f}" y="{my - 5:.0f}" font-size="11.5" fill="#2c3a50" text-anchor="middle" '
                   f'paint-order="stroke" stroke="#ffffff" stroke-width="5">{escape(label)}</text>')
    lx, ly = 40, H - 46                                   # 범례 — 아래 왼쪽 빈자리(토르 아래)
    out.append(f'<text x="{lx}" y="{ly - 12}" font-size="11.5" font-weight="700" fill="#5f6b7a">화살표 색은 보내는 장비 색</text>')
    for i, (k, label) in enumerate(LEGEND):
        bg, fg = COLOR[k]
        x = lx + i * 130
        out.append(f'<rect x="{x}" y="{ly}" width="16" height="16" rx="4" fill="{bg}" stroke="{fg}" stroke-opacity=".6"/>')
        out.append(f'<text x="{x + 22}" y="{ly + 12.5}" font-size="12" fill="#1b2a41">{escape(label)}</text>')
    x = lx + len(LEGEND) * 130
    out.append(f'<line x1="{x}" y1="{ly + 8}" x2="{x + 26}" y2="{ly + 8}" stroke="#5f6b7a" stroke-width="1.6" stroke-dasharray="5 4"/>')
    out.append(f'<text x="{x + 32}" y="{ly + 12.5}" font-size="12" fill="#1b2a41">보조 경로</text>')
    out.append("</svg>")
    return "\n".join(out)
