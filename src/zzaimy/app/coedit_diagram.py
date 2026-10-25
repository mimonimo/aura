"""공동 편집 순서 그림(SVG) — 처리 흐름 화면의 「공동 편집 처리 방식」 표 위에 둔다.

표만으로는 「언제 무엇이 어디로 가는가」 가 안 보여, 사용자 화면과 서버·한글 파일을 세로 칸으로 나누고 시간 순서대로 화살표를 그린다.
걸리는 시간 가운데 설정으로 정하는 값(보내기 간격·써 넣기 대기)은 운영 설정(coedit:<키>)에서, 실측 값은 ADR-0070 에서 온다.
"""
from __future__ import annotations

from html import escape

W = 1060
LANE_X = {"A": 260, "S": 500, "B": 740, "F": 950}   # 왼쪽 120px 는 시간 칸


def _seq(title: str, lanes: list[tuple[str, str, str]], steps: list[tuple], note: str) -> str:
    """lanes: [(키, 이름, 설명)] · steps: (보내는 칸, 받는 칸, 글, 시간 글) — 같은 칸이면 그 칸 위 상자로."""
    top, gap = 92, 52
    h = top + gap * len(steps) + 70
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" role="img" aria-label="{escape(title)}" '
         'font-family="Pretendard, \'Apple SD Gothic Neo\', \'Malgun Gothic\', sans-serif">',
         '<defs><marker id="ca" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
         '<path d="M0,0 L10,5 L0,10 z" fill="#3d5a80"/></marker></defs>',
         f'<rect width="{W}" height="{h}" fill="#ffffff"/>',
         f'<text x="20" y="28" font-size="15" font-weight="700" fill="#1b2a41">{escape(title)}</text>']
    for key, name, desc in lanes:
        x = LANE_X[key]
        fill = "#eef3fb" if key == "S" else "#f8fafc"
        o.append(f'<rect x="{x - 95}" y="40" width="190" height="40" rx="8" fill="{fill}" stroke="#c9d3e0"/>')
        o.append(f'<text x="{x}" y="57" font-size="13" font-weight="700" fill="#1b2a41" text-anchor="middle">{escape(name)}</text>')
        o.append(f'<text x="{x}" y="73" font-size="10.5" fill="#6b778a" text-anchor="middle">{escape(desc)}</text>')
        o.append(f'<line x1="{x}" y1="80" x2="{x}" y2="{h - 50}" stroke="#d8dee8" stroke-dasharray="4 4"/>')
    for i, (a, b, label, when) in enumerate(steps):
        y = top + gap * i + 18
        o.append(f'<text x="20" y="{y + 4}" font-size="11" fill="#8a5a00" font-weight="600">{escape(when)}</text>')
        if a == b:
            x = LANE_X[a]
            bw = max(200, int(len(label) * 11.2) + 24)              # 글 길이에 맞춘 폭(한글 한 자 약 11px)
            bx = min(max(x - bw / 2, 124), W - bw - 8)               # 시간 칸·오른쪽 끝을 넘지 않게
            o.append(f'<rect x="{bx:.0f}" y="{y - 14}" width="{bw}" height="26" rx="6" fill="#fff8ec" stroke="#e8c98a"/>')
            x = bx + bw / 2
            o.append(f'<text x="{x}" y="{y + 4}" font-size="11.5" fill="#3b4757" text-anchor="middle">{escape(label)}</text>')
            continue
        x1, x2 = LANE_X[a], LANE_X[b]
        d = 1 if x2 > x1 else -1
        o.append(f'<line x1="{x1 + 6 * d}" y1="{y}" x2="{x2 - 6 * d}" y2="{y}" stroke="#3d5a80" stroke-width="1.6" marker-end="url(#ca)"/>')
        o.append(f'<text x="{(x1 + x2) / 2:.0f}" y="{y - 7}" font-size="11.5" fill="#1b2a41" text-anchor="middle" '
                 f'paint-order="stroke" stroke="#ffffff" stroke-width="4">{escape(label)}</text>')
    o.append(f'<text x="20" y="{h - 22}" font-size="11.5" fill="#5b6b82">{escape(note)}</text>')
    o.append("</svg>")
    return "\n".join(o)


def field_flow(conf: dict) -> str:
    """사람이 칸에 글을 칠 때 — 내 화면 → 서버(DB) → 다른 사람 화면 → 한글 파일."""
    send = int(conf.get("throttle_ms", 300))
    pause = int(conf.get("pause_ms", 300))
    delay, most = int(conf.get("commit_delay", 3)), int(conf.get("commit_max", 20))
    lanes = [("A", "김가람 화면", "글을 치는 사람"), ("S", "AURA 서버", "칸 글 표(DB) — 지금의 정답"),
             ("B", "이나래 화면", "같은 문서를 연 사람"), ("F", "한글 파일", "hwpx")]
    steps = [
        ("A", "A", "「요약」 칸에 첫 글자 — 내 화면엔 바로 보임", "0초"),
        ("A", "S", "그 칸 빌리기 — 이제 다른 사람은 이 칸을 못 고침", "곧바로"),
        ("A", "S", f"칸 글 보내기 — 입력 중에도 {send}ms 마다", f"{send}ms 마다"),
        ("S", "S", "판 번호 +1, 칸 글 표에 저장", "수 ms"),
        ("S", "B", "알림(SSE) — 「요약」 칸이 바뀜", "곧바로"),
        ("B", "B", f"내가 {pause}ms 쉬면 그 칸만 고쳐 넣음 + 이름 깃발", "합계 약 0.2초"),
        ("S", "F", f"{delay}초 조용하면 그 칸만 써 넣음(계속 치면 {most}초마다)", f"{delay}초 뒤"),
    ]
    return _seq("① 사람이 칸에 글을 칠 때 — 다른 사람 화면까지 약 0.2초, 한글 파일까지 몇 초", lanes, steps,
                "같은 칸은 먼저 빌린 사람만 고친다. 다른 칸은 동시에 고칠 수 있다. 판이 어긋난 글은 버리지 않고 충돌 사본으로 남긴다.")


def aura_flow(conf: dict) -> str:
    """AURA(27B)가 절을 쓸 때 — 서버가 한글 파일에 바로 쓰고, 열린 화면들이 새 판을 연다."""
    lanes = [("A", "김가람 채팅", "AURA 에게 맡긴 사람"), ("S", "AURA 서버", "27B 문서 작업 에이전트"),
             ("F", "한글 파일", "hwpx"), ("B", "열린 기안기 화면", "김가람·이나래…")]
    steps = [
        ("A", "S", "「빈 절 하나 써 줘」", "0초"),
        ("S", "S", "27B 가 절 고르기 → 근거 찾기 → 개요·본문·자기 점검", "1~3분"),
        ("S", "S", "관문 — 근거 밖 숫자 거절, 사람이 쥔 칸은 건너뜀", "수 초"),
        ("S", "F", "그 절 자리 뒤에 문단·표를 바로 넣고 저장(앞 판은 .판 폴더)", "1초 안"),
        ("S", "B", "알림 — 「AURA 가 문서를 새로 저장함」, 머리줄 AURA 표시", "곧바로"),
        ("B", "B", "입력이 멈추면 새 판 다시 열기(큰 문서는 5~10초 더)", "0.4~0.5초"),
        ("S", "A", "「1. 개요」 를 썼습니다 — 남은 빈 절 이어 쓸까요?", "끝"),
    ]
    return _seq("② AURA(27B)가 절을 쓸 때 — 서버가 한글 파일에 바로 쓰고 열린 화면이 새 판을 연다", lanes, steps,
                "연결이 끊겨도 보낼 글은 브라우저에 남아 다시 이어지면 보낸다. 45초 동안 알림이 없으면 화면이 스스로 다시 연결한다.")
