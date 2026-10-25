"""vLLM 호출 계층.

공고 스키마 추출(guided_json — vLLM 구조화 출력으로 형식 보장)과
섹션 초안 생성. 수치는 근거에 있는 것만 쓰도록 프롬프트로 지시하되,
최종 보증은 프롬프트가 아니라 검증기가 한다(브리프 5.3 — 구조 수준 강제).
"""

from __future__ import annotations

import functools
import os

try:
    from openai import OpenAI
except ImportError:  # 개발 장비에 openai 가 없어도 앱·테스트는 뜬다(생성 서버 호출 때만 필요)
    OpenAI = None  # type: ignore[assignment]

from zzaimy.generate.schema import AnnouncementSchema
from zzaimy.retrieve.stub import Evidence

_SCHEMA_PROMPT = """다음은 국고사업 공고문 본문이다. 공고에서 아래 정보를 추출해 JSON으로 답하라.
- title: 사업명
- sections: 계획서에 써야 할 목차. 공고에 작성 항목·제출 목차가 있으면 항목별로
  나눠라(하나로 뭉치지 마라). 목차가 없으면 사업 개요/추진 계획/성과 관리/예산처럼
  공고 내용에서 유추되는 3~6개 항목으로 나눠라
- criteria: 평가지표. 반드시 배점표·심사기준에 적힌 것만 쓰고, points는 그 표의
  배점 숫자만 넣어라. 사업비·지원 금액·인원수 같은 수치를 배점으로 넣지 마라.
  배점표가 아예 없으면 criteria는 빈 배열로 둬라
- budget_limit_krw: 예산 상한 (원 단위 정수, 명시 없으면 null)

공고 본문에 없는 내용은 만들지 마라.

공고 본문:
{announcement}"""

_SECTION_PROMPT = """너는 전문대학의 국고사업 계획서 작성을 돕는 조력자다.
아래 섹션 초안을 한국어 공문서 문체로 작성하라.

[섹션] {section_name}
[요구사항] {requirements}
[평가지표] {criteria}
[방향성] {direction}

[인출된 근거 — 수치는 반드시 여기 있는 것만 쓰고, 근거 없는 수치 자리는 (근거 없음)으로 둘 것]
{evidence}

섹션 본문만 출력하라. 3~5문단. 예산·일정·추진체계처럼 표가 적합한 내용은
마크다운 파이프 표(| 열 | 열 | 형식, 머리글 구분줄 포함)로 작성하라 —
표의 수치도 근거에 있는 것만 쓴다."""


def describe_llm_error(e: BaseException) -> str:
    """LLM 호출 실패를 담당자 말로 — 예외 이름을 화면에 내보내지 않는다.

    OpenAI 호환 서버 공통 규약(401/403 인증, 404 모델 없음, 429 한도, 5xx 일시 불가)을 사람 말로 옮긴다.
    """
    name = type(e).__name__
    status = getattr(e, "status_code", None)
    if "Connection" in name or "Timeout" in name or "Connect" in name:
        return "AI 모델 서버가 연결되지 않아 답변을 만들지 못했습니다. 연결 후 다시 질문해 주세요."
    if "Authentication" in name or "PermissionDenied" in name or status in (401, 403):
        return "AI 모델 서버가 API 키를 거부했습니다. LLM 연결의 키를 확인해 주세요."
    if "NotFound" in name or status == 404:
        return "선택한 모델이 서버에 없습니다. LLM 연결에서 모델을 다시 골라 주세요."
    if "RateLimit" in name or status == 429:
        return "요청 한도를 넘었습니다. 잠시 후 다시 시도해 주세요."
    if status is not None and int(status) >= 500:
        return "AI 모델 서버가 일시적으로 응답하지 않습니다. 잠시 후 다시 시도해 주세요."
    return "답변을 만들지 못했습니다. 잠시 후 다시 시도해 주세요."


def _strip_fences(text: str) -> str:
    """모델이 JSON을 ```json 펜스로 감싸는 경우의 방어적 제거."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()


# 생각하는 모델 대비 — 답이 비어 오면 한도를 넓혀 한 번만 다시 받는다.
# 바닥값은 측정 근거다: 생각하는 모델은 같은 질문에 생각 1,250자 안팎을 먼저 내고 답을 낸다.
_THINKING_FLOOR = int(os.environ.get("ZZAIMY_LLM_THINKING_FLOOR", "1500"))
_THINKING_CEILING = int(os.environ.get("ZZAIMY_LLM_THINKING_CEILING", "4096"))


def _rejects_extra(exc: Exception) -> bool:
    """서버가 확장 인자를 모른다고 거절했는지 — 문구가 아니라 상태 코드로 본다."""
    status = getattr(exc, "status_code", None)
    if status in (400, 422):
        return True
    return "unknown" in str(exc).lower() and "field" in str(exc).lower()


# 구조화 출력(response_format json_schema)을 거절하는 서버면 JSON 지시 + 파싱으로 물러난다. 거절인지는 상태 코드(400·422)와
# 오류 글에 구조화 출력 낱말이 있는지로 본다 — 그 밖의 400 은 아래 확장 인자 거절 처리로 넘긴다.
_SCHEMA_WORDS = ("response_format", "json_schema", "guided", "structured", "grammar", "format")
_DEFAULT_NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}
_DEFAULT_THINK = {"chat_template_kwargs": {"enable_thinking": True}}


def _rejects_schema(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status not in (400, 422):
        return False
    text = str(exc).lower()
    return any(w in text for w in _SCHEMA_WORDS)


def schema_as_prompt(kw: dict) -> dict:
    """response_format 을 빼고 같은 요구를 글 지시로 옮긴다 — 호출부는 지금처럼 JSON 을 파싱한다(울타리·앞뒤 글은 호출부가 걷는다)."""
    rf = kw.get("response_format") or {}
    if not isinstance(rf, dict) or rf.get("type") not in ("json_schema", "json_object"):
        return kw
    import json as _json

    schema = ((rf.get("json_schema") or {}).get("schema")) if rf.get("type") == "json_schema" else None
    note = ("\n\n[출력 형식] 다른 글 없이 JSON 객체 하나만 출력하라. 코드 울타리(```)를 쓰지 마라."
            + (" 다음 JSON 스키마를 따른다:\n" + _json.dumps(schema, ensure_ascii=False) if schema else ""))
    msgs = [dict(m) for m in (kw.get("messages") or [])]
    for m in reversed(msgs):
        if m.get("role") == "user":
            if isinstance(m.get("content"), str):
                m["content"] = m["content"] + note
            elif isinstance(m.get("content"), list):
                m["content"] = list(m["content"]) + [{"type": "text", "text": note.strip()}]
            break
    else:
        msgs.append({"role": "user", "content": note.strip()})
    out = {k: v for k, v in kw.items() if k != "response_format"}
    out["messages"] = msgs
    return out


_OLLAMA_CACHE: dict[str, bool] = {}


def is_ollama(base_url: str) -> bool:
    """이 주소의 서버가 Ollama 인가 — /api/version 이 답하면 Ollama 다. 주소별로 한 번만 묻는다.

    왜: 생각 모드를 끄는 인자가 서버마다 다르다. vLLM 은 chat_template_kwargs.enable_thinking,
    Ollama 는 reasoning_effort="none" 만 듣는다(vLLM 식 인자는 무시해 300토큰을 생각에 다 쓰고
    본문이 비지만, reasoning_effort="none" 이면 1초 안에 바로 답한다).
    """
    root = (base_url or "").rstrip("/")
    root = root[:-3] if root.endswith("/v1") else root
    if not root.startswith(("http://", "https://")):
        return False
    if root in _OLLAMA_CACHE:
        return _OLLAMA_CACHE[root]
    ok = False
    try:
        import urllib.request

        with urllib.request.urlopen(root + "/api/version", timeout=3) as r:
            ok = r.status == 200 and b"version" in r.read(200)
    except Exception:
        ok = False
    _OLLAMA_CACHE[root] = ok
    return ok


_VISION_CACHE: dict[tuple[str, str], bool] = {}


def model_can_see(base_url: str, model: str) -> bool:
    """Ollama 모델이 이미지를 읽을 수 있는가 — /api/show 의 capabilities 에 vision 이 있는지."""
    if not model or not is_ollama(base_url):
        return False
    root = base_url.rstrip("/")
    root = root[:-3] if root.endswith("/v1") else root
    key = (root, model)
    if key not in _VISION_CACHE:
        ok = False
        try:
            import json as _json
            import urllib.request

            req = urllib.request.Request(root + "/api/show", _json.dumps({"model": model}).encode(),
                                         {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                ok = "vision" in (_json.loads(r.read()).get("capabilities") or [])
        except Exception:
            ok = False
        _VISION_CACHE[key] = ok
    return _VISION_CACHE[key]


def _caller() -> str:
    """이 호출을 시킨 곳(모듈.함수) — 생성 연결·문맥 맞추기 층은 건너뛴다. 학습 데이터의 갈래 나누기에 쓴다."""
    import inspect
    for fr in inspect.stack()[2:]:
        mod = fr.frame.f_globals.get("__name__", "")
        if mod.startswith(("zzaimy.generate", "zzaimy.app.llm_budget", "openai", "functools")):
            continue
        return f"{mod}.{fr.function}"
    return ""


def _record(kw: dict, resp) -> None:
    """ZZAIMY_LLM_RECORD(폴더)가 켜져 있으면 모델에 보낸 요청과 받은 답을 그대로 한 줄씩 남긴다 — Writer 학습 데이터를 플랫폼이
    실제로 보내는 꼴로 만들 때(K61). 기본은 꺼짐이고, 시험 DB 로 돌린 시나리오에서만 켠다(운영에서 켜지 않는다 — 문서 원문이 들어간다).
    기록 실패는 생성을 막지 않는다."""
    folder = os.environ.get("ZZAIMY_LLM_RECORD", "").strip()
    if not folder:
        return
    try:
        import datetime as _dt
        import json as _json
        from pathlib import Path as _P
        msg = resp.choices[0].message
        row = {"at": _dt.datetime.now().isoformat(timespec="seconds"), "caller": _caller(), "model": kw.get("model", ""),
               "messages": kw.get("messages"), "tools": kw.get("tools"), "response_format": kw.get("response_format"),
               "max_tokens": kw.get("max_tokens"), "temperature": kw.get("temperature"),
               "answer": getattr(msg, "content", None) or "",
               "tool_calls": [tc.model_dump() for tc in (getattr(msg, "tool_calls", None) or []) if hasattr(tc, "model_dump")],
               "finish": getattr(resp.choices[0], "finish_reason", "")}
        d = _P(folder)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"llm-{_dt.date.today().isoformat()}.jsonl", "a", encoding="utf-8") as f:
            f.write(_json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


class VllmClient:
    def __init__(self, base_url: str | None = None, model: str | None = None,
                 role: str = "", *, timeout: float | None = None, max_retries: int | None = None) -> None:
        """role 을 주면 그 용도로 지정한 서버·모델을 쓴다(화면의 '용도별 지정')."""
        from zzaimy.generate import model_config

        cfg = model_config.current(role)      # 용도 지정 > 기본 연결 > 화면 설정 > 환경변수
        if base_url and base_url != cfg.get("base_url"):
            # 주소를 직접 준 호출(환경변수로 켠 질의 확장·LLM 재순위 등) — 등록 연결의 종류·키·설정을 끌어 쓰지 않는다(키가 다른 서버로 새지 않게)
            cfg = {**cfg, **model_config._options({}), "kind": "vllm", "connection_id": "", "external": False,
                   "vision_model": "", "api_key": os.environ.get("VLLM_API_KEY", "none")}
        if OpenAI is None:
            raise RuntimeError("openai 패키지가 없습니다 — 생성 서버 호출에 필요합니다")
        self.kind = cfg.get("kind", "vllm")
        self.connection_id = cfg.get("connection_id", "")
        self.structured = cfg.get("structured") or "auto"
        self.context_default = int(cfg.get("context") or 0)
        # 시간 제한·재시도는 어느 OpenAI 호환 서버든 같은 규약(429·5xx 는 SDK 가 백오프 재시도)
        self.client = OpenAI(
            base_url=base_url or cfg["base_url"], api_key=cfg["api_key"],
            timeout=float(timeout if timeout is not None else os.environ.get("ZZAIMY_LLM_TIMEOUT", "180")),
            max_retries=int(max_retries if max_retries is not None else os.environ.get("ZZAIMY_LLM_RETRIES", "3")),
        )
        self.model = model or cfg["model"] or self.client.models.list().data[0].id
        # 생각 끄기·켜기 인자 — 모델 계열마다 다르다. 연결 설정(no_think·think)이 있으면 그것, 없으면 내부 서버(vllm 종류)는
        # Qwen 식 chat_template_kwargs 가 기본(운영 그대로), 외부 연결은 보내지 않는다(모르는 인자를 거부한다)
        no_think = cfg.get("no_think")
        self._no_think = dict(no_think) if no_think is not None else (dict(_DEFAULT_NO_THINK) if self.kind == "vllm" else {})
        think = cfg.get("think")
        self.think_extra = dict(think) if think is not None else (
            dict(_DEFAULT_THINK) if no_think is None and self.kind == "vllm" else None)
        # 응답의 usage 를 연결·모델·날짜별로 기록한다 (한도 관리용) — SDK 호출은 그대로 감싼다
        try:
            _orig = self.client.chat.completions.create

            def _answer_is_empty(resp) -> bool:
                """생각만 하고 답을 못 낸 응답인지 — 생각하는 모델에서 한도가 모자랄 때 생긴다."""
                try:
                    choice = resp.choices[0]
                except (AttributeError, IndexError):
                    return False
                if (getattr(choice.message, "content", None) or "").strip():
                    return False
                thought = ""
                for field in ("reasoning", "reasoning_content"):
                    thought = thought or (getattr(choice.message, field, None) or "")
                return bool(thought) or getattr(choice, "finish_reason", "") == "length"

            # Ollama 자동 판별은 생각 끄기 인자를 연결에 따로 정하지 않았을 때만 쓴다
            ollama = self.kind == "vllm" and no_think is None and is_ollama(str(getattr(self.client, "base_url", "") or ""))
            _no_extra: list = []                                       # 확장 인자를 거절한 서버 — 한 번 겪으면 더 보내지 않는다
            _no_schema: list = ["x"] if self.structured == "prompt" else []   # 구조화 출력을 거절한 서버(또는 설정이 prompt)

            def _create(*a, _via=None, **kw):
                orig = _via or _orig                                   # with_options 사본이면 그 사본의 호출
                # Ollama 는 생각 끄기 인자가 다르다 — 호출부가 따로 정하지 않았으면 끈다
                if ollama and "reasoning_effort" not in kw and not (
                    kw.get("extra_body") or {}).get("reasoning_effort"):
                    eb = dict(kw.get("extra_body") or {})
                    eb.pop("chat_template_kwargs", None)
                    eb["reasoning_effort"] = "none"
                    kw = dict(kw, extra_body=eb)
                # 내부 vLLM 은 생각 모드가 기본으로 켜져 있다 — 호출부가 정하지 않았으면 끈다. 빠뜨린 호출이 출력 한도를
                # 생각에 다 써 느려지지 않게(반입 정체 읽기가 한 건 100초 걸리던 원인). 서버가 거절한 적이 있으면 보내지 않는다.
                # 끄는 인자는 연결 설정(no_think)을 따른다 — 호출부가 같은 이름의 인자를 정했으면 그 값이 먼저다
                elif self._no_think and not _no_extra and not (set(self._no_think) & set(kw.get("extra_body") or {})):
                    kw = dict(kw, extra_body={**(kw.get("extra_body") or {}), **self._no_think})
                if _no_schema and kw.get("response_format"):
                    kw = schema_as_prompt(kw)
                try:
                    resp = orig(*a, **kw)
                except TypeError:
                    raise
                except Exception as e:
                    # 서버가 구조화 출력을 거절하면 JSON 지시로 옮겨 다시 보낸다(설정이 json_schema 면 물러나지 않는다)
                    if kw.get("response_format") and self.structured == "auto" and _rejects_schema(e):
                        _no_schema.append(True)
                        import logging
                        logging.getLogger(__name__).warning("구조화 출력을 서버가 거절해 JSON 지시로 물러납니다: %s", self.model)
                        kw = schema_as_prompt(kw)
                        resp = orig(*a, **kw)
                    # 서버가 모르는 확장 인자를 거부하면 한 번만 빼고 다시 보낸다
                    elif kw.get("extra_body") and _rejects_extra(e):
                        self._extra = {}
                        _no_extra.append(True)
                        kw = dict(kw, extra_body=None)
                        resp = orig(*a, **kw)
                    else:
                        raise
                if not kw.get("stream"):
                    model_config.record_usage(
                        self.connection_id, kw.get("model", self.model), getattr(resp, "usage", None)
                    )
                    # 생각하는 모델은 한도를 생각에 다 써 답이 비어 돌아온다 — 한 번만 넓혀 다시 받는다
                    if _answer_is_empty(resp):
                        asked = int(kw.get("max_tokens") or 0)
                        wider = min(max(asked * 4, _THINKING_FLOOR), _THINKING_CEILING)
                        if wider > asked:
                            retry_kw = dict(kw, max_tokens=wider)
                            resp2 = orig(*a, **retry_kw)
                            model_config.record_usage(
                                self.connection_id, kw.get("model", self.model),
                                getattr(resp2, "usage", None),
                            )
                            _record(retry_kw, resp2)
                            return resp2
                    _record(kw, resp)
                return resp

            self.client.chat.completions.create = _create
            # with_options 로 만든 사본(시간 제한만 바꾼 호출)도 같은 규약을 거치게 — 사본의 생성 호출을 같은 방식으로 감싼다
            if hasattr(self.client, "with_options"):
                _with = self.client.with_options

                def _with_options(*wa, **wkw):
                    other = _with(*wa, **wkw)
                    inner = other.chat.completions.create

                    other.chat.completions.create = functools.partial(_create, _via=inner)
                    return other
                self.client.with_options = _with_options
                self.client.copy = _with_options
        except Exception:  # 가짜 클라이언트 등 — 기록만 건너뛴다
            pass
        # 외부 서버 연결이면 모든 생성 요청이 공통 관문을 거친다 — 개인정보를 토큰으로 바꿔 보내고 답에서 되돌린다(ADR-0044).
        # 관문을 씌우지 못하면 이 연결을 쓰지 않는다(원문 통과 금지).
        self.external = bool(cfg.get("external")) and (base_url is None or base_url == cfg.get("base_url"))
        if self.external:
            from zzaimy.generate import outbound

            try:
                outbound.guard_client(self.client)
            except Exception as e:
                raise RuntimeError("외부 연결의 개인정보 가림 관문을 준비하지 못해 이 연결을 쓰지 않습니다") from e
        # 문서 이미지 판독은 비전 모델이 따로 있으면 그것으로 (없으면 같은 모델).
        # 글 모델에 이미지를 보내면 거절당하거나 연결이 끊긴다. 그래서 따로 지정된
        # 모델이 있는지를 함께 알려, 호출부가 헛걸음하지 않게 한다.
        self.vision_model = cfg.get("vision_model") or self.model
        self.has_vision = bool(cfg.get("vision_model"))
        if not self.has_vision and self.kind == "vllm":
            # 비전 모델을 따로 지정하지 않아도, 기본 모델이 이미지를 읽을 수 있으면 쓴다
            self.has_vision = model_can_see(str(getattr(self.client, "base_url", "") or ""), self.model)
        # 생각 모드 끄기 인자는 내부 서버에만 보낸다(연결 설정이 있으면 그것) — 외부 API 는 모르는 인자를 거부한다
        self._extra = dict(self._no_think)

    @property
    def info(self) -> dict:
        """결과 기록에 남길 모델 정보(model_info — 서빙 이름·바탕 가중치·어댑터, 주소는 빼고). 연결·모델마다 한 번만 묻는다."""
        from zzaimy.generate import model_info

        try:
            return model_info.describe(self)
        except Exception:
            return {"name": self.model}

    def extract_schema(self, announcement_text: str) -> AnnouncementSchema:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "user", "content": _SCHEMA_PROMPT.format(announcement=announcement_text)}
            ],
            temperature=0.0,
            max_tokens=2048,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "AnnouncementSchema",
                    "schema": AnnouncementSchema.model_json_schema(),
                },
            },
            extra_body=self._extra,
        )
        return AnnouncementSchema.model_validate_json(
            _strip_fences(resp.choices[0].message.content or "")
        )

    def generate_section(
        self,
        section_name: str,
        requirements: str,
        criteria_text: str,
        direction: str,
        evidence: list[Evidence],
    ) -> str:
        evidence_block = "\n".join(
            f"- {e.text} (출처: {e.source_doc} p.{e.source_page})" for e in evidence
        )
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": _SECTION_PROMPT.format(
                        section_name=section_name,
                        requirements=requirements,
                        criteria=criteria_text,
                        direction=direction,
                        evidence=evidence_block,
                    ),
                }
            ],
            temperature=0.3,
            max_tokens=1024,
            extra_body=self._extra,
        )
        return (resp.choices[0].message.content or "").strip()
