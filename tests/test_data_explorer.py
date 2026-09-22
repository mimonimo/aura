"""데이터 열람(/dev/db) — 문서 중심 탐색기 조립 함수와 화면 스모크.

수치는 전부 DB에서 나온 것이어야 하고, 마스킹 기록은 유형·건수만 보여야 한다.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import data_explorer as dx
from zzaimy.app.db import Database
from zzaimy.app.main import create_app


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "dx.db")


@pytest.fixture()
def client(tmp_path):
    app = create_app(
        db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(),
    )
    return TestClient(app)


def _unit(heading: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(heading=heading, content=content)


def _seed(db: Database) -> dict:
    """규정 1건(검색 단위 2개) + 접수 문서 1건(추출 조각 3개, 마스킹 기록) — 전부 합성."""
    reg = db.add_document("학칙.pdf", "/x/reg.pdf", doc_type="regulation")
    db.add_regulation_chunks(reg, "학칙", [
        _unit("제1조(목적)", "이 규정은 학사 운영의 기본을 정한다. " * 8),
        _unit("제2조(휴학)", "휴학은 학기 단위로 신청한다."),
    ])
    db.replace_doc_chunks(reg, [{"kind": "heading", "page_no": 1, "content": "학칙"}])
    intake = db.add_document(
        "신청서.pdf", "/x/intake.pdf", doc_type="recruit", related_criteria_id=reg,
    )
    db.update_document(intake, status="reviewed", masked_text="지원자 [KR_NAME] 연락처 [KR_PHONE]")
    db.replace_doc_chunks(intake, [
        {"kind": "text", "page_no": 1, "content": "지원자 [KR_NAME] 연락처 [KR_PHONE] " * 10},
        {"kind": "table", "page_no": 1, "content": "| 항목 | 값 |"},
        {"kind": "image_text", "page_no": 2, "content": "도장"},
    ])
    db.replace_mask_events(intake, [
        {"entity_type": "KR_NAME", "n": 2, "context": "지원자 [KR_NAME] 연락처"},
        {"entity_type": "KR_PHONE", "n": 1, "context": None},
    ])
    return {"reg": reg, "intake": intake}


# --- 목록·개요 ---


def test_list_docs_counts_chunks_and_filters(db):
    ids = _seed(db)
    docs = {d["id"]: d for d in dx.list_docs(db)}
    assert docs[ids["reg"]]["n_units"] == 2 and docs[ids["reg"]]["n_chunks"] == 1
    assert docs[ids["intake"]]["n_units"] == 0 and docs[ids["intake"]]["n_chunks"] == 3
    assert [d["id"] for d in dx.list_docs(db, doc_type="recruit")] == [ids["intake"]]
    assert [d["id"] for d in dx.list_docs(db, q="학칙")] == [ids["reg"]]
    receipt = docs[ids["intake"]]["receipt_no"]
    assert receipt.startswith("2026-채용-")
    assert [d["id"] for d in dx.list_docs(db, q=receipt)] == [ids["intake"]]
    assert {t["doc_type"]: t["n"] for t in dx.type_counts(db)} == {"regulation": 1, "recruit": 1}


def test_list_docs_shortens_long_names_but_keeps_full(db):
    long = "가" * 200 + ".pdf"
    db.add_document(long, "/x/long.pdf", doc_type="grant")
    d = dx.list_docs(db)[0]
    assert d["filename"] == long
    assert len(d["name"]) <= dx.NAME_CHARS and d["name"].endswith("…")


def test_doc_overview_reports_masking_by_type_and_count_only(db):
    ids = _seed(db)
    doc = db.get_document(ids["intake"])
    o = dx.doc_overview(db, doc, file_exists=lambda p: p.endswith("intake.pdf"))
    assert o["original"] == {"exists": True, "suffix": "pdf", "name": "intake.pdf"}
    assert o["mask"]["subject"] and o["mask"]["recorded"]
    assert [(m["label"], m["n"]) for m in o["mask"]["rows"]] == [("성명", 2), ("전화번호", 1)]
    assert o["mask"]["total"] == 3
    assert "context" not in o["mask"] and not any("context" in m for m in o["mask"]["rows"])
    assert o["related_criteria"]["id"] == ids["reg"] and o["related_criteria"]["name"] == "학칙"
    # 기준 문서는 마스킹 대상이 아니고 기록도 없다
    reg = dx.doc_overview(db, db.get_document(ids["reg"]), file_exists=lambda p: False)
    assert not reg["mask"]["subject"] and not reg["mask"]["recorded"]
    assert reg["original"]["exists"] is False


def test_doc_overview_distinguishes_zero_hits_from_no_record(db):
    doc_id = db.add_document("a.pdf", "/x/a", doc_type="auto")
    db.replace_mask_events(doc_id, [])   # 돌았지만 0건
    o = dx.doc_overview(db, db.get_document(doc_id), file_exists=lambda p: False)
    assert o["mask"]["recorded"] and o["mask"]["rows"] == [] and o["mask"]["total"] == 0


# --- 조각·임베딩 ---


def test_chunks_view_counts_kinds_and_marks_embedding_by_index_ids(db):
    ids = _seed(db)
    units = db.chunks_for_docs([ids["reg"]])
    embedded = frozenset({units[0]["id"]})
    v = dx.doc_chunks_view(db, ids["reg"], embedded)
    assert v["n_units"] == 2 and v["n_embedded"] == 1
    assert [u["embedded"] for u in v["units"]] == [True, False]
    assert v["units"][0]["long"] and v["units"][0]["preview"].endswith("…")
    assert len(v["units"][0]["preview"]) <= dx.PREVIEW_CHARS
    assert v["units"][1]["long"] is False and v["units"][1]["preview"] == "휴학은 학기 단위로 신청한다."
    # 색인 자체를 모르면 조각별 판정은 미확인(None)
    unknown = dx.doc_chunks_view(db, ids["reg"], None)
    assert unknown["n_embedded"] is None and all(u["embedded"] is None for u in unknown["units"])

    intake = dx.doc_chunks_view(db, ids["intake"], embedded)
    assert intake["n_units"] == 0 and intake["n_extract"] == 3
    assert sorted((k["label"], k["n"]) for k in intake["kinds"]) == [("그림 속 글자", 1), ("본문", 1), ("표", 1)]
    assert [r["kind"] for r in intake["extract"]] == ["text", "table", "image_text"]
    assert intake["extract"][0]["content"].startswith("지원자 [KR_NAME]")   # 마스킹본 그대로


def test_embedding_index_reads_meta_and_npz_ids(tmp_path):
    np = pytest.importorskip("numpy")
    meta = tmp_path / "chunk_embeddings.meta.json"
    npz = tmp_path / "chunk_embeddings.npz"
    assert dx.embedding_index(meta, npz)["exists"] is False
    meta.write_text(json.dumps({"model": "nlpai-lab/KURE-v1", "n_chunks": 2, "dim": 4}))
    info = dx.embedding_index(meta, npz)
    assert info["exists"] and info["model"] == "nlpai-lab/KURE-v1" and info["n_chunks"] == 2
    assert info["ids"] is None                                   # npz 없음 → 조각별 미확인
    np.savez(npz, ids=np.array([11, 12]), vectors=np.zeros((2, 4), dtype="float32"))
    info = dx.embedding_index(meta, npz)
    assert info["ids"] == frozenset({11, 12})


def test_regulation_docs_count_embedded_units(db):
    ids = _seed(db)
    units = db.chunks_for_docs([ids["reg"]])
    docs = dx.regulation_docs(db, frozenset({u["id"] for u in units}))
    assert [d["id"] for d in docs] == [ids["reg"]]
    assert docs[0]["title"] == "학칙" and docs[0]["n_units"] == 2 and docs[0]["n_embedded"] == 2
    assert dx.regulation_docs(db, None)[0]["n_embedded"] is None


def test_regulation_tab_marks_search_hits_in_selected_doc(db):
    ids = _seed(db)
    units = db.chunks_for_docs([ids["reg"]])
    calls: list[str] = []

    def fake_search(text: str) -> list[dict]:
        calls.append(text)
        return [units[1]]

    view = dx.regulation_tab(db, q="휴학", doc_id=ids["reg"], search_fn=fake_search)
    assert calls == ["휴학"]
    assert [h["heading"] for h in view["hits"]] == ["제2조(휴학)"]
    assert [u["hit"] for u in view["selected"]["units"]] == [False, True]
    # 빈 질의는 검색하지 않는다 · 접수 문서를 고르면 규정 선택은 비어 있다
    view = dx.regulation_tab(db, q="  ", doc_id=ids["intake"], search_fn=fake_search)
    assert calls == ["휴학"] and view["hits"] == [] and view["selected"] is None


# --- 연관(그래프) ---


def test_related_for_doc_groups_neighbors_by_edge_kind():
    graph = {
        "nodes": [
            {"id": "d1", "doc_id": 1, "label": "신청서", "kind": "intake", "doc_type": "grant"},
            {"id": "d2", "doc_id": 2, "label": "학칙", "kind": "criteria", "doc_type": "regulation"},
            {"id": "p3", "doc_id": None, "label": "2026 사업", "kind": "project", "doc_type": "project"},
            {"id": "e4", "doc_id": None, "label": "한국장학재단", "kind": "entity", "doc_type": "org"},
        ],
        "edges": [
            {"s": "d1", "t": "d2", "kind": "refers", "w": 1.0,
             "why": "담당자가 이 문서의 근거 기준으로 지정했습니다."},
            {"s": "p3", "t": "d1", "kind": "relates", "w": 0.62},
            {"s": "d1", "t": "e4", "kind": "mentions", "w": 0.4},
            {"s": "d2", "t": "e4", "kind": "mentions", "w": 0.9},   # 남의 간선
        ],
    }
    r = dx.related_for_doc(graph, 1)
    assert r["available"] and r["in_graph"] and r["n"] == 3
    assert [g["kind"] for g in r["groups"]] == ["refers", "relates", "mentions"]
    by_kind = {g["kind"]: g["nodes"] for g in r["groups"]}
    # 간선마다 '왜 이어졌는지'가 데이터로 따라와야 한다 (graph.build.add_edge)
    assert by_kind["refers"][0] == {
        "id": "d2", "label": "학칙", "node_kind": "criteria", "kind_label": "기준",
        "doc_type": "regulation", "w": 1.0, "note": "", "href": "/doc/2",
        "why": "담당자가 이 문서의 근거 기준으로 지정했습니다.", "evidence": [],
    }
    assert by_kind["relates"][0]["href"] == "/project/3" and by_kind["relates"][0]["kind_label"] == "프로젝트"
    assert by_kind["relates"][0]["note"] == "0.62"                 # 가중 간선만 값을 보인다
    assert by_kind["mentions"][0]["href"] == "/graph?focus=e4" and by_kind["mentions"][0]["kind_label"] == "기관"
    suggested = dx.related_for_doc(
        {"nodes": graph["nodes"], "edges": [{"s": "d1", "t": "d2", "kind": "refers", "w": 0.7}]}, 1)
    assert suggested["groups"][0]["nodes"][0]["note"] == "자동 제안"
    assert dx.related_for_doc(graph, 99) == {"available": True, "in_graph": False, "groups": [], "n": 0}
    assert dx.related_for_doc(None, 1)["available"] is False


# --- 채팅·코퍼스·탭 ---


def test_chat_sessions_view_counts_messages_and_known_sources(db):
    sid = db.create_chat_session("질병 휴학 기준")
    db.add_chat(sid, "user", "질병 휴학 기준이 뭐야")
    db.add_chat(sid, "assistant", "진단서가 필요합니다.")
    other = db.create_chat_session("다른 질문")
    rows = {r["id"]: r for r in dx.chat_sessions_view(db, {sid: [{"title": "학칙"}] * 3})}
    assert rows[sid]["n_messages"] == 2 and rows[sid]["n_answers"] == 1 and rows[sid]["n_sources"] == 3
    assert rows[other]["n_messages"] == 0 and rows[other]["n_sources"] is None   # 이번 프로세스가 모르는 세션


def test_corpus_tab_without_db_and_with_search(db):
    assert dx.corpus_tab(None, q="장학")["summary"] == {
        "exists": False, "docs": 0, "chunks": 0, "by_source": []}
    _seed(db)   # 코퍼스 DB도 같은 스키마 — 규정 조각을 코퍼스 조각으로 쓴다
    view = dx.corpus_tab(db, q="휴학", search_fn=lambda t: db.chunks_for_docs([1])[:1], dense_ready=True)
    assert view["summary"]["docs"] == 2 and view["summary"]["chunks"] == 2
    assert view["summary"]["by_source"][0] == {"name": "학칙.pdf", "n": 2}
    assert view["results"][0]["rank"] == 1 and view["results"][0]["heading"] == "제1조(목적)"


def test_normalize_tab_maps_legacy_table_urls():
    assert dx.normalize_tab("corpus") == "corpus"
    assert dx.normalize_tab("", "regulation_chunks") == "regulation"
    assert dx.normalize_tab("", "chat_messages") == "chat"
    assert dx.normalize_tab("nope", "documents") == "docs"


# --- 화면 스모크 ---


def test_dev_db_renders_tabs_and_document_detail(client):
    long = "가" * 200
    client.post("/upload", data={"doc_type": "recruit"},
                files={"file": (long + ".pdf", b"%PDF", "application/pdf")})
    r = client.get("/dev/db")
    assert r.status_code == 200
    for key in ("tab=docs", "tab=regulation", "tab=corpus", "tab=chat"):
        assert f'href="/dev/db?{key}"' in r.text
    assert f'title="{long}.pdf"' in r.text and (long + ".pdf") not in r.text.split('title="')[0]
    assert "추출·검색 자료" in r.text

    detail = client.get("/dev/db?tab=docs&doc=1")
    assert detail.status_code == 200
    assert "합성 마스킹 본문" not in detail.text            # 본문 전문은 조각으로만, 원문 덤프 없음
    assert "추출·검색 자료" in detail.text and "연관 (지식 그래프)" in detail.text
    assert 'href="/graph?focus=d1"' in detail.text and 'href="/doc/1"' in detail.text
    assert client.get("/dev/db?tab=docs&doc=999").status_code == 200   # 없는 문서 → 목록만


def test_dev_db_other_tabs_and_legacy_urls(client):
    assert client.get("/dev/db?tab=regulation").status_code == 200
    corpus = client.get("/dev/db?tab=corpus")   # 코퍼스 DB는 작업 폴더 기준 — 있으면 검색 화면, 없으면 안내
    assert corpus.status_code == 200 and ("등록된 공개 문서 모음이 없습니다." in corpus.text or "공개 문서 검색" in corpus.text)
    chat = client.get("/dev/db?tab=chat")
    assert chat.status_code == 200 and "채팅 기록 없음" in chat.text
    legacy = client.get("/dev/db?table=regulation_chunks")
    assert legacy.status_code == 200 and "규정 문서 없음" in legacy.text


def test_dev_corpus_redirects_into_data_explorer(client):
    r = client.get("/dev/corpus", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/dev/db?tab=corpus"
    r = client.get("/dev/corpus?q=장학금", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/dev/db?tab=corpus&q=%EC%9E%A5%ED%95%99%EA%B8%88"
    assert client.get("/dev/corpus").status_code == 200           # 따라가면 열람 화면


def test_short_content_also_has_full_reader(client, tmp_path):
    db = Database(tmp_path / "test.db")
    ids = _seed(db)
    regulation = client.get(f"/dev/db?tab=regulation&doc={ids['reg']}").text
    document = client.get(f"/dev/db?tab=docs&doc={ids['intake']}").text
    assert '<pre>휴학은 학기 단위로 신청한다.</pre>' in regulation
    assert '<pre>도장</pre>' in document
    assert 'id="dxDetail"' in regulation
    assert 'class="dx-more can"' in regulation
    assert 'aria-label="추출 내용"' in document
    assert 'class="dx-group"' not in document
    assert 'box-shadow:inset 3px' not in document


def test_table_chunk_preview_is_readable():
    from zzaimy.app.data_explorer import chunk_rows, table_text

    raw = '{"n_rows": 2, "n_cols": 2, "cells": [[0, 0, 1, 1, 0, "구분"], [0, 1, 1, 1, 0, "금액"], [1, 0, 1, 1, 0, "인건비"], [1, 1, 1, 1, 0, "1,200"]]}'
    assert table_text(raw) == "구분 | 금액\n인건비 | 1,200"
    assert table_text("그냥 글") is None
    row = chunk_rows([{"id": 1, "seq": 0, "kind": "table", "content": raw}])[0]
    assert row["preview"].startswith("구분 | 금액") and "n_rows" not in row["content"]
