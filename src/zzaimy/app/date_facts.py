"""질문에 나온 날짜의 요일·기간 끝 날짜를 코드가 계산해 답의 근거로 건넨다.

11-25 운영 잼: 「12월 3일에 끝난 출장의 정산 기한」 을 모델이 셈하다 요일을 틀리고(12월 17일 목요일을 금요일로), 생각 모드로 셈하면
10,072토큰·434초가 걸렸다. 셈은 코드가, 어느 규정의 어느 기간을 쓸지는 27B 가 정한다.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

_WD = "월화수목금토일"
_YMD = re.compile(r"(20\d{2})\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})\s*일?")
_MD = re.compile(r"(?<![\d년.\-/])(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_SPANS = (7, 14, 30, 60, 90)


def _fmt(d: date) -> str:
    return f"{d.year}년 {d.month}월 {d.day}일({_WD[d.weekday()]})"


def dates_in(text: str, today: date) -> list[date]:
    """글에 나온 날짜 — 연도가 없으면 올해로 본다. 없는 날짜(2월 30일)는 뺀다."""
    out: list[date] = []
    taken: list[tuple[int, int]] = []
    for m in _YMD.finditer(text or ""):
        try:
            out.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            taken.append(m.span())
        except ValueError:
            pass
    for m in _MD.finditer(text or ""):
        if any(a <= m.start() < b for a, b in taken):
            continue
        try:
            out.append(date(today.year, int(m.group(1)), int(m.group(2))))
        except ValueError:
            pass
    return list(dict.fromkeys(out))[:4]


def date_facts(question: str, today: date | None = None) -> str:
    """질문에 날짜가 있으면 요일·다음 날·다음 날부터 센 기간의 끝·그달 말일을 적은 글. 날짜가 없으면 빈 글."""
    today = today or date.today()
    ds = dates_in(question, today)
    if not ds:
        return ""
    lines = [f"[날짜 셈 — 코드가 계산한 값. 날짜·요일은 이 값을 그대로 쓴다] 오늘: {_fmt(today)}"]
    for d in ds:
        nxt = d + timedelta(days=1)
        ends = ", ".join(f"{n}일 {_fmt(d + timedelta(days=n))}" for n in _SPANS)
        eom = (date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1))
        lines.append(f"- {_fmt(d)}: 다음 날 {_fmt(nxt)} · 다음 날부터 세어 N일째(=그날부터 N일 뒤) — {ends} · 그달 말일 {_fmt(eom)}")
    return "\n".join(lines)
