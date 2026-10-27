"""문맥 예산 — 모델에 보내는 요청이 서빙 모델의 문맥 한도(max_model_len) 안에 들도록 보내기 전에 줄인다.

vLLM 은 입력 토큰 + max_tokens 가 max_model_len 을 넘으면 400 으로 거절한다. 답변·에이전트 경로의 호출은 모두
fit_messages(또는 create_fitted)를 거친다:
- 한도: 서빙 엔드포인트의 /v1/models 가 돌려주는 max_model_len 을 연결(base_url·모델)마다 한 번 읽어 둔다.
  읽지 못하면 연결 설정(context), 그다음 ZZAIMY_WRITER_CONTEXT(기본 16384 — 모르는 서버에 너무 크게 보내지 않게 작게 잡는다)를 쓴다.
- 토큰 수: 이 장비에는 토크나이저가 없다(transformers·tokenizers 미설치). 서버가 vLLM 이면 /tokenize 로 실제 수를
  한 번 재어 어림의 오차를 보정하고, 아니면 아래 글자 갈래별 비율로 넉넉히(크게) 어림한다.
- 줄이는 순서는 호출부가 정한다(cuts) — 요청과 에이전트가 찾은 근거는 맨 뒤, 첨부·연결 문서·오래된 대화가 먼저.
  출력 상한(max_tokens)은 정한 바닥까지만 줄인다. 그래도 넘으면 보내지 않고 ContextOverflow 를 낸다.
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass

DEFAULT_CONTEXT = 16384            # 서버에서 읽지 못할 때만 — 운영 27B 는 서버가 262144 를 알려 준다(configs/serving.yaml)
SAFETY_TOKENS = 128                # 대화 틀(chat template)의 특수 토큰·역할 머리 등 어림에 안 잡히는 몫

# 글자 갈래별 토큰 비율(글자 하나당 토큰). Qwen 계열 BPE(어휘 약 15만)는 자주 쓰는 한글 음절·낱말을 한 토큰으로
# 묶지만 드문 음절은 UTF-8 바이트 2~3토큰으로 쪼갠다. 행정 문서 글은 평균 한 음절에 1토큰을 넘지 않는 것으로
# 보고, 넘치지 않게 한글 1.0, 한자 1.5, 영문·숫자 0.3(약 3.3자에 1토큰), 공백 0.25, 문장부호 0.5, 그 밖의 기호
# (□○※·→ 등 멀티바이트) 1.0 으로 잡는다. 이 비율은 상한 쪽 어림이며, vLLM /tokenize 가 되는 서버에서는
# 실측 비율(_CORRECTION)로 바로잡는다. 운영 사례: 한도 초과 400 의 「at least 13313 input tokens」 를 받으면
# create_fitted 가 그 수로 보정한 뒤 한 번만 다시 줄여 보낸다.
_RATES = ((re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏]"), 1.0),
          (re.compile(r"[一-鿿㐀-䶿]"), 1.5),
          (re.compile(r"[A-Za-z0-9]"), 0.3),
          (re.compile(r"\s"), 0.25),
          (re.compile(r"[!-/:-@\[-`{-~]"), 0.5))
_MARGIN = 1.10
_PER_MESSAGE = 8

_LEN_CACHE: dict[tuple[str, str], tuple[int, float, bool]] = {}   # (base_url, model) → (한도, 읽은 시각, 서버에서 읽었나)
_LEN_RETRY_SEC = 300                                        # 읽지 못했으면 이 시간 뒤 다시 묻는다
_TOKENIZE_OK: dict[str, bool] = {}
_CORRECTION: dict[str, float] = {}                          # 연결별 실측/어림 비율(1 이상만 둔다)


class ContextOverflow(ValueError):
    """줄일 수 있는 것을 다 줄여도 한도를 넘는다 — 보내면 400 이므로 보내지 않는다."""


def _base(client) -> str:
    return str(getattr(getattr(client, "client", None), "base_url", "") or "").rstrip("/")


def context_len(client) -> int:
    """이 연결의 서빙 모델 문맥 한도. vLLM /v1/models 의 max_model_len, 없으면 설정 기본값."""
    # 서버에서 읽지 못할 때: 연결 설정(context) > ZZAIMY_WRITER_CONTEXT > 기본값
    default = int(getattr(client, "context_default", 0) or 0) or int(os.environ.get("ZZAIMY_WRITER_CONTEXT", str(DEFAULT_CONTEXT)))
    key = (_base(client), str(getattr(client, "model", "") or ""))
    got = _LEN_CACHE.get(key)
    if got and (got[2] or time.time() - got[1] < _LEN_RETRY_SEC):
        return got[0]
    found = None
    try:
        api = client.client
        api = api.with_options(timeout=5, max_retries=0) if hasattr(api, "with_options") else api   # 서버가 없으면 오래 붙잡지 않는다
        for m in api.models.list().data:
            if key[1] and getattr(m, "id", None) not in (key[1], None):
                continue
            v = getattr(m, "max_model_len", None)
            if v is None:
                v = (getattr(m, "model_extra", None) or {}).get("max_model_len")
            if v:
                found = int(v)
                break
    except Exception:
        found = None
    _LEN_CACHE[key] = (found or default, time.time(), bool(found))
    return found or default


def work_len(client) -> int:
    """몫 계산 기준 — 기본은 문맥 한도 그대로. 몫은 상한일 뿐이고 실제 양은 요구에 맞춘다(출력은 요청 분량에서, 근거는 찾은 만큼,
    대화 기억은 대화 길이만큼). 운영에서 따로 묶어야 할 때만 연결 설정 work_context 나 ZZAIMY_WORK_CONTEXT 로 상한을 둔다.
    대화에서 고른 답변 깊이(app/effort — 「빠르게」 는 가볍게 묶고 「꼼꼼하게」 는 묶지 않음)이 있으면 그 상한도 함께 본다."""
    from zzaimy.app import effort

    cap = int(getattr(client, "work_context", 0) or 0) or int(os.environ.get("ZZAIMY_WORK_CONTEXT", "0") or 0)
    n = context_len(client)
    caps = [c for c in (cap, effort.work_cap()) if c > 0]
    return min([n, *caps])


def served_context_len(client) -> int | None:
    """서버가 알려 준 문맥 한도만(없으면 연결 설정 context, 그것도 없으면 None) — 호출부가 자기 기본값을 쓸 수 있게."""
    n = context_len(client)
    got = _LEN_CACHE.get((_base(client), str(getattr(client, "model", "") or "")))
    if got and got[2]:
        return n
    return int(getattr(client, "context_default", 0) or 0) or None


def estimate_tokens(text: str) -> int:
    """글자 갈래별 비율로 넉넉히 어림한 토큰 수(위 _RATES 설명)."""
    t = text or ""
    total, counted = 0.0, 0
    for pat, rate in _RATES:
        n = len(pat.findall(t))
        total += n * rate
        counted += n
    total += (len(t) - counted) * 1.0                       # 그 밖의 기호
    return int(total * _MARGIN) + 1


def estimate_messages(messages: list[dict]) -> int:
    return sum(estimate_tokens(str(m.get("content") or "")) + _PER_MESSAGE for m in messages if m) + SAFETY_TOKENS


def _server_count(client, messages: list[dict]) -> int | None:
    """vLLM /tokenize 로 실제 입력 토큰 수. 안 되는 서버면 연결마다 한 번만 시도하고 None."""
    base = _base(client)
    if getattr(client, "external", False) is True:
        return None                    # 외부 연결에는 가리기 전 메시지를 세러 보내지 않는다 — 어림값으로 맞춘다
    if not base.startswith(("http://", "https://")) or _TOKENIZE_OK.get(base) is False:
        return None
    root = base[:-3] if base.endswith("/v1") else base
    try:
        body = json.dumps({"model": client.model, "messages": messages}).encode()
        key = str(getattr(client.client, "api_key", "") or "")
        req = urllib.request.Request(root + "/tokenize", body, {"Content-Type": "application/json",
                                                                 **({"Authorization": f"Bearer {key}"} if key else {})})
        with urllib.request.urlopen(req, timeout=5) as r:
            n = int(json.loads(r.read()).get("count"))
        _TOKENIZE_OK[base] = True
        return n
    except Exception:
        _TOKENIZE_OK.setdefault(base, False)
        return None


@dataclass
class Cut:
    """줄일 수 있는 조각. msg 는 messages 의 자리, text 는 그 메시지 안의 글(None 이면 메시지 전체를 뺀다).
    output=True 면 이 차례에 출력 상한을 바닥까지 줄인다."""
    msg: int = -1
    text: str | None = None
    marker: str = "\n[이 자료는 문맥 한도로 일부만 실었다 — 생략된 부분의 사실은 확인하지 못함]"
    output: bool = False


OUTPUT = Cut(output=True)


def _shorten(block: str, over_chars: int, marker: str) -> str:
    keep = max(0, len(block) - over_chars - len(marker))
    keep = max(keep, block.rfind("\n", 0, 200))             # 머리 몇 줄(출처·제목)은 남긴다 — 무엇이었는지는 알 수 있게
    cut = block.rfind("\n", 0, keep + 1)                    # 숫자·조건·표 칸을 줄 가운데서 자르지 않는다
    head = block[:cut].rstrip() if cut > 0 else block[:keep]
    return (head + marker) if head else marker.strip()


def fit_messages(client, messages: list[dict], max_tokens: int, cuts: list[Cut] | None = None, *,
                 floor: int = 512) -> tuple[list[dict], int]:
    """messages + max_tokens 가 한도 안에 들게 cuts 차례대로 줄인다. 줄인 사본과 출력 상한을 돌려준다.
    cuts 에 OUTPUT 이 없으면 맨 끝에 출력 줄이기를 둔다. 다 줄여도 넘으면 ContextOverflow."""
    limit = context_len(client)
    base = _base(client)
    msgs: list[dict | None] = [dict(m) for m in messages]
    texts = {i: (c.text if c.text is not None else None) for i, c in enumerate(cuts or [])}
    order = list(cuts or [])
    if not any(c.output for c in order):
        order.append(OUTPUT)
    floor = min(floor, max_tokens)

    def need() -> int:
        return int(estimate_messages([m for m in msgs if m]) * _CORRECTION.get(base, 1.0))

    def over() -> int:
        return need() + max_tokens - limit

    def calibrate() -> None:
        exact = _server_count(client, [m for m in msgs if m])
        if exact:
            est = estimate_messages([m for m in msgs if m])
            _CORRECTION[base] = max(1.0, exact / max(est, 1))

    if over() <= 0:
        return [m for m in msgs if m], max_tokens
    calibrate()
    for ci, cut in enumerate(order):
        if over() <= 0:
            break
        if cut.output:
            max_tokens = max(floor, max_tokens - max(over(), 0))
            continue
        m = msgs[cut.msg] if -len(msgs) <= cut.msg < len(msgs) else None
        if m is None:
            continue
        block = texts.get(ci)
        if block is None:                                   # 메시지 통째로 뺀다(오래된 대화)
            msgs[cut.msg] = None
            continue
        content = str(m.get("content") or "")
        if not block or content.count(block) != 1:
            continue
        ratio = max(len(block), 1) / max(estimate_tokens(block), 1)      # 이 조각의 글자/토큰
        over_chars = int(over() * ratio / _CORRECTION.get(base, 1.0)) + 64
        shorter = _shorten(block, over_chars, cut.marker)
        if len(shorter) < len(block):
            m["content"] = content.replace(block, shorter, 1)
    if over() > 0:
        raise ContextOverflow(f"문맥 한도({limit}) 안에 요청과 근거를 담을 수 없습니다 — 입력 어림 {need()}토큰, 출력 {max_tokens}토큰")
    return [m for m in msgs if m], max_tokens


_PROMPT_TOKENS = re.compile(r"maximum context length is (\d+).*?contains at least (\d+) input tokens", re.S)


def note_overflow(client, messages: list[dict], err: BaseException) -> bool:
    """서버가 한도 초과 400 을 냈으면 그 실측으로 한도·보정 비율을 고친다. 고쳤으면 True(다시 줄여 볼 만하다)."""
    m = _PROMPT_TOKENS.search(str(err))
    if not m:
        return False
    limit, actual = int(m.group(1)), int(m.group(2))
    _LEN_CACHE[(_base(client), str(getattr(client, "model", "") or ""))] = (limit, time.time(), True)
    est = estimate_messages(messages)
    base = _base(client)
    _CORRECTION[base] = max(_CORRECTION.get(base, 1.0), actual / max(est, 1) * 1.05)
    return True


# 요청 하나(대화 답변)에서 모델에 보낸 양 — 화면의 「문맥 사용」 표시용. track() 으로 시작하면 그 실행 흐름의 create_fitted 가 더한다
_USAGE: contextvars.ContextVar[dict | None] = contextvars.ContextVar("llm_usage", default=None)


_LABEL: contextvars.ContextVar[str] = contextvars.ContextVar("llm_label", default="")


def track() -> dict:
    d = {"calls": 0, "prompt": 0, "peak": 0, "limit": 0, "trimmed": False, "items": []}
    _USAGE.set(d)
    return d


class label:
    """with llm_budget.label("에이전트 · 3단계"): … — 그 안의 모델 호출에 이름표(화면의 호출별 문맥 사용). 「서브 · …」 로 시작하면 서브 묶음."""

    def __init__(self, name: str):
        self.name, self._tok = name, None

    def __enter__(self):
        self._tok = _LABEL.set(self.name)
        return self

    def __exit__(self, *exc):
        _LABEL.reset(self._tok)
        return False


def group_of(name: str) -> str:
    return "서브" if name.startswith("서브") else "메인"


def usage() -> dict | None:
    """지금 실행 흐름에서 track() 으로 시작한 사용 기록(없으면 None)."""
    return _USAGE.get()


def headroom() -> tuple[float, int] | None:
    """메인 에이전트의 남은 문맥 몫 — (1 - 가장 최근 메인 호출의 입력 / 한도, 한도). 기록이 없으면 None.
    다음 걸음은 그 입력에 이번 결과가 더해지므로, 이 몫이 작으면 긴 본문을 직접 쓸 자리가 없다."""
    d = _USAGE.get()
    if not d:
        return None
    mains = [x for x in d.get("items") or [] if x.get("group") == "메인" and x.get("limit")]
    if not mains:
        return None
    last = mains[-1]
    return max(0.0, 1.0 - float(last["in"]) / float(last["limit"])), int(last["limit"])


def _note_usage(client, fitted: list[dict], messages: list[dict], max_tokens: int) -> None:
    d = _USAGE.get()
    if d is None:
        return
    n = int(estimate_messages(fitted) * _CORRECTION.get(_base(client), 1.0))
    d["calls"] += 1
    d["prompt"] += n
    d["peak"] = max(d["peak"], n + max_tokens)
    d["limit"] = context_len(client)
    trimmed = len(fitted) < len(messages) or sum(len(str(m.get("content") or "")) for m in fitted) < sum(len(str(m.get("content") or "")) for m in messages)
    if trimmed:
        d["trimmed"] = True                                 # 한도 때문에 오래된 대화·첨부를 줄였다
    name = _LABEL.get() or "기타"
    if len(d["items"]) < 80:
        d["items"].append({"label": name, "group": group_of(name), "in": n, "out": int(max_tokens), "limit": d["limit"], "trimmed": trimmed})


# create_fitted 가 이미 적은 호출 — 연결의 생성 호출 감싸개(generate/client)가 같은 호출을 한 번 더 세지 않게
_NOTED: contextvars.ContextVar[bool] = contextvars.ContextVar("llm_noted", default=False)


def note_direct(client, messages, max_tokens) -> None:
    """create_fitted 를 거치지 않은 모델 호출(문서 편집 계획·질문 읽기·검색어 넓히기 등)도 사용 기록에 더한다.
    연결의 생성 호출 감싸개가 부른다 — track() 이 없거나 create_fitted 안이면 아무것도 하지 않는다."""
    if _USAGE.get() is None or _NOTED.get():
        return
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    try:
        _note_usage(client, msgs, msgs, int(max_tokens or 0))
    except Exception:                                    # 기록 실패로 답이 멈추면 안 된다
        pass


def _send(client, fitted, mt, kw):
    tok = _NOTED.set(True)
    try:
        return client.client.chat.completions.create(model=client.model, messages=fitted, max_tokens=mt, **kw)
    finally:
        _NOTED.reset(tok)


def create_fitted(client, messages: list[dict], max_tokens: int, cuts: list[Cut] | None = None, *, floor: int = 512,
                  on_fitted=None, **kw):
    """fit_messages 로 줄여 보낸다. 그래도 서버가 한도 초과로 거절하면 실측으로 보정해 한 번만 다시 줄여 보낸다.
    on_fitted(messages) 는 실제로 보낸 메시지를 받는다(모델에 건넨 글을 기록하는 호출부용)."""
    fitted, mt = fit_messages(client, messages, max_tokens, cuts, floor=floor)
    if on_fitted:
        on_fitted(fitted)
    _note_usage(client, fitted, messages, mt)
    try:
        return _send(client, fitted, mt, kw)
    except Exception as e:
        if not note_overflow(client, fitted, e):
            raise
    fitted, mt = fit_messages(client, messages, max_tokens, cuts, floor=floor)
    if on_fitted:
        on_fitted(fitted)
    return _send(client, fitted, mt, kw)


def reset_cache() -> None:
    """시험용 — 연결별 기억을 비운다."""
    _LEN_CACHE.clear()
    _TOKENIZE_OK.clear()
    _CORRECTION.clear()
