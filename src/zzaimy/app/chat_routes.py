"""에이전트 대화(/chat*) — 대화 화면(새 대화·이어 보기·동료 대화 읽기), 보내기(/chat/send)와 옆 패널 질문(/chat/ask),
진행 상태(/chat/{id}/status)·내용 JSON(/chat/{id}/messages), 답 평가·다시 받기·비공개·지우기·프로젝트 넣기,
도우미 제안 실행(/chat/{id}/proposals/{pid}/run), 문서 훑어보기(/chat/peek/{doc_id}).

- 대화 고쳐 묻기·판본(/chat/{id}/revisions·…/edit)은 app/chat_revisions, 대화 기록 목록(/api/chat/sessions*)은
  app/chat_history, 대화 문서함(/api/chat/…·/api/chat-documents/…)은 main·app/chat_documents 에 그대로 있다.
- DB·화면 틀·화면 공통 맥락(ctx)과 답 만들기(배경 작업 _schedule_answer·진행 표 _chat_running/_chat_progress·근거 표
  _chat_sources·화면 경로 _chat_pages), 문서 작업 도우미(_intake_bundle·_set_options·_remember_given 등)는 main.create_app 이
  앱마다 만들어 대화 묶음(app.state.chat — make_chat_helpers)에 넘긴다. 진행 표·근거 표는 main 의 답 만들기와 같은 객체를 함께 쓴다.
- 대화에서만 쓰는 도우미(열람 범위 한 줄·맞는 기준·첨부 판독·플랫폼 업무 도우미·말에서 문서 찾기·바로 하는 일)는
  make_chat_helpers 안에 있다 — 앱마다 하나라 시험 앱끼리 상태를 나누지 않는다.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from zzaimy.app import access_policy as _ap
from zzaimy.app import korean_text as _ko
from zzaimy.app import platform_guide, platform_ops, storage
from zzaimy.app.chat_history import display_title as _chat_title
from zzaimy.generate import llm_connections

router = APIRouter()


FEEDBACK_REASONS = ("근거 문서가 틀림", "숫자·사실이 틀림", "질문과 다른 답", "너무 길거나 짧음", "느림", "기타")


async def _form_fields(request: Request) -> dict:
    """선택지 입력 양식(kind=form)의 칸 값 — field_<이름> 폼 필드와 template 를 모아 문장을 만든다(자바스크립트 없이도 됨)."""
    try:
        form = await request.form()
    except Exception:
        return {}
    vals = {k[6:]: str(v).strip() for k, v in form.multi_items() if k.startswith("field_") and not hasattr(v, "filename")}
    later = [str(v).strip() for k, v in form.multi_items() if k == "later" and not hasattr(v, "filename") and str(v).strip()]
    if not vals and not later:
        return {}
    return {"values": vals, "template": str(form.get("template") or ""), "later": later}


def _strip_attach_prefix(text: str) -> str:
    """저장된 사용자 메시지에서 첨부 표시줄을 떼고 질문만 남긴다."""
    lines = (text or "").split("\n")
    while len(lines) > 1 and (lines[0].startswith("[첨부") or lines[0].startswith("[실험 · ")):
        lines.pop(0)
    return "\n".join(lines)


PROJECT_GRADE_NOTE = "프로젝트는 사업 책임자 이상이 만듭니다"


def make_chat_helpers(
    *,
    db, db_path, processor, drafter, templates, ctx,
    accounts, password, chat_topics, chat_revisions, _chat_running, _chat_progress,
    _chat_sources, _chat_pages, _RETRIEVAL_NOTE, _owned_chat, _can_read_chat, _reader_doc_ok, _criteria_docs,
    _viewer_sees, _extra_depts, _seen_depts, _local_back, _share_sync, _chat_suggestions, _intake_bundle,
    _link_past_materials, _link_past_projects, _remember_given, _schedule_answer, _set_options, _title_from_bundle,
    _link_criteria_kind, _all_depts, _chat_step, _scrub_internal, _session_viewer, _view_as,
    _auto_route, _identify_document, _can_edit_doc,
) -> SimpleNamespace:
    """앱마다 하나 — main.create_app 이 넘긴 값과 대화 전용 도우미를 한 묶음(app.state.chat)으로."""
    deps = dict(locals())
    from zzaimy.app.main import INBOX_TYPES   # 업무 갈래 — 정본은 main(같은 표)

    def _scope_label(request: Request) -> str:
        """대화 화면 상단의 열람 범위 한 줄 — 검색이 실제로 거르는 범위(_doc_access_sql)와 같은 말로. 역할 이름은 사용자 메뉴에 있다."""
        role = getattr(request.state, "role", "")
        dept = getattr(request.state, "dept", "") or ""
        if role == "student":
            return "검색 범위: 학생 공개 규정"
        if role == "dev":
            return "검색 범위: 모든 문서(관리자)"
        extra = [d for d in _seen_depts(getattr(request.state, "user", "")) if d != dept]
        mine = [d for d in [dept, *extra] if d and d != "공통"]
        return "검색 범위: 전체 공개 문서" + (" + " + "·".join(mine) + " 문서" if mine else "")

    def _recommended_criteria(session_id: int | None, messages: list) -> list[int]:
        """이 대화에 맞는 기준 — 프로젝트에 연결된 것, 없으면 오간 말과 겹치는 것.

        전부 늘어놓으면 고를 수 없다. 맞는 것이 먼저 와야 한다.
        """
        import re as _re

        if session_id is not None:
            session = db.get_chat_session(session_id)
            if session and session.get("project_id"):
                ids = db.get_project_criteria_ids(int(session["project_id"]))
                if ids:
                    return list(ids)
        said = " ".join(m.get("content") or "" for m in (messages or [])[-6:])
        words = {w for w in _re.findall(r"[0-9A-Za-z가-힣]{2,}", said)}
        if not words:
            return []
        scored: list[tuple[int, int]] = []
        for d in _criteria_docs():
            if session_id is not None and not _viewer_sees(session_id, d):
                continue
            hay = set(_re.findall(r"[0-9A-Za-z가-힣]{2,}", d.get("filename") or ""))
            n = len(words & hay)
            if n:
                scored.append((n, d["id"]))
        scored.sort(reverse=True)
        return [i for _, i in scored[:6]]

    def _process_attachment(doc_id: int, stored: Path) -> None:
        """대화 첨부 — 판독·색인까지만 기다리고 검토 의견(27B ~3분)은 뒤로 미룬다. 답변은 판독이 끝나는 대로 이어서 시작된다."""
        import inspect as _ins
        if "defer_review" in _ins.signature(processor.process).parameters:
            processor.process(db, doc_id, stored, defer_review=True)
        else:
            processor.process(db, doc_id, stored)
        _link_criteria_kind(db, doc_id)

    def _may_create_project(user: str, role: str) -> bool:
        """새 프로젝트를 만들 수 있는 계정인가 — 사업 책임자 이상(grades.can_create_project). 프로젝트 화면의 만들기 단추와 같은 판정."""
        from zzaimy.app import grades
        acc = dict((accounts or {}).get(user or "") or {})
        acc.setdefault("role", role or "")
        return grades.can_create_project(acc)

    def _ops_ctx(user: str, role: str, background: BackgroundTasks | None = None) -> "platform_ops.Ctx":
        """플랫폼 동작에 넘기는 맥락 — background 가 있으면(실행 때) 새 에이전트 대화를 열고 첫 요청의 답을 뒤에서 만든다."""
        def start_chat(project_id: int | None, question: str) -> int:
            sid_ = db.create_chat_session(title=_chat_title(question)[:60], project_id=project_id, owner=user or "zzaimy")
            db.add_chat(sid_, "user", question)
            _schedule_answer(background, sid_, question, None, [])
            return sid_
        return platform_ops.Ctx(db=db, data_dir=Path(db_path).parent, user=user or "", role=_ap.role_class(role), depts=_all_depts,
                                sectors=dict(INBOX_TYPES), start_chat=start_chat if background is not None else None,
                                may_create_project=_may_create_project(user, role))

    def _helper_search(owner: str, role: str, dept: str | None):
        """도우미의 문서 찾기 — 에이전트 대화와 같은 열람 범위(search_scope): 사업 문서(grant_search)와 규정(find_relevant)."""
        from zzaimy.app import access_guard as ag, grant_search
        from zzaimy.app.regulations import find_relevant

        from zzaimy.app import grades as _gr
        scope = ag.search_scope(dept, role, "" if _view_as(owner) else owner, Path(db_path).parent,
                                grade=_gr.user_grade(accounts.get(owner), _view_as(owner)))

        def search(query: str) -> list[dict]:
            out: list[dict] = []
            try:
                for h in find_relevant(db, query, top_k=2, **ag.reg_kwargs(scope)):
                    out.append({"title": " · ".join(x for x in (h.get("reg_title") or "규정", h.get("heading") or "") if x),
                                "text": h.get("content") or "", "doc_id": h.get("doc_id")})
            except Exception:
                logging.getLogger("zzaimy.app.helper").warning("도우미 규정 찾기 실패", exc_info=True)
            if role != "student" and scope.get("grant") is not False:
                try:
                    for h in grant_search.search(db, query, k=3, user=scope.get("user"), depts=scope.get("grant_depts"))["hits"]:
                        out.append({"title": h.get("filename") or "문서", "text": h.get("content") or "", "doc_id": h.get("doc_id")})
                except Exception:
                    logging.getLogger("zzaimy.app.helper").warning("도우미 문서 찾기 실패", exc_info=True)
            return out[:4]
        return search

    _helper_runs: dict[int, object] = {}       # 대화마다 지금 도는 도우미 요청의 표 — 「멈춤」 이 표를 거두면 그 요청의 남은 걸음·답은 버린다

    class _Stopped(Exception):
        pass

    def _helper_stop(session_id: int) -> bool:
        """도는 도우미 요청을 멈춘다 — 다음 걸음부터 하지 않고 늦게 온 답·제안은 버린다. 대화는 바로 다음 질문을 받는다."""
        if _helper_runs.pop(session_id, None) is None:
            return False
        _chat_running.discard(session_id)
        _chat_progress.pop(session_id, None)
        platform_ops.expire(db, session_id)
        db.add_chat(session_id, "assistant", "멈췄습니다.")
        return True

    def _helper_task(session_id: int, q: str, page: str, run_id: object = None, said: str | None = None, controls: list | None = None) -> None:
        """플랫폼 업무 도우미(app/platform_helper) — 옆 패널의 질문. 모델에 닿지 못하면 안내 자료에서 찾은 화면을 그대로 보인다.
        said 는 담당자가 친 글 그대로 — 커서가 마지막 단추(저장·지우기 등)를 누를지는 이 글로만 정한다."""
        from zzaimy.app import platform_helper
        from zzaimy.generate.client import VllmClient

        if run_id is None:
            run_id = object()
            _helper_runs[session_id] = run_id

        def live() -> bool:
            return _helper_runs.get(session_id) is run_id

        def say(text: str) -> None:
            if live():
                db.add_chat(session_id, "assistant", _scrub_internal(text))

        def step(label: str) -> None:
            if not live():
                raise _Stopped()
            _chat_step(session_id, label)

        try:
            owner, role, dept = _session_viewer(session_id)
            sess = db.get_chat_session(session_id) or {}
            project = db.get_project(int(sess["project_id"])) if sess.get("project_id") else None
            before = len(db.list_chats(session_id))
            env = platform_helper.Env(db=db, data_dir=Path(db_path).parent, session_id=session_id, user=owner, role=_ap.role_class(role), dept=dept,
                                      page=page, ctx=_ops_ctx(owner, role), project=project, controls=controls or [],
                                      say=say, on_step=step,
                                      search=_helper_search(owner, role, dept))
            ep = None
            try:
                ep = platform_helper.run(env, VllmClient(role=platform_helper.model_role()), q, said=q if said is None else said)
            except _Stopped:
                return
            except Exception as e:
                logging.getLogger("zzaimy.app.helper").warning("도우미 실패 (대화 %s): %s", session_id, type(e).__name__)
            if not live() or len(db.list_chats(session_id)) > before:
                return
            if ep is not None and not ep.may_fall_back:
                db.add_chat(session_id, "assistant", _scrub_internal(doc_agent_failure(ep)))
                return
            db.add_chat(session_id, "assistant", "도우미 모델에 닿지 못해 안내 자료에서 찾은 화면만 보입니다.\n\n"
                        + platform_guide.lookup(q, current=page, role=role))
        finally:
            if live():                         # 멈춘 뒤 새 요청이 돌고 있으면 그 진행 표는 건드리지 않는다
                _helper_runs.pop(session_id, None)
                _chat_running.discard(session_id)
                _chat_progress.pop(session_id, None)

    def doc_agent_failure(ep) -> str:
        from zzaimy.app import doc_agent as _da
        return _da.failure_message(ep).replace(" 문서는 바꾸지 않았습니다.", " 아무것도 바꾸지 않았습니다.")

    def _find_document(question: str, viewer: dict | None = None) -> int | None:
        """말 속에서 어느 문서를 가리키는지 찾는다.

        파일 이름과 문서 정체(사업 이름)의 낱말이 얼마나 겹치는지로 고른다.
        으뜸이 버금보다 뚜렷하게 앞설 때만 고른다 — 애매하면 고르지 않는다.
        """
        import json
        import re as _re

        words = {w for w in _re.findall(r"[0-9A-Za-z가-힣]{2,}", question or "")}
        if not words:
            return None
        # 문서마다 따로 묻지 않는다 — 문서가 수만 건이라 질문 하나에 수만 번 DB 를 오가 창이 멈췄다(10-15).
        # 낱말이 이름·정체에 든 문서만 한 번에 가져와(낱말은 긴 것부터 8개) 그 안에서 겹침을 센다
        probe = sorted(words, key=len, reverse=True)[:8]
        cond = " OR ".join(["filename LIKE ?", "COALESCE(identity, '') LIKE ?"] * len(probe))
        args = [f"%{w}%" for w in probe for _ in (0, 1)]
        # 묻는 사람이 볼 수 있는 문서 안에서만(access_policy) — 짚은 문서가 단추·안내로 드러나므로
        from zzaimy.app import access_policy as _ap
        acc, acc_args = _ap.doc_sql(viewer or _ap.viewer("", None, ""), alias="")
        with db._conn() as conn:
            rows = conn.execute(f"SELECT id, filename, identity FROM documents WHERE ({cond}){acc} LIMIT 5000",
                                [*args, *acc_args]).fetchall()
        best, second, best_id = 0, 0, None
        for did, name, ident_raw in rows:
            hay = set(_re.findall(r"[0-9A-Za-z가-힣]{2,}", name or ""))
            try:
                ident = json.loads(ident_raw or "{}")
            except ValueError:
                ident = {}
            if isinstance(ident, dict) and ident.get("program"):
                hay |= set(_re.findall(r"[0-9A-Za-z가-힣]{2,}", str(ident["program"])))
            score = len(words & hay)
            if score > best:
                best, second, best_id = score, best, int(did)
            elif score > second:
                second = score
        return best_id if best >= 2 and best > second else None

    def _run_action(key: str, ctx: dict) -> str:
        """교내에서 끝나고 되돌릴 수 있는 일은 에이전트가 직접 한다.

        한 줄 결과를 돌려준다. 실패해도 대화는 이어져야 하므로 사유만 남긴다.
        """
        try:
            if key == "doc.identity":
                r = _identify_document(db, int(ctx["doc_id"]))
                if not r["ok"]:
                    return f"사업 정보를 읽지 못했습니다 — {r['error']}"
                return "사업 정보를 정리했습니다 — " + " · ".join(r["identity"].values())
            if key == "doc.analyze":
                doc_id = int(ctx["doc_id"])
                if db.get_document(doc_id) is None:
                    return "없는 문서입니다"
                db.update_document(doc_id, coverage="분석 중입니다 (30초~1분)")
                processor.analyze(db, doc_id)
                return "맥락을 다시 분석했습니다"
            if key == "doc.revise":
                doc_id = int(ctx["doc_id"])
                doc = db.get_document(doc_id)
                if doc is None:
                    return "없는 문서입니다"
                want = (ctx.get("question") or "").strip()
                if len(want) < 4:
                    return "어떻게 고칠지 한 문장으로 알려 주십시오"
                if not (doc.get("draft") or "").strip():
                    return "아직 초안이 없습니다. 먼저 초안을 만들어야 합니다"
                db.add_review(doc_id, want)
                db.update_document(doc_id, coverage="초안을 고치는 중입니다")
                drafter.generate(db, doc_id)
                return f"「{doc['filename']}」 초안을 요청대로 고쳤습니다"
            if key == "doc.route":
                doc_id = int(ctx["doc_id"])
                if db.get_document(doc_id) is None:
                    return "없는 문서입니다"
                line = _auto_route(db, doc_id, user_chose_type=False)
                return line or "지금 값이 알맞아 그대로 두었습니다"
            if key == "plan.start":
                if not ctx.get("may_create_project", True):
                    return PROJECT_GRADE_NOTE
                doc_id = int(ctx["doc_id"])
                doc = db.get_document(doc_id)
                if doc is None:
                    return "없는 문서입니다"
                ident = db.get_doc_identity(doc_id)
                if not ident:
                    r = _identify_document(db, doc_id)
                    ident = r.get("identity") or {}
                name = (ident.get("program") or doc["filename"]).strip()[:60]
                sector = doc.get("sector") if doc.get("sector") in INBOX_TYPES else "grant"
                pid = db.create_project(sector, name, owner=str(ctx.get("owner") or "zzaimy"))
                db.set_document_project(doc_id, pid)
                steps = [f"프로젝트 「{name}」를 만들고 공고를 붙였습니다"]
                if ident.get("organizer") or ident.get("period"):
                    bits = [v for v in (ident.get("organizer"), ident.get("period"),
                                        ident.get("scale")) if v]
                    steps.append("공고에서 확인한 것 — " + " · ".join(bits))
                return " / ".join(steps) + f" (프로젝트 {pid})"
            if key == "doc.draft":
                doc_id = int(ctx["doc_id"])
                doc = db.get_document(doc_id)
                if doc is None:
                    return "없는 문서입니다"
                db.update_document(doc_id, coverage="초안 작성 중입니다")
                drafter.generate(db, doc_id)
                fresh = db.get_document(doc_id) or {}
                if not (fresh.get("draft") or "").strip():
                    return f"「{doc['filename']}」 초안을 만들지 못했습니다"
                return (f"「{doc['filename']}」 초안을 만들었습니다."
                        f" 문서 화면에서 확인하십시오.")
            if key == "llm.test":
                from zzaimy.generate import llm_connections as _lcm

                cid = str(ctx.get("cid", ""))
                conn = _lcm.get(cid) if cid else None
                if conn is None:
                    return "확인할 연결을 찾지 못했습니다"
                pr = _lcm.probe(conn)
                _lcm.record_check(cid, pr["ok"],
                                  f"모델 {len(pr['models'])}개" if pr["ok"] else pr["error"])
                return (f"「{conn['name']}」 연결됨 · 모델 {len(pr['models'])}개"
                        if pr["ok"] else f"「{conn['name']}」 연결 실패 — {pr['error']}")
        except Exception as e:                      # 어떤 실패도 대화를 끊지 않는다
            return f"처리하지 못했습니다 ({_ko.reason(e)})"
        return ""

    def _page_actions(page: str, question: str = "", viewer: dict | None = None) -> list[dict]:
        """말과 화면을 보고 지금 할 수 있는 일 — 되돌리기 어려운 일만 단추로 남긴다."""
        from zzaimy.app import actions as _acts

        extra: dict = {}
        active = [c for c in llm_connections.list_public() if c.get("active")]
        if active:
            extra["cid"] = active[0]["id"]
        if "doc_id" not in _acts.context_from_page(page):
            found = _find_document(question, viewer)
            if found is not None:
                extra["doc_id"] = found
        extra["question"] = question          # 고치기 요청은 말 자체가 내용이다
        return _acts.suggest(question, page, extra)

    return SimpleNamespace(**deps,
                           _scope_label=_scope_label, _recommended_criteria=_recommended_criteria, _process_attachment=_process_attachment, _ops_ctx=_ops_ctx,
                           _helper_search=_helper_search, _helper_task=_helper_task, _helper_runs=_helper_runs, _helper_stop=_helper_stop,
                           doc_agent_failure=doc_agent_failure, _find_document=_find_document,
                           _run_action=_run_action, _page_actions=_page_actions, _may_create_project=_may_create_project)


@router.get("/chat", response_class=HTMLResponse)
def chat_new(request: Request, project: int | None = None):
    h = request.app.state.chat
    db, templates, ctx, _criteria_docs = h.db, h.templates, h.ctx, h._criteria_docs
    _scope_label = h._scope_label
    chat_project = db.get_project(project) if project else None
    # 프로젝트 안 새 대화는 담당자와 「함께 작업」으로 공유받은 사람(project_access)
    if project and (not chat_project or request.app.state.project_access(request, chat_project) != "edit"):
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
    return templates.TemplateResponse(
        request,
        "chat_workspace.html",
        ctx(request, {
            "messages": [], "criteria_docs": _criteria_docs(request),
            "waiting": False, "session_id": None, "sources": [],
            "recommended_criteria": db.get_project_criteria_ids(project) if project else [],
            "chat_session": None, "chat_project": chat_project, "chat_topic": "",
            "scope_label": _scope_label(request),
        }),
    )


def _shared_lost(request: Request, session: dict) -> bool:
    """남의 프로젝트 안에서 연 내 대화인데 그 프로젝트의 「함께 작업」 공유가 풀렸는가 — 이어 쓰기는 main._owned_chat 이 막는다."""
    if not session.get("project_id"):
        return False
    proj = request.app.state.chat.db.get_project(int(session["project_id"]))
    return bool(proj) and proj.get("owner") != getattr(request.state, "user", "") \
        and request.app.state.project_access(request, proj) != "edit"


_ATTACH_ID = re.compile(r"\[첨부#(\d+)\]")
HIDDEN_SOURCE = "볼 수 없는 자료"


def reader_evidence(request: Request, sources: list[dict], messages: list[dict]) -> tuple[list[dict], set[int]]:
    """읽기 화면의 근거 — (근거 목록, 열어도 되는 첨부 문서 id). 질문·답 글은 그대로 두고, 출처 문서 링크·원문 조각·첨부는 읽는 사람이
    볼 수 있는 문서(access_policy, 문서 화면과 같은 검사)만 연다. 범위 밖 근거는 이름을 「볼 수 없는 자료」 로 가리고 링크·조각을 뺀다.
    문서가 아닌 근거(웹 출처 등, doc_id 없음)는 그대로."""
    ok = request.app.state.chat._reader_doc_ok
    seen: dict[int, bool] = {}

    def opens(did) -> bool:
        if int(did) not in seen:
            seen[int(did)] = ok(request, did)
        return seen[int(did)]
    out = []
    for s in sources or []:
        did = s.get("doc_id")
        if not did or opens(did):
            out.append({**s, "hidden": False})
        else:
            out.append({"doc_id": None, "title": HIDDEN_SOURCE, "heading": "", "snippet": "", "origin": "", "weak": s.get("weak"),
                        "hidden": True})
    attach = {int(x) for m in messages for x in _ATTACH_ID.findall(m.get("content") or "") if opens(x)}
    return out, attach


@router.get("/chat/{session_id}/read", response_class=HTMLResponse)
def chat_read(request: Request, session_id: int):
    """공유받은 프로젝트의 남의 대화(비공개 제외)를 읽기 전용으로 — 입력줄·문서 패널·다시 쓰기 없이 주고받은 글만.
    근거는 읽는 사람 범위 안의 것만 연다(reader_evidence)."""
    h = request.app.state.chat
    db, templates, ctx, chat_topics = h.db, h.templates, h.ctx, h.chat_topics
    _can_read_chat = h._can_read_chat
    session = db.get_chat_session(session_id)
    if not _can_read_chat(request, session):
        raise HTTPException(404, "대화를 찾을 수 없습니다.")
    project = db.get_project(int(session["project_id"])) if session.get("project_id") else None
    mine = session.get("owner") == getattr(request.state, "user", "")
    messages = db.list_chats(session_id)
    evidence, attach_ok = reader_evidence(request, chat_topics.latest(session_id), messages)
    return templates.TemplateResponse(request, "chat_read.html", ctx(request, {
        "chat_session": session, "chat_project": project, "messages": messages,
        "sources": [s for s in evidence if not s["hidden"]], "evidence": evidence, "attach_ok": attach_ok, "mine": mine,
        "shared_lost": mine and _shared_lost(request, session),
        "project_open": bool(project) and bool(request.app.state.project_access(request, project))}))


@router.post("/chat/{session_id}/messages/{message_id}/feedback")
def answer_feedback(request: Request, session_id: int, message_id: int, verdict: str = Form(""),
                    reasons: list[str] = Form([]), note: str = Form("")):
    """답 평가(베타 수집) — 도움됨·아쉬움과 아쉬운 까닭·한 줄 의견. 대화 주인만, 그 대화의 답에만."""
    h = request.app.state.chat
    db, _owned_chat = h.db, h._owned_chat
    _owned_chat(request, session_id)
    if verdict not in ("", "good", "bad"):
        raise HTTPException(400, "평가 값을 확인해 주세요")
    if not any(int(m["id"]) == int(message_id) and m["role"] == "assistant" for m in db.list_chats(session_id, limit=1000)):
        raise HTTPException(404, "답을 찾을 수 없습니다")
    db.set_answer_feedback(message_id, session_id, getattr(request.state, "user", "") or "", verdict,
                           [x for x in reasons if x in FEEDBACK_REASONS] if verdict == "bad" else [], note.strip()[:500])
    return {"ok": True, "verdict": verdict}


@router.post("/chat/{session_id}/private")
def chat_set_private(request: Request, session_id: int, private: str = Form("1"), back: str = Form("")):
    """대화 비공개 켜고 끄기 — 만든 사람만. 비공개면 같은 부서 동료도 읽지 못한다."""
    h = request.app.state.chat
    db, _owned_chat, _local_back, _share_sync = h.db, h._owned_chat, h._local_back, h._share_sync
    sess = _owned_chat(request, session_id)
    db.set_chat_private(session_id, private == "1")
    if sess.get("project_id"):                       # 비공개로 돌리면 그 대화의 작업 문서 보기 권한도 바로 거둔다
        _share_sync(db.get_project(int(sess["project_id"])), wait=True)
    return RedirectResponse(_local_back(back, f"/chat/{session_id}"), status_code=303)


@router.get("/chat/{session_id}", response_class=HTMLResponse)
def chat_session(request: Request, session_id: int):
    h = request.app.state.chat
    db, templates, ctx, chat_topics = h.db, h.templates, h.ctx, h.chat_topics
    _chat_running, _chat_sources, _owned_chat, _can_read_chat = h._chat_running, h._chat_sources, h._owned_chat, h._can_read_chat
    _criteria_docs, _chat_suggestions, _scope_label, _recommended_criteria = h._criteria_docs, h._chat_suggestions, h._scope_label, h._recommended_criteria
    sess0 = db.get_chat_session(session_id)
    if sess0 and sess0.get("owner") != request.state.user and _can_read_chat(request, sess0):
        return RedirectResponse(f"/chat/{session_id}/read", status_code=303)     # 동료의 대화는 읽기 전용 화면으로
    if sess0 and sess0.get("owner") == request.state.user and _shared_lost(request, sess0):
        return RedirectResponse(f"/chat/{session_id}/read", status_code=303)     # 공유가 풀린 남의 프로젝트 안 내 대화 — 이어 쓰지 못하니 읽기 화면으로
    _owned_chat(request, session_id)
    messages = db.list_chats(session_id)
    waiting = session_id in _chat_running or (bool(messages) and messages[-1]["role"] == "user")
    session = db.get_chat_session(session_id)
    project = (
        db.get_project(session["project_id"])
        if session and session.get("project_id") else None
    )
    latest_question = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
    )
    topic = " ".join(_strip_attach_prefix(latest_question).split())
    return templates.TemplateResponse(
        request,
        "chat_workspace.html",
        ctx(request, {
            "messages": messages, "criteria_docs": _criteria_docs(request),
            "waiting": waiting, "session_id": session_id,
            "suggestions": [] if waiting else _chat_suggestions(session, getattr(request.state, "user", "zzaimy")),
            "proposals": [] if waiting else platform_ops.pending(db, session_id),
            "sources": _chat_sources.get(session_id) or chat_topics.latest(session_id),
            "recommended_criteria": _recommended_criteria(session_id, messages),
            "chat_session": session, "chat_project": project, "chat_topic": topic,
            "answer_feedback": db.answer_feedback_for(session_id, getattr(request.state, "user", "") or ""),
            "scope_label": _scope_label(request),
        }),
    )


@router.get("/chat/{session_id}/status")
def chat_status(request: Request, session_id: int):
    h = request.app.state.chat
    db, _chat_running, _chat_progress, _owned_chat = h.db, h._chat_running, h._chat_progress, h._owned_chat
    _owned_chat(request, session_id)
    messages = db.list_chats(session_id)
    prog = _chat_progress.get(session_id)
    return {"waiting": session_id in _chat_running or (bool(messages) and messages[-1]["role"] == "user"),
            "progress": ({**prog, "now": time.time()} if prog else prog)}


@router.post("/chat/send")
def chat_send(
    request: Request,
    background: BackgroundTasks,
    question: str = Form(...),
    form_fields: dict = Depends(_form_fields),
    session_id: int | None = Form(None),
    criteria: list[int] = Form([]),
    attachment: list[UploadFile] = File([]),
    project_id: int | None = Form(None),
    web: str = Form(""),
    retrieval: str = Form(""),
):
    h = request.app.state.chat
    db, db_path, chat_revisions, _chat_running = h.db, h.db_path, h.chat_revisions, h._chat_running
    _RETRIEVAL_NOTE, _owned_chat, _intake_bundle, _link_past_materials = h._RETRIEVAL_NOTE, h._owned_chat, h._intake_bundle, h._link_past_materials
    _link_past_projects, _remember_given, _schedule_answer, _set_options = h._link_past_projects, h._remember_given, h._schedule_answer, h._set_options
    _title_from_bundle, _process_attachment = h._title_from_bundle, h._process_attachment
    from zzaimy.app.main import ALLOWED_EXTENSIONS, INBOX_TYPES
    from zzaimy.app.responder import normalize_input
    from zzaimy.app.grant_search import GRAPH_MODES

    # 검색 방식 실험(풀텍스트 RAG 대조)은 관리자만 — 다른 계정이 보내도 운영 기본으로
    retrieval = retrieval if getattr(request.state, "role", "") == "dev" and retrieval in GRAPH_MODES else ""

    q = normalize_input(question).strip()          # 전각 영문·숫자(ＡＩＤ)·「。、」를 고쳐야 검색어가 맞는다
    if form_fields:
        from zzaimy.app import asks as _asks

        # 비워 둔 칸(과 「나중에」 로 고른 값)은 이번에 묻지 않고 차례의 뒤로 — 같은 칸에 적은 값이 있으면 값이 먼저다
        blank = [k for k, v in (form_fields.get("values") or {}).items() if not v]
        later = [n for n in dict.fromkeys([*(form_fields.get("later") or []), *blank]) if not (form_fields.get("values") or {}).get(n)]
        if later and session_id is not None:
            _owned_chat(request, session_id)
            _asks.defer(db, session_id, later)
        vals = {k: v for k, v in (form_fields.get("values") or {}).items() if v and k not in later}
        composed = _asks.compose(form_fields.get("template") or "; ".join(f"{k}: {{{k}}}" for k in vals), vals) if vals else ""
        asked = {a["name"] for a in _asks.pending(db, session_id)} if session_id is not None else set()
        given = {k: v for k, v in vals.items() if k in asked}
        if given and session_id is not None:
            # 칸에 적어 보낸 값은 물었던 값의 대답 그 자체다 — 자유 글처럼 모델에게 해석을 맡기지 않고 그대로 기억한다
            _owned_chat(request, session_id)
            db.add_chat(session_id, "user", composed or "; ".join(f"{k}: {v}" for k, v in given.items()), mode="document")
            _remember_given(session_id, given)
            return RedirectResponse(f"/chat/{session_id}", status_code=303)
        if composed:
            q = composed
        elif later and session_id is not None:
            return RedirectResponse(f"/chat/{session_id}", status_code=303)   # 미루기만 — 대화로 보낼 값이 없다
        elif form_fields.get("template"):
            return RedirectResponse(f"/chat/{session_id}" if session_id is not None else "/chat", status_code=303)   # 빈 칸만 — 「(값)」 틀을 보내지 않는다
    if not q:
        return RedirectResponse("/chat", status_code=303)
    files = [f for f in (attachment or []) if f is not None and f.filename]
    for f in files:
        if Path(f.filename).suffix.lower() not in ALLOWED_EXTENSIONS:
            raise HTTPException(400, f"허용되지 않는 파일 형식입니다: {Path(f.filename).suffix.lower()}")
    made_project: dict | None = None
    grade_note = False
    if session_id is None:
        title = q
        if project_id and (proj := db.get_project(project_id)):
            if request.app.state.project_access(request, proj) != "edit":
                raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
            title = f"[{proj['name'][:14]}] {q}"
        elif len(files) >= 2 and not h._may_create_project(getattr(request.state, "user", ""), getattr(request.state, "role", "")):
            # 일반 등급은 프로젝트를 만들지 않는다 — 막지 않고 대화 첨부로만 읽는다(안내 한 줄은 대화를 연 뒤에)
            project_id = None
            grade_note = True
        elif len(files) >= 2:
            # 새 대화에 문서 세트를 던지면 프로젝트가 생긴다 — 이름은 묶음에서, 기준/접수로 나눠 들인다
            pname = _title_from_bundle(files)
            pid = db.create_project("grant", pname, owner=getattr(request.state, "user", "zzaimy"))
            made_project = db.get_project(pid) or {"id": pid, "sector": "grant", "name": pname}
            project_id = pid
            title = f"[{pname[:14]}] {q}"
        else:
            project_id = None
        session_id = db.create_chat_session(
            title=title, project_id=project_id,
            owner=getattr(request.state, "user", "zzaimy"),
        )
        if grade_note:
            db.add_chat(session_id, "assistant", PROJECT_GRADE_NOTE + " — 올린 파일은 이 대화의 첨부로 읽습니다.")
    else:
        _owned_chat(request, session_id)
    if made_project is not None:
        made = _intake_bundle(request, background, made_project, files)
        past = _link_past_materials(made_project)
        refs = _link_past_projects(request, made_project)
        lines = [f"[첨부#{d}] {storage.title_of((db.get_document(d) or {}).get('filename') or '')}" for d in [*made["criteria"], *made["intake"]]]
        lines += [f"[참조 보관 사업] {r['name']} (문서 {r['n_docs']}건)" for r in refs]
        db.add_chat(session_id, "user", "\n".join(lines) + "\n" + q)
        note = (f"프로젝트 {_ko.quote(made_project['name'], '을')} 만들었습니다 — 기준 문서 {len(made['criteria'])}건, 접수 문서 {len(made['intake'])}건. "
                f"추출이 끝나면 아래 선택지로 양식 작성을 시작하거나 그림 쪽 판독을 요청할 수 있습니다.")
        if past:
            note += " 지난 자료 " + ", ".join(_ko.quote(storage.title_of(d.get('filename') or '')) for d in past[:4]) + _ko.josa(storage.title_of(past[:4][-1].get('filename') or ''), '을') + " 기준으로 이었습니다."
        db.add_chat(session_id, "assistant", note)
        _set_options(session_id, [{"kind": "ask", "text": "공고 요건 확인", "question": f"{made_project['name']}의 신청 자격·지원 규모·기한을 공고 기준으로 알려 줘"},
                                  {"kind": "draft", "text": "양식으로 작성 시작", "question": "양식으로 작업하자"},
                                  {"kind": "read", "text": "그림 쪽 판독", "question": "그림 쪽 판독해 줘"}])
        chat_revisions.remember(db.list_chats(session_id, limit=2)[0]["id"], None, criteria)
        return RedirectResponse(f"/chat/{session_id}", status_code=303)
    # 응답 대기 중 중복 전송 방지 — 마지막 메시지가 아직 답변 전이면 무시
    last = db.list_chats(session_id, limit=1)
    if session_id in _chat_running or (last and last[-1]["role"] == "user"):
        return RedirectResponse(f"/chat/{session_id}", status_code=303)

    stored: Path | None = None
    shown = q
    markers: list[str] = []
    for attachment in files:
        stored = storage.attachment_path(Path(db_path).parent, session_id, attachment.filename)
        with stored.open("wb") as out:
            shutil.copyfileobj(attachment.file, out)
        # 첨부는 문서로도 등록한다 — 대화에서 바로 열어 보고(/doc/{id}), 프로젝트 문서 목록에도 선다.
        # 파일은 첨부 폴더에 그대로 두고(ADR-0030 2층 '첨부'), 부서·등급은 올린 사람 기준이다.
        from zzaimy.app.access_policy import classify

        session_ = db.get_chat_session(session_id) or {}
        proj_ = db.get_project(int(session_["project_id"])) if session_.get("project_id") else None
        owner_ = getattr(request.state, "user", "zzaimy")
        a_dept, a_level = classify("auto", owner=owner_, uploader_dept=getattr(request.state, "dept", ""),
                                   project_dept=(proj_ or {}).get("dept"))
        att_type = (proj_ or {}).get("sector") if (proj_ or {}).get("sector") in INBOX_TYPES else "auto"
        att_doc = db.add_document(filename=attachment.filename, stored_path=str(stored), doc_type=att_type,
                                  project_id=(proj_ or {}).get("id"), owner=owner_, dept=a_dept, access_level=a_level)
        db.add_file("attachment", str(stored), name=attachment.filename, session_id=session_id, doc_id=att_doc,
                    size=stored.stat().st_size)
        background.add_task(_process_attachment, att_doc, stored)
        markers.append(f"[첨부#{att_doc}] {attachment.filename}")
    if markers:
        shown = "\n".join(markers) + "\n" + q

    _set_options(session_id, [])
    if retrieval and not web:
        shown = f"[실험 · {_RETRIEVAL_NOTE[retrieval]}]\n" + shown     # 화면 기록용 — 검색에 쓰는 질문(q)은 그대로
    db.add_chat(session_id, "user", shown, mode="model" if web == "model" else "web" if web else "document")
    chat_revisions.remember(db.list_chats(session_id, limit=1)[0]["id"], stored, criteria)
    _schedule_answer(background, session_id, q, stored, criteria, (web or "").strip().lower()[:8],   # "1"=웹 검색, "model"=모델 지식
                     retrieval="" if web else retrieval)
    return RedirectResponse(f"/chat/{session_id}", status_code=303)


@router.get("/chat/{session_id}/messages")
def chat_messages(request: Request, session_id: int):
    """대화 내용을 JSON 으로 돌려준다 — 페이지를 떠나지 않는 위젯과 갱신에 쓴다."""
    h = request.app.state.chat
    db, _chat_running, _owned_chat, _chat_suggestions = h.db, h._chat_running, h._owned_chat, h._chat_suggestions
    session = _owned_chat(request, session_id)
    rows = db.list_chats(session_id)
    return {
        "title": session["title"], "project_id": session.get("project_id"), "channel": session.get("channel") or "chat",
        "waiting": session_id in _chat_running or (bool(rows) and rows[-1]["role"] == "user"),
        "suggestions": _chat_suggestions(session, getattr(request.state, "user", "zzaimy")),
        "proposals": platform_ops.pending(db, session_id),
        "messages": [
            {"id": m["id"], "role": m["role"], "content": m["content"], "mode": m.get("mode")} for m in rows
        ],
    }


@router.post("/chat/{session_id}/proposals/{pid}/run")
def chat_proposal_run(request: Request, background: BackgroundTasks, session_id: int, pid: str, back: str = Form("")):
    """도우미가 제안한 플랫폼 동작을 담당자가 [실행] 으로 누른다 — 권한·대상은 여기서 다시 본다(platform_ops.run_proposal).
    결과와 바뀐 것은 대화에 남는다. 옆 패널은 JSON 을(go: 옮겨 갈 주소), 대화 화면의 폼(back)은 그 대화로 돌아간다."""
    h = request.app.state.chat
    db, _chat_running, _owned_chat, _ops_ctx = h.db, h._chat_running, h._owned_chat, h._ops_ctx
    _owned_chat(request, session_id)
    go = ""
    if session_id in _chat_running:
        out = {"ok": False, "error": "답변을 만드는 중입니다 — 끝난 뒤 눌러 주세요"}
    else:
        try:
            ctx_ = _ops_ctx(request.state.user, request.state.role, background)
            text = platform_ops.run_proposal(ctx_, session_id, pid)
            db.add_chat(session_id, "assistant", "실행했습니다 — " + text)
            go = ctx_.after.get("go", "")
            out = {"ok": True, "message": text, "go": go}
        except KeyError:
            raise HTTPException(404, "제안을 찾을 수 없습니다")
        except platform_ops.OpRefused as e:
            if request.state.role != "dev" and "관리자" in str(e):
                raise HTTPException(403, "관리자 계정만 할 수 있는 일입니다")
            out = {"ok": False, "error": str(e)}
            db.add_chat(session_id, "assistant", f"실행하지 않았습니다 — {e}")
        except Exception as e:
            out = {"ok": False, "error": f"실행하지 못했습니다({_ko.reason(e)}) — 바뀐 것은 없습니다"}
            db.add_chat(session_id, "assistant", out["error"])
    if back == f"/chat/{session_id}":
        return RedirectResponse(go or back, status_code=303)
    return out


@router.post("/chat/{session_id}/proposals/{pid}/result")
async def chat_proposal_result(request: Request, session_id: int, pid: str):
    """AURA 커서가 화면에서 실제로 한 결과(입력함·누름·자리 못 찾음·꺼진 단추) — 대화에 한 줄로 남긴다(platform_ops.report).
    완료 문구는 이 보고로만 생긴다. 몸은 {results: [{step: 번호, result: 갈래}]} — 단계 이름·넣은 말은 서버의 제안에서 읽는다."""
    h = request.app.state.chat
    h._owned_chat(request, session_id)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    try:
        line = platform_ops.report(h.db, session_id, pid, (body or {}).get("results") if isinstance(body, dict) else [])
    except KeyError:
        raise HTTPException(404, "제안을 찾을 수 없습니다")
    if line:
        h.db.add_chat(session_id, "assistant", line)
    return {"ok": True, "message": line}


@router.post("/chat/{session_id}/stop")
def chat_stop(request: Request, session_id: int):
    """옆 패널의 「멈춤」 — 도는 도우미 요청을 멈춘다(다음 걸음부터 하지 않는다). 멈출 것이 없으면 stopped=false."""
    h = request.app.state.chat
    h._owned_chat(request, session_id)
    return {"ok": True, "stopped": h._helper_stop(session_id)}


@router.get("/api/helper/suggest")
def helper_suggest(request: Request, page: str = ""):
    """빈 도우미 패널의 추천 질문 — 지금 화면의 안내 자료(platform_guide)에서 이 계정이 할 수 있는 일 몇 개.
    도우미가 대신 해 줄 수 있는 일(동작·가리킬 요소가 있는 것)을 먼저 둔다. 화면 항목이 없으면 빈 목록."""
    role = _ap.role_class(getattr(request.state, "role", "") or "")
    p = platform_guide.page_for(page or "/")
    if p is None or role not in p.roles:
        return {"page": "", "items": []}
    tasks = sorted(p.tasks, key=lambda t: 0 if (t.op or t.mark) else 1)
    return {"page": p.title, "items": [t.what for t in tasks[:3]]}


@router.post("/chat/{session_id}/retry")
def chat_retry(request: Request, background: BackgroundTasks, session_id: int):
    """마지막 답변을 지우고 같은 질문으로 다시 생성한다."""
    h = request.app.state.chat
    db, _chat_running, _owned_chat, _schedule_answer = h.db, h._chat_running, h._owned_chat, h._schedule_answer
    _owned_chat(request, session_id)
    rows = db.list_chats(session_id)
    if not rows:
        raise HTTPException(404)
    if session_id in _chat_running or rows[-1]["role"] == "user":
        return {"ok": False, "error": "답변을 기다리는 중입니다"}
    question = ""
    question_mode = None
    for m in reversed(rows):
        if m["role"] == "user":
            question = _strip_attach_prefix(m["content"])
            question_mode = m.get("mode")
            break
    if not question:
        return {"ok": False, "error": "다시 보낼 질문이 없습니다"}
    db.delete_chat_message(int(rows[-1]["id"]))
    criteria: list[int] = []
    session = db.get_chat_session(session_id)
    if session and session.get("project_id"):
        criteria = db.get_project_criteria_ids(int(session["project_id"]))
    _schedule_answer(background, session_id, question, None, criteria,
                     web="1" if question_mode == "web" else "model" if question_mode == "model" else "")
    return {"ok": True}


@router.post("/chat/ask")
def chat_ask(
    request: Request,
    background: BackgroundTasks,
    question: str = Form(...),
    context: str = Form(""),
    session_id: int | None = Form(None),
    page: str = Form(""),
    controls: str = Form(""),
):
    """화면을 떠나지 않고 묻는다 — 업무 도우미 옆 패널과 글 선택 질문이 쓴다."""
    h = request.app.state.chat
    db, _chat_running, _chat_progress, _chat_pages = h.db, h._chat_running, h._chat_progress, h._chat_pages
    _owned_chat, _schedule_answer, _can_edit_doc, _helper_task = h._owned_chat, h._schedule_answer, h._can_edit_doc, h._helper_task
    _run_action, _page_actions = h._run_action, h._page_actions
    q = question.strip()
    if not q:
        return {"ok": False, "error": "질문을 입력하세요"}
    quoted = context.strip()
    if quoted:
        if len(quoted) > 1200:
            quoted = quoted[:1200]
        asked = f"「{quoted}」\n\n{q}"
    else:
        asked = q
    project_match = re.fullmatch(r"/project/(\d+)", page)
    doc_match = re.fullmatch(r"/doc/(\d+)", page)
    page_project = int(project_match[1]) if project_match else None
    if doc_match:
        page_doc = db.get_document(int(doc_match[1]))
        if page_doc and page_doc.get("owner") == request.state.user:
            page_project = page_doc.get("project_id")
    if page_project:
        project = db.get_project(page_project)
        if not project or project.get("owner") != request.state.user:
            raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
    # 옆 패널에 직접 쓴 질문은 플랫폼 업무 도우미가 받는다. 화면 글을 골라 묻는 질문(context)은 문서 내용이라 기존 답변 경로로
    use_helper = (not quoted and os.environ.get("ZZAIMY_PLATFORM_HELPER", "1") != "0"
                  and getattr(request.state, "role", "") != "student")
    if session_id is not None:
        session = _owned_chat(request, session_id)
        # 업무 상담(도우미) 대화는 프로젝트에 묶지 않는다 — 어느 화면에서든 기록에서 골라 이어 간다. 프로젝트 대화만 그 프로젝트에서
        if (session.get("channel") or "chat") == "helper":
            use_helper = not quoted and use_helper
        elif page_project and session.get("project_id") != page_project:
            raise HTTPException(409, "다른 프로젝트의 대화입니다. 이 프로젝트에서 새 대화를 시작해 주세요.")
        last = db.list_chats(session_id, limit=1)
        if session_id in _chat_running or (last and last[-1]["role"] == "user"):
            return {"ok": False, "error": "앞선 답변을 기다리는 중입니다", "session_id": session_id}
    if session_id is None:
        # 도우미 대화는 「업무 상담」 — 왼쪽 사이드바(문서 작성·업무 대화)에는 싣지 않고 옆 패널의 기록에서 다룬다. 프로젝트에도 묶지 않는다
        session_id = db.create_chat_session(
            title=_chat_title(q)[:60], project_id=None if use_helper else page_project,
            owner=getattr(request.state, "user", "zzaimy"),
            channel="helper" if use_helper else "chat",
        )
    db.add_chat(session_id, "user", asked)
    if re.fullmatch(r"/[0-9A-Za-z/_\-.]{0,120}", page or ""):
        _chat_pages[session_id] = page
    else:
        _chat_pages.pop(session_id, None)
    # 업무 도우미는 27B 가 할 일을 고른다(open_page·propose_action) — 낱말 맞추기 단추는 에이전트 대화에만. 도우미에 남겨 두니
    # 「김예시 입력해줘」 의 「예시」 에 「학습 데이터 만들기」 가 붙어 나왔다(10-27)
    found = [] if use_helper else _page_actions(page, q, _ap.request_viewer(request))
    # 문서를 바꾸는 일은 지금 보고 있는 문서(화면 주소)이고 그 문서를 바꿀 수 있는 계정일 때만 — 질문 낱말로 짐작한 문서에는
    # 하지 않는다(10-16: 「…계획서의 성과지표」 질문에 짐작한 남의 문서의 사업 정보 읽기가 저절로 돌았다). 학생은 어떤 일도 없다
    page_doc_id = int(doc_match[1]) if doc_match else None

    def _may(a: dict) -> bool:
        if getattr(request.state, "role", "") == "student":
            return False
        if not a.get("changes"):
            return True
        did = (a.get("ctx") or {}).get("doc_id")
        if did is None:
            return True
        return did == page_doc_id and _can_edit_doc(request, db.get_document(int(did)))
    found = [a for a in found if _may(a)]
    done: list[str] = []
    for a in [x for x in found if x.get("auto")][:1]:
        line = _run_action(a["key"], {**(a.get("ctx") or {}), "owner": getattr(request.state, "user", "") or "zzaimy",
                                      "may_create_project": h._may_create_project(getattr(request.state, "user", ""),
                                                                                  getattr(request.state, "role", ""))})
        if line:
            done.append(line)
            db.add_chat(session_id, "assistant", line)
    if use_helper:
        _chat_progress[session_id] = {"started_at": time.time(), "steps": ["요청 확인 중"], "at": [time.time()]}
        _chat_running.add(session_id)
        run_id = h._helper_runs[session_id] = object()
        background.add_task(_helper_task, session_id, asked, _chat_pages.get(session_id, ""), run_id, q,
                            platform_ops.clean_controls(controls[:40000]) if controls else [])
    else:
        _schedule_answer(background, session_id, asked, None, [])
    return {"ok": True, "session_id": session_id, "done": done,
            "actions": [x for x in found if not x.get("auto")]}


@router.get("/chat/peek/{doc_id}")
def chat_doc_peek(request: Request, doc_id: int, q: str = ""):
    """문서를 화면 안에서 바로 훑어본다 — 다른 페이지로 옮겨가지 않게 한다.
    q(근거 대목·소제목)가 오면 그 대목이 든 조각부터 보여 준다 — 출처를 누르면 그 자리가 보이게."""
    h = request.app.state.chat
    db = h.db
    from zzaimy.app.main import SECTOR_LABELS
    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404)
    from zzaimy.app.grant_search import _text as _chunk_text
    chunks = [{**c, "content": _chunk_text(c)} for c in db.list_doc_chunks(doc_id)]   # 표 조각은 평문으로(검색이 건넨 글과 같게)
    start = 0
    squash = lambda t: re.sub(r"\s+", "", t or "")
    for probe in (squash(q)[:40], squash(q)[:16]):
        if len(probe) < 6:
            break
        hit = next((i for i, c in enumerate(chunks) if probe in squash(c.get("content"))), None)
        if hit is not None:
            start = hit
            break
    parts: list[str] = []
    total = 0
    for c in chunks[start:]:
        text = (c.get("content") or "").strip()
        if not text:
            continue
        parts.append(text)
        total += len(text)
        if total >= 2400:
            break
    return {
        "id": doc_id,
        "title": doc.get("filename", ""),
        "sector": SECTOR_LABELS.get(doc.get("sector", ""), doc.get("sector", "")),
        "doc_type": doc.get("doc_type", ""),
        "status": doc.get("status", ""),
        "n_chunks": len(chunks),
        "from_start": start == 0,
        "text": ("… " if start else "") + "\n\n".join(parts),
    }


@router.post("/chat/{session_id}/delete")
def delete_chat(request: Request, session_id: int):
    """대화 삭제 — 본인 대화만. 첨부·작업본 문서는 남고 대화 기록만 지운다(최근 대화 정리)."""
    h = request.app.state.chat
    db, _chat_running, _owned_chat, _share_sync = h.db, h._chat_running, h._owned_chat, h._share_sync
    _owned_chat(request, session_id)
    if session_id in _chat_running:
        raise HTTPException(409, "답변이 끝난 뒤 지워 주세요.")
    sess_ = db.get_chat_session(session_id) or {}
    db.delete_chat_session(session_id)
    if sess_.get("project_id"):                      # 지운 대화의 작업 문서에 준 보기 권한도 거둔다
        _share_sync(db.get_project(int(sess_["project_id"])), wait=True)
    return RedirectResponse("/chat", status_code=303)


@router.post("/chat/{session_id}/project")
def chat_set_project(request: Request, session_id: int, project_id: int = Form(0)):
    """대화를 프로젝트에 넣는다(0 이면 뺀다). 이어진 드라이브 문서는 프로젝트 폴더로(없으면 기타로) 따라 옮긴다."""
    h = request.app.state.chat
    db, accounts, password, _owned_chat = h.db, h.accounts, h.password, h._owned_chat
    from zzaimy.ingest import gdrive_files

    _owned_chat(request, session_id)
    owner = getattr(request.state, "user", "zzaimy")
    proj = db.get_project(project_id) if project_id else None
    if project_id and (not proj or proj.get("owner") != owner):
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다")
    db.set_chat_project(session_id, int(project_id) if proj else None)
    moved = None
    try:
        acct_ = accounts.get(owner, {}) if password is not None else {}
        moved = gdrive_files.move_to_project(db, session_id, (proj or {}).get("name"), dept=acct_.get("dept") or None)
    except Exception as e:
        db.add_chat(session_id, "assistant", f"대화는 프로젝트에 넣었지만 드라이브 문서는 옮기지 못했습니다({_ko.reason(e)}).")
    if moved:
        db.add_chat(session_id, "assistant", f"드라이브 문서를 「{moved['project']}」 폴더로 옮겼습니다.")
    return {"ok": True, "project_id": int(project_id) if proj else None, "moved": bool(moved)}
