from zzaimy.app.responder import NO_EVIDENCE_NOTE, pack_criteria_context
from types import SimpleNamespace
import pytest


def test_oversized_source_does_not_hide_following_evidence():
    huge = {"doc_id": 1, "reg_title": "큰 문서명" * 300, "content": "넣지 못하는 근거"}
    small = {"doc_id": 2, "reg_title": "근거 문서", "content": "2025년 참여 목표 120명"}
    context, hits = pack_criteria_context([huge, small], budget=300)
    assert hits == [small]
    assert small["content"] in context and huge["content"] not in context
    assert len(context) <= 300


def test_no_included_evidence_means_no_advertised_sources():
    context, hits = pack_criteria_context([
        {"reg_title": "큰 제목" * 500, "content": "원문 근거"},
        {"reg_title": "본문 없는 문서", "content": ""},
    ], budget=300)
    assert context == NO_EVIDENCE_NOTE and hits == []


def test_header_and_separators_count_toward_budget():
    chunks = [{"doc_id": i, "reg_title": f"문서 {i}", "content": "목표 120명\n" * 10} for i in range(10)]
    context, hits = pack_criteria_context(chunks, budget=300)
    assert hits and len(hits) < len(chunks) and len(context) <= 300
    for hit in hits:
        assert hit["reg_title"] in context


def test_answer_displays_only_sources_sent_to_model(monkeypatch):
    from zzaimy.app import responder
    from zzaimy.generate import client

    chunks = [
        {"doc_id": 1, "reg_title": "초과 제목" * 2000, "content": "제외할 근거"},
        {"doc_id": 2, "reg_title": "선택된 자료", "content": "참여 목표 120명"},
    ]
    db = SimpleNamespace(chunks_for_docs=lambda ids: chunks, all_settings=lambda: {})
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="답변"))])

    fake = SimpleNamespace(model="fake", client=SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(client, "VllmClient", lambda **kwargs: fake)
    monkeypatch.setattr(responder, "rank_criteria_chunks", lambda *args: chunks)
    agent = responder.AgentResponder()
    progress = []
    agent.answer(db, "참여 목표는?", criteria_ids=[1, 2], on_progress=progress.append)
    assert progress == ["관련 근거 검색 중", "답변에 사용할 근거 1개 구성", "답변 작성 중"]
    assert [h["doc_id"] for h in agent.last_sources] == [2]
    assert "참여 목표 120명" in calls[0]["messages"][-1]["content"]
    assert "제외할 근거" not in calls[0]["messages"][-1]["content"]


@pytest.mark.parametrize("found,weak", [(False, False), (True, False), (True, True)])
def test_registered_search_keeps_sources_and_missing_evidence_notice(monkeypatch, found, weak):
    from zzaimy.app import paths, regulations, responder
    from zzaimy.generate import client

    def retired(*args, **kwargs):
        pytest.fail("폐기 코퍼스를 조회하면 안 됩니다")

    monkeypatch.setattr(paths, "corpus_db_existing", retired)
    hits = [{"doc_id": 42, "reg_title": "등록 문서", "content": "참여 목표 120명",
             "weak_evidence": weak}] if found else []
    received = []

    def search(db, query, **scope):
        assert scope["user"] == "staff" and scope["dept"] == "학생처"
        return hits

    def create(**kwargs):
        received.append(kwargs["messages"][-1]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="검사 답변"))])

    monkeypatch.setattr(regulations, "find_relevant", search)
    fake = SimpleNamespace(model="fake", client=SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(client, "VllmClient", lambda **kwargs: fake)
    agent = responder.AgentResponder()
    assert agent.answer(SimpleNamespace(all_settings=lambda: {}), "참여 목표는?",
                        scope={"user": "staff", "dept": "학생처"}) == "검사 답변"
    assert [s["doc_id"] for s in agent.last_sources] == ([42] if found else [])
    assert (responder.NO_EVIDENCE_NOTE in received[0]) == (not found)
    assert (responder.WEAK_EVIDENCE_NOTE in received[0]) == weak


@pytest.mark.parametrize("weak", [True, False])
def test_weak_regulations_dropped_when_grant_documents_answer(monkeypatch, weak):
    """사업 문서 근거가 있으면 하한을 못 넘은 규정 근거는 빼고, 「근거가 약하다」 안내도 붙이지 않는다(10/5 RAG 실측)."""
    from zzaimy.app import grant_search, regulations, responder
    from zzaimy.generate import client

    reg = [{"doc_id": 7, "reg_title": "AID 기본계획", "content": "2026학년도 AID 계획", "weak_evidence": weak}]
    monkeypatch.setattr(regulations, "find_relevant", lambda db, q, **scope: reg)
    monkeypatch.setattr(grant_search, "search", lambda *a, **k: {"hits": [
        {"doc_id": 9, "content": "2017년 연수 개요 본문", "filename": "계획서.hwp", "path": ["LINC+", "계획서.hwp"]}]})
    received = []

    def create(**kwargs):
        received.append(kwargs["messages"][-1]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="답"))])
    fake = SimpleNamespace(model="fake", client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(client, "VllmClient", lambda **kwargs: fake)
    responder.AgentResponder().answer(SimpleNamespace(all_settings=lambda: {}), "LINC+ 2017 연수 개요?", scope={"user": "s"})
    assert "2017년 연수 개요 본문" in received[0]
    assert ("AID 계획" in received[0]) == (not weak)
    assert responder.WEAK_EVIDENCE_NOTE not in received[0]
