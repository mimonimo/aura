"""원본 보관소 — 사업 → 사업 연도 → 문서 종류 훑기, 겹치는 조건(사업·연도·종류·형식·처리 상태·검색), 쪽 넘김, 표시 이름.

사업 이름은 합성 이름이다(규칙이 특정 사업에 묶이지 않았는지 본다)."""

import re

import pytest
from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from zzaimy.app import archive, archive_state, archive_view

LINKED = "은하수/2024/1차년도/계획서/은하수_개척_사업계획서.hwp"


def _rows():
    rows = [
        {"rel": LINKED, "size": 2_500_000, "mtime": 1, "ext": "hwp", "status": "auto",
         "program": "program:a", "program_name": "은하수 개척 사업", "kind": "plan", "year": 2024, "round": 1},
        {"rel": "은하수/2025/2차년도/보고/은하수 개척 결과보고서.pdf", "size": 900, "mtime": 1, "ext": "pdf", "status": "auto",
         "program": "program:a", "program_name": "은하수 개척 사업", "kind": "report", "year": 2025, "round": 2},
        # 이름 표기만 다른 열쇠(앞 부처명) — 같은 사업으로 보인다
        {"rel": "은하수/채용/01_계약직원 채용 평가기준.hwp", "size": 700, "mtime": 1, "ext": "hwp", "status": "auto",
         "program": "program:a2", "program_name": "해양미래부 은하수 개척 사업", "kind": "criteria", "year": 2026, "round": 3},
        {"rel": "은하수/지출/영수증 모음.pdf", "size": 10, "mtime": 1, "ext": "pdf", "status": "folder",
         "program": "program:a", "program_name": "은하수 개척 사업", "kind": "evidence"},
        {"rel": "은하수/실패/깨진 문서.hwp", "size": 10, "mtime": 1, "ext": "hwp", "status": "auto",
         "program": "program:a", "program_name": "은하수 개척 사업", "kind": ""},
        {"rel": "기타/공고 100%_안내.다운로드", "size": 40, "mtime": 1, "ext": "다운로드",
         "program": "program:x", "program_name": "통합공고 사업", "kind": "notice"},         # 사업 아닌 이름 → 사업 미분류
        {"rel": "기타/표지.ai", "size": 10, "mtime": 1, "ext": "ai"},
    ]
    rows += [{"rel": f"별빛/사진/{i:03d}.jpg", "size": i, "mtime": 1, "ext": "jpg", "status": "auto",
              "program": "program:b", "program_name": "별빛 부트랩 사업", "kind": ""} for i in range(61)]
    rows += [{"rel": f"꼬리/{i}/자료.hwp", "size": 1, "mtime": 1, "ext": "hwp", "status": "auto",
              "program": f"program:t{i}", "program_name": f"꼬리{chr(0xAC00 + i * 7)}{chr(0xB098 + i)} 지원사업", "kind": "plan"}
             for i in range(13)]
    return rows


@pytest.fixture
def client(tmp_path):
    app = _app(tmp_path)
    db = app.state.db
    archive.load(db, _rows(), origins={LINKED: 7})
    archive_state.mark_failures(db, {"은하수/실패/깨진 문서.hwp": "암호 문서", LINKED: "무시돼야 함"})
    c = TestClient(app)
    assert _login(c, "zzdev", "devpass")
    return c


def _titles(html: str) -> list[str]:
    return re.findall(r'<span class="ar-name" title="[^"]*">([^<]+)</span>', html)


def _count(html: str) -> str:
    m = re.search(r'<p class="ar-count" role="status">(.*?)</p>', html)
    return re.sub(r"<[^>]+>", "", m.group(1)) if m else ""


def test_page_wording_and_labels(client):
    page = client.get("/archive").text
    assert "<title>" in page and "DGX" not in page
    assert "원본 보관소" in page and "학교 문서 원본을 모아 둔 곳입니다" in page
    assert "문서함 연결 수는 등록된 문서 수와 다를 수 있습니다" in page
    for internal in ("플랫폼 원본 대장", "OCR·분석 완료율", "미정", "파일명으로 검색", ">evidence", ">curriculum"):
        assert internal not in page, internal
    from zzaimy.app.doc_routing import KINDS
    assert KINDS["evidence"] in page and KINDS["plan"] in page                     # 라이브러리와 같은 종류 이름
    kinds = page[page.index('aria-label="문서 종류"'):]
    kinds = kinds[:kinds.index("</ul>")]
    assert kinds.rstrip().endswith("</a></li>") and kinds.rindex("분류 전") > kinds.index(KINDS["plan"])   # 분류 전은 끝
    fmt = page[page.index('name="ext"'):page.index("</select>", page.index('name="ext"'))]
    assert "AI(일러스트)" in fmt and "다운로드" not in fmt and 'value="hwp"' in fmt


def test_program_groups_long_tail_and_unclassified(client):
    page = client.get("/archive").text
    table = page[page.index('aria-label="사업별 원본 수"'):]
    assert "해양미래부" not in table                                                # 앞 부처명만 다른 이름은 한 사업
    assert re.search(r'>은하수 개척 사업</a>.*?<td class="num" data-label="원본">5</td>', table, re.S)
    assert "통합공고 사업" not in page                                              # 사업 아닌 값은 사업 미분류로
    assert "그 밖의 사업 3개" in page                                               # 상위 12개만 펼침(사업 15개)
    assert table.index("사업 미분류") > table.index("별빛 부트랩 사업")
    assert table.count(">파일 보기</a>") == 16                                       # 사업 15 + 미분류 — 모든 줄에 같은 단추
    assert "/projects/archived?q=" in table                                         # 사업 화면은 프로젝트 검색으로


def test_browse_levels_and_combined_filters(client):
    by_name = client.get("/archive?program=해양미래부 은하수 개척 사업").text      # 어떤 표기로 와도 같은 묶음
    assert _count(by_name) == "5건 중 1–5"
    assert _count(client.get("/archive?program=program:a2").text) == "5건 중 1–5"   # 장부 열쇠로 와도
    years = by_name[by_name.index('aria-label="사업 연도"'):by_name.index("</ul>", by_name.index('aria-label="사업 연도"'))]
    assert "2024년 · 1차년도" in years and "2026년 · 3차년도" in years and "연도 미상" in years
    assert _titles(client.get("/archive?program=program:a&year=2025").text) == ["은하수 개척 결과보고서"]
    assert _titles(client.get("/archive?program=program:a&kind=criteria&year=2026").text) == ["01_계약직원 채용 평가기준"]
    assert _titles(client.get("/archive?program=program:a&kind=none").text) == ["깨진 문서"]
    assert _count(client.get("/archive?program=none").text) == "2건 중 1–2"
    assert _titles(client.get("/archive?ext=ai").text) == ["표지.ai"]
    assert _titles(client.get("/archive?q=결과보고서 은하수").text) == ["은하수 개척 결과보고서"]
    assert _titles(client.get("/archive?q=100%25_").text) == ["공고 100%_안내.다운로드"]   # %·_ 는 글자 그대로
    assert _titles(client.get("/archive?q=1_0").text) == []


def test_processing_states(client):
    assert _titles(client.get("/archive?state=registered").text) == ["은하수 개척 사업계획서"]
    excluded = client.get("/archive?state=excluded&program=program:a").text
    assert _titles(excluded) == ["영수증 모음"] and "범위 밖 경로" in excluded
    failed = client.get("/archive?state=failed").text
    assert _titles(failed) == ["깨진 문서"] and "암호 문서" in failed
    waiting = client.get("/archive?state=waiting&program=program:a").text
    assert sorted(_titles(waiting)) == ["01_계약직원 채용 평가기준", "은하수 개척 결과보고서"]
    states = client.get("/archive?program=program:a").text
    assert re.search(r"처리 제외\(범위 밖\)<span>1</span>", states) and re.search(r"실패<span>1</span>", states)


def test_row_title_tags_and_buttons(client):
    page = client.get("/archive?state=registered").text
    row = page[page.index('<li class="ar-row">'):page.index("</li>", page.index('<li class="ar-row">'))]
    assert "은하수 개척 사업계획서" in row and "은하수_개척" not in row.split("<code>")[0]   # 파일 이름이 아니라 제목
    assert f"<code>{LINKED}</code>" in row and "… / 2024 / 1차년도 / 계획서" in row
    assert ">원본 열기</a>" in row and 'href="/doc/7"' in row and ">등록 문서 보기</a>" in row
    assert ">열기</a>" not in row and ">문서 보기</a>" not in row
    assert "2.5 MB" in row and "1차년도" in row and "2024년" in row and "폴더 기준" not in row
    recruit = client.get("/archive?q=채용").text                                     # 파일 이름이 사업을 부르지 않는다
    assert "폴더 기준" in recruit
    folder = client.get("/archive?q=영수증").text                                    # 상위 폴더 물려받기
    assert "폴더 기준" in folder


def test_round_converted_year_is_marked_as_program_year(client):
    page = client.get("/archive?q=채용").text                                        # 경로에 2026 이 없다 → 연차 환산
    assert "2026년(사업 연도)" in page
    page = client.get("/archive?state=registered").text                              # 경로에 연도가 있다
    assert "(사업 연도)" not in page


def test_paging(client):
    first = client.get("/archive?program=별빛 부트랩 사업").text
    assert _count(first) == "61건 중 1–50" and "1 / 2쪽" in first and len(_titles(first)) == 50
    second = client.get("/archive?program=별빛 부트랩 사업&page=2").text
    assert _count(second) == "61건 중 51–61" and len(_titles(second)) == 11
    assert _count(client.get("/archive?program=별빛 부트랩 사업&page=99").text) == "61건 중 51–61"
    assert _titles(client.get("/archive?q=은하수&sort=size").text)[0] == "은하수 개척 사업계획서"


def test_chips_remove_one_condition_and_keep_others(client):
    page = client.get("/archive?q=은하수&year=2025&sort=size").text
    chips = {k: v.replace("&amp;", "&") for v, k in re.findall(r'<a class="ar-chip" href="([^"]+)" data-clear="(\w+)"', page)}
    assert chips["year"].startswith("/archive?q=") and "sort=size" in chips["year"] and "year" not in chips["year"]
    assert "year=2025" in chips["q"] and "모두 지우기" in page


def test_partial_returns_only_lower_sections(client):
    part = client.get("/archive?q=은하수&partial=1").text
    assert 'id="archive-browse"' in part and 'id="archive-list"' in part
    assert "<html" not in part and 'id="arForm"' not in part and "partial" not in part


def test_masking_follows_internal_setting(client):
    from zzaimy.app import privacy_policy
    db = client.app.state.db
    archive.load(db, [{"rel": "연락/담당 연락처 010-1234-5678.hwp", "size": 1, "mtime": 1, "ext": "hwp"}])
    assert "010-1234-5678" in client.get("/archive?q=연락").text                       # 기본은 꺼짐(ADR-0044)
    privacy_policy.save(db, True, ["KR_PHONE"], "zzdev")
    page = client.get("/archive?q=연락").text
    assert "010-1234-5678" not in page.split('href="/archive/original')[0]


def test_staff_cannot_open_unregistered_and_hidden_docs_excluded(client, monkeypatch):
    assert _login(client, "zzaimy", "boot-pass-1")
    page = client.get("/archive?q=깨진").text
    assert "열람 권한 필요" in page and 'href="/archive/original' not in page
    from zzaimy.app import access_policy
    monkeypatch.setattr(access_policy, "visible", lambda *a, **kw: False)
    client.app.state.db.add_document("x.hwp", "dgx://x", owner="other")
    hidden = archive_view._hidden_docs(client.app.state.db, {"dept": None, "user": "zzaimy", "role": "staff"})
    assert hidden
    with client.app.state.db._conn() as conn:
        conn.execute("UPDATE archive_files SET doc_id = ? WHERE doc_id = 7", (min(hidden),))
    page = client.get("/archive?q=은하수 개척 사업").text
    assert _titles(page) == [] and _count(page) == "0건"


def test_helpers():
    assert archive_view.short_path("a/b/c/d/e.pdf") == "… / b / c / d"
    assert archive_view.short_path("a/e.pdf") == "a" and archive_view.short_path("e.pdf") == ""
    assert [archive_view.ext_of(x) for x in ("PDF", ".hwpx", "다운로드", "rdf/rds", "", "toolongext", "123")] == \
        ["pdf", "hwpx", "", "", "", "", ""]
    assert archive_view.ext_label("ai") == "AI(일러스트)" and archive_view.ext_label("hwp") == "HWP"
    assert archive_view.kind_label("") == "분류 전"
    assert archive_view.named_in_file("은하수_개척_계획.hwp", "은하수 개척 사업", [])
    assert not archive_view.named_in_file("채용 평가기준.hwp", "은하수 개척 사업", [])
    assert archive_view.named_in_file("ZT3.0 보고.hwp", "은하수 개척 사업", ["ZT 3.0"])


def test_platform_copies_and_converter_byproducts_are_not_originals(client):
    from zzaimy.app.archive import UPLOAD_PREFIX
    db = client.app.state.db
    archive.load(db, [
        {"rel": UPLOAD_PREFIX + "반입/2024/국고/2024-국고-0001 은하수/원본.hwpx", "size": 1, "mtime": 1, "ext": "hwpx",
         "program": "program:a", "program_name": "은하수 개척 사업", "kind": "plan"},
        {"rel": "은하수/2024/계획서/은하수_개척_사업계획서_imgs/image10.JPEG", "size": 1, "mtime": 1, "ext": "jpeg",
         "program": "program:a", "program_name": "은하수 개척 사업"},
        {"rel": "은하수/2024/계획서/보고_mineru_out/auto/page1.png", "size": 1, "mtime": 1, "ext": "png",
         "program": "program:a", "program_name": "은하수 개척 사업"},
        {"rel": "은하수/2024/my_imgs_folder/사진.jpg", "size": 1, "mtime": 1, "ext": "jpg",          # 접미가 폴더 이름 끝이 아님
         "program": "program:a", "program_name": "은하수 개척 사업"},
    ])
    page = client.get("/archive?program=program:a").text
    assert "원본.hwpx" not in page and "image10" not in page and "page1" not in page
    assert _count(page) == "6건 중 1–6"                                               # 원래 5건 + my_imgs_folder 사진
    assert "jpeg" not in page[page.index('name="ext"'):page.index("</select>", page.index('name="ext"'))]
    assert archive_state.is_original("a/b.hwp") and not archive_state.is_original("a/b_kordoc/c.md")


def test_year_options_and_non_program_years(client):
    db = client.app.state.db
    archive.load(db, [{"rel": "회계/서식/ACA0040_2000.rdf", "size": 1, "mtime": 1, "ext": "rdf", "year": 2000},
                      {"rel": "은하수/옛/1987년 자료.hwp", "size": 1, "mtime": 1, "ext": "hwp", "year": 1987,
                       "program": "program:a", "program_name": "은하수 개척 사업"}])
    page = client.get("/archive").text
    sel = page[page.index('name="year"'):page.index("</select>", page.index('name="year"'))]
    assert "none년" not in sel and ">연도 미상<" in sel and "2000년" not in sel and "1987년" not in sel
    assert ">2024년<" in sel
    assert "2000년" not in client.get("/archive?q=ACA0040").text                       # 사업 없는 파일의 숫자는 사업 연도가 아님
    assert _titles(client.get("/archive?year=2000").text) == []
    assert "ACA0040_2000.rdf" in client.get("/archive?year=none&q=ACA").text


def test_rare_acronym_typo_joins_frequent_program(client):
    db = client.app.state.db
    archive.load(db, [{"rel": f"별빛/ZETA3.0/{i}.hwp", "size": 1, "mtime": 1, "ext": "hwp", "program": "program:zeta30",
                       "program_name": "4단계 별빛 선도대학 육성사업"} for i in range(20)]
                 + [{"rel": "별빛/오타/ZOTA3.0 회의.hwp", "size": 1, "mtime": 1, "ext": "hwp", "program": "program:zota30",
                     "program_name": "ZOTA3.0"}])
    page = client.get("/archive").text
    assert ">ZOTA3.0<" not in page and "ZOTA3.0</option>" not in page
    assert _count(client.get("/archive?program=4단계 별빛 선도대학 육성사업").text) == "21건 중 1–21"


def test_program_table_actions_wrap_on_narrow_screens(client):
    page = client.get("/archive").text
    assert '<td class="ar-acts-cell"><div class="ar-acts">' in page
    css = page[page.index("@media(max-width:760px)"):]
    assert ".ar-acts{display:flex;flex-wrap:wrap" in css and ".ar-grid thead{display:none}" in css


def test_unclassified_row_has_no_project_link(client):
    table = client.get("/archive").text
    table = table[table.index('aria-label="사업별 원본 수"'):]
    row = table[table.rindex("<tr>", 0, table.index(">사업 미분류<")):]
    row = row[:row.index("</tr>")]
    assert "프로젝트 검색" not in row and "파일 보기" in row
