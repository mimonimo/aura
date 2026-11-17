"""논문 3장(환경·설계)·4장(구현)의 도식 13종(SVG)을 만든다. 내용은 docs/paper/논문/03-환경-설계.md·04-구현.md 의
글 상자 그림과 그 앞뒤 본문 기준이다(본문에 없는 부품·수치는 넣지 않는다).

    python docs/images/diagrams/make_diagrams_ch34.py     # c3-*.svg · c4-*.svg 13개를 이 폴더에 쓴다
    node docs/images/diagrams/render.mjs                  # 이 폴더의 SVG 전부 → PNG(3배)

모양·색·글꼴·인쇄 기준(폭 720, 본문 16px = 9.4pt, 높이 630 이하)은 make_diagrams.py 의 Svg,
작은 조각·노드는 make_diagrams_ch2.py 의 chip·node 를 그대로 쓴다.
"""
from __future__ import annotations

from make_diagrams import (ACCENT, BODY, BORDER, FILL, HEAD, HLH, INK, LH, LINE, M, MUTED, NAVY, PAD, PADX, SOFT, SW, SWS,
                           W, Svg, box_h, tw, wrap)
from make_diagrams_ch2 import mono, node

GAP = 18   # 상자 사이 세로 간격


def ptw(label, bold=False):
    """조각 폭: 글 폭(어림의 0.97배, 실측에 가깝게, 굵은 글은 5 % 더) + 좌우 9px."""
    return tw(label) * (1.02 if bold else 0.97) + 18


def pill(s: Svg, x, y, label, *, kind="plain", h=30, w=None):
    """글 길이에 맞춘 조각. kind: plain · ok(강조) · soft(옅은 바탕). 너비를 돌려준다."""
    w = w or ptw(label, kind == "ok")
    s.rect(x, y, w, h, fill={"plain": FILL, "ok": ACCENT, "soft": SOFT}[kind], stroke=NAVY if kind == "ok" else BORDER,
           sw=SWS if kind == "ok" else SW, rx=6)
    bold = kind == "ok" and ptw(label, True) <= w
    s.text(x + w / 2, y + h / 2 + 5.5, label, weight=700 if bold else 400, color=NAVY if kind == "ok" else INK, anchor="middle")
    return w


def row_h(w, detail, name_w):
    return LH * len(wrap(detail, w - name_w - 2 * PADX)) + 10


def step_row(s: Svg, x, y, w, name, detail, *, name_w=150, strong=False, h=None):
    """왼쪽 단계 이름 + 오른쪽 설명(줄 바꿈). 그린 높이를 돌려준다."""
    nl = wrap(name, name_w - 2 * PADX, HEAD - 1)
    h = h or max(row_h(w, detail, name_w), len(nl) * 22 + 10)
    s.rect(x, y, w, h, fill=FILL, stroke=BORDER)
    s.rect(x, y, name_w, h, fill=ACCENT if strong else SOFT, stroke=NAVY if strong else BORDER, sw=SWS if strong else SW)
    ny = y + h / 2 - len(nl) * 22 / 2
    for i, t in enumerate(nl):
        s.text(x + PADX, ny + 16.5 + i * 22, t, size=HEAD - 1, weight=700, color=NAVY)
    dl = wrap(detail, w - name_w - 2 * PADX)
    dy = y + h / 2 - len(dl) * LH / 2
    for i, t in enumerate(dl):
        s.text(x + name_w + PADX, dy + 16 + i * LH, t)
    return h


# ── 3장 ──────────────────────────────────────────────────────────────


def layers():
    """[그림 3-1] 계층 구조."""
    spec = [
        ("화면층", "업무 화면과 개발자 화면", [("채팅", 0), ("라이브러리", 0), ("프로젝트", 0), ("한글 문서함(기안기)", 0),
                                  ("조직·구성원", 0), ("사업 관리", 0), ("업무 도우미 창", 0), ("|", 0), ("개발자 화면(/dev · 원본 관리)", 1)]),
        ("앱층", "라우터 · 대화 · 에이전트", [("영역별 라우터(문서 · 프로젝트 · 채팅 · 기안기 · 조직 · 개발 …)", 0),
                                   ("답변기(responder)", 1), ("문서 작업 에이전트(doc_agent)", 1), ("업무 도우미", 1),
                                   ("권한 판정(access_policy · grades · rag_spaces)", 0), ("문맥 맞추기(llm_budget)", 0)]),
        ("도메인층", "처리 단계별 패키지", [("ingest(읽기 · 개인정보 · 분류)", 0), ("→", 0), ("index(청크 · 색인)", 0), ("→", 0),
                                 ("retrieve(하이브리드)", 0), ("graph(온톨로지 · 지식 그래프)", 0), ("generate(모델 연결 · 생성)", 0),
                                 ("verify(검증기)", 0), ("dataset(학습 후보 · 검수 · 반출)", 0), ("export(산출 문서)", 0)]),
        ("저장층", "DB 와 문서 폴더", [("PostgreSQL(문서 · 청크 · 대장 · 그래프 · 권한 · 기록)", 1), ("+ pgvector(사업 문서 벡터)", 0),
                                ("문서 폴더(수집 · 첨부 · 생성 · 기안기)", 0), ("설정 · 감사 파일", 0)]),
        ("모델층", "모두 교내 장비", [("Writer 27B × 2(토르 02 · 03)", 1), ("Embed v2", 0), ("Rerank v1(토르 03)", 0)]),
    ]
    s = Svg()
    y = s.title("AURA 계층 구조: 화면 · 앱 · 도메인 · 저장 · 모델")
    lx = M + 104
    right = W - M - 8
    ph, pg = 30, 5
    for name, sub, items in spec:
        # 조각 흘려 놓기(→ 는 다음 조각과 같은 줄에)
        rows, cur, x = [], [], lx
        i = 0
        while i < len(items):
            lab, k = items[i]
            if lab == "→":
                w = 22 + ptw(items[i + 1][0], bool(items[i + 1][1]))
                if x + w > right:
                    rows.append(cur)
                    cur, x = [], lx
                cur.append((lab, k, 22))
                x += 22
                i += 1
                continue
            w = 16 if lab == "|" else ptw(lab, bool(k))
            if x + w > right and cur:
                rows.append(cur)
                cur, x = [], lx
            if lab == "|" and not cur:
                i += 1
                continue
            cur.append((lab, k, w))
            x += w + 6
            i += 1
        rows.append(cur)
        sl = wrap(sub, lx - M - 22)
        h = max(len(rows) * (ph + pg) - pg + 16, 24 + len(sl) * LH + 18)
        s.rect(M, y, W - 2 * M, h, fill=SOFT, stroke=BORDER, rx=10)
        ty = y + h / 2 - (24 + len(sl) * LH) / 2
        s.text(M + PADX, ty + 18, name, size=HEAD, weight=700, color=NAVY)
        for j, t in enumerate(sl):
            s.text(M + PADX, ty + 24 + 16 + j * LH, t, color=MUTED)
        ry = y + h / 2 - (len(rows) * (ph + pg) - pg) / 2
        for r in rows:
            x = lx
            for lab, k, w in r:
                if lab == "→":
                    s.arrow([(x, ry + ph / 2), (x + 18, ry + ph / 2)])
                    x += 22
                    continue
                if lab == "|":
                    s.out.append(f'<line x1="{x + 4}" y1="{ry + 2}" x2="{x + 4}" y2="{ry + ph - 2}" stroke="{LINE}" stroke-width="2" stroke-dasharray="3 3"/>')
                    x += 16
                    continue
                pill(s, x, ry, lab, kind="ok" if k else "plain", h=ph)
                x += w + 6
            assert x - 8 <= right + 1, (name, x)
            ry += ph + pg
        y += h + 6
    yb = s.note(y + 4, "구축 파이프라인(배치)과 질의 파이프라인(대화형)은 저장층에서만 만난다.")
    s.fit(yb)
    s.save("c3-01-layers", "AURA 계층 구조")


def build_flow():
    """[그림 3-2] 구축 흐름."""
    s = Svg()
    y = s.title("구축 흐름: 수집에서 색인 · 그래프까지")
    gap = 26
    bw = (W - 2 * M - 2 * gap) / 3
    top = [("학교 원본", ["학습 장비 · 읽기 전용 연결"], SOFT, False),
           ("원본 대장", ["archive_files · 원본 하나가 한 줄", "· 10분 주기 목록 비교: 새로 · 바뀜 · 옮김 · 없어짐"], None, False),
           ("업로드 · 채팅 첨부", ["구글 드라이브 가져오기"], SOFT, False)]
    tws = [200, W - 2 * M - 2 * gap - 200 - 166, 166]
    th = max(box_h(w, t, ln) for w, (t, ln, _, _) in zip(tws, top))
    tx = [M, M + tws[0] + gap, M + tws[0] + tws[1] + 2 * gap]
    for x, w, (t, ln, f, st) in zip(tx, tws, top):
        s.box(x, y, w, th, t, ln, fill=f, strong=st)
    s.arrow([(tx[0] + tws[0], y + th / 2), (tx[1], y + th / 2)])
    y2 = y + th + 22
    steps = [("① 형식별 읽기", "디지털 PDF 텍스트 레이어 · MinerU · 27B 이미지 판독 · kordoc · hwpx 파서 · 오피스"),
             ("② 표 정리", "셀 격자 [행, 열, 병합, 머리 셀, 글] → 화면용 HTML 표 / 검색용 평문"),
             ("③ 개인정보", "Presidio + 한국형 인식기 8종, 「내부 마스킹 설정」은 항목별(기본 꺼짐)"),
             ("④ 청크 나누기", "블록 단위(최대 2,000자), 규정은 조문 단위, 쪽 번호 · 절 제목 경로"),
             ("⑤ 판정", "서류 종류(27B, 토르 03) · 사업 분류(대장 + 27B) · 문서 이름(본문 제목)"),
             ("⑥ 버전 묶기", "제목 열쇠 + 8자 단위 문자열 겹침(0.95 이상 같은 내용, 그 아래 버전)")]
    iw, nw = W - 2 * M - 2 * PADX, 136
    rhs = [row_h(iw, b, nw) for _, b in steps]
    bh = 34 + sum(rhs) + 5 * 6 + 10
    s.rect(M, y2, W - 2 * M, bh, fill=ACCENT, stroke=NAVY, sw=SWS, rx=10)
    s.text(M + PADX, y2 + 24, "하나의 처리기", size=HEAD, weight=700, color=NAVY)
    s.text(M + PADX + tw("하나의 처리기", HEAD) + 12, y2 + 24, "DocumentProcessor: 모든 길이 거침", color=MUTED)
    for x, w in zip(tx[1:], tws[1:]):
        s.arrow([(x + w / 2, y + th), (x + w / 2, y2)])
    ry = y2 + 34
    for (a, b), rh in zip(steps, rhs):
        step_row(s, M + PADX, ry, iw, a, b, name_w=nw, h=rh)
        ry += rh + 6
    xs = [M + i * (bw + gap) for i in range(3)]
    y3 = y2 + bh + 24
    outs = [("어휘 색인", ["Kiwi 명사 · IDF", "규정 / 사업 문서(GIN) 따로"]),
            ("의미 색인", ["Embed v2 → pgvector HNSW", "1분 주기"]),
            ("지식 그래프", ["사업 > 연차 > 문서 > 절", "계획↔실적 · 앞뒤 단계 · SHACL 검사", "· 15분 안 다시 지음"])]
    oh = max(box_h(bw, t, ln) for t, ln in outs)
    for i, (x, (t, ln)) in enumerate(zip(xs, outs)):
        s.box(x, y3, bw, oh, t, ln, strong=(i == 2))
        s.arrow([(W / 2, y2 + bh), (W / 2, y3 - 12), (x + bw / 2, y3 - 12), (x + bw / 2, y3)])
    s.fit(y3 + oh)
    s.save("c3-02-build-flow", "구축 흐름")


def query_flow():
    """[그림 3-3] 질의 흐름."""
    s = Svg()
    y = s.title("질의 흐름: 검색 → 생성 → 검증")
    g = 14
    w1, w2 = 120, 120
    w3 = W - 2 * M - w1 - w2 - 2 * 26
    a = [("질문 · 요청", []), ("입력 정리", []), ("권한 점검", ["개인정보 요청 차단 · RAG 공간 결정 · 감사 기록"])]
    ha = max(box_h(w, t, ln) for w, (t, ln) in zip((w1, w2, w3), a))
    x = M
    for w, (t, ln) in zip((w1, w2, w3), a):
        s.box(x, y, w, ha, t, ln, valign=True)
        if x > M:
            s.arrow([(x - 26, y + ha / 2), (x, y + ha / 2)])
        x += w + 26
    cw = (W - 2 * M - 2 * g) / 3
    cx = [M + i * (cw + g) for i in range(3)]
    cm = [c + cw / 2 for c in cx]
    yb = y + ha + 18
    hb = s.box(M, yb, W - 2 * M, None, "문서 작업 에이전트", [], tag="Writer 27B 가 단계마다 도구를 고름", strong=True)
    s.arrow([(W - M - w3 / 2, y + ha), (W - M - w3 / 2, yb)])
    yc = yb + hb + 22
    heads = [("질문 읽기(27B)", ["사업 · 연도 · 종류 · 관계"]), ("근거 찾기 · 이어 읽기", []), ("양식 고르기 · 설계", ["절 쓰기"])]
    hc = max(box_h(cw, t, ln) for t, ln in heads)
    for c, (t, ln) in zip(cx, heads):
        s.box(c, yc, cw, hc, t, ln, valign=True)
    for m in cm:
        s.arrow([(m, yb + hb), (m, yc)])
    yd = yc + hc + 13
    lane = cx[2] + cw - 22
    sw2 = lane - 18 - cx[1]
    hd = max(box_h(cw, "그래프 범위 정하기", []), box_h(sw2, "규정 검색 ∥ 사업 문서 검색", ["어휘 + 의미 RRF, 규정은 리랭커까지"]))
    s.box(cx[0], yd, cw, hd, "그래프 범위 정하기", [], valign=True)
    s.box(cx[1], yd, sw2, hd, "규정 검색 ∥ 사업 문서 검색", ["어휘 + 의미 RRF, 규정은 리랭커까지"])
    s.arrow([(cm[0], yc + hc), (cm[0], yd)])
    s.arrow([(cm[1], yc + hc), (cm[1], yd)])
    s.arrow([(cx[0] + cw, yd + hd / 2), (cx[1], yd + hd / 2)])
    ye = yd + hd + 13
    he = box_h(2 * cw + g, "근거 묶기 · 문맥 한도 맞추기", [])
    s.box(cx[0], ye, 2 * cw + g, he, "근거 묶기 · 문맥 한도 맞추기", [])
    s.box(cx[2], ye, cw, he, "Writer 27B 생성", [], strong=True)
    s.arrow([(cm[1], yd + hd), (cm[1], ye)])
    s.arrow([(cx[1] + cw, ye + he / 2), (cx[2], ye + he / 2)])
    s.arrow([(lane, yc + hc), (lane, ye)])
    yf = ye + he + 13
    s.band(M, yf, W - 2 * M, 32 + 30 + 10, "검증")
    px = M + PADX
    for t in ["수치 검증: 근거 밖 숫자 표시 · 「○○」 마스킹", "양식 검증 단계", "자가 점검"]:
        px += pill(s, px, yf + 32, t) + 10
    s.arrow([(cm[2], ye + he), (cm[2], yf)])
    yg = yf + 72 + 16
    hw = (W - 2 * M - g) / 2
    hg = max(box_h(hw, "출처 달린 답", ["채팅 화면"]), box_h(hw, "한글 작업본(기안기) · 독스 작업본", ["→ 원본 서식 완성본"]))
    s.box(M, yg, hw, hg, "출처 달린 답", ["채팅 화면"], strong=True)
    s.box(M + hw + g, yg, hw, hg, "한글 작업본(기안기) · 독스 작업본", ["→ 원본 서식 완성본"], strong=True)
    s.arrow([(M + hw / 2, yf + 72), (M + hw / 2, yg)])
    s.arrow([(M + hw + g + hw / 2, yf + 72), (M + hw + g + hw / 2, yg)])
    yb2 = s.note(yg + hg + 8, "권한은 답을 만든 뒤가 아니라 검색 단계의 SQL 조건으로 건다. 화면 출처는 모델에 실제로 건넨 근거 목록에서만 만든다.")
    s.fit(yb2)
    s.save("c3-03-query-flow", "질의 흐름")


def folders():
    """[그림 3-4] 1 층 문서 폴더 배치."""
    rows = [(1, "반입/<연도>/<유형>/<접수번호> <제목>/원본.<확장자>", "수집 원본과 추출 부산물"),
            (1, "첨부/<연도>/대화-<번호>/<시각> <파일명>", "채팅 첨부"),
            (1, "생성/<연도>/<접수번호>/<시각> <종류>.<확장자>", "초안 · 판독 · 복원 · 완성본 사본"),
            (1, "보고/주간/<월요일>.md", "주간 보고"),
            (1, "기안기/<부서>/<폴더>/<제목>.hwpx", "한글 문서함(부서 디렉터리)"),
            (2, ".판/ · .휴지통/", "부서마다 저장 전 버전과 지운 문서"),
            (2, "공통/공통 양식/<목적>/", "어디서나 쓰는 빈 양식"),
            (2, "공통/지난 사업 문서/<사업>/<목적>/", "사업 전용 양식"),
            (2, "~<아이디>/", "내 폴더(처음 열 때 생김)")]
    s = Svg()
    y = s.title("1 층 문서 폴더 배치: 문서마다 접수번호와 제목으로 된 폴더")
    s.rect(M, y, 130, 32, fill=ACCENT, stroke=NAVY, sw=SWS, rx=6)
    mono(s, M + 12, y + 22, "documents/", weight=700, color=NAVY)
    s.text(W - M - 8, y + 22, "하는 일", weight=700, color=MUTED, anchor="end")
    y0 = y + 32
    ry = y0 + 10
    mids = []
    for lv, path, desc in rows:
        x0 = M + 18 if lv == 1 else M + 58
        bx = x0 + 22
        bw = W - M - bx
        one = tw(path, mono=True) + tw(desc) + 40 <= bw
        rh = 34 if one else 34 + LH - 4
        s.rect(bx, ry, bw, rh, fill=FILL if lv == 1 else SOFT, stroke=BORDER, rx=6)
        mono(s, bx + 10, ry + 23, path, weight=700 if lv == 1 else 400)
        s.text(W - M - 10, ry + 23 + (0 if one else LH - 4), desc, color=MUTED, anchor="end")
        s.line([(x0, ry + 17), (bx, ry + 17)], sw=1.6)
        mids.append((lv, ry + 17))
        ry += rh + 6
    last1 = max(m for lv, m in mids if lv == 1)
    last2 = max(m for lv, m in mids if lv == 2)
    s.line([(M + 18, y0), (M + 18, last1)], sw=1.6)
    s.line([(M + 58, last1 + 17), (M + 58, last2)], sw=1.6)
    yb = s.note(ry + 4, "파일의 실제 자리는 DB 의 파일 대장이 가리킨다. 폴더를 통째로 옮길 때는 경로 앞부분만 바꾸면 된다. "
                        "경로 규칙은 app/paths.py 한 곳에서만 나오고, 새 수집 지점은 원본 채택 함수(storage.adopt_original)를 거친다.")
    s.fit(yb)
    s.save("c3-04-folders", "1 층 문서 폴더 배치")


def access():
    """[그림 3-5] 검색 단계의 권한 적용."""
    s = Svg()
    y = s.title("검색 단계의 권한 적용: 답을 만들기 전에 SQL 조건으로")
    h0 = s.box(M, y, W - 2 * M, None, "로그인 계정", ["역할(교직원 · 학생 · 관리자) · 소속 부서 · 추가 권한 부서 · 등급"])
    lw = 236
    rx = M + lw + 30
    rw = W - M - rx
    ry = y + h0 + 26
    h1 = s.box(rx, ry, rw, None, "rag_spaces.resolve: 검색 공간", ["student / dept:<부서> / staff / dev"])
    s.arrow([(rx + rw / 2, y + h0), (rx + rw / 2, ry)])
    ry2 = ry + h1 + GAP
    h2 = s.box(rx, ry2, rw, None, "access_policy.doc_sql: 검색 SQL 의 WHERE", ["열람 등급 · 부서 · 종류 제외"])
    s.arrow([(rx + rw / 2, ry + h1), (rx + rw / 2, ry2)])
    ry3 = ry2 + h2 + 26
    hw = (rw - 14) / 2
    a = ("규정 검색", ["열람 등급"])
    b = ("사업 문서 검색", ["그래프 범위 · 절 축 청크", "· 공간 부서 · 증빙 종류 제외 · 다시 거름"])
    h3 = max(box_h(hw, *a), box_h(hw, *b))
    s.box(rx, ry3, hw, h3, *a)
    s.box(rx + hw + 14, ry3, hw, h3, *b)
    for cx in (rx + hw / 2, rx + hw + 14 + hw / 2):
        s.arrow([(rx + rw / 2, ry2 + h2), (rx + rw / 2, ry2 + h2 + 13), (cx, ry2 + h2 + 13), (cx, ry3)])
    ry4 = ry3 + h3 + 26
    h4 = s.box(rx, ry4, rw, None, "모델에 건넨 근거 = 화면 출처", ["같은 목록에서 만든다"], strong=True)
    for cx in (rx + hw / 2, rx + hw + 14 + hw / 2):
        s.arrow([(cx, ry3 + h3), (cx, ry4)])
    # 범위 밖 부서
    hx = box_h(lw, "질문이 범위 밖 부서를 가리킴", [])
    s.box(M, ry2 + h2 / 2 - hx / 2, lw, hx, "질문이 범위 밖 부서를 가리킴", [], dashed=True, tcolor=INK)
    s.arrow([(M + lw / 2, y + h0), (M + lw / 2, ry2 + h2 / 2 - hx / 2)])
    s.box(M, ry3, lw, None, "「열람 권한 없음」 안내", ["+ 감사 기록", "· 부서 이름만 싣고 제목 · 건수는 말하지 않음"])
    s.arrow([(M + lw / 2, ry2 + h2 / 2 + hx / 2), (M + lw / 2, ry3)])
    yb = s.note(ry4 + h4 + 10, "모르는 역할은 가장 좁은 학생 범위로 본다. 같은 규칙을 화면 판정 함수와 SQL 조건 두 꼴로 내고, 모든 화면과 검색이 이것을 쓴다.")
    s.fit(yb)
    s.save("c3-05-access", "검색 단계의 권한 적용")


def model_swap():
    """[그림 3-7] 용도별 연결과 모델 교체 구조."""
    s = Svg()
    y = s.title("용도별 연결과 모델 교체 구조: 모델 이름이 아니라 용도로 부른다")
    lw = 268
    h1 = s.box(M, y, lw, None, "호출하는 쪽", ["답변기 · 에이전트 · 판정 · 도우미", "· 용도 이름만 넘김", "· answer / review / vision / helper"])
    y2 = y + h1 + 26
    h2 = s.box(M, y2, lw, None, "연결 설정", ["llm_connections", "서버 · 모델 · 추론 모드 끄기 인자", "구조화 출력 방식 · 동시 상한",
                                          "· 「도구 · 모델 연결」(/dev/tools)"], strong=True)
    s.arrow([(M + lw / 2, y + h1), (M + lw / 2, y2)])
    rules = [("문맥 한도", "서버가 알려 주는 max_model_len 먼저, 없으면 설정값"),
             ("구조화 출력", "JSON 스키마 강제, 서버가 거절하면 JSON 지시 + 파싱으로 물러남"),
             ("장애", "같은 모델을 내는 다른 교내 연결로 넘김(외부로는 넘기지 않음)"),
             ("기록", "결과마다 서버가 알려 준 실제 모델 정보(주소 · 가중치 경로는 빼고)")]
    rx = M + lw + 36
    rw = W - M - rx
    ry = y
    ys = []
    for a, b in rules:
        h = step_row(s, rx, ry, rw, a, b, name_w=104)
        ys.append(ry + h / 2)
        ry += h + 10
    jx = M + lw + 18
    s.line([(M + lw, y2 + h2 / 2), (jx, y2 + h2 / 2)])
    s.line([(jx, ys[0]), (jx, max(ys[-1], y2 + h2 / 2))])
    for yy in ys:
        s.arrow([(jx, yy), (rx, yy)])
    y3 = max(y2 + h2, ry - 10) + 26
    w3 = 440
    t3 = ("교체 점검 (scripts/195)", ["후보 모델을 운영 설정을 바꾸지 않고 같은 데이터로 나란히 측정", "· 검색 · 답 · 종류 판정"])
    t4 = ("채택 결정", ["비교표로 정한 뒤 용도별 서버만 바꿔 저장"])
    h3 = max(box_h(w3, *t3), box_h(W - 2 * M - w3 - 30, *t4))
    s.box(M, y3, w3, h3, *t3)
    s.box(M + w3 + 30, y3, W - 2 * M - w3 - 30, h3, *t4, strong=True)
    s.arrow([(M + lw / 2, y2 + h2), (M + lw / 2, y3)])
    s.arrow([(M + w3, y3 + h3 / 2), (M + w3 + 30, y3 + h3 / 2)])
    yb = s.note(y3 + h3 + 10, "모델 이름 · 문맥 길이 · 추론 모드 끄기 인자를 코드에 박지 않는다. 용도별 서버를 바꾸고 저장하면 코드 수정 없이 모델이 바뀐다. "
                              "문맥은 비율로 나눈다(작업 메모 6 %, 최근 대화 30 %). 문맥이 긴 모델로 바꾸면 저절로 맞는다.")
    s.fit(yb)
    s.save("c3-07-model-swap", "용도별 연결과 모델 교체 구조")


# ── 4장 ──────────────────────────────────────────────────────────────


def doc_process():
    """[그림 4-4] 문서 한 건의 처리 순서."""
    rows = [("디지털 PDF", "표본 6쪽 글자 충분 · 깨진 글자 비율 < 0.15",
             "pdfium 글자 좌표 텍스트 레이어 추출 + MinerU 구조(표 · 2단 · 읽기 순서) 위에 원본 글자 덮어쓰기"),
            ("스캔 PDF", "쪽당 200자 미만 또는 깨진 글자 ≥ 0.15", "기본 MinerU, 짧으면 27B 이미지 판독(최대 30쪽)"),
            ("사진", "", "27B 이미지 판독 → MinerU OCR → tesseract"),
            ("hwp", "", "kordoc(버전 고정) → 실패 시 pyhwp"),
            ("hwpx", "", "자체 OWPML 파서(표 · 글상자 · 머리말)"),
            ("docx · xlsx · pptx", "쪽당 60자 미만이면 MinerU 로 다시", "kordoc → python-docx · openpyxl · python-pptx"),
            ("옛 형식", "xls · doc · ppt · hwp3", "LibreOffice 로 새 형식 변환 뒤 위의 길")]
    s = Svg()
    y = s.title("문서 한 건의 처리 순서: 형식별 읽기 뒤에는 모두 같은 길")
    h0 = s.box(M, y, W - 2 * M, None, "원본 파일", ["실제 형식 확인(확장자가 없거나 틀리면 파일 머리로)"], fill=SOFT)
    lx = M + 18
    bx = lx + 20
    bw = W - M - bx
    nw = 150
    ry = y + h0 + 12
    mids = []
    for name, cond, path in rows:
        tl = wrap(path, bw - nw - 2 * PADX)
        cl = wrap(cond, bw - nw - 2 * PADX) if cond else []
        rh = (len(tl) + len(cl)) * LH + 8
        s.rect(bx, ry, bw, rh, stroke=BORDER)
        nl = wrap(name, nw - 2 * PADX, HEAD - 1)
        for i, t in enumerate(nl):
            s.text(bx + PADX, ry + rh / 2 - len(nl) * 11 + 16.5 + i * 22, t, size=HEAD - 1, weight=700, color=NAVY)
        ty = ry + 4
        for t in cl:
            s.text(bx + nw + PADX, ty + 16, t, color=MUTED)
            ty += LH
        for t in tl:
            s.text(bx + nw + PADX, ty + 16, t)
            ty += LH
        s.arrow([(lx, ry + rh / 2), (bx, ry + rh / 2)])
        mids.append(ry + rh / 2)
        ry += rh + 5
    s.line([(lx, y + h0), (lx, mids[-1])])
    y2 = ry + 18
    steps = [["표 정리", "개인정보(4.2)", "청크 나누기", "서류 종류(27B)"],
             ["사업 분류", "버전 묶기", "문서 이름", "색인(4.3) · 그래프(4.4)"]]
    g = 22
    cw = (W - 2 * M - 2 * PADX - 3 * g) / 4
    bh = 32 + 2 * 32 + 22 + 10
    s.band(M, y2, W - 2 * M, bh, "형식별 읽기 다음")
    s.arrow([(W / 2, ry - 5), (W / 2, y2)])
    py = y2 + 32
    for r, row in enumerate(steps):
        for c, lab in enumerate(row):
            x = M + PADX + c * (cw + g)
            pill(s, x, py, lab, w=cw, h=32, kind="ok" if lab.startswith("색인") else "plain")
            if c < 3:
                s.arrow([(x + cw, py + 16), (x + cw + g, py + 16)])
        if r == 0:
            x3 = M + PADX + 3 * (cw + g) + cw / 2
            x0 = M + PADX + cw / 2
            s.arrow([(x3, py + 32), (x3, py + 43), (x0, py + 43), (x0, py + 54)])
        py += 54
    s.fit(y2 + bh)
    s.save("c4-04-doc-process", "문서 한 건의 처리 순서")


def search_flow():
    """[그림 4-8] 검색의 흐름."""
    s = Svg()
    y = s.title("검색의 흐름: 규정 검색과 사업 문서 검색")
    g = 26
    a = [(110, "질문", []), (200, "입력 정리", ["전각 → 반각"]), (None, "RAG 공간 · 열람 조건", ["검색 SQL 의 WHERE"])]
    a[2] = (W - 2 * M - 110 - 200 - 2 * g, a[2][1], a[2][2])
    ha = max(box_h(w, t, ln) for w, t, ln in a)
    x = M
    for i, (w, t, ln) in enumerate(a):
        s.box(x, y, w, ha, t, ln, valign=True)
        if i:
            s.arrow([(x - g, y + ha / 2), (x, y + ha / 2)])
        x += w + g
    bw = (W - 2 * M - 14) / 2
    L, R = M, M + bw + 14
    y2 = y + ha + 26
    xr = W - M - a[2][0] / 2
    s.arrow([(xr, y + ha), (xr, y2)])
    s.arrow([(xr, y + ha + 13), (L + bw / 2, y + ha + 13), (L + bw / 2, y2)])
    iw = bw - 2 * PADX
    half = (iw - 10) / 2
    lv = [
        [[("어휘", ["Kiwi 명사 + 영문 약칭, IDF"]), ("의미", ["Embed v2(토르 03)"])],
         [("RRF 순위 융합", ["k=60 · 어휘 0.4 · 의미 1.0"])],
         [("리랭커", ["후보 20 → Rerank v1(토르 03)"])],
         [("꼬리 버림", ["1위 점수의 0.25배 아래"])]],
        [[("그래프 절 축 먼저", ["묶어 적은 절 제목"])],
         [("어휘", ["grant_lex(GIN)"]), ("의미", ["grant_vec(HNSW)"])],
         [("RRF 순위 융합", ["같은 가중, 어휘 1위 청크 지킴"])],
         [("고르기", ["버전 최종본만 · 문서당 2청크 · 상위 5"])]]]
    hs = []
    for k in range(4):
        hs.append(max(box_h(half if len(col[k]) == 2 else iw, t, ln) for col in lv for t, ln in col[k]))
    lg = 16
    bh = 32 + sum(hs) + 3 * lg + 10
    s.band(L, y2, bw, bh, "규정 검색")
    s.band(R, y2, bw, bh, "사업 문서 검색: 그래프 범위 안(4.4)")
    for x0, col in ((L, lv[0]), (R, lv[1])):
        yy = y2 + 32
        prev = None
        for k, items in enumerate(col):
            xs = [x0 + PADX] if len(items) == 1 else [x0 + PADX, x0 + PADX + half + 10]
            ws = [iw] if len(items) == 1 else [half, half]
            for (t, ln), xx, ww in zip(items, xs, ws):
                s.box(xx, yy, ww, hs[k], t, ln, strong=(t == "리랭커"))
            cur = [xx + ww / 2 for xx, ww in zip(xs, ws)]
            if prev:
                py, pc = prev
                if len(pc) == 2:
                    for c in pc:
                        s.arrow([(c, py), (c, yy)])
                elif len(cur) == 2:
                    for c in cur:
                        s.arrow([(c, py), (c, yy)])
                else:
                    s.arrow([(pc[0], py), (pc[0], yy)])
            prev = (yy + hs[k], cur)
            yy += hs[k] + lg
    y3 = y2 + bh + 28
    w1 = 440
    h3 = max(box_h(w1, "근거 묶기", tag="규정 묶음 / 사업 문서 묶음"), box_h(W - 2 * M - w1 - g, "생성 (4.5)", tag="Writer 27B"))
    s.box(M, y3, w1, h3, "근거 묶기", tag="규정 묶음 / 사업 문서 묶음")
    s.box(M + w1 + g, y3, W - 2 * M - w1 - g, h3, "생성 (4.5)", tag="Writer 27B", strong=True)
    s.arrow([(L + bw / 2, y2 + bh), (L + bw / 2, y3)])
    s.arrow([(R + bw / 2, y2 + bh), (R + bw / 2, y3 - 15), (M + w1 - 60, y3 - 15), (M + w1 - 60, y3)])
    s.arrow([(M + w1, y3 + h3 / 2), (M + w1 + g, y3 + h3 / 2)])
    yb = s.note(y3 + h3 + 8, "어휘 축은 색인과 질의가 같은 낱말 함수(lex_terms)를 쓴다. 리랭커는 규정 검색에만 쓴다.")
    s.fit(yb)
    s.save("c4-08-search-flow", "검색의 흐름")


def ontology_rel():
    """[그림 4-12] 온톨로지의 노드와 관계."""
    s = Svg()
    y = s.title("온톨로지의 노드와 관계")
    gap = 36
    nw = (W - 2 * M - 4 * gap) / 5
    keys = ("묶음", "사업", "연차", "문서", "절")
    xs = {k: M + i * (nw + gap) for i, k in enumerate(keys)}
    hh = max(box_h(nw, t, [sub] if sub else []) for t, sub in
             (("사업 묶음", ""), ("문서", "서류 종류가 하위 분류"), ("사업", "앞 · 뒤 단계 사업"), ("문서", "계획서 ↔ 실적보고서"),
              ("문서(평가)", "평가 문서"), ("기관", "부처 · 전담기관"), ("성과지표", "기준 · 목표 · 실적 · 달성률")))
    lab = 3 * LH + 14
    yt, ym, yb_ = y, y + hh + lab, y + 2 * (hh + lab)
    node(s, xs["묶음"], ym, nw, hh, "사업 묶음")
    node(s, xs["사업"], ym, nw, hh, "사업", strong=True)
    node(s, xs["연차"], ym, nw, hh, "연차")
    node(s, xs["문서"], ym, nw, hh, "문서", "서류 종류가 하위 분류")
    node(s, xs["절"], ym, nw, hh, "절")
    for a, b in (("묶음", "사업"), ("사업", "연차"), ("연차", "문서"), ("문서", "절")):
        s.arrow([(xs[a] + nw, ym + hh / 2), (xs[b], ym + hh / 2)])
    # 위
    node(s, xs["사업"], yt, nw, hh, "사업", "앞 · 뒤 단계 사업", dashed=True)
    s.arrow([(xs["사업"] + nw / 2, ym), (xs["사업"] + nw / 2, yt + hh)])
    s.para(xs["사업"] + nw / 2 - 8, yt + hh + 6, nw / 2 + gap + nw - 10, "succeeded_by · integrated_into (외부 확인 대장)", color=MUTED, anchor="end")
    node(s, xs["연차"], yt, nw, hh, "문서", "계획서 ↔ 실적보고서", dashed=True)
    xa = xs["문서"] + 22
    s.arrow([(xa, ym), (xa, yt + hh + lab / 2), (xs["연차"] + nw / 2, yt + hh + lab / 2), (xs["연차"] + nw / 2, yt + hh)], both=True)
    s.para(xs["연차"] + nw / 2 + 8, yt + hh + 4, nw / 2 + gap + 14, "plans_reports", color=MUTED)
    s.para(xs["연차"] + nw / 2 + 8, yt + hh + lab / 2 + 4, nw / 2 + gap + 14, "(계획↔실적)", color=MUTED)
    node(s, xs["문서"], yt, nw, hh, "문서(평가)", "평가 문서", dashed=True)
    xe = xs["문서"] + nw - 22
    s.arrow([(xe, yt + hh), (xe, ym)])
    s.para(xe + 8, yt + hh + 6, 120, "evaluates", color=MUTED)
    # 아래
    node(s, xs["사업"], yb_, nw, hh, "기관", "부처 · 전담기관")
    s.arrow([(xs["사업"] + nw / 2, ym + hh), (xs["사업"] + nw / 2, yb_)])
    s.para(xs["사업"] + nw / 2 - 8, ym + hh + 6, nw / 2 + gap + nw - 10, "supervised_by · managed_by", color=MUTED, anchor="end")
    node(s, xs["문서"], yb_, nw, hh, "성과지표", "기준 · 목표 · 실적 · 달성률")
    s.arrow([(xs["문서"] + nw / 2, ym + hh), (xs["문서"] + nw / 2, yb_)])
    s.para(xs["문서"] + nw / 2 - 8, ym + hh + 6, nw / 2 + gap + nw - 10, "측정(표 셀에서 읽은 값)", color=MUTED, anchor="end")
    node(s, xs["절"], yb_, nw, hh, "단위과제")
    s.arrow([(xs["절"] + nw / 2, ym + hh), (xs["절"] + nw / 2, yb_)])
    s.para(xs["절"] + nw / 2 - 8, ym + hh + 6, nw / 2 + gap - 8, "같은 사업에서 되풀이", color=MUTED, anchor="end")
    yb = s.note(yb_ + hh + 10, "가로 화살표는 contains. 관계마다 기준(구조 · 분류 · 식별자 일치 · 추출 · 유사도 가운데 하나)과 근거(문서 · 절 위치 또는 외부 출처 URL)를 단다. "
                               "점선 상자는 같은 종류의 다른 노드다. 그래프를 다시 지을 때마다 SHACL 규칙(graph/shapes.ttl)으로 검사한다.")
    s.fit(yb)
    s.save("c4-12-ontology", "온톨로지의 노드와 관계")


def graph_rag():
    """[그림 4-13] 27B 질문 읽기와 그래프 범위 검색."""
    s = Svg()
    y = s.title("27B 질문 읽기와 그래프 범위 검색: 뜻은 모델이 읽고 탐색은 코드가")
    g = 40
    cw = (W - 2 * M - g) / 2
    L, R = M, M + cw + g
    left = [("질문 + 사업 후보 목록 + 관계 7종", ["사업 후보는 질문과 겹치는 사업, 번호를 붙여 건넴"], "입력", False),
            ("Writer 27B 질문 읽기", ["온도 0 · 출력 120토큰 · JSON 스키마 강제 · 같은 질문 10분 기억"], "모델", True),
            ("{사업 번호, 연도, 차수, 종류, 따라갈 관계}", ["종류는 계획 · 실적 · 평가, 사업은 목록 번호로만 받음"], "모델이 낸 값", False)]
    right = [("범위 정하기", ["이름 맞추기 + 모델 읽기 보탬 → 관계 따라 넓히기(짝 · 평가 · 앞뒤 단계 · 편입)",
                          "· 연도를 물으면 연도 모르는 문서 제외", "· 「」로 묶은 이름이 문서 · 절 이름이면 그것으로 좁힘"], "코드(결정적)", False),
             ("범위 안 하이브리드 검색 (4.3)", ["관계 질문은 양쪽 문서에서 한 청크씩", "· 절 축 청크도 권한을 다시 거름"], "코드", False),
             ("근거 → 답", [], "", True)]
    pos = {}
    for x, col in ((L, left), (R, right)):
        yy = y
        prev = None
        for t, ln, tag, st in col:
            h = s.box(x, yy, cw, None, t, ln, tag=tag, strong=st)
            pos[t] = (x, yy, h)
            if prev is not None:
                s.arrow([(x + cw / 2, prev), (x + cw / 2, yy)])
            prev = yy + h
            yy += h + 26
    _, y1, h1 = pos["Writer 27B 질문 읽기"]
    _, y3, h3 = pos["{사업 번호, 연도, 차수, 종류, 따라갈 관계}"]
    _, yr, hr = pos["범위 정하기"]
    s.arrow([(L + cw, y3 + h3 / 2), (L + cw + g / 3, y3 + h3 / 2), (L + cw + g / 3, yr + hr - 26), (R, yr + hr - 26)])
    yd = y1 + 26
    s.arrow([(L + cw, yd), (L + cw + 2 * g / 3, yd), (L + cw + 2 * g / 3, yr + hr / 2 - 10), (R, yr + hr / 2 - 10)], dashed=True)
    bottom = max(y3 + h3, pos["근거 → 답"][1] + pos["근거 → 답"][2])
    yb = s.note(bottom + 10, "점선: 모델 읽기가 실패하면 이름 맞추기로 물러남")
    s.fit(yb)
    s.save("c4-13-graph-rag", "27B 질문 읽기와 그래프 범위 검색")


def doc_agent():
    """[그림 4-21] 문서 작업 에이전트의 단계 구조와 관문."""
    s = Svg()
    y = s.title("문서 작업 에이전트: 모델이 고르고 코드 검증 단계가 지킨다")
    cw = 576
    h1 = s.box(M, y, cw, None, "상태", ["요청 · 작업 메모 · 최근 대화 · 프로젝트 문서 목록",
                                      "작업본 절 목록과 빈 절 · 받지 못한 값 · 지금까지 한 단계"], tag="자료 구분 기호 <<< >>> 안에만 넣음")
    y2 = y + h1 + GAP
    lw = 330
    nx = M + lw + 20
    nw = M + cw - nx
    note = "한도에 닿았는데 찾은 근거가 있으면 「답하기만 고를 수 있는 마지막 단계」"
    h2 = max(box_h(lw, "Writer 27B", ["{생각, 도구, 인자} 를 JSON 으로"]), box_h(nw, "", [note]))
    s.box(M, y2, lw, h2, "Writer 27B", ["{생각, 도구, 인자} 를 JSON 으로"], strong=True, valign=True)
    s.box(nx, y2, nw, h2, "", [note], dashed=True, valign=True)
    s.line([(M + lw, y2 + h2 / 2), (nx, y2 + h2 / 2)], dashed=True)
    s.arrow([(M + lw / 2, y + h1), (M + lw / 2, y2)])
    y3 = y2 + h2 + GAP
    h3 = s.box(M, y3, cw, None, "코드 검증 단계", ["단계 14번 · 문서 쓰기 4번까지 · 같은 단계 세 번 잇달으면 멈춤",
                                              "되돌리기 어려운 도구는 담당자 요청 글에 그 뜻이 부정 없이 있을 때만",
                                              "찾기 전 사실 답 금지(먼저 찾기) · 열람 범위 · 수치 근거 대조 · 양식 모양"])
    s.arrow([(M + lw / 2, y2 + h2), (M + lw / 2, y3)])
    y4 = y3 + h3 + GAP
    t4 = ("도구 실행", ["찾기 · 읽기 · 양식 · 쓰기 · 고치기 · 묻기 · 내보내기 …", "· 결과는 자료로만 돌려줌(자료 속 구분 기호 · 머리말은 전각으로)"])
    t5 = ("모든 단계 기록 (doc_agent_episodes)", ["리즈닝 학습 재료"])
    tw4 = lw + 20
    h4 = max(box_h(tw4, *t4), box_h(cw - tw4 - 30, *t5))
    s.box(M, y4, tw4, h4, *t4)
    s.box(M + tw4 + 30, y4, cw - tw4 - 30, h4, *t5)
    s.arrow([(M + lw / 2, y3 + h3), (M + lw / 2, y4)])
    s.arrow([(M + tw4, y4 + h4 / 2), (M + tw4 + 30, y4 + h4 / 2)])
    lx = M + cw + 40
    s.arrow([(M + lw / 2 + 60, y4 + h4), (M + lw / 2 + 60, y4 + h4 + 14), (lx, y4 + h4 + 14), (lx, y + h1 / 2), (M + cw, y + h1 / 2)])
    s.para(lx + 8, y + h1 + 10, W - M - lx - 8, "다음 단계", weight=700, color=MUTED)
    s.fit(y4 + h4 + 16)
    s.save("c4-21-doc-agent", "문서 작업 에이전트의 단계 구조와 관문")


def hwpx_fill():
    """[그림 4-26] 원본 서식 보존 채우기."""
    s = Svg()
    y = s.title("원본 서식 보존 채우기: 작업본의 절 본문을 원본 hwpx 에 옮긴다")
    g = 24
    bw = (W - 2 * M - g) / 2
    a = ("원본 hwpx (ZIP)", ["구역 XML 을 다시 직렬화하지 않음"])
    b = ("작업본 절 본문", ["독스 또는 기안기 · 글 · 표 · 그림(안내 상자 제외)"])
    h0 = max(box_h(bw, *a), box_h(bw, *b))
    s.box(M, y, bw, h0, *a, fill=SOFT)
    s.box(M + bw + g, y, bw, h0, *b, fill=SOFT)
    y2 = y + h0 + 24
    for x in (M + bw / 2, M + bw + g + bw / 2):
        s.arrow([(x, y + h0), (x, y2)])
    nw = 196
    hf = step_row(s, M, y2, W - 2 * M, "절 제목 문단 찾기", "번호 · 제목 글자로 맞춤(또는 그 뒤 【작성방법】 상자)", name_w=nw, strong=True)
    cases = [("서식에 이미 있는 문단 · 표", "다시 넣지 않고 그 자리를 지나 이어 씀"),
             ("서식 표를 채웠을 때", "작업본 열 너비로 값 셀 x 위치를 재어 원래 셀에 써 넣음"),
             ("새 표", "id 는 문서 최댓값 다음부터, 테두리는 그 구역 본문 표의 가장 흔한 실선을 이어받음"),
             ("끼워 넣기", "글자열 위치에 문단 · 표 · 그림 XML (손대지 않은 문단은 한 글자도 안 바뀜)")]
    lx = M + 18
    cx = lx + 22
    ry = y2 + hf + 8
    mids = []
    for a_, b_ in cases:
        h = step_row(s, cx, ry, W - M - cx, a_, b_, name_w=nw - 22)
        s.arrow([(lx, ry + h / 2), (cx, ry + h / 2)])
        mids.append(ry + h / 2)
        ry += h + 6
    s.line([(lx, y2 + hf), (lx, mids[-1])])
    y3 = ry + 18
    s.arrow([(W / 2, ry - 6), (W / 2, y3)])
    hr = step_row(s, M, y3, W - 2 * M, "다시 쓰기", "바뀐 구역만 줄 배치 캐시 지움 · ZIP 항목 순서와 압축 방식 유지 · 바뀐 항목만 다시 씀", name_w=nw)
    y4 = y3 + hr + 24
    s.arrow([(W / 2, y3 + hr), (W / 2, y4)])
    w5 = 450
    c = ("자가 점검", ["ZIP 규약 · XML 형식 · 참조 id · 항목 수 · 표 격자"])
    d = ("저장", ["문제 목록이 비었을 때만"])
    h5 = max(box_h(w5, *c), box_h(W - 2 * M - w5 - 50, *d))
    s.box(M, y4, w5, h5, *c)
    s.box(M + w5 + 50, y4, W - 2 * M - w5 - 50, h5, *d, strong=True)
    s.arrow([(M + w5, y4 + h5 / 2), (M + w5 + 50, y4 + h5 / 2)], "통과", ly=y4 + h5 / 2 - 8)
    yb = s.note(y4 + h5 + 8, "이 층은 내용을 만들지 않는다. 원본이 옛 .hwp 면 채우지 않고 hwpx 로 저장한 서식의 수집을 안내한다.")
    s.fit(yb)
    s.save("c4-26-hwpx-fill", "원본 서식 보존 채우기")


def sft_pipeline():
    """[그림 4-14] SFT 파이프라인."""
    s = Svg()
    y = s.title("SFT 파이프라인: 학습 데이터에서 운영 교체 결정까지")
    cands = [("실문서 절 작성쌍 (scripts/152)", "양식 × 완성본 · 서빙 프롬프트 형식 → 편집 계획 JSON"),
             ("문서 구조 단계 문답 (scripts/153)", "[N단계] 사업명 → 개요 → 목차 → 요구 항목 → 뼈대"),
             ("근거 기반 연결 대화", "사업 · 연차 · 문서 · 절 경로와 근거 위치, 2~6턴"),
             ("운영 기록: 마스킹한 사본", "에이전트 단계 · 질문 읽기 · 양식 고르기 · 못 채운 이유")]
    g = 12
    cw = (W - 2 * M - 2 * PADX - g) / 2
    ch = max(box_h(cw, t, [d]) for t, d in cands)
    bh = 32 + 2 * ch + g + 10
    s.band(M, y, W - 2 * M, bh, "학습 후보")
    for i, (t, d) in enumerate(cands):
        s.box(M + PADX + (i % 2) * (cw + g), y + 32 + (i // 2) * (ch + g), cw, ch, t, [d])
    left = [("자동 검사", "경로·근거 기록, 근거가 그 턴에 그대로, 숫자 대조"),
            ("사람 검수", "Label Studio: 근거·구조·맥락·개인정보"),
            ("개인정보", "전체 탐지, 다시 마스킹해도 같은 파일만 반출"),
            ("묶음 검사", "사업 단위 학습·검증·시험 분리, 중복 제거")]
    right = [("학습", "TRL·PEFT bf16 LoRA SFT, 문맥 16,384"),
             ("병합·재양자화", "병합 → NVFP4 → 후보 이름으로 서빙"),
             ("비교", "정답 로그확률 → 같은 시험으로 기반 모델과")]
    g2 = 36
    colw = (W - 2 * M - g2) / 2
    x1, x2 = M, M + colw + g2
    y0 = y + bh + 20
    nw = 100
    s.arrow([(x1 + 60, y + bh), (x1 + 60, y0)])
    ends = {}
    for x, steps in ((x1, left), (x2, right)):
        yy = y0
        prev = None
        for a, b in steps:
            h = step_row(s, x, yy, colw, a, b, name_w=nw, strong=(a == "학습"))
            if prev is not None:
                s.arrow([(x + nw / 2, prev), (x + nw / 2, yy)])
            ends.setdefault(x, []).append((yy, h))
            prev = yy + h
            yy += h + 10
    ly, lh = ends[x1][-1]
    ry, rh = ends[x2][0]
    s.arrow([(x1 + colw, ly + lh / 2), (x1 + colw + g2 / 2, ly + lh / 2), (x1 + colw + g2 / 2, ry + rh / 2), (x2, ry + rh / 2)])
    ly2, lh2 = ends[x2][-1]
    y5 = ly2 + lh2 + 18
    hw1 = 190
    hw2 = colw - hw1 - 10
    t1 = ("채택 기준을 넘으면", ["용도를 후보 모델로"])
    t2 = ("넘지 못하면", ["학습셋 보강 뒤 다시"])
    h5 = max(box_h(hw1, *t1), box_h(hw2, *t2))
    s.box(x2, y5, hw1, h5, *t1, strong=True)
    s.box(x2 + hw1 + 10, y5, hw2, h5, *t2, dashed=True)
    s.arrow([(x2 + nw / 2, ly2 + lh2), (x2 + nw / 2, y5)])
    s.arrow([(x2 + hw1 + 10 + hw2 / 2, ly2 + lh2), (x2 + hw1 + 10 + hw2 / 2, y5)])
    yb = s.note(max(ends[x1][-1][0] + ends[x1][-1][1], 0) + 18, "비교에서는 모델 하나만 바꾸고 질문 · 플랫폼 코드 · 생성 설정 · 채점 모델은 그대로 둔다. "
                                                            "학습셋 원문은 학습 장비 밖으로 나가지 않는다.", w=colw)
    s.fit(max(yb, y5 + h5))
    s.save("c4-38-sft-pipeline", "SFT 파이프라인")


if __name__ == "__main__":
    for f in (layers, build_flow, query_flow, folders, access, model_swap,
              doc_process, search_flow, ontology_rel, graph_rag, doc_agent, hwpx_fill, sft_pipeline):
        f()
    print("ok")
