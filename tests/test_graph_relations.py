"""그래프 단계 — 질문을 온톨로지 말로 읽은 결과(Reading)대로 관계를 따라 범위를 넓히고, 관계의 양쪽을 근거에 담는다."""
import json

import pytest

from zzaimy.app.db import Database
from zzaimy.graph import kg_store, question_read
from zzaimy.graph import retrieve as gr
from zzaimy.graph.question_read import Reading


@pytest.fixture
def graph(tmp_path):
    db = Database(tmp_path / "rel.db")
    kg_store.ensure(db)
    gr._PROG_CACHE.update(at=0.0, rows=None)
    with db._conn() as conn:
        for pid, label in (("program:old", "가나 1단계 사업"), ("program:new", "가나 2단계 사업"), ("program:far", "다라 사업")):
            kg_store.put_node(conn, pid, "program", label, {"names": [label]})
        kg_store.put_node(conn, "group:g", "program_group", "가나 묶음", {})
        kg_store.put_edge(conn, "group:g", "program:new", "contains", "분류", ["묶음"])
        kg_store.put_edge(conn, "group:g", "program:far", "contains", "분류", ["묶음"])
        kg_store.put_edge(conn, "program:old", "program:new", "succeeded_by", "분류", ["앞 단계 → 다음 단계"])
        kg_store.put_node(conn, "year:new:y2024", "year", "2024년", {"year": 2024})
        kg_store.put_edge(conn, "program:new", "year:new:y2024", "contains", "분류", ["연도"])
        docs = ((1, "year:new:y2024", "plan"), (2, "year:new:y2024", "report"), (3, "year:new:y2024", "evaluation"),
                (4, "program:old", "plan"), (5, "program:far", "report"))
        for did, parent, kind in docs:
            kg_store.put_node(conn, f"doc:{did}", "doc", f"문서{did}", {"kind": kind}, did)
            kg_store.put_edge(conn, parent, f"doc:{did}", "contains", "분류", ["분류"])
        kg_store.put_edge(conn, "doc:1", "doc:2", "plans_reports", "식별자 일치", ["같은 연차"])
        kg_store.put_edge(conn, "doc:3", "doc:2", "evaluates", "분류", ["평가"])
    return db


def reader(**kw):
    return lambda q, progs: Reading(**kw)


def test_plan_report_relation_brings_both_sides_and_marks_them_for_cover(graph):
    sc = gr.scope(graph, "가나 2단계 2024 계획 대비 실적", reader=reader(programs=["program:new"], years=[2024], kinds=["plan"],
                                                                        relations=["plan_report"]))
    assert sc.read_by == "이름 맞추기 + 모델 읽기" and {1, 2} <= sc.docs and 3 not in sc.docs   # 평가는 이 관계의 갈래가 아니다
    assert {why for why, _ in sc.cover} == {"계획서", "실적보고서"}


def test_predecessor_relation_adds_the_previous_stage_program(graph):
    sc = gr.scope(graph, "가나 2단계의 앞 단계 사업에서는", reader=reader(programs=["program:new"], relations=["predecessor"]))
    assert 4 in sc.docs and ("앞 단계 사업", {4}) in sc.cover and 5 not in sc.docs


def test_group_and_evaluation_relations(graph):
    sc = gr.scope(graph, "같은 묶음 사업", reader=reader(programs=["program:new"], relations=["group"]))
    assert 5 in sc.docs and 4 not in sc.docs
    ev = gr.scope(graph, "평가 의견", reader=reader(programs=["program:new"], years=[2024], kinds=["report"], relations=["evaluates"]))
    assert {2, 3} <= ev.docs and any(why == "평가 문서" for why, _ in ev.cover)


def test_no_reader_or_failed_reading_falls_back_to_name_matching(graph):
    sc = gr.scope(graph, "가나 2단계 사업 2024년 계획서")
    assert sc.read_by == "이름 맞추기" and sc.program == "program:new" and sc.docs == {1} and not sc.cover
    failed = gr.scope(graph, "가나 2단계 사업 2024년 계획서", reader=lambda q, p: None)
    assert failed.read_by == "이름 맞추기" and failed.docs == {1}


def test_relation_without_program_is_not_followed(graph):
    sc = gr.scope(graph, "계획 대비 실적", reader=reader(relations=["plan_report"]))
    assert sc.docs is None and "따라가지 않음" in sc.steps[-1]


class _Resp:
    def __init__(self, text):
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]


class _Client:
    """VllmClient 꼴 — client.with_options(...).chat.completions.create 만 흉내."""
    def __init__(self, text):
        self.model, self._extra, self.calls = "m", {}, 0
        outer = self

        class _C:
            def with_options(self, **kw):
                return self

            class chat:
                class completions:
                    @staticmethod
                    def create(**kw):
                        outer.calls += 1
                        assert kw["response_format"]["type"] == "json_schema"
                        return _Resp(text)
        self.client = _C()


def test_model_reading_keeps_only_catalog_names_and_caches(graph):
    question_read._cache.clear()
    progs = gr._programs(graph)
    catalog, ids = question_read._catalog("가나 2단계 계획 대비 실적", progs)
    assert ids[0] == "program:new" and "다라" not in catalog            # 질문과 겹치는 사업만, 번호로
    c = _Client(json.dumps({"programs": [1, 99], "years": [2024], "rounds": [],
                            "kinds": ["plan", "bogus"], "relations": ["plan_report", "bogus"]}, ensure_ascii=False))
    r = question_read.read(c, "가나 2단계 계획 대비 실적", progs)
    assert r.programs == ["program:new"] and r.kinds == ["plan"] and r.relations == ["plan_report"] and r.years == [2024]
    question_read.read(c, "가나 2단계 계획 대비 실적", progs)
    assert c.calls == 1                                   # 같은 질문은 다시 묻지 않는다
    assert question_read.read(_Client("형식 아님"), "다른 질문", progs) is None


def test_answer_flow_reads_numbers_only_from_machine_outputs(graph, tmp_path):
    from zzaimy.app import answer_flow
    answer_flow._cache.update(at=0.0, edges=None)
    empty = answer_flow.snapshot(graph, tmp_path / "eval")
    assert empty["probe"] == [] and {r["kind"]: r["n"] for r in empty["relations"]}["plans_reports"] == 1
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "graph_relation_probe_t.json").write_text(json.dumps({"made": "2026-10-13 12:00", "name": {"summary": {
        "n": 2, "ok": 0.5, "sec_median": 1.0, "plan_report": {"n": 2, "ok": 0.5}}}, "model": {"summary": {
        "n": 2, "ok": 1.0, "sec_median": 2.0, "plan_report": {"n": 2, "ok": 1.0}}}}), encoding="utf-8")
    snap = answer_flow.snapshot(graph, tmp_path / "eval")
    assert snap["probe"][0]["name"] == 0.5 and snap["probe"][0]["model"] == 1.0 and snap["probe"][-1]["label"] == "전체"
    assert snap["timing"] == {"name": 1.0, "model": 2.0}
    assert snap["compare"] == {}
    (tmp_path / "eval" / "graphrag_vs_fulltext_t.json").write_text(json.dumps({"made": "2026-10-13 23:00", "summary": {
        "fulltext": {"search": {"sec_median": 1.1, "section": {"n": 74, "doc_at_k": 0.5,
                                                               "counts": {"doc_hit": {"k": 37, "n": 74, "rate": 0.5, "ci95": [0.39, 0.61]}}},
                                "relation": {"n": 59, "ok": 0.2}},
                     "answer": {"n": 30, "wall_sec_median": 50.0}},
        "graph": {"search": {"sec_median": 2.2, "section": {"n": 74, "doc_at_k": 0.7}, "relation": {"n": 59, "ok": 0.6}},
                  "answer": {"n": 30, "wall_sec_median": 60.0}}}}), encoding="utf-8")
    cmp_ = answer_flow.snapshot(graph, tmp_path / "eval")["compare"]
    assert cmp_["modes"] == ["① 풀텍스트 RAG — 그래프 없이", "② GraphRAG — 질문 해석: 코드(낱말 맞추기)"] and cmp_["n_sec"] == 74 and cmp_["n_ans"] == 30
    assert cmp_["adopted"] == [False, True]                                    # 27B 읽기를 끈 설정 — ② 가 운영
    assert cmp_["rows"][0]["vals"] == ["50% (37/74, 39~61%)", "—"]          # 파일 값 그대로
    assert {r["label"]: r["vals"] for r in cmp_["rows"]}["답이 나오기까지 — 보통(중앙값)"] == ["50.0초", "60.0초"]
    assert cmp_["summary"][0] == "특정 문서의 한 부분을 묻는 질문 — 그 문서가 상위 5건에: ① 50% · ② 70%"
    assert cmp_["summary"][1] == "관계를 묻는 질문 — 관계 양쪽 문서가 모두 상위에: ① 20% · ② 60%"
    assert "답이 나오기까지(보통): ① 50.0초 · ② 60.0초" in cmp_["summary"]


def test_partner_of_evidence_doc_is_added(monkeypatch):
    """근거에 든 계획서의 「짝」 실적보고서를 담는다 — 같은 연차의 아무 실적보고서가 아니라."""
    from zzaimy.app import grant_search
    order = [{"id": 11, "doc_id": 1, "content": "계획", "filename": "계획서"}, {"id": 99, "doc_id": 9, "content": "남", "filename": "다른 실적"},
             {"id": 22, "doc_id": 2, "content": "실적", "filename": "실적보고서"}]
    hits = [{"chunk_id": 11, "doc_id": 1, "content": "계획", "filename": "계획서", "path": []}]
    added = grant_search._cover_relations(None, [], hits, order, [("실적보고서", {2, 9})], None, None, {},
                                          {1: [("그 계획서의 실적보고서", 2)]})
    assert [h["doc_id"] for h in hits] == [1, 2] and added == [("그 계획서의 실적보고서", 1)]


def test_kinds_at_both_ends_follow_the_relation_without_model(graph):
    """모델 읽기 없이도 물은 갈래가 관계의 양 끝(계획서·실적보고서)이면 그 관계(계획↔실적 짝)를 따른다."""
    sc = gr.scope(graph, "가나 2단계 사업 2024년 계획서와 실적보고서")
    assert sc.read_by == "이름 맞추기" and sc.relations == ["plan_report"]
    assert sc.partners == {1: [("그 계획서의 실적보고서", 2)], 2: [("그 실적보고서의 계획서", 1)]}
    assert {why for why, _ in sc.cover} == {"계획서", "실적보고서"}
    one = gr.scope(graph, "가나 2단계 사업 2024년 실적보고서")
    assert one.relations == [] and not one.partners                   # 한쪽 갈래만 물으면 따라가지 않는다


def test_each_evidence_doc_brings_a_different_best_ranked_partner(monkeypatch):
    """문서 짝이 같은 연차의 계획서 × 실적보고서를 모두 이을 때 — 근거 문서마다 아직 없는 짝 가운데 순위가 앞선 것을 담아
    실적보고서가 하나만 되풀이되지 않는다. 순위에 없는 짝은 짝 문서들 안에서 같은 낱말로 다시 찾은 순위로."""
    from zzaimy.app import grant_lex, grant_search
    pair = [("그 계획서의 실적보고서", r) for r in (20, 21, 22)]
    partners = {1: pair, 3: pair}
    order = [{"id": 11, "doc_id": 1, "content": "계획 가", "filename": "계획서 가"},
             {"id": 31, "doc_id": 3, "content": "계획 나", "filename": "계획서 나"},
             {"id": 221, "doc_id": 22, "content": "실적 다", "filename": "실적보고서 다"}]
    hits = [{"chunk_id": 11, "doc_id": 1, "content": "계획 가", "filename": "계획서 가", "path": []},
            {"chunk_id": 31, "doc_id": 3, "content": "계획 나", "filename": "계획서 나", "path": []}]
    seen = {}

    def fake_rank(db, lq, scope_docs, user, depts=None):
        seen["scope"] = set(scope_docs)
        return [211, 201]                                      # 짝 안에서 다시 찾은 순위 — 21 이 20 보다 앞

    monkeypatch.setattr(grant_lex, "rank", fake_rank)
    monkeypatch.setattr(grant_search, "chunks_by_ids", lambda db, ids: [
        {"id": 201, "doc_id": 20, "content": "실적 가 " * 80, "filename": "실적보고서 가", "seq": 1},
        {"id": 211, "doc_id": 21, "content": "실적 나 " * 80, "filename": "실적보고서 나", "seq": 1}])
    monkeypatch.setattr(grant_search, "expand_many", lambda db, chunks: None)
    added = grant_search._cover_relations(None, frozenset({"실적"}), hits, order, [], None, None, {}, partners)
    assert seen["scope"] == {20, 21}                           # 순위에 이미 있는 짝(22)은 다시 찾지 않는다
    assert [h["doc_id"] for h in hits] == [1, 3, 22, 21]       # 순위에 있는 짝 먼저, 그다음 다시 찾은 순위로 — 서로 다른 짝
    assert added == [("그 계획서의 실적보고서", 1)] * 2


def test_quoted_document_name_narrows_to_that_document(graph):
    """질문이 문서 이름을 묶어 적으면 그 문서로 좁힌다 — 이름 속 「2차년도」·「실적」 은 질문의 연차·갈래로 읽지 않고,
    절 이름으로도 쓰지 않는다. 문서함에 없는 이름이면 예전처럼 연차·갈래로 좁힌다."""
    names = ["단위과제(2-1) 과제계획서.hwpx", "단위과제(2-3) 2차년도 실적보고서.hwpx", "평가 결과.pdf", "옛 계획서.hwp", "다라 보고서.hwp"]
    assert [graph.add_document(n, f"x/{n}") for n in names] == [1, 2, 3, 4, 5]
    sc = gr.scope(graph, "가나 2단계 사업 2024년 「단위과제(2-3) 2차년도 실적보고서」의 「추진 실적」 내용을 알려줘")
    assert sc.doc_names == ["단위과제(2-3) 2차년도 실적보고서"] and sc.docs == {2} and sc.round is None
    plain = gr.scope(graph, "가나 2단계 사업 2024년 계획서의 「추진 실적」 내용을 알려줘")
    assert plain.doc_names == [] and plain.kinds == ["plan"] and plain.docs == {1}     # 절 이름 속 「실적」 은 갈래가 아니다
    with graph._conn() as conn:                                          # 공개가 아닌 문서의 이름은 맞춰 보지 않는다(있는지가 새지 않게)
        conn.execute("UPDATE documents SET access_level = 'owner' WHERE id = 2")
    hidden = gr.scope(graph, "가나 2단계 사업 2024년 「단위과제(2-3) 2차년도 실적보고서」의 「추진 실적」 내용을 알려줘")
    assert hidden.doc_names == [] and not any("단위과제" in s_ for s_ in hidden.steps)
    from zzaimy.app import grant_search
    assert grant_search.section_phrases("「단위과제(2-3) 2차년도 실적보고서」의 「추진 실적」", sc.doc_names) == ["추진 실적"]
