"""개발자 화면의 수 이름 — 「학교 원본」과 「원본 장부 전체」를 나눠 부르고, 보관소 「대기」가 연동 점검과 같은 판단을 쓴다.

문서 반입(/dev/intake)·데이터 현황(/dev/db)·원본 보관소(/archive)가 같은 이름으로 다른 수를 보이지 않게 하는 확인.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import archive, archive_cleanup, archive_state, intake_status, metrics
from zzaimy.app.main import create_app


def _client(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter())
    return TestClient(app), app.state.db


def _put(db, rows):
    archive.ensure(db)
    with db._conn() as conn:
        for rel, ext, dup, doc_id in rows:
            conn.execute("INSERT INTO archive_files (rel, size, mtime, ext, dup_of, doc_id) VALUES (?, 10, 0, ?, ?, ?)",
                         (rel, ext, dup, doc_id))


def _audit(tmp_path, **over):
    base = {"at": "2031-02-03 04:05", "archive_files": 6, "targets": 2, "in_docbox": 2, "failed": 0, "waiting": 0, "missed": 0,
            "composition": {"처리 대상 문서": 2, "같은 파일 중복": 1, "정리함": 1, "부산물(변환 출력·웹 페이지 저장 자료)": 2},
            "missed_by_ext": {}, "missed_by_dir": {}, "failed_by_reason": {}, "stuck_docs": 0, "grant_chunks": 1,
            "embedding_index": 1, "lexical_index": 1, "grant_docs": 31234, "graph_doc_nodes": 45678, "alerts": []}
    base.update(over)
    (tmp_path / "pipeline_audit.json").write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "sync_status.json").write_text(json.dumps({"graph": {"at": "2031-02-03 04:00", "summary": "문서 38573 · 조각 1980742"}},
                                                          ensure_ascii=False), encoding="utf-8")


def test_group_rep_whose_copy_is_registered_is_not_waiting(tmp_path):
    """같은 파일 묶음의 다른 사본이 문서에 이어졌으면 대표는 「대기」가 아니다 — 연동 점검이 처리된 것으로 세는 것과 같다."""
    _, db = _client(tmp_path)
    _put(db, [("링크/계획서.hwp", "hwp", "", None), ("링크/계획서 사본.hwp", "hwp", "링크/계획서.hwp", 1),
              ("링크/보고서.hwp", "hwp", "", None)])
    expr, args = archive_state.state_sql()
    with db._conn() as conn:
        got = dict(conn.execute(f"SELECT rel, {expr} FROM archive_files", args).fetchall())
    assert got == {"링크/계획서.hwp": "excluded", "링크/계획서 사본.hwp": "registered", "링크/보고서.hwp": "waiting"}
    assert archive_state.exclusion_reason({"rel": "링크/계획서.hwp", "ext": "hwp"}) == archive_state.COVERED_REASON
    # 정리 갈래에는 들지 않는다(처리된 묶음의 대표를 정리함 후보로 내놓지 않는다)
    assert archive_cleanup.category({"rel": "링크/계획서.hwp", "ext": "hwp", "size": 10}) == ""


def test_ledger_breakdown_adds_up(tmp_path):
    _, db = _client(tmp_path)
    _put(db, [("링크/a.hwp", "hwp", "", None), ("링크/b.pdf", "pdf", "", None), ("링크/사진.jpg", "jpg", "", None),
              (archive.UPLOAD_PREFIX + "반입/c.pdf", "pdf", "", None)])
    with db._conn() as conn:
        conn.execute("INSERT INTO archive_cleanup (rel, category, at, by_user) VALUES ('링크/사진.jpg', 'photo', '', '')")
    lg = intake_status.ledger_breakdown(db, force=True)
    assert lg == {"total": 4, "originals": 2, "cleaned": 1, "platform": 1, "byproduct": 0}
    assert lg["originals"] == metrics.live(db, force=True)["values"]["archive_originals"]   # 원본 보관소 머리 숫자와 같은 정의


def test_with_commas_formats_counts_not_dates():
    assert intake_status.with_commas("문서 38573 · 조각 1980742") == "문서 38,573 · 조각 1,980,742"
    assert intake_status.with_commas("1607299/1607299") == "1,607,299/1,607,299"
    assert intake_status.with_commas("2026-10-13 22:52 원본 167,116 · 2026년") == "2026-10-13 22:52 원본 167,116 · 2026년"


def test_intake_page_names_school_originals_and_folds_graph_counts(tmp_path):
    _audit(tmp_path)
    c, db = _client(tmp_path)
    _put(db, [("링크/a.hwp", "hwp", "", None)])
    page = c.get("/dev/intake").text
    assert "학교 원본" in page
    # 처리하지 않는 파일(따로 모은 파일·변환 중간 파일)은 학교 원본 수에서 빼고 짧은 한 줄로 — DGX 파일은 그대로
    assert "처리 안 함 0 · 따로 모은 파일 0 · 변환 중간 파일 0" in page
    assert page.count('<span class="ik-school">학교 원본</span>') == 2                   # 처리 대상 문서 · 같은 파일 중복
    assert "(절대 규칙 11)" not in page and "<details" not in page and "▶" not in page   # 접기 없이 한 표
    after = page.split("<h2>라이브러리 다음 단계</h2>", 1)[1].split("</section>", 1)[0]
    assert "45,678" in after and "31,234" in after and "문서 38,573 · 조각 1,980,742" in after
    assert "38573" not in page


def test_intake_has_no_drive_tab_and_doc_flow_lives_on_overview(tmp_path):
    """단계별 문서 수는 처리 흐름 한 곳 — 문서 가져오기는 한 줄 바로가기. Drive 탭은 없다(옛 주소는 사용자·계정으로)."""
    c, _ = _client(tmp_path)
    page = c.get("/dev/intake").text
    assert 'href="/dev/flow#stage-1"' in page and 'class="df"' not in page     # 단계별 문서 수는 처리 흐름 1·3단계
    assert "tab=drive" not in page and 'id="connectedFolders"' not in page and "← 개발 현황" not in page
    r = c.get("/dev/nas?ok=저장", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/dev/users#google-links"


def test_dev_db_ledger_labels_and_dead_corpus_tab(tmp_path):
    c, db = _client(tmp_path)
    _put(db, [("링크/a.hwp", "hwp", "", 1)])
    with db._conn() as conn:
        conn.execute("UPDATE archive_files SET status = 'agent', analysis = '가벼운 처리'")
    page = c.get("/dev/db?tab=archive").text
    assert ">에이전트 검토 판정<" in page and "원본 장부 전체(부산물·정리함 포함)" in page and "학교 원본" in page
    assert '<span class="dx-help">가벼운 처리</span>' in page and "검토 의견 생성은 건너뛴" in page
    assert c.get("/dev/db?tab=corpus", follow_redirects=False).headers["location"] == "/dev/db?tab=docs"


def test_archive_cleanup_text_has_no_internal_rule_number():
    assert all("절대 규칙" not in note for _, _, note in archive_cleanup.CATEGORIES)


def test_long_extraction_list_folds_after_first_page(tmp_path):
    c, db = _client(tmp_path)
    c.post("/upload", data={"doc_type": "recruit"}, files={"file": ("긴문서.pdf", b"%PDF", "application/pdf")})
    db.replace_doc_chunks(1, [{"kind": "text", "page_no": 1, "content": f"조각 {i}"} for i in range(130)])
    page = c.get("/dev/db?tab=docs&doc=1").text
    head, more = page.split('<details class="chip-toggle dx-extract-more">', 1)
    assert "더 보기 · 나머지 30건" in more and "조각 99" in head and "조각 100" not in head and "조각 129" in more
