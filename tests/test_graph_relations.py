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
    assert sc.read_by == "모델 읽기" and {1, 2} <= sc.docs and 3 not in sc.docs   # 평가는 이 관계의 갈래가 아니다
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
    c = _Client(json.dumps({"programs": ["가나 2단계 사업", "없는 사업"], "years": [2024], "rounds": [],
                            "kinds": ["plan", "bogus"], "relations": ["plan_report", "bogus"]}, ensure_ascii=False))
    r = question_read.read(c, "가나 2단계 계획 대비 실적", progs)
    assert r.programs == ["program:new"] and r.kinds == ["plan"] and r.relations == ["plan_report"] and r.years == [2024]
    question_read.read(c, "가나 2단계 계획 대비 실적", progs)
    assert c.calls == 1                                   # 같은 질문은 다시 묻지 않는다
    assert question_read.read(_Client("형식 아님"), "다른 질문", progs) is None
