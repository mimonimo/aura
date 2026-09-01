"""규정 저장소 테스트 (외부/내부 관리 규정 근거 검토).

에이전트가 규정을 근거로 검토 의견을 내기 위한 계층. 데이터는 전부 합성.
"""

from zzaimy.app.db import Database
from zzaimy.app.regulations import compose_review_context, find_relevant, split_regulation

ARTICLE_STYLE = """제1조(목적) 이 규정은 합성사업의 운영 기준을 정함을 목적으로 한다.
제2조(정의) 이 규정에서 평가위원이란 선정평가를 수행하는 자를 말한다.
제3조(회피) 평가위원은 이해관계가 있는 대학의 평가를 회피하여야 한다."""

MANUAL_STYLE = (
    "목적\n교육부 재정지원사업의 기본 절차를 제시한다.\n\n"
    "평가위원 선정 및 관리\n평가위원은 외부 전문가로 구성하며 보안서약서를 제출한다.\n\n"
    "사업운영 및 사후관리\n사업비 집행은 정산 기준을 따르고 현장점검을 실시한다."
)


def test_split_article_style_by_article():
    chunks = split_regulation(ARTICLE_STYLE)
    assert len(chunks) == 3
    assert chunks[0].heading.startswith("제1조")
    assert "평가위원" in chunks[1].content


def test_split_manual_style_by_paragraph():
    chunks = split_regulation(MANUAL_STYLE)
    assert len(chunks) >= 3
    assert all(c.content.strip() for c in chunks)


def test_find_relevant_matches_topic(tmp_path):
    db = Database(tmp_path / "t.db")
    doc_id = db.add_document(filename="합성규정.pdf", stored_path="/tmp/x", doc_type="regulation")
    db.add_regulation_chunks(doc_id, "합성규정", split_regulation(ARTICLE_STYLE))

    hits = find_relevant(db, "평가위원 회피 의무가 있는지 검토")
    assert hits
    assert any("회피" in h["content"] for h in hits)


def test_find_relevant_returns_empty_for_unrelated(tmp_path):
    db = Database(tmp_path / "t.db")
    doc_id = db.add_document(filename="합성규정.pdf", stored_path="/tmp/x", doc_type="regulation")
    db.add_regulation_chunks(doc_id, "합성규정", split_regulation(ARTICLE_STYLE))
    assert find_relevant(db, "김치찌개 끓이는 순서") == []


def test_compose_review_context_cites_regulation(tmp_path):
    db = Database(tmp_path / "t.db")
    doc_id = db.add_document(filename="합성규정.pdf", stored_path="/tmp/x", doc_type="regulation")
    db.add_regulation_chunks(doc_id, "합성규정", split_regulation(ARTICLE_STYLE))

    ctx = compose_review_context(db, "평가위원 위촉 계획과 이해관계 회피 기준을 다루는 문서")
    assert "합성규정" in ctx
    assert "회피" in ctx or "평가위원" in ctx


def test_compose_review_context_empty_when_no_regulations(tmp_path):
    db = Database(tmp_path / "t.db")
    assert compose_review_context(db, "아무 문서") == ""
