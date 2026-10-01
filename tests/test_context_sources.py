from zzaimy.app.responder import NO_EVIDENCE_NOTE, pack_criteria_context
from types import SimpleNamespace


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
    agent.answer(db, "참여 목표는?", criteria_ids=[1, 2])
    assert [h["doc_id"] for h in agent.last_sources] == [2]
    assert "참여 목표 120명" in calls[0]["messages"][-1]["content"]
    assert "제외할 근거" not in calls[0]["messages"][-1]["content"]
