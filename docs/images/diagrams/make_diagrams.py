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
    """전체 구성 — 플랫폼 화면(개발 현황 · 처리 흐름)과 같은 그림. 장비·연결 목록(process_flow.HOSTS·LINKS)에서 그린다(손그림은 장비가 바뀌어도 남아 옛 모습이 됐다)."""
    import sys
    sys.path.insert(0, str(HERE.parents[2] / "src"))
    from zzaimy.app import system_diagram as sd
    svg = sd.svg().replace("<svg xmlns", f'<svg width="{sd.W}" height="{sd.H}" xmlns', 1)
    (HERE / "01-system.svg").write_text(svg + "\n", encoding="utf-8")


def coedit():
    """공동 편집 순서 — 처리 흐름 화면 「공동 편집 처리 방식」 과 같은 그림(app/coedit_diagram). 값은 공동 편집 기본 설정(webhwp_coedit.DEFAULTS)."""
    import sys
    sys.path.insert(0, str(HERE.parents[2] / "src"))
    from zzaimy.app import coedit_diagram as cd
    from zzaimy.app.webhwp_coedit import DEFAULTS
    for name, svg in (("c4-27-coedit-field", cd.field_flow(DEFAULTS)), ("c4-27b-coedit-aura", cd.aura_flow(DEFAULTS))):
        h = svg.split('viewBox="0 0 ', 1)[1].split('"', 1)[0].split()[1]
        (HERE / f"{name}.svg").write_text(svg.replace("<svg xmlns", f'<svg width="{cd.W}" height="{h}" xmlns', 1) + "\n", encoding="utf-8")


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
    s = Svg(1140, 540)
    s.text(28, 38, "웹 한글 기안기 — 칸 단위 공동 편집", size=17, weight=700, color=NAVY)
    s.box(28, 74, 196, 100, "사용자 A", ["「요약」 칸을 쥐고 쓰는 중", "· 300ms 마다 칸 글 전체"], tag="편집")
    s.box(28, 190, 196, 100, "사용자 B", ["다른 칸을 동시에 씀", "· A 가 쥔 칸은 입력 막힘"], tag="편집")
    s.box(28, 330, 196, 100, "AURA 에이전트", ["대화 요청으로 절을 씀(27B)", "· 사람이 쥔 칸은 건너뜀"], tag="참여자", strong=True)
    s.box(280, 74, 252, 216, "편집 화면", [
        "어댑터 — 기안기 API 를 부르는",
        "유일한 곳(칸 읽기·쓰기·잠그기)",
        "공동 편집 층 — 임대·판 번호,",
        "보낼 줄·충돌 사본, 이름 깃발",
        "· 보낼 줄은 IndexedDB 에도 둠",
        "· 한글 조합 중에는 읽지 않음",
    ], tag="브라우저마다")
    s.box(590, 74, 272, 356, "AURA 서버 (운영 서버)", [
        "칸 임대 — 먼저 잡은 사람만 씀",
        "· 입력·커서로 늘고, 옮기면 풀림",
        "· 기본 300초, 기다리면 120초",
        "",
        "판 번호 — 같은 변경은 한 번만",
        "· 판이 어긋나면 충돌 사본으로 둠",
        "",
        "칸 밖 고치기 — 구조 잠금, 한 사람씩",
        "",
        "알림 줄 → SSE 로 다른 화면에",
        "AURA 절은 한글 파일에 바로 넣고",
        "「문서 저장」 알림 → 화면이 새 판을 엶",
    ], tag="정답을 쥔 곳", strong=True)
    s.box(916, 74, 196, 120, "기안기 서버", ["웹 한글 편집 엔진", "운영 서버 안에서만 열림", "· 밖에서 직접 접속 불가"], tag="내부")
    s.box(916, 250, 196, 180, "한글 파일 (hwpx)", ["사람 칸 글: 3초 뒤", "(이어지면 20초마다) 써 넣음", "AURA 절: 바로 넣음", "· 앞 판은 .판/ 에 남김", "· 부서 문서함에 저장"], tag="파일")
    for y in (124, 240):
        s.arrow([(224, y), (280, y)], "", both=True)
    s.arrow([(532, 126), (590, 126)], "임대·변경", ly=118)
    s.arrow([(590, 230), (532, 230)], "SSE 알림", dashed=True, ly=222)
    s.arrow([(224, 380), (590, 380)], "절 쓰기 요청", ly=372)
    s.arrow([(862, 134), (916, 134)], "프록시", ly=126)
    s.arrow([(862, 340), (916, 340)], "써 넣기", ly=332)
    s.text(28, 486, "한 칸은 먼저 잡은 사람만 쓰고, 칸이 아닌 표·본문·용지는 「칸 밖 고치기」로 한 사람씩 고친다. 칸이 없는 문서는 문서 전체 잠금으로 돈다.",
           size=12, color=MUTED)
    s.text(28, 506, "글이 겹치면 버리지 않고 충돌 사본으로 둔다. 시간 값(임대·보내기 간격·써 넣기)은 모두 설정으로 바꾼다(ADR-0070).", size=12, color=MUTED)
    s.save("03-hwp-coedit", "웹 한글 기안기 칸 단위 공동 편집")


def model():
    s = Svg(1000, 430)
    s.text(28, 38, "모델 학습과 비교", size=17, weight=700, color=NAVY)
    top = [("학습 후보", ["실문서 양식 × 완성본", "서빙 프롬프트 꼴 문답"]),
           ("사람 검수", ["근거 일치 · 개인정보", "승인본만 학습"]),
           ("LoRA SFT", ["DGX · bf16", "writer-v1 4,434쌍"]),
           ("병합 · NVFP4", ["어댑터 병합 뒤", "4비트 양자화"]),
           ("토르 02 후보 서빙", ["운영 모델과 같은 장비", "같은 질문으로 나란히"])]
    x, w, gap = 28, 168, 26
    for i, (t, ln) in enumerate(top):
        s.box(x, 76, w, 100, t, ln, strong=(t == "LoRA SFT"))
        if i < len(top) - 1:
            s.arrow([(x + w, 126), (x + w + gap, 126)])
        x += w + gap
    s.box(546, 230, 426, 136, "비교 평가 (10-24, 채택하지 않음)", ["시험 220문항 chrF 27.9 → 36.6, 질문에 없는 숫자 4.1% → 0%",
                                                          "문서 작성 시나리오 작업본 2/2 → 0/2",
                                                          "· 학습셋 틀이 에이전트 출력에 새어 동작이 멈춤",
                                                          "· 운영은 오픈 27B(NVFP4)를 그대로 쓴다"], strong=True)
    s.arrow([(870, 176), (870, 230)])
    s.box(28, 230, 446, 136, "Writer v2 학습 데이터 (계획)", ["플랫폼이 실제로 보내는 꼴로 만든다",
                                                     "에이전트 단계 JSON · 작업 메모 · 채팅 답 · 절 쓰기",
                                                     "· 넘기기 전 점검: 틀 0/45, 작업본 2/2, chrF v1 이상",
                                                     "· 사실·수치는 검색으로, 모델에는 맥락 구성 능력만"], dashed=True)
    s.arrow([(546, 300), (474, 300)], "다시 학습", ly=292)
    s.arrow([(112, 230), (112, 176)], "", dashed=True)
    s.text(28, 404, "비교는 모델 하나만 바꾸고 질문·플랫폼 코드·설정·채점 모델은 그대로 둔다. 먼저 정답 로그확률로 학습이 서빙에 들어갔는지 본다(ADR-0069).",
           size=12, color=MUTED)
    s.save("04-model-pipeline", "모델 학습과 비교")


if __name__ == "__main__":
    for f in (system, flow, hwp, model, coedit):
        f()
    print("ok")
