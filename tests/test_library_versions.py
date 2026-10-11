"""라이브러리 판본 묶음·쪽 나눔·옛 주소·공통 양식 사본 — 오늘 데이터 이름이 아니라 판 꼬리 모양의 일반 규칙으로."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app.library import copy_url, group_ids, page_numbers, version_key
from zzaimy.app.main import create_app


def test_version_key_strips_only_version_tails():
    same = ["현장실습 운영 결과_ver2.hwp", "현장실습 운영 결과_ver10.1.pdf", "현장실습 운영 결과 (최종).hwpx",
            "현장실습 운영 결과_수정본2.docx", "현장실습 운영 결과 (1).pdf", "현장실습 운영 결과(박철수 수정).pptx",
            "현장실습 운영 결과 v3", "현장실습 운영 결과"]
    assert len({version_key(n) for n in same}) == 1
    # 판 꼬리가 아닌 번호·낱말은 남긴다 — 다른 문서를 한 줄로 숨기지 않게
    assert version_key("운영위원회 회의록(1)") != version_key("운영위원회 회의록(2)")
    assert version_key("IPv6 전환 계획") != version_key("IPv4 전환 계획")
    assert version_key("학칙 수정") != version_key("학칙")
    assert version_key("(최종)") and version_key("v2")                      # 다 떼어 비면 원래 이름


def test_group_ids_keeps_latest_first_and_separates_doc_types():
    rows = [(9, "grant", "안내_ver3"), (8, "grant", "다른 문서"), (7, "regulation", "안내"), (5, "grant", "안내 (1)"), (2, "grant", "안내")]
    assert group_ids(rows) == [[9, 5, 2], [8], [7]]
    assert group_ids([(4, "grant", ""), (3, "grant", "")]) == [[4], [3]]   # 이름 없는 문서는 따로


def test_page_numbers_and_copy_url():
    assert page_numbers(1, 0) == [] and page_numbers(1, 1) == [1]
    assert page_numbers(50, 120) == [1, None, 48, 49, 50, 51, 52, None, 120]
    assert copy_url("https://docs.google.com/document/d/AbC-9_x/edit?usp=drivesdk") == "https://docs.google.com/document/d/AbC-9_x/copy"
    assert copy_url("https://docs.google.com/spreadsheets/d/K1/view") == "https://docs.google.com/spreadsheets/d/K1/copy"
    assert copy_url("https://example.org/a") == "" and copy_url("") == ""


def _client(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(),
                     password="boot-pass-1")
    c = TestClient(app)
    assert c.post("/login", data={"username": "zzaimy", "pw": "boot-pass-1"}, follow_redirects=False).status_code == 303
    return app, c


def test_library_shows_latest_version_with_count(tmp_path):
    app, c = _client(tmp_path)
    db = app.state.db
    old = db.add_document("장학 운영 보고_ver1.hwp", "x", doc_type="grant")
    mid = db.add_document("장학 운영 보고_ver2.hwp", "x", doc_type="grant")
    new = db.add_document("장학 운영 보고 (최종).hwp", "x", doc_type="grant")
    other = db.add_document("기숙사 안내.pdf", "x", doc_type="grant")
    page = c.get("/?type=all").text
    table = page.split('class="intake-table"', 1)[1].split("</table>", 1)[0]
    assert "판본 3개" in table and table.count("<tr>") == 3                   # 머리 줄 + 묶음 2줄
    assert table.index(f'href="/doc/{other}"') < table.index(f'href="/doc/{new}"')   # 묶음 순서는 최신 판 기준
    versions = table.split("판본 3개", 1)[1]
    assert f'href="/doc/{mid}"' in versions and f'href="/doc/{old}"' in versions
    assert "문서 4건" in page and "판본 2건은 최신본 아래에 묶음" in page


def test_inbox_redirects_to_library_keeping_filters(tmp_path):
    _app, c = _client(tmp_path)
    r = c.get("/inbox", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?type=all"
    r = c.get("/inbox?type=recruit&q=이력서", follow_redirects=False)
    assert r.headers["location"].startswith("/?type=recruit&q=")


def test_library_hides_empty_groups_and_flags_unknown_business(tmp_path):
    app, c = _client(tmp_path)
    app.state.db.add_document("공문.hwp", "x", doc_type="grant")
    page = c.get("/?type=all").text
    group = page.split('name="group"', 1)[1].split("</select>", 1)[0]
    assert "업무 문서 1" in group and "본문에서 나온 문서" not in group      # 0건 출처는 고를 수 없다
    assert "「admin」 업무는 없습니다" in c.get("/?type=admin").text
    assert "공통 양식" not in page                                            # 양식 바로가기는 그 업무 탭에만
