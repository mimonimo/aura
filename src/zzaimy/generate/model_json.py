"""모델이 낸 글에서 JSON 한 덩이를 꺼낸다 — 서빙 모델을 바꿔도(생각 글·울타리·파이썬 꼴·덧붙인 괄호) 같은 결과를 얻게.

모델마다 JSON 을 내는 버릇이 다르다:
- 생각 모드가 기본으로 켜진 모델은 「<think>…</think>」 를 앞에 붙이거나, 서버가 여는 표시를 떼고 「…</think>」 만 남긴다.
- 「```json … ```」 울타리, 앞뒤 설명 글, 닫는 괄호를 하나 더 붙인 꼴(「}}」)을 낸다.
- true/false/null 대신 파이썬 꼴(True/False/None, 작은따옴표)을 쓴다.
여기서 그 차이를 걷어 내고, 부르는 쪽은 꺼낸 dict 만 본다. 꺼내지 못하면 None — 지어내지 않는다.
"""
from __future__ import annotations

import ast
import json
import re

_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def strip_thinking(text: str) -> str:
    """생각 글을 걷는다 — 닫힌 「<think>…</think>」, 여는 표시 없이 남은 「…</think>」 앞부분, 닫히지 않은 「<think>…」(잘린 생각)."""
    t = _THINK.sub("", text or "")
    if "</think>" in t:                       # 서버가 여는 표시를 뗀 꼴 — 마지막 닫는 표시 뒤가 답이다
        t = t.rsplit("</think>", 1)[1]
    if "<think>" in t:                        # 닫히지 않은 생각 — 출력 한도에 잘렸다. 생각 글 안의 괄호를 답으로 읽지 않는다
        t = t.split("<think>", 1)[0]
    return t.strip()


def _python_literal(span: str):
    """파이썬 꼴 dict 글(True/None·작은따옴표)을 읽는다. JSON 의 true/false/null 이 섞였어도 읽는다. 아니면 None."""
    fixed = re.sub(r"\btrue\b", "True", re.sub(r"\bfalse\b", "False", re.sub(r"\bnull\b", "None", span)))
    try:
        got = ast.literal_eval(fixed)
    except (ValueError, SyntaxError, MemoryError, RecursionError, TypeError):
        return None
    return got


def _balanced(text: str, start: int) -> str | None:
    """start 의 「{」 와 짝이 맞는 「}」 까지(따옴표 안 괄호는 세지 않는다). 짝이 없으면 None."""
    depth, quote, esc = 0, "", False
    for i in range(start, len(text)):
        c = text[i]
        if quote:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                quote = ""
            continue
        if c in "\"'":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def loads(raw: str, key: str | None = None) -> dict | None:
    """모델 글 → dict. key 를 주면 그 열쇠가 있는 첫 dict 를 찾는다(없으면 None). 앞뒤 글·울타리·덧붙인 괄호는 버린다."""
    text = strip_thinking(raw)
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    dec = json.JSONDecoder()
    for body in candidates:
        for n, m in enumerate(re.finditer(r"\{", body)):
            if n >= 200:                      # 괄호가 아주 많은 긴 글 — 앞쪽에서 찾지 못했으면 그만 본다
                break
            got = None
            try:
                got, _end = dec.raw_decode(body, m.start())
            except ValueError:
                span = _balanced(body, m.start())
                if span and len(span) < 200_000:
                    got = _python_literal(span)
            if isinstance(got, dict) and (key is None or key in got):
                return got
    return None
