"""화면 문구의 한국어 다듬기 — 조사 고르기·붙이기, 문장 경계에서 자르기.

화면에 내보내는 글이 「사업계획서」 을 처럼 받침과 맞지 않는 조사를 띄어 쓰거나, 길이로 잘라
"…일치해야 합니" 처럼 낱말 중간에서 끊기지 않게 한다. 특정 낱말이 아니라 마지막 글자의 받침과
문장·낱말 경계만 본다.
"""
from __future__ import annotations

import re

# 받침 있을 때 / 없을 때 꼴. ㄹ 받침은 '으로' 대신 '로'를 쓴다.
_PAIRS = {
    "을": ("을", "를"), "를": ("을", "를"),
    "이": ("이", "가"), "가": ("이", "가"),
    "은": ("은", "는"), "는": ("은", "는"),
    "과": ("과", "와"), "와": ("과", "와"),
    "으로": ("으로", "로"), "로": ("으로", "로"),
    "이나": ("이나", "나"), "나": ("이나", "나"),
    "이라": ("이라", "라"), "라": ("이라", "라"),
}
# 숫자·영문은 읽는 소리의 받침으로 본다(1 일, 3 삼, L 엘, M 엠 …). 값은 (받침 있음, ㄹ 받침)
_DIGIT = {"0": (True, False), "1": (True, True), "2": (False, False), "3": (True, False), "4": (False, False),
          "5": (False, False), "6": (True, False), "7": (True, True), "8": (True, True), "9": (False, False)}
_LATIN = {"l": (True, True), "m": (True, False), "n": (True, False), "r": (True, True)}
_TRAIL = " \t\n)]}」』>\"'”’.,·…"


def _final(word: str) -> tuple[bool, bool] | None:
    """마지막 글자의 (받침 있음, ㄹ 받침). 판단할 수 없으면 None."""
    w = (word or "").rstrip(_TRAIL)
    if not w:
        return None
    ch = w[-1]
    if "가" <= ch <= "힣":
        jong = (ord(ch) - 0xAC00) % 28
        return bool(jong), jong == 8
    if ch in _DIGIT:
        return _DIGIT[ch]
    if ch.isascii() and ch.isalpha():
        return _LATIN.get(ch.lower(), (False, False))
    return None


def josa(word: str, particle: str) -> str:
    """word 뒤에 붙일 조사 꼴. particle 은 어느 꼴이든(을/를) 받는다. 모르는 조사는 그대로."""
    pair = _PAIRS.get(particle)
    if pair is None:
        return particle
    final = _final(word)
    if final is None:
        return pair[0] + "(" + pair[1] + ")" if pair[0] != pair[1] else pair[0]
    has, rieul = final
    if pair[0] == "으로":
        return "로" if (not has or rieul) else "으로"
    return pair[0] if has else pair[1]


def quote(word: str, particle: str = "") -> str:
    """「word」 + 받침에 맞는 조사(띄우지 않음)."""
    return f"「{word}」" + (josa(word, particle) if particle else "")


# 「…」 뒤에 띄어 쓴 조사 — 받침에 맞춰 붙인다. '이'·'가'는 '이 문서'처럼 관형사·낱말일 수 있어 건드리지 않는다.
_SPACED = re.compile(r"「([^「」\n]{1,200})」[ \t]+(으로|로|을|를|은|는|과|와|의|에서|에게|에)(?=[\s.,!?)\]」:;]|$)")


def attach_particles(text: str) -> str:
    """「제목」 을 → 「제목」을, 「사업계획서」 를 → 「사업계획서」를. 받침과 어긋난 꼴은 바로잡는다."""
    if not text or "」" not in text:
        return text or ""

    def fix(m: re.Match) -> str:
        word, particle = m.group(1), m.group(2)
        return f"「{word}」" + josa(word, particle)

    return _SPACED.sub(fix, text)


_SENTENCE_END = re.compile(r"(?<![\d.])[.!?。](?=[\s)\]」]|$)")


def clip(text: str, limit: int, ellipsis: str = "…") -> str:
    """limit 자 안에서 자른다 — 문장 끝이 있으면 거기서, 없으면 낱말 사이에서. 낱말 중간에서 끊지 않는다.

    공백은 한 칸으로 접는다. 앞쪽 절반 안에 경계가 없을 만큼 긴 낱말이면 그때만 글자 수로 자른다."""
    t = " ".join((text or "").split())
    if limit <= 0:
        return ""
    if len(t) <= limit:
        return t
    room = max(limit - len(ellipsis), 1)
    head = t[: room + 1]
    floor = room // 2
    ends = [m.end() for m in _SENTENCE_END.finditer(head) if floor <= m.end() <= room]
    if ends:
        return t[: ends[-1]].rstrip()
    cut = head.rfind(" ", floor, room + 1)
    if cut > 0:
        return t[:cut].rstrip(" ,·(-—") + ellipsis
    return t[:room].rstrip() + ellipsis
