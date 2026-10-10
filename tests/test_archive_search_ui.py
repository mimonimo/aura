"""DGX 원본 보관소 「원본 찾기」 — 조건(사업 이름·연도·연차·형식·연결)·정렬·쪽 넘김·조건 칩·결과 칸만 다시 그리기."""

import re

import pytest
from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from zzaimy.app import archive, archive_view


def _rows():
    rows = [
        {"rel": "링크/LINC3.0 모음/2022/1차년도/계획서/LINC 사업계획서.hwp", "size": 2_500_000, "mtime": 1, "ext": "hwp",
         "program": "program:linc30", "program_name": "LINC3.0 육성사업", "kind": "plan", "year": 2022, "round": 1},
        {"rel": "링크/LINC3.0 모음/2023/2차년도/보고/LINC 실적보고서.pdf", "size": 900, "mtime": 1, "ext": "pdf",
         "program": "program:linc30", "program_name": "LINC3.0 육성사업", "kind": "report", "year": 2023, "round": 2},
        {"rel": "앵커/RISE/공고 100%_안내.pdf", "size": 40, "mtime": 1, "ext": ".PDF",
         "program": "program:rise", "program_name": "RISE사업", "kind": "notice", "year": 2024},
        {"rel": "기타/분류 전/메모.txt", "size": 10, "mtime": 1, "ext": "txt"},
    ]
    rows += [{"rel": f"앵커/RISE/사진/{i:03d}.jpg", "size": i, "mtime": 1, "ext": "jpg",
              "program": "program:rise", "program_name": "RISE사업", "kind": ""} for i in range(60)]
    return rows


@pytest.fixture
def client(tmp_path):
    app = _app(tmp_path)
    archive.load(app.state.db, _rows(), origins={"링크/LINC3.0 모음/2022/1차년도/계획서/LINC 사업계획서.hwp": 7})
    c = TestClient(app)
    assert _login(c, "zzdev", "devpass")
    return c


def _names(html: str) -> list[str]:
    return re.findall(r'<span class="ar-name" title="[^"]*">([^<]+)</span>', html)


def test_program_picker_is_searchable_and_accepts_name_or_key(client):
    page = client.get("/archive").text
    assert 'list="arPrograms"' in page and '<option value="LINC3.0 육성사업">' in page
    assert '<select name="program"' not in page and 'name="program" value=""' in page   # 미분류(빈 열쇠)가 기본값으로 들어가지 않게
    by_key = _names(client.get("/archive?program=program:linc30").text)
    assert by_key == _names(client.get("/archive?program=LINC3.0 육성사업").text)
    assert by_key == _names(client.get("/archive?program=linc").text)          # 이름 일부·대소문자 무관
    assert sorted(by_key) == ["LINC 사업계획서.hwp", "LINC 실적보고서.pdf"]
    assert 'value="LINC3.0 육성사업"' in client.get("/archive?program=program:linc30").text   # 열쇠로 와도 칸에는 이름
    assert "조건에 맞는 파일이 없습니다" in client.get("/archive?program=없는사업").text


def test_quick_filters_and_literal_like(client):
    assert _names(client.get("/archive?year=2023&q=linc").text) == ["LINC 실적보고서.pdf"]
    assert _names(client.get("/archive?round=1&q=linc").text) == ["LINC 사업계획서.hwp"]
    assert sorted(_names(client.get("/archive?ext=pdf").text)) == ["LINC 실적보고서.pdf", "공고 100%_안내.pdf"]
    assert _names(client.get("/archive?linked=yes").text) == ["LINC 사업계획서.hwp"]
    assert "LINC 사업계획서.hwp" not in client.get("/archive?linked=no&q=linc").text
    assert _names(client.get("/archive?q=100%25_").text) == ["공고 100%_안내.pdf"]   # %·_ 는 글자 그대로
    assert _names(client.get("/archive?q=1_0").text) == []
    assert _names(client.get("/archive?q=실적 linc").text) == ["LINC 실적보고서.pdf"]   # 여러 낱말은 모두 포함


def test_sort_count_and_pagination(client):
    first = client.get("/archive?program=program:rise").text
    assert "<strong>61</strong>개 중 1–50" in first and "1 / 2쪽" in first
    assert len(_names(first)) == 50 and "page=2" in first and "최대 200개" not in first
    second = client.get("/archive?program=program:rise&page=2").text
    assert len(_names(second)) == 11 and "51–61" in second
    assert len(_names(client.get("/archive?program=program:rise&page=99").text)) == 11   # 넘친 쪽은 마지막 쪽
    by_size = _names(client.get("/archive?q=linc&sort=size").text)
    assert by_size == ["LINC 사업계획서.hwp", "LINC 실적보고서.pdf"]
    by_year = _names(client.get("/archive?q=linc&sort=year").text)
    assert by_year == ["LINC 실적보고서.pdf", "LINC 사업계획서.hwp"]


def test_chips_remove_one_condition_and_keep_others(client):
    page = client.get("/archive?q=linc&year=2023&sort=size").text
    chips = dict(re.findall(r'<a class="ar-chip" href="([^"]+)" data-clear="(\w+)"', page))
    by_key = {v: k.replace("&amp;", "&") for k, v in chips.items()}
    assert by_key["year"] == "/archive?q=linc&sort=size#archive-search"
    assert by_key["q"] == "/archive?year=2023&sort=size#archive-search"
    assert "2023년" in page and "모두 지우기" in page


def test_result_row_layout(client):
    page = client.get("/archive?q=사업계획서").text
    row = page[page.index('<li class="ar-row">'):page.index("</li>", page.index('<li class="ar-row">'))]
    assert 'data-ext="hwp"' in row and ">HWP<" in row
    assert "… / 2022 / 1차년도 / 계획서" in row                                    # 마지막 폴더 셋
    assert 'title="링크/LINC3.0 모음/2022/1차년도/계획서/LINC 사업계획서.hwp"' in row  # 전체 경로는 툴팁·펼침
    assert "LINC3.0 육성사업" in row and "계획서</span>" in row and "1차년도</span>" in row
    assert "2.5 MB" in row and ">열기</a>" in row and 'href="/doc/7"' in row and "경로 복사" in row
    assert 'href="/archive/original?rel=' in row


def test_partial_returns_only_results(client):
    part = client.get("/archive?q=linc&partial=1").text
    assert part.lstrip().startswith('<div class="ar-resbar">') and "<html" not in part
    assert "LINC 실적보고서.pdf" in part and "사업별 보관 현황" not in part
    assert "partial" not in part                                                   # 공유 주소에 남기지 않는다


def test_staff_cannot_open_unlinked_and_hidden_docs_excluded(client, monkeypatch):
    assert _login(client, "zzaimy", "boot-pass-1")
    page = client.get("/archive?q=메모").text
    assert "열람 권한 필요" in page and 'href="/archive/original' not in page
    from zzaimy.app import access_policy
    monkeypatch.setattr(access_policy, "visible", lambda *a, **kw: False)
    client.app.state.db.add_document("x.hwp", "dgx://x", owner="other")
    hidden = archive_view._hidden_docs(client.app.state.db, {"dept": None, "user": "zzaimy", "role": "staff"})
    assert hidden
    with client.app.state.db._conn() as conn:
        conn.execute("UPDATE archive_files SET doc_id = ? WHERE doc_id = 7", (min(hidden),))
    page = client.get("/archive?q=linc").text
    assert _names(page) == ["LINC 실적보고서.pdf"] and "<strong>1</strong>개" in page


def test_short_path():
    assert archive_view.short_path("a/b/c/d/e.pdf") == "… / b / c / d"
    assert archive_view.short_path("a/e.pdf") == "a"
    assert archive_view.short_path("e.pdf") == ""
