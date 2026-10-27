"""공동 편집 순서 그림(SVG) — 처리 흐름 화면의 「공동 편집 처리 방식」 표 위에 둔다.

표만으로는 「언제 무엇이 어디로 가는가」 가 안 보여, 사용자 화면과 서버·한글 파일을 세로 칸으로 나누고 시간 순서대로 화살표를 그린다.
읽기 쉽게(10-27 사용자 「사람이 더 이해하기 쉽도록」): 단계마다 번호, 칸마다 색(사람·서버·화면·파일), 화살표 색은 보내는 칸,
구간(쓰는 동안·멈춘 뒤·파일 저장 등)은 옅은 띠로 묶고, 개발 용어 대신 쉬운 말.
걸리는 시간 가운데 설정으로 정하는 값(보내기 간격·써 넣기 대기)은 운영 설정(coedit:<키>)에서, 실측 값은 ADR-0070 에서 온다.
"""
from __future__ import annotations

from html import escape

W = 1060
LANE_X = {"A": 320, "S": 540, "B": 760, "F": 950}   # 왼쪽 220px 는 번호·시간 칸, 오른쪽 끝 칸 머리가 그림 안에 들게
# 칸 색(머리 바탕, 테두리·화살표) — 사람 파랑, 서버 보라, 다른 화면 초록, 파일 회색
COLOR = {"A": ("#e8f0fe", "#2f5fb3"), "S": ("#f1ecfb", "#6b4fb3"), "B": ("#e6f4ea", "#2e7d4f"), "F": ("#f1f3f5", "#5f6b7a")}
STEP, TOP = 54, 108


def _wrap(text: str, width: int, size: float = 12.0) -> list[str]:
    """글을 폭에 맞게 줄 나눔(한글 한 자 약 size 픽셀) — 최대 두 줄."""
    per = max(8, int(width / (size * 0.95)))
    if len(text) <= per:
        return [text]
    cut = text.rfind(" ", 0, per)
    cut = cut if cut > per * 0.5 else per
    rest = text[cut:].strip()
    return [text[:cut].strip(), rest if len(rest) <= per else rest[:per - 1] + "…"]


def _seq(title: str, lead: str, lanes: list[tuple[str, str, str]], steps: list[tuple], bands: list[tuple[int, int, str]], note: str) -> str:
    """lanes: [(키, 이름, 설명)] · steps: (보내는 칸, 받는 칸, 글, 시간) — 같은 칸이면 그 칸 위 상자로.
    bands: (첫 단계 번호, 끝 단계 번호, 구간 이름) — 1부터."""
    h = TOP + STEP * len(steps) + 64
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" role="img" aria-label="{escape(title)}" '
         'font-family="Pretendard, \'Apple SD Gothic Neo\', \'Malgun Gothic\', sans-serif">', '<defs>']
    for k, (_bg, fg) in COLOR.items():
        o.append(f'<marker id="ar{k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">'
                 f'<path d="M0,0 L10,5 L0,10 z" fill="{fg}"/></marker>')
    o.append('</defs>')
    o.append(f'<rect width="{W}" height="{h}" fill="#ffffff"/>')
    o.append(f'<text x="24" y="30" font-size="16" font-weight="700" fill="#1b2a41">{escape(title)}</text>')
    o.append(f'<text x="24" y="52" font-size="12.5" fill="#5b6b82">{escape(lead)}</text>')
    # 구간 띠
    for i, (a, b, name) in enumerate(bands):
        y0, y1 = TOP + STEP * (a - 1) - 6, TOP + STEP * b - 10
        fill = "#f7f9fc" if i % 2 == 0 else "#fbfcfe"
        o.append(f'<rect x="16" y="{y0}" width="{W - 32}" height="{y1 - y0}" rx="10" fill="{fill}" stroke="#e6ebf2"/>')
        o.append(f'<text x="{W - 30}" y="{y0 + 17}" font-size="11" font-weight="700" fill="#7a869a" text-anchor="end">{escape(name)}</text>')   # 오른쪽 위 — 번호와 겹치지 않게
    # 칸 머리와 세로줄
    for key, name, desc in lanes:
        x = LANE_X[key]
        bg, fg = COLOR[key]
        o.append(f'<rect x="{x - 88}" y="64" width="176" height="36" rx="8" fill="{bg}" stroke="{fg}" stroke-opacity=".35"/>')
        o.append(f'<text x="{x}" y="80" font-size="13" font-weight="700" fill="{fg}" text-anchor="middle">{escape(name)}</text>')
        o.append(f'<text x="{x}" y="94" font-size="10.5" fill="#5f6b7a" text-anchor="middle">{escape(desc)}</text>')
        o.append(f'<line x1="{x}" y1="100" x2="{x}" y2="{h - 48}" stroke="{fg}" stroke-opacity=".22" stroke-width="2"/>')
    # 단계
    for i, (a, b, label, when) in enumerate(steps, 1):
        y = TOP + STEP * (i - 1) + 24
        _bg, fg = COLOR[a]
        o.append(f'<circle cx="44" cy="{y}" r="11" fill="{fg}"/>')
        o.append(f'<text x="44" y="{y + 4}" font-size="11.5" font-weight="700" fill="#ffffff" text-anchor="middle">{i}</text>')
        o.append(f'<text x="64" y="{y + 4}" font-size="11.5" fill="#8a5a00" font-weight="600">{escape(when)}</text>')
        if a == b:
            x = LANE_X[a]
            lines = _wrap(label, 300)
            bw = min(330, max(170, int(max(len(t) for t in lines) * 11.6) + 28))
            bx = min(max(x - bw / 2, 172), W - bw - 20)
            bh = 26 if len(lines) == 1 else 40
            o.append(f'<rect x="{bx:.0f}" y="{y - bh / 2:.0f}" width="{bw}" height="{bh}" rx="8" fill="#ffffff" stroke="{fg}" stroke-width="1.4"/>')
            for j, t in enumerate(lines):
                ty = y + 4 if len(lines) == 1 else y - 3 + j * 15
                o.append(f'<text x="{bx + bw / 2:.0f}" y="{ty:.0f}" font-size="12" fill="#1b2a41" text-anchor="middle">{escape(t)}</text>')
            continue
        x1, x2 = LANE_X[a], LANE_X[b]
        d = 1 if x2 > x1 else -1
        o.append(f'<line x1="{x1 + 8 * d}" y1="{y + 6}" x2="{x2 - 10 * d}" y2="{y + 6}" stroke="{fg}" stroke-width="2" marker-end="url(#ar{a})"/>')
        lines = _wrap(label, abs(x2 - x1) + 70)                 # 화살표보다 조금 넓게 — 한 줄로 읽히게
        for j, t in enumerate(lines):
            ty = y - 4 - (len(lines) - 1 - j) * 14
            o.append(f'<text x="{(x1 + x2) / 2:.0f}" y="{ty:.0f}" font-size="12" fill="#1b2a41" text-anchor="middle" '
                     f'paint-order="stroke" stroke="#ffffff" stroke-width="5">{escape(t)}</text>')
    o.append(f'<text x="24" y="{h - 20}" font-size="12" fill="#5b6b82">{escape(note)}</text>')
    o.append("</svg>")
    return "\n".join(o)


def _sec(ms: float) -> str:
    """ms → 「0.3초」 꼴(그림 글은 초로 — ms 와 초가 섞여 읽기 어려웠다)."""
    v = ms / 1000
    return f"{v:g}초"


def field_flow(conf: dict) -> str:
    """사람이 칸에 글을 칠 때 — 내 화면 → 서버(DB) → 다른 사람 화면 → 한글 파일."""
    send, pause = _sec(int(conf.get("throttle_ms", 300))), _sec(int(conf.get("pause_ms", 300)))
    delay, most = int(conf.get("commit_delay", 3)), int(conf.get("commit_max", 20))
    lanes = [("A", "김가람 화면", "글을 쓰는 사람"), ("S", "AURA 서버", "칸 글을 바로 저장"),
             ("B", "이나래 화면", "같은 문서를 연 사람"), ("F", "한글 파일", "hwpx")]
    steps = [
        ("A", "A", "「요약」 칸에 글자를 친다 — 내 화면엔 바로", "0초"),
        ("A", "S", "이 칸을 맡는다(다른 사람은 기다림)", "바로"),
        ("A", "S", f"쓰는 동안 바뀐 글자만 {send}마다 보냄", f"{send}마다"),
        ("S", "S", "받은 글을 저장하고 칸 판 번호를 올림", "0.01초 안"),
        ("S", "B", "「요약」 칸이 바뀌었다고 알림", "바로"),
        ("B", "B", f"입력이 {pause} 멈추면 그 칸에 넣음 · 「김가람」 이름표", "약 0.2초 뒤"),
        ("A", "S", "글자 크기·색 같은 서식은 칸째 따로 보냄", "0.7초 멈추면"),
        ("S", "B", "서식째 그 칸을 바꿔 넣음", "바로"),
        ("S", "F", f"그 칸만 한글 파일에 저장(계속 쓰면 {most}초마다)", f"{delay}초 멈추면"),
    ]
    bands = [(1, 3, "쓰는 동안"), (4, 6, "다른 화면에 보이기"), (7, 8, "서식 바꿀 때"), (9, 9, "파일에 남기기")]
    return _seq("① 사람이 칸에 글을 칠 때", "다른 사람 화면까지 약 0.2초, 한글 파일까지 몇 초", lanes, steps, bands,
                "제목·소제목·표 칸까지 문서 전체가 칸이다. 한 칸은 한 사람씩, 다른 칸은 여럿이 동시에. 어긋난 글은 충돌 사본으로 남긴다.")


def aura_flow(conf: dict) -> str:
    """AURA(27B)가 양식을 만들고 절을 쓸 때 — 사람과 같은 칸 길로 조금씩 흘려 보내고, 파일에는 다 쓴 뒤 그 칸만 써 넣는다."""
    from zzaimy.app.webhwp_agent import HwpTools          # 흘려 쓰기 보폭은 실제 쓰는 값에서
    pace, cps = HwpTools.STREAM_PACE, HwpTools.STREAM_CPS
    lanes = [("A", "김가람", "AURA 에게 요청한 사람"), ("S", "AURA 서버", "27B 문서 작업"),
             ("B", "열려 있는 화면", "같은 문서를 연 사람들"), ("F", "한글 파일", "hwpx")]
    steps = [
        ("A", "S", "「AURA 양식 만들기」 — 가상 사업 설정과 함께", "0초"),
        ("S", "S", "27B 가 짧은 사업계획서 양식을 설계(절 차례·작성방법)", "몇십 초"),
        ("S", "F", "빈 문서를 그 양식으로 바꿔 저장", "바로"),
        ("S", "B", "새 판을 알림 → 모든 화면이 양식을 함께 받음 · 요약 칸 채움", "바로"),
        ("A", "S", "「AURA 이어 쓰기」 — 1절부터 차례로 세 개", "다음 요청"),
        ("S", "S", "27B 가 절 초안(설정 밖 숫자·좁은 칸·안내 상자는 관문이 막음), 다음 절은 미리", "절마다 몇십 초"),
        ("S", "B", f"칸 길로 조금씩 보냄(초당 약 {cps:g}자) · 머리줄에 「2. … 쓰는 중 (2/3)」", f"{pace:g}초마다"),
        ("B", "B", "열린 화면에 써 내려감 · 가만히 있던 화면은 그 자리로 따라감", "바로"),
        ("S", "F", "다 쓰면 그 칸만 파일에 넣고 칸을 놓음", "몇 초 뒤"),
        ("S", "A", "「n개 절을 썼습니다」 — 실제로 쓴 수 그대로", "끝"),
    ]
    bands = [(1, 4, "양식 만들기"), (5, 8, "절 쓰기"), (9, 10, "마무리")]
    return _seq("② AURA(27B)가 양식을 만들고 절을 쓸 때", "사람과 같은 칸 길로 흘려 써서 열린 화면에 바로 보인다", lanes, steps, bands,
                "문서를 다시 여는 것은 양식을 넣을 때 한 번뿐이다. AURA 가 쓴 글도 칸 안에 들어가 사람이 바로 고칠 수 있다.")
