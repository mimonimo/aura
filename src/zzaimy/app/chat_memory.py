"""대화 기억 — 긴 대화도 앞부분을 잃지 않고, 두루뭉술하게 시작한 요청이 대화를 거치며 분명한 목표로 모이게 한다.

왜. 요청마다 최근 몇 개만 넣으면 그 밖의 대화(담당자가 알려 준 값, 정한 방향, 만든 문서)가 통째로 사라진다.
- 작업 메모: 답이 끝날 때마다(배경에서) 앞 메모 + 그 뒤 대화 → 새 메모로 고친다(compact). 메모는 다섯 갈래다 —
  최종 결과물(문서 이름·용도·대상·마감 한 줄), 지금 목표(대화가 진행되며 구체화된 목표와 핵심 조건), 확정된 것(정한 것·알려 준 값),
  진행 상황(만든 문서·쓴 부분), 남은 일·물어볼 것. 최종 결과물 줄은 담당자가 바꾸자고 하지 않으면 이전 값을 지킨다(keep_final — 코드 관문).
  대화가 길어질수록 지난 과정은 더 압축되고 목표·결정·값·남은 일은 남는다. 메모 길이 상한은 문맥 한도의 몫(SUMMARY_SHARE).
- 최근 창: 원문은 개수가 아니라 그 모델 문맥 한도의 일정 몫(RECENT_SHARE)만큼 새 것부터 — 모델을 큰 것으로 바꾸면 저절로 더 담는다.
- 메모는 맥락일 뿐 근거가 아니다 — 답과 문서의 수치는 지금처럼 검색한 근거에서만 쓴다(절대 규칙 1).
저장: 설정 chat_summary:<대화 id> = {"text", "upto"(메모에 접은 마지막 메시지 id), "n"(접은 메시지 수), "at"}.
"""
from __future__ import annotations

import json
import os
import re
import time

from zzaimy.app import llm_budget

RECENT_SHARE = 0.30          # 최근 대화 원문에 쓰는 문맥 몫
SUMMARY_SHARE = 0.06         # 요약 길이 상한(토큰) — 문맥 몫
MSG_CAP = 4000               # 메시지 하나에서 넣는 글자 상한(아주 긴 답 하나가 창을 다 먹지 않게)
MIN_FOLD = 1                 # 새 메시지가 하나라도 있으면 메모를 고친다(목표가 매 턴 구체화되게)


def load(db, session_id: int) -> dict:
    try:
        v = json.loads(db.get_setting(f"chat_summary:{int(session_id)}", "") or "{}")
    except ValueError:
        v = {}
    return v if isinstance(v, dict) else {}


def _tokens(m: dict) -> int:
    return llm_budget.estimate_tokens(str(m.get("content") or "")[:MSG_CAP]) + 8


def split(messages: list[dict], budget: int) -> tuple[list[dict], list[dict]]:
    """(창 밖 앞부분, 최근 창) — 새 것부터 budget 토큰까지 창에 담는다. 가장 새 메시지 하나는 넘어도 담는다."""
    used, start = 0, len(messages)
    for i in range(len(messages) - 1, -1, -1):
        t = _tokens(messages[i])
        if used + t > budget and start < len(messages):
            break
        used += t
        start = i
    return messages[:start], messages[start:]


def window(db, session_id: int, context_len: int, *, exclude_ids: set[int] | None = None) -> tuple[str, list[dict]]:
    """(지난 대화 요약, 최근 대화) — 요청에 넣을 기억. exclude_ids 는 지금 처리 중인 질문처럼 따로 넣는 메시지."""
    msgs = [m for m in db.list_chats(session_id, limit=400) if m.get("role") in ("user", "assistant")
            and int(m["id"]) not in (exclude_ids or set())]
    _, recent = split(msgs, int(context_len * RECENT_SHARE))
    s = load(db, session_id)
    summary = (s.get("text") or "").strip()
    if summary and recent and int(s.get("upto") or 0) < int(recent[0]["id"]) - 1:
        # 요약이 창 앞까지 아직 따라오지 못했다(요약이 밀린 채 대화가 이어짐) — 그 사이 메시지도 짧게 창에 붙인다
        gap = [m for m in msgs if int(s.get("upto") or 0) < int(m["id"]) < int(recent[0]["id"])]
        recent = gap[-6:] + recent
    elif not summary:
        older, _ = split(msgs, int(context_len * RECENT_SHARE))
        if older:                                            # 요약이 아직 없으면(첫 압축 전) 창 바로 앞 몇 개라도
            recent = older[-4:] + recent
    return summary, recent


SUMMARY_PROMPT = """아래는 행정 업무 대화의 [지난 작업 메모]와 그 뒤에 이어진 [대화]다. 둘을 합쳐 새 작업 메모를 쓴다.
형식(제목 줄 그대로, 갈래마다 「- 」 짧은 항목 줄):
{final}지금 목표:
- 담당자가 결국 얻으려는 최종 결과물을 한두 줄로. 처음엔 막연했어도 대화에서 구체화된 만큼 분명하게(문서 이름·용도·마감·범위), 그 결과물을 정하는
  핵심 조건(대상·규모·예산·기간처럼 대화에서 정해진 것)을 함께 적는다. 지금 하고 있는 한 단계(절 채우기·서식 고치기)는 목표가 아니라 진행 상황에 적는다.
확정된 것:
- 담당자가 정했거나 알려 준 것(이름·날짜·금액·부서·방향은 원문 그대로).
진행 상황:
- 만든·연 문서와 쓴 부분, 끝낸 일.
남은 일·물어볼 것:
- 아직 안 한 일, 담당자에게 받아야 할 값·결정.
규칙: 대화에 없는 사실을 보태지 않는다. 바뀐 결정은 새 것만 남긴다. 인사·되풀이·과정 설명·근거 문서 본문은 버린다. 모두 {limit}자 안에서."""
FINAL_PROMPT = """최종 결과물:
- 담당자가 결국 받을 문서 한 줄 — 문서 이름·용도·대상·마감(대화에 나온 만큼). 담당자가 다른 문서로 바꾸자고 하지 않았으면 [지난 작업 메모]의 이 줄을
  그대로 두고, 대화에서 더 분명해진 것만 보탠다. 지금 하는 단계(절 쓰기·고쳐 쓰기·표 만들기·서식 바꾸기)를 여기에 적지 않는다.
"""


def compact(db, session_id: int, client, *, force: bool = False) -> bool:
    """창 밖으로 밀려난 대화를 요약에 접는다(답이 끝난 뒤 부른다). 고쳤으면 True."""
    limit = llm_budget.work_len(client)
    msgs = [m for m in db.list_chats(session_id, limit=400) if m.get("role") in ("user", "assistant")]
    s = load(db, session_id)
    upto = int(s.get("upto") or 0)
    fresh = [m for m in msgs if int(m["id"]) > upto]
    # 한 번에 접는 양도 한도 안에서 — 메모가 오래 밀렸으면 오래된 것부터 나눠 접는다(남은 것은 다음 답 뒤에)
    budget = int(limit * 0.5)
    taken, used = [], 0
    for m in fresh:
        t = _tokens(m)
        if taken and used + t > budget:
            break
        taken.append(m)
        used += t
    fresh = taken
    if not fresh or (len(fresh) < MIN_FOLD and not force):
        return False
    cap_tokens = max(200, int(limit * SUMMARY_SHARE))
    cap_chars = cap_tokens * 2                               # 한국어 어림(글자 ≈ 토큰의 2배 안쪽)
    convo = "\n".join(f"[{'담당자' if m['role'] == 'user' else 'AURA'}] {str(m.get('content') or '')[:MSG_CAP]}" for m in fresh)
    messages = [{"role": "system", "content": SUMMARY_PROMPT.format(limit=cap_chars, final=FINAL_PROMPT if goal_lock() else "")},
                {"role": "user", "content": f"[지난 작업 메모]\n{s.get('text') or '(없음 — 대화 처음)'}\n\n[대화]\n{convo}"}]
    try:
        resp = llm_budget.create_fitted(client, messages, cap_tokens + 64, [llm_budget.Cut(msg=1, text=convo)], floor=200,
                                        temperature=0.1, extra_body=getattr(client, "_extra", {}))
        text = (resp.choices[0].message.content or "").strip()
    except Exception:
        return False
    if not text:
        return False
    if goal_lock():
        text = keep_final(s.get("text") or "", text, [str(m.get("content") or "") for m in fresh if m.get("role") == "user"])
    try:                                                     # 메모 갱신도 화면의 호출 목록에(배경 호출이라 답의 기록에 덧붙인다)
        u = json.loads(db.get_setting(f"chat_ctx:{int(session_id)}", "") or "{}")
        if isinstance(u, dict):
            n_in = llm_budget.estimate_messages(messages)
            u.setdefault("items", []).append({"label": "작업 메모 갱신", "group": "메모", "in": n_in, "out": cap_tokens + 64,
                                              "limit": limit, "trimmed": False})
            db.set_setting(f"chat_ctx:{int(session_id)}", json.dumps(u, ensure_ascii=False))
    except (ValueError, TypeError):
        pass
    db.set_setting(f"chat_summary:{int(session_id)}", json.dumps(
        {"text": text[:cap_chars + 200], "upto": int(fresh[-1]["id"]), "n": int(s.get("n") or 0) + len(fresh), "at": time.time()},
        ensure_ascii=False))
    return True


HEADS = ("최종 결과물", "지금 목표", "확정된 것", "진행 상황", "남은 일·물어볼 것")
FINAL = "최종 결과물"
# 담당자가 결과물을 바꾸자는 말 — 일반 낱말(바꾸·변경·대신·말고·다른 문서·새 문서). 이 말이 없으면 최종 결과물 줄은 다듬기만 받는다
_CHANGE = re.compile(r"바꾸|바꿔|변경|대신|말고|다른\s*(?:문서|양식|사업|보고서|계획서)|새\s*(?:문서|양식)|그만")
KEEP_OVERLAP = 0.5           # 새 줄이 이전 줄의 낱말을 이만큼 품으면 다듬은 것(구체화)으로 받는다
# 아직 정해지지 않은 줄(미정·확인 필요 같은 빈자리 표시) — 지킬 결과물이 아직 없으니 구체화한 새 줄을 받는다
_UNSETTLED = re.compile(r"미정|미확정|확인\s*필요|정해지지|정하지\s*않|모름|불명|추후")


def goal_lock() -> bool:
    """최종 결과물 줄 지키기(코드 관문) — ZZAIMY_MEMO_GOAL_LOCK=0 이면 끈다."""
    return os.environ.get("ZZAIMY_MEMO_GOAL_LOCK", "1").strip().lower() not in ("0", "off", "false", "no")


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", text or "")}


def keep_final(old_text: str, new_text: str, user_msgs: list[str]) -> str:
    """갱신한 메모의 「최종 결과물」 줄 관문 — 담당자가 바꾸라고 하지 않았는데 그 줄이 사라지거나 이전 줄과 낱말이 거의 겹치지 않으면
    (지금 단계 이름으로 바뀐 경우 등) 이전 줄을 그대로 둔다. 이전 줄이 없거나, 담당자가 바꾸자고 했거나, 새 줄이 이전 줄을 품고 더 분명해졌으면 새 것."""
    old = parts(old_text).get(FINAL, "")
    if not old:
        return new_text
    new = parts(new_text).get(FINAL, "")
    if any(_CHANGE.search(u or "") for u in user_msgs):
        return new_text if new else _set_part(new_text, FINAL, old)
    if new and _UNSETTLED.search(old) and len(_words(new) - _words(old)) >= 2:
        return new_text                                    # 이전 줄이 빈자리 표시뿐이면 새 낱말이 든 줄로 구체화를 받는다
    ow = _words(old)
    if new and ow and len(ow & _words(new)) >= KEEP_OVERLAP * len(ow):
        return new_text
    return _set_part(new_text, FINAL, old)


def _set_part(text: str, head: str, body: str) -> str:
    """메모 글에서 한 갈래의 글을 바꾸거나(없으면 맨 앞에) 넣는다 — 다른 갈래는 그대로."""
    p = parts(text)
    p[head] = body
    return "\n".join(f"{h}:\n{p[h]}" for h in HEADS if p.get(h)) + "".join(
        f"\n{k}:\n{v}" for k, v in p.items() if k not in HEADS)


def parts(text: str) -> dict[str, str]:
    """작업 메모 글 → 갈래별 글 {지금 목표, 확정된 것, 진행 상황, 남은 일·물어볼 것}. 제목 줄이 없으면 통째로 「지금 목표」."""
    out: dict[str, list[str]] = {}
    cur = ""
    for ln in (text or "").splitlines():
        head = ln.strip().rstrip(":：").strip()
        hit = next((h for h in HEADS if head == h or head.startswith(h)), None) if ln.strip().endswith((":", "：")) or head in HEADS else None
        if hit:
            cur = hit
            out.setdefault(cur, [])
            continue
        if ln.strip():
            out.setdefault(cur or "지금 목표", []).append(ln.rstrip())
    return {k: "\n".join(v).strip() for k, v in out.items() if "\n".join(v).strip()}


def block(summary: str) -> str:
    """요청에 넣는 작업 메모 — 맥락일 뿐 근거가 아님을 함께 적는다."""
    return ("[이 대화의 작업 메모 — 지금까지의 목표·결정·진행·남은 일. 맥락일 뿐 근거가 아니다. 수치·날짜는 근거 자료에서 다시 확인]\n"
            + summary) if summary else ""


def compact_later(db, session_id: int) -> None:
    """답을 보낸 뒤 배경에서 메모를 고친다(답이 늦어지지 않게). 배경 작업용 연결(review)을 쓴다."""
    import threading

    def run():
        try:
            from zzaimy.generate.client import VllmClient
            compact(db, session_id, VllmClient(role="review"))
        except Exception:
            pass
    threading.Thread(target=run, daemon=True, name=f"chat-memo-{session_id}").start()
