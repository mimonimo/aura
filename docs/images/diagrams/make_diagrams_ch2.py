"""논문 2장(관련 연구) 개념도 8종(SVG)을 만든다. 내용은 docs/paper/논문/02-관련-연구.md(및 04-구현.md 4.4·4.6) 기준.

    python docs/images/diagrams/make_diagrams_ch2.py      # c2-*.svg 8개를 이 폴더에 쓴다
    node docs/images/diagrams/render.mjs                  # 이 폴더의 SVG 전부 → PNG(2배)

모양·색·글꼴은 make_diagrams.py 의 Svg 를 그대로 쓴다.
"""
from __future__ import annotations

from xml.sax.saxutils import escape

from make_diagrams import ACCENT, BORDER, FILL, INK, LINE, MUTED, NAVY, SOFT, Svg

MONO = "'SF Mono', Menlo, Consolas, monospace"


def chip(s: Svg, x, y, w, label, *, kind="plain", h=28):
    """토큰·사건 같은 작은 조각. kind: plain(실선) · guess(점선) · ok(강조) · no(지운 줄)."""
    stroke = {"plain": BORDER, "guess": LINE, "ok": NAVY, "no": BORDER}[kind]
    fill = {"plain": FILL, "guess": SOFT, "ok": ACCENT, "no": SOFT}[kind]
    dash = ' stroke-dasharray="4 3"' if kind in ("guess", "no") else ""
    s.out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" '
                 f'stroke-width="{1.3 if kind == "ok" else 1}"{dash}/>')
    s.text(x + w / 2, y + h / 2 + 4.5, label, size=12.5, weight=600 if kind == "ok" else 400,
           color=MUTED if kind == "no" else (NAVY if kind == "ok" else INK), anchor="middle")
    if kind == "no":
        s.out.append(f'<line x1="{x + 8}" y1="{y + h / 2}" x2="{x + w - 8}" y2="{y + h / 2}" stroke="{MUTED}" stroke-width="1"/>')


def node(s: Svg, x, y, w, h, title, sub="", *, strong=False, dashed=False):
    """가운데 맞춤 노드(온톨로지 등)."""
    stroke = NAVY if strong else (LINE if dashed else BORDER)
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    s.out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{ACCENT if strong else (SOFT if dashed else FILL)}" '
                 f'stroke="{stroke}" stroke-width="{1.4 if strong else 1}"{dash}/>')
    cy = y + h / 2 + (5 if not sub else -3)
    s.text(x + w / 2, cy, title, size=15, weight=700, color=NAVY if strong else (MUTED if dashed else INK), anchor="middle")
    if sub:
        s.text(x + w / 2, cy + 19, sub, size=11.5, color=MUTED, anchor="middle")


def mono(s: Svg, x, y, t, *, size=12, color=INK, weight=400):
    s.out.append(f'<text x="{x}" y="{y}" font-size="{size}" font-family="{MONO}" font-weight="{weight}" fill="{color}">{escape(t)}</text>')


def speculative():
    s = Svg(1040, 420)
    s.text(28, 38, "추측 디코딩 — 예측 층이 짐작하고 본 모델이 검증한다", size=17, weight=700, color=NAVY)
    s.box(28, 76, 200, 160, "지금까지의 문맥", ["이미 확정된 토큰"], tag="입력")
    chip(s, 44, 176, 62, "사업")
    chip(s, 112, 176, 96, "계획서의")
    s.box(280, 76, 220, 160, "예측 층 (MTP)", ["작은 층이 다음 토큰", "두 개를 미리 짐작"], tag="① 짐작")
    chip(s, 298, 196, 80, "목표", kind="guess")
    chip(s, 386, 196, 80, "는", kind="guess")
    s.box(552, 76, 250, 160, "본 모델", ["짐작한 두 자리를", "한 번의 계산으로 확인"], tag="② 검증", strong=True)
    chip(s, 568, 196, 70, "목표", kind="ok")
    chip(s, 646, 196, 52, "는", kind="no")
    chip(s, 706, 196, 80, "와", kind="ok")
    s.text(603, 188, "맞음", size=11, color=MUTED, anchor="middle")
    s.text(672, 188, "틀림", size=11, color=MUTED, anchor="middle")
    s.text(746, 188, "본 모델 토큰", size=11, color=MUTED, anchor="middle")
    s.box(854, 76, 158, 160, "받아들인 결과", ["한 번에 두 토큰"], tag="③ 수락")
    chip(s, 870, 196, 60, "목표", kind="ok")
    chip(s, 936, 196, 60, "와", kind="ok")
    s.arrow([(228, 156), (280, 156)])
    s.arrow([(500, 156), (552, 156)], "짐작 전달", ly=148, lx=526)
    s.arrow([(802, 156), (854, 156)])
    s.arrow([(933, 236), (933, 272), (128, 272), (128, 236)], "문맥에 덧붙이고 다시 짐작", lx=530, ly=264)
    s.band(28, 296, 984, 82, "규칙")
    notes = [("맞은 자리는 수락", "짐작이 본 모델과 같으면 그대로 받는다"),
             ("틀린 자리부터는 버림", "그 자리는 본 모델 토큰으로 채운다"),
             ("출력 분포는 그대로", "검증을 본 모델이 하므로 맞힌 만큼만 빨라진다")]
    for i, (a, b) in enumerate(notes):
        gx = 46 + i * 330
        s.text(gx, 342, a, size=13.5, weight=700)
        s.text(gx, 363, b, size=12, color=MUTED)
    s.save("c2-04-speculative", "추측 디코딩 개념도")


def lora():
    s = Svg(920, 470)
    s.text(28, 38, "LoRA — 고정 가중치 옆에 저랭크 행렬 두 개", size=17, weight=700, color=NAVY)
    node(s, 28, 186, 100, 60, "입력 x")
    # 고정 가중치
    s.out.append(f'<rect x="230" y="70" width="270" height="130" rx="10" fill="{SOFT}" stroke="{BORDER}"/>')
    s.text(246, 92, "고정", size=11, color=MUTED)
    s.text(246, 116, "사전학습 가중치 W", size=15, weight=700)
    s.text(246, 140, "d × d 크기, 값을 바꾸지 않음", size=12.5)
    s.text(246, 160, "· 학습하지 않음", size=12.5, color=MUTED)
    # 저랭크 행렬 A(d→r), B(r→d)
    s.out.append(f'<polygon points="230,250 340,290 340,330 230,370" fill="{ACCENT}" stroke="{NAVY}" stroke-width="1.3"/>')
    s.out.append(f'<polygon points="390,290 500,250 500,370 390,330" fill="{ACCENT}" stroke="{NAVY}" stroke-width="1.3"/>')
    s.text(276, 316, "A", size=17, weight=700, color=NAVY, anchor="middle")
    s.text(454, 316, "B", size=17, weight=700, color=NAVY, anchor="middle")
    s.arrow([(340, 310), (390, 310)])
    s.text(365, 300, "r", size=13, weight=700, color=NAVY, anchor="middle")
    s.text(285, 394, "A : r × d, 학습함", size=12.5, anchor="middle")
    s.text(445, 394, "B : d × r, 학습함", size=12.5, anchor="middle")
    s.text(365, 418, "r 은 d 보다 훨씬 작다 (저랭크)", size=12, color=MUTED, anchor="middle")
    # 갈래와 합치기
    s.out.append(f'<path d="M128,216 L180,216" fill="none" stroke="{LINE}" stroke-width="1.3"/>')
    s.arrow([(180, 216), (180, 135), (230, 135)])
    s.arrow([(180, 216), (180, 310), (230, 310)])
    s.out.append(f'<circle cx="580" cy="216" r="18" fill="{FILL}" stroke="{NAVY}" stroke-width="1.3"/>')
    s.text(580, 222, "+", size=18, weight=700, color=NAVY, anchor="middle")
    s.arrow([(500, 135), (580, 135), (580, 198)], "Wx", lx=540, ly=127)
    s.arrow([(500, 310), (580, 310), (580, 234)], "BAx", lx=540, ly=302)
    s.arrow([(598, 216), (650, 216)])
    s.box(650, 166, 242, 100, "출력 h", ["h = Wx + BAx"], tag="결과", strong=True)
    s.text(28, 448, "학습 결과는 A·B 만 담은 작은 어댑터 파일로 남는다. 운영에 넣을 때는 W + BA 로 합쳐 한 모델로 쓴다.",
           size=12, color=MUTED)
    s.save("c2-05-lora", "LoRA 구조 개념도")


def hwpx():
    s = Svg(1110, 600)
    s.text(28, 38, "HWPX 파일 구조와 서식 보존 채우기가 손대는 곳", size=17, weight=700, color=NAVY)
    s.band(28, 62, 450, 470, "HWPX 파일 = ZIP 묶음 (OWPML)")
    rows = [("mimetype", "첫 항목, 압축 없이 형식 표시", ""),
            ("version.xml · settings.xml", "판 정보와 설정", ""),
            ("META-INF/", "묶음 목록(container·manifest)", ""),
            ("Contents/content.hpf", "항목 목록", "그림 넣을 때 등록"),
            ("Contents/header.xml", "글꼴·문단·테두리 모양(스타일)", "필요한 모양만 더함"),
            ("Contents/section0.xml …", "본문 구역 — 문단·표·누름틀", "내용을 채움"),
            ("BinData/", "그림 바이트", "넣은 그림 추가"),
            ("Preview/", "미리보기 글·그림", "")]
    y = 94
    for name, desc, touch in rows:
        strong = name.startswith("Contents/section")
        stroke = NAVY if strong else (LINE if touch else BORDER)
        s.out.append(f'<rect x="44" y="{y}" width="418" height="46" rx="8" fill="{ACCENT if strong else FILL}" '
                     f'stroke="{stroke}" stroke-width="{1.4 if strong else 1}"/>')
        mono(s, 58, y + 20, name, size=12.5, weight=700 if strong else 400, color=NAVY if strong else INK)
        s.text(58, y + 38, desc, size=11.5, color=MUTED)
        s.text(450, y + 28, touch or "그대로", size=11.5, weight=600 if touch else 400,
               color=NAVY if touch else LINE, anchor="end")
        y += 54
    # 구역 XML 확대
    sy = 94 + 5 * 54
    s.box(530, 62, 552, 470, "구역 XML 안 (section0.xml)", [], tag="확대", strong=True)
    s.arrow([(462, sy + 23), (530, sy + 23)])
    # ① 절 제목 문단
    s.out.append(f'<rect x="550" y="120" width="512" height="40" rx="6" fill="{FILL}" stroke="{BORDER}"/>')
    mono(s, 564, 145, "<hp:p>", color=MUTED)
    s.text(624, 145, "절 제목 문단 「1. 사업 개요」", size=12.5)
    s.text(1050, 145, "그대로", size=11.5, color=LINE, anchor="end")
    s.out.append(f'<rect x="550" y="168" width="512" height="52" rx="6" fill="{FILL}" stroke="{NAVY}" stroke-width="1.3" stroke-dasharray="5 4"/>')
    s.text(564, 190, "① 절 제목 뒤에 끼워 넣기", size=13, weight=700, color=NAVY)
    s.text(564, 209, "새 문단·표·그림 XML 을 원본 바이트 사이에 넣는다", size=12, color=MUTED)
    # ② 누름틀
    s.out.append(f'<rect x="550" y="236" width="512" height="96" rx="6" fill="{FILL}" stroke="{BORDER}"/>')
    mono(s, 564, 258, "<hp:p>", color=MUTED)
    s.text(624, 258, "누름틀이 든 문단", size=12.5)
    mono(s, 564, 292, "fieldBegin", size=11.5, color=MUTED)
    s.out.append(f'<rect x="654" y="274" width="250" height="28" rx="5" fill="{ACCENT}" stroke="{NAVY}" stroke-width="1.3"/>')
    s.text(779, 293, "사업명이 들어갈 칸", size=12.5, weight=600, color=NAVY, anchor="middle")
    mono(s, 914, 292, "fieldEnd", size=11.5, color=MUTED)
    s.text(564, 322, "② 누름틀 안 글만 바꾼다 (누름틀 표시와 모양은 그대로)", size=12, color=MUTED)
    # ③ 표
    s.out.append(f'<rect x="550" y="348" width="512" height="164" rx="6" fill="{FILL}" stroke="{BORDER}"/>')
    mono(s, 564, 370, "<hp:tbl>", color=MUTED)
    s.text(634, 370, "표 — 행(tr) · 칸(tc)", size=12.5)
    gx, gy, cw, ch = 564, 384, 120, 30
    labels = [["구분", "목표", "실적"], ["취업률", "", ""]]
    for r in range(2):
        for c in range(3):
            hit = r == 1 and c > 0
            s.out.append(f'<rect x="{gx + c * cw}" y="{gy + r * ch}" width="{cw}" height="{ch}" '
                         f'fill="{ACCENT if hit else (SOFT if r == 0 else FILL)}" stroke="{NAVY if hit else BORDER}" '
                         f'stroke-width="{1.3 if hit else 1}"/>')
            t = labels[r][c] or "칸 안 글"
            s.text(gx + c * cw + cw / 2, gy + r * ch + 20, t, size=12, weight=600 if hit else 400,
                   color=NAVY if hit else INK, anchor="middle")
    s.text(564, 470, "③ 표 칸 안 글만 채운다", size=13, weight=700, color=NAVY)
    s.text(564, 490, "테두리·칸 폭·글꼴 같은 표 속성은 원본 그대로", size=12, color=MUTED)
    s.text(28, 560, "구역 XML 을 다시 직렬화하지 않고 원본 바이트를 그대로 둔다. ZIP 항목 순서와 압축 방식을 지키고, 바뀐 항목만 다시 쓴다.",
           size=12, color=MUTED)
    s.text(28, 580, "변환기로 새로 만든 한글 파일은 표 테두리·칸 폭·글꼴이 달라져 제출용으로 쓸 수 없어서 이 방식을 쓴다.", size=12, color=MUTED)
    s.save("c2-11-hwpx", "HWPX 파일 구조와 서식 보존 채우기 위치")


def rag():
    s = Svg(1040, 400)
    s.text(28, 38, "검색 증강 생성(RAG) — 찾고, 묶고, 쓴다", size=17, weight=700, color=NAVY)
    steps = [("질문", ["교직원의 질문", "· 질문한 사람의 권한"], ""),
             ("검색", ["질문과 가까운", "문서 조각을 찾음"], "①"),
             ("근거 묶기", ["조각과 출처를", "모델 입력에 넣음"], "②"),
             ("생성 모델", ["근거 안에서", "답을 씀"], "③"),
             ("근거 붙은 답", ["답 + 출처", "(문서·쪽·절)"], "")]
    x, w, gap = 28, 172, 31
    for i, (t, ln, tag) in enumerate(steps):
        s.box(x, 76, w, 112, t, ln, tag=tag, strong=(t == "생성 모델"))
        if i < len(steps) - 1:
            s.arrow([(x + w, 132), (x + w + gap, 132)])
        x += w + gap
    # 색인
    s.box(160, 236, 220, 76, "규정 색인", ["교내 규정·지침, 조문 단위"], fill=SOFT)
    s.box(400, 236, 220, 76, "사업 문서 색인", ["사업 문서 조각"], fill=SOFT)
    s.arrow([(270, 236), (270, 214), (317, 214), (317, 188)], "")
    s.out.append(f'<path d="M510,236 L510,214 L317,214" fill="none" stroke="{LINE}" stroke-width="1.3"/>')
    s.text(640, 268, "두 색인은 합치지 않는다 —", size=12, color=MUTED)
    s.text(640, 288, "규정 질문에 사업 조각이 섞이지 않게", size=12, color=MUTED)
    s.text(28, 350, "열람 권한은 검색 조건(SQL)에 넣는다. 답을 만든 뒤에 거르면 모델이 이미 권한 밖 자료를 읽은 뒤이기 때문이다.",
           size=12, color=MUTED)
    s.text(28, 370, "모델이 학습하지 않은 최신·기관 내부 정보로 답할 수 있고, 답의 근거를 출처로 보일 수 있다.", size=12, color=MUTED)
    s.save("c2-13-rag", "RAG 개념도")


def hybrid():
    s = Svg(1180, 440)
    s.text(28, 38, "RRF 하이브리드 검색 — 두 축의 순위를 섞고 다시 줄 세운다", size=17, weight=700, color=NAVY)
    s.box(28, 166, 132, 96, "질문", ["사업 약칭·규정 이름", "+ 뜻으로 찾을 말"])
    s.band(184, 62, 412, 140, "어휘 축")
    s.band(184, 226, 412, 140, "의미 축")
    s.box(200, 92, 176, 96, "낱말 뽑기", ["형태소 분석(Kiwi)", "명사·영문 약칭"])
    s.box(404, 92, 176, 96, "전문 색인 검색", ["GIN 색인", "드문 낱말에 무게(IDF)"])
    s.box(200, 256, 176, 96, "임베딩", ["문장 → 1,024차원", "벡터(KURE 기반)"])
    s.box(404, 256, 176, 96, "벡터 색인 검색", ["pgvector HNSW", "가까운 이웃 찾기"])
    s.arrow([(160, 200), (180, 200), (180, 140), (200, 140)])
    s.arrow([(160, 228), (180, 228), (180, 304), (200, 304)])
    s.arrow([(376, 140), (404, 140)])
    s.arrow([(376, 304), (404, 304)])
    s.box(632, 150, 200, 128, "순위 융합 (RRF)", ["어휘 순위 + 의미 순위", "Σ 가중 / (k + 순위)", "k=60 · 어휘 0.4 · 의미 1.0"], strong=True)
    s.arrow([(580, 140), (606, 140), (606, 196), (632, 196)])
    s.arrow([(580, 304), (606, 304), (606, 232), (632, 232)])
    s.box(856, 150, 168, 128, "리랭커", ["질문과 조각을", "함께 읽어 다시", "줄 세움(교차 인코더)"])
    s.box(1054, 150, 100, 128, "상위 근거", ["답의", "재료"])
    s.arrow([(832, 214), (856, 214)])
    s.arrow([(1024, 214), (1054, 214)])
    s.text(940, 300, "규정 검색에서 씀", size=11.5, color=MUTED, anchor="middle")
    s.text(28, 410, "두 검색의 점수는 척도가 달라 그대로 더하지 않고 순위로 섞는다. 사업 문서 쪽은 어휘 1위 조각이 섞은 순위에서 밀리지 않게 지킨다.",
           size=12, color=MUTED)
    s.save("c2-17-hybrid", "RRF 하이브리드 검색 흐름")


def ontology():
    s = Svg(1080, 580)
    s.text(28, 38, "AURA 온톨로지 — 노드 여덟 종류와 주요 관계", size=17, weight=700, color=NAVY)
    W, H, Y = 150, 60, 236
    xs = {"묶음": 28, "사업": 238, "연차": 448, "문서": 658, "절": 868}
    node(s, xs["묶음"], Y, W, H, "사업 묶음", "사업 체계의 큰 갈래")
    node(s, xs["사업"], Y, W, H, "사업", "재정지원 사업 하나", strong=True)
    node(s, xs["연차"], Y, W, H, "연차", "N차년도·연도")
    node(s, xs["문서"], Y, W, H, "문서", "계획서·실적보고서·평가 …")
    node(s, xs["절"], Y, W, H, "절", "문서의 목차 항목")
    for a, b in (("묶음", "사업"), ("사업", "연차"), ("연차", "문서"), ("문서", "절")):
        s.arrow([(xs[a] + W, Y + 30), (xs[b], Y + 30)], "포함", ly=Y + 22)
    # 위쪽: 기관, 다른 문서, 다른 연차의 절
    node(s, xs["사업"], 80, W, H, "기관", "부처·지자체·전담기관")
    s.arrow([(xs["사업"] + 75, Y), (xs["사업"] + 75, 140)], "주관 부처 · 전담기관", lx=xs["사업"] + 160, ly=192)
    node(s, xs["문서"], 80, W, H, "문서", "같은 사업·연차의 다른 문서", dashed=True)
    s.arrow([(xs["문서"] + 75, Y), (xs["문서"] + 75, 140)], "계획 → 실적 · 평가", lx=xs["문서"] + 148, ly=192)
    node(s, xs["절"], 80, W, H, "절", "짝 문서·다음 연차의 절", dashed=True)
    s.arrow([(xs["절"] + 75, Y), (xs["절"] + 75, 140)], "계획↔실적 짝", lx=xs["절"] + 124, ly=184)
    s.text(xs["절"] + 124, 200, "연차 이어짐", size=11.5, color=MUTED, anchor="middle")
    # 아래쪽: 다른 사업, 성과지표, 단위과제
    node(s, xs["묶음"], 420, W, H, "사업", "앞·뒤 단계, 다른 사업", dashed=True)
    s.arrow([(xs["사업"] + 40, Y + H), (xs["사업"] + 40, 450), (xs["묶음"] + W, 450)], "후속 · 편입 · 연관", lx=228, ly=442)
    node(s, xs["연차"], 420, W, H, "성과지표", "기준·목표·실적·달성률")
    s.arrow([(xs["사업"] + 110, Y + H), (xs["사업"] + 110, 440), (xs["연차"], 440)], "성과지표 둠", lx=xs["사업"] + 170, ly=432)
    s.arrow([(xs["문서"] + 75, Y + H), (xs["문서"] + 75, 450), (xs["연차"] + W, 450)], "지표 값(측정)", lx=xs["문서"] + 20, ly=442)
    node(s, xs["절"], 420, W, H, "단위과제", "여러 문서에 되풀이되는 과제")
    s.arrow([(xs["절"] + 75, Y + H), (xs["절"] + 75, 420)], "같은 단위과제", lx=xs["절"] + 128, ly=364)
    s.text(28, 530, "점선 상자는 같은 종류의 다른 노드다. 관계마다 기준(구조·분류·식별자 일치·추출·유사도)과 근거(문서·절 위치 또는 외부 출처 URL)를 달고,",
           size=12, color=MUTED)
    s.text(28, 550, "근거를 댈 수 없는 관계는 만들지 않는다. 후속·편입·주관 부처·전담기관은 외부 확인 장부에서, 연관은 문서 근거에서 온다.", size=12, color=MUTED)
    s.save("c2-19-ontology", "AURA 온톨로지")


def agent():
    s = Svg(1000, 600)
    s.text(28, 38, "에이전트 실행 흐름 — 모델이 고르고 코드가 지킨다", size=17, weight=700, color=NAVY)
    s.box(28, 62, 470, 120, "상태", ["요청 · 작업 메모 · 최근 대화 · 프로젝트 문서 목록",
                                   "작업본의 절 목록과 빈 절 · 받지 못한 값",
                                   "· 지금까지 한 단계"], tag="① 매 단계 다시 봄")
    s.box(28, 204, 470, 84, "모델이 다음 행동 고르기", ["{생각, 도구, 인자} 를 JSON 으로 (구조화 출력)"], tag="② 판단", strong=True)
    s.box(28, 318, 470, 104, "코드 관문", ["단계 수 상한 · 되돌리기 어려운 도구는 요청 글에 뜻이 있을 때만",
                                       "자료 울타리 · 열람 범위 · 수치 근거 대조"], tag="③ 검사")
    s.box(28, 452, 470, 84, "도구 실행", ["근거 찾기 · 문서 읽기 · 절 쓰기 · 검토 · 내보내기 · 묻기 …"], tag="④ 실행")
    s.arrow([(263, 182), (263, 204)])
    s.arrow([(263, 288), (263, 318)])
    s.arrow([(263, 422), (263, 452)], "통과", lx=290, ly=442)
    s.box(590, 452, 240, 84, "결과 기록", ["결과는 자료로 돌려주고 남김"], tag="⑤ 기록")
    s.arrow([(498, 494), (590, 494)])
    s.arrow([(498, 370), (710, 370), (710, 452)], "막으면 까닭을 남김", dashed=True, lx=604, ly=362)
    s.arrow([(830, 494), (900, 494), (900, 122), (498, 122)], "다음 단계 (되풀이)", lx=820, ly=114)
    s.box(590, 204, 240, 84, "마무리", ["답 · 작업본 · 한글 파일"], tag="끝", strong=True)
    s.arrow([(498, 246), (590, 246)], "마무리 고름", ly=238)
    s.text(28, 570, "무엇을 할지는 모델이 판단하고, 코드는 그 판단을 안전하게 실행한다. 출력 형식은 구조화 출력으로 강제한다.", size=12, color=MUTED)
    s.save("c2-23-agent", "에이전트 실행 흐름")


def sse():
    s = Svg(1100, 430)
    s.text(28, 38, "SSE 알림 흐름 — 변경은 POST 로, 알림은 밀어 주기로", size=17, weight=700, color=NAVY)
    s.box(28, 90, 210, 120, "브라우저 A", ["칸 하나를 고침", "칸 글 전체 + 판 번호"], tag="고치는 사람")
    s.box(320, 76, 300, 280, "AURA 서버", [
        "① 임대 확인",
        "· 이 칸을 쥔 사람인가",
        "② 판 번호 확인",
        "· 어긋나면 충돌 사본으로 둠",
        "③ 기록",
        "· 새 판 번호로 저장 (정답)",
        "④ 알림 줄에 사건 추가",
    ], tag="정답을 쥔 곳", strong=True)
    s.arrow([(238, 130), (320, 130)], "POST 칸 변경", ly=122)
    s.arrow([(320, 180), (238, 180)], "새 판 번호", dashed=True, ly=172)
    s.band(668, 76, 170, 280, "알림 줄 (SSE)")
    chip(s, 684, 112, 138, "사건 41 · 칸 변경", kind="ok")
    chip(s, 684, 148, 138, "사건 42 · 임대")
    chip(s, 684, 184, 138, "사건 43 · 칸 변경")
    s.text(684, 240, "HTTP 연결 하나로", size=12, color=MUTED)
    s.text(684, 258, "서버 → 브라우저", size=12, color=MUTED)
    s.text(684, 276, "한 방향으로 계속", size=12, color=MUTED)
    s.arrow([(620, 300), (668, 300)])
    s.box(886, 76, 190, 120, "브라우저 B", ["EventSource 로 받음", "그 칸 글을 바꿔 그림"], tag="보는 사람")
    s.box(886, 236, 190, 120, "브라우저 C", ["같은 사건을 받음", "끊기면 Last-Event-ID 로", "이어 받기"], tag="보는 사람")
    s.arrow([(838, 136), (886, 136)])
    s.arrow([(838, 296), (886, 296)])
    s.text(28, 400, "변경 보내기는 일반 HTTP POST, 밀어 주기는 SSE 다. 별도 양방향 라이브러리나 빌드 단계 없이 FastAPI 서버 그대로 쓴다.",
           size=12, color=MUTED)
    s.save("c2-27-sse", "SSE 알림 흐름")


if __name__ == "__main__":
    for f in (speculative, lora, hwpx, rag, hybrid, ontology, agent, sse):
        f()
    print("ok")
