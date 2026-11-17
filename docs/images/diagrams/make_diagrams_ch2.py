"""논문 2장(관련 연구) 개념도 8종(SVG)을 만든다. 내용은 docs/paper/논문/02-관련-연구.md(및 04-구현.md 4.4·4.6) 기준.

    python docs/images/diagrams/make_diagrams_ch2.py      # c2-*.svg 8개를 이 폴더에 쓴다
    node docs/images/diagrams/render.mjs                  # 이 폴더의 SVG 전부 → PNG(3배)

모양·색·글꼴·인쇄 기준(폭 720, 본문 16px = 9.4pt, 높이 630 이하)은 make_diagrams.py 의 Svg 를 그대로 쓴다.
"""
from __future__ import annotations

from make_diagrams import (ACCENT, BODY, BORDER, FILL, HEAD, INK, LH, LINE, M, MONO, MUTED, NAVY, PAD, PADX, SOFT, SW, SWS,
                           W, Svg, box_h, tw, wrap)


def chip(s: Svg, x, y, w, label, *, kind="plain", h=30):
    """토큰·사건 같은 작은 조각. kind: plain(실선) · guess(점선) · ok(강조) · no(지운 줄)."""
    stroke = {"plain": BORDER, "guess": LINE, "ok": NAVY, "no": BORDER}[kind]
    fill = {"plain": FILL, "guess": SOFT, "ok": ACCENT, "no": SOFT}[kind]
    s.rect(x, y, w, h, fill=fill, stroke=stroke, sw=SWS if kind == "ok" else SW, dashed=kind in ("guess", "no"), rx=6)
    s.text(x + w / 2, y + h / 2 + 5.5, label, weight=700 if kind == "ok" else 400,
           color=MUTED if kind == "no" else (NAVY if kind == "ok" else INK), anchor="middle")
    if kind == "no":
        s.out.append(f'<line x1="{x + 6}" y1="{y + h / 2}" x2="{x + w - 6}" y2="{y + h / 2}" stroke="{MUTED}" stroke-width="1.6"/>')


def node(s: Svg, x, y, w, h, title, sub="", *, strong=False, dashed=False):
    """가운데 맞춤 노드(온톨로지 등). h=None 이면 글에 맞춘 높이. 그린 높이를 돌려준다."""
    return s.box(x, y, w, h, title, [sub] if sub else [], strong=strong, dashed=dashed, center=True,
                 tcolor=NAVY if strong else (MUTED if dashed else INK))


def mono(s: Svg, x, y, t, *, size=BODY, color=INK, weight=400, anchor="start"):
    s.text(x, y, t, size=size, color=color, weight=weight, family=MONO, anchor=anchor)


def speculative():
    s = Svg()
    y = s.title("MTP 추측 디코딩 과정")
    gap = 40
    bw = (W - 2 * M - gap) / 2
    xl, xr = M, M + bw + gap
    h1 = box_h(bw, "지금까지의 문맥", ["이미 확정된 토큰"], tag="입력") + 40
    h1 = max(h1, box_h(bw, "예측 층 (MTP)", ["작은 층이 다음 토큰 두 개를 미리 제안"], tag="① 제안") + 40)
    s.box(xl, y, bw, h1, "지금까지의 문맥", ["이미 확정된 토큰"], tag="입력")
    chip(s, xl + PADX, y + h1 - 42, 64, "사업")
    chip(s, xl + PADX + 72, y + h1 - 42, 96, "계획서의")
    s.box(xr, y, bw, h1, "예측 층 (MTP)", ["작은 층이 다음 토큰 두 개를 미리 제안"], tag="① 제안")
    chip(s, xr + PADX, y + h1 - 42, 72, "목표", kind="guess")
    chip(s, xr + PADX + 80, y + h1 - 42, 60, "는", kind="guess")
    s.arrow([(xl + bw, y + 40), (xr, y + 40)])
    y2 = y + h1 + 40
    h2 = box_h(bw, "본 모델", ["제안된 두 자리를 한 번의 계산으로 확인"], tag="② 검증") + 64
    s.box(xr, y2, bw, h2, "본 모델", ["제안된 두 자리를 한 번의 계산으로 확인"], tag="② 검증", strong=True)
    cy = y2 + h2 - 42
    for x, w, t, k, lab in ((xr + PADX, 72, "목표", "ok", "일치"), (xr + PADX + 84, 60, "는", "no", "불일치"),
                            (xr + PADX + 156, 60, "와", "ok", "본 모델 토큰으로 대체")):
        chip(s, x, cy, w, t, kind=k)
        s.text(x if k == "ok" and lab != "일치" else x + w / 2, cy - 7, lab, color=MUTED, anchor="start" if lab == "본 모델 토큰으로 대체" else "middle")
    s.arrow([(xr + bw / 2, y + h1), (xr + bw / 2, y2)], "후보 전달", lx=xr + bw / 2 + 10, ly=y + h1 + 25, anchor="start")
    s.box(xl, y2, bw, h2, "수락된 결과", ["한 번의 계산으로 두 토큰 확정"], tag="③ 수락")
    chip(s, xl + PADX, cy, 72, "목표", kind="ok")
    chip(s, xl + PADX + 80, cy, 60, "와", kind="ok")
    s.arrow([(xr, y2 + 40), (xl + bw, y2 + 40)])
    s.arrow([(xl + bw / 2, y2), (xl + bw / 2, y + h1)], "문맥에 추가한 뒤 다음 제안", lx=xl + bw / 2 + 10, ly=y + h1 + 25, anchor="start")
    y3 = y2 + h2 + 18
    notes = [("일치한 토큰 수락", "제안이 본 모델의 예측과 같으면 그대로 채택"),
             ("불일치 이후 폐기", "불일치 위치는 본 모델 토큰으로 대체"),
             ("출력 분포 유지", "본 모델 검증으로 출력은 동일, 일치한 만큼 속도 향상")]
    bh = 34 + len(notes) * LH + 8
    s.band(M, y3, W - 2 * M, bh, "규칙")
    for i, (a, b) in enumerate(notes):
        ty = y3 + 34 + i * LH + 16
        s.text(M + PADX, ty, a, weight=700)
        s.text(M + PADX + 180, ty, b, color=MUTED)
    s.fit(y3 + bh)
    s.save("c2-04-speculative", "추측 디코딩 개념도")


def lora():
    s = Svg()
    y = s.title("LoRA 구조")
    # 고정 가중치
    wx, ww = 150, 270
    wh = s.box(wx, y, ww, None, "사전학습 가중치 W", ["d × d 크기의 원래 가중치로, 학습하는 동안 값을 바꾸지 않는다"], tag="고정", fill=SOFT)
    yw = y + wh / 2
    # 저랭크 A, B
    ty = y + wh + 34
    th = 110
    ax0, ax1 = wx, wx + 105
    bx0, bx1 = wx + ww - 105, wx + ww
    s.out.append(f'<polygon points="{ax0},{ty} {ax1},{ty + 35} {ax1},{ty + th - 35} {ax0},{ty + th}" fill="{ACCENT}" stroke="{NAVY}" stroke-width="{SWS}"/>')
    s.out.append(f'<polygon points="{bx0},{ty + 35} {bx1},{ty} {bx1},{ty + th} {bx0},{ty + th - 35}" fill="{ACCENT}" stroke="{NAVY}" stroke-width="{SWS}"/>')
    ym = ty + th / 2
    s.text(ax0 + 42, ym + 7, "A", size=20, weight=700, color=NAVY, anchor="middle")
    s.text(bx1 - 42, ym + 7, "B", size=20, weight=700, color=NAVY, anchor="middle")
    s.arrow([(ax1, ym), (bx0, ym)])
    s.text((ax1 + bx0) / 2, ym - 10, "r", size=18, weight=700, color=NAVY, anchor="middle")
    s.text(ax0 + 52, ty + th + 24, "A : r × d, 학습 대상", anchor="middle")
    s.text(bx1 - 52, ty + th + 24, "B : d × r, 학습 대상", anchor="middle")
    yl = s.para(wx, ty + th + 34, ww + 40, "r 은 d 보다 훨씬 작다 (저랭크)", color=MUTED)
    # 입력 · 갈래
    ih = node(s, M, (yw + ym) / 2 - 30, 92, 60, "입력 x")
    iy = (yw + ym) / 2
    s.line([(M + 92, iy), (M + 112, iy)])
    s.arrow([(M + 112, iy), (M + 112, yw), (wx, yw)])
    s.arrow([(M + 112, iy), (M + 112, ym), (ax0, ym)])
    # 합치기
    cx = wx + ww + 70
    s.out.append(f'<circle cx="{cx}" cy="{iy}" r="20" fill="{FILL}" stroke="{NAVY}" stroke-width="{SWS}"/>')
    s.text(cx, iy + 8, "+", size=22, weight=700, color=NAVY, anchor="middle")
    s.arrow([(wx + ww, yw), (cx, yw), (cx, iy - 20)], "Wx", lx=wx + ww + 30, ly=yw - 8)
    s.arrow([(bx1, ym), (cx, ym), (cx, iy + 20)], "BAx", lx=wx + ww + 32, ly=ym - 8)
    ox = cx + 40
    oh = box_h(W - M - ox, "출력 h", ["h = Wx + BAx"], tag="결과")
    s.box(ox, iy - oh / 2, W - M - ox, oh, "출력 h", ["h = Wx + BAx"], tag="결과", strong=True)
    s.arrow([(cx + 20, iy), (ox, iy)])
    yb = s.note(yl + 16, "학습 결과는 A와 B만 담은 작은 어댑터 파일로 저장된다. 운영에 적용할 때는 W + BA 로 병합하여 하나의 모델로 사용한다.")
    s.fit(yb)
    s.save("c2-05-lora", "LoRA 구조 개념도")


def hwpx():
    s = Svg()
    y = s.title("HWPX 파일 구조")
    lw = 334
    rows = [("mimetype", "압축하지 않은 첫 항목, 파일 형식 표시", ""),
            ("version.xml · settings.xml", "버전 정보와 설정", ""),
            ("META-INF/", "패키지 구성 목록(container·manifest)", ""),
            ("Contents/content.hpf", "항목 목록", "그림 추가 시 등록"),
            ("Contents/header.xml", "글꼴·문단·테두리 모양(스타일)", "필요한 스타일만 추가"),
            ("Contents/section0.xml …", "본문 구역: 문단·표·누름틀", "내용 채우기"),
            ("BinData/", "그림 데이터", "삽입한 그림 추가"),
            ("Preview/", "미리보기 텍스트와 이미지", "")]
    rg = 4

    def place(name, desc, touch):
        tt = touch or "변경 없음"
        if tw(name, mono=True) + tw(tt) + 24 <= lw - 36:
            return 1
        if tw(desc) + tw(tt) + 24 <= lw - 36:
            return 2
        return 3
    hs = [(3 if place(*r) == 3 else 2) * LH + 6 for r in rows]
    bh = 34 + sum(h + rg for h in hs) + 4
    s.band(M, y, lw, bh, "HWPX 파일: ZIP 패키지 (OWPML)")
    ry = y + 34
    sec_y = sec_h = 0
    for (name, desc, touch), rh in zip(rows, hs):
        strong = name.startswith("Contents/section")
        if strong:
            sec_y, sec_h = ry, rh
        s.rect(M + 8, ry, lw - 16, rh, fill=ACCENT if strong else FILL, stroke=NAVY if strong else (LINE if touch else BORDER),
               sw=SWS if strong else SW)
        mono(s, M + 18, ry + 19, name, weight=700 if strong else 400, color=NAVY if strong else INK)
        s.text(M + 18, ry + 19 + LH, desc, color=MUTED)
        tt = touch or "변경 없음"
        k = place(name, desc, touch)
        s.text(M + lw - 18 if k < 3 else M + 18, ry + 19 + LH * (k - 1), tt, weight=700 if touch else 400,
               color=NAVY if touch else LINE, anchor="end" if k < 3 else "start")
        ry += rh + rg
    # 구역 XML 확대
    rx = M + lw + 30
    rw = W - M - rx
    s.box(rx, y, rw, bh, "구역 XML 안 (section0.xml)", [], tag="확대", strong=True)
    s.arrow([(M + lw - 8, sec_y + sec_h / 2), (rx, sec_y + sec_h / 2)])
    ix, iw = rx + 10, rw - 20
    cy = y + 44
    s.rect(ix, cy, iw, 34, stroke=BORDER, rx=6)
    mono(s, ix + 10, cy + 23, "<hp:p>", color=MUTED)
    s.text(ix + 72, cy + 23, "절 제목 문단", color=INK)
    s.text(ix + iw - 10, cy + 23, "변경 없음", color=LINE, anchor="end")
    cy += 42
    t2 = "새 문단·표·그림 XML 을 원본 바이트 사이에 삽입"
    h2 = 2 * 8 + LH + LH * len(wrap(t2, iw - 20))
    s.rect(ix, cy, iw, h2, stroke=NAVY, sw=SWS, dashed=True, rx=6)
    s.text(ix + 10, cy + 8 + 16, "① 절 제목 뒤 삽입", weight=700, color=NAVY)
    s.para(ix + 10, cy + 8 + LH, iw - 20, t2)
    cy += h2 + 8
    t3 = "② 누름틀 안의 글만 교체 (누름틀 표시와 서식은 유지)"
    h3 = 8 + LH + 38 + LH * len(wrap(t3, iw - 20)) + 8
    s.rect(ix, cy, iw, h3, stroke=BORDER, rx=6)
    mono(s, ix + 10, cy + 8 + 16, "<hp:p>", color=MUTED)
    s.text(ix + 72, cy + 8 + 16, "누름틀을 포함한 문단")
    fy = cy + 8 + LH + 4
    mono(s, ix + 10, fy + 21, "fieldBegin", size=BODY, color=MUTED)
    fx0 = ix + 10 + tw("fieldBegin", mono=True) + 6
    fx1 = ix + iw - 10 - tw("fieldEnd", mono=True) - 6
    s.rect(fx0, fy, fx1 - fx0, 30, fill=ACCENT, stroke=NAVY, sw=SWS, rx=5)
    s.text((fx0 + fx1) / 2, fy + 21, "사업명 입력란", weight=700, color=NAVY, anchor="middle")
    mono(s, ix + iw - 10, fy + 21, "fieldEnd", color=MUTED, anchor="end")
    s.para(ix + 10, fy + 38, iw - 20, t3)
    cy += h3 + 8
    h4 = y + bh - 10 - cy
    s.rect(ix, cy, iw, h4, stroke=BORDER, rx=6)
    mono(s, ix + 10, cy + 8 + 16, "<hp:tbl>", color=MUTED)
    s.text(ix + 10 + tw("<hp:tbl>", mono=True) + 8, cy + 8 + 16, "표: 행(tr) · 셀(tc)")
    gx, gy = ix + 10, cy + 8 + LH + 6
    cw, ch = (iw - 20) / 3, 30
    labels = [["구분", "목표", "실적"], ["취업률", "", ""]]
    for r in range(2):
        for c in range(3):
            hit = r == 1 and c > 0
            s.rect(gx + c * cw, gy + r * ch, cw, ch, fill=ACCENT if hit else (SOFT if r == 0 else FILL),
                   stroke=NAVY if hit else BORDER, sw=SWS if hit else SW, rx=0)
            s.text(gx + c * cw + cw / 2, gy + r * ch + 21, labels[r][c] or "셀 안 글", weight=700 if hit else 400,
                   color=NAVY if hit else INK, anchor="middle")
    ty = gy + 2 * ch + 6
    s.text(ix + 10, ty + 16, "③ 표 셀 안의 글만 채우기", weight=700, color=NAVY)
    s.para(ix + 10, ty + LH, iw - 20, "테두리·셀 폭·글꼴 등 표 속성은 원본 유지")
    yb = s.note(y + bh + 10, "구역 XML 을 다시 직렬화하지 않고 원본 바이트를 그대로 유지한다. ZIP 항목 순서와 압축 방식을 보존하고 바뀐 항목만 다시 기록한다. "
                             "변환기로 새로 생성한 한글 파일은 표 테두리·셀 폭·글꼴이 달라져 제출용으로 사용할 수 없으므로 이 방식을 택하였다.")
    s.fit(yb)
    s.save("c2-11-hwpx", "HWPX 파일 구조와 서식 보존 채우기 위치")


def rag():
    s = Svg()
    y = s.title("검색 증강 생성(RAG) 과정")
    steps = [("질문", ["교직원의 질문과 질문자의 열람 권한"], ""),
             ("검색", ["질문과 관련된 문서 청크 검색"], "①"),
             ("근거 구성", ["청크와 출처를 모델 입력에 포함"], "②"),
             ("생성 모델", ["근거 범위 안에서 답변 생성"], "③"),
             ("근거를 단 답변", ["문서와 쪽, 절 위치를 출처로 단 답변"], "")]
    gap = 18
    bw = (W - 2 * M - 4 * gap) / 5
    bh = max(box_h(bw, t, ln, tag=g) for t, ln, g in steps)
    for i, (t, ln, g) in enumerate(steps):
        x = M + i * (bw + gap)
        s.box(x, y, bw, bh, t, ln, tag=g, strong=(t == "생성 모델"))
        if i < 4:
            s.arrow([(x + bw, y + bh / 2), (x + bw + gap, y + bh / 2)])
    y2 = y + bh + 40
    iw = 200
    ih = max(box_h(iw, "규정 색인", ["조문 단위로 나눈 교내 규정·지침"]), box_h(iw, "사업 문서 색인", ["사업 문서 청크"]))
    x1, x2 = M, M + iw + 16
    s.box(x1, y2, iw, ih, "규정 색인", ["조문 단위로 나눈 교내 규정·지침"], fill=SOFT)
    s.box(x2, y2, iw, ih, "사업 문서 색인", ["사업 문서 청크"], fill=SOFT)
    sx = M + bw + gap + bw / 2
    s.line([(x1 + iw / 2, y2), (x1 + iw / 2, y2 - 18), (x2 + iw / 2, y2 - 18), (x2 + iw / 2, y2)])
    s.arrow([(sx, y2 - 18), (sx, y + bh)])
    s.para(x2 + iw + 20, y2 + 6, W - M - x2 - iw - 20, "규정 질문에 사업 문서 청크가 섞이지 않도록 두 색인을 분리한다.", color=MUTED)
    yb = s.note(y2 + ih + 16, "열람 권한은 검색 조건(SQL)에 포함한다. 답변을 생성한 뒤에 거르면 모델이 이미 권한 밖 자료를 읽은 상태가 된다. "
                              "RAG 를 사용하면 모델이 학습하지 않은 최신 정보와 기관 내부 정보로 답할 수 있고, 답변의 근거를 출처로 제시할 수 있다.")
    s.fit(yb)
    s.save("c2-13-rag", "RAG 개념도")


def hybrid():
    s = Svg()
    y = s.title("RRF 하이브리드 검색 과정")
    qw = 160
    bx = M + qw + 34
    bw_ = W - M - bx
    iw = (bw_ - 2 * PADX - 30 - 14) / 2
    lex = [("단어 추출", ["형태소 분석기(Kiwi)로 명사와 영문 약칭을 뽑는다"]), ("단어 색인 검색", ["같은 단어가 든 청크를 찾고, 드문 단어일수록 높은 점수를 준다"])]
    sem = [("임베딩", ["질문을 1,024차원 벡터로 바꾼다(KURE 기반)"]), ("벡터 색인 검색", ["pgvector 색인에서 의미가 가장 가까운 청크를 찾는다"])]
    ih = max(box_h(iw, t, ln) for t, ln in lex + sem)
    bh = 32 + ih + 10
    ys = [y, y + bh + 12]
    mids = []
    for (name, items), by in zip((("어휘 축", lex), ("의미 축", sem)), ys):
        s.band(bx, by, bw_, bh, name)
        for j, (t, ln) in enumerate(items):
            s.box(bx + PADX + j * (iw + 30), by + 32, iw, ih, t, ln)
        my = by + 32 + ih / 2
        s.arrow([(bx + PADX + iw, my), (bx + PADX + iw + 30, my)])
        mids.append(my)
    qh = box_h(qw, "질문", ["사업 약칭이나 규정 이름과 의미로 찾을 표현이 섞인 질문"])
    qy = (ys[0] + ys[1] + bh) / 2 - qh / 2
    s.box(M, qy, qw, qh, "질문", ["사업 약칭이나 규정 이름과 의미로 찾을 표현이 섞인 질문"])
    for my in mids:
        s.arrow([(M + qw, qy + qh / 2), (M + qw + 16, qy + qh / 2), (M + qw + 16, my), (bx + PADX, my)])
    y2 = ys[1] + bh + 34
    rw = [250, 220, W - 2 * M - 250 - 220 - 2 * 30]
    items = [("순위 융합 (RRF)", ["두 검색의 순위를 점수로 바꾸어 가중 합산한다(어휘 0.4, 의미 1.0)"], True),
             ("리랭커", ["질문과 청크를 함께 읽어 순위를 다시 매긴다"], False),
             ("상위 근거", ["답변 생성에 사용할 상위 청크"], False)]
    rh = max(box_h(w, t, ln) for w, (t, ln, _) in zip(rw, items))
    x = M
    xs = []
    for w, (t, ln, st) in zip(rw, items):
        s.box(x, y2, w, rh, t, ln, strong=st)
        xs.append(x)
        x += w + 30
    for i in range(2):
        s.arrow([(xs[i] + rw[i], y2 + rh / 2), (xs[i + 1], y2 + rh / 2)])
    ex = bx + bw_ - 8
    s.line([(bx + PADX + iw + 30 + iw, mids[0]), (ex, mids[0]), (ex, mids[1])])
    s.line([(bx + PADX + iw + 30 + iw, mids[1]), (ex, mids[1])])
    s.arrow([(ex, mids[1]), (ex, y2 - 16), (M + rw[0] / 2, y2 - 16), (M + rw[0] / 2, y2)])
    s.text(xs[1] + rw[1] / 2, y2 + rh + 22, "규정 검색에만 적용", color=MUTED, anchor="middle")
    yb = s.note(y2 + rh + 34, "두 검색의 점수는 척도가 달라 그대로 더하지 않고 순위로 융합한다. 사업 문서 검색에서는 어휘 검색 1위 청크가 융합 순위에서 밀려나지 않도록 보장한다.")
    s.fit(yb)
    s.save("c2-17-hybrid", "RRF 하이브리드 검색 흐름")


def ontology():
    s = Svg()
    y = s.title("AURA 온톨로지 구조")
    gap = 36
    nw = (W - 2 * M - 4 * gap) / 5
    keys = ("묶음", "사업", "연차", "문서", "절")
    xs = {k: M + i * (nw + gap) for i, k in enumerate(keys)}
    mid = [("묶음", "사업군", "사업 체계의 상위 분류", False, False), ("사업", "사업", "재정지원 사업 하나", True, False),
           ("연차", "연차", "N차년도·연도", False, False), ("문서", "문서", "계획서·실적보고서·평가 …", False, False),
           ("절", "절", "문서의 목차 항목", False, False)]
    top = [("사업", "기관", "부처·지자체·전담기관", False, False), ("문서", "문서", "같은 사업·연차의 다른 문서", False, True),
           ("절", "절", "대응 문서나 다음 연차의 절", False, True)]
    bot = [("묶음", "사업", "앞·뒤 단계 사업, 연관 사업", False, True), ("연차", "성과지표", "기준·목표·실적·달성률", False, False),
           ("절", "단위과제", "여러 문서에 반복되는 과제", False, False)]
    hh = max(box_h(nw, t, [sub]) for _, t, sub, _, _ in mid + top + bot)
    y_top = y
    y_mid = y_top + hh + 3 * LH + 12
    y_bot = y_mid + hh + 3 * LH + 12
    for row, yy in ((top, y_top), (mid, y_mid), (bot, y_bot)):
        for k, t, sub, st, da in row:
            node(s, xs[k], yy, nw, hh, t, sub, strong=st, dashed=da)
    for a, b in (("묶음", "사업"), ("사업", "연차"), ("연차", "문서"), ("문서", "절")):
        s.arrow([(xs[a] + nw, y_mid + hh / 2), (xs[b], y_mid + hh / 2)])
        s.text(xs[a] + nw + gap / 2, y_mid + hh / 2 - 9, "포함", color=MUTED, anchor="middle", halo=True)
    gy0, gy1 = y_top + hh, y_mid
    lw = nw + gap - 22

    def vlab(k, text, up=True, dx=14):
        x = xs[k] + dx
        if up:
            s.arrow([(x, gy1), (x, gy0)])
            s.para(x + 8, gy0 + 6, min(lw, W - M - x - 12), text, color=MUTED)
        else:
            s.arrow([(x, y_mid + hh), (x, y_bot)])
            s.para(x + 8, y_mid + hh + 6, lw, text, color=MUTED)
    vlab("사업", "주관 부처와 전담기관")
    vlab("문서", "계획서와 실적·평가 문서 대응")
    vlab("절", "계획·실적 대응, 다음 연차 연결")
    # 아래
    s.arrow([(xs["사업"] + 14, y_mid + hh), (xs["사업"] + 14, y_bot + hh / 2), (xs["묶음"] + nw, y_bot + hh / 2)])
    s.para(xs["사업"] + 22, y_mid + hh + 6, nw - 44, "후속·편입·연관 사업", color=MUTED)
    s.arrow([(xs["사업"] + nw - 10, y_mid + hh), (xs["사업"] + nw - 10, y_bot + 22), (xs["연차"], y_bot + 22)])
    s.text(xs["연차"] + 8, y_mid + hh + 22, "연차별 성과지표", color=MUTED)
    s.arrow([(xs["문서"] + 14, y_mid + hh), (xs["문서"] + 14, y_bot + hh / 2), (xs["연차"] + nw, y_bot + hh / 2)])
    s.para(xs["문서"] + 22, y_mid + hh + 6, lw, "문서에서 측정한 지표 값", color=MUTED)
    s.arrow([(xs["절"] + nw / 2, y_mid + hh), (xs["절"] + nw / 2, y_bot)])
    s.para(xs["절"] + nw / 2 - 8, y_mid + hh + 6, nw / 2 + gap - 16, "다루는 단위과제", color=MUTED, anchor="end")
    yb = s.note(y_bot + hh + 12, "점선 상자는 같은 종류의 다른 노드다. 관계마다 기준(구조·분류·식별자 일치·추출·유사도)과 근거(문서·절 위치 또는 외부 출처 URL)를 기록하며, "
                                 "근거를 제시할 수 없는 관계는 생성하지 않는다. 후속·편입·주관 부처·전담기관 관계는 외부 확인 대장에서, 연관 관계는 문서 근거에서 도출한다.")
    s.fit(yb)
    s.save("c2-19-ontology", "AURA 온톨로지")


def agent():
    s = Svg()
    y = s.title("에이전트 실행 흐름")
    lw = 440
    gap = 56
    rx = M + lw + gap
    rw = W - M - 26 - rx
    spec = [("상태", ["요청, 작업 메모, 최근 대화, 프로젝트 문서 목록", "작업 문서의 절 목록, 빈 절, 아직 받지 못한 값", "지금까지 수행한 단계"], "①", False),
            ("모델의 다음 행동 선택", ["{생각, 도구, 인자} 를 구조화 출력(JSON)으로 생성"], "②", True),
            ("코드 검증 단계", ["단계 수에 상한을 두고, 되돌리기 어려운 도구는 요청에 그 의도가 있을 때만 허용한다", "열람 범위를 확인하고 답변의 수치를 근거와 대조한다"], "③", False),
            ("도구 실행", ["근거 검색, 문서 읽기, 절 작성, 검토, 내보내기, 사용자에게 묻기 등"], "④", False)]
    ys, hs = [], []
    cy = y
    for t, ln, g, st in spec:
        h = s.box(M, cy, lw, None, t, ln, tag=g, strong=st)
        ys.append(cy)
        hs.append(h)
        cy += h + 24
    cx = M + lw / 2
    for i in range(3):
        s.arrow([(cx, ys[i] + hs[i]), (cx, ys[i + 1])], "통과" if i == 2 else "", lx=cx + 10, ly=ys[i + 1] - 6, anchor="start")
    # 오른쪽
    fh = box_h(rw, "마무리", ["답변이나 작업 문서, 한글 파일을 출력한다"], tag="종료")
    fy = ys[1] + hs[1] / 2 - fh / 2
    s.box(rx, fy, rw, fh, "마무리", ["답변이나 작업 문서, 한글 파일을 출력한다"], tag="종료", strong=True)
    s.arrow([(M + lw, ys[1] + hs[1] / 2), (rx, ys[1] + hs[1] / 2)])
    rh = box_h(rw, "결과 기록", ["도구 결과를 자료로 돌려주고 기록한다"], tag="⑤")
    ry = ys[3] + hs[3] / 2 - rh / 2
    s.box(rx, ry, rw, rh, "결과 기록", ["도구 결과를 자료로 돌려주고 기록한다"], tag="⑤")
    s.arrow([(M + lw, ys[3] + hs[3] / 2), (rx, ys[3] + hs[3] / 2)])
    s.arrow([(M + lw, ys[2] + hs[2] / 2), (rx + rw / 2, ys[2] + hs[2] / 2), (rx + rw / 2, ry)], dashed=True)
    s.para(rx + rw / 2 + 8, ys[2] + hs[2] / 2 + 6, rw / 2 + 10, "차단하면 그 사유를 기록", color=MUTED)
    lx = W - M - 8
    s.arrow([(rx + rw, ry + rh / 2), (lx, ry + rh / 2), (lx, ys[0] + 30), (M + lw, ys[0] + 30)])
    s.para(M + lw + 8, ys[0] + 38, lx - M - lw - 16, "다음 단계로 반복", color=MUTED)
    s.text(rx + rw / 2, fy - 8, "끝낼 때", color=MUTED, anchor="middle")
    yb = s.note(ys[3] + hs[3] + 12, "무엇을 할지는 모델이 판단하고, 코드는 그 판단을 안전하게 실행한다. 출력 형식은 구조화 출력으로 강제한다.")
    s.fit(yb)
    s.save("c2-23-agent", "에이전트 실행 흐름")


def sse():
    s = Svg()
    y = s.title("SSE 알림 흐름")
    aw, gap = 220, 120
    sx = M + aw + gap
    sw_ = W - M - sx
    srv = ["① 요청자가 편집 잠금을 가졌는지 확인한다", "② 버전 번호가 다르면 충돌 사본으로 보관한다",
           "③ 새 버전 번호로 저장한다", "④ 알림 스트림에 이벤트를 추가한다"]
    shh = s.box(sx, y, sw_, None, "AURA 서버", srv, tag="기준 데이터 보관", strong=True)
    ah = s.box(M, y, aw, None, "브라우저 A", ["편집 영역 하나를 고친 뒤 그 글 전체와 버전 번호를 보낸다"], tag="편집자")
    s.arrow([(M + aw, y + 40), (sx, y + 40)])
    s.text(M + aw + gap / 2, y + 32, "POST", color=MUTED, anchor="middle", halo=True)
    s.para(M + aw + 8, y + 48, gap - 16, "편집 영역 변경", color=MUTED, anchor="start")
    s.arrow([(sx, y + 120), (M + aw, y + 120)], dashed=True)
    s.para(M + aw + 8, y + 128, gap - 16, "새 버전 번호", color=MUTED)
    y2 = y + shh + 34
    bw = 300
    chips = ["이벤트 41: 편집 영역 변경", "이벤트 42: 편집 잠금", "이벤트 43: 편집 영역 변경"]
    notes = "하나의 HTTP 연결을 열어 두고 서버가 브라우저로 이벤트를 계속 보내는 단방향 스트림이다."
    nh = LH * len(wrap(notes, bw - 2 * PADX))
    bh = 32 + len(chips) * 38 + nh + 12
    s.band(M, y2, bw, bh, "알림 스트림 (SSE)")
    for i, t in enumerate(chips):
        chip(s, M + PADX, y2 + 34 + i * 38, bw - 2 * PADX, t, kind="ok" if i == 0 else "plain")
    s.para(M + PADX, y2 + 34 + len(chips) * 38 + 2, bw - 2 * PADX, notes, color=MUTED)
    s.arrow([(sx + 40, y + shh), (sx + 40, y2 - 14), (M + bw / 2, y2 - 14), (M + bw / 2, y2)])
    cx = M + bw + 60
    cw = W - M - cx
    B_ = ["이벤트를 받아 해당 편집 영역의 글을 새로 표시한다"]
    C_ = ["같은 이벤트를 받고, 연결이 끊기면 마지막으로 받은 이벤트 다음부터 이어 받는다"]
    hb = box_h(cw, "브라우저 B", B_, tag="열람자")
    hc = box_h(cw, "브라우저 C", C_, tag="열람자")
    s.box(cx, y2, cw, hb, "브라우저 B", B_, tag="열람자")
    s.box(cx, y2 + hb + 14, cw, hc, "브라우저 C", C_, tag="열람자")
    s.arrow([(M + bw, y2 + hb / 2), (cx, y2 + hb / 2)])
    s.arrow([(M + bw, y2 + hb + 14 + hc / 2), (cx, y2 + hb + 14 + hc / 2)])
    yb = s.note(max(y2 + bh, y2 + hb + 14 + hc) + 12, "변경 전송은 일반 HTTP POST 로, 알림 전송은 SSE 로 처리한다. 별도의 양방향 통신 라이브러리나 빌드 단계 없이 FastAPI 서버만으로 구현한다.")
    s.fit(yb)
    s.save("c2-27-sse", "SSE 알림 흐름")


if __name__ == "__main__":
    for f in (speculative, lora, hwpx, rag, hybrid, ontology, agent, sse):
        f()
    print("ok")
