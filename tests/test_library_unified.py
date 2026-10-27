"""라이브러리 한 목록(ADR-0062) — 쓰임새 칩·기준 꼬리표·옛 주소 넘김·업무 탭 숨김 규칙.

모두 합성 문서다. 칩은 서류 갈래(kind)와 규정 색인 여부(doc_type)라는 일반 규칙으로 거르고, 데이터는 바꾸지 않는다.
"""
from __future__ import annotations

import re

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import library, metrics
from zzaimy.app.main import INBOX_TYPES, create_app
from zzaimy.app.project_documents import CRITERIA_KINDS


def _client(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(),
                     password="boot-pass-1")
    c = TestClient(app)
    assert c.post("/login", data={"username": "zzaimy", "pw": "boot-pass-1"}, follow_redirects=False).status_code == 303
    return app, c


def _table(page: str) -> str:
    return page.split("data-doc-list>", 1)[-1].split("<!-- /lib-rows -->", 1)[0]


def _ids(page: str) -> set[int]:
    return {int(x) for x in re.findall(r'href="/doc/(\d+)"', _table(page))}


def test_use_rule_matches_sql_for_every_kind(tmp_path):
    """쓰임새 규칙은 파이썬(use_of)과 SQL(use_sql) 이 같다 — 모든 갈래, 갈래 없음, 규정 색인 문서."""
    app, c = _client(tmp_path)
    db = app.state.db
    from zzaimy.app.doc_routing import KINDS

    want: dict[int, str] = {}
    for i, kind in enumerate([*KINDS, None]):
        for doc_type in ("grant", "regulation"):
            did = db.add_document(f"합성 문서 {i} {doc_type}.pdf", "x", doc_type=doc_type)
            db.set_document_kind(did, kind)
            want[did] = library.use_of(doc_type, kind)
    assert {k for k, v in want.items() if v == "criteria"} == _ids(c.get("/?type=all&use=criteria").text)
    assert {k for k, v in want.items() if v == "work"} == _ids(c.get("/?type=all&use=work").text)
    assert set(want) == _ids(c.get("/?type=all").text)
    assert library.use_of("regulation", "plan") == "criteria"          # 규정 색인 문서는 갈래와 무관하게 기준
    assert all(library.use_of("grant", k) == "criteria" for k in CRITERIA_KINDS)


def test_criteria_tag_marks_only_regulation_index_documents(tmp_path):
    app, c = _client(tmp_path)
    db = app.state.db
    reg = db.add_document("가상 학사 내규.pdf", "x", doc_type="regulation")
    notice = db.add_document("가상 사업 공고.pdf", "x", doc_type="grant")
    db.set_document_kind(notice, "announcement")
    db.update_document(reg, status="reviewed")                            # 처리 끝났는데 규정 조각이 없다
    page = c.get("/?type=all&use=criteria").text
    rows = {int(m.group(1)): m.group(0) for m in re.finditer(r'class="lib-row-title" href="/doc/(\d+)".*?</li>', _table(page), re.S)}
    assert set(rows) == {reg, notice}                                     # 공고 갈래 사업 문서도 기준 칩에 든다
    assert 'class="pill lib-criteria"' in rows[reg] and "lib-criteria" not in rows[notice]
    assert "규정 검색에 잡히지 않습니다" in rows[reg]
    # 칩 수치는 지표 이름(metrics.py)의 정의를 툴팁으로 단다
    for key in library.USE_METRICS.values():
        assert key in metrics.DEFS and metrics.DEFS[key].source == "view"
    assert metrics.DEFS["documents_use_criteria"].definition.split(" — ")[0] in page


def test_old_addresses_land_on_the_unified_list(tmp_path):
    _app, c = _client(tmp_path)
    for old, new in (("/criteria", "/?type=all&use=criteria"),
                     ("/?type=all&group=criteria", "/?type=all&use=criteria"),
                     ("/?type=grant&group=extract&q=a", "/?type=grant&q=a"),
                     ("/?group=reference", "/?type=all")):
        r = c.get(old, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == new, old
    assert c.get("/inbox?type=all&group=criteria").url.query == b"type=all&use=criteria"
    assert c.get("/?type=recruit").status_code == 200                      # 업무 주소는 그대로 쓴다
    page = c.get("/?type=all").text
    assert 'href="/criteria"' not in page and "원본 보관소" not in page and 'href="/archive' not in page   # 라이브러리는 문서 한 목록(원본 관리는 개발 메뉴)


def test_business_tabs_show_only_with_two_or_more_businesses(tmp_path):
    app, c = _client(tmp_path)
    db = app.state.db
    db.add_document("가상 결과보고서.pdf", "x", doc_type="grant")
    common = db.add_document("가상 공통 규정.pdf", "x", doc_type="regulation", sector="common")
    page = c.get("/?type=all").text
    assert 'aria-label="업무"' in page and '<nav class="space-links" aria-label="업무"' not in page   # 탭 줄 없음
    assert '<select name="type" aria-label="업무">' in page                 # 선택 상자에서만
    assert 'class="lib-row-biz"' not in page                                # 하나뿐이면 업무 표시도 없다
    assert common in _ids(c.get("/?type=grant").text)                       # 공통 기준은 모든 업무에 보인다
    assert library.show_business_tabs({"grant": 3, "common": 2}, INBOX_TYPES) is False
    db.add_document("가상 채용 서류.pdf", "x", doc_type="recruit")           # 다른 업무 문서가 생기면 다시 보인다
    page = c.get("/?type=all").text
    assert '<nav class="space-links" aria-label="업무"' in page and 'class="lib-row-biz"' in page
    tabs = page.split('<nav class="space-links" aria-label="업무"', 1)[1].split("</nav>", 1)[0]
    assert ">채용</a>" in tabs and ">국고사업</a>" in tabs and ">입학</a>" not in tabs   # 문서 없는 업무 탭은 없다
    assert '<select name="type"' not in page


def test_criteria_registration_uses_existing_intake_path_only(tmp_path):
    """라이브러리 목록 화면에서 기준으로 넣는 길은 기존 반입(/criteria/upload) 하나 — 목록에 doc_type 을 바꾸는 동작을 두지 않는다.
    (이미 반입된 규정을 기준 문서로 올리는 일은 같은 칩의 관리자 「새 판·미등록」 — /criteria/regs, regulation_register)"""
    app, c = _client(tmp_path)
    r = c.post("/criteria/upload", data={"sector": "grant"},
               files={"file": ("가상 지침.pdf", b"%PDF", "application/pdf")}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?type=all&use=criteria"
    doc = app.state.db.list_documents("regulation")[0]
    assert doc["sector"] == "grant"
    page = c.get("/?type=all&use=criteria").text
    forms = re.findall(r'<form action="([^"]+)" method="post"', page)
    assert "/criteria/upload" in forms and not [f for f in forms if f.startswith("/doc/")]
    paths = {getattr(rt, "path", "") for rt in app.routes}
    assert not [p for p in paths if p.startswith("/doc/") and "criteria" in p]   # 기준으로 쓰기·빼기 우회 없음
    assert 'data-modal-open="#criteriaUploadModal"' not in c.get("/?type=all").text   # 등록 단추는 기준 칩에 한 곳


def test_library_browses_by_program_and_year(tmp_path):
    """사업별로 보기 — 왼쪽 목록은 문서가 속한 프로젝트의 사업 열쇠로 묶고, 고르면 그 사업 문서만, 연도 칩으로 좁힌다."""
    app, c = _client(tmp_path)
    db = app.state.db
    with db._conn() as conn:
        p24 = conn.execute("INSERT INTO projects (sector, name, created_at, owner, archived, program) VALUES "
                           "('grant', '2024년 가상 사업', '2026-01-01', 'zzaimy', 1, 'program:gasang|2024') RETURNING id").fetchone()[0]
        p25 = conn.execute("INSERT INTO projects (sector, name, created_at, owner, archived, program) VALUES "
                           "('grant', '2025년 가상 사업', '2026-01-01', 'zzaimy', 1, 'program:gasang|2025') RETURNING id").fetchone()[0]
    a = db.add_document("가상 계획서 2024.pdf", "x", doc_type="grant", project_id=p24)
    b = db.add_document("가상 계획서 2025.pdf", "x", doc_type="grant", project_id=p25)
    loose = db.add_document("떠도는 문서.pdf", "x", doc_type="grant")
    page = c.get("/?type=all").text
    assert 'href="/?type=all&amp;use=all&amp;prog=prog%3Aprogram%3Agasang"' in page and "사업 미분류" in page
    picked = c.get("/?type=all&prog=prog:program:gasang").text
    assert _ids(picked) == {a, b} and "2024년" in picked and "2025년" in picked
    assert _ids(c.get("/?type=all&prog=prog:program:gasang&year=2025").text) == {b}
    assert _ids(c.get("/?type=all&prog=none").text) == {loose}


def test_rail_counts_follow_use_filter_and_non_rep_key(tmp_path):
    app, c = _client(tmp_path)
    db = app.state.db
    with db._conn() as conn:
        p = conn.execute("INSERT INTO projects (sector, name, created_at, owner, archived, program) VALUES "
                         "('grant', '2024년 가상', '2026-01-01', 'zzaimy', 1, 'program:gasang|2024') RETURNING id").fetchone()[0]
    a = db.add_document("가상 공고.pdf", "x", doc_type="grant", project_id=p)
    db.set_document_kind(a, "announcement")
    db.add_document("가상 계획서.pdf", "x", doc_type="grant", project_id=p)
    page = c.get("/?type=all&use=criteria").text
    rail = page.split('id="libRail"', 1)[1].split("</nav>", 1)[0]
    assert re.search(r"가상</span><small>1</small>", rail)                    # 기준 칩을 고르면 사업 목록 수도 기준만
