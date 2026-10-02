"""성과지표 추출 — 계획서·보고서의 표에서 지표 이름과 기준값·목표·실적·달성률을 그대로 꺼낸다(절대 규칙 1: 수치는 인출만).

표 조각(doc_chunks.kind = table)은 셀 격자 JSON {"cells": [[행, 열, 행 병합, 열 병합, _, 글], ...]} 이다.
성과지표 표의 일반 꼴:
  - 지표 이름 칸의 머리 글이 「성과지표(명)」·「지표(명)」 — 그 칸이 있는 행부터 그 칸의 행 병합만큼이 머리행
  - 값 칸의 머리는 여러 줄일 수 있다(「목푯값」 아래 「1차년도 … 5차년도」) — 위에서 아래로 이어 붙여 칸 이름으로
  - 칸 이름에서 값의 갈래(기준·목표·실적·달성률)와 시점(N차년도·연도)을 읽는다
특정 사업의 표 모양에 맞춘 규칙을 두지 않는다(절대 규칙 10). 지표는 사업마다 따로 묶는다(절대 규칙 12) — 묶는 일은 그래프(157)가 한다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

_NAME_HEAD = re.compile(r"^(?:(?:핵심|자율|주요|자체|공통|대표|정량|정성)\s*)?성과\s*지표(?:\s*명)?(?:\s*\([^)]*\))?$|^지\s*표\s*(?:명|이름)?$")
_MEASURES = (
    ("rate", re.compile(r"달성\s*(?:률|율|도)")),
    ("actual", re.compile(r"실\s*적|달성\s*(?:값|치)|결\s*과\s*값|성\s*과\s*값")),
    ("target", re.compile(r"목\s*표|목푯값|계\s*획")),
    ("baseline", re.compile(r"기\s*준|현\s*재|현\s*황|기\s*존")),
)
_ROUND = re.compile(r"(\d{1,2})\s*(?:차\s*년\s*도|차\s*연\s*도|차년|년\s*차|차)")
_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)|[‵'’‘`´](\d{2})(?!\d)")
_UNITS = r"%p|%|％|명|건|개소|개|점|천원|백만원|억원|원|시간|회|팀|학점|배|과|곳|종|편|일|년|월|명당"
# 값 칸은 숫자 하나(+단위·이상/이하·괄호 덧말)여야 한다 — 평가 서술표의 글 칸(「•대구시 D5 …」)을 값으로 읽지 않게
_VALUE = re.compile(r"^[약~∼\s]*([-+]?\d[\d,]*(?:\.\d+)?)\s*(" + _UNITS + r")?\s*(?:이상|이하|미만|초과|[↑↓▲▼])?\s*(?:\([^)]{0,30}\))?\s*$")
_TAG = re.compile(r"^\s*[\[［【<〈]\s*([^\]］】>〉]{1,8})\s*[\]］】>〉]\s*")
_NUM = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_SKIP_COL = re.compile(r"단\s*위|가\s*중\s*치|산\s*출|측\s*정|정\s*의|근\s*거|비\s*고|자\s*료|출\s*처|방\s*법|구\s*분")
_BULLET = re.compile(r"^(?:[\s\-–·•○●◦▪■□◇◆※*󰋼󰋻󰊲❐➟→①-⑳]+|\(?\d{1,2}[.)]\s+|[가-하][.)]\s+)+")


@dataclass
class Observation:
    indicator: str            # 지표 이름(표에 적힌 그대로, 앞 기호만 뗌)
    group: str                # 왼쪽 구분 칸(있으면)
    column: str               # 칸 이름(머리행을 이은 것)
    measure: str              # baseline · target · actual · rate · ""(칸 이름에 갈래가 없음)
    period: str               # "1차" · "2024" · ""
    value_text: str           # 칸 글 그대로
    value: float | None       # 첫 숫자
    unit: str = ""
    tag: str = ""             # 지표 등급 꼬리표(「[핵심]」「[자율②]」) — 이름과 따로
    row: int = 0
    evidence: str = field(default="")


def _grid(content) -> list[list[str]] | None:
    try:
        t = json.loads(content) if isinstance(content, str) else content
        cells = t["cells"]
    except (ValueError, TypeError, KeyError):
        return None
    if not cells:
        return None
    nr = max(int(c[0]) + max(1, int(c[2])) for c in cells)
    nc = max(int(c[1]) + max(1, int(c[3])) for c in cells)
    if nr > 400 or nc > 40:
        return None
    g = [[""] * nc for _ in range(nr)]
    for r, c, rs, cs, *_rest in cells:
        text = str(_rest[-1] if _rest else "").strip()
        for i in range(int(r), int(r) + max(1, int(rs))):
            for j in range(int(c), int(c) + max(1, int(cs))):
                g[i][j] = text
    return g


def _spans(content) -> dict[tuple[int, int], int]:
    t = json.loads(content) if isinstance(content, str) else content
    return {(int(c[0]), int(c[1])): max(1, int(c[2])) for c in t["cells"]}


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def measure_of(label: str) -> str:
    for m, rx in _MEASURES:
        if rx.search(label):
            return m
    return ""


def period_of(label: str) -> str:
    m = _ROUND.search(label)
    if m:
        return f"{int(m.group(1))}차"
    m = _YEAR.search(label)
    if m:
        return m.group(1) or f"20{m.group(2)}"
    return ""


def clean_name(s: str) -> str:
    s = _flat(s)
    s = _BULLET.sub("", s)
    s = re.sub(r"\s*\*+$|\s*\(\s*\*+\s*\)$", "", s)
    return s.strip()


def split_tag(s: str) -> tuple[str, str]:
    """「[자율②] 평생교육 … 만족도」 → (「자율②」, 「평생교육 … 만족도」)."""
    m = _TAG.match(s or "")
    return (m.group(1).strip(), s[m.end():].strip()) if m else ("", s)


def unit_in_name(s: str) -> str:
    m = re.search(r"\(\s*(" + _UNITS + r")\s*\)\s*$", s or "")
    return m.group(1) if m else ""


def name_key(s: str) -> str:
    """같은 지표를 묶는 열쇠 — 띄어쓰기·괄호 꼬리·기호·등급 꼬리표만 다른 것."""
    s = split_tag(clean_name(s))[1]
    s = re.sub(r"\([^)]*\)$", "", s)
    return re.sub(r"[\s·ㆍ,./()\[\]「」'\"-]", "", s).lower()


def from_table(content) -> list[Observation]:
    g = _grid(content)
    if not g:
        return []
    spans = _spans(content)
    nr, nc = len(g), len(g[0])
    head = None
    for r in range(min(nr, 6)):
        for c in range(nc):
            if _NAME_HEAD.match(_flat(g[r][c])) and (c == 0 or g[r][c - 1] != g[r][c]):
                head = (r, c)
                break
        if head:
            break
    if not head:
        return []
    r0, c0 = head
    # 이름 칸이 왼쪽으로 병합돼 있으면 가장 오른쪽 칸이 이름 칸
    while c0 + 1 < nc and g[r0][c0 + 1] == g[r0][c0]:
        c0 += 1
    start = next((c for c in range(c0, -1, -1) if (r0, c) in spans), c0)
    rows_h = spans.get((r0, start), 1)
    h_end = r0 + rows_h                       # 머리행: r0 .. h_end-1
    labels: dict[int, str] = {}
    for c in range(c0 + 1, nc):
        parts: list[str] = []
        for r in range(r0, h_end):
            t = _flat(g[r][c])
            if t and (not parts or parts[-1] != t):
                parts.append(t)
        # 머리행 위에 표 제목처럼 걸친 행(「정량적 목표」)이 있으면 갈래의 단서로 앞에 붙인다
        for r in range(r0 - 1, -1, -1):
            t = _flat(g[r][c])
            if t and t != _flat(g[r][0]) and measure_of(t) and not any(measure_of(p) for p in parts):
                parts.insert(0, t)
                break
        labels[c] = "/".join(parts)
    # 단위 칸
    unit_col = next((c for c, l in labels.items() if re.fullmatch(r"단\s*위", l.split("/")[-1])), None)
    value_cols = [c for c, l in labels.items() if l and not _SKIP_COL.search(l.split("/")[-1]) and (measure_of(l) or period_of(l))]
    if not value_cols:
        return []
    out: list[Observation] = []
    seen_rows: set[str] = set()
    for r in range(h_end, nr):
        tag, name = split_tag(clean_name(g[r][c0]))
        if not name or len(name) < 2 or len(name) > 80 or _NAME_HEAD.match(name) or not re.search(r"[가-힣A-Za-z]", name):
            continue
        group = ""
        for c in range(c0 - 1, -1, -1):
            t = clean_name(g[r][c])
            if t and t != name:
                group = t
                break
        unit = (_flat(g[r][unit_col]) if unit_col is not None else "") or unit_in_name(name)
        key = f"{name}|{group}|" + "|".join(g[r][c] for c in value_cols)
        if key in seen_rows:                 # 행 병합으로 같은 행이 되풀이된 것
            continue
        seen_rows.add(key)
        for c in value_cols:
            text = _flat(g[r][c])
            m = _VALUE.match(text)
            if not m or text == name:
                continue
            try:
                val = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            label = labels[c]
            out.append(Observation(indicator=name, group=group, column=label, measure=measure_of(label), period=period_of(label),
                                   value_text=text[:60], value=val, unit=(unit or m.group(2) or "")[:20], tag=tag, row=r,
                                   evidence=f"{name[:40]} · {label[:40]} = {text[:30]}"))
    return out


def from_chunks(chunks: list[dict]) -> list[tuple[int, Observation]]:
    """문서의 조각들 → [(조각 순번, 관측값)]. 표 조각만 본다."""
    out = []
    for c in chunks:
        if c.get("kind") != "table":
            continue
        content = c.get("content")
        if not content or "지표" not in str(content):
            continue
        for o in from_table(content):
            out.append((int(c.get("seq") or 0), o))
    return out
