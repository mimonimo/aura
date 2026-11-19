"""README·논문 구성도(SVG)를 만든다. 내용은 PROJECT_BRIEF 4장, docs/architecture.md, docs/dev-now.md, app/model_tracks.py 기준.

    python docs/images/diagrams/make_diagrams.py          # 이 파일의 그림(SVG)을 이 폴더에 쓴다
    node docs/images/diagrams/render.mjs                  # SVG → PNG(3배, README·논문이 쓰는 그림)

논문 인쇄 기준(11-17 지도 교수 검토): 그림은 폭 15cm, 높이 12.5cm 안에 들어간다. 캔버스를 폭 W=720 으로 두고
본문 글자 16px(인쇄 9.4pt), 상자 이름 18px, 그림 제목 20px 로 쓴다. 높이는 630 을 넘기지 않는다(넘으면 높이에 맞춰
줄어 9pt 아래가 된다). 상자 안 여백 10~12px, 바깥 여백 14px. 글은 상자 폭에 맞춰 줄을 바꾼다(wrap).
글꼴은 Pretendard → Noto Sans KR → Apple SD Gothic Neo → 맑은 고딕 순으로 고른다. 모양을 바꾸면 두 단계를 다시 돌린다.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
FONT = "Pretendard, 'Noto Sans KR', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"
MONO = "'SF Mono', Menlo, Consolas, monospace"
NAVY, INK, MUTED, LINE, BORDER = "#1b3b75", "#1f2937", "#4b5563", "#7b8797", "#aab4c3"
FILL, ACCENT, SOFT = "#ffffff", "#eef2f9", "#f6f8fb"

W = 720            # 캔버스 폭(인쇄 15cm)
M = 14             # 바깥 여백
TITLE, HEAD, BODY = 20, 18, 16
LH, HLH = 21, 24   # 본문·상자 이름 줄 높이
PAD, PADX = 10, 12
SW, SWS, SWA = 1.5, 2.2, 2.0   # 상자 테두리 · 강조 테두리 · 화살표


def tw(t: str, size: float = BODY, mono: bool = False) -> float:
    """글자 폭 어림(크롬 + Apple SD Gothic Neo 실측보다 조금 넉넉하게: 한글 0.87em → 0.9)."""
    if mono:
        return len(t) * size * 0.61
    w = 0.0
    for c in t:
        o = ord(c)
        if c == " ":
            w += 0.3
        elif c == "·":
            w += 0.36
        elif c in "「」":
            w += 0.6
        elif o >= 0x1100 or 0x2190 <= o <= 0x22ff or 0x2460 <= o <= 0x24ff or c in "○∥Σ×≥≤":
            w += 0.9
        elif c in "mwMW@%":
            w += 0.85
        elif c.isupper():
            w += 0.59
        elif c.isdigit():
            w += 0.55
        elif c.isalpha():
            w += 0.46
        else:
            w += 0.38
    return w * size


def wrap(t: str, width: float, size: float = BODY) -> list[str]:
    """폭에 맞게 줄을 나눈다(띄어쓰기에서, 그다음 「·」 뒤에서, 안 되면 글자에서)."""
    if not t or tw(t, size) <= width:
        return [t]
    toks = []
    for wd in t.split(" "):
        if tw(wd, size) > width and "·" in wd.strip("·"):
            parts = wd.split("·")
            toks += [(p + "·", False) for p in parts[:-1]] + [(parts[-1], True)]
        else:
            toks.append((wd, True))
    lines, cur, glue = [], "", True
    for wd, sp in toks:
        cand = (f"{cur} {wd}" if glue else cur + wd) if cur else wd
        if tw(cand, size) <= width:
            cur, glue = cand, sp
            continue
        if cur:
            lines.append(cur)
        cur = ""
        while tw(wd, size) > width:
            k = len(wd)
            while k > 1 and tw(wd[:k], size) > width:
                k -= 1
            lines.append(wd[:k])
            wd = wd[k:]
        cur, glue = wd, sp
    if cur:
        lines.append(cur)
    return lines


def _tag_inline(inner, title, tag, title_size=HEAD) -> bool:
    return bool(title) and len(wrap(title, inner, title_size)) == 1 and tw(title, title_size) + tw(tag) + 14 <= inner


def box_h(w, title="", lines=(), *, tag="", title_size=HEAD) -> int:
    """box() 가 그릴 높이."""
    inner = w - 2 * PADX
    h = 2 * PAD
    if tag and not _tag_inline(inner, title, tag, title_size):
        h += LH * len(wrap(tag, inner))
    if title:
        h += HLH * len(wrap(title, inner, title_size))
    for ln in lines:
        h += 8 if ln == "" else LH * len(wrap(ln, inner))
    return h


class Svg:
    def __init__(self, w: int = W, h: int = 600):
        self.w, self.h, self.out = w, h, []

    # ── 글 ──
    def text(self, x, y, s, *, size=BODY, weight=400, color=INK, anchor="start", family=None, halo=False):
        fam = f' font-family="{family}"' if family else ""
        hl = ' paint-order="stroke" stroke="#ffffff" stroke-width="5" stroke-linejoin="round"' if halo else ""
        self.out.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" fill="{color}" '
                        f'text-anchor="{anchor}"{fam}{hl}>{escape(s)}</text>')

    def para(self, x, y, w, s, *, size=BODY, color=MUTED, weight=400, anchor="start", lh=None, halo=False, family=None) -> float:
        """y 는 첫 줄 윗선. 줄을 바꿔 쓰고 마지막 줄 아랫선을 돌려준다."""
        lh = lh or (LH if size <= BODY else round(size * 1.33))
        y_end = y
        for i, ln in enumerate(wrap(s, w, size)):
            self.text(x, y + lh * 0.76 + i * lh, ln, size=size, color=color, weight=weight, anchor=anchor, halo=halo, family=family)
            y_end = y + (i + 1) * lh
        return y_end

    def title(self, s, y=M):
        """그림 제목(넘치면 줄 바꿈). 다음 요소를 둘 y 를 돌려준다."""
        return self.para(M, y, self.w - 2 * M, s, size=TITLE, color=NAVY, weight=700, lh=26) + 10

    def note(self, y, s, *, x=M, w=None) -> float:
        """그림 아래 설명(흐린 글, 줄 바꿈)."""
        return self.para(x, y, w or (self.w - x - M), s, color=MUTED)

    # ── 상자 ──
    def rect(self, x, y, w, h, *, fill=FILL, stroke=BORDER, sw=SW, dashed=False, rx=8, opacity=None):
        dash = ' stroke-dasharray="6 4"' if dashed else ""
        op = f' fill-opacity="{opacity}"' if opacity is not None else ""
        self.out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" fill="{fill}"{op} '
                        f'stroke="{stroke}" stroke-width="{sw}"{dash}/>')

    def box(self, x, y, w, h, title, lines=(), *, tag="", strong=False, dashed=False, fill=None, center=False,
            stroke=None, tcolor=None, title_size=HEAD, valign=False) -> float:
        """h=None 이면 글에 맞춘 높이. 줄이 「·」 로 시작하면 흐린 글. 빈 줄은 8px 띄움. 그린 높이를 돌려준다."""
        need = box_h(w, title, lines, tag=tag, title_size=title_size)
        h = h or need
        self.rect(x, y, w, h, fill=fill or (ACCENT if strong else (SOFT if dashed else FILL)),
                  stroke=stroke or (NAVY if strong else (LINE if dashed else BORDER)), sw=SWS if strong else SW, dashed=dashed)
        inner = w - 2 * PADX
        cy = y + PAD + ((h - need) / 2 if (center or valign) else 0)
        tx, anc = (x + w / 2, "middle") if center else (x + PADX, "start")
        if tag:
            if _tag_inline(inner, title, tag, title_size):
                self.text(x + w - PADX, cy + HLH * 0.74, tag, color=MUTED, anchor="end")
            else:
                cy = self.para(tx, cy, inner, tag, color=MUTED, anchor=anc)
        if title:
            cy = self.para(tx, cy, inner, title, size=title_size, weight=700, lh=HLH,
                           color=tcolor or (NAVY if strong else INK), anchor=anc)
        for ln in lines:
            if ln == "":
                cy += 8
                continue
            cy = self.para(tx, cy, inner, ln, color=MUTED if ln.startswith("·") else INK, anchor=anc)
        return h

    def band(self, x, y, w, h, label):
        """점선 묶음 띠. 안쪽 내용은 y+32 부터."""
        self.rect(x, y, w, h, fill=SOFT, stroke=BORDER, dashed=True, rx=10)
        self.text(x + PADX, y + 22, label, weight=700, color=MUTED)

    # ── 선 ──
    def line(self, pts, *, dashed=False, color=LINE, sw=SWA):
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        dash = ' stroke-dasharray="6 4"' if dashed else ""
        self.out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}"{dash}/>')

    def arrow(self, pts, label="", *, dashed=False, lx=None, ly=None, color=LINE, both=False, anchor="middle", sw=SWA):
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        dash = ' stroke-dasharray="6 4"' if dashed else ""
        start = ' marker-start="url(#tail)"' if both else ""
        self.out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}"{dash} marker-end="url(#head)"{start}/>')
        if label:
            (x1, y1), (x2, y2) = pts[0], pts[-1]
            self.text(lx if lx is not None else (x1 + x2) / 2, ly if ly is not None else (y1 + y2) / 2 - 8, label,
                      color=MUTED, anchor=anchor, halo=True)

    def fit(self, bottom):
        """그림 높이를 내용 아래 끝 + 바깥 여백으로."""
        self.h = int(round(bottom + M))

    def save(self, name: str, title: str):
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{int(self.w)}" height="{int(self.h)}" viewBox="0 0 {int(self.w)} {int(self.h)}" '
                f'font-family="{FONT}"><title>{escape(title)}</title>'
                '<defs><marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">'
                f'<path d="M0,0 L10,5 L0,10 z" fill="{LINE}"/></marker>'
                '<marker id="tail" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">'
                f'<path d="M0,0 L10,5 L0,10 z" fill="{LINE}"/></marker></defs>'
                f'<rect width="{int(self.w)}" height="{int(self.h)}" fill="#ffffff"/>')
        (HERE / f"{name}.svg").write_text(head + "".join(self.out) + "</svg>\n", encoding="utf-8")


# 앱 목록(장비·공동 편집 단계)에서 온 글을 논문 용어로 맞춘다(그림에만, 앱 화면 글은 그대로).
TERMS = [("칸 글", "편집 영역 글"), ("칸 판 번호", "편집 영역 버전 번호"), ("새 판", "새 버전"), ("판 번호", "버전 번호"),
         ("관문이", "검증 단계가"), ("관문을", "검증 단계를"), ("관문은", "검증 단계는"), ("관문", "검증 단계"), ("칸 길", "편집 영역 길"), ("이 칸", "이 편집 영역"), ("그 칸", "그 편집 영역"),
         ("칸째", "편집 영역째"), ("칸을 놓음", "편집 영역을 놓음"), ("칸 안", "편집 영역 안"), ("요약」 칸", "요약」 편집 영역"),
         ("요약 칸", "요약 편집 영역"), ("문서 전체가 칸", "문서 전체가 편집 영역"), ("한 칸은", "한 편집 영역은"),
         ("다른 칸", "다른 편집 영역"), ("표 칸", "표 셀"), ("좁은 칸", "좁은 셀")]


def terms(s: str) -> str:
    for a, b in TERMS:
        s = s.replace(a, b)
    return s


# ── 1. 전체 구성(장비·연결 목록에서) ──────────────────────────────────────────


def system():
    """전체 구성 — 플랫폼 화면(개발 현황 · 처리 흐름)과 같은 장비·연결 목록(process_flow.HOSTS·LINKS)에서 그린다.
    색·짧은 이름 규칙은 app/system_diagram 의 것을 쓰고, 자리는 논문 인쇄 폭(720)에 맞춰 이 함수가 정한다."""
    import sys
    sys.path.insert(0, str(HERE.parents[2] / "src"))
    from zzaimy.app import system_diagram as sd
    from zzaimy.app.process_flow import HOSTS, LINKS
    hosts = {h["name"]: h for h in HOSTS} | {k: {"name": k, **v} for k, v in sd.EXTRA_HOSTS.items()}
    known = {"교직원 브라우저", "구글 드라이브·독스", "운영 VM", "토르 02", "토르 03", "DGX", "원본 보관 PC"}
    assert set(hosts) <= known, f"새 장비 {set(hosts) - known} — 논문 구성도 자리를 정할 것"
    s = Svg()

    # 앱 목록의 짧은 글 가운데 논문에서 다듬어 쓰는 것(장비 이름·부품 목록은 그대로)
    paper_role = {"원본 보관 PC": "사업별 폴더로 학교 실문서 보관"}
    paper_spec = {"구글 드라이브·독스": "계정을 연결한 사용자 전용"}
    paper_link = {("교직원 브라우저", "운영 VM"): "화면 요청, 기안기 문서 열기·저장, 편집 영역 글 송수신",
                  ("운영 VM", "토르 02"): "질의응답·문서 작성 요청, 27B 의 도구 선택과 답변 작성",
                  ("운영 VM", "토르 03"): "새 문서 검토와 서류 종류 판정, 검색 임베딩과 리랭킹",
                  ("DGX", "운영 VM"): "판독 결과와 원본 목록",
                  ("DGX", "토르 02·03"): "학습된 모델",
                  ("운영 VM", "구글 드라이브·독스"): "보조 경로: 계정을 연결한 사용자의 가져오기·독스 편집"}
    for n, t in paper_role.items():
        hosts[n] = {**hosts[n], "role": t}
    for n, t in paper_spec.items():
        hosts[n] = {**hosts[n], "spec": t}

    # 논문 인쇄본은 부품 이름 나열 대신 하는 일을 문장으로 적는다
    paper_lines = {"교직원 브라우저": ["AURA 화면과 웹 한글 기안기를 쓰며, 학교망(VPN)으로 접속한다"],
                   "구글 드라이브·독스": ["계정을 연결한 사용자만 쓰는 보조 경로"],
                   "운영 VM": ["플랫폼 화면과 API, 웹 한글 기안기 서버, 공동 편집 서버(SSE)를 실행한다",
                              "PostgreSQL(pgvector)에 문서와 검색 색인, 지식그래프를 저장한다", "CPU 8코어, 메모리 64GB"],
                   "토르 02": ["vLLM 으로 Writer 27B(Qwen3.8-27B)를 서빙한다", "4비트(NVFP4) 양자화"],
                   "토르 03": ["검토·판독용 27B 와 Embed v2, Rerank v1 을 서빙한다"],
                   "DGX": ["원본을 보관하고 MinerU 판독과 LoRA 학습을 한다", "통합 메모리 121GB, 디스크 3.7TB"],
                   "원본 보관 PC": ["사업별 폴더로 학교 실문서를 보관하고 DGX 로 복사한다"]}

    def lines_of(n):
        if n in paper_lines:
            return paper_lines[n]
        h = hosts[n]
        parts = h.get("parts")
        body = [" · ".join(parts)] if parts else [h.get("role", "")]
        return [terms(t) for t in body + ([f"· {h['spec']}"] if h.get("spec") else [])]

    def draw(n, x, y, w, hh=None):
        bg, fg = sd._color(n)
        return s.box(x, y, w, hh, n, lines_of(n), fill=bg, stroke=fg, tcolor=fg, dashed=n in sd.DASHED)

    label = {(a, b): paper_link.get((a, b)) or terms(sd._short(w)) for a, b, w in LINKS}
    # 1줄: 브라우저 · 구글
    y1 = M
    bw1 = 340
    gw = W - 2 * M - bw1 - 24
    h1 = max(box_h(bw1, "교직원 브라우저", lines_of("교직원 브라우저")), box_h(gw, "구글 드라이브·독스", lines_of("구글 드라이브·독스")))
    draw("교직원 브라우저", M, y1, bw1, h1)
    draw("구글 드라이브·독스", W - M - gw, y1, gw, h1)
    # 2줄: 운영 VM
    t1 = label[("교직원 브라우저", "운영 VM")]
    y2 = y1 + h1 + len(wrap(t1, 330)) * LH + 20
    h2 = draw("운영 VM", M, y2, W - 2 * M)
    _, fb = sd._color("교직원 브라우저")
    s.arrow([(M + 34, y1 + h1), (M + 34, y2)], color=fb)
    s.para(M + 48, y1 + h1 + 9, 330, t1, color=INK)
    _, fv = sd._color("운영 VM")
    gx = W - M - 40
    s.arrow([(gx, y2), (gx, y1 + h1)], dashed=True, color=fv)
    s.para(gx - 12, y1 + h1 + 9, 200, label[("운영 VM", "구글 드라이브·독스")], color=INK, anchor="end")
    # 3줄: 토르 02 · 토르 03 · DGX · 원본 보관 PC
    names = ["토르 02", "토르 03", "DGX", "원본 보관 PC"]
    cg = 12
    cw = (W - 2 * M - 3 * cg) / 4
    xs = [M + i * (cw + cg) for i in range(4)]
    _, fd = sd._color("DGX")
    labs = [(xs[0] + 18, label[("운영 VM", "토르 02")]), (xs[1] + 18, label[("운영 VM", "토르 03")])]
    lw = cw - 30
    up, down = "↑ " + label[("DGX", "운영 VM")], "↓ " + label[("운영 VM", "DGX")]
    dlw = 2 * cw + cg - 44
    n_lab = max(max(len(wrap(t, lw)) for _, t in labs), len(wrap(up, dlw)) + len(wrap(down, dlw)))
    y3 = y2 + h2 + n_lab * LH + 20
    h3 = max(box_h(cw, n, lines_of(n)) for n in names)
    for n, x in zip(names, xs):
        draw(n, x, y3, cw, h3)
    for x, t in labs:
        s.arrow([(x, y2 + h2), (x, y3)], color=fv)
        s.para(x + 10, y2 + h2 + 9, lw, t, color=INK)
    s.arrow([(xs[2] + 12, y3), (xs[2] + 12, y2 + h2)], color=fd)
    s.arrow([(xs[2] + 28, y2 + h2), (xs[2] + 28, y3)], color=fv)
    yy = s.para(xs[2] + 38, y2 + h2 + 9, dlw, up, color=INK)
    s.para(xs[2] + 38, yy, dlw, down, color=INK)
    # 아래: DGX → 토르 02·03(학습본), 원본 보관 PC → DGX
    yb = y3 + h3 + 20
    c0, c1 = xs[0] + cw / 2, xs[1] + cw / 2
    s.line([(xs[2] + 20, y3 + h3), (xs[2] + 20, yb), (c0, yb)], color=fd)
    for cx in (c0, c1):
        s.arrow([(cx, yb), (cx, y3 + h3)], color=fd)
    s.text((c0 + c1) / 2, yb + 22, label[("DGX", "토르 02·03")], color=INK, anchor="middle")
    _, fp = sd._color("원본 보관 PC")
    s.line([(xs[3] + cw / 2, y3 + h3), (xs[3] + cw / 2, yb), (xs[2] + cw - 24, yb)], color=fp)
    s.arrow([(xs[2] + cw - 24, yb), (xs[2] + cw - 24, y3 + h3)], color=fp)
    s.text(xs[3] + cw / 2 - 10, yb + 22, label[("원본 보관 PC", "DGX")], color=INK, anchor="middle")
    # 범례
    ly = yb + 40
    lead = "화살표 색: 송신 장비의 색"
    s.text(M, ly + 13, lead, weight=700, color=MUTED)
    x = M + tw(lead) + 20
    for k, name in sd.LEGEND:
        bg, fg = sd.COLOR[k]
        s.rect(x, ly, 16, 16, fill=bg, stroke=fg, rx=4)
        s.text(x + 22, ly + 13, name, color=INK)
        x += 22 + tw(name) + 14
    s.fit(ly + 18)
    s.save("01-system", "전체 프로젝트 구조: 교내 장비와 연결")


# ── 공동 편집 순서(앱 그림과 같은 단계 목록에서) ─────────────────────────────


def coedit():
    """공동 편집 순서 — 처리 흐름 화면 「공동 편집 처리 방식」 과 같은 단계 목록(app/coedit_diagram)을 받아 논문 폭으로 다시 그린다.
    값은 공동 편집 기본 설정(webhwp_coedit.DEFAULTS)."""
    import sys
    sys.path.insert(0, str(HERE.parents[2] / "src"))
    from zzaimy.app import coedit_diagram as cd
    from zzaimy.app.webhwp_coedit import DEFAULTS
    got = {}
    orig = cd._seq

    def grab(*a):
        got["args"] = a
        return ""
    cd._seq = grab
    try:
        for name, fn in (("c4-27-coedit-field", cd.field_flow), ("c4-27b-coedit-aura", cd.aura_flow)):
            got.clear()
            fn(DEFAULTS)
            _seq_paper(name, cd.COLOR, *_paper_seq(name, DEFAULTS, *got["args"]))
    finally:
        cd._seq = orig


def _when(t: str) -> str:
    """걸리는 시간 글을 논문 말로(바로 → 즉시, 멈추면 → 정지 시)."""
    for a, b in (("바로", "즉시"), (" 안", " 이내"), (" 뒤", " 후"), (" 멈추면", " 정지 시"), ("끝", "종료")):
        t = t.replace(a, b)
    return t


def _paper_seq(name, conf, title, lead, lanes, steps, bands, note):
    """앱 단계 그림의 글을 논문 문어체로 바꾼다. 단계 수·칸·시간 값은 앱 목록 그대로 두고, 수치는 같은 설정에서 다시 읽는다."""
    from zzaimy.app.coedit_diagram import _sec
    from zzaimy.app.webhwp_agent import HwpTools
    send, pause = _sec(int(conf.get("throttle_ms", 300))), _sec(int(conf.get("pause_ms", 300)))
    most = int(conf.get("commit_max", 20))
    cps = HwpTools.STREAM_CPS
    if name == "c4-27-coedit-field":
        p_lead = "다른 사용자 화면까지 약 0.2초, 한글 파일까지 몇 초 소요"
        p_lanes = {"A": "글을 작성하는 사용자", "S": "편집 영역 글을 즉시 저장", "B": "같은 문서를 연 사용자", "F": "hwpx"}
        p_steps = ["「요약」 편집 영역에 글자 입력(내 화면에는 즉시 표시)",
                   "이 편집 영역의 편집 잠금 획득(다른 사용자는 대기)",
                   f"작성 중 변경된 글자만 {send}마다 전송",
                   "받은 글을 저장하고 편집 영역 버전 번호 증가",
                   "「요약」 편집 영역의 변경 알림",
                   f"입력이 {pause} 멈추면 편집 영역에 반영하고 「김가람」 이름표 표시",
                   "글자 크기·색 등 서식은 편집 영역 단위로 별도 전송",
                   "서식을 포함해 해당 편집 영역 교체",
                   f"해당 편집 영역만 한글 파일에 저장(계속 입력하면 {most}초마다)"]
        p_bands = ["작성 중", "다른 화면에 표시", "서식 변경", "파일 저장"]
        p_note = ("제목·소제목·표 셀까지 문서 전체가 편집 영역으로 구성된다. 한 편집 영역은 한 사람씩 수정하고, "
                  "서로 다른 편집 영역은 여러 사람이 동시에 수정할 수 있다. 충돌한 글은 충돌 사본으로 보관한다.")
    else:
        p_lead = "사람과 같은 편집 영역 경로로 나누어 전송하여 열린 화면에 즉시 표시"
        p_lanes = {"A": "AURA 에 요청한 사용자", "S": "27B 문서 작업", "B": "같은 문서를 연 사용자들", "F": "hwpx"}
        p_steps = ["「AURA 양식 만들기」 요청과 가상 사업 설정 전달",
                   "27B 의 짧은 사업계획서 양식 설계(절 순서·작성방법)",
                   "빈 문서를 해당 양식으로 변환해 저장",
                   "새 버전 알림, 모든 화면에 양식 반영과 요약 편집 영역 채우기",
                   "「AURA 이어 쓰기」 요청(1절부터 차례로 세 개 절)",
                   "27B 의 절 초안 작성(설정 밖 수치·좁은 셀·안내 상자는 검증 단계에서 차단)과 다음 절 미리 준비",
                   f"편집 영역 경로로 초당 약 {cps:g}자씩 전송, 머리줄에 「2. … 쓰는 중 (2/3)」",
                   "열린 화면에 순차 표시, 대기 중인 화면은 해당 위치로 자동 이동",
                   "작성 완료 후 해당 편집 영역만 파일에 기록하고 편집 잠금 해제",
                   "「n개 절을 썼습니다」 응답(실제로 작성한 절 수)"]
        p_bands = ["양식 생성", "절 작성", "마무리"]
        p_note = "문서를 다시 여는 것은 양식을 넣을 때 한 번뿐이다. AURA 가 작성한 글도 편집 영역 안에 들어가므로 사용자가 바로 수정할 수 있다."
    assert len(p_steps) == len(steps) and len(p_bands) == len(bands) and {k for k, _, _ in lanes} == set(p_lanes), \
        f"{name}: 앱 단계 목록이 바뀜 — 논문 글(_paper_seq)도 맞출 것"
    lanes = [(k, nm, p_lanes[k]) for k, nm, _ in lanes]
    steps = [(a, b, t, _when(w)) for (a, b, _, w), t in zip(steps, p_steps)]
    bands = [(a, b, t) for (a, b, _), t in zip(bands, p_bands)]
    return title, p_lead, lanes, steps, bands, p_note


CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮"


def _seq_paper(name, color, title, lead, lanes, steps, bands, note):
    """세로 칸(사람·서버·화면·파일) + 시간 순서 화살표. 단계 글 = 「번호 글 · 걸리는 시간」 한 줄, 구간 이름은 왼쪽."""
    s = Svg()
    y = s.title(terms(title))
    y = s.para(M, y - 7, W - 2 * M, terms(lead), color=MUTED) + 6
    left = 110
    n = len(lanes)
    lx = {k: left + (W - M - left) * (i + 0.5) / n for i, (k, _, _) in enumerate(lanes)}
    lw = (W - M - left) / n - 8
    hh = max(box_h(lw, nm, [terms(d)]) for _, nm, d in lanes)
    for k, nm, d in lanes:
        bg, fg = color[k]
        s.box(lx[k] - lw / 2, y, lw, hh, nm, [terms(d)], fill=bg, stroke=fg, tcolor=fg, center=True)
    top = y + hh
    area = W - M - left - 12
    rows, cy = [], top + 6
    for i, (a, b, label, when) in enumerate(steps, 1):
        full = f"{CIRCLED[i - 1]} {terms(label)}"
        ln = wrap(f"{full} · {terms(when)}", area - (2 * PADX if a == b else 0))
        h = len(ln) * LH + (2 * 5 + 4 if a == b else 13)
        rows.append((i, a, b, ln, terms(when), cy, h))
        cy += h + 2
    bottom = cy
    for j, (a, b, nm) in enumerate(bands):
        y0 = rows[a - 1][5] - 3
        y1 = rows[b - 1][5] + rows[b - 1][6] + 1
        s.rect(M, y0, W - 2 * M, y1 - y0, fill="#f1f4f9" if j % 2 == 0 else "#fafbfd", stroke="#dfe5ee", rx=8)
        s.para(M + 6, y0 + 3, left - M + 2, nm, weight=700, color=MUTED)
    for k, _, _ in lanes:
        s.out.append(f'<line x1="{lx[k]:.1f}" y1="{top}" x2="{lx[k]:.1f}" y2="{bottom}" stroke="{color[k][1]}" stroke-opacity=".3" stroke-width="2"/>')

    def line_text(x, yb, t, when, anchor, halo):
        """마지막 줄 끝의 「· 시간」 은 갈색 굵게."""
        hl = ' paint-order="stroke" stroke="#ffffff" stroke-width="5" stroke-linejoin="round"' if halo else ""
        tail = f" · {when}"
        if t.endswith(tail):
            body = escape(t[: -len(tail)]) + f'<tspan font-weight="700" fill="#8a5a00">{escape(tail)}</tspan>'
        else:
            body = escape(t)
        s.out.append(f'<text x="{x:.1f}" y="{yb:.1f}" font-size="{BODY}" fill="{INK}" text-anchor="{anchor}"{hl}>{body}</text>')

    for i, a, b, ln, when, ry, h in rows:
        fg = color[a][1]
        wmax = max(tw(t) for t in ln)
        if a == b:
            bw = wmax + 2 * PADX + 4
            bx = min(max(lx[a] - bw / 2, left + 4), W - M - 4 - bw)
            s.rect(bx, ry + 2, bw, h - 4, fill="#ffffff", stroke=fg, sw=SWS)
            for j, t in enumerate(ln):
                line_text(bx + PADX, ry + 2 + 5 + 16 + LH * j, t, when, "start", False)
            continue
        x1, x2 = lx[a], lx[b]
        d = 1 if x2 > x1 else -1
        mid = min(max((x1 + x2) / 2, left + 6 + wmax / 2), W - M - 6 - wmax / 2)
        for j, t in enumerate(ln):
            line_text(mid, ry + 16 + LH * j, t, when, "middle", True)
        ay = ry + h - 6
        s.out.append(f'<line x1="{x1 + 6 * d:.1f}" y1="{ay:.1f}" x2="{x2 - 9 * d:.1f}" y2="{ay:.1f}" stroke="{fg}" stroke-width="2.4" '
                     f'marker-end="url(#ar{a})"/>')
    yb = s.note(bottom + 6, terms(note))
    s.out.insert(0, "<defs>" + "".join(f'<marker id="ar{k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="4" markerHeight="4" orient="auto">'
                                       f'<path d="M0,0 L10,5 L0,10 z" fill="{fg}"/></marker>' for k, (_bg, fg) in color.items()) + "</defs>")
    s.fit(yb)
    s.save(name, terms(title))


# ── README 그림 ────────────────────────────────────────────────────────────


def flow():
    s = Svg()
    y = s.title("AURA 전체 처리 흐름")
    steps = [("원본", ["PDF · HWP/HWPX", "오피스 · 이미지"]),
             ("읽기", ["텍스트 레이어 추출", "OCR과 한글 문서 변환"]),
             ("등록·분류", ["서류 종류와 사업 분류", "부서와 열람 권한 지정"]),
             ("색인·그래프", ["어휘·의미 검색 색인", "사업·문서·절 관계 그래프"]),
             ("에이전트", ["Writer 27B", "근거 검색, 초안 작성, 검토"]),
             ("결과", ["출처를 단 답변", "한글 작업 문서와 HWPX"])]
    gap = 34
    bw = (W - 2 * M - 2 * gap) / 3
    bh = max(box_h(bw, t, ln) for t, ln in steps)
    for i, (t, ln) in enumerate(steps):
        r, c = divmod(i, 3)
        x = M + c * (bw + gap)
        yy = y + r * (bh + 34)
        s.box(x, yy, bw, bh, t, ln, strong=(t == "에이전트"))
        if c < 2:
            s.arrow([(x + bw, yy + bh / 2), (x + bw + gap, yy + bh / 2)])
    xl = M + 2 * (bw + gap) + bw / 2
    s.arrow([(xl, y + bh), (xl, y + bh + 17), (M + bw / 2, y + bh + 17), (M + bw / 2, y + bh + 34)])
    y += 2 * bh + 34 + 20
    gates = [("열람 범위", "검색 단계에서 열람 권한 밖 문서를 제외한다"), ("수치 근거 대조", "근거에 없는 수치는 「○○」로 마스킹한다"),
             ("양식 모양", "원본 서식은 유지하고 내용만 채운다"), ("사람 확인", "확인되지 않은 값은 담당자에게 확인을 요청한다")]
    gw = (W - 2 * M - 2 * PADX - 16) / 2
    gh = max(LH * (1 + len(wrap(b, gw))) for _, b in gates) + 8
    bh2 = 34 + 2 * gh
    s.band(M, y, W - 2 * M, bh2, "코드로 수행하는 검증 단계")
    for i, (a, b) in enumerate(gates):
        r, c = divmod(i, 2)
        gx, gy = M + PADX + c * (gw + 16), y + 32 + r * gh
        s.text(gx, gy + 16, a, weight=700)
        s.para(gx, gy + LH, gw, b)
    s.fit(y + bh2)
    s.save("02-doc-flow", "AURA 전체 처리 흐름")


def hwp():
    s = Svg()
    y = s.title("웹 한글 기안기의 영역 단위 공동 편집") - 4
    cw = (W - 2 * M - 2 * 12) / 3
    users = [("사용자 A", ["「요약」 영역을 잠그고 쓰며 0.3초마다 변경을 보낸다"], "편집", False),
             ("사용자 B", ["다른 영역을 동시에 쓰며 A 의 이름표를 본다"], "편집", False),
             ("AURA 에이전트", ["요청받은 절을 27B 가 쓰고 잠긴 영역은 건너뛴다"], "참여자", True)]
    uh = max(box_h(cw, t, ln, tag=g) for t, ln, g, _ in users)
    for i, (t, ln, g, st) in enumerate(users):
        s.box(M + i * (cw + 12), y, cw, uh, t, ln, tag=g, strong=st)
    y2 = y + uh + 30
    lw, gap = 310, 70
    sx = M + lw + gap
    sw_ = W - M - sx
    ed = ["연결 모듈 하나가 편집 영역을 읽고 쓰고 잠근다", "편집 잠금과 버전 번호, 보낼 변경, 이름표를 관리한다",
          "보낼 변경은 브라우저에도 보관한다", "한글을 조합하는 동안에는 읽지 않는다"]
    eh = s.box(M, y2, lw, None, "편집 화면", ed, tag="브라우저마다")
    sv = ["편집 잠금은 먼저 요청한 사용자에게 주고 입력하는 동안 연장한다",
          "버전 번호가 맞지 않는 변경은 충돌 사본으로 보관한다",
          "표나 그림을 바꾸면 문서 전체를 잠근다",
          "변경은 SSE 알림으로 다른 화면에 전달한다", "AURA 가 쓴 절도 같은 경로로 나누어 보낸다"]
    shh = s.box(sx, y2, sw_, None, "AURA 서버 (운영 서버)", sv, tag="기준 데이터 보관", strong=True)
    ca = M + cw / 2
    cb = M + cw + 12 + cw / 2
    s.arrow([(ca, y + uh), (ca, y2)], both=True)
    s.arrow([(cb, y + uh), (cb, y + uh + 15), (M + lw - 40, y + uh + 15), (M + lw - 40, y2)], both=True)
    ax = M + 2 * (cw + 12) + cw / 2
    s.arrow([(ax, y + uh), (ax, y2)], "절 작성 요청", lx=ax - 8, ly=y + uh + 23, anchor="end")
    s.arrow([(M + lw, y2 + 40), (sx, y2 + 40)])
    s.para(M + lw + 3, y2 + 46, gap - 4, "잠금·변경", color=MUTED)
    s.arrow([(sx, y2 + 150), (M + lw, y2 + 150)], dashed=True)
    s.para(M + lw + 4, y2 + 156, gap - 4, "SSE 알림", color=MUTED)
    y4 = y2 + eh + 16
    fh = s.box(M, y4, lw, None, "한글 파일 (hwpx)", ["입력이 3초 멈추면 바뀐 부분만 반영한다", "AURA 가 쓴 절은 다 쓴 뒤에 반영한다", "이전 버전은 따로 보관한다"], tag="파일")
    y3 = y2 + shh + 24
    gh = s.box(sx, y3, sw_, None, "기안기 서버 (웹 한글 편집 엔진)", ["운영 서버 안에서만 접근할 수 있다"])
    s.arrow([(sx + 40, y2 + shh), (sx + 40, y3)], "중계", lx=sx + 52, ly=y2 + shh + 18, anchor="start")
    ya = min(y4 + 40, y2 + shh - 20)
    s.arrow([(sx, ya), (M + lw, ya)])
    s.para(M + lw + 4, ya + 6, gap - 4, "파일 기록", color=MUTED)
    y4 = max(y4 + fh, y3 + gh) - fh
    yb = max(y4 + fh, y3 + gh) + 6
    # 문장마다 새 줄로 — 이어 쓰면 「수정한다.」 한 낱말만 다음 줄로 넘어가 계단처럼 보였다(11-19 사용자)
    for sent in ("문서를 열 때 편집 영역이 없는 문단도 편집 영역으로 감싸, 제목과 표 셀까지 모두 편집 영역이 되게 한다.",
                 "한 편집 영역은 편집 잠금을 얻은 사용자만 고치며, 표·그림·용지 변경은 문서 전체를 한 사람씩 고친다.",
                 "충돌한 글은 지우지 않고 충돌 사본으로 보관하며, 잠금 대기 같은 시간 값은 모두 설정에서 바꿀 수 있다."):
        yb = s.note(yb, sent)
    s.fit(yb)
    s.save("03-hwp-coedit", "웹 한글 기안기 편집 영역 단위 공동 편집")


def model():
    s = Svg()
    y = s.title("모델 학습과 비교")
    top = [("학습 후보", ["실문서 양식과 완성본으로 만든 문답 쌍"]),
           ("사람 검수", ["근거와 개인정보를 확인해 승인된 데이터만 쓴다"]),
           ("LoRA SFT", ["DGX 에서 4,434쌍으로 학습한다"]),
           ("병합과 양자화", ["어댑터를 병합한 뒤 4비트로 줄인다"]),
           ("토르 02 후보 서빙", ["운영 모델과 같은 장비에서 같은 질문으로 비교한다"])]
    gap = 26
    bw = (W - 2 * M - 2 * gap) / 3
    bh = max(box_h(bw, t, ln) for t, ln in top)
    pos = [(0, 0), (0, 1), (0, 2), (1, 2), (1, 1)]
    for (t, ln), (r, c) in zip(top, pos):
        s.box(M + c * (bw + gap), y + r * (bh + 30), bw, bh, t, ln, strong=(t == "LoRA SFT"))
    yr = y + bh / 2
    for c in range(2):
        s.arrow([(M + c * (bw + gap) + bw, yr), (M + (c + 1) * (bw + gap), yr)])
    x2 = M + 2 * (bw + gap) + bw / 2
    s.arrow([(x2, y + bh), (x2, y + bh + 30)])
    yr2 = y + bh + 30 + bh / 2
    s.arrow([(M + 2 * (bw + gap), yr2), (M + bw + gap + bw, yr2)])
    y2 = y + 2 * bh + 30 + 34
    ev = ["시험 220문항에서 chrF 는 27.9에서 36.6으로 오르고, 질문에 없는 수치는 4.1%에서 0%로 줄었다",
          "그러나 문서 작성 시나리오에서는 작업 문서 생성이 2건 중 2건에서 0건으로 떨어졌다",
          "학습 데이터 형식이 에이전트 출력에 섞여 동작이 멈췄기 때문이며, 운영에는 공개 27B 모델을 그대로 쓴다"]
    pl = ["플랫폼이 실제로 보내는 입력 형식으로 에이전트 단계, 작업 메모, 채팅 답변, 절 작성을 담는다",
          "사실과 수치는 검색으로 얻고, 모델은 맥락을 구성하는 능력만 학습한다"]
    gp = 46
    hw = (W - 2 * M - gp) / 2
    ta, tb = "비교 평가와 미채택", "Writer v2 학습 데이터셋 (계획)"
    hh = max(box_h(hw, ta, ev), box_h(hw, tb, pl))
    s.box(M + hw + gp, y2, hw, hh, ta, ev, strong=True)
    s.box(M, y2, hw, hh, tb, pl, dashed=True)
    xm = M + bw + gap + bw / 2
    s.arrow([(xm, y + 2 * bh + 30), (xm, y2 - 14), (M + hw + gp + hw / 2, y2 - 14), (M + hw + gp + hw / 2, y2)])
    s.arrow([(M + hw + gp, y2 + hh / 2), (M + hw, y2 + hh / 2)])
    s.para(M + hw + 4, y2 + hh / 2 + 6, gp - 6, "다시 학습", color=MUTED)
    s.arrow([(M + bw / 2, y2), (M + bw / 2, y + bh)], dashed=True)
    yb = s.note(y2 + hh + 12, "비교 평가에서는 모델만 바꾸고 질문, 플랫폼 코드, 설정, 채점 모델은 그대로 둔다. "
                              "먼저 정답 로그확률로 학습 결과가 서빙 모델에 반영되었는지 확인한다.")
    s.fit(yb)
    s.save("04-model-pipeline", "모델 학습과 비교")


if __name__ == "__main__":
    for f in (system, flow, hwp, model, coedit):
        f()
    print("ok")
