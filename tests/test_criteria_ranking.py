"""프로젝트 기준 문서가 여럿일 때 답변 근거는 관련도순이고 문서마다 한 조각은 들어간다."""

from zzaimy.app import responder as R


def test_rank_criteria_chunks_gives_each_document_a_slot(monkeypatch):
    chunks = [{"doc_id": 1, "content": f"기본계획 {i}", "reg_title": "기본계획"} for i in range(5)] + \
             [{"doc_id": 2, "content": "공고 신청 기한 2026. 4. 3.", "reg_title": "공고문"}]
    ranked = [chunks[0], chunks[1], chunks[5], chunks[2]]
    import zzaimy.app.regulations as reg
    monkeypatch.setattr(reg, "find_relevant", lambda db, q, top_k=3, chunks=None, **kw: ranked)
    out = R.rank_criteria_chunks(None, "신청 기한은?", chunks, [1, 2])
    assert out[0]["doc_id"] == 1 and out[1]["doc_id"] == 2          # 문서마다 첫 자리
    assert [c["content"] for c in out] == ["기본계획 0", "공고 신청 기한 2026. 4. 3.", "기본계획 1", "기본계획 2"]


def test_rank_criteria_chunks_falls_back_to_document_order(monkeypatch):
    chunks = [{"doc_id": 1, "content": "a"}, {"doc_id": 2, "content": "b"}]
    import zzaimy.app.regulations as reg
    monkeypatch.setattr(reg, "find_relevant", lambda *a, **k: [])
    assert R.rank_criteria_chunks(None, "q", chunks, [1, 2]) == chunks
