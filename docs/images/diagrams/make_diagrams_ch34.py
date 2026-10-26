"""논문 3장(환경·설계)·4장(구현)의 도식 13종(SVG)을 만든다. 내용은 docs/paper/논문/03-환경-설계.md·04-구현.md 의
글 상자 그림과 그 앞뒤 본문 기준이다(본문에 없는 부품·수치는 넣지 않는다).

    python docs/images/diagrams/make_diagrams_ch34.py     # c3-*.svg · c4-*.svg 13개를 이 폴더에 쓴다
    node docs/images/diagrams/render.mjs                  # 이 폴더의 SVG 전부 → PNG(2배)

모양·색·글꼴은 make_diagrams.py 의 Svg, 작은 조각·노드는 make_diagrams_ch2.py 의 chip·node 를 그대로 쓴다.
"""
from __future__ import annotations

from make_diagrams import ACCENT, BORDER, FILL, INK, LINE, MUTED, NAVY, SOFT, Svg
from make_diagrams_ch2 import mono, node

W = 980


def tw(t: str, size: float) -> float:
    """글자 폭 어림(한글은 글자 크기만큼, 나머지는 0.6배)."""
    return sum(size * (1.0 if ord(c) >= 0x1100 and c not in "·" else 0.6) for c in t)


def pill(s: Svg, x, y, label, *, kind="plain", h=30, size=12.5, w=None):
    """글 길이에 맞춘 조각. kind: plain · ok(강조) · soft(옅은 바탕). 너비를 돌려준다."""
    w = w or tw(label, size) + 26
    stroke = NAVY if kind == "ok" else BORDER
    fill = {"plain": FILL, "ok": ACCENT, "soft": SOFT}[kind]
    s.out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{fill}" stroke="{stroke}" '
                 f'stroke-width="{1.3 if kind == "ok" else 1}"/>')
    s.text(x + w / 2, y + h / 2 + 4.5, label, size=size, weight=600 if kind == "ok" else 400,
           color=NAVY if kind == "ok" else INK, anchor="middle")
    return w


def step_row(s: Svg, x, y, w, name, detail, *, h=48, name_w=170, strong=False):
    """왼쪽 단계 이름 + 오른쪽 설명 한 줄."""
    s.out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{FILL}" stroke="{BORDER}"/>')
    s.out.append(f'<rect x="{x}" y="{y}" width="{name_w}" height="{h}" rx="9" fill="{ACCENT if strong else SOFT}" '
                 f'stroke="{NAVY if strong else BORDER}" stroke-width="{1.3 if strong else 1}"/>')
    s.text(x + 14, y + h / 2 + 5, name, size=13.5, weight=700, color=NAVY)
    s.text(x + name_w + 16, y + h / 2 + 4.5, detail, size=12.5)


def footer(s: Svg, y, *lines):
    for i, ln in enumerate(lines):
        s.text(28, y + i * 20, ln, size=12, color=MUTED)


# ── 3장 ──────────────────────────────────────────────────────────────


def layers():
    """[그림 3-1] 계층 구조."""
    spec = [
        ("화면층", "업무 화면과 개발자 화면", [
            [("채팅", 0), ("라이브러리", 0), ("프로젝트", 0), ("한글 문서함(기안기)", 0)],
            [("조직·구성원", 0), ("사업 관리", 0), ("업무 도우미 창", 0), ("|", 0), ("개발자 화면(/dev · 원본 관리)", 1)]]),
        ("앱층", "라우터 · 대화 · 에이전트", [
            [("영역별 라우터(문서 · 프로젝트 · 채팅 · 기안기 · 조직 · 개발 …)", 0)],
            [("답변기(responder)", 1), ("문서 작업 에이전트(doc_agent)", 1), ("업무 도우미", 1)],
            [("권한 판정(access_policy · grades · rag_spaces)", 0), ("문맥 맞추기(llm_budget)", 0)]]),
        ("도메인층", "처리 단계별 패키지", [
            [("ingest(읽기 · 개인정보 · 분류)", 0), ("→", 0), ("index(조각 · 색인)", 0), ("→", 0), ("retrieve(하이브리드)", 0)],
            [("graph(온톨로지 · 지식 그래프)", 0), ("generate(모델 연결 · 생성)", 0), ("verify(검증기)", 0)],
            [("dataset(학습 후보 · 검수 · 반출)", 0), ("export(산출 문서)", 0)]]),
        ("저장층", "DB 와 문서 폴더", [
            [("PostgreSQL(문서 · 조각 · 장부 · 그래프 · 권한 · 기록)", 1), ("+ pgvector(사업 문서 벡터)", 0)],
            [("문서 폴더(반입 · 첨부 · 생성 · 기안기)", 0), ("설정 · 감사 파일", 0)]]),
        ("모델층", "모두 교내 장비", [
            [("Writer 27B × 2(토르 02 · 03)", 1), ("Embed v2", 0), ("Rerank v1(토르 03)", 0)]]),
    ]
    rows = sum(len(r) for _, _, r in spec)
    s = Svg(W, 70 + rows * 40 + len(spec) * 30 + 40)
    s.text(28, 38, "AURA 계층 구조 — 화면 · 앱 · 도메인 · 저장 · 모델", size=17, weight=700, color=NAVY)
    y = 62
    for name, sub, rws in spec:
        h = len(rws) * 40 + 22
        s.out.append(f'<rect x="28" y="{y}" width="{W - 56}" height="{h}" rx="12" fill="{SOFT}" stroke="{BORDER}"/>')
        s.text(46, y + h / 2 - 2, name, size=15, weight=700, color=NAVY)
        s.text(46, y + h / 2 + 17, sub, size=11, color=MUTED)
        ry = y + 16
        for r in rws:
            x = 196
            for lab, k in r:
                if lab == "→":
                    s.arrow([(x - 2, ry + 15), (x + 16, ry + 15)])
                    x += 22
                    continue
                if lab == "|":
                    s.out.append(f'<line x1="{x + 6}" y1="{ry + 2}" x2="{x + 6}" y2="{ry + 28}" stroke="{LINE}" stroke-dasharray="3 3"/>')
                    x += 20
                    continue
                x += pill(s, x, ry, lab, kind="ok" if k else "plain") + 10
            assert x < W - 28, (name, x)
            ry += 40
        y += h + 10
    footer(s, y + 18, "구축 파이프라인(배치)과 질의 파이프라인(대화형)은 저장층에서만 만난다.")
    s.save("c3-01-layers", "AURA 계층 구조")


def build_flow():
    """[그림 3-2] 구축 흐름."""
    s = Svg(W, 790)
    s.text(28, 38, "구축 흐름 — 반입에서 색인 · 그래프까지", size=17, weight=700, color=NAVY)
    s.box(28, 62, 320, 72, "학교 원본", ["학습 장비 · 읽기 전용 연결"], fill=SOFT)
    s.box(632, 62, 320, 72, "업로드 · 채팅 첨부", ["구글 드라이브 가져오기"], fill=SOFT)
    s.box(28, 196, 320, 72, "원본 장부", ["archive_files · 원본 하나가 한 줄"])
    s.arrow([(188, 134), (188, 196)])
    s.text(200, 162, "10분 주기 목록 비교", size=11.5, color=MUTED)
    s.text(200, 180, "새로 · 바뀜 · 옮김 · 없어짐", size=11.5, color=MUTED)
    s.box(632, 196, 320, 72, "하나의 처리기", ["DocumentProcessor — 모든 길이 거침"], strong=True)
    s.arrow([(348, 232), (632, 232)])
    s.arrow([(792, 134), (792, 196)])
    # 처리기 안의 단계
    s.band(28, 296, W - 56, 344, "처리기 안의 단계")
    s.arrow([(792, 268), (792, 296)])
    steps = [("① 형식별 읽기", "디지털 PDF 글자층 · MinerU · 27B 이미지 판독 · kordoc · hwpx 파서 · 오피스"),
             ("② 표 정리", "칸 격자 [행, 열, 병합, 머리칸, 글] → 화면용 HTML 표 / 검색용 평문"),
             ("③ 개인정보", "Presidio + 한국형 인식기 8종, 「내부 마스킹 설정」은 항목별(기본 꺼짐)"),
             ("④ 조각 나누기", "블록 단위(최대 2,000자), 규정은 조문 단위, 쪽 번호 · 절 제목 경로"),
             ("⑤ 판정", "서류 종류(27B, 토르 03) · 사업 분류(장부 + 27B) · 문서 이름(본문 제목)"),
             ("⑥ 판본 묶기", "제목 열쇠 + 8자 조각 겹침(0.95 이상 같은 내용, 그 아래 판본)")]
    y = 328
    for i, (a, b) in enumerate(steps):
        step_row(s, 46, y, W - 92, a, b, h=40, name_w=150)
        if i < len(steps) - 1:
            s.arrow([(121, y + 40), (121, y + 52)])
        y += 52
    # 결과 셋
    outs = [("어휘 색인", ["Kiwi 명사 · IDF", "규정 / 사업 문서(GIN) 따로"]),
            ("의미 색인", ["Embed v2 → pgvector HNSW", "1분 주기"]),
            ("지식 그래프", ["사업 > 연차 > 문서 > 절", "계획↔실적 · 앞뒤 단계 · SHACL 검사", "· 15분 안 다시 지음"])]
    bw, gap = 296, 18
    for i, (t, ln) in enumerate(outs):
        x = 28 + i * (bw + gap)
        s.box(x, 676, bw, 100, t, ln, strong=(i == 2))
        s.arrow([(490, 640), (490, 658), (x + bw / 2, 658), (x + bw / 2, 676)])
    s.save("c3-02-build-flow", "구축 흐름")


def query_flow():
    """[그림 3-3] 질의 흐름."""
    s = Svg(W, 850)
    s.text(28, 38, "질의 흐름 — 검색 → 생성 → 검증", size=17, weight=700, color=NAVY)
    s.box(28, 62, 150, 64, "질문 · 요청")
    s.box(214, 62, 150, 64, "입력 정리")
    s.box(400, 62, 552, 64, "권한 점검", ["개인정보 요청 차단 · RAG 공간 결정 · 감사 기록"])
    s.arrow([(178, 94), (214, 94)])
    s.arrow([(364, 94), (400, 94)])
    s.box(250, 162, 480, 64, "문서 작업 에이전트", ["Writer 27B 가 단계마다 도구를 고름"], strong=True)
    s.arrow([(676, 126), (676, 144), (490, 144), (490, 162)])
    cols = [28, 342, 656]
    bw = 296
    heads = [("질문 읽기(27B)", ["사업 · 연도 · 종류 · 관계"]), ("근거 찾기 · 이어 읽기", []),
             ("양식 고르기 · 설계", ["절 쓰기"])]
    for x, (t, ln) in zip(cols, heads):
        s.box(x, 262, bw, 72, t, ln)
        s.arrow([(490, 226), (490, 244), (x + bw / 2, 244), (x + bw / 2, 262)])
    s.box(28, 364, bw, 52, "그래프 범위 정하기")
    s.arrow([(176, 334), (176, 364)])
    s.box(28, 446, 610, 72, "규정 검색 ∥ 사업 문서 검색", ["어휘 + 의미 RRF, 규정은 리랭커까지"])
    s.arrow([(176, 416), (176, 446)])
    s.arrow([(490, 334), (490, 446)])
    s.box(28, 548, 610, 56, "근거 묶기 · 문맥 한도 맞추기")
    s.arrow([(333, 518), (333, 548)])
    s.box(656, 548, bw, 56, "Writer 27B 생성", strong=True)
    s.arrow([(638, 576), (656, 576)])
    s.arrow([(804, 334), (804, 548)])
    s.band(28, 634, W - 56, 76, "검증")
    for i, t in enumerate(["수치 검증 — 근거 밖 숫자 표시 · 「○○」 가림", "양식 관문", "자가 점검"]):
        pill(s, [46, 470, 640][i], 664, t, w=[400, 150, 150][i])
    s.arrow([(804, 604), (804, 634)])
    s.box(28, 742, 430, 64, "출처 달린 답", ["채팅 화면"], strong=True)
    s.box(498, 742, 454, 64, "한글 작업본(기안기) · 독스 작업본", ["→ 원본 서식 완성본"], strong=True)
    s.arrow([(243, 710), (243, 742)])
    s.arrow([(725, 710), (725, 742)])
    footer(s, 832, "권한은 답을 만든 뒤가 아니라 검색 단계의 SQL 조건으로 건다. 화면 출처는 모델에 실제로 건넨 근거 목록에서만 만든다.")
    s.save("c3-03-query-flow", "질의 흐름")


def folders():
    """[그림 3-4] 1 층 문서 폴더 배치."""
    rows = [(1, "반입/<연도>/<유형>/<접수번호> <제목>/원본.<확장자>", "반입 원본과 추출 부산물"),
            (1, "첨부/<연도>/대화-<번호>/<시각> <파일명>", "채팅 첨부"),
            (1, "생성/<연도>/<접수번호>/<시각> <종류>.<확장자>", "초안 · 판독 · 복원 · 완성본 사본"),
            (1, "보고/주간/<월요일>.md", "주간 보고"),
            (1, "기안기/<부서>/<폴더>/<제목>.hwpx", "한글 문서함(부서 디렉터리)"),
            (2, ".판/ · .휴지통/", "부서마다 저장 전 판과 지운 문서"),
            (2, "공통/공통 양식/<목적>/", "어디서나 쓰는 빈 양식"),
            (2, "공통/지난 사업 문서/<사업>/<목적>/", "사업 전용 양식"),
            (2, "~<아이디>/", "내 폴더(처음 열 때 생김)")]
    rh, top = 40, 116
    s = Svg(W, top + len(rows) * rh + 80)
    s.text(28, 38, "1 층 문서 폴더 배치 — 문서마다 접수번호와 제목으로 된 폴더", size=17, weight=700, color=NAVY)
    s.out.append(f'<rect x="28" y="62" width="150" height="34" rx="7" fill="{ACCENT}" stroke="{NAVY}" stroke-width="1.3"/>')
    mono(s, 42, 84, "documents/", size=13.5, weight=700, color=NAVY)
    s.text(W - 28, 84, "하는 일", size=11.5, weight=600, color=MUTED, anchor="end")
    last1 = max(i for i, r in enumerate(rows) if r[0] == 1)
    s.out.append(f'<line x1="48" y1="96" x2="48" y2="{top + last1 * rh + 18}" stroke="{LINE}" stroke-width="1.3"/>')
    s.out.append(f'<line x1="88" y1="{top + last1 * rh + 34}" x2="88" y2="{top + (len(rows) - 1) * rh + 18}" stroke="{LINE}" stroke-width="1.3"/>')
    for i, (lv, path, desc) in enumerate(rows):
        y = top + i * rh
        x0 = 48 if lv == 1 else 88
        s.out.append(f'<line x1="{x0}" y1="{y + 18}" x2="{x0 + 22}" y2="{y + 18}" stroke="{LINE}" stroke-width="1.3"/>')
        s.out.append(f'<rect x="{x0 + 24}" y="{y + 2}" width="{W - 28 - x0 - 24}" height="32" rx="6" '
                     f'fill="{FILL if lv == 1 else SOFT}" stroke="{BORDER}"/>')
        mono(s, x0 + 36, y + 23, path, size=12.5, weight=600 if lv == 1 else 400, color=INK)
        s.text(W - 44, y + 23, desc, size=12.5, color=MUTED, anchor="end")
    footer(s, top + len(rows) * rh + 30, "파일의 실제 자리는 DB 의 파일 장부가 가리킨다. 폴더를 통째로 옮길 때는 경로 앞부분만 바꾸면 된다.",
           "경로 규칙은 app/paths.py 한 곳에서만 나오고, 새 반입 지점은 원본 채택 함수(storage.adopt_original)를 거친다.")
    s.save("c3-04-folders", "1 층 문서 폴더 배치")


def access():
    """[그림 3-5] 검색 단계의 권한 적용."""
    s = Svg(W, 660)
    s.text(28, 38, "검색 단계의 권한 적용 — 답을 만들기 전에 SQL 조건으로", size=17, weight=700, color=NAVY)
    s.box(28, 62, W - 56, 64, "로그인 계정", ["역할(교직원 · 학생 · 관리자) · 소속 부서 · 추가 권한 부서 · 등급"])
    s.box(380, 166, 572, 72, "rag_spaces.resolve — 검색 공간", ["student / dept:<부서> / staff / dev"])
    s.box(380, 278, 572, 72, "access_policy.doc_sql — 검색 SQL 의 WHERE", ["열람 등급 · 부서 · 종류 제외"])
    s.arrow([(666, 126), (666, 166)])
    s.arrow([(666, 238), (666, 278)])
    s.box(380, 394, 230, 82, "규정 검색", ["열람 등급"])
    s.box(634, 394, 318, 82, "사업 문서 검색", ["그래프 범위 · 절 축 조각", "· 공간 부서 · 증빙 종류 제외 · 다시 거름"])
    s.arrow([(666, 350), (666, 372), (495, 372), (495, 394)])
    s.arrow([(666, 350), (666, 372), (793, 372), (793, 394)])
    s.box(380, 518, 572, 64, "모델에 건넨 근거 = 화면 출처", ["같은 목록에서 만든다"], strong=True)
    s.arrow([(495, 476), (495, 518)])
    s.arrow([(793, 476), (793, 518)])
    # 범위 밖 부서
    s.box(28, 288, 300, 52, "질문이 범위 밖 부서를 가리킴", [], dashed=True, fill=SOFT)
    s.box(28, 394, 300, 82, "「열람 권한 없음」 안내", ["+ 감사 기록", "· 부서 이름만 싣고 제목 · 건수는 말하지 않음"])
    s.arrow([(178, 126), (178, 288)])
    s.arrow([(178, 340), (178, 394)])
    footer(s, 620, "모르는 역할은 가장 좁은 학생 범위로 본다. 같은 규칙을 화면 판정 함수와 SQL 조건 두 꼴로 내고, 모든 화면과 검색이 이것을 쓴다.")
    s.save("c3-05-access", "검색 단계의 권한 적용")


def model_swap():
    """[그림 3-7] 용도별 연결과 모델 교체 구조."""
    s = Svg(W, 560)
    s.text(28, 38, "용도별 연결과 모델 교체 구조 — 모델 이름이 아니라 용도로 부른다", size=17, weight=700, color=NAVY)
    s.box(28, 62, 330, 104, "호출하는 쪽", ["답변기 · 에이전트 · 판정 · 도우미", "· 용도 이름만 넘김", "· answer / review / vision / helper"])
    s.box(28, 206, 330, 128, "연결 설정", ["llm_connections", "서버 · 모델 · 생각 끄기 인자", "구조화 출력 방식 · 동시 상한",
                                       "· 「도구 · 모델 연결」(/dev/tools)"], strong=True)
    s.arrow([(193, 166), (193, 206)])
    rules = [("문맥 한도", "서버가 알려 주는 max_model_len 먼저, 없으면 설정값"),
             ("구조화 출력", "JSON 스키마 강제, 서버가 거절하면 JSON 지시 + 파싱으로 물러남"),
             ("장애", "같은 모델을 내는 다른 교내 연결로 넘김(외부로는 넘기지 않음)"),
             ("기록", "결과마다 서버가 알려 준 실제 모델 정보(주소 · 가중치 경로는 빼고)")]
    ys = []
    for i, (a, b) in enumerate(rules):
        y = 62 + i * 70
        step_row(s, 418, y, 534, a, b, h=56, name_w=104)
        ys.append(y + 28)
    s.out.append(f'<path d="M358,270 L388,270" fill="none" stroke="{LINE}" stroke-width="1.3"/>')
    s.out.append(f'<path d="M388,{ys[0]} L388,{ys[-1]}" fill="none" stroke="{LINE}" stroke-width="1.3"/>')
    for y in ys:
        s.arrow([(388, y), (418, y)])
    s.box(28, 380, 590, 84, "교체 점검 (scripts/195)", ["후보 모델을 운영 설정을 바꾸지 않고 같은 데이터로 나란히 측정",
                                                  "· 검색 · 답 · 종류 판정"])
    s.box(662, 380, 290, 84, "채택 결정", ["비교표로 정한 뒤 용도별", "서버만 바꿔 저장"], strong=True)
    s.arrow([(193, 334), (193, 380)])
    s.arrow([(618, 422), (662, 422)])
    footer(s, 500, "모델 이름 · 문맥 길이 · 생각 끄기 인자를 코드에 박지 않는다. 용도별 서버를 바꾸고 저장하면 코드 수정 없이 모델이 바뀐다.",
           "문맥은 비율로 나눈다(작업 메모 6 %, 최근 대화 30 %). 문맥이 긴 모델로 바꾸면 저절로 맞는다.")
    s.save("c3-07-model-swap", "용도별 연결과 모델 교체 구조")


# ── 4장 ──────────────────────────────────────────────────────────────


def doc_process():
    """[그림 4-4] 문서 한 건의 처리 순서."""
    rows = [("디지털 PDF", "표본 6쪽 글자 충분 · 깨진 글자 비율 < 0.15",
             "pdfium 글자 좌표 직독 + MinerU 구조(표 · 2단 · 읽기 순서) 위에 원본 글자 덮어쓰기"),
            ("스캔 PDF", "쪽당 200자 미만 또는 깨진 글자 ≥ 0.15", "기본 MinerU, 짧으면 27B 이미지 판독(최대 30쪽)"),
            ("사진", "", "27B 이미지 판독 → MinerU OCR → tesseract"),
            ("hwp", "", "kordoc(판 고정) → 실패 시 pyhwp"),
            ("hwpx", "", "자체 OWPML 파서(표 · 글상자 · 머리말)"),
            ("docx · xlsx · pptx", "쪽당 60자 미만이면 MinerU 로 다시", "kordoc → python-docx · openpyxl · python-pptx"),
            ("옛 형식", "xls · doc · ppt · hwp3", "LibreOffice 로 새 형식 변환 뒤 위의 길")]
    s = Svg(W, 740)
    s.text(28, 38, "문서 한 건의 처리 순서 — 형식별 읽기 뒤에는 모두 같은 길", size=17, weight=700, color=NAVY)
    s.box(28, 62, 420, 64, "원본 파일", ["실제 형식 확인(확장자가 없거나 틀리면 파일 머리로)"], fill=SOFT)
    y, rh = 150, 54
    s.out.append(f'<line x1="48" y1="126" x2="48" y2="{y + (len(rows) - 1) * rh + 23}" stroke="{LINE}" stroke-width="1.3"/>')
    for name, cond, path in rows:
        s.arrow([(48, y + 23), (70, y + 23)])
        s.out.append(f'<rect x="70" y="{y}" width="{W - 98}" height="46" rx="8" fill="{FILL}" stroke="{BORDER}"/>')
        s.text(86, y + 28, name, size=13.5, weight=700, color=NAVY)
        if cond:
            s.text(250, y + 19, cond, size=11.5, color=MUTED)
            s.text(250, y + 37, path, size=12.5)
        else:
            s.text(250, y + 28, path, size=12.5)
        y += rh
    s.band(28, y + 30, W - 56, 152, "형식별 읽기 다음")
    s.arrow([(490, y - 8), (490, y + 30)])
    steps = [["표 정리", "개인정보(4.2)", "조각 나누기", "서류 종류(27B)"],
             ["사업 분류", "판본 묶기", "문서 이름", "색인(4.3) · 그래프(4.4)"]]
    cw, gap = 190, 42
    ry = y + 62
    for r, row in enumerate(steps):
        for c, lab in enumerate(row):
            x = 56 + c * (cw + gap)
            pill(s, x, ry, lab, w=cw, h=34, kind="ok" if lab.startswith("색인") else "plain")
            if c < 3:
                s.arrow([(x + cw, ry + 17), (x + cw + gap, ry + 17)])
        if r == 0:
            x3 = 56 + 3 * (cw + gap) + cw / 2
            s.arrow([(x3, ry + 34), (x3, ry + 46), (56 + cw / 2, ry + 46), (56 + cw / 2, ry + 62)])
        ry += 62
    s.save("c4-04-doc-process", "문서 한 건의 처리 순서")


def search_flow():
    """[그림 4-8] 검색의 흐름."""
    s = Svg(W, 720)
    s.text(28, 38, "검색의 흐름 — 규정 검색과 사업 문서 검색", size=17, weight=700, color=NAVY)
    s.box(28, 62, 150, 60, "질문")
    s.box(214, 62, 250, 60, "입력 정리", ["전각 → 반각"])
    s.box(500, 62, 452, 60, "RAG 공간 · 열람 조건", ["검색 SQL 의 WHERE"])
    s.arrow([(178, 92), (214, 92)])
    s.arrow([(464, 92), (500, 92)])
    s.arrow([(726, 122), (726, 160)])
    s.arrow([(726, 140), (254, 140), (254, 160)])
    s.band(28, 160, 452, 400, "규정 검색")
    s.band(500, 160, 452, 400, "사업 문서 검색 — 그래프 범위 안(4.4)")
    ys, h = [192, 282, 372, 462], 66
    L, R, iw = 44, 516, 420
    half = (iw - 12) / 2
    # 규정
    s.box(L, ys[0], half, h, "어휘", ["Kiwi 명사 + 영문 약칭, IDF"])
    s.box(L + half + 12, ys[0], half, h, "의미", ["Embed v2(토르 03)"])
    s.box(L, ys[1], iw, h, "RRF 순위 융합", ["k=60 · 어휘 0.4 · 의미 1.0"])
    s.box(L, ys[2], iw, h, "리랭커", ["후보 20 → Rerank v1(토르 03)"], strong=True)
    s.box(L, ys[3], iw, h, "꼬리 버림", ["1위 점수의 0.25배 아래"])
    # 사업 문서
    s.box(R, ys[0], iw, h, "그래프 절 축 먼저", ["묶어 적은 절 제목"])
    s.box(R, ys[1], half, h, "어휘", ["grant_lex(GIN)"])
    s.box(R + half + 12, ys[1], half, h, "의미", ["grant_vec(HNSW)"])
    s.box(R, ys[2], iw, h, "RRF 순위 융합", ["같은 가중, 어휘 1위 조각 지킴"])
    s.box(R, ys[3], iw, h, "고르기", ["판본 최종본만 · 문서당 2조각 · 상위 5"])
    for x0 in (L, R):
        for a in range(3):
            if (x0 == L and a == 0) or (x0 == R and a == 1):
                for cx in (x0 + half / 2, x0 + half + 12 + half / 2):
                    s.arrow([(cx, ys[a] + h), (cx, ys[a + 1])])
            else:
                s.arrow([(x0 + iw / 2, ys[a] + h), (x0 + iw / 2, ys[a + 1])])
    s.box(28, 600, 600, 64, "근거 묶기", ["규정 묶음 / 사업 문서 묶음"])
    s.box(672, 600, 280, 64, "생성 (4.5)", ["Writer 27B"], strong=True)
    s.arrow([(254, 560), (254, 600)])
    s.arrow([(726, 560), (726, 580), (560, 580), (560, 600)])
    s.arrow([(628, 632), (672, 632)])
    footer(s, 700, "어휘 축은 색인과 질의가 같은 낱말 함수(lex_terms)를 쓴다. 리랭커는 규정 검색에만 쓴다.")
    s.save("c4-08-search-flow", "검색의 흐름")


def ontology_rel():
    """[그림 4-12] 온톨로지의 노드와 관계."""
    s = Svg(W, 560)
    s.text(28, 38, "온톨로지의 노드와 관계", size=17, weight=700, color=NAVY)
    Wn, Hn, Y = 140, 60, 236
    xs = {k: 28 + i * 195 for i, k in enumerate(("묶음", "사업", "연차", "문서", "절"))}
    node(s, xs["묶음"], Y, Wn, Hn, "사업 묶음")
    node(s, xs["사업"], Y, Wn, Hn, "사업", strong=True)
    node(s, xs["연차"], Y, Wn, Hn, "연차")
    node(s, xs["문서"], Y, Wn, Hn, "문서", "서류 종류가 하위 분류")
    node(s, xs["절"], Y, Wn, Hn, "절")
    for a, b in (("묶음", "사업"), ("사업", "연차"), ("연차", "문서"), ("문서", "절")):
        s.arrow([(xs[a] + Wn, Y + 30), (xs[b], Y + 30)], "contains", ly=Y + 22)
    # 위: 앞뒤 사업, 짝 문서, 평가 문서
    node(s, xs["사업"], 76, Wn, Hn, "사업", "앞 · 뒤 단계 사업", dashed=True)
    s.arrow([(xs["사업"] + 70, Y), (xs["사업"] + 70, 136)])
    s.text(xs["사업"] - 8, 182, "succeeded_by · integrated_into", size=11.5, color=MUTED, anchor="end")
    s.text(xs["사업"] - 8, 200, "(외부 확인 장부)", size=11.5, color=MUTED, anchor="end")
    node(s, xs["연차"], 76, Wn, Hn, "문서", "계획서 ↔ 실적보고서", dashed=True)
    s.arrow([(xs["문서"] + 40, Y), (xs["문서"] + 40, 176), (xs["연차"] + 70, 176), (xs["연차"] + 70, 136)], both=True)
    s.text((xs["연차"] + 70 + xs["문서"] + 40) / 2, 168, "plans_reports (계획↔실적)", size=11.5, color=MUTED, anchor="middle")
    node(s, xs["문서"], 76, Wn, Hn, "문서(평가)", "평가 문서", dashed=True)
    s.arrow([(xs["문서"] + 105, 136), (xs["문서"] + 105, Y)])
    s.text(xs["문서"] + 113, 196, "evaluates", size=11.5, color=MUTED)
    # 아래: 기관, 성과지표, 단위과제
    node(s, xs["사업"], 396, Wn, Hn, "기관", "부처 · 전담기관")
    s.arrow([(xs["사업"] + 70, Y + Hn), (xs["사업"] + 70, 396)])
    s.text(xs["사업"] - 8, 338, "supervised_by · managed_by", size=11.5, color=MUTED, anchor="end")
    node(s, xs["문서"], 396, Wn, Hn, "성과지표", "기준 · 목표 · 실적 · 달성률")
    s.arrow([(xs["문서"] + 70, Y + Hn), (xs["문서"] + 70, 396)])
    s.text(xs["문서"] + 78, 338, "측정(표 칸에서 읽은 값)", size=11.5, color=MUTED)
    node(s, xs["절"], 396, Wn, Hn, "단위과제")
    s.arrow([(xs["절"] + 70, Y + Hn), (xs["절"] + 70, 396)])
    s.text(xs["절"] + 78, 330, "같은 사업에서", size=11.5, color=MUTED)
    s.text(xs["절"] + 78, 348, "되풀이", size=11.5, color=MUTED)
    footer(s, 504, "관계마다 기준(구조 · 분류 · 식별자 일치 · 추출 · 유사도 가운데 하나)과 근거(문서 · 절 위치 또는 외부 출처 URL)를 단다.",
           "점선 상자는 같은 종류의 다른 노드다. 그래프를 다시 지을 때마다 SHACL 규칙(graph/shapes.ttl)으로 검사한다.")
    s.save("c4-12-ontology", "온톨로지의 노드와 관계")


def graph_rag():
    """[그림 4-13] 27B 질문 읽기와 그래프 범위 검색."""
    s = Svg(W, 790)
    s.text(28, 38, "27B 질문 읽기와 그래프 범위 검색 — 뜻은 모델이 읽고 탐색은 코드가", size=17, weight=700, color=NAVY)
    bw = 640
    s.box(28, 62, bw, 84, "질문 + 사업 후보 목록 + 관계 7종", ["사업 후보는 질문과 겹치는 사업, 번호를 붙여 건넴"], tag="입력")
    s.box(28, 180, bw, 84, "Writer 27B 질문 읽기", ["온도 0 · 출력 120토큰 · JSON 스키마 강제 · 같은 질문 10분 기억"], tag="모델", strong=True)
    s.box(28, 298, bw, 84, "{사업 번호, 연도, 차수, 종류, 따라갈 관계}", ["종류는 계획 · 실적 · 평가, 사업은 목록 번호로만 받음"], tag="모델이 낸 값")
    s.box(28, 416, bw, 124, "범위 정하기", ["이름 맞추기 + 모델 읽기 보탬 → 관계 따라 넓히기(짝 · 평가 · 앞뒤 단계 · 편입)",
                                         "· 연도를 물으면 연도 모르는 문서 제외",
                                         "· 「」로 묶은 이름이 문서 · 절 이름이면 그것으로 좁힘"], tag="코드(결정적)")
    s.box(28, 574, bw, 104, "범위 안 하이브리드 검색 (4.3)", ["관계 질문은 양쪽 문서에서 한 조각씩",
                                                        "· 절 축 조각도 권한을 다시 거름"], tag="코드")
    s.box(28, 712, bw, 52, "근거 → 답", strong=True)
    for a, b in ((146, 180), (264, 298), (382, 416), (540, 574), (678, 712)):
        s.arrow([(348, a), (348, b)])
    s.arrow([(668, 222), (740, 222), (740, 478), (668, 478)], dashed=True)
    for i, t in enumerate(["모델 읽기가 실패하면", "이름 맞추기로 물러남"]):
        s.text(756, 342 + i * 19, t, size=12, color=MUTED)
    s.save("c4-13-graph-rag", "27B 질문 읽기와 그래프 범위 검색")


def doc_agent():
    """[그림 4-21] 문서 작업 에이전트의 단계 구조와 관문."""
    s = Svg(W, 630)
    s.text(28, 38, "문서 작업 에이전트 — 모델이 고르고 코드 관문이 지킨다", size=17, weight=700, color=NAVY)
    bw = 640
    s.box(28, 62, bw, 98, "상태", ["요청 · 작업 메모 · 최근 대화 · 프로젝트 문서 목록",
                                  "작업본 절 목록과 빈 절 · 받지 못한 값 · 지금까지 한 단계"], tag="자료 구분 기호 <<< >>> 안에만 넣음")
    s.box(28, 192, bw, 64, "Writer 27B", ["{생각, 도구, 인자} 를 JSON 으로"], strong=True)
    s.box(28, 288, bw, 104, "코드 관문", ["단계 14번 · 문서 쓰기 4번까지 · 같은 단계 세 번 잇달으면 멈춤",
                                      "되돌리기 어려운 도구는 담당자 요청 글에 그 뜻이 부정 없이 있을 때만",
                                      "찾기 전 사실 답 금지(먼저 찾기) · 열람 범위 · 수치 근거 대조 · 양식 모양"])
    s.box(28, 424, bw, 84, "도구 실행", ["찾기 · 읽기 · 양식 · 쓰기 · 고치기 · 묻기 · 내보내기 …",
                                      "· 결과는 자료로만 돌려줌(자료 속 구분 기호 · 머리말은 전각으로)"])
    s.box(28, 540, bw, 64, "모든 단계 기록 (doc_agent_episodes)", ["리즈닝 학습 재료"])
    for a, b in ((160, 192), (256, 288), (392, 424), (508, 540)):
        s.arrow([(348, a), (348, b)])
    s.arrow([(668, 466), (704, 466), (704, 111), (668, 111)])
    s.text(714, 460, "다음 단계", size=12.5, weight=600, color=MUTED)
    s.out.append(f'<rect x="734" y="176" width="218" height="96" rx="10" fill="{SOFT}" stroke="{LINE}" stroke-dasharray="5 4"/>')
    for i, t in enumerate(["한도에 닿았는데 찾은", "근거가 있으면 「답하기만", "고를 수 있는 마지막 단계」"]):
        s.text(750, 204 + i * 20, t, size=12, color=INK)
    s.out.append(f'<path d="M734,224 L668,224" fill="none" stroke="{LINE}" stroke-width="1.3" stroke-dasharray="4 3"/>')
    s.save("c4-21-doc-agent", "문서 작업 에이전트의 단계 구조와 관문")


def hwpx_fill():
    """[그림 4-26] 원본 서식 보존 채우기."""
    s = Svg(W, 780)
    s.text(28, 38, "원본 서식 보존 채우기 — 작업본의 절 본문을 원본 hwpx 에 옮긴다", size=17, weight=700, color=NAVY)
    s.box(28, 62, 440, 78, "원본 hwpx (ZIP)", ["구역 XML 을 다시 직렬화하지 않음"], fill=SOFT)
    s.box(512, 62, 440, 78, "작업본 절 본문", ["독스 또는 기안기 · 글 · 표 · 그림(안내 상자 제외)"], fill=SOFT)
    s.box(28, 178, W - 56, 70, "절 제목 문단 찾기", ["번호 · 제목 글자로 맞춤(또는 그 뒤 【작성방법】 상자)"], strong=True)
    s.arrow([(248, 140), (248, 178)])
    s.arrow([(732, 140), (732, 178)])
    cases = [("서식에 이미 있는 문단 · 표", "다시 넣지 않고 그 자리를 지나 이어 씀"),
             ("서식 표를 채웠을 때", "작업본 열 너비로 값 칸 x 위치를 재어 원래 칸에 써 넣음"),
             ("새 표", "id 는 문서 최댓값 다음부터, 테두리는 그 구역 본문 표의 가장 흔한 실선을 이어받음"),
             ("끼워 넣기", "글자열 위치에 문단 · 표 · 그림 XML (손대지 않은 문단은 한 글자도 안 바뀜)")]
    y = 280
    s.out.append(f'<line x1="48" y1="248" x2="48" y2="{y + 3 * 56 + 24}" stroke="{LINE}" stroke-width="1.3"/>')
    for a, b in cases:
        s.arrow([(48, y + 24), (70, y + 24)])
        step_row(s, 70, y, W - 98, a, b, name_w=210)
        y += 56
    s.box(28, y + 30, W - 56, 70, "다시 쓰기", ["바뀐 구역만 줄 배치 캐시 지움 · ZIP 항목 순서와 압축 방식 유지 · 바뀐 항목만 다시 씀"])
    s.arrow([(490, y - 8), (490, y + 30)])
    y += 136
    s.box(28, y, 620, 70, "자가 점검", ["ZIP 규약 · XML 형식 · 참조 id · 항목 수 · 표 격자"])
    s.box(712, y, 240, 70, "저장", ["문제 목록이 비었을 때만"], strong=True)
    s.arrow([(490, y - 36), (490, y)])
    s.arrow([(648, y + 35), (712, y + 35)], "통과", ly=y + 27)
    footer(s, y + 110, "이 층은 내용을 만들지 않는다. 원본이 옛 .hwp 면 채우지 않고 hwpx 로 저장한 서식의 반입을 안내한다.")
    s.save("c4-26-hwpx-fill", "원본 서식 보존 채우기")


def sft_pipeline():
    """[그림 4-38] SFT 파이프라인."""
    s = Svg(W, 830)
    s.text(28, 38, "SFT 파이프라인 — 학습 데이터에서 운영 교체 결정까지", size=17, weight=700, color=NAVY)
    right = W - 60
    s.band(28, 62, right - 28, 160, "학습 후보")
    cands = [("실문서 절 작성쌍 (scripts/152)", "양식 × 완성본 · 서빙 프롬프트 꼴 → 편집 계획 JSON"),
             ("문서 구조 단계 문답 (scripts/153)", "[N단계] 사업명 → 개요 → 목차 → 요구 항목 → 뼈대"),
             ("근거 기반 연결 대화", "사업 · 연차 · 문서 · 절 경로와 근거 위치, 2~6턴"),
             ("운영 기록 — 가린 사본", "에이전트 단계 · 질문 읽기 · 양식 고르기 · 못 채운 까닭")]
    cw = (right - 28 - 16 * 3) / 2
    for i, (t, ln) in enumerate(cands):
        s.box(44 + (i % 2) * (cw + 16), 88 + (i // 2) * 64, cw, 56, t, [], fill=FILL)
        s.text(44 + (i % 2) * (cw + 16) + 16, 88 + (i // 2) * 64 + 46, ln, size=12, color=MUTED)
    steps = [("자동 검사", "경로 · 근거 기록, 근거가 그 턴에 글자 그대로, 숫자가 근거에(수치 검증기와 같은 함수), 순서, 중복"),
             ("사람 검수", "Label Studio — 근거 · 구조 · 맥락 · 개인정보 네 항목 확인, 채택 · 수정만 승인본"),
             ("개인정보", "내부 설정과 상관없이 전체 탐지, 다시 가려도 바뀌지 않는 파일만 반출(fail-closed)"),
             ("묶음 검사", "사업 단위 학습 · 검증 · 시험 분리와 중복을 전체에서 다시"),
             ("학습", "학습 장비 · TRL · PEFT, bf16 LoRA SFT, 문맥 16,384, 베이스라인 없으면 시작 안 함"),
             ("병합 · 재양자화", "병합 → NVFP4 → 서빙 장비에 후보 이름으로 올림 (scripts/116)"),
             ("비교 (ADR-0069)", "학습이 서빙에 들어갔나(정답 로그확률) → 같은 시험으로 오픈 모델과")]
    y = 254
    s.arrow([(113, 222), (113, y)])
    for i, (a, b) in enumerate(steps):
        step_row(s, 28, y, right - 28, a, b, h=46, name_w=170, strong=(a == "학습"))
        if i < len(steps) - 1:
            s.arrow([(113, y + 46), (113, y + 62)])
        y += 62
    y += 14
    s.arrow([(113, y - 30), (113, y)])
    s.box(28, y, 420, 64, "채택 기준을 넘으면", ["용도를 후보 모델로 옮김"], strong=True)
    s.box(492, y, right - 492, 64, "넘지 못하면", ["학습셋을 보강해 같은 절차로 다시"], dashed=True, fill=SOFT)
    s.arrow([(113, y - 16), (670, y - 16), (670, y)])
    s.arrow([(right, y + 32), (W - 30, y + 32), (W - 30, 142), (right, 142)], dashed=True)
    footer(s, y + 100, "비교에서는 모델 하나만 바꾸고 질문 · 플랫폼 코드 · 생성 설정 · 채점 모델은 그대로 둔다.",
           "학습셋 원문은 학습 장비 밖으로 나가지 않는다.")
    s.save("c4-38-sft-pipeline", "SFT 파이프라인")


if __name__ == "__main__":
    for f in (layers, build_flow, query_flow, folders, access, model_swap,
              doc_process, search_flow, ontology_rel, graph_rag, doc_agent, hwpx_fill, sft_pipeline):
        f()
    print("ok")
