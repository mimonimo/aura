"""에이전트 채팅 응답기 — 기준 문서 저장소를 근거로 질문에 답한다.

담당자가 "휴학 처리 기준이 뭐지" 같은 질문을 하면 등록된 규정·지침에서
관련 조각을 찾아 인용하며 답한다. 근거가 없으면 없다고 말한다.
"""

from __future__ import annotations

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
- 근거가 없는 내용은 추측하지 않습니다. 근거가 없으면 정중하게 그 사실을 알리고,
  어떤 규정·기준 문서를 등록하면 도움이 될지 한 문장으로 안내합니다.
- 자연스러운 존댓말로, 필요한 만큼만 간결하게 답합니다. 같은 문장을 반복하지 않습니다.
- 최종 판단은 담당자의 몫이라는 전제를 지킵니다."""


def compose_system(profile: dict) -> str:
    """기본 시스템 프롬프트에 담당자 프로필·지침을 얹는다 (설정 화면에서 저장)."""
    parts = [_SYSTEM]
    call_me = (profile.get("call_me") or "").strip()
    dept = (profile.get("dept") or "").strip()
    if call_me or dept:
        who = []
        if call_me:
            who.append(f'담당자를 "{call_me}"(으)로 부릅니다')
        if dept:
            who.append(f"담당자는 {dept} 소속입니다 — 그 부서 업무 맥락을 우선 고려합니다")
        parts.append("담당자 정보:\n- " + "\n- ".join(who))
    instructions = (profile.get("instructions") or "").strip()
    if instructions:
        parts.append(f"담당자가 등록한 지침 — 답변과 검토 의견 작성 시 따릅니다:\n{instructions}")
    return "\n\n".join(parts)


class AgentResponder:
    def answer(
        self,
        db: Database,
        question: str,
        attachment_text: str | None = None,
        criteria_ids: list[int] | None = None,
        session_id: int | None = None,
        project: dict | None = None,
    ) -> str:
        from zzaimy.generate.client import VllmClient

        if criteria_ids:
            # 담당자가 기준을 직접 고른 경우 — 그 기준의 조각들을 우선 사용
            chunks = db.chunks_for_docs(criteria_ids)
            budget, parts = 6000, []
            for c in chunks:
                piece = f"《{c['reg_title']} · {c['heading']}》\n{c['content'][:800]}"
                if budget - len(piece) < 0:
                    break
                budget -= len(piece)
                parts.append(piece)
            context = "[선택된 기준 문서 — 이 기준으로 판단하고 인용하라]\n\n" + "\n\n".join(parts)
        else:
            context = compose_review_context(db, attachment_text or question)
        history = db.list_chats(session_id, limit=6) if session_id else []
        system = compose_system(db.all_settings())
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
        messages: list[dict] = [{"role": "system", "content": system}]
        for m in history:
            messages.append({"role": m["role"], "content": m["content"][:2000]})
        user_content = question
        if attachment_text:
            user_content += f"\n\n[첨부 문서 본문 — 개인정보 마스킹됨]\n{attachment_text[:8000]}"
        if context:
            user_content += f"\n\n{context}"
        messages.append({"role": "user", "content": user_content})

        client = VllmClient()
        resp = client.client.chat.completions.create(
            model=client.model,
            messages=messages,
            temperature=0.2,
            max_tokens=1024,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        return (resp.choices[0].message.content or "").strip()
