"""에이전트 채팅 응답기 — 기준 문서 저장소를 근거로 질문에 답한다.

담당자가 "휴학 처리 기준이 뭐지" 같은 질문을 하면 등록된 규정·지침에서
관련 조각을 찾아 인용하며 답한다. 근거가 없으면 없다고 말한다.
"""

from __future__ import annotations

import os
import re

from zzaimy.app import llm_budget
from zzaimy.app.db import Database
from zzaimy.app.regulations import compose_review_context

_SYSTEM = """당신은 영남이공대학교 행정 담당자(교직원)를 돕는 AI 에이전트입니다.
사용자는 학생이 아니라 업무를 처리하는 교직원입니다. 학생 관련 규정을 물어도
그것은 담당자가 민원·서류를 처리하기 위한 것이므로, 처리 절차·확인 사항·근거 조항
중심의 업무 관점으로 답합니다.

답변 원칙:
- 참고 규정이 주어지면 그 내용을 근거로 답하고, 출처(규정명·조항)를 자연스럽게 언급합니다.
- 규정의 적용 대상을 구분합니다: 학칙·학사 규정은 학생에게, 취업규칙·임용 내규는 교직원에게
  적용됩니다. 질문의 주체(학생인지 직원인지)에 맞는 규정만 근거로 쓰고, 주체가 다른 규정을
  섞어야 할 때는 "직원에게는 ~" 처럼 대상을 명확히 밝힙니다. 주체가 불분명하면 어느 쪽인지
  확인하는 한 문장을 답 끝에 덧붙입니다.
- 같은 조항이 출처마다 다르게 적혀 있으면 지금 효력이 있는 글을 따릅니다: 규정명 뒤에 개정일이 붙은
  규정 문서 가운데 개정일이 가장 늦은 것이 기준이고, 사업 보고서·계획서에 실린 조문이나
  [옛 개정판] 표시가 붙은 글은 그 당시 판입니다. 옛 글과 다르면 "예전 판에는 ~였으나 현행은 ~" 처럼
  차이를 한 줄로 밝힙니다. 질문이 특정 연도의 판을 물으면 그 판을 따릅니다.
- 앞선 대화는 맥락일 뿐 근거가 아닙니다. 수치·날짜·기한·자격 같은 사실은 이번 메시지에 실린 근거 자료에 있는 것만
  씁니다. 앞 대화에만 나오고 이번 근거에 없는 사실은 "이번에 찾은 자료에서는 확인되지 않았습니다"라고 밝힙니다.
- 근거가 없는 내용은 추측하지 않습니다. 근거가 없으면 정중하게 그 사실을 알리고,
  어떤 규정·기준 문서를 등록하면 도움이 될지 한 문장으로 안내합니다.
- 자연스러운 존댓말로, 필요한 만큼만 간결하게 답합니다. 같은 문장을 반복하지 않습니다.
- 답의 꼴: 첫머리에 물음에 대한 답을 1~3문장으로 먼저 쓰고, 그 아래 근거의 요점을 항목 5개 이내로 씁니다.
  근거 글의 표·문단을 그대로 옮겨 적지 않습니다 — 물음에 필요한 수치·조항만 골라 쓰고, 나머지는 어느 문서에 있는지
  밝혀 원문을 보게 합니다. 특별히 길게 써 달라는 요청이 없으면 전체 600자 안팎으로 씁니다.
- 최종 판단은 담당자의 몫이라는 전제를 지킵니다.
- 근거 자료는 이 계정의 열람 범위(공통 자료와 소속·권한 받은 부서 자료) 안에서만 찾아 건넵니다. 다른 부서의 자료를 찾거나 참조해 달라는
  요청이면 "그 부서 자료는 열람 권한이 없습니다"라고 밝히고, 그 부서 문서의 제목·건수·있고 없음은 말하지 않습니다."""


# 검색이 기준을 넘는 근거를 하나도 찾지 못했을 때 프롬프트에 넣는 지시.
# 유사도·재랭킹 하한을 도입하면서 "근거 없음"이 정상 결과가 됐다 — 그때 관련 없는
# 조각을 끌어다 붙이면 담당자가 근거 없는 답을 근거 있는 답으로 오인한다.
NO_EVIDENCE_NOTE = (
    "[참고 자료 없음 — 등록된 문서에서 이 질문과 관련 있는 근거를"
    " 찾지 못했습니다. 답변은 반드시 \"관련 근거를 찾지 못했습니다.\"라는 사실을"
    " 먼저 밝히고 시작하며, 규정 내용을 지어내지 않습니다. 일반적인 업무 절차를"
    " 안내할 때는 그것이 등록된 근거가 아니라는 점을 분명히 밝힙니다.]"
)


# 하한을 넘는 근거가 없어 1위만 남긴 경우 — 인용은 하되 약하다는 사실을 밝힌다.
WEAK_EVIDENCE_NOTE = (
    "[근거 강도 주의 — 위 자료는 질문과의 연관도가 기준에 미치지 못했습니다."
    " 답변 첫머리에 관련 근거가 충분하지 않다는 점을 밝히고, 참고 수준으로만"
    " 인용하며 단정적인 판단을 내리지 않습니다.]"
)


# 답 쓰기(생성 단계)의 생각 모드 손잡이 — 기본은 끈다(운영 동작). ZZAIMY_ANSWER_THINK=1 이면 내부 vLLM 에 생각을 켜고
# 생각에 쓸 몫만큼 출력 한도와 시간 제한을 넓힌다(생각 글은 reasoning_content 로 따로 오고 답 글에는 섞지 않는다).
# 생각을 켜는 인자(chat_template_kwargs)는 그 인자를 받는 서버(client._extra 에 있는 경우)에만 보낸다. 측정: scripts/188 --think-compare
def answer_cot() -> bool:
    """답 생성 전 단계 추론(CoT) — 기본 켬. 끄려면 ZZAIMY_ANSWER_COT=0."""
    return os.environ.get("ZZAIMY_ANSWER_COT", "1").strip().lower() not in ("0", "false", "no", "off")


# 학습 데이터(구조 단계 문답, 「[N단계: …]」 추론 + 「[답]」)와 같은 꼴 — 입력마다 질문 파악 → 근거 확인 → 답 구성
COT_RULE = """답하기 전에 짧게 단계별로 생각한다. 꼴(제목 줄 그대로):
[1단계: 질문 파악] 담당자가 지금 원하는 것과 작업 메모의 목표에 비춰 이 요청이 어디에 해당하는지(1~2줄)
[2단계: 근거 확인] 아래 근거에서 쓸 것과 근거에 없는 것(1~3줄, 문서 이름·수치만 짚기)
[3단계: 답 구성] 답의 순서·형식(표·목록 여부)과 담당자에게 물어야 할 것(1~2줄)
[답]
여기부터 담당자에게 보일 답만 쓴다. 단계 글은 답에 되풀이하지 않는다."""

_ANS_MARK = re.compile(r"^\s*\[답\]\s*$|^\s*\[답\]\s*", re.M)
_STEP = re.compile(r"^\s*\[\d+단계[^\]]*\].*$", re.M)


def split_reasoning(text: str) -> tuple[str, str | None]:
    """(단계 추론, 답) — 「[답]」 이 아직 없으면 답은 None(쓰는 중)."""
    m = _ANS_MARK.search(text or "")
    if not m:
        return (text or "").strip(), None
    return text[:m.start()].strip(), text[m.end():].lstrip("\n")


def strip_reasoning(text: str) -> str:
    """답 글에 섞인 단계 줄을 걷어 낸다(모델이 [답] 을 빼먹었거나 지난 대화에 남은 경우)."""
    r, a = split_reasoning(text or "")
    if a is not None:
        return a
    if _STEP.search(text or ""):
        lines = [ln for ln in (text or "").splitlines() if not _STEP.match(ln)]
        return "\n".join(lines).strip()
    return text or ""


def answer_think() -> bool:
    """답 쓰기에 생각을 켜는가 — 운영 설정(ZZAIMY_ANSWER_THINK=1) 또는 이 질문에 고른 답변 깊이 「꼼꼼하게」(app/effort)."""
    from zzaimy.app import effort

    return effort.thinking() or os.environ.get("ZZAIMY_ANSWER_THINK", "0").strip().lower() in ("1", "true", "yes", "on")


_NOT_SET = object()


def _think_extra(client, extra: dict) -> dict | None:
    """생각을 켜는 추가 인자 — 연결 설정(client.think_extra)이 있으면 그것. 그 속성이 없는 클라이언트(가짜·옛 꼴)는
    끄는 인자에 chat_template_kwargs 가 있을 때만 그 안의 enable_thinking 을 켠다(예전 규칙)."""
    think = getattr(client, "think_extra", _NOT_SET)
    if think is _NOT_SET:
        return {"chat_template_kwargs": {"enable_thinking": True}} if "chat_template_kwargs" in (extra or {}) else None
    return think or None


def answer_gen_options(client) -> tuple[int, dict]:
    """답 생성 호출의 (출력 한도, 추가 인자). 생각을 끈 기본은 지금까지와 같다 — 3,072 토큰, client._extra 그대로."""
    extra = getattr(client, "_extra", {})
    limit = int(os.environ.get("ZZAIMY_ANSWER_MAX_TOKENS", "3072"))
    think = _think_extra(client, extra) if answer_think() else None
    if not think:
        return limit, {"extra_body": extra}
    eb = dict(extra or {})
    for k, v in think.items():                       # 같은 이름의 인자는 안쪽 값만 덮는다(chat_template_kwargs 의 다른 값은 둔다)
        eb[k] = {**eb[k], **v} if isinstance(v, dict) and isinstance(eb.get(k), dict) else v
    from zzaimy.app import effort

    # 출력 한도 = 답 몫 + 생각 몫(꼼꼼하게의 think_tokens) — 생각이 한도를 다 먹어 답이 비지 않게
    return (int(os.environ.get("ZZAIMY_ANSWER_THINK_MAX_TOKENS", "") or max(8192, limit + effort.think_tokens())),
            {"extra_body": eb, "timeout": float(os.environ.get("ZZAIMY_ANSWER_THINK_TIMEOUT", "900"))})


_FULLWIDTH = {c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}
_FULLWIDTH.update({0x3000: 0x20})


def normalize_input(text: str) -> str:
    """입력 정리 — 전각 영문·숫자·기호(ＡＩＤ, ＩＣＴ)를 반각으로, 「。」「、」를 마침표·쉼표로. 한글은 그대로.
    일부 입력기가 전각으로 보내면 검색 낱말(AID)이 문서와 맞지 않는다."""
    t = (text or "").translate(_FULLWIDTH)
    return t.replace("。", ". ").replace("、", ", ").replace("  ", " ")


_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\uFE0F\u200D]")


def strip_emoji(text: str) -> str:
    """답의 이모지를 뺀다 — 행정 문서·화면은 이모지를 쓰지 않는다. 기호(□○※·→)는 그대로."""
    return re.sub(r"[ \t]{2,}", " ", _EMOJI.sub("", text or ""))


_HAN = re.compile(r"[\u4e00-\u9fff]{2,}")


def fix_foreign_han(answer: str, given: str, client) -> str:
    """답에 섞인 중국어 낱말(「인재缺口」)을 한국어로 — 건넨 글(질문·근거)에 없는 한자 줄만 골라 그 줄만 다시 쓰게 한다."""
    lines = answer.splitlines()
    bad = [i for i, ln in enumerate(lines) if any(w not in given for w in _HAN.findall(ln))][:12]
    if not bad:
        return answer
    try:
        numbered = "\n".join(f"{k}|{lines[i]}" for k, i in enumerate(bad))
        resp = llm_budget.create_fitted(
            client, [{"role": "user", "content": (
                "아래 줄들에 섞인 중국어 낱말을 뜻이 같은 한국어로 바꾸세요. 다른 글자·기호·서식은 그대로 두고, "
                "「번호|줄」 꼴 그대로 줄마다 하나씩만 돌려주세요.\n\n" + numbered)}], 900, floor=900,
            temperature=0.0, extra_body=getattr(client, "_extra", {}))
        for row in (resp.choices[0].message.content or "").splitlines():
            k, _, body = row.partition("|")
            if k.strip().isdigit() and int(k) < len(bad) and body.strip():
                lines[bad[int(k)]] = body
    except Exception:
        return answer
    return "\n".join(lines)


def rank_criteria_chunks(db, question: str, chunks: list[dict], criteria_ids: list[int], top_k: int = 12) -> list[dict]:
    """기준 조각을 질문과의 관련도로 고른다 — 문서마다 첫 자리를 하나씩 보장한 뒤 나머지는 점수순. 검색이 안 되면 문서 순서."""
    if not chunks:
        return []
    # 열린 검색과 달리 꼬리를 자르지 않는다 — 기준은 이미 담당자가 고른 범위라 순서만 필요하다(자르면 한 조각만 남아
    # 지원 규모·기한 같은 근거가 빠진다)
    try:
        from zzaimy.app.regulations import hybrid_candidates
        from zzaimy.app.rerank import rerank_scored

        cands = hybrid_candidates(db, question, 1, None, None, chunks=chunks, limit=max(60, len(chunks)))
        scored = rerank_scored(question, cands) if cands else None
        ranked = [c for c, _s in sorted(scored, key=lambda x: -x[1])] if scored else list(cands)
    except Exception:
        ranked = []
    if not ranked:
        return chunks
    out: list[dict] = []
    seen: set = set()
    for did in criteria_ids:                                 # 문서마다 가장 관련 있는 조각 하나
        first = next((h for h in ranked if h.get("doc_id") == did), None)
        if first is not None and id(first) not in seen:
            out.append(first); seen.add(id(first))
    for h in ranked:                                         # 그다음은 점수순
        if id(h) not in seen:
            out.append(h); seen.add(id(h))
    key = lambda c: (c.get("id"), c.get("doc_id"), (c.get("content") or "")[:80])   # noqa: E731
    have = {key(c) for c in out}
    for c in chunks:                                         # 후보에 안 든 조각은 문서 순서대로 뒤에
        if key(c) not in have:
            out.append(c)
    return out


def _cite(hit: dict) -> str:
    """근거 블록 한 덩어리 — 표제가 없으면 문서명만 쓴다(빈 《 · 》를 만들지 않는다)."""
    title = hit.get("reg_title") or "문서"
    heading = (hit.get("heading") or "").strip()
    head = f"《{title} · {heading}》" if heading else f"《{title}》"
    return f"{head}\n{(hit.get('content') or '')[:600]}"


def pack_criteria_context(ordered: list[dict], budget: int = 6000) -> tuple[str, list[dict]]:
    """Only advertise sources whose evidence was actually included in the prompt."""
    header = "[선택된 기준 문서 — 이 기준으로 판단하고 인용하라]\n\n"
    remaining = budget - len(header)
    parts, included = [], []
    for chunk in ordered:
        if not str(chunk.get("content") or "").strip():
            continue
        piece = _cite(chunk)
        cost = len(piece) + (2 if parts else 0)
        if cost > remaining:
            continue
        parts.append(piece)
        included.append(chunk)
        remaining -= cost
    if not parts:
        return NO_EVIDENCE_NOTE, []
    return header + "\n\n".join(parts), included


_SYSTEM_STUDENT = """당신은 영남이공대학교 학생의 학사 질문에 답하는 AI 안내 도우미입니다.
학생은 수강·휴학·복학·졸업·학적·계절학기·편입·학사경고처럼 자기 학교생활에 관한 것을 묻습니다. 사업 문서 작성이나 교직원 업무는 다루지 않습니다.

답변 원칙:
- 근거로 건넨 학칙·학사 규정에 있는 내용으로만 답하고, 어느 규정 몇 조인지 자연스럽게 밝힙니다.
- 학생이 알아듣게 쉬운 말로 풀어 씁니다. 결론(되는지·언제까지·몇 학점인지)을 먼저, 이어서 조건과 절차를 짧게 씁니다.
- 기한·학점·횟수 같은 수치는 근거에 있는 그대로 씁니다. 근거에 없으면 추측하지 않고 "규정에서 확인되지 않습니다"라고 밝히고
  학과 사무실이나 학사 담당 부서에 확인하라고 안내합니다.
- 같은 규정이 여러 판이면 개정일이 가장 늦은 판을 따릅니다.
- 개인 성적·학적 상태처럼 본인 기록을 봐야 답할 수 있는 것은 이 도우미가 볼 수 없다고 알리고 확인할 곳을 안내합니다.
- 규정 해석이 갈리거나 예외 승인이 필요한 일은 최종 판단이 학교(학과·학사 담당 부서)에 있다고 밝힙니다.
- 자연스러운 존댓말로, 필요한 만큼만 간결하게 답합니다."""


def compose_system(profile: dict, audience: str = "staff") -> str:
    """기본 시스템 프롬프트에 담당자 프로필·지침을 얹는다 (설정 화면에서 저장). 학생 계정은 학사 질문 안내 지시(audience="student")."""
    if audience == "student":
        return _SYSTEM_STUDENT
    parts = [_SYSTEM]
    # 호칭·개인 응답 지침은 설정 화면에서 뺐다(10-17) — 화면에 없는 옛 값이 답을 바꾸지 않게 쓰지 않는다. 지침은 프로젝트 「작성 원칙」
    dept = (profile.get("dept") or "").strip()
    if dept:
        parts.append(f"담당자 정보:\n- 담당자는 {dept} 소속입니다 — 그 부서 업무 맥락을 우선 고려합니다")
    return "\n\n".join(parts)


class AgentResponder:
    @staticmethod
    def _stream(client, messages, limit, cuts, on_fitted, on_partial, gen: dict | None = None,
                usage_out: list | None = None, timing: dict | None = None) -> tuple[str, str]:
        """조각 응답으로 답 생성 — (글, 끝난 까닭). 0.4초마다 지금까지 쓴 글을 on_partial 로 넘긴다.
        gen: 생성 추가 인자(answer_gen_options) — 없으면 client._extra. 생각 글(delta.reasoning_content)은 답에 넣지 않는다.
        usage_out: 주면 마지막 사용량(usage)을 덧붙인다(측정용). timing: 주면 첫 글자까지 걸린 초(first_token_sec)를 적는다."""
        import time as _time

        t_start = _time.monotonic()
        from zzaimy.generate import model_config

        gen = gen if gen is not None else {"extra_body": getattr(client, "_extra", {})}
        with llm_budget.label("답 쓰기"):
            stream = llm_budget.create_fitted(client, messages, limit, cuts, floor=1024, on_fitted=on_fitted, temperature=0.2,
                                          stream=True, stream_options={"include_usage": True}, **gen)
        parts: list[str] = []
        finish, last, usage = "", 0.0, None
        # 생각을 켠 호출(답변 깊이 「꼼꼼하게」)은 서버가 생각 글을 본문에 「…</think>」 로 붙여 보낸다 — 그 뒤만 답이다.
        # 생각이 끝나기 전에는 화면에 흘리지 않는다(10-27 운영에서 영어 생각 글이 답에 그대로 보였다)
        from zzaimy.app import effort as _effort
        _kw = ((gen or {}).get("extra_body") or {}).get("chat_template_kwargs") or {}
        think_on = bool(_effort.thinking() or _kw.get("enable_thinking"))

        def visible(final: bool = False) -> str:
            t = "".join(parts)
            if "</think>" in t:
                return t.split("</think>")[-1].lstrip()
            return t if (final or not think_on) else ""
        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            for ch in getattr(chunk, "choices", None) or []:
                piece = getattr(getattr(ch, "delta", None), "content", None)
                if piece:
                    if not parts and timing is not None:
                        timing["first_token_sec"] = round(_time.monotonic() - t_start, 2)   # 담당자가 첫 글자를 보기까지(읽기 + 첫 토큰)
                    parts.append(piece)
                if getattr(ch, "finish_reason", None):
                    finish = ch.finish_reason
            now = _time.monotonic()
            if parts and now - last >= 0.4:
                last = now
                shown = visible()
                if shown:
                    try:
                        on_partial(shown)
                    except Exception:
                        pass
        if usage is not None and usage_out is not None:
            usage_out.append(usage)
        if usage is not None:                                     # 조각 응답은 사용량 기록을 건너뛰므로 여기서
            try:
                model_config.record_usage(getattr(client, "connection_id", None), client.model, usage)
            except Exception:
                pass
        return visible(final=True), finish

    def answer(
        self,
        db: Database,
        question: str,
        attachment_text: str | None = None,
        criteria_ids: list[int] | None = None,
        session_id: int | None = None,
        project: dict | None = None,
        scope: dict | None = None,
        on_progress=None,
        evidence: list[dict] | None = None,
        on_partial=None,
    ) -> str:
        """evidence: 부른 쪽(문서 작업 에이전트)이 이미 찾아 둔 근거 [{title, text, doc_id?, note?}]. 열람 범위 안에서 찾은 것이며,
        답의 첫 근거로 건네고 출처 목록에도 넣는다. 수치는 이 근거와 아래 검색 근거에 있는 것만 쓴다."""
        from zzaimy.generate.client import VllmClient
        from zzaimy.app.regulations import find_relevant

        # 범위(부서·역할)는 검색 단계에서 자른다 — 범위 밖 조각은 모델에게 건네지지 않는다(절대 규칙 4)
        scope = scope or {}
        question = normalize_input(question)

        self.last_sources = []
        self.last_flagged: list[str] = []
        self.last_model: dict = {}
        self.last_steps: list[str] = []
        import time as _time
        _t0 = _time.time()
        self.last_grant_docs: list = []
        self.last_timing = {}                               # 단계별 걸린 시간(초) — 측정 스크립트(scripts/188)가 읽는다
        if on_progress:
            on_progress("관련 근거 검색 중")
        if criteria_ids:
            # 담당자가 기준을 직접 고른 경우 — 그 기준의 조각들만 사용(범위 고정). 문서 순서대로 앞부분만 자르면
            # 기준 문서가 여럿일 때 첫 문서만 들어가고 나머지(공고의 신청 기한 등)가 빠진다.
            # 그래서 질문과의 관련도로 고르고, 기준 문서마다 적어도 한 조각은 들어가게 한다.
            chunks = db.chunks_for_docs(criteria_ids)
            ordered = rank_criteria_chunks(db, question, chunks, criteria_ids)
            context, hits = pack_criteria_context(ordered)
        else:
            # 현재 등록 문서만 검색한다. 폐기한 공개 코퍼스는 조회하지 않는다.
            from zzaimy.app.access_guard import reg_kwargs
            from concurrent.futures import ThreadPoolExecutor
            # 규정 검색은 사업 문서 검색(질문 읽기·그래프)과 서로 기대지 않는다 — 함께 돌려 기다리는 시간을 줄인다
            _pool = ThreadPoolExecutor(max_workers=1)
            _reg_future = _pool.submit(lambda: (find_relevant(db, attachment_text or question, sector=scope.get("sector"),
                                                              **reg_kwargs(scope)), _time.time()))
            _t1 = _time.time()
            blocks = []
            # 사업 문서 계열(국고 계획서·실적보고서·평가 …) — 규정과 따로 찾고 따로 건넨다(절대 규칙 6, ADR-0049).
            # 그래프로 사업·연차를 먼저 좁힌 뒤 그 문서들에서 찾는다. 문서함·업로드·DGX 원본 어디서 들어왔든 같은 색인
            grant_hits = []
            try:
                if scope.get("role") == "student" or scope.get("grant") is False:
                    raise LookupError("이 RAG 공간은 사업 문서를 쓰지 않는다(ADR-0052·0053)")
                from zzaimy.app import grant_search
                from zzaimy.graph.question_read import reader_for
                # 질문을 온톨로지 말(사업·연차·갈래·따라갈 관계)로 읽는 일은 모델이 — 실패하면 이름 맞추기로 물러난다
                reader = reader_for("answer")
                prefer = None
                own: set[int] = set()
                if project and project.get("id"):
                    # 이 프로젝트에 올린 문서(공고·기본계획·작성 서식 등)가 먼저다 — 제 문서를 일반 검색에 맡기면
                    # 「공고문으로 개요·목차를 잡아 줘」 같은 요청에 다른 사업 계획서가 근거로 앞선다
                    with db._conn() as conn:
                        own = {int(r[0]) for r in conn.execute(
                            "SELECT id FROM documents WHERE project_id = ?", (int(project["id"]),)).fetchall()}
                    # 프로젝트에 참조로 붙은 과거 사업 보관 묶음의 문서를 먼저 본다 — 없거나 맞는 조각이 없으면 평소대로
                    from zzaimy.app import project_refs
                    ref_ids = [r["ref_project_id"] for r in project_refs.list_refs(db, int(project["id"]))]
                    if ref_ids:
                        with db._conn() as conn:
                            prefer = {int(r[0]) for r in conn.execute(
                                f"SELECT id FROM documents WHERE project_id IN ({','.join('?' * len(ref_ids))})", ref_ids).fetchall()}
                g = grant_search.search(db, attachment_text or question, k=5, user=scope.get("user"), prefer_docs=prefer,
                                        depts=scope.get("grant_depts"), reader=reader)
                grant_hits = g["hits"]
                self.last_steps = [str(x)[:200] for x in (g.get("steps") or [])][:12]
                if own:
                    mine = grant_search.search(db, attachment_text or question, k=5, user=scope.get("user"), prefer_docs=own,
                                               depts=scope.get("grant_depts"), reader=reader)["hits"]
                    mine = [h for h in mine if h.get("doc_id") in own]
                    seen = {h.get("chunk_id") for h in mine}
                    grant_hits = mine + [h for h in grant_hits if h.get("chunk_id") not in seen][: max(2, 7 - len(mine))]
                if grant_hits:
                    blocks.append("[사업 문서 — 계획서·실적보고서 등. 사업·연차·문서 이름을 밝히고, 수치는 이 글에 있는 것만 쓴다]\n"
                                  + "\n\n".join(f"〈{' > '.join(h['path'])}〉\n{h['content']}" for h in grant_hits))
            except LookupError:
                grant_hits = []
            except Exception as e:                       # 검색이 깨져도 답은 하되, 조용히 「근거 없음」으로 숨지 않게 남긴다
                import logging
                logging.getLogger(__name__).warning("사업 문서 검색 실패: %s: %s", type(e).__name__, str(e)[:200])
                grant_hits = []
            self.last_timing["grant_sec"] = round(_time.time() - _t1, 2)
            hits, _reg_done = _reg_future.result()
            _pool.shutdown(wait=False)
            self.last_timing["regulation_sec"] = round(_reg_done - _t0, 2)
            if hits:                                     # 규정 묶음은 사업 문서 묶음 앞에(예전과 같은 순서)
                blocks.insert(0, "[교내 규정 — 관련 조항을 근거로 인용하라]\n" + "\n\n".join(_cite(h) for h in hits))
            self.last_grant_docs = [h.get("doc_id") for h in grant_hits]
            if grant_hits and hits and all(h.get("weak_evidence") for h in hits) and blocks and blocks[0].startswith("[교내 규정"):
                # 사업 문서 근거가 있는데 규정 근거는 하한을 못 넘은 것뿐이면 규정 묶음은 빼고 사업 문서만 — 관계없는 규정이
                # 다른 사업·연도 질문의 답에 끼어들지 않게
                blocks = blocks[1:]
                hits = []
            if not blocks:
                # 기준 미달이라 근거가 하나도 남지 않은 경우. 있는 척하지 않는다.
                blocks.append(NO_EVIDENCE_NOTE)
            elif hits and all(h.get("weak_evidence") for h in hits):
                # 하한을 넘지 못해 1위만 남긴 경우 — 약하다는 사실을 함께 알린다
                blocks.append(WEAK_EVIDENCE_NOTE)
            context = "\n\n".join(blocks)

        ev = [e for e in (evidence or []) if (e.get("text") or "").strip()][:8]
        ev_block = ""
        if ev:
            ev_block = ("[이미 찾은 근거 — 먼저 이것으로 답하고 출처는 《 》 안의 문서 제목으로 밝힌다. 수치·날짜는 근거에 있는 것만 쓴다]\n"
                        + "\n\n".join(f"《{e.get('title') or '문서'}》{(' · ' + e['note']) if e.get('note') else ''}\n{str(e['text'])[:1200]}"
                                        for e in ev))
            # 검색만으로는 근거가 없다고 나왔어도 이미 찾은 근거가 있으면 「근거 없음」 안내를 붙이지 않는다
            if context.strip() == NO_EVIDENCE_NOTE.strip():
                context = ""
        search_ctx = context
        context = "\n\n".join(x for x in (ev_block, search_ctx) if x)
        # 실제 검색된 등록 문서의 근거. LLM 성공/실패와 무관하게 저장.
        def _mk(h: dict, origin: str, linkable: bool) -> dict:
            return {
                "title": h.get("reg_title") or "문서",
                "heading": h.get("heading") or "",
                "snippet": (h.get("content") or "")[:160],
                "doc_id": h.get("doc_id") if linkable else None,
                "origin": origin,
                # 하한을 못 넘어 남긴 근거 — 화면에서 "근거가 약합니다"로 표시한다
                "weak": bool(h.get("weak_evidence")),
            }

        self.last_sources = [_mk(h, "교내 규정", True) for h in (hits or [])[:4]]
        for h in (locals().get("grant_hits") or [])[:4]:
            self.last_sources.append({"title": h.get("filename") or "사업 문서", "heading": " > ".join(h.get("path", [])[:2]),
                                      "snippet": (h.get("content") or "")[:160], "doc_id": h.get("doc_id"),
                                      "origin": "사업 문서", "weak": False})
        for e in ev[:4]:
            self.last_sources.insert(0, {"title": e.get("title") or "문서", "heading": e.get("note") or "",
                                         "snippet": str(e.get("text") or "")[:160], "doc_id": e.get("doc_id"),
                                         "origin": "에이전트가 찾음", "weak": False})
        if on_progress:
            on_progress(f"답변에 사용할 근거 {len(hits or [])}개 구성"
                        if hits else "관련 근거 없음 · 확인 가능한 범위로 답변 준비")
        # 대화 기억 — 작업 메모(목표·결정·진행·남은 일)와 문맥 한도 몫만큼의 최근 대화(chat_memory). 개수로 자르지 않는다
        from zzaimy.app import chat_memory
        _ctx_len = llm_budget.work_len(VllmClient(role="answer"))
        memo, history = ("", [])
        if session_id:
            _all = db.list_chats(session_id, limit=2)
            _cur = {int(m["id"]) for m in _all[-1:] if m.get("role") == "user" and (m.get("content") or "").strip() == question.strip()}
            memo, history = chat_memory.window(db, session_id, _ctx_len, exclude_ids=_cur)
        prof = db.profile_for(scope.get("user")) if hasattr(db, "profile_for") else db.all_settings()
        if scope.get("dept"):
            prof = dict(prof, dept=scope["dept"])           # 부서는 계정에 정해진 값(RAG 공간과 같은 것)
        system = compose_system(prof, "student" if tuple(scope.get("levels") or ()) == ("student",) else "staff")
        if project:
            lines = [f"이 대화는 프로젝트 「{project['name']}」 업무 맥락입니다."]
            if (project.get("instructions") or "").strip():
                lines.append(f"프로젝트 지침: {project['instructions'].strip()}")
            if (project.get("memo") or "").strip():
                lines.append(f"프로젝트 메모: {project['memo'].strip()}")
            notes = db.list_project_notes(int(project["id"]))[:10]
            for n in notes:
                lines.append(f"프로젝트 지침·메모({n['created_at'][:10]}): {n['content']}")
            system += "\n\n" + "\n".join(lines)
        if memo:
            system += "\n\n" + chat_memory.block(memo)
        if answer_cot():
            system += "\n\n" + COT_RULE
        messages: list[dict] = [{"role": "system", "content": system}]
        for m in history:
            messages.append({"role": m["role"], "content": strip_reasoning(m["content"])[:chat_memory.MSG_CAP]})
        if history:
            messages.append({"role": "user", "content": "[앞선 대화는 여기까지 — 맥락일 뿐 근거가 아니다. 수치·날짜는 아래 근거 자료에서만]"})
        att_block = f"\n\n[첨부 문서 본문 — 개인정보 마스킹됨]\n{attachment_text[:8000]}" if attachment_text else ""
        user_content = question + att_block + (f"\n\n{context}" if context else "")
        messages.append({"role": "user", "content": user_content})

        client = VllmClient(role="answer")
        self.last_timing["search_sec"] = round(_time.time() - _t0, 2)
        _t2 = _time.time()
        if on_progress:
            on_progress("답변 작성 중")
        # 목차·개요처럼 긴 답은 짧은 상한(1,024 토큰)에서 중간에 끊긴다. 상한을 넉넉히 두고, 그래도 끊기면 한 번 잇는다
        # 생각 모드는 기본 끔 — ZZAIMY_ANSWER_THINK=1 일 때만 켜고 한도·시간 제한을 넓힌다(answer_gen_options)
        limit, gen = answer_gen_options(client)
        self.last_timing["think"] = answer_think() and bool(_think_extra(client, getattr(client, "_extra", {}) or {}))
        usages: list = []
        # 문맥 한도 — 요청과 에이전트가 찾은 근거는 끝까지 남기고, 첨부·연결 문서 → 오래된 대화 → 출력 상한(바닥까지)
        # → 검색 근거 → 에이전트 근거 차례로 줄인다(llm_budget). 다 줄여도 넘으면 보내지 않는다(ContextOverflow)
        hist_cuts = [llm_budget.Cut(msg=i) for i in range(1, 1 + len(history))]
        cuts = ([llm_budget.Cut(msg=-1, text=att_block)] if att_block else []) + hist_cuts + [llm_budget.OUTPUT]
        cuts += [llm_budget.Cut(msg=-1, text=x) for x in (search_ctx, ev_block) if x]
        sent: dict = {}
        self.last_context = user_content                    # 줄이기 전 글 — 아래에서 실제로 보낸 글로 바뀐다

        def _sent(msgs: list[dict]) -> None:
            sent["messages"] = msgs
            self.last_context = msgs[-1]["content"]         # 모델에 실제로 건넨 글(측정 스크립트가 답의 수치를 이것과 대조한다)

        text, finish = "", ""
        if on_partial is not None and answer_cot():
            _raw_partial = on_partial

            def on_partial(t, _p=_raw_partial):          # 단계 추론 동안은 진행 줄에 단계만, [답] 부터 화면에
                reasoning, ans = split_reasoning(t)
                if ans is None:
                    if on_progress and reasoning:
                        step = re.findall(r"\[(\d+)단계[^\]]*\]", reasoning)
                        on_progress(f"생각하는 중 · {step[-1]}단계" if step else "생각하는 중")
                    return
                _p(ans)
        if on_partial is not None:
            # 쓰는 대로 화면에 — 답 생성(초당 10토큰 안팎)이 끝날 때까지 기다리지 않게. 조각 응답을 못 하는 서버면 아래 한 번에 받기로
            try:
                text, finish = self._stream(client, messages, limit, cuts, _sent, on_partial, gen=gen, usage_out=usages,
                                            timing=self.last_timing)
            except Exception:
                text, finish = "", ""
        if not text:
            with llm_budget.label("답 쓰기"):
                resp = llm_budget.create_fitted(client, messages, limit, cuts, floor=1024, on_fitted=_sent, temperature=0.2, **gen)
            usages.append(getattr(resp, "usage", None))
            text = resp.choices[0].message.content or ""            # 생각 글(reasoning_content)은 답에 넣지 않는다
            finish = getattr(resp.choices[0], "finish_reason", "")
        messages, user_content = sent["messages"], sent["messages"][-1]["content"]
        if finish == "length" and text:
            if on_progress:
                on_progress("답변 이어 쓰는 중")
            try:
                more_msgs = messages + [{"role": "assistant", "content": text},
                                        {"role": "user", "content": "끊긴 곳 바로 뒤부터 이어서 끝까지 쓰세요. 앞 내용은 되풀이하지 마세요."}]
                more = llm_budget.create_fitted(client, more_msgs, limit,
                                                [llm_budget.Cut(msg=i) for i in range(1, len(messages) - 1)], floor=512,
                                                temperature=0.2, **gen)
                usages.append(getattr(more, "usage", None))
                text = text.rstrip() + "\n" + (more.choices[0].message.content or "").lstrip()
            except Exception:
                pass
        reasoning, ans = split_reasoning(text)
        self.last_reasoning = reasoning if ans is not None else ""
        text = ans if ans is not None else strip_reasoning(text)
        _t_han = _time.time()
        out = strip_emoji(fix_foreign_han(text.strip(), user_content, client))
        if _time.time() - _t_han > 0.05:
            self.last_timing["han_fix_sec"] = round(_time.time() - _t_han, 2)   # 중국어 낱말 고치기 호출이 돈 경우만
        try:
            from zzaimy.generate import model_info as _mi
            self.last_model = _mi.stamp(client)              # 답 실행 기록(answer_runs)에 — 어느 모델이 썼나
        except Exception:
            self.last_model = {}
        # 마지막 점검 — 모델에 건넨 글(질문·첨부·근거)에 없는 수치는 지우지 않고 「(근거 확인 필요)」를 붙인다(절대 규칙 1: 수치는 인출만)
        from zzaimy.verify.numbers import flag_unsupported
        out, flagged = flag_unsupported(out, [user_content])
        self.last_flagged = flagged
        if flagged:
            out += f"\n\n※ 근거 문서에서 찾지 못한 수치 {len(flagged)}개에 「(근거 확인 필요)」를 붙였습니다 — 원문에서 확인해 주세요."
        self.last_timing["generate_sec"] = round(_time.time() - _t2, 2)
        toks = [getattr(u, "completion_tokens", None) for u in usages if u is not None]
        if any(isinstance(t, int) for t in toks):
            self.last_timing["completion_tokens"] = sum(t for t in toks if isinstance(t, int))
        ptoks = [getattr(u, "prompt_tokens", None) for u in usages if u is not None]
        if any(isinstance(t, int) for t in ptoks):
            self.last_timing["prompt_tokens"] = sum(t for t in ptoks if isinstance(t, int))   # 근거가 길면 읽기(prefill)가 첫 글자를 늦춘다
        self.last_timing["total_sec"] = round(_time.time() - _t0, 2)
        return out
