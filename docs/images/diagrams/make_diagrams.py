"""README 구성도 4종(SVG)을 만든다. 내용은 PROJECT_BRIEF 4장, docs/architecture.md, docs/dev-now.md, app/model_tracks.py 기준.

    python docs/images/diagrams/make_diagrams.py          # SVG 4개를 이 폴더에 쓴다
    node docs/images/diagrams/render.mjs                  # SVG → PNG(2배, README 가 쓰는 그림)

글꼴은 Pretendard → Noto Sans KR → Apple SD Gothic Neo → 맑은 고딕 순으로 고른다. 모양을 바꾸면 두 단계를 다시 돌린다.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
FONT = "Pretendard, 'Noto Sans KR', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"
NAVY, INK, MUTED, LINE, BORDER = "#1b3b75", "#1f2937", "#64748b", "#94a3b8", "#cbd5e1"
FILL, ACCENT, SOFT = "#ffffff", "#eef2f9", "#f8fafc"


class Svg:
    def __init__(self, w: int, h: int):
        self.w, self.h, self.out = w, h, []

    def box(self, x, y, w, h, title, lines=(), *, tag="", strong=False, dashed=False, fill=None):
        stroke = NAVY if strong else BORDER
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        self.out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill or (ACCENT if strong else FILL)}" '
                        f'stroke="{stroke}" stroke-width="{1.4 if strong else 1}"{dash}/>')
        ty = y + 26
        if tag:
            self.text(x + 16, y + 22, tag, size=11, color=MUTED)
            ty = y + 44
        self.text(x + 16, ty, title, size=15, weight=700, color=NAVY if strong else INK)
        for i, ln in enumerate(lines):
            self.text(x + 16, ty + 22 + i * 19, ln, size=12.5, color=INK if not ln.startswith("·") else MUTED)

    def text(self, x, y, s, *, size=13, weight=400, color=INK, anchor="start"):
        self.out.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{color}" '
                        f'text-anchor="{anchor}">{escape(s)}</text>')

    def arrow(self, pts, label="", *, dashed=False, lx=None, ly=None, color=LINE, both=False):
        d = "M" + " L".join(f"{x},{y}" for x, y in pts)
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        start = ' marker-start="url(#tail)"' if both else ""
        self.out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.3"{dash} marker-end="url(#head)"{start}/>')
        if label:
            (x1, y1), (x2, y2) = pts[0], pts[-1]
            self.text(lx if lx is not None else (x1 + x2) / 2, ly if ly is not None else (y1 + y2) / 2 - 7, label,
                      size=11.5, color=MUTED, anchor="middle")

    def band(self, x, y, w, h, label):
        self.out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{SOFT}" stroke="{BORDER}" stroke-dasharray="3 4"/>')
        self.text(x + 14, y + 20, label, size=11.5, weight=600, color=MUTED)

    def save(self, name: str, title: str):
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}" '
                f'font-family="{FONT}"><title>{escape(title)}</title>'
                '<defs><marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                f'<path d="M0,0 L10,5 L0,10 z" fill="{LINE}"/></marker>'
                '<marker id="tail" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                f'<path d="M0,0 L10,5 L0,10 z" fill="{LINE}"/></marker></defs>'
                f'<rect width="{self.w}" height="{self.h}" fill="#ffffff"/>')
        (HERE / f"{name}.svg").write_text(head + "".join(self.out) + "</svg>\n", encoding="utf-8")


def system():
    s = Svg(790, 492)
    s.text(28, 38, "전체 구성 — 장비와 하는 일", size=17, weight=700, color=NAVY)
    s.box(28, 80, 190, 112, "학교 원본 문서", ["부서·사업 폴더의", "공고·계획서·보고서·규정"], tag="입력")
    s.box(28, 250, 190, 150, "교직원", ["브라우저로 접속", "채팅·문서함·한글 기안기", "· 로그인·부서별 열람 범위"], tag="사용자")
    s.box(276, 80, 210, 150, "DGX", ["원본 보관·동기화", "문서 일괄 읽기(MinerU OCR)", "Writer LoRA 학습", "· GB10 · 메모리 121GB"], tag="원본·처리·학습")
    s.box(276, 270, 210, 150, "운영 서버 (VM)", ["AURA 웹 화면·API", "PostgreSQL · pgvector 색인", "지식 그래프 · 한글 기안기", "· 상시 가동, 외부 AI 호출 없음"],
          tag="플랫폼·데이터", strong=True)
    s.box(560, 80, 200, 132, "젯슨 AGX 토르 02", ["Writer 27B (NVFP4)", "대화·초안·문서 에이전트", "· 사람이 기다리는 일"], tag="실시간 모델")
    s.box(560, 260, 200, 132, "젯슨 AGX 토르 03", ["Writer 27B (NVFP4)", "반입 검토·이미지 판독", "임베딩·리랭커"], tag="실시간 모델")
    s.arrow([(218, 136), (276, 136)], "동기화", ly=128)
    s.arrow([(381, 230), (381, 270)], "읽은 문서·판정", lx=440, ly=254)
    s.arrow([(218, 330), (276, 330)], "HTTPS", ly=322)
    s.arrow([(486, 330), (523, 330), (523, 146), (560, 146)], "", both=True)
    s.arrow([(486, 345), (560, 345)], "", both=True)
    s.text(660, 241, "모델 호출(교내망)", size=11.5, color=MUTED, anchor="middle")
    s.arrow([(486, 100), (560, 100)], "학습본 이관", dashed=True, ly=92)
    s.text(28, 452, "학습본은 bf16 병합 → NVFP4 양자화 뒤 토르로 옮긴다. 작업본은 학교 Google Docs(선택)에 둘 수 있고,", size=12, color=MUTED)
    s.text(28, 472, "문서와 첨부는 교외 AI 서비스로 나가지 않는다.", size=12, color=MUTED)
    s.save("01-system", "AURA 전체 구성")


def flow():
    s = Svg(982, 330)
    s.text(28, 38, "문서가 답과 초안이 되기까지", size=17, weight=700, color=NAVY)
    steps = [("원본", ["PDF · HWP/HWPX", "오피스 · 이미지"]),
             ("읽기", ["글자층 직독", "OCR · 한글 변환"]),
             ("등록·분류", ["서류 갈래 · 사업", "부서 · 열람 권한"]),
             ("색인·그래프", ["어휘 + 의미 검색", "사업·문서·절 관계"]),
             ("에이전트", ["Writer 27B", "찾기 · 쓰기 · 검토"]),
             ("결과", ["출처 단 답", "한글 작업본 · HWPX"])]
    x, w, gap = 28, 136, 22
    for i, (t, ln) in enumerate(steps):
        s.box(x, 76, w, 104, t, ln, strong=(t == "에이전트"))
        if i < len(steps) - 1:
            s.arrow([(x + w, 128), (x + w + gap, 128)])
        x += w + gap
    s.band(28, 206, 926, 92, "코드가 지키는 관문")
    gates = [("열람 범위", "검색 단계에서 권한 밖 문서를 뺀다"), ("수치 근거 대조", "근거에 없는 숫자는 「○○」로 가린다"),
             ("양식 모양", "원본 서식은 두고 내용만 채운다"), ("사람 확인", "모르는 값은 담당자에게 묻는다")]
    for i, (a, b) in enumerate(gates):
        gx = 46 + i * 228
        s.text(gx, 252, a, size=13.5, weight=700, color=INK)
        s.text(gx, 274, b, size=12, color=MUTED)
    s.save("02-doc-flow", "문서가 답과 초안이 되기까지")


def hwp():
    s = Svg(1000, 470)
    s.text(28, 38, "한글 웹 기안기 — 같은 문서를 여럿이 열 때", size=17, weight=700, color=NAVY)
    s.box(28, 74, 220, 96, "사용자 A", ["먼저 연 사람 — 편집 잠금", "고치면 2초 뒤 자동 저장"], tag="편집")
    s.box(28, 186, 220, 96, "사용자 B", ["「A 님이 편집 중 — 보기 전용」", "A 가 닫으면 잠금을 이어받음"], tag="대기")
    s.box(28, 298, 220, 96, "AURA 에이전트", ["대화 요청으로 칸·절을 씀", "편집 중인 화면에 바로 반영"], tag="채팅", strong=True)
    s.box(330, 74, 330, 320, "AURA 플랫폼 (운영 서버)", [
        "로그인·열람 범위 확인 뒤 기안기로 넘김",
        "",
        "편집 잠금 — 한 문서에 한 사람",
        "· 45초마다 늘리고, 끊기면 2분 뒤 풀림",
        "· 잠금 없는 저장은 거절(409)",
        "",
        "칸 글 실시간 전달 — AURA 가 쓴 칸",
        "· 편집 화면 머리줄 「AURA 작성 중 · 절」",
        "",
        "저장 — 앞 판은 10분에 하나, 30개까지",
        "에이전트 쓰기도 같은 판 기록(고친 사람 AURA)",
    ], strong=True)
    s.box(742, 74, 230, 132, "기안기 서버", ["웹 한글 편집 엔진", "운영 서버 안에서만 열림", "· 밖에서 직접 접속 불가"], tag="내부")
    s.box(742, 262, 230, 132, "한글 문서함", ["부서 폴더 · 내 폴더", "공통 양식 · 지난 사업 문서", "· 앞 판 · 휴지통"], tag="파일")
    for y in (122, 234):
        s.arrow([(248, y), (330, y)], "", both=True)
    s.arrow([(248, 346), (330, 346)], "칸·절 쓰기", ly=338)
    s.arrow([(660, 140), (742, 140)], "프록시", ly=132)
    s.arrow([(660, 328), (742, 328)], "저장·판", ly=320)
    s.text(28, 440, "지금은 한 번에 한 사람이 고치고, 다른 사람은 보기 전용으로 기다린다. 기다리는 화면은 다시 열 때 최신 판을 받는다.",
           size=12, color=MUTED)
    s.save("03-hwp-coedit", "한글 웹 기안기 동시 열기")


def model():
    s = Svg(1000, 400)
    s.text(28, 38, "모델 학습과 서빙", size=17, weight=700, color=NAVY)
    top = [("학습 후보", ["실문서 양식 × 완성본", "서빙 프롬프트 꼴 문답"]),
           ("사람 검수", ["근거 일치 · 개인정보", "승인본만 학습"]),
           ("LoRA SFT", ["DGX · bf16", "writer-v1 학습셋"]),
           ("병합 · NVFP4", ["어댑터 병합 뒤", "4비트 양자화"]),
           ("토르 02 서빙", ["운영 모델과", "같은 장비에서 후보로"])]
    x, w, gap = 28, 168, 26
    for i, (t, ln) in enumerate(top):
        s.box(x, 76, w, 100, t, ln, strong=(t == "LoRA SFT"))
        if i < len(top) - 1:
            s.arrow([(x + w, 126), (x + w + gap, 126)])
        x += w + gap
    s.box(570, 230, 402, 118, "비교 평가 (진행 중)", ["같은 문항으로 운영 모델 · 어댑터 · 병합본을 견줌",
                                                  "눈가림 쌍대 채점 · 지어낸 숫자 · 응답 속도",
                                                  "· 나으면 운영 교체, 아니면 학습셋 보강"], strong=True)
    s.arrow([(870, 176), (870, 230)])
    s.box(28, 230, 470, 118, "writer-v1 학습셋", ["학습 4,434 · 검증 346 · 시험 220 (승인본)",
                                                 "LoRA 학습은 10-22 마침, 결과 수치는 비교가 끝난 뒤 적는다",
                                                 "· 사실·수치는 검색으로 공급, 모델에는 맥락 구성 능력만"])
    s.arrow([(570, 300), (498, 300)], "보강", ly=292)
    s.save("04-model-pipeline", "모델 학습과 서빙")


if __name__ == "__main__":
    for f in (system, flow, hwp, model):
        f()
    print("ok")
