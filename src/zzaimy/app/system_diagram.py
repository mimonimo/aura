"""시스템 구성도(SVG) — 장비·연결 목록(process_flow.HOSTS·LINKS)에서 그린다.

손으로 그린 그림은 장비·역할이 바뀌어도 그대로 남아 옛 모습이 됐다(구글 독스 협업·「학습본 준비 중」). 이제 그림도 같은 목록에서
만들어 개발 현황 「시스템 구성」·논문·발표가 늘 같은 내용을 쓴다. 자리는 장비 이름으로 정하고, 자리가 없는 새 장비는 맨 아래 줄에 붙는다.
그림 파일 static/topology.svg 도 이 함수의 결과다(인수인계 문서가 링크) — 장비 목록을 바꾸면 다시 쓴다:
  PYTHONPATH=src python -c "from zzaimy.app import system_diagram as s; open('src/zzaimy/app/static/topology.svg','w').write(s.svg()+'\\n')"
시험(test_dev_flow)이 파일과 함수 결과가 다르면 막는다.
"""
from __future__ import annotations

from html import escape

W, H = 980, 720
BOX_W, BOX_H = 280, 112

# 장비 이름 → 왼쪽 위 좌표. 사용자(위) → 플랫폼(가운데) → 모델·학습(아래) 차례
POS = {
    "교직원 브라우저": (60, 30),
    "구글 드라이브·독스": (650, 30),
    "운영 VM": (330, 220),
    "토르 02": (40, 420),
    "토르 03": (355, 420),
    "DGX": (670, 420),
    "원본 보관 PC": (670, 575),
}
EXTRA_HOSTS = {"구글 드라이브·독스": {"role": "보조 경로 — 드라이브 가져오기·독스 편집", "spec": "계정을 이은 사용자만"}}
ALIAS = {"토르 02·03": "토르 03"}          # 연결 목록의 묶음 이름 → 그림에서 가리킬 상자
MAIN = "운영 VM"
DASHED = {"구글 드라이브·독스"}


def _wrap(text: str, width: int) -> list[str]:
    """한글 기준 width 글자로 자른다 — 「·」·띄어쓰기 뒤에서만 끊어 줄머리에 「·」 가 오지 않게."""
    import re
    parts = [p for p in re.split(r"(?<=·)|(?<= )", text.replace(" · ", "·")) if p]
    out, line = [], ""
    for part in parts:
        if len((line + part).strip()) > width and line.strip():
            out.append(line.strip())
            line = part
        else:
            line += part
    if line.strip():
        out.append(line.strip())
    return out[:3]


def _short(what: str) -> str:
    s = what.split(" — ")[0].split("(")[0].strip()
    return s if len(s) <= 18 else s[:17] + "…"


def _edge(a: tuple, b: tuple, offset: float) -> tuple[float, float, float, float]:
    """두 상자 가운데를 잇는 선을 상자 테두리에서 끊는다. offset 은 같은 두 상자 사이 두 번째 선을 옆으로 비킨다."""
    ax, ay = a[0] + BOX_W / 2, a[1] + BOX_H / 2
    bx, by = b[0] + BOX_W / 2, b[1] + BOX_H / 2
    dx, dy = bx - ax, by - ay
    n = max((dx * dx + dy * dy) ** 0.5, 1.0)
    px, py = -dy / n * offset, dx / n * offset

    def cut(cx, cy, tx, ty):
        sx = (BOX_W / 2) / abs(tx) if tx else 1e9
        sy = (BOX_H / 2) / abs(ty) if ty else 1e9
        s = min(sx, sy)
        return cx + tx * s, cy + ty * s

    x1, y1 = cut(ax, ay, dx, dy)
    x2, y2 = cut(bx, by, -dx, -dy)
    return x1 + px, y1 + py, x2 + px, y2 + py


def svg() -> str:
    from zzaimy.app.process_flow import HOSTS, LINKS
    hosts = {h["name"]: h for h in HOSTS} | {k: {"name": k, **v} for k, v in EXTRA_HOSTS.items()}
    pos = dict(POS)
    x = 40
    for name in hosts:                                    # 자리가 없는 새 장비 — 맨 아래 줄
        if name not in pos:
            pos[name] = (x, 575)
            x += 300
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Pretendard, \'Apple SD Gothic Neo\', \'Malgun Gothic\', sans-serif">',
           '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
           '<path d="M0,0 L10,5 L0,10 z" fill="#5b6b82"/></marker></defs>',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
    links = [(ALIAS.get(a, a), ALIAS.get(b, b), what) for a, b, what in LINKS]
    links = [l for l in links if l[0] in pos and l[1] in pos]
    total: dict[frozenset, int] = {}
    for a, b, _ in links:
        total[frozenset((a, b))] = total.get(frozenset((a, b)), 0) + 1
    seen: dict[frozenset, int] = {}
    for a, b, what in links:
        key = frozenset((a, b))
        k = seen.get(key, 0)
        seen[key] = k + 1
        x1, y1, x2, y2 = _edge(pos[a], pos[b], (k - (total[key] - 1) / 2) * 34 * (1 if a < b else -1))   # 같은 두 장비 사이 선이 여럿이면 옆으로 나란히 — 방향이 반대인 선도 같은 기준으로 비킨다
        dash = ' stroke-dasharray="5 4"' if a in DASHED or b in DASHED else ""
        out.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" stroke="#5b6b82" stroke-width="1.6"{dash} marker-end="url(#arr)"/>')
        f = 0.5 if total[key] == 1 else (0.35 if k == 0 else 0.65)       # 나란한 두 선의 글은 앞뒤로 비켜 겹치지 않게
        mx, my = x1 + (x2 - x1) * f, y1 + (y2 - y1) * f
        label = escape(_short(what))
        out.append(f'<text x="{mx:.0f}" y="{my - 5:.0f}" font-size="11.5" fill="#3b4757" text-anchor="middle" '
                   f'paint-order="stroke" stroke="#ffffff" stroke-width="4">{label}</text>')
    for name, h in hosts.items():
        x0, y0 = pos[name]
        main = name == MAIN
        fill = "#eef3fb" if main else "#f8fafc"
        border = "#7f9cc4" if main else "#c9d3e0"
        dash = ' stroke-dasharray="5 4"' if name in DASHED else ""
        out.append(f'<rect x="{x0}" y="{y0}" width="{BOX_W}" height="{BOX_H}" rx="10" fill="{fill}" stroke="{border}" stroke-width="1.4"{dash}/>')
        out.append(f'<text x="{x0 + 16}" y="{y0 + 26}" font-size="15" font-weight="700" fill="#1b2a41">{escape(name)}</text>')
        for i, ln in enumerate(_wrap(h.get("role", ""), 24)):
            out.append(f'<text x="{x0 + 16}" y="{y0 + 47 + i * 15}" font-size="11.5" fill="#3b4757">{escape(ln)}</text>')
        if h.get("spec"):
            out.append(f'<text x="{x0 + 16}" y="{y0 + BOX_H - 10}" font-size="10.5" fill="#7a869a">{escape(h["spec"])}</text>')
    out.append("</svg>")
    return "\n".join(out)
