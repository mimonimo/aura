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
    y = s.title("추측 디코딩: 예측 층이 짐작하고 본 모델이 검증한다")
    gap = 40
    bw = (W - 2 * M - gap) / 2
    xl, xr = M, M + bw + gap
    h1 = box_h(bw, "지금까지의 문맥", ["이미 확정된 토큰"], tag="입력") + 40
    h1 = max(h1, box_h(bw, "예측 층 (MTP)", ["작은 층이 다음 토큰 두 개를 미리 짐작"], tag="① 짐작") + 40)
    s.box(xl, y, bw, h1, "지금까지의 문맥", ["이미 확정된 토큰"], tag="입력")
    chip(s, xl + PADX, y + h1 - 42, 64, "사업")
    chip(s, xl + PADX + 72, y + h1 - 42, 96, "계획서의")
    s.box(xr, y, bw, h1, "예측 층 (MTP)", ["작은 층이 다음 토큰 두 개를 미리 짐작"], tag="① 짐작")
    chip(s, xr + PADX, y + h1 - 42, 72, "목표", kind="guess")
    chip(s, xr + PADX + 80, y + h1 - 42, 60, "는", kind="guess")
    s.arrow([(xl + bw, y + 40), (xr, y + 40)])
    y2 = y + h1 + 40
    h2 = box_h(bw, "본 모델", ["짐작한 두 자리를 한 번의 계산으로 확인"], tag="② 검증") + 64
    s.box(xr, y2, bw, h2, "본 모델", ["짐작한 두 자리를 한 번의 계산으로 확인"], tag="② 검증", strong=True)
    cy = y2 + h2 - 42
    for x, w, t, k, lab in ((xr + PADX, 72, "목표", "ok", "맞음"), (xr + PADX + 84, 60, "는", "no", "틀림"),
                            (xr + PADX + 156, 60, "와", "ok", "본 모델 토큰")):
        chip(s, x, cy, w, t, kind=k)
        s.text(x if k == "ok" and lab != "맞음" else x + w / 2, cy - 7, lab, color=MUTED, anchor="start" if lab == "본 모델 토큰" else "middle")
    s.arrow([(xr + bw / 2, y + h1), (xr + bw / 2, y2)], "짐작 전달", lx=xr + bw / 2 + 10, ly=y + h1 + 25, anchor="start")
    s.box(xl, y2, bw, h2, "받아들인 결과", ["한 번에 두 토큰"], tag="③ 수락")
    chip(s, xl + PADX, cy, 72, "목표", kind="ok")
    chip(s, xl + PADX + 80, cy, 60, "와", kind="ok")
    s.arrow([(xr, y2 + 40), (xl + bw, y2 + 40)])
    s.arrow([(xl + bw / 2, y2), (xl + bw / 2, y + h1)], "문맥에 덧붙이고 다시 짐작", lx=xl + bw / 2 + 10, ly=y + h1 + 25, anchor="start")
    y3 = y2 + h2 + 18
    notes = [("맞은 자리는 수락", "짐작이 본 모델과 같으면 그대로 받는다"),
             ("틀린 자리부터는 버림", "그 자리는 본 모델 토큰으로 채운다"),
             ("출력 분포는 그대로", "검증을 본 모델이 하므로 맞힌 만큼만 빨라진다")]
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
    y = s.title("LoRA: 고정 가중치 옆에 저랭크 행렬 두 개")
    # 고정 가중치
    wx, ww = 150, 270
    wh = s.box(wx, y, ww, None, "사전학습 가중치 W", ["d × d 크기, 값을 바꾸지 않음", "· 학습하지 않음"], tag="고정", fill=SOFT)
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
    s.text(ax0 + 52, ty + th + 24, "A : r × d, 학습함", anchor="middle")
    s.text(bx1 - 52, ty + th + 24, "B : d × r, 학습함", anchor="middle")
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
    yb = s.note(yl + 16, "학습 결과는 A·B 만 담은 작은 어댑터 파일로 남는다. 운영에 넣을 때는 W + BA 로 합쳐 한 모델로 쓴다.")
    s.fit(yb)
    s.save("c2-05-lora", "LoRA 구조 개념도")


def hwpx():
    s = Svg()
    y = s.title("HWPX 파일 구조와 서식 보존 채우기가 손대는 곳")
    lw = 334
    rows = [("mimetype", "첫 항목, 압축 없이 형식 표시", ""),
            ("version.xml · settings.xml", "버전 정보와 설정", ""),
            ("META-INF/", "묶음 목록(container·manifest)", ""),
            ("Contents/content.hpf", "항목 목록", "그림 넣을 때 등록"),
            ("Contents/header.xml", "글꼴·문단·테두리 모양(스타일)", "필요한 모양만 더함"),
            ("Contents/section0.xml …", "본문 구역: 문단·표·누름틀", "내용을 채움"),
            ("BinData/", "그림 바이트", "넣은 그림 추가"),
            ("Preview/", "미리보기 글·그림", "")]
    rg = 4

    def place(name, desc, touch):
        tt = touch or "그대로"
        if tw(name, mono=True) + tw(tt) + 24 <= lw - 36:
            return 1
        if tw(desc) + tw(tt) + 24 <= lw - 36:
            return 2
        return 3
    hs = [(3 if place(*r) == 3 else 2) * LH + 6 for r in rows]
    bh = 34 + sum(h + rg for h in hs) + 4
    s.band(M, y, lw, bh, "HWPX 파일 = ZIP 묶음 (OWPML)")
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
        tt = touch or "그대로"
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
    s.text(ix + iw - 10, cy + 23, "그대로", color=LINE, anchor="end")
    cy += 42
    t2 = "새 문단·표·그림 XML 을 원본 바이트 사이에 넣는다"
    h2 = 2 * 8 + LH + LH * len(wrap(t2, iw - 20))
    s.rect(ix, cy, iw, h2, stroke=NAVY, sw=SWS, dashed=True, rx=6)
    s.text(ix + 10, cy + 8 + 16, "① 절 제목 뒤에 끼워 넣기", weight=700, color=NAVY)
    s.para(ix + 10, cy + 8 + LH, iw - 20, t2)
    cy += h2 + 8
    t3 = "② 누름틀 안 글만 바꾼다 (누름틀 표시와 모양은 그대로)"
    h3 = 8 + LH + 38 + LH * len(wrap(t3, iw - 20)) + 8
    s.rect(ix, cy, iw, h3, stroke=BORDER, rx=6)
    mono(s, ix + 10, cy + 8 + 16, "<hp:p>", color=MUTED)
    s.text(ix + 72, cy + 8 + 16, "누름틀이 든 문단")
    fy = cy + 8 + LH + 4
    mono(s, ix + 10, fy + 21, "fieldBegin", size=BODY, color=MUTED)
    fx0 = ix + 10 + tw("fieldBegin", mono=True) + 6
    fx1 = ix + iw - 10 - tw("fieldEnd", mono=True) - 6
    s.rect(fx0, fy, fx1 - fx0, 30, fill=ACCENT, stroke=NAVY, sw=SWS, rx=5)
    s.text((fx0 + fx1) / 2, fy + 21, "사업명 자리", weight=700, color=NAVY, anchor="middle")
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
    s.text(ix + 10, ty + 16, "③ 표 셀 안 글만 채운다", weight=700, color=NAVY)
    s.para(ix + 10, ty + LH, iw - 20, "테두리·셀 폭·글꼴 같은 표 속성은 원본 그대로")
    yb = s.note(y + bh + 10, "구역 XML 을 다시 직렬화하지 않고 원본 바이트를 그대로 둔다. ZIP 항목 순서와 압축 방식을 지키고, 바뀐 항목만 다시 쓴다. "
                             "변환기로 새로 만든 한글 파일은 표 테두리·셀 폭·글꼴이 달라져 제출용으로 쓸 수 없어서 이 방식을 쓴다.")
    s.fit(yb)
    s.save("c2-11-hwpx", "HWPX 파일 구조와 서식 보존 채우기 위치")


def rag():
    s = Svg()
    y = s.title("검색 증강 생성(RAG): 찾고, 묶고, 쓴다")
    steps = [("질문", ["교직원의 질문", "· 질문한 사람의 권한"], ""),
             ("검색", ["질문과 가까운 문서 청크를 찾음"], "①"),
             ("근거 묶기", ["청크와 출처를 모델 입력에 넣음"], "②"),
             ("생성 모델", ["근거 안에서 답을 씀"], "③"),
             ("근거 붙은 답", ["답 + 출처 (문서·쪽·절)"], "")]
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
    ih = max(box_h(iw, "규정 색인", ["교내 규정·지침, 조문 단위"]), box_h(iw, "사업 문서 색인", ["사업 문서 청크"]))
    x1, x2 = M, M + iw + 16
    s.box(x1, y2, iw, ih, "규정 색인", ["교내 규정·지침, 조문 단위"], fill=SOFT)
    s.box(x2, y2, iw, ih, "사업 문서 색인", ["사업 문서 청크"], fill=SOFT)
    sx = M + bw + gap + bw / 2
    s.line([(x1 + iw / 2, y2), (x1 + iw / 2, y2 - 18), (x2 + iw / 2, y2 - 18), (x2 + iw / 2, y2)])
    s.arrow([(sx, y2 - 18), (sx, y + bh)])
    s.para(x2 + iw + 20, y2 + 6, W - M - x2 - iw - 20, "두 색인은 합치지 않는다. 규정 질문에 사업 청크가 섞이지 않게", color=MUTED)
    yb = s.note(y2 + ih + 16, "열람 권한은 검색 조건(SQL)에 넣는다. 답을 만든 뒤에 거르면 모델이 이미 권한 밖 자료를 읽은 뒤이기 때문이다. "
                              "모델이 학습하지 않은 최신·기관 내부 정보로 답할 수 있고, 답의 근거를 출처로 보일 수 있다.")
    s.fit(yb)
    s.save("c2-13-rag", "RAG 개념도")


def hybrid():
    s = Svg()
    y = s.title("RRF 하이브리드 검색: 두 축의 순위를 섞고 다시 줄 세운다")
    qw = 160
    bx = M + qw + 34
    bw_ = W - M - bx
    iw = (bw_ - 2 * PADX - 30 - 14) / 2
    lex = [("낱말 뽑기", ["형태소 분석(Kiwi)", "명사·영문 약칭"]), ("전문 색인 검색", ["GIN 색인", "드문 낱말에 무게(IDF)"])]
    sem = [("임베딩", ["문장 → 1,024차원 벡터(KURE 기반)"]), ("벡터 색인 검색", ["pgvector HNSW", "가까운 이웃 찾기"])]
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
    qh = box_h(qw, "질문", ["사업 약칭·규정 이름", "+ 뜻으로 찾을 말"])
    qy = (ys[0] + ys[1] + bh) / 2 - qh / 2
    s.box(M, qy, qw, qh, "질문", ["사업 약칭·규정 이름", "+ 뜻으로 찾을 말"])
    for my in mids:
        s.arrow([(M + qw, qy + qh / 2), (M + qw + 16, qy + qh / 2), (M + qw + 16, my), (bx + PADX, my)])
    y2 = ys[1] + bh + 34
    rw = [250, 220, W - 2 * M - 250 - 220 - 2 * 30]
    items = [("순위 융합 (RRF)", ["어휘 순위 + 의미 순위", "Σ 가중 / (k + 순위)", "k=60 · 어휘 0.4 · 의미 1.0"], True),
             ("리랭커", ["질문과 청크를 함께 읽어 다시 줄 세움(교차 인코더)"], False),
             ("상위 근거", ["답의 재료"], False)]
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
    s.text(xs[1] + rw[1] / 2, y2 + rh + 22, "규정 검색에서 씀", color=MUTED, anchor="middle")
    yb = s.note(y2 + rh + 34, "두 검색의 점수는 척도가 달라 그대로 더하지 않고 순위로 섞는다. 사업 문서 쪽은 어휘 1위 청크가 섞은 순위에서 밀리지 않게 지킨다.")
    s.fit(yb)
    s.save("c2-17-hybrid", "RRF 하이브리드 검색 흐름")


def ontology():
    s = Svg()
    y = s.title("AURA 온톨로지: 노드 여덟 종류와 주요 관계")
    gap = 36
    nw = (W - 2 * M - 4 * gap) / 5
    keys = ("묶음", "사업", "연차", "문서", "절")
    xs = {k: M + i * (nw + gap) for i, k in enumerate(keys)}
    mid = [("묶음", "사업군", "사업 체계의 큰 갈래", False, False), ("사업", "사업", "재정지원 사업 하나", True, False),
           ("연차", "연차", "N차년도·연도", False, False), ("문서", "문서", "계획서·실적보고서·평가 …", False, False),
           ("절", "절", "문서의 목차 항목", False, False)]
    top = [("사업", "기관", "부처·지자체·전담기관", False, False), ("문서", "문서", "같은 사업·연차의 다른 문서", False, True),
           ("절", "절", "짝 문서·다음 연차의 절", False, True)]
    bot = [("묶음", "사업", "앞·뒤 단계, 다른 사업", False, True), ("연차", "성과지표", "기준·목표·실적·달성률", False, False),
           ("절", "단위과제", "여러 문서에 되풀이되는 과제", False, False)]
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
            s.para(x + 8, gy0 + 6, lw, text, color=MUTED)
        else:
            s.arrow([(x, y_mid + hh), (x, y_bot)])
            s.para(x + 8, y_mid + hh + 6, lw, text, color=MUTED)
    vlab("사업", "주관 부처 · 전담기관")
    vlab("문서", "계획 → 실적 · 평가")
    vlab("절", "계획↔실적 짝, 연차 이어짐")
    # 아래
    s.arrow([(xs["사업"] + 14, y_mid + hh), (xs["사업"] + 14, y_bot + hh / 2), (xs["묶음"] + nw, y_bot + hh / 2)])
    s.para(xs["사업"] + 22, y_mid + hh + 6, nw - 44, "후속 · 편입 · 연관", color=MUTED)
    s.arrow([(xs["사업"] + nw - 10, y_mid + hh), (xs["사업"] + nw - 10, y_bot + 22), (xs["연차"], y_bot + 22)])
    s.text(xs["연차"] + 8, y_mid + hh + 22, "성과지표 둠", color=MUTED)
    s.arrow([(xs["문서"] + 14, y_mid + hh), (xs["문서"] + 14, y_bot + hh / 2), (xs["연차"] + nw, y_bot + hh / 2)])
    s.para(xs["문서"] + 22, y_mid + hh + 6, lw, "지표 값(측정)", color=MUTED)
    s.arrow([(xs["절"] + nw / 2, y_mid + hh), (xs["절"] + nw / 2, y_bot)])
    s.para(xs["절"] + nw / 2 - 8, y_mid + hh + 6, nw / 2 + gap - 16, "같은 단위과제", color=MUTED, anchor="end")
    yb = s.note(y_bot + hh + 12, "점선 상자는 같은 종류의 다른 노드다. 관계마다 기준(구조·분류·식별자 일치·추출·유사도)과 근거(문서·절 위치 또는 외부 출처 URL)를 달고, "
                                 "근거를 댈 수 없는 관계는 만들지 않는다. 후속·편입·주관 부처·전담기관은 외부 확인 대장에서, 연관은 문서 근거에서 온다.")
    s.fit(yb)
    s.save("c2-19-ontology", "AURA 온톨로지")


def agent():
    s = Svg()
    y = s.title("에이전트 실행 흐름: 모델이 고르고 코드가 지킨다")
    lw = 440
    gap = 56
    rx = M + lw + gap
    rw = W - M - 26 - rx
    spec = [("상태", ["요청 · 작업 메모 · 최근 대화 · 프로젝트 문서 목록", "작업본의 절 목록과 빈 절 · 받지 못한 값", "· 지금까지 한 단계"], "① 매 단계 다시 봄", False),
            ("모델이 다음 행동 고르기", ["{생각, 도구, 인자} 를 JSON 으로 (구조화 출력)"], "② 판단", True),
            ("코드 검증 단계", ["단계 수 상한 · 되돌리기 어려운 도구는 요청 글에 뜻이 있을 때만", "자료 울타리 · 열람 범위 · 수치 근거 대조"], "③ 검사", False),
            ("도구 실행", ["근거 찾기 · 문서 읽기 · 절 쓰기 · 검토 · 내보내기 · 묻기 …"], "④ 실행", False)]
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
    fh = box_h(rw, "마무리", ["답 · 작업본 · 한글 파일"], tag="끝")
    fy = ys[1] + hs[1] / 2 - fh / 2
    s.box(rx, fy, rw, fh, "마무리", ["답 · 작업본 · 한글 파일"], tag="끝", strong=True)
    s.arrow([(M + lw, ys[1] + hs[1] / 2), (rx, ys[1] + hs[1] / 2)])
    rh = box_h(rw, "결과 기록", ["결과는 자료로 돌려주고 남김"], tag="⑤ 기록")
    ry = ys[3] + hs[3] / 2 - rh / 2
    s.box(rx, ry, rw, rh, "결과 기록", ["결과는 자료로 돌려주고 남김"], tag="⑤ 기록")
    s.arrow([(M + lw, ys[3] + hs[3] / 2), (rx, ys[3] + hs[3] / 2)])
    s.arrow([(M + lw, ys[2] + hs[2] / 2), (rx + rw / 2, ys[2] + hs[2] / 2), (rx + rw / 2, ry)], dashed=True)
    s.para(rx + rw / 2 + 8, ys[2] + hs[2] / 2 + 6, rw / 2 + 10, "막으면 까닭을 남김", color=MUTED)
    lx = W - M - 8
    s.arrow([(rx + rw, ry + rh / 2), (lx, ry + rh / 2), (lx, ys[0] + 30), (M + lw, ys[0] + 30)])
    s.para(M + lw + 8, ys[0] + 38, lx - M - lw - 16, "다음 단계 (되풀이)", color=MUTED)
    s.text(rx + rw / 2, fy - 8, "마무리 고름", color=MUTED, anchor="middle")
    yb = s.note(ys[3] + hs[3] + 12, "무엇을 할지는 모델이 판단하고, 코드는 그 판단을 안전하게 실행한다. 출력 형식은 구조화 출력으로 강제한다.")
    s.fit(yb)
    s.save("c2-23-agent", "에이전트 실행 흐름")


def sse():
    s = Svg()
    y = s.title("SSE 알림 흐름: 변경은 POST 로, 알림은 밀어 주기로")
    aw, gap = 220, 120
    sx = M + aw + gap
    sw_ = W - M - sx
    srv = ["① 편집 잠금 확인", "· 이 편집 영역을 쥔 사람인가", "② 버전 번호 확인", "· 어긋나면 충돌 사본으로 둠",
           "③ 기록", "· 새 버전 번호로 저장 (정답)", "④ 알림 줄에 사건 추가"]
    shh = s.box(sx, y, sw_, None, "AURA 서버", srv, tag="정답을 쥔 곳", strong=True)
    ah = s.box(M, y, aw, None, "브라우저 A", ["편집 영역 하나를 고침", "편집 영역 글 전체 + 버전 번호"], tag="고치는 사람")
    s.arrow([(M + aw, y + 40), (sx, y + 40)])
    s.text(M + aw + gap / 2, y + 32, "POST", color=MUTED, anchor="middle", halo=True)
    s.para(M + aw + 8, y + 48, gap - 16, "편집 영역 변경", color=MUTED, anchor="start")
    s.arrow([(sx, y + 120), (M + aw, y + 120)], dashed=True)
    s.para(M + aw + 8, y + 128, gap - 16, "새 버전 번호", color=MUTED)
    y2 = y + shh + 34
    bw = 300
    chips = ["사건 41 · 편집 영역 변경", "사건 42 · 편집 잠금", "사건 43 · 편집 영역 변경"]
    notes = "HTTP 연결 하나로 서버 → 브라우저 한 방향으로 계속"
    nh = LH * len(wrap(notes, bw - 2 * PADX))
    bh = 32 + len(chips) * 38 + nh + 12
    s.band(M, y2, bw, bh, "알림 줄 (SSE)")
    for i, t in enumerate(chips):
        chip(s, M + PADX, y2 + 34 + i * 38, bw - 2 * PADX, t, kind="ok" if i == 0 else "plain")
    s.para(M + PADX, y2 + 34 + len(chips) * 38 + 2, bw - 2 * PADX, notes, color=MUTED)
    s.arrow([(sx + 40, y + shh), (sx + 40, y2 - 14), (M + bw / 2, y2 - 14), (M + bw / 2, y2)])
    cx = M + bw + 60
    cw = W - M - cx
    hb = box_h(cw, "브라우저 B", ["EventSource 로 받음", "그 편집 영역 글을 바꿔 그림"], tag="보는 사람")
    hc = box_h(cw, "브라우저 C", ["같은 사건을 받음", "끊기면 Last-Event-ID 로 이어 받기"], tag="보는 사람")
    s.box(cx, y2, cw, hb, "브라우저 B", ["EventSource 로 받음", "그 편집 영역 글을 바꿔 그림"], tag="보는 사람")
    s.box(cx, y2 + hb + 14, cw, hc, "브라우저 C", ["같은 사건을 받음", "끊기면 Last-Event-ID 로 이어 받기"], tag="보는 사람")
    s.arrow([(M + bw, y2 + hb / 2), (cx, y2 + hb / 2)])
    s.arrow([(M + bw, y2 + hb + 14 + hc / 2), (cx, y2 + hb + 14 + hc / 2)])
    yb = s.note(max(y2 + bh, y2 + hb + 14 + hc) + 12, "변경 보내기는 일반 HTTP POST, 밀어 주기는 SSE 다. 별도 양방향 라이브러리나 빌드 단계 없이 FastAPI 서버 그대로 쓴다.")
    s.fit(yb)
    s.save("c2-27-sse", "SSE 알림 흐름")


if __name__ == "__main__":
    for f in (speculative, lora, hwpx, rag, hybrid, ontology, agent, sse):
        f()
    print("ok")
