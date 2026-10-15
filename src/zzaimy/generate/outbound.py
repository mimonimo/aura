"""외부 연결로 나가는 생성 요청의 공통 관문(ADR-0044 외부 전송 보호).

문서 작업 기본 연결이 교내 서버가 아닌 외부 서버(llm_connections 의 external)일 때만 쓴다. VllmClient 가
그 연결의 chat.completions.create 를 이 관문으로 감싸므로, 호출부가 무엇이든 같은 규칙을 거친다.

- 메시지 글의 개인정보를 되돌릴 수 있는 토큰([[KR_NAME_1]])으로 바꿔 보낸다. 같은 값은 요청 안에서 같은 토큰이다.
- 답에 돌아온 토큰은 교내에서 원값으로 되돌린다(정확 치환). 원값 장부는 요청 하나 동안 메모리에만 있다.
- 가림 도구를 쓸 수 없으면 보내지 않는다. 원문 통과로 처리하지 않는다.
- 그림은 가릴 수 없으므로 외부 연결로 보내지 않는다.

정규식·인식기 기반이라 라벨 없는 이름·주소는 놓칠 수 있다. 외부 연결 지정 때의 개인정보 검토 확인(ack)을
대신하지 않는다.
"""

from __future__ import annotations

import functools
import logging

log = logging.getLogger(__name__)

_HOLD_MAX = 48            # 토큰 길이 상한 — 이보다 길게 닫히지 않은 '[[' 는 토큰이 아니다


class OutboundBlocked(RuntimeError):
    """외부 연결로 보내면 안 되는 요청 — 가림을 할 수 없거나 그림이 들어 있다."""


_masker = None


def _get_masker():
    global _masker
    if _masker is None:
        from zzaimy.ingest.pii import PiiMasker

        _masker = PiiMasker()
    return _masker


class Vault:
    """요청 하나의 토큰 장부 — 여러 메시지에 걸쳐 같은 값은 같은 토큰."""

    def __init__(self) -> None:
        self.tok2val: dict[str, str] = {}
        self._val2tok: dict[str, str] = {}
        self._counters: dict[str, int] = {}

    def mask(self, text: str) -> str:
        if not text:
            return text
        try:
            spans = _get_masker()._spans(text)
        except Exception as e:  # 가림 도구가 없거나 실패 — 원문을 내보내지 않는다
            raise OutboundBlocked(f"개인정보 가림을 하지 못해 외부 연결로 보내지 않았습니다({type(e).__name__})") from e
        parts: list[str] = []
        cursor = 0
        for start, end, entity in spans:
            val = text[start:end]
            tok = self._val2tok.get(val)
            if tok is None:
                n = self._counters.get(entity, 0) + 1
                self._counters[entity] = n
                tok = f"[[{entity}_{n}]]"
                self._val2tok[val] = tok
                self.tok2val[tok] = val
            parts.append(text[cursor:start])
            parts.append(tok)
            cursor = end
        parts.append(text[cursor:])
        return "".join(parts)

    def restore(self, text):
        if not isinstance(text, str) or not text or not self.tok2val:
            return text
        for tok in sorted(self.tok2val, key=len, reverse=True):
            text = text.replace(tok, self.tok2val[tok])
        return text


def mask_messages(messages, vault: Vault) -> list:
    """메시지 목록의 글을 가린 사본. 그림 조각이 있으면 OutboundBlocked."""
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            raise OutboundBlocked("알 수 없는 메시지 형식이라 외부 연결로 보내지 않았습니다")
        m2 = dict(m)
        content = m.get("content")
        if isinstance(content, str):
            m2["content"] = vault.mask(content)
        elif isinstance(content, list):
            parts = []
            for p in content:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append(dict(p, text=vault.mask(str(p.get("text") or ""))))
                else:
                    raise OutboundBlocked("그림은 가릴 수 없어 외부 연결로 보내지 않았습니다")
            m2["content"] = parts
        elif content is not None:
            raise OutboundBlocked("알 수 없는 메시지 형식이라 외부 연결로 보내지 않았습니다")
        out.append(m2)
    return out


def _restore_message(msg, vault: Vault) -> None:
    for field in ("content", "reasoning_content", "reasoning"):
        val = getattr(msg, field, None)
        if isinstance(val, str):
            try:
                setattr(msg, field, vault.restore(val))
            except Exception:
                pass


def restore_response(resp, vault: Vault):
    for choice in getattr(resp, "choices", None) or []:
        msg = getattr(choice, "message", None)
        if msg is not None:
            _restore_message(msg, vault)
    return resp


def _split_safe(buf: str) -> tuple[str, str]:
    """지금 내보내도 되는 앞부분과, 토큰이 덜 와서 잡아 둘 뒷부분."""
    i = buf.rfind("[[")
    if i >= 0 and "]]" not in buf[i:] and len(buf) - i <= _HOLD_MAX:
        return buf[:i], buf[i:]
    if buf.endswith("["):
        return buf[:-1], "["
    return buf, ""


def restore_stream(stream, vault: Vault):
    """조각 응답의 토큰을 되돌리며 그대로 흘려보낸다. 토큰이 조각 경계에서 잘리면 닫힐 때까지 잡아 둔다."""
    pending: dict[int, str] = {}
    last = None
    for chunk in stream:
        for ch in getattr(chunk, "choices", None) or []:
            idx = getattr(ch, "index", 0) or 0
            delta = getattr(ch, "delta", None)
            piece = getattr(delta, "content", None) if delta is not None else None
            if isinstance(piece, str):
                out, keep = _split_safe(pending.get(idx, "") + piece)
                pending[idx] = keep
                delta.content = vault.restore(out)
            if getattr(ch, "finish_reason", None) and pending.get(idx) and delta is not None:
                delta.content = (getattr(delta, "content", None) or "") + vault.restore(pending.pop(idx))
            last = chunk if delta is not None else last
        yield chunk
    rest = "".join(pending.values())
    if rest and last is not None:                      # 끝 알림 없이 끊긴 흐름 — 남은 글을 한 조각으로 더 보낸다
        try:
            tail = last.model_copy(deep=True)
            tail.choices = tail.choices[:1]
            tail.choices[0].delta.content = vault.restore(rest)
            tail.choices[0].finish_reason = None
            yield tail
        except Exception:
            log.warning("외부 연결 조각 응답의 끝 글을 되돌리지 못했습니다")


def guard(create):
    """chat.completions.create 를 감싼다 — 보낼 때 가리고, 받을 때 되돌린다."""

    @functools.wraps(create)
    def _guarded(*a, **kw):
        vault = Vault()
        if "messages" in kw:
            kw = dict(kw, messages=mask_messages(kw["messages"], vault))
        elif a:
            raise OutboundBlocked("메시지를 위치 인자로 넘기면 가림을 확인할 수 없어 외부 연결로 보내지 않았습니다")
        resp = create(*a, **kw)
        if kw.get("stream"):
            return restore_stream(resp, vault)
        return restore_response(resp, vault)

    _guarded.outbound_guard = True
    return _guarded


def guard_client(client) -> None:
    """OpenAI 클라이언트의 생성 호출과 with_options 로 만든 사본까지 관문을 씌운다."""
    client.chat.completions.create = guard(client.chat.completions.create)
    orig_with = client.with_options

    def _with_options(*a, **kw):
        c = orig_with(*a, **kw)
        guard_client(c)
        return c

    client.with_options = _with_options
    client.copy = _with_options
