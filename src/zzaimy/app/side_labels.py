"""사이드바 목록 이름의 가운데 줄임(11-15).

한 목록 안에서 여러 이름이 긴 앞부분을 같이 쓰면(「2026 전문대학 특성화(4번 재현 8차)」·「…9차)」) 끝 말줄임으로는
모두 같은 앞부분만 보여 서로 가릴 수 없다. 이름을 앞부분(head)과 서로 다른 뒷부분(tail)으로 나눠 두면 화면(CSS)이
앞부분만 줄이고 뒷부분은 남긴다 — 「2026 전문대학 특…재현 8차)」. 목록의 이름끼리만 견주는 일반 규칙이고 특정 이름을 보지 않는다.
"""
from __future__ import annotations

MIN_SHARED = 8            # 이보다 짧게 겹치는 앞부분은 끝 말줄임으로도 서로 보인다
MIN_TAIL = 6              # 뒷부분이 이보다 짧으면 앞 낱말 하나를 더 붙여 무엇의 차이인지 보이게(「8차)」 → 「재현 8차)」)
MIN_HEAD = 4              # 앞부분은 이만큼은 남긴다(앞부분이 거의 없으면 나눌 까닭이 없다)
_BREAKS = set(" \t([{<「『·-_/,.:|")


def _common(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _tail_start(name: str, d: int) -> int:
    """d(처음 갈리는 자리) 앞쪽의 낱말 경계 — 낱말 가운데서 자르지 않는다."""
    k = d
    while k > 0 and name[k - 1] not in _BREAKS:
        k -= 1
    while len(name) - k < MIN_TAIL and k > MIN_HEAD:     # 너무 짧은 뒷부분은 한 낱말 더
        j = k - 1
        while j > 0 and name[j - 1] in _BREAKS:            # 경계 문자 묶음 건너뛰기
            j -= 1
        while j > 0 and name[j - 1] not in _BREAKS:
            j -= 1
        if j < MIN_HEAD:
            break
        k = j
    return k


def split_names(names: list[str]) -> list[tuple[str, str]]:
    """이름마다 (앞부분, 뒷부분). 나눌 까닭이 없으면 (이름, "")."""
    names = [str(n or "") for n in names]
    out: list[tuple[str, str]] = []
    for i, name in enumerate(names):
        # 긴 앞부분을 같이 쓰는 이름들 가운데 가장 먼저 갈리는 자리 — 그 뒤가 모두와 다른 부분이다
        shared = [c for j, other in enumerate(names) if j != i and other != name
                  for c in (_common(name, other),) if c >= MIN_SHARED]
        if not shared:
            out.append((name, ""))
            continue
        d = min(shared)
        k = _tail_start(name, d)
        if k < MIN_HEAD:                       # 띄어 쓰지 않은 이름 — 갈리는 자리 앞 두 글자부터
            k = max(MIN_HEAD, d - 2)
        if k >= len(name):
            out.append((name, ""))
            continue
        out.append((name[:k], name[k:]))
    return out
