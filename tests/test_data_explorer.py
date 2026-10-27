"""반입 결과 보기(/dev/archive/doc/{id}) — 문서 하나 들여다보기 조립 함수와 화면 스모크. 옛 데이터 현황(/dev/db)은 10-27 에 없앴다.

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
    assert by_kind["mentions"][0]["href"].startswith("/graph/explore?q=") and by_kind["mentions"][0]["kind_label"] == "기관"
    suggested = dx.related_for_doc(
        {"nodes": graph["nodes"], "edges": [{"s": "d1", "t": "d2", "kind": "refers", "w": 0.7}]}, 1)
    assert suggested["groups"][0]["nodes"][0]["note"] == "자동 제안"
    assert dx.related_for_doc(graph, 99) == {"available": True, "in_graph": False, "groups": [], "n": 0}
    assert dx.related_for_doc(None, 1)["available"] is False


# --- 채팅·코퍼스·탭 ---


# --- 화면 스모크 ---


def test_doc_result_page_shows_overview_chunks_and_graph(client, tmp_path):
    long = "가" * 200
    client.post("/upload", data={"doc_type": "recruit"},
                files={"file": (long + ".pdf", b"%PDF", "application/pdf")})
    detail = client.get("/dev/archive/doc/1")
    assert detail.status_code == 200
    assert "반입 결과 보기" in detail.text and f'title="{long}.pdf"' in detail.text
    assert "합성 마스킹 본문" not in detail.text            # 본문 전문은 조각으로만, 원문 덤프 없음
    assert "추출·검색 자료" in detail.text and "연관 (지식 그래프)" in detail.text
    assert 'href="/graph/explore?id=doc:1"' in detail.text and 'href="/doc/1"' in detail.text
    assert client.get("/dev/archive/doc/999").status_code == 404
    doc = client.get("/doc/1").text                          # 문서 화면 「관리」 에서 관리자에게만
    assert 'href="/dev/archive/doc/1"' in doc


def test_old_data_explorer_urls_are_gone(client):
    """데이터 현황은 없앴다(10-27) — 옛 주소는 넘기지 않고 지운다. 원본 장부는 원본 관리, 문서 하나는 반입 결과 보기, 기준 문서는 RAG 공간."""
    for url in ("/dev/db", "/dev/db?tab=archive", "/dev/db?tab=docs&doc=1", "/dev/db?tab=regulation", "/dev/db?tab=chat",
                "/dev/db?table=documents", "/dev/corpus"):
        assert client.get(url, follow_redirects=False).status_code == 404, url
    assert 'href="/dev/db' not in client.get("/dev").text


def test_short_content_also_has_full_reader(client, tmp_path):
    db = Database(tmp_path / "test.db")
    ids = _seed(db)
    regulation = client.get(f"/dev/archive/doc/{ids['reg']}").text
    document = client.get(f"/dev/archive/doc/{ids['intake']}").text
    assert '<pre>휴학은 학기 단위로 신청한다.</pre>' in regulation
    assert '<pre>도장</pre>' in document
    assert 'id="dxDetail"' in regulation
    assert 'class="dx-more can"' in regulation
    assert 'aria-label="항목"' in document
    assert 'class="dx-group"' not in document
    assert 'box-shadow:inset 3px' not in document
    assert f'href="/dev/archive/doc/{ids["reg"]}"' in document          # 관련 기준 문서도 반입 결과 보기로


def test_table_chunk_preview_is_readable():
    from zzaimy.app.data_explorer import chunk_rows, table_text

    raw = '{"n_rows": 2, "n_cols": 2, "cells": [[0, 0, 1, 1, 0, "구분"], [0, 1, 1, 1, 0, "금액"], [1, 0, 1, 1, 0, "인건비"], [1, 1, 1, 1, 0, "1,200"]]}'
    assert table_text(raw) == "구분 | 금액\n인건비 | 1,200"
    assert table_text("그냥 글") is None
    row = chunk_rows([{"id": 1, "seq": 0, "kind": "table", "content": raw}])[0]
    assert row["preview"].startswith("구분 | 금액") and "n_rows" not in row["content"]


def test_empty_table_preview_is_summary_not_json():
    from zzaimy.app.data_explorer import table_text

    assert table_text('{"n_rows": 1, "n_cols": 1, "cells": [[0, 0, 1, 1, 0, ""]]}') == "빈 표(1×1)"
    assert table_text('{"other": 1}') is None


def test_grant_docs_count_grant_index_chunks(db):
    g = db.add_document("사업계획서.pdf", "/x/g.pdf", doc_type="grant")
    db.update_document(g, status="reviewed")
    db.replace_doc_chunks(g, [
        {"kind": "text", "page_no": 1, "content": "사업 목표"},
        {"kind": "table", "page_no": 1, "content": "| 항목 | 값 |"},
        {"kind": "heading", "page_no": 1, "content": "1. 개요"},
    ])
    view = dx.doc_chunks_view(db, g, None, db.get_document(g))
    assert view["n_grant"] == 2
    assert dx.doc_chunks_view(db, g, None)["n_grant"] is None


def test_ledger_tab_moved_to_archive_with_raw_values(client, tmp_path):
    """옛 「원본 장부」 탭은 원본 관리 한 곳으로(10-27) — 장부 원값은 원본 관리 줄에서 펼치고, 장부 전체는 「범위」 로."""
    from zzaimy.app import archive
    db = Database(tmp_path / "test.db")
    archive.ensure(db)
    with db._conn() as conn:
        conn.execute("INSERT INTO archive_files(rel, program_name, ext, status, dup_of, analysis, seen_at, removed_at) VALUES"
                     " (?, ?, ?, ?, ?, ?, ?, '')", ('가상사업/폴더/아주긴이름의계획서.hwp', '가상사업', 'hwp', 'auto', '가상사업/대표.hwp',
                                                    '처리 실패: 시험', '2031-01-02T03:04:05'))
        conn.execute("INSERT INTO archive_files(rel, ext, removed_at) VALUES (?, ?, '')", (archive.UPLOAD_PREFIX + "반입/원본.pdf", 'pdf'))
    page = client.get("/dev/archive?q=가상 계획서").text
    assert ">규칙으로 확정<" in page and '장부 코드 auto"' in page                  # 분류 상태는 코드 대신 이름(코드는 툴팁)
    assert "가상사업/대표.hwp" in page and "2031-01-02 03:04" in page and "반입 결과" in page and "처리 실패: 시험" in page
    assert 'class="side-searchbox"' not in page                 # 개발자 사이드바에는 빠른 찾기 상자를 두지 않는다(업무 화면 쪽은 아래 시험)
    assert "반입/원본.pdf" not in client.get("/dev/archive?q=반입").text            # 기본은 학교 원본만
    wide = client.get("/dev/archive?q=반입&scope=all").text                       # 장부 전체 — 플랫폼 사본까지
    assert "반입/원본.pdf" in wide and ">사본·부산물<" in wide and "장부 전체(사본·부산물·정리함 포함)" in wide


def test_global_search_box_keeps_query_only_on_search_page(client):
    page = client.get("/search?q=가상검색어").text
    top = page.split('class="side-searchbox"', 1)[1].split("</a>", 1)[0]
    assert "data-quick-search" in top and 'value="가상검색어"' in page      # 검색어는 검색 화면 안 입력칸에 남는다


def test_parse_note_shown_in_plain_words():
    """읽기 메모는 저장값 그대로 두고 화면에서만 쉬운 말로 — 처리기 용어(직독·추출 실패·복원)가 보이지 않는다."""
    from zzaimy.app.data_explorer import plain_parse_note

    got = plain_parse_note("글자층 직독 (구조 추출 실패, 쪽수 12) · 갈라진 낱말 14개 복원")
    assert got == "PDF 글자 그대로 읽음 (구조 읽기 실패, 12쪽) · 끊긴 낱말 14개 이음"
    assert plain_parse_note("") == ""
