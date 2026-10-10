"""에이전트 채팅 응답기 — 기준 문서 저장소를 근거로 질문에 답한다.

담당자가 "휴학 처리 기준이 뭐지" 같은 질문을 하면 등록된 규정·지침에서
관련 조각을 찾아 인용하며 답한다. 근거가 없으면 없다고 말한다.
"""

from __future__ import annotations

import os
import re

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
- 근거가 없는 내용은 추측하지 않습니다. 근거가 없으면 정중하게 그 사실을 알리고,
  어떤 규정·기준 문서를 등록하면 도움이 될지 한 문장으로 안내합니다.
- 자연스러운 존댓말로, 필요한 만큼만 간결하게 답합니다. 같은 문장을 반복하지 않습니다.
- 최종 판단은 담당자의 몫이라는 전제를 지킵니다."""


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


_FULLWIDTH = {c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}
_FULLWIDTH.update({0x3000: 0x20})


def normalize_input(text: str) -> str:
    """입력 정리 — 전각 영문·숫자·기호(ＡＩＤ, ＩＣＴ)를 반각으로, 「。」「、」를 마침표·쉼표로. 한글은 그대로.
    일부 입력기가 전각으로 보내면 검색 낱말(AID)이 문서와 맞지 않는다."""
    t = (text or "").translate(_FULLWIDTH)
    return t.replace("。", ". ").replace("、", ", ").replace("  ", " ")


_HAN = re.compile(r"[\u4e00-\u9fff]{2,}")


def fix_foreign_han(answer: str, given: str, client) -> str:
    """답에 섞인 중국어 낱말(「인재缺口」)을 한국어로 — 건넨 글(질문·근거)에 없는 한자 줄만 골라 그 줄만 다시 쓰게 한다."""
    lines = answer.splitlines()
    bad = [i for i, ln in enumerate(lines) if any(w not in given for w in _HAN.findall(ln))][:12]
    if not bad:
        return answer
    try:
        numbered = "\n".join(f"{k}|{lines[i]}" for k, i in enumerate(bad))
        resp = client.client.chat.completions.create(
            model=client.model, temperature=0.0, max_tokens=900, extra_body=getattr(client, "_extra", {}),
            messages=[{"role": "user", "content": (
                "아래 줄들에 섞인 중국어 낱말을 뜻이 같은 한국어로 바꾸세요. 다른 글자·기호·서식은 그대로 두고, "
                "「번호|줄」 꼴 그대로 줄마다 하나씩만 돌려주세요.\n\n" + numbered)}])
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
    # 지원 규모·기한이 빠졌다, 실측 2026-09-24 대화 20)
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
        scope: dict | None = None,
        on_progress=None,
    ) -> str:
        from zzaimy.generate.client import VllmClient
        from zzaimy.app.regulations import find_relevant

        # 범위(부서·역할)는 검색 단계에서 자른다 — 범위 밖 조각은 모델에게 건네지지 않는다(절대 규칙 4)
        scope = scope or {}
        question = normalize_input(question)

        self.last_sources = []
        if on_progress:
            on_progress("관련 근거 검색 중")
        if criteria_ids:
            # 담당자가 기준을 직접 고른 경우 — 그 기준의 조각들만 사용(범위 고정). 예전에는 문서 순서대로 앞 6000자를 잘라
            # 넣어 기준 문서가 여럿이면 첫 문서(기본계획 48조각)만 들어가고 공고의 신청 기한은 빠졌다(실측 2026-09-24, 대화 19).
            # 질문과의 관련도로 고르고, 기준 문서마다 적어도 한 조각은 들어가게 한다.
            chunks = db.chunks_for_docs(criteria_ids)
            ordered = rank_criteria_chunks(db, question, chunks, criteria_ids)
            context, hits = pack_criteria_context(ordered)
        else:
            # 현재 등록 문서만 검색한다. 폐기한 공개 코퍼스는 조회하지 않는다.
            hits = find_relevant(db, attachment_text or question, dept=scope.get("dept"), sector=scope.get("sector"),
                                 user=scope.get("user"), levels=scope.get("levels"))
            blocks = []
            if hits:
                blocks.append(
                    "[교내 규정 — 관련 조항을 근거로 인용하라]\n"
                    + "\n\n".join(_cite(h) for h in hits))
            # 사업 문서 계열(국고 계획서·실적보고서·평가 …) — 규정과 따로 찾고 따로 건넨다(절대 규칙 6, ADR-0049).
            # 그래프로 사업·연차를 먼저 좁힌 뒤 그 문서들에서 찾는다. 문서함·업로드·DGX 원본 어디서 들어왔든 같은 색인
            grant_hits = []
            try:
                if scope.get("role") == "student" or scope.get("grant") is False:
                    raise LookupError("이 RAG 공간은 사업 문서를 쓰지 않는다(ADR-0052·0053)")
                from zzaimy.app import grant_search
                prefer = None
                own: set[int] = set()
                if project and project.get("id"):
                    # 이 프로젝트에 올린 문서(공고·기본계획·작성 서식 등)가 먼저다 — 참조 보관 사업만 앞세우고 제 문서는
                    # 일반 검색에 맡겨, 「공고문으로 개요·목차를 잡아 줘」에 다른 사업 계획서가 근거로 서던 일(10/10 대화 23)
                    with db._conn() as conn:
                        own = {int(r[0]) for r in conn.execute(
                            "SELECT id FROM documents WHERE project_id = ?", (int(project["id"]),)).fetchall()}
                    # 프로젝트에 참조로 붙은 과거 사업 보관 묶음의 문서를 먼저 본다(C-192 흐름) — 없거나 맞는 조각이 없으면 평소대로
                    from zzaimy.app import project_refs
                    ref_ids = [r["ref_project_id"] for r in project_refs.list_refs(db, int(project["id"]))]
                    if ref_ids:
                        with db._conn() as conn:
                            prefer = {int(r[0]) for r in conn.execute(
                                f"SELECT id FROM documents WHERE project_id IN ({','.join('?' * len(ref_ids))})", ref_ids).fetchall()}
                g = grant_search.search(db, attachment_text or question, k=5, user=scope.get("user"), prefer_docs=prefer,
                                        depts=scope.get("grant_depts"))
                grant_hits = g["hits"]
                if own:
                    mine = grant_search.search(db, attachment_text or question, k=5, user=scope.get("user"), prefer_docs=own,
                                               depts=scope.get("grant_depts"))["hits"]
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
            if grant_hits and hits and all(h.get("weak_evidence") for h in hits) and blocks and blocks[0].startswith("[교내 규정"):
                # 사업 문서 근거가 있는데 규정 근거는 하한을 못 넘은 것뿐이면 규정 묶음은 빼고 사업 문서만 — 관계없는 규정
                # (「2026학년도 AID 기본계획」)이 LINC+ 2017년 질문의 답에 끼어들던 일(10/5 RAG 실측)
                blocks = blocks[1:]
                hits = []
            if not blocks:
                # 기준 미달이라 근거가 하나도 남지 않은 경우. 있는 척하지 않는다.
                blocks.append(NO_EVIDENCE_NOTE)
            elif hits and all(h.get("weak_evidence") for h in hits):
                # 하한을 넘지 못해 1위만 남긴 경우 — 약하다는 사실을 함께 알린다
                blocks.append(WEAK_EVIDENCE_NOTE)
            context = "\n\n".join(blocks)

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
        if on_progress:
            on_progress(f"답변에 사용할 근거 {len(hits or [])}개 구성"
                        if hits else "관련 근거 없음 · 확인 가능한 범위로 답변 준비")
        history = db.list_chats(session_id, limit=6) if session_id else []
        prof = db.profile_for(scope.get("user")) if hasattr(db, "profile_for") else db.all_settings()
        if scope.get("dept"):
            prof = dict(prof, dept=scope["dept"])           # 부서는 계정에 정해진 값(RAG 공간과 같은 것)
        system = compose_system(prof)
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
        self.last_context = user_content                    # 모델에 실제로 건넨 글(실측이 답의 수치를 이것과 대조한다)

        client = VllmClient(role="answer")
        if on_progress:
            on_progress("답변 작성 중")
        # 목차·개요처럼 긴 답이 1,024 토큰에서 끊겼다(10/10 대화 23 — 「Ⅳ.」 제목에서 멈춤). 상한을 넉넉히, 그래도 끊기면 한 번 잇는다
        limit = int(os.environ.get("ZZAIMY_ANSWER_MAX_TOKENS", "3072"))
        resp = client.client.chat.completions.create(
            model=client.model,
            messages=messages,
            temperature=0.2,
            max_tokens=limit,
            extra_body=getattr(client, "_extra", {}),
        )
        text = resp.choices[0].message.content or ""
        if getattr(resp.choices[0], "finish_reason", "") == "length" and text:
            if on_progress:
                on_progress("답변 이어 쓰는 중")
            try:
                more = client.client.chat.completions.create(
                    model=client.model, temperature=0.2, max_tokens=limit, extra_body=getattr(client, "_extra", {}),
                    messages=messages + [{"role": "assistant", "content": text},
                                         {"role": "user", "content": "끊긴 곳 바로 뒤부터 이어서 끝까지 쓰세요. 앞 내용은 되풀이하지 마세요."}])
                text = text.rstrip() + "\n" + (more.choices[0].message.content or "").lstrip()
            except Exception:
                pass
        return fix_foreign_han(text.strip(), user_content, client)
