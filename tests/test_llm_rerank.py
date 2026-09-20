"""LLM 리랭커 — 점수로 정렬하되 동점·실패 시 검색 순서를 지킨다 (가짜 모델)."""
import types

import pytest


def _fake_openai(scores):
    class _Comp:
        def create(self, **kw):
            body = kw["messages"][0]["content"]
            for key, val in scores.items():
                if f"조각:\n{key}" in body:
                    if val is None:
                        raise RuntimeError("down")
                    msg = types.SimpleNamespace(content=str(val))
                    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])
            raise AssertionError(body)

    class _OpenAI:
        def __init__(self, **kw):
            self.chat = types.SimpleNamespace(completions=_Comp())
    return _OpenAI


@pytest.fixture
def make(monkeypatch):
    from zzaimy.app import llm_rerank
    from zzaimy.generate import client as cl

    def _make(scores):
        monkeypatch.setattr(cl, "OpenAI", _fake_openai(scores))
        monkeypatch.setattr(cl, "is_ollama", lambda url: True)
        return llm_rerank.make_reranker("http://x/v1", "m", workers=2)
    return _make


def _c(t):
    return {"id": t, "content": t, "reg_title": "규정", "heading": ""}


def test_scores_reorder_and_ties_keep_search_order(make):
    rr = make({"a": 1, "b": 3, "c": 1, "d": 3})
    assert [c["id"] for c in rr("질의", [_c("a"), _c("b"), _c("c"), _c("d")])] == ["b", "d", "a", "c"]


def test_all_failures_keep_search_order(make):
    rr = make({"a": None, "b": None})
    assert [c["id"] for c in rr("질의", [_c("a"), _c("b")])] == ["a", "b"]


def test_production_scores_use_llm_when_configured(monkeypatch, make):
    from zzaimy.app import llm_rerank, rerank

    make({"a": 1, "b": 3})             # 가짜 모델 설치
    monkeypatch.setenv("ZZAIMY_LLM_RERANK", "http://x/v1|m")
    llm_rerank._scorer_cache.clear()
    scored = rerank.rerank_scored("질의", [_c("a"), _c("b")])
    assert [(c["id"], round(s, 2)) for c, s in scored] == [("b", 1.0), ("a", 0.33)]
    monkeypatch.delenv("ZZAIMY_LLM_RERANK")
    llm_rerank._scorer_cache.clear()


def test_query_expand_is_a_noop_without_config_or_on_failure(monkeypatch):
    from zzaimy.app import query_expand
    from zzaimy.generate import client as cl

    monkeypatch.delenv("ZZAIMY_QUERY_EXPAND", raising=False)
    assert query_expand.expand("계약직 뽑으면 누가 결재해?") == "계약직 뽑으면 누가 결재해?"

    class Boom:
        def __init__(self, **kw):
            raise RuntimeError("down")
    monkeypatch.setattr(cl, "OpenAI", Boom)
    query_expand._clients.clear()
    assert query_expand.expand("질문", "http://x/v1", "m") == "질문"


def test_find_relevant_expands_only_when_evidence_is_weak(monkeypatch):
    """1위가 약할 때만 확장하고, 확장본이 더 나을 때만 바꾼다."""
    from zzaimy.app import query_expand, regulations, rerank

    calls = []
    cands = {"orig": [{"id": 1, "content": "무관"}], "exp": [{"id": 2, "content": "임용권자"}]}
    monkeypatch.setattr(regulations, "hybrid_candidates",
                        lambda db, q, *a, **k: cands["exp" if "임용권자" in q else "orig"])
    monkeypatch.setattr(rerank, "rerank_scored",
                        lambda q, cs, *a: [(c, 0.9 if c["id"] == 2 else 0.02) for c in cs])
    monkeypatch.setattr(query_expand, "configured", lambda: ("http://x/v1", "m"))
    monkeypatch.setattr(query_expand, "expand", lambda q, *a: calls.append(q) or f"{q} 임용권자")
    out = regulations.find_relevant(None, "계약직 뽑으면 누가 결재해?")
    assert [c["id"] for c in out] == [2] and not out[0].get("weak_evidence")
    assert calls == ["계약직 뽑으면 누가 결재해?"]
    # 1위가 강하면 확장하지 않는다
    calls.clear()
    monkeypatch.setattr(rerank, "rerank_scored", lambda q, cs, *a: [(c, 0.95) for c in cs])
    regulations.find_relevant(None, "임용권자 규정")
    assert calls == []
