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


# 검색이 기준을 넘는 근거를 하나도 찾지 못했을 때 프롬프트에 넣는 지시.
# 유사도·재랭킹 하한을 도입하면서 "근거 없음"이 정상 결과가 됐다 — 그때 관련 없는
# 조각을 끌어다 붙이면 담당자가 근거 없는 답을 근거 있는 답으로 오인한다.
NO_EVIDENCE_NOTE = (
    "[참고 자료 없음 — 등록된 기준 문서와 국고 공고에서 이 질문과 관련 있는 근거를"
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


def _cite(hit: dict) -> str:
    """근거 블록 한 덩어리 — 표제가 없으면 문서명만 쓴다(빈 《 · 》를 만들지 않는다)."""
    title = hit.get("reg_title") or "문서"
    heading = (hit.get("heading") or "").strip()
    head = f"《{title} · {heading}》" if heading else f"《{title}》"
    return f"{head}\n{(hit.get('content') or '')[:600]}"


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
    def _corpus_hits(self, query: str, top_k: int = 5) -> list:
        """국고 공고 코퍼스(corpus_pilot.db) 하이브리드 검색 — 없으면 빈 리스트.

        채팅 근거를 교내 규정에만 한정하지 않고, 국고사업 공고·요건까지 함께
        찾도록 교차한다(코퍼스가 없으면 조용히 건너뛴다)."""
        try:
            from pathlib import Path

            from zzaimy.app.corpus_search import corpus_hybrid_search

            from zzaimy.app import paths as _paths

            p = _paths.corpus_db_existing(Path(db.path).parent)
            if not p.exists():
                return []
            cdb = Database(str(p))
            return corpus_hybrid_search(cdb, query, top_k=top_k) or []
        except Exception:
            return []

    def answer(
        self,
        db: Database,
        question: str,
        attachment_text: str | None = None,
        criteria_ids: list[int] | None = None,
        session_id: int | None = None,
        project: dict | None = None,
        scope: dict | None = None,
    ) -> str:
        from zzaimy.generate.client import VllmClient
        from zzaimy.app.regulations import find_relevant

        # 범위(부서·역할)는 검색 단계에서 자른다 — 범위 밖 조각은 모델에게 건네지지 않는다(절대 규칙 4)
        scope = scope or {}

        self.last_sources = []
        corpus_hits: list = []
        if criteria_ids:
            # 담당자가 기준을 직접 고른 경우 — 그 기준의 조각들만 사용(범위 고정)
            chunks = db.chunks_for_docs(criteria_ids)
            budget, parts = 6000, []
            for c in chunks:
                piece = _cite(c)
                if budget - len(piece) < 0:
                    break
                budget -= len(piece)
                parts.append(piece)
            context = "[선택된 기준 문서 — 이 기준으로 판단하고 인용하라]\n\n" + "\n\n".join(parts)
            hits = chunks[:8]
        else:
            # 교내 규정(platform) + 국고 공고 코퍼스(corpus_pilot) 교차 검색
            hits = find_relevant(db, attachment_text or question, dept=scope.get("dept"), sector=scope.get("sector"),
                                 user=scope.get("user"), levels=scope.get("levels"))
            corpus_hits = self._corpus_hits(attachment_text or question, top_k=5)
            blocks = []
            if corpus_hits:
                blocks.append(
                    "[국고 공고·사업 문서 — 최신 요건·배점의 근거로 인용하라]\n"
                    + "\n\n".join(_cite(h) for h in corpus_hits))
            if hits:
                blocks.append(
                    "[교내 규정 — 관련 조항을 근거로 인용하라]\n"
                    + "\n\n".join(_cite(h) for h in hits))
            if not blocks:
                # 기준 미달이라 근거가 하나도 남지 않은 경우. 있는 척하지 않는다.
                blocks.append(NO_EVIDENCE_NOTE)
            elif all(h.get("weak_evidence") for h in (corpus_hits or []) + (hits or [])):
                # 하한을 넘지 못해 1위만 남긴 경우 — 약하다는 사실을 함께 알린다
                blocks.append(WEAK_EVIDENCE_NOTE)
            context = "\n\n".join(blocks)

        # 근거(연관 자료) — 국고 공고 우선, 그다음 교내 규정. LLM 성공/실패와 무관하게 저장.
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

        self.last_sources = (
            [_mk(h, "국고 공고", False) for h in corpus_hits[:5]]
            + [_mk(h, "교내 규정", True) for h in (hits or [])[:4]]
        )[:8]
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

        client = VllmClient(role="answer")
        resp = client.client.chat.completions.create(
            model=client.model,
            messages=messages,
            temperature=0.2,
            max_tokens=1024,
            extra_body=getattr(client, "_extra", {}),
        )
        return (resp.choices[0].message.content or "").strip()
