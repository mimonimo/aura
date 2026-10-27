"""답변 깊이 — 대화 입력줄의 두 단계(「빠르게」·「꼼꼼하게」). 요청 하나에 값 하나를 실어 모델 호출 쪽 설정이 이 표 하나만 읽는다.

- 빠르게(기본): 27B 생각 끔(지금까지와 같음), 작업 몫(llm_budget.work_len)을 가볍게 묶고(기본 32,768 토큰 — 대화 기억·근거·출력 몫의
  기준), 맡긴 절의 자기 점검은 건너뛴다. 에이전트 걸음 한도는 지금 그대로.
- 꼼꼼하게: 답·에이전트 호출에 생각을 켜고(출력 한도에 생각 몫을 더한다 — 안 더하면 한도를 생각에 다 써 답이 빈다),
  작업 몫은 문맥 한도 그대로, 맡긴 절 자기 점검을 하고, 걸음 한도를 1.5배로.
- 값을 정하지 않은 호출(반입·배치·측정 등 대화 밖)은 아무것도 바꾸지 않는다 — 표의 "" 줄.

값은 contextvar 로 나른다(llm_budget 의 사용 기록과 같은 방식). 대화 답(main._answer_task)·도우미(chat_routes._helper_task)가
요청 머리에서 use() 로 정하고, 읽는 곳은 client(생각 켜기·생각 몫), llm_budget.work_len(작업 몫), delegate(자기 점검),
doc_agent.run(걸음 한도), responder.answer_think(답 생각) 다섯 곳뿐이다. 모델 이름은 여기 두지 않는다 — 생각 켜기 인자는
연결 설정(VllmClient.think_extra)을 따른다.
"""
from __future__ import annotations

import contextlib
import contextvars
import os

FAST, DEEP = "fast", "deep"
LEVELS = (FAST, DEEP)
DEFAULT = FAST
LABELS = {FAST: "빠르게", DEEP: "꼼꼼하게"}
HINTS = {FAST: "생각 단계 없이 바로 답합니다", DEEP: "생각을 거쳐 자료를 넓게 보고 점검합니다 — 느립니다"}

_CUR: contextvars.ContextVar[str] = contextvars.ContextVar("answer_effort", default="")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _plans() -> dict[str, dict]:
    """단계 → 설정. 숫자는 환경변수로 바꿀 수 있다(모델을 바꿔도 코드는 그대로)."""
    return {
        "": {"thinking": False, "work_cap": 0, "step_scale": 1.0, "self_check": True},
        FAST: {"thinking": False, "work_cap": _int_env("ZZAIMY_EFFORT_FAST_CONTEXT", 32768), "step_scale": 1.0,
               "self_check": False},
        DEEP: {"thinking": True, "work_cap": 0, "step_scale": 1.5, "self_check": True,
               "think_tokens": _int_env("ZZAIMY_EFFORT_THINK_TOKENS", 8192),
               "think_timeout": float(_int_env("ZZAIMY_ANSWER_THINK_TIMEOUT", 900))},
    }


def clean(level) -> str:
    """알려진 단계만, 아니면 빈 글."""
    v = str(level or "").strip().lower()
    return v if v in LEVELS else ""


def current() -> str:
    """이 요청의 단계 — 정하지 않았으면 빈 글."""
    return _CUR.get()


@contextlib.contextmanager
def use(level):
    """이 블록 안의 모델 호출을 주어진 단계로. 모르는 값은 기본(빠르게)."""
    tok = _CUR.set(clean(level) or DEFAULT)
    try:
        yield
    finally:
        _CUR.reset(tok)


def plan(level: str | None = None) -> dict:
    return _plans()[clean(level if level is not None else current())]


def thinking() -> bool:
    return bool(plan()["thinking"])


def work_cap() -> int:
    return int(plan()["work_cap"])


def self_check() -> bool:
    return bool(plan()["self_check"])


def steps(n: int) -> int:
    return max(1, int(round(int(n) * float(plan()["step_scale"]))))


def think_tokens() -> int:
    return int(plan().get("think_tokens") or 0)


def think_timeout() -> float:
    return float(plan().get("think_timeout") or 0)


def label(level: str) -> str:
    return LABELS.get(clean(level) or DEFAULT, LABELS[DEFAULT])


def tag(level: str) -> str:
    """대화 질문 머리에 붙일 말 — 기본(빠르게)이면 빈 글."""
    v = clean(level)
    return LABELS[v] if v and v != DEFAULT else ""


def _msg_key(mid: int) -> str:
    return f"chat_msg_effort:{int(mid)}"


def remember(db, mid: int, level: str) -> None:
    """질문 하나에 그때 고른 단계를 남긴다(기본이면 남기지 않는다) — 답 만들기·대화 표시가 읽는다."""
    v = clean(level)
    if v and v != DEFAULT:
        db.set_setting(_msg_key(mid), v)


def of_message(db, mid: int | None) -> str:
    """remember 로 남긴 단계, 없으면 기본."""
    if not mid:
        return DEFAULT
    return clean(db.get_setting(_msg_key(mid), "")) or DEFAULT
