"""칸 안 문단 단위 공동 편집의 글 셈(ADR-0070 덧붙임 10-27 「문단 단위」) — 문단 맞추기·자리 옮기기·세 갈래 합치기.

칸 글은 문단을 \\n 으로 이은 글이다(기안기 GetFieldText 의 \\r\\n). 한 칸을 두 사람이 서로 다른 문단에서 동시에 고치면, 서버는 보낸 쪽이 본 판(base)과
지금 판(theirs), 보낸 글(mine)을 문단 목록으로 맞춰(difflib.SequenceMatcher — 문단을 한 줄로 보는 줄 맞추기) 겹치지 않는 고침을 모두 남긴다.
문단을 넣거나 지운 사람이 있어도 맞추기가 문단 자리를 따라가므로 다른 사람의 고침이 엉뚱한 문단을 덮지 않는다.
같은 문단을 둘 다 고쳤으면(겹침) 나중에 온 쪽(mine)을 남기고 겹친 자리를 돌려준다 — 부르는 쪽이 충돌 사본으로 남긴다.
편집 화면(webhwp-collab.js paraDiff·merge3·mapPara)도 같은 규칙으로 셈한다.
"""
from __future__ import annotations

from difflib import SequenceMatcher


def paras(text: str) -> list[str]:
    return (text or "").split("\n")


def _hunks(a: list[str], b: list[str]) -> list[tuple[int, int, int, int]]:
    """a → b 의 바뀐 덩이 [(i1, i2, j1, j2)] — 같은 문단은 건너뛴다."""
    sm = SequenceMatcher(None, a, b, autojunk=False)
    return [(i1, i2, j1, j2) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]


def para_map(old: str, new: str) -> list[int]:
    """옛 글의 문단 번호 → 새 글의 문단 번호(옛 문단마다). 바뀐 덩이 안의 문단은 새 덩이의 같은 자리(넘으면 끝), 지워졌으면 그 자리 다음 문단."""
    a, b = paras(old), paras(new)
    out = list(range(len(a)))
    shift = 0
    last = 0
    for i1, i2, j1, j2 in _hunks(a, b):
        for i in range(last, i1):
            out[i] = i + shift
        for k, i in enumerate(range(i1, i2)):
            out[i] = min(j1 + k, j2 - 1) if j2 > j1 else min(j1, len(b) - 1)
        shift = j2 - i2
        last = i2
    for i in range(last, len(a)):
        out[i] = i + shift
    return [max(0, min(x, len(b) - 1)) for x in out]


def map_delta(old: str, at: int, dl: int, ins: str, para: int) -> int:
    """바뀐 부분 하나(at·del·ins, 글자 자리)로 문단 번호 옮기기 — 맞추기 없이 줄바꿈 수만 센다(글자 하나 칠 때마다 부르므로 가볍게)."""
    p_at = old[:at].count("\n")
    nd, ni = old[at:at + dl].count("\n"), ins.count("\n")
    if para < p_at:
        return para
    if para > p_at + nd:
        return para + ni - nd
    return p_at


def merge3(base: str, mine: str, theirs: str) -> tuple[str, list[int]]:
    """문단 세 갈래 합치기 — (합친 글, 겹친 자리(합친 글의 문단 번호) 목록). 겹치면 mine.
    두 쪽이 같은 자리에 문단을 새로 넣기만 했으면 겹침이 아니다(theirs 다음에 mine). 두 쪽이 같은 글로 고쳤으면 한 번만."""
    B, M, T = paras(base), paras(mine), paras(theirs)
    hs = [(i1, i2, j1, j2, "m") for i1, i2, j1, j2 in _hunks(B, M)] + [(i1, i2, j1, j2, "t") for i1, i2, j1, j2 in _hunks(B, T)]
    hs.sort(key=lambda h: (h[0], h[1], h[4]))

    def touch(lo: int, hi: int, h) -> bool:          # 덩이가 지금 무리 [lo, hi) 와 겹치나 — 경계에 닿기만 한 것은 겹침이 아니다
        i1, i2 = h[0], h[1]
        if i1 == i2:                                   # 넣기만 — 무리 안쪽(경계 빼고)이거나, 같은 자리 넣기끼리
            return lo < i1 < hi or (lo == hi == i1)
        if lo == hi:
            return i1 < lo < i2
        return i1 < hi and lo < i2

    groups: list[list] = []
    for h in hs:
        if groups:
            g = groups[-1]
            lo, hi = min(x[0] for x in g), max(x[1] for x in g)
            if touch(lo, hi, h):
                g.append(h)
                continue
        groups.append([h])

    def side(g, who, src, lo, hi) -> list[str]:      # 무리 [lo, hi) 를 한쪽 고침대로
        out, i = [], lo
        for h in sorted((x for x in g if x[4] == who), key=lambda x: (x[0], x[1])):
            out += B[i:h[0]] + src[h[2]:h[3]]
            i = h[1]
        return out + B[i:hi]

    res: list[str] = []
    clash: list[int] = []
    pos = 0
    for g in groups:
        lo, hi = min(x[0] for x in g), max(x[1] for x in g)
        res += B[pos:lo]
        whos = {x[4] for x in g}
        m, t = side(g, "m", M, lo, hi), side(g, "t", T, lo, hi)
        if whos == {"m"}:
            res += m
        elif whos == {"t"}:
            res += t
        elif m == t:
            res += m
        elif lo == hi:                                  # 같은 자리에 둘 다 새 문단 — 둘 다 남긴다
            res += t + m
        else:
            clash.append(len(res))
            res += m
        pos = hi
    res += B[pos:]
    return "\n".join(res), clash
