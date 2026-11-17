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
        ("화면층", "사용자가 쓰는 화면", [("채팅", 0), ("라이브러리", 0), ("프로젝트", 0), ("한글 문서함(기안기)", 0),
                                  ("조직·구성원", 0), ("사업 관리", 0), ("업무 도우미 창", 0), ("|", 0), ("운영 관리 화면", 1)]),
        ("앱층", "요청 처리와 에이전트", [("화면별 요청 처리", 0),
                                   ("질의응답", 1), ("문서 작업 에이전트", 1), ("업무 도우미", 1),
                                   ("열람 권한 판정", 0), ("컨텍스트 길이 맞추기", 0)]),
        ("도메인층", "업무와 무관한 공통 처리", [("문서 읽기와 개인정보 마스킹", 0), ("→", 0), ("청크 나누기와 색인", 0), ("→", 0),
                                 ("하이브리드 검색", 0), ("지식 그래프", 0), ("모델 연결과 생성", 0),
                                 ("수치 검증", 0), ("학습 데이터 관리", 0), ("문서 내보내기", 0)]),
        ("저장층", "DB 와 문서 폴더", [("PostgreSQL(문서 · 청크 · 대장 · 그래프 · 권한 · 기록)", 1), ("+ pgvector(사업 문서 벡터)", 0),
                                ("문서 폴더(수집 · 첨부 · 생성 · 기안기)", 0), ("설정 · 감사 파일", 0)]),
        ("모델층", "교내 장비 서빙", [("Writer 27B × 2(토르 02 · 03)", 1), ("Embed v2", 0), ("Rerank v1(토르 03)", 0)]),
    ]
    s = Svg()
    y = s.title("AURA 계층 구조")
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
    yb = s.note(y + 4, "구축 파이프라인(배치)과 질의 파이프라인(대화형)은 저장층에서만 연결된다.")
    s.fit(yb)
    s.save("c3-01-layers", "AURA 계층 구조")


def build_flow():
    """[그림 3-2] 문서 구축 흐름: 들어오는 경로 → 하나의 처리기(여섯 단계) → 색인 둘과 지식 그래프."""
    s = Svg()
    y = s.title("문서 구축 흐름")
    # 1. 문서가 들어오는 경로
    s.text(M, y + 15, "문서가 들어오는 경로", weight=700, color=MUTED)
    y += 24
    srcs = ["학교 원본 폴더 동기화", "문서함 업로드", "채팅 첨부", "구글 드라이브 가져오기"]
    g = 10
    pw = (W - 2 * M - 3 * g) / 4
    for i, t in enumerate(srcs):
        pill(s, M + i * (pw + g), y, t, w=pw, h=34)
    yp = y + 34
    # 2. 하나의 처리기
    y2 = yp + 26
    for i in range(4):
        cx = M + i * (pw + g) + pw / 2
        s.line([(cx, yp), (cx, yp + 13)])
    s.line([(M + pw / 2, yp + 13), (W - M - pw / 2, yp + 13)])
    s.arrow([(W / 2, yp + 13), (W / 2, y2)])
    steps = [("① 읽기", "파일 형식에 맞는 방법으로 글과 표를 읽어 낸다(스캔 문서는 OCR)."),
             ("② 표 정리", "병합된 셀을 풀어 화면용 표와 검색용 평문을 만든다."),
             ("③ 개인정보 마스킹", "이름·연락처 같은 개인정보를 찾아 설정에 따라 마스킹한다."),
             ("④ 청크 나누기", "검색 단위로 나누고 쪽 번호와 절 제목을 함께 기록한다."),
             ("⑤ 분류", "서류 종류와 사업을 판정하고 본문 제목을 문서 이름으로 정한다."),
             ("⑥ 버전 묶기", "내용이 같은 문서와 고쳐 쓴 문서를 한 버전 그룹으로 묶는다.")]
    iw, nw = W - 2 * M - 2 * PADX, 176
    rhs = [max(row_h(iw, b, nw), 32) for _, b in steps]
    bh = 38 + sum(rhs) + 5 * 6 + 12
    s.rect(M, y2, W - 2 * M, bh, fill=ACCENT, stroke=NAVY, sw=SWS, rx=10)
    s.text(M + PADX, y2 + 25, "하나의 처리기", size=HEAD, weight=700, color=NAVY)
    s.text(M + PADX + tw("하나의 처리기", HEAD) + 12, y2 + 25, "모든 경로의 문서가 같은 순서로 처리된다", color=MUTED)
    ry = y2 + 38
    for (a, b), rh in zip(steps, rhs):
        step_row(s, M + PADX, ry, iw, a, b, name_w=nw, h=rh)
        ry += rh + 6
    # 3. 결과물
    gap = 18
    bw = (W - 2 * M - 2 * gap) / 3
    xs = [M + i * (bw + gap) for i in range(3)]
    y3 = y2 + bh + 30
    outs = [("어휘 색인", ["같은 단어가 든 글을 찾는 색인"]),
            ("의미 색인", ["뜻이 비슷한 글을 찾는 색인"]),
            ("지식 그래프", ["사업·연차·문서·절의 관계"])]
    oh = max(box_h(bw, t, ln) for t, ln in outs)
    s.line([(W / 2, y2 + bh), (W / 2, y3 - 14)])
    s.line([(xs[0] + bw / 2, y3 - 14), (xs[2] + bw / 2, y3 - 14)])
    for i, (x, (t, ln)) in enumerate(zip(xs, outs)):
        s.box(x, y3, bw, oh, t, ln, strong=(i == 2))
        s.arrow([(x + bw / 2, y3 - 14), (x + bw / 2, y3)])
    yb = s.note(y3 + oh + 10, "원본 폴더에서 문서가 추가되거나 바뀌면 그 문서만 다시 처리하고, 색인과 그래프도 함께 갱신한다.")
    s.fit(yb)
    s.save("c3-02-build-flow", "문서 구축 흐름")


def query_flow():
    """[그림 3-3] 질의응답 흐름: 위에서 아래 한 줄, 문서 작성은 곁가지 하나."""
    s = Svg()
    y = s.title("질의응답 흐름")
    steps = [("① 질문", "대화 화면에 입력한 질문"),
             ("② 권한 확인", "볼 수 있는 문서 범위 결정"),
             ("③ 질문 해석(27B)", "사업·연도·서류 종류 파악"),
             ("④ 범위 정하기", "지식 그래프로 관련 사업·문서 선택"),
             ("⑤ 문서 검색", "규정과 사업 문서를 함께 검색"),
             ("⑥ 근거 구성", "찾은 청크를 입력 길이에 맞춰 정리"),
             ("⑦ 답변 생성(27B)", "근거만으로 답변 작성"),
             ("⑧ 수치 검증", "근거에 없는 수치를 찾아 표시"),
             ("⑨ 출처가 달린 답변", "답변과 근거 문서 목록 표시")]
    cw, nw, g = 488, 186, 14
    ys = []
    yy = y
    for i, (a, b) in enumerate(steps):
        h = max(row_h(cw, b, nw), 32)
        step_row(s, M, yy, cw, a, b, name_w=nw, strong=(i == len(steps) - 1))
        if ys:
            s.arrow([(M + nw / 2, ys[-1][0] + ys[-1][1]), (M + nw / 2, yy)])
        ys.append((yy, h))
        yy += h + g
    # 곁가지: 문서 작성 요청
    bx = M + cw + 34
    bw = W - M - bx
    side = ("문서 작성 요청", ["같은 검색을 거쳐 문서 작업 에이전트가 처리한다(4.6절)."])
    sh = box_h(bw, *side)
    sy5, sh5 = ys[4]
    by = sy5 + sh5 / 2 - 18
    s.box(bx, by, bw, sh, *side, dashed=True, tcolor=INK)
    s.arrow([(M + cw, sy5 + sh5 / 2), (bx, sy5 + sh5 / 2)], dashed=True)
    yb = s.note(yy - g + 10, "권한은 검색 단계에서 적용하므로 볼 수 없는 문서는 처음부터 근거에 들어가지 않는다. "
                            "화면의 출처는 모델에 실제로 전달한 근거 목록에서만 만든다.")
    s.fit(yb)
    s.save("c3-03-query-flow", "질의응답 흐름")


def folders():
    """[그림 3-4] 문서 폴더 배치. 굵은 글은 실제 폴더 이름, 흐린 글은 문서마다 달라지는 부분."""
    V = None  # 표시: 문자열 앞에 「*」 를 붙이면 문서마다 달라지는 부분
    rows = [(1, "반입", ["*연도", "*서류 유형", "*접수번호와 제목", "*원본 파일"], "수집한 원본과 추출 결과"),
            (1, "첨부", ["*연도", "*대화 번호", "*시각과 파일 이름"], "채팅에 첨부한 파일"),
            (1, "생성", ["*연도", "*접수번호", "*시각과 결과 종류"], "초안·판독 결과·완성본 사본"),
            (1, "보고", ["주간", "*그 주 월요일.md"], "주간 보고서"),
            (1, "기안기", ["*부서", "*폴더", "*제목.hwpx"], "부서별 한글 문서함"),
            (2, ".판", [], "이전 버전"),
            (2, ".휴지통", [], "삭제한 문서"),
            (2, "공통", ["공통 양식", "*용도"], "함께 쓰는 빈 양식"),
            (2, "공통", ["지난 사업 문서", "*사업", "*용도"], "사업 전용 양식"),
            (2, "~아이디", [], "개인 폴더")]
    del V
    s = Svg()
    y = s.title("문서 폴더 배치")
    s.rect(M, y, 130, 32, fill=ACCENT, stroke=NAVY, sw=SWS, rx=6)
    mono(s, M + 12, y + 22, "documents/", weight=700, color=NAVY)
    s.text(W - M - 10, y + 22, "담는 것", weight=700, color=MUTED, anchor="end")
    s.text(M + 150, y + 22, "굵은 글: 폴더 이름, 흐린 글: 문서마다 달라지는 부분", color=MUTED)
    y0 = y + 32
    ry = y0 + 10
    mids = []
    rh = 34
    for lv, name, tail, desc in rows:
        x0 = M + 18 if lv == 1 else M + 58
        bx = x0 + 22
        bw = W - M - bx
        s.rect(bx, ry, bw, rh, fill=FILL if lv == 1 else SOFT, stroke=BORDER, rx=6)
        tx = bx + 10
        segs = [(name, True)] + [(t[1:], False) if t.startswith("*") else (t, True) for t in tail]
        for i, (t, lit) in enumerate(segs):
            if i:
                s.text(tx, ry + 23, "/", color=LINE)
                tx += 14
            s.text(tx, ry + 23, t, weight=700 if lit else 400, color=INK if lit else MUTED)
            tx += tw(t) * (0.97 if lit else 0.95) + 6
        s.text(W - M - 10, ry + 23, desc, color=INK, anchor="end")
        s.line([(x0, ry + rh / 2), (bx, ry + rh / 2)], sw=1.6)
        mids.append((lv, ry + rh / 2))
        ry += rh + 6
    last1 = max(m for lv, m in mids if lv == 1)
    last2 = max(m for lv, m in mids if lv == 2)
    s.line([(M + 18, y0), (M + 18, last1)], sw=1.6)
    s.line([(M + 58, last1 + rh / 2), (M + 58, last2)], sw=1.6)
    yb = s.note(ry + 4, "파일의 실제 위치는 DB 의 파일 대장에 기록되어 있으므로, 폴더 전체를 옮길 때는 대장에 적힌 경로의 앞부분만 "
                        "바꾸면 된다. 기안기 아래의 다섯 폴더는 부서마다 하나씩 있으며, 개인 폴더는 사용자가 처음 열 때 만들어진다.")
    s.fit(yb)
    s.save("c3-04-folders", "문서 폴더 배치")


def access():
    """[그림 3-6] 검색 단계의 권한 적용."""
    s = Svg()
    y = s.title("검색 단계의 권한 적용")
    lw = 280
    rx = M + lw + 34
    rw = W - M - rx
    g = 22
    h0 = s.box(rx, y, rw, None, "로그인한 사용자", ["역할, 소속 부서, 등급"])
    y1 = y + h0 + g
    h1 = s.box(rx, y1, rw, None, "볼 수 있는 문서 범위 결정", ["역할과 부서로 검색할 문서 묶음을 정한다."])
    s.arrow([(rx + rw / 2, y + h0), (rx + rw / 2, y1)])
    y2 = y1 + h1 + g
    h2 = s.box(rx, y2, rw, None, "검색 조건에 열람 제한 추가", ["범위 밖 문서를 빼는 조건을 검색에 넣는다."])
    s.arrow([(rx + rw / 2, y1 + h1), (rx + rw / 2, y2)])
    y3 = y2 + h2 + 30
    hw = (rw - 14) / 2
    a = ("규정 검색", ["같은 열람 제한 적용"])
    b = ("사업 문서 검색", ["같은 열람 제한 적용"])
    h3 = max(box_h(hw, *a), box_h(hw, *b))
    s.box(rx, y3, hw, h3, *a)
    s.box(rx + hw + 14, y3, hw, h3, *b)
    s.line([(rx + rw / 2, y2 + h2), (rx + rw / 2, y2 + h2 + 15)])
    s.line([(rx + hw / 2, y2 + h2 + 15), (rx + hw + 14 + hw / 2, y2 + h2 + 15)])
    for cx in (rx + hw / 2, rx + hw + 14 + hw / 2):
        s.arrow([(cx, y2 + h2 + 15), (cx, y3)])
    y4 = y3 + h3 + 30
    h4 = s.box(rx, y4, rw, None, "모델에 전달한 근거 = 화면 출처", ["출처는 전달한 근거 목록에서만 만든다."], strong=True)
    s.line([(rx + hw / 2, y3 + h3), (rx + hw / 2, y3 + h3 + 15)])
    s.line([(rx + hw + 14 + hw / 2, y3 + h3), (rx + hw + 14 + hw / 2, y3 + h3 + 15)])
    s.line([(rx + hw / 2, y3 + h3 + 15), (rx + hw + 14 + hw / 2, y3 + h3 + 15)])
    s.arrow([(rx + rw / 2, y3 + h3 + 15), (rx + rw / 2, y4)])
    # 곁가지: 범위 밖 부서를 묻는 질문
    q = ("다른 부서 문서를 묻는 질문", [])
    hq = box_h(lw, *q)
    qy = y1 + h1 / 2 - hq / 2
    s.box(M, qy, lw, hq, *q, dashed=True, tcolor=INK)
    s.arrow([(rx, y1 + h1 / 2), (M + lw, y1 + h1 / 2)], dashed=True)
    n = ("열람 권한 없음 안내", ["권한이 없다는 것만 알린다.", "문서 제목과 건수는 밝히지 않는다.", "질문은 감사 기록에 남긴다."])
    s.box(M, y2 + 6, lw, None, *n)
    s.arrow([(M + lw / 2, qy + hq), (M + lw / 2, y2 + 6)])
    yb = s.note(y4 + h4 + 10, "역할을 알 수 없는 계정에는 가장 좁은 범위인 학생 범위를 적용한다. 같은 규칙을 화면 표시와 검색 조건에 "
                              "함께 사용하므로, 화면에서 볼 수 없는 문서는 검색 결과에도 나오지 않는다.")
    s.fit(yb)
    s.save("c3-05-access", "검색 단계의 권한 적용")


def model_swap():
    """[그림 3-7] 용도별 연결과 모델 교체 구조."""
    s = Svg()
    y = s.title("용도별 연결과 모델 교체 구조")
    lw = 268
    g = 24
    h1 = s.box(M, y, lw, None, "기능", ["답변 작성, 수집 검토, 이미지 판독, 업무 도우미", "· 용도 이름으로 모델을 요청한다."])
    y2 = y + h1 + g
    h2 = s.box(M, y2, lw, None, "연결 설정", ["용도마다 서버와 모델을 지정한다.", "· 운영 관리 화면에서 변경한다."], strong=True)
    s.arrow([(M + lw / 2, y + h1), (M + lw / 2, y2)])
    y3 = y2 + h2 + g
    h3 = s.box(M, y3, lw, None, "실제 모델 호출", ["지정한 교내 서버의 모델이 응답한다."])
    s.arrow([(M + lw / 2, y2 + h2), (M + lw / 2, y3)])
    rules = [("컨텍스트 길이", "서버가 알려 주는 길이를 읽어 와 사용한다."),
             ("구조화 출력", "지원하지 않는 서버에는 지시문 방식으로 대체한다."),
             ("장애 대응", "같은 모델을 올린 다른 교내 서버로 전환한다."),
             ("기록", "결과마다 실제로 사용한 모델을 기록한다.")]
    rx = M + lw + 36
    rw = W - M - rx
    s.text(rx, y + 15, "연결 설정이 함께 처리하는 일", weight=700, color=MUTED)
    ry = y + 26
    ys = []
    for a, b in rules:
        h = step_row(s, rx, ry, rw, a, b, name_w=128)
        ys.append(ry + h / 2)
        ry += h + 8
    jx = M + lw + 18
    s.line([(M + lw, y2 + h2 / 2), (jx, y2 + h2 / 2)])
    s.line([(jx, min(ys[0], y2 + h2 / 2)), (jx, max(ys[-1], y2 + h2 / 2))])
    for yy in ys:
        s.arrow([(jx, yy), (rx, yy)])
    # 교체 절차
    yb0 = max(y3 + h3, ry - 8) + 22
    steps = [("① 나란히 측정", ["후보 모델을 같은 평가 데이터셋으로 측정"]),
             ("② 채택 결정", ["비교 결과로 교체 여부 결정"]),
             ("③ 설정 저장", ["연결 설정만 바꿔 저장"])]
    cg = 26
    inner = W - 2 * M - 2 * PADX
    cw = (inner - 2 * cg) / 3
    ch = max(box_h(cw, *t) for t in steps)
    s.band(M, yb0, W - 2 * M, 32 + ch + 12, "교체 절차")
    for i, t in enumerate(steps):
        cx = M + PADX + i * (cw + cg)
        s.box(cx, yb0 + 32, cw, ch, *t, strong=(i == 2))
        if i:
            s.arrow([(cx - cg, yb0 + 32 + ch / 2), (cx, yb0 + 32 + ch / 2)])
    yb = s.note(yb0 + 32 + ch + 12 + 10, "모델 이름과 컨텍스트 길이를 코드에 고정하지 않으므로, 연결 설정만 바꾸어 저장하면 코드를 고치지 않고 "
                                         "모델을 교체할 수 있다. 컨텍스트는 길이에 비례해 나누어 쓰므로 더 긴 모델로 바꾸면 배분도 함께 늘어난다.")
    s.fit(yb)
    s.save("c3-07-model-swap", "용도별 연결과 모델 교체 구조")


# ── 4장 ──────────────────────────────────────────────────────────────


def doc_process():
    """[그림 4-4] 문서 한 건의 처리 순서."""
    rows = [("디지털 PDF", "앞 6쪽에 글자가 충분하고 깨진 글자가 15% 미만일 때",
             "텍스트 레이어에서 글자를 그대로 꺼내고, MinerU 가 찾은 표와 읽기 순서에 그 글자를 채운다"),
            ("스캔 PDF", "쪽당 글자가 200자 미만이거나 깨진 글자가 15% 이상일 때",
             "MinerU 로 OCR 하고, 30쪽 이하의 짧은 문서는 27B 가 이미지를 읽는다"),
            ("사진", "", "27B 가 이미지를 읽고, 실패하면 OCR 도구로 넘긴다"),
            ("한글(hwp)", "", "한글 문서 변환기로 읽고, 실패하면 예비 변환기를 쓴다"),
            ("한글(hwpx)", "", "자체 변환기로 표와 글상자, 머리말까지 읽는다"),
            ("오피스 문서", "워드·엑셀·파워포인트",
             "문서 변환기로 읽고, 쪽당 60자 미만이면 MinerU 로 다시 읽는다"),
            ("옛 형식", "xls·doc·ppt 와 옛 한글 파일", "LibreOffice 로 새 형식으로 바꾼 뒤 위 경로로 처리한다")]
    s = Svg()
    y = s.title("문서 한 건의 처리 순서")
    h0 = s.box(M, y, W - 2 * M, None, "원본 파일", ["확장자가 없거나 틀려도 파일 첫머리를 읽어 실제 형식을 판별한다"], fill=SOFT)
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
    steps = [["표 정리", "개인정보 마스킹", "청크 분할", "서류 종류 판정"],
             ["사업 분류", "버전 그룹화", "문서 이름 결정", "색인과 그래프 등록"]]
    g = 22
    cw = (W - 2 * M - 2 * PADX - 3 * g) / 4
    bh = 32 + 2 * 32 + 22 + 10
    s.band(M, y2, W - 2 * M, bh, "형식별 읽기 이후 단계")
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
    y = s.title("규정 검색과 사업 문서 검색 흐름")
    g = 26
    a = [(110, "질문", []), (200, "입력 정리", ["전각을 반각으로 통일"]), (None, "검색 범위와 열람 조건", ["RAG 공간과 열람 권한을 조건으로"])]
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
        [[("어휘", ["단어 색인 검색"]), ("의미", ["벡터 색인 검색"])],
         [("RRF 순위 융합", ["어휘 0.4, 의미 1.0 으로 합친다"])],
         [("리랭커", ["상위 20개를 Rerank v1 로 재정렬"])],
         [("하위 결과 제외", ["1위 점수의 4분의 1 미만은 뺀다"])]],
        [[("그래프 절 우선", ["질문이 가리키는 절부터 찾는다"])],
         [("어휘", ["단어 색인 검색"]), ("의미", ["벡터 색인 검색"])],
         [("RRF 순위 융합", ["어휘 1위 청크는 남기며 합친다"])],
         [("최종 선택", ["문서당 2개씩 상위 5개를 고른다"])]]]
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
    h3 = max(box_h(w1, "근거 구성", tag="규정 근거 / 사업 문서 근거"), box_h(W - 2 * M - w1 - g, "생성 (4.5)", tag="Writer 27B"))
    s.box(M, y3, w1, h3, "근거 구성", tag="규정 근거 / 사업 문서 근거")
    s.box(M + w1 + g, y3, W - 2 * M - w1 - g, h3, "생성 (4.5)", tag="Writer 27B", strong=True)
    s.arrow([(L + bw / 2, y2 + bh), (L + bw / 2, y3)])
    s.arrow([(R + bw / 2, y2 + bh), (R + bw / 2, y3 - 15), (M + w1 - 60, y3 - 15), (M + w1 - 60, y3)])
    s.arrow([(M + w1, y3 + h3 / 2), (M + w1 + g, y3 + h3 / 2)])
    yb = s.note(y3 + h3 + 8, "색인할 때와 질문을 처리할 때 같은 방식으로 단어를 뽑으며, 리랭커는 규정 검색에만 적용한다.")
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
             (("사업군", ""), ("문서", "서류 종류로 하위 분류"), ("사업", "앞·뒤 단계 사업"), ("문서", "계획서 ↔ 실적보고서"),
              ("문서(평가)", "평가 문서"), ("기관", "부처 · 전담기관"), ("성과지표", "기준 · 목표 · 실적 · 달성률")))
    lab = 3 * LH + 14
    yt, ym, yb_ = y, y + hh + lab, y + 2 * (hh + lab)
    node(s, xs["묶음"], ym, nw, hh, "사업군")
    node(s, xs["사업"], ym, nw, hh, "사업", strong=True)
    node(s, xs["연차"], ym, nw, hh, "연차")
    node(s, xs["문서"], ym, nw, hh, "문서", "서류 종류로 하위 분류")
    node(s, xs["절"], ym, nw, hh, "절")
    for a, b in (("묶음", "사업"), ("사업", "연차"), ("연차", "문서"), ("문서", "절")):
        s.arrow([(xs[a] + nw, ym + hh / 2), (xs[b], ym + hh / 2)])
    # 위
    node(s, xs["사업"], yt, nw, hh, "사업", "앞·뒤 단계 사업", dashed=True)
    s.arrow([(xs["사업"] + nw / 2, ym), (xs["사업"] + nw / 2, yt + hh)])
    s.para(xs["사업"] + nw / 2 - 8, yt + hh + 6, nw / 2 + gap + nw - 10, "후속 사업, 편입된 사업(외부 확인 대장)", color=MUTED, anchor="end")
    node(s, xs["연차"], yt, nw, hh, "문서", "계획서 ↔ 실적보고서", dashed=True)
    xa = xs["문서"] + 22
    s.arrow([(xa, ym), (xa, yt + hh + lab / 2), (xs["연차"] + nw / 2, yt + hh + lab / 2), (xs["연차"] + nw / 2, yt + hh)], both=True)
    s.para(xs["연차"] + nw / 2 + 8, yt + hh + 4, nw / 2 + gap + 14, "계획과 실적", color=MUTED)
    s.para(xs["연차"] + nw / 2 + 8, yt + hh + lab / 2 + 4, nw / 2 + gap + 14, "대응 관계", color=MUTED)
    node(s, xs["문서"], yt, nw, hh, "문서(평가)", "평가 문서", dashed=True)
    xe = xs["문서"] + nw - 22
    s.arrow([(xe, yt + hh), (xe, ym)])
    s.para(xe + 8, yt + hh + 6, 120, "평가 대상", color=MUTED)
    # 아래
    node(s, xs["사업"], yb_, nw, hh, "기관", "부처 · 전담기관")
    s.arrow([(xs["사업"] + nw / 2, ym + hh), (xs["사업"] + nw / 2, yb_)])
    s.para(xs["사업"] + nw / 2 - 8, ym + hh + 6, nw / 2 + gap + nw - 10, "주관 부처와 전담기관", color=MUTED, anchor="end")
    node(s, xs["문서"], yb_, nw, hh, "성과지표", "기준 · 목표 · 실적 · 달성률")
    s.arrow([(xs["문서"] + nw / 2, ym + hh), (xs["문서"] + nw / 2, yb_)])
    s.para(xs["문서"] + nw / 2 - 8, ym + hh + 6, nw / 2 + gap + nw - 10, "표에서 읽은 지표 값", color=MUTED, anchor="end")
    node(s, xs["절"], yb_, nw, hh, "단위과제")
    s.arrow([(xs["절"] + nw / 2, ym + hh), (xs["절"] + nw / 2, yb_)])
    s.para(xs["절"] + nw / 2 - 8, ym + hh + 6, nw / 2 + gap - 8, "반복되는 단위과제", color=MUTED, anchor="end")
    yb = s.note(yb_ + hh + 10, "가로 화살표는 포함 관계이고, 점선 상자는 같은 종류의 다른 노드다. 관계마다 어떤 기준으로 이었는지와 그 근거가 되는 문서 위치나 외부 출처를 기록한다. "
                               "그래프를 다시 구축할 때마다 SHACL 규칙으로 구조를 검사한다.")
    s.fit(yb)
    s.save("c4-12-ontology", "온톨로지의 노드와 관계")


def graph_rag():
    """[그림 4-13] 27B 질문 읽기와 그래프 범위 검색."""
    s = Svg()
    y = s.title("질문 해석과 그래프 범위 검색")
    g = 40
    cw = (W - 2 * M - g) / 2
    L, R = M, M + cw + g
    left = [("질문과 사업 후보 목록", ["질문과 이름이 겹치는 사업을 후보로 골라 번호를 붙여 함께 전달한다"], "입력", False),
            ("Writer 27B 질문 해석", ["정해진 형식(JSON)으로만 답하게 하고, 같은 질문은 10분 동안 결과를 재사용한다"], "모델", True),
            ("해석 결과", ["사업 번호, 연도, 차수, 문서 종류, 따라갈 관계", "사업은 후보 목록의 번호로만 고른다"], "모델 출력", False)]
    right = [("범위 결정", ["이름 일치와 모델 해석으로 사업을 정하고, 대응·평가·앞뒤 단계·편입 관계를 따라 범위를 넓힌다",
                          "연도를 물으면 연도를 알 수 없는 문서는 뺀다", "질문이 문서나 절 이름을 가리키면 그 범위로 좁힌다"], "코드", False),
             ("범위 안 하이브리드 검색 (4.3)", ["관계를 묻는 질문은 양쪽 문서에서 한 청크씩 고른다", "절 단위로 찾은 청크도 열람 권한을 다시 확인한다"], "코드", False),
             ("근거를 붙인 답변 생성", [], "", True)]
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
    _, y1, h1 = pos["Writer 27B 질문 해석"]
    _, y3, h3 = pos["해석 결과"]
    _, yr, hr = pos["범위 결정"]
    # 실선(해석 결과)은 바깥쪽 세로선, 점선(해석 실패)은 안쪽 세로선으로 그려 서로 엇갈리지 않게 한다
    s.arrow([(L + cw, y3 + h3 / 2), (L + cw + 2 * g / 3, y3 + h3 / 2), (L + cw + 2 * g / 3, yr + hr - 26), (R, yr + hr - 26)])
    yd = y1 + 26
    s.arrow([(L + cw, yd), (L + cw + g / 3, yd), (L + cw + g / 3, yr + 40), (R, yr + 40)], dashed=True)
    bottom = max(y3 + h3, pos["근거를 붙인 답변 생성"][1] + pos["근거를 붙인 답변 생성"][2])
    yb = s.note(bottom + 10, "점선은 모델 해석이 실패했을 때 이름 일치만으로 범위를 정하는 경로다.")
    s.fit(yb)
    s.save("c4-13-graph-rag", "27B 질문 읽기와 그래프 범위 검색")


def doc_agent():
    """[그림 4-21] 문서 작업 에이전트의 단계 구조와 관문."""
    s = Svg()
    y = s.title("문서 작업 에이전트 구조")
    cw = 576
    h1 = s.box(M, y, cw, None, "상태", ["요청, 작업 메모, 최근 대화, 프로젝트 문서 목록",
                                      "작업 문서의 절 목록, 빈 절, 받지 못한 값, 지금까지 수행한 단계"], tag="자료는 구분 기호 안에만 넣는다")
    y2 = y + h1 + GAP
    lw = 330
    nx = M + lw + 20
    nw = M + cw - nx
    note = "단계 한도에 이르렀을 때 찾은 근거가 있으면, 답변만 할 수 있는 마지막 단계를 한 번 더 실행한다"
    wl = ["생각과 쓸 도구, 그 인자를 정해진 형식(JSON)으로 출력한다"]
    h2 = max(box_h(lw, "Writer 27B", wl), box_h(nw, "", [note]))
    s.box(M, y2, lw, h2, "Writer 27B", wl, strong=True, valign=True)
    s.box(nx, y2, nw, h2, "", [note], dashed=True, valign=True)
    s.line([(M + lw, y2 + h2 / 2), (nx, y2 + h2 / 2)], dashed=True)
    s.arrow([(M + lw / 2, y + h1), (M + lw / 2, y2)])
    y3 = y2 + h2 + GAP
    h3 = s.box(M, y3, cw, None, "코드 검증 단계", ["최대 14단계, 문서 쓰기는 4회까지 허용하고 같은 단계가 세 번 이어지면 멈춘다",
                                              "되돌리기 어려운 도구는 담당자 요청에 그 의도가 분명히 드러날 때만 허용한다",
                                              "검색하기 전에는 사실을 답하지 못하게 하고, 열람 범위와 수치 근거, 양식 형태를 확인한다"])
    s.arrow([(M + lw / 2, y2 + h2), (M + lw / 2, y3)])
    y4 = y3 + h3 + GAP
    t4 = ("도구 실행", ["검색, 읽기, 양식 선택, 작성, 수정, 질문, 내보내기 등", "결과는 자료로만 돌려주며, 자료 안의 구분 기호는 다른 글자로 바꾼다"])
    t5 = ("모든 단계 기록", ["단계마다 판단과 결과를 남겨 추론 학습 데이터로 활용한다"])
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
    y = s.title("원본 서식 보존 채우기")
    g = 24
    bw = (W - 2 * M - g) / 2
    a = ("원본 한글 파일(hwpx)", ["원본의 내용과 서식을 그대로 두고 그 위에 채운다"])
    b = ("작업 문서의 절 본문", ["독스나 기안기에서 쓴 글과 표, 그림(안내 상자는 제외)"])
    h0 = max(box_h(bw, *a), box_h(bw, *b))
    s.box(M, y, bw, h0, *a, fill=SOFT)
    s.box(M + bw + g, y, bw, h0, *b, fill=SOFT)
    y2 = y + h0 + 24
    for x in (M + bw / 2, M + bw + g + bw / 2):
        s.arrow([(x, y + h0), (x, y2)])
    nw = 196
    hf = step_row(s, M, y2, W - 2 * M, "절 위치 찾기", "원본에서 절 번호와 제목이 같은 문단(또는 그 뒤의 작성 방법 상자)을 찾는다", name_w=nw, strong=True)
    cases = [("이미 있는 문단과 표", "다시 넣지 않고 그 다음 위치부터 이어서 쓴다"),
             ("양식의 표", "작업 문서 표의 열 위치를 원본 표의 셀에 맞추어 값을 채운다"),
             ("새로 넣는 표", "원본 본문 표에서 가장 많이 쓰인 테두리를 그대로 따른다"),
             ("새 내용 넣기", "찾은 위치에 문단과 표, 그림을 넣고, 손대지 않은 문단은 원본 그대로 둔다")]
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
    hr = step_row(s, M, y3, W - 2 * M, "파일 다시 쓰기", "파일 안의 항목 순서와 압축 방식을 유지한 채 바뀐 항목만 다시 쓴다", name_w=nw)
    y4 = y3 + hr + 24
    s.arrow([(W / 2, y3 + hr), (W / 2, y4)])
    w5 = 450
    c = ("자가 점검", ["파일 구조와 문서 형식, 항목 수, 표의 셀 배치가 올바른지 검사한다"])
    d = ("저장", ["점검을 통과한 파일만 저장한다"])
    h5 = max(box_h(w5, *c), box_h(W - 2 * M - w5 - 50, *d))
    s.box(M, y4, w5, h5, *c)
    s.box(M + w5 + 50, y4, W - 2 * M - w5 - 50, h5, *d, strong=True)
    s.arrow([(M + w5, y4 + h5 / 2), (M + w5 + 50, y4 + h5 / 2)], "통과", ly=y4 + h5 / 2 - 8)
    yb = s.note(y4 + h5 + 8, "이 단계는 내용을 생성하지 않는다. 원본이 이전 형식인 .hwp 이면 채우지 않고, hwpx 로 저장한 서식을 수집하도록 안내한다.")
    s.fit(yb)
    s.save("c4-26-hwpx-fill", "원본 서식 보존 채우기")


def sft_pipeline():
    """[그림 4-14] SFT 파이프라인."""
    s = Svg()
    y = s.title("SFT 파이프라인")
    cands = [("실문서 절 작성 쌍", "양식과 완성본으로 운영 입력 형식의 쌍을 만든다"),
             ("문서 구조 단계 문답", "사업명부터 뼈대까지 단계별로 답한다"),
             ("근거 기반 연결 대화", "사업에서 절까지의 근거를 담은 대화"),
             ("운영 기록의 마스킹 사본", "에이전트 단계와 질문 해석 기록")]
    g = 12
    cw = (W - 2 * M - 2 * PADX - g) / 2
    ch = max(box_h(cw, t, [d]) for t, d in cands)
    bh = 32 + 2 * ch + g + 10
    s.band(M, y, W - 2 * M, bh, "학습 후보")
    for i, (t, d) in enumerate(cands):
        s.box(M + PADX + (i % 2) * (cw + g), y + 32 + (i // 2) * (ch + g), cw, ch, t, [d])
    left = [("자동 검사", "근거와 수치가 맞는지 확인한다"),
            ("사람 검수", "근거와 구조, 개인정보를 확인한다"),
            ("개인정보", "재마스킹 결과가 같은 파일만 쓴다"),
            ("분할 검사", "사업 단위로 나누고 중복을 없앤다")]
    right = [("학습", "LoRA 로 지도 미세조정(SFT)한다"),
             ("병합", "4비트로 양자화해 후보로 서빙한다"),
             ("비교", "같은 시험으로 기반 모델과 비교한다")]
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
    t1 = ("채택 기준 충족", ["해당 용도의 모델을 후보 모델로 바꾼다"])
    t2 = ("기준 미달", ["데이터를 보강해 다시 학습한다"])
    h5 = max(box_h(hw1, *t1), box_h(hw2, *t2))
    s.box(x2, y5, hw1, h5, *t1, strong=True)
    s.box(x2 + hw1 + 10, y5, hw2, h5, *t2, dashed=True)
    s.arrow([(x2 + nw / 2, ly2 + lh2), (x2 + nw / 2, y5)])
    s.arrow([(x2 + hw1 + 10 + hw2 / 2, ly2 + lh2), (x2 + hw1 + 10 + hw2 / 2, y5)])
    yb = s.note(max(ends[x1][-1][0] + ends[x1][-1][1], 0) + 18, "비교할 때는 모델만 바꾸고 질문과 플랫폼 코드, 생성 설정, 채점 모델은 그대로 둔다. "
                                                            "학습 데이터셋 원문은 학습 장비 밖으로 반출하지 않는다.", w=colw)
    s.fit(max(yb, y5 + h5))
    s.save("c4-38-sft-pipeline", "SFT 파이프라인")


if __name__ == "__main__":
    for f in (layers, build_flow, query_flow, folders, access, model_swap,
              doc_process, search_flow, ontology_rel, graph_rag, doc_agent, hwpx_fill, sft_pipeline):
        f()
    print("ok")
