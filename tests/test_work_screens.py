"""업무 화면(기준 문서·프로젝트·설정·문서 보기)의 일반 규칙 — 본 적 없는 합성 사례로 확인한다."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app.main import create_app

STATIC = Path(__file__).resolve().parents[1] / "src" / "zzaimy" / "app" / "static"


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(db_path=tmp_path / "w.db", inbox_dir=tmp_path / "inbox",
                                 processor=FakeProcessor(), drafter=FakeDrafter()))


def _doc(db, name, **kw):
    did = db.add_document(name, "/tmp/none.pdf", doc_type=kw.pop("doc_type", "grant"), **kw)
    db.update_document(did, status="reviewed")
    return did


# ── 표시 규칙 ──────────────────────────────────────────────────────────────

def test_receipt_and_title_display():
    from zzaimy.app.doc_title import display_title, receipt_label

    assert receipt_label("2026-기준-0003") == "2026-기준-00003"
    assert receipt_label("2026-국고-41768") == "2026-국고-41768"
    assert receipt_label("2031-입학-7") == "2031-입학-00007"
    assert receipt_label("") == "" and receipt_label("임시번호") == "임시번호"
    assert display_title({"filename": "가상 연구소 운영 지침.hwpx"}) == "가상 연구소 운영 지침"
    assert display_title({"filename": "확장자 없는 제목"}) == "확장자 없는 제목"


def test_extract_view_drops_split_numbers_duplicate_headings_and_table_echo():
    from zzaimy.app.render import chunk_blocks

    table = json.dumps({"n_rows": 2, "n_cols": 2, "cells": [
        [0, 0, 1, 1, True, "접수 기간"], [0, 1, 1, 1, False, "3월 2일부터 3월 9일까지"],
        [1, 0, 1, 1, True, "접수 방법"], [1, 1, 1, 1, False, "전자우편 제출"]]})
    chunks = [
        {"id": 1, "kind": "heading", "content": "3 시범 운영 신청 방법", "page_no": 1},
        {"id": 2, "kind": "text", "content": "3", "page_no": 1},
        {"id": 3, "kind": "heading", "content": "시범 운영 신청 방법", "page_no": 1},
        {"id": 4, "kind": "text", "content": "접수 기간 3월 2일부터 3월 9일까지 접수 방법 전자우편 제출", "page_no": 1},
        {"id": 5, "kind": "table", "content": table, "page_no": 1},
        {"id": 6, "kind": "text", "content": "2.7. (선택지표)참여 인원 :", "page_no": 2},
        {"id": 7, "kind": "heading", "content": "4. 유의 사항", "page_no": 2},
        {"id": 8, "kind": "text", "content": "제출할 서류는 다음과 같다:", "page_no": 2},
        {"id": 9, "kind": "text", "content": "가. 신청서 1부", "page_no": 2},
    ]
    html = "".join(str(b) for b in chunk_blocks(chunks, 7, {}))
    assert html.count("시범 운영 신청 방법") == 1                  # 번호만 다른 같은 제목은 한 번
    assert '<p class="extract-p" data-kind="text" data-page="1">3</p>' not in html
    assert html.count("전자우편 제출") == 1                         # 표를 평문과 표로 두 번 그리지 않는다
    assert "참여 인원" not in html                                   # 값 없는 번호 항목(다음이 제목)
    assert "제출할 서류는 다음과 같다:" in html                     # 번호 없는 안내 문장은 남는다
    assert "표 내려받기(CSV)" in html and "table-dl" in html


# ── 기준 문서 화면 ─────────────────────────────────────────────────────────

def test_criteria_page_script_is_scoped_and_filters_have_counts(client):
    db = client.app.state.db
    a = _doc(db, "가상 공고문.pdf", doc_type="regulation")
    db.set_document_kind(a, "announcement")
    b = _doc(db, "가상 내규.pdf", doc_type="regulation")
    db.set_document_kind(b, None)
    c_ = db.add_document("처리중 기준.pdf", "/tmp/x.pdf", doc_type="regulation")   # 접수 상태 그대로
    page = client.get("/criteria").text
    script = page.split('<script>\n(function () {', 1)
    assert len(script) == 2                                            # 화면 스크립트는 함수로 감싼다
    assert "var status " not in page and "var status=" not in page
    assert '<option value="_none">분류 전 2</option>' in page
    assert '<option value="announcement">공고 1</option>' in page
    assert '<option value="processing">처리 중 1</option>' in page and '<option value="reviewed">등록 완료 2</option>' in page
    assert f'data-status="processing"' in page and c_
    assert "검색 자료" not in page and "적용 영역" not in page
    assert "<h1>" in page and page.count("<h1") == 1
    assert 'type="submit" disabled>등록</button>' in page          # 파일을 고르기 전에는 등록을 끈다
    assert "sel.selectedIndex = 0" in page                            # 초기화는 모든 필터 칸을 되돌린다


# ── 공고 갈래 → 프로젝트 기준 문서 ─────────────────────────────────────────

def test_announcement_kind_documents_become_project_criteria(client):
    from zzaimy.app import project_documents

    db = client.app.state.db
    pid = db.create_project("grant", "가상 실습 사업")
    notice = db.add_document("모집 안내.pdf", "/tmp/a.pdf", doc_type="grant", project_id=pid)
    form = db.add_document("신청 양식.hwpx", "/tmp/b.hwpx", doc_type="grant", project_id=pid)
    db.set_document_kind(notice, "announcement")
    db.set_document_kind(form, "form")
    sid = db.create_chat_session("질문", project_id=pid)
    att = db.add_document("대화 첨부 지침.pdf", "/tmp/c.pdf", doc_type="grant")
    db.add_file("attachment", "/tmp/c.pdf", name="대화 첨부 지침.pdf", session_id=sid, doc_id=att)
    db.set_document_kind(att, "guideline")
    assert project_documents.link_criteria_kind(db, notice) == [pid]
    assert project_documents.link_criteria_kind(db, form) == []
    assert project_documents.link_criteria_kind(db, att) == [pid]
    assert project_documents.link_criteria_kind(db, notice) == []      # 이미 이어져 있으면 그대로
    groups = project_documents.collect(db, db.get_project(pid))
    assert {d["id"] for d in groups["rules"]} == {notice, att}
    assert [d["id"] for d in groups["attached"]] == [form]
    page = client.get(f"/project/{pid}").text
    assert "공고·지침·평가 기준으로 분류된 문서는 기준 문서로 연결됩니다" in page


def test_archive_origin_is_shown_as_source(client):
    from zzaimy.app import project_documents

    db = client.app.state.db
    pid = db.create_project("grant", "지난 묶음", archived=True)
    db.add_document("옛 결과보고.pdf", "dgx://사업/옛 결과보고.pdf", doc_type="grant", project_id=pid)
    groups = project_documents.collect(db, db.get_project(pid))
    assert groups["attached"][0]["via"] == "원본 보관소에서 가져옴"
    page = client.get(f"/project/{pid}").text
    assert "지난 사업</span>" in page and "보관 해제" in page
    assert 'name="question"' not in page                               # 지난 사업에서는 새 대화·문서 추가를 띄우지 않는다


# ── 자동 연결은 근거가 있을 때만 ───────────────────────────────────────────

def test_suggested_criteria_need_same_project_or_program(client):
    from zzaimy.app.regulations import criteria_link_evidence, evidenced_suggestions

    db = client.app.state.db
    pid = db.create_project("grant", "가상 사업 A")
    doc = db.add_document("A 운영 안내.pdf", "/tmp/a.pdf", doc_type="grant", project_id=pid)
    same_proj = _doc(db, "A 지침.pdf", doc_type="regulation")
    db.add_project_criteria(pid, [same_proj])
    same_prog = _doc(db, "A 기본계획.pdf", doc_type="regulation")
    other = _doc(db, "전혀 다른 사업 기본계획.pdf", doc_type="regulation")
    db.set_doc_identity(doc, {"program": "가상 미래 인재 양성 사업"})
    db.set_doc_identity(same_prog, {"program": "가상 미래인재양성사업 (2027)"})
    db.set_doc_identity(other, {"program": "다른 지역 혁신 사업"})
    d = db.get_document(doc)
    assert criteria_link_evidence(db, d, same_proj) == "같은 프로젝트"
    assert criteria_link_evidence(db, d, same_prog) == "같은 사업"
    assert criteria_link_evidence(db, d, other) == ""
    got = evidenced_suggestions(db, d, [{"id": other, "title": "다른"}, {"id": same_prog, "title": "A 기본계획"}])
    assert [g["id"] for g in got] == [same_prog]
    db.update_document(doc, suggested_criteria=json.dumps([{"id": other, "title": "전혀 다른 사업 기본계획"}], ensure_ascii=False))
    page = client.get(f"/doc/{doc}").text
    assert "전혀 다른 사업 기본계획" not in page                          # 근거 없는 추천은 보이지 않는다


# ── 관련 지난 사업 후보 ───────────────────────────────────────────────────

def test_archived_candidates_use_criteria_titles_and_program_names(client):
    from zzaimy.app import project_refs

    db = client.app.state.db
    pid = db.create_project("grant", "새 분야 계획서")
    past = db.create_project("grant", "2024년 가상 해양 드론 실증 사업", archived=True)
    db.add_document("옛 보고서.pdf", "/tmp/o.pdf", doc_type="grant", project_id=past)
    proj = db.get_project(pid)
    assert project_refs.candidates(db, proj) == []                     # 프로젝트 이름만으로는 없다
    got = project_refs.candidates(db, proj, names=["가상 해양드론 실증사업"])
    assert [c["id"] for c in got] == [past] and "일치" in got[0]["why"]
    weak = project_refs.candidates(db, proj, names=["해양 관련 공고 제2026-1호 안내 및 서식 일체"])
    assert weak == []                                                  # 조각 한두 개 우연 겹침은 후보가 아니다


# ── 내 설정: 자동 가져오기 멈춤 ───────────────────────────────────────────

def test_gimport_stall_rule_uses_interval(monkeypatch):
    from zzaimy.app import gdrive_import as gi

    monkeypatch.setattr(gi, "INTERVAL", 300)
    now = datetime(2031, 5, 1, 12, 0)
    assert gi.stalled({"at": (now - timedelta(minutes=14)).strftime("%Y-%m-%d %H:%M")}, now) is False
    assert gi.stalled({"at": (now - timedelta(minutes=16)).strftime("%Y-%m-%d %H:%M")}, now) is True
    assert gi.stalled({}, now) is False and gi.stalled({"at": "모름"}, now) is False
    assert gi.interval_minutes() == 5


def test_settings_warns_when_import_stalled_and_account_google_redirects(tmp_path, monkeypatch):
    from zzaimy.ingest import gdocs, gdrive

    monkeypatch.setattr(gdrive, "list_accounts", lambda: [{"email": "staff@ync.ac.kr", "granted_at": "", "scopes": []}])
    monkeypatch.setattr(gdocs, "has_docs_scope", lambda email: True)
    monkeypatch.setattr(gdrive, "configured", lambda: True)
    c = TestClient(_app(tmp_path))
    assert _login(c, "zzaimy", "boot-pass-1")
    db = c.app.state.db
    db.set_setting("google_account:zzaimy", "staff@ync.ac.kr")
    old = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
    db.set_setting("gimport_status:zzaimy", json.dumps({"at": old}))
    page = c.get("/settings").text
    assert "자동 가져오기가 멈췄습니다" in page and "지금 가져오기" in page
    assert "RAG" not in page
    r = c.get("/account/google", headers={"accept": "text/html,application/xhtml+xml"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings#myGoogle"
    assert c.get("/account/google").json()["bound"] == "staff@ync.ac.kr"   # 화면이 아닌 요청은 그대로 JSON


# ── 되돌리기 어려운 동작은 확인 ───────────────────────────────────────────

def test_irreversible_forms_carry_confirmation(client):
    db = client.app.state.db
    pid = db.create_project("grant", "확인 검사")
    db.add_project_note(pid, "원칙")
    page = client.get(f"/project/{pid}").text
    for action in (f"/project/{pid}/archive", f"/projects/{pid}/delete", f"/project/{pid}/notes/1/delete"):
        form = page.split(f'action="{action}"', 1)[1].split(">", 1)[0]
        assert "data-confirm=" in form, action
    js = (STATIC / "workspace-navigation.js").read_text(encoding="utf-8")
    assert "'confirm' in form.dataset" in js and "ownConfirm" in js


def test_criteria_auto_link_skips_archive_bundles_and_foreign_documents(client):
    from zzaimy.app import project_documents

    db = client.app.state.db
    bundle = db.create_project("grant", "가상 지난 사업 (연도 미상)", owner="zzdev", archived=True, archive_source="dgx")
    plain_archived = db.create_project("grant", "담당자가 보관한 사업", archived=True)
    user = db.create_project("grant", "담당자 진행 프로젝트", owner="kim")
    in_bundle = db.add_document("옮겨 온 공고.pdf", "dgx://사업/옮겨 온 공고.pdf", doc_type="grant", project_id=bundle)
    in_archived = db.add_document("보관된 공고.pdf", "/tmp/k.pdf", doc_type="grant", project_id=plain_archived)
    mine = db.add_document("내가 올린 공고.pdf", "/tmp/m.pdf", doc_type="grant", project_id=user, owner="kim")
    moved = db.add_document("보관소 원본 지침.pdf", "dgx://사업/지침.pdf", doc_type="grant", project_id=user, owner="kim")
    foreign = db.add_document("다른 사람이 넣은 규정.pdf", "/tmp/f.pdf", doc_type="grant", project_id=user, owner="system")
    for d in (in_bundle, in_archived, mine, moved, foreign):
        db.set_document_kind(d, "announcement")
    assert project_documents.link_criteria_kind(db, in_bundle) == []
    assert project_documents.link_criteria_kind(db, in_archived) == []
    assert project_documents.link_criteria_kind(db, moved) == []          # 원본 보관소에서 옮겨 온 문서
    assert project_documents.link_criteria_kind(db, foreign) == []        # 담당자가 올리지 않은 문서
    plan = project_documents.criteria_kind_backlog(db)
    assert [(r["project_id"], r["doc_id"]) for r in plan["link"]] == [(user, mine)]
    assert project_documents.link_criteria_kind(db, mine) == [user]


def test_split_numbering_lines_collapse_but_numbers_stay():
    from zzaimy.app.render import table_html
    from zzaimy.app.text_repair import collapse_split_numbering

    box = "0 시범 신청 방법\n0\n시범 신청 방법\n가. 접수 기간"
    assert collapse_split_numbering(box) == "0 시범 신청 방법\n가. 접수 기간"
    assert collapse_split_numbering("3 운영 계획\n운영 계획\n- 세부") == "3 운영 계획\n- 세부"
    assert collapse_split_numbering("Ⅱ 추진 체계\nⅡ\n추진 체계") == "Ⅱ 추진 체계"
    keep = "1 첫째 항목\n2 둘째 항목\n○\n담당"                      # 겹치지 않으면 그대로(표의 ○ 표시 칸 포함)
    assert collapse_split_numbering(keep) == keep
    html = str(table_html(json.dumps({"n_rows": 1, "n_cols": 1, "cells": [[0, 0, 1, 1, False, box]]})))
    assert html.count("시범 신청 방법") == 1


def test_library_tab_names_come_from_one_place(client):
    page = client.get("/criteria").text
    assert page.count(">등록 문서</a>") == 2                         # 탭과 안내문이 같은 이름
    templates = Path(__file__).resolve().parents[1] / "src" / "zzaimy" / "app" / "templates"
    for f in templates.glob("*.html"):
        assert "「프로젝트·문서」" not in f.read_text(encoding="utf-8"), f.name


def test_zero_bullet_and_empty_field_headings():
    from zzaimy.app.render import chunk_blocks
    from zzaimy.app.text_repair import drop_zero_bullet, normalize_numbering

    assert drop_zero_bullet("0 시범 신청 방법") == "시범 신청 방법"          # 0 으로 읽힌 글머리
    assert drop_zero_bullet("○ 운영 계획 수립") == "운영 계획 수립"
    assert drop_zero_bullet("0 원") == "0 원" and drop_zero_bullet("0 건수") == "0 건수"   # 값인 줄은 그대로
    assert drop_zero_bullet("1 사업신청 방법") == "1 사업신청 방법"          # 절 번호는 그대로
    assert normalize_numbering("0 시범 신청 방법\n0\n시범 신청 방법\n가. 기간") == "시범 신청 방법\n가. 기간"
    html = "".join(str(b) for b in chunk_blocks([
        {"id": 1, "kind": "heading", "content": "2.4. (선택지표)참여 인원 :", "page_no": 1},
        {"id": 2, "kind": "table", "content": json.dumps({"n_rows": 1, "n_cols": 1, "cells": [[0, 0, 1, 1, False, "지표명"]]}), "page_no": 1},
        {"id": 3, "kind": "heading", "content": "0 추진 체계", "page_no": 1},
    ], 3, {}))
    assert '<h4 class="extract-h"' in html and "(선택지표)참여 인원 :</p>" in html   # 빈 양식 칸 제목은 목차(h4)에 올리지 않는다
    assert ">추진 체계</h4>" in html


def test_archived_candidates_compare_words_not_jamo_and_skip_self(client):
    import unicodedata

    from zzaimy.app import project_refs

    db = client.app.state.db
    pid = db.create_project("grant", unicodedata.normalize("NFD", "가상 해양 드론"))     # 맥 파일 이름처럼 자모가 풀린 이름
    past = db.create_project("grant", "가상 해양드론 실증 사업", archived=True)
    db.add_document("옛 보고서.pdf", "/tmp/o.pdf", doc_type="grant", project_id=past)
    got = project_refs.browse(db, None, status="all", for_project=pid)
    ids = [it["id"] for it in got["items"]]
    assert pid not in ids                                              # 연결하려는 프로젝트 자신은 후보가 아니다
    whys = [it["why"] for it in got["items"] if it["why"]]
    assert all(not any("\u1100" <= ch <= "\u11ff" for ch in w) for w in whys)   # 자모 조각이 까닭에 나오지 않는다
    cand = project_refs.candidates(db, db.get_project(pid), names=["가상 해양드론 실증 사업 공고"])
    assert [c["id"] for c in cand] == [past]
