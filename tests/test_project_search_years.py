"""프로젝트 검색의 수행 연도 — 본문 연혁 숫자(설립·개교)를 수행 연도로 쓰지 않고, 연도 필터에는 연도만(연차는 따로)."""
from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from zzaimy.app import archive as ar
from zzaimy.app import project_refs
from zzaimy.app.db import Database
from zzaimy.graph import programs as P


def test_body_year_skips_history_phrases_and_implausible_years():
    assert P.body_year("본교는 1968년 개교한 이래 반려동물지원센터 민간위탁 사업을") is None
    assert P.body_year("1968년 설립 · 2024년도 반려동물지원센터 민간위탁 사업 계획") == 2024
    assert P.body_year("○○센터는 2005년 개원하였으며 2023학년도 운영 결과") == 2023
    assert P.body_year("연혁: 2010년 산학협력단 출범") is None
    assert P.body_year("협약 기업(2012년 창립) 현황") is None
    assert P.body_year("1999년 자료 정리") is None                       # 2000년 앞은 본문 근거로 받지 않는다
    assert P.body_year("2025년 3월 1일 시행") is None                    # 날짜는 수행 연도 표기가 아니다
    assert P.body_year("2025년도 사업계획서") == 2025


def test_classify_does_not_take_founding_year_from_head():
    card = P.ProgramCard(key="pet")
    card.names["반려동물지원센터 민간위탁 사업"] = 3
    docs = [{"id": 1, "filename": "반려동물지원센터 민간위탁 사업 제안서.hwp", "path": "",
             "head": "영남이공대학교는 1968년 개교 이래 반려동물지원센터 민간위탁 사업을 수행"}]
    got = P.classify(docs, [card])[0]
    assert got.year is None and got.year_src == ""


def test_folder_year_beats_body_year():
    x = P.Assignment(doc_id=1, program="program:x", program_name="X", year=2019, status="auto")
    x.year_src = "head"
    P.fill_period([{"path": "사업/2023년 자료", "filename": "계획.hwp"}], [x], {})
    assert x.year == 2023 and x.year_src == "path"
    y = P.Assignment(doc_id=2, program="program:x", program_name="X", year=2021, status="auto")
    y.year_src = "title"                                                    # 파일 이름 연도는 그대로
    P.fill_period([{"path": "사업/2023년 자료", "filename": "2021년 계획.hwp"}], [y], {})
    assert y.year == 2021


def test_bundle_key_ignores_implausible_year():
    assert ar.bundle_key("program:pet", 1968, None) == ("program:pet|?", "{name} (연도 미상)")
    assert ar.bundle_key("program:pet", "1990", "2") == ("program:pet|r2", "{name} 2차년도")
    assert ar.bundle_key("program:pet", 2024, None) == ("program:pet|2024", "2024년 {name}")


def test_align_renames_bundle_with_founding_year(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "SPLIT_MIN", 1)
    db = Database(tmp_path / "t.db")
    old = db.create_project("grant", "1968년 반려동물지원센터 민간위탁 사업", owner="zzdev", archived=True,
                            program="program:pet|1968", archive_source="dgx")
    d = db.add_document("a.hwp", "dgx://p/a.hwp", doc_type="grant", project_id=old)
    ar.load(db, [dict(rel="p/a.hwp", size=1, mtime=1, program="program:pet", program_name="반려동물지원센터 민간위탁 사업",
                      year=1968)], {})
    got = ar.align_archived_projects(db)
    assert got == {"moved": 1, "removed_projects": 1}
    p = db.get_project(db.get_document(d)["project_id"])
    assert p["name"] == "반려동물지원센터 민간위탁 사업 (연도 미상)" and p["program"] == "program:pet|?"


def _bundles(db):
    made = {}
    for name, key in [("2024년 가 사업 (2차년도)", "program:a|2024"), ("가 사업 3차년도", "program:a|r3"),
                      ("나 사업 2차년도", "program:b|r2"), ("1968년 다 사업", "program:c|1968"),
                      ("라 사업 (연도 미상)", "program:d|?")]:
        pid = db.create_project("grant", name, owner="zzdev", archived=True, program=key)
        for i in range(1 + len(made)):
            db.add_document(f"{key}{i}.hwp", f"dgx://{key}/{i}.hwp", doc_type="grant", project_id=pid)
        made[key] = pid
    return made


def test_browse_years_are_years_only_and_rounds_separate(tmp_path):
    db = Database(tmp_path / "t.db")
    made = _bundles(db)
    got = project_refs.browse(db, None, status="archived")
    assert got["years"] == ["2025", "2024"]                                   # 가 사업 3차년도 → 시작 2023 + 2 = 2025(환산)
    assert all(y.isdigit() and len(y) == 4 for y in got["years"])
    assert got["rounds"] == ["r2"] and got["has_unknown"] is True              # 나 사업은 시작 연도를 몰라 연차로만
    by = {it["id"]: it for it in got["items"]}
    assert by[made["program:a|r3"]]["year"] == "2025" and by[made["program:a|r3"]]["year_derived"] is True
    assert by[made["program:c|1968"]]["year"] == "" and by[made["program:c|1968"]]["when"] == "연도 미상"
    assert [it["id"] for it in project_refs.browse(db, None, year="r2")["items"]] == [made["program:b|r2"]]
    assert [it["id"] for it in project_refs.browse(db, None, year="2차년도")["items"]] == [made["program:b|r2"]]
    assert {it["id"] for it in project_refs.browse(db, None, year="none")["items"]} == {made["program:c|1968"], made["program:d|?"]}
    narrowed = project_refs.browse(db, None, year="2024")
    assert [it["id"] for it in narrowed["items"]] == [made["program:a|2024"]]
    assert narrowed["years"] == ["2025", "2024"]                                # 연도를 골라도 다른 연도 선택지는 남는다


def test_browse_sort_and_grouping(tmp_path):
    db = Database(tmp_path / "t.db")
    made = _bundles(db)
    docs = project_refs.browse(db, None, sort="docs")["items"]
    assert [it["n_docs"] for it in docs] == sorted((it["n_docs"] for it in docs), reverse=True)
    names = project_refs.browse(db, None, sort="name")["items"]
    assert [it["group_name"] for it in names] == sorted(it["group_name"] for it in names)
    assert project_refs.browse(db, None, sort="엉뚱한값")["sort"] == "year"
    groups = project_refs.group_items(project_refs.browse(db, None)["items"])
    ga = next(g for g in groups if g["key"] == "program:a")
    assert ga["name"] == "가 사업" and len(ga["rows"]) == 2 and ga["span"] == "2024~2025" and ga["n_docs"] == 1 + 2
    assert project_refs.browse(db, None, q="사업 가")["total"] == 2              # 낱말마다(순서 무관)


def test_archived_page_filters_chips_and_groups(tmp_path):
    app = _app(tmp_path)
    db = app.state.db
    made = _bundles(db)
    client = TestClient(app)
    assert _login(client, "zzaimy", "boot-pass-1")
    page = client.get("/projects/archived")
    assert page.status_code == 200
    html = page.text
    assert '<optgroup label="수행 연도">' in html and '<option value="2024"' in html
    assert '<option value="r2"' in html and "2차년도 (연도 미상)" in html
    assert '<option value="1968"' not in html and "1968년 다 사업" in html    # 이름은 다음 동기화의 맞춤에서 바뀐다
    assert 'class="browser-group"' in html and "가 사업" in html and "연도별 묶음 <strong>5</strong>개" in html
    assert 'id="browserSort"' in html and "browser-chip" not in html.split('id="browserLive"')[1].split("browser-meta")[0]
    page = client.get("/projects/archived?year=r2&q=나&sort=docs")
    assert 'data-clear="year"' in page.text and "2차년도 (연도 미상)" in page.text and 'data-clear="q"' in page.text
    assert 'href="/projects/archived?q=%EB%82%98&amp;sort=docs"' in page.text   # 연도 칩을 지우는 링크(스크립트 없이도)
    assert '<option value="docs" selected>' in page.text
    assert "관련도순" not in page.text                                           # 참조 연결 화면에서만
    assert 'class="side-label-action active"' in page.text                       # 사이드바 「보관된 사업」 버튼
