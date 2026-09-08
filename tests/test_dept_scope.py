"""부서(dept) 축 — 규정 검색·그래프가 부서별로 스코프되는지 검증."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from zzaimy.app.db import Database
from zzaimy.graph.build import build_graph


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "dept.db")


def _c(heading, content):
    return SimpleNamespace(heading=heading, content=content)


def test_regulation_chunks_scoped_by_dept(db):
    plan = db.add_document("기획.pdf", "/x", doc_type="regulation")
    welfare = db.add_document("복지.pdf", "/x", doc_type="regulation")
    common = db.add_document("학칙.pdf", "/x", doc_type="regulation")
    db.add_regulation_chunks(plan, "기획규정", [_c("1", "예산 편성 지침")], dept="기획처")
    db.add_regulation_chunks(welfare, "복지규정", [_c("1", "장학 지원 지침")], dept="복지처")
    db.add_regulation_chunks(common, "학칙", [_c("1", "공통 학사 규정")], dept="공통")

    plan_chunks = db.list_regulation_chunks(dept="기획처")
    titles = {c["reg_title"] for c in plan_chunks}
    assert titles == {"기획규정", "학칙"}          # 기획처 + 공통만
    assert "복지규정" not in titles                # 타 부서 배제

    welfare_chunks = db.list_regulation_chunks(dept="복지처")
    assert {c["reg_title"] for c in welfare_chunks} == {"복지규정", "학칙"}


def test_graph_scoped_by_dept(db):
    a = db.add_document("기획사업.pdf", "/x", doc_type="regulation", sector="grant")
    b = db.add_document("복지사업.pdf", "/x", doc_type="regulation", sector="grant")
    # dept 컬럼은 add_document 기본 '공통' — 직접 갱신
    with db._conn() as conn:
        conn.execute("UPDATE documents SET dept=? WHERE id=?", ("기획처", a))
        conn.execute("UPDATE documents SET dept=? WHERE id=?", ("복지처", b))

    g = build_graph(db, include_similarity=False, dept="기획처")
    labels = {n["label"] for n in g["nodes"]}
    assert "기획사업" in labels
    assert "복지사업" not in labels               # 타 부서 문서 제외


def test_dept_none_returns_all(db):
    a = db.add_document("문서.pdf", "/x", doc_type="regulation")
    db.add_regulation_chunks(a, "규정", [_c("1", "내용")], dept="기획처")
    assert len(db.list_regulation_chunks()) == 1  # dept 미지정이면 전체
