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
    d = db.add_document("은하수 개척 사업계획서.hwp", "x", doc_type="grant")   # 반입 기록이 가리키는 문서(지금 있는 문서만 잇는다)
    with db._conn() as conn:
        conn.execute("UPDATE documents SET id = 7 WHERE id = ?", (d,))
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
    assert "한 문서에 원본이 여럿일 수 있어 라이브러리 문서 수와 다릅니다" in page
    for internal in ("플랫폼 원본 대장", "OCR·분석 완료율", "미정", "파일명으로 검색", ">evidence", ">curriculum"):
        assert internal not in page, internal
    from zzaimy.app.doc_routing import KINDS
    assert KINDS["evidence"] in page and KINDS["plan"] in page                     # 라이브러리와 같은 종류 이름
    kinds = page[page.index('aria-label="문서 종류"'):]
    kinds = kinds[:kinds.index("</ul>")]
    assert kinds.rstrip().endswith("</a></li>") and kinds.rindex("분류 전") > kinds.index(KINDS["plan"])   # 분류 전은 끝
    fmt = page[page.index('name="ext"'):page.index("</select>", page.index('name="ext"'))]
    assert "AI(일러스트)" in fmt and "다운로드" not in fmt and 'value="hwp"' in fmt


def _tree(html: str) -> str:
    """왼쪽 사업 목록(대·중·소 분류) 부분."""
    tree = html[html.index('aria-label="사업별 원본 수"'):]
    return tree[:tree.index("</nav>")]


def test_program_groups_long_tail_and_unclassified(client):
    page = client.get("/archive").text
    tree = _tree(page)
    assert "해양미래부" not in tree                                                 # 앞 부처명만 다른 이름은 한 사업
    assert ">은하수 개척 사업<span>5</span></a>" in tree
    assert "통합공고 사업" not in page                                              # 사업 아닌 값은 사업 미분류로
    assert tree.count('class="ar-ri l3') == 15                                       # 사업 15개 모두 — 위 12개만 자르지 않는다
    assert 'class="ar-l1' in tree and 'class="ar-l1-h"' in tree                     # 대분류 머리(접기·펼치기 단추)
    assert tree.index("사업 미분류") > tree.index("별빛 부트랩 사업")               # 미분류는 맨 아래
    assert ">전체 사업<span>81</span></a>" in tree
    assert "/projects/archived?q=" not in page                                      # 사업 동작은 사업을 골랐을 때 머리에


def test_tree_follows_the_ledger_classification(client, monkeypatch):
    """대·중·소 분류는 라이브러리와 같은 나무(app.program_tree, 외부 확인 장부 기준) — 장부에 있는 사업은 대분류·중분류 아래로."""
    ledger = {"categories": {"일반재정지원": {"label": "일반재정지원"}},
              "programs": [{"name": "은하수 개척 사업", "ministry": "교육부", "category": "일반재정지원"}]}
    monkeypatch.setitem(archive_view._LEDGER, "v", ledger)
    tree = _tree(client.get("/archive").text)
    edu = tree.index("교육부 재정지원")
    assert edu < tree.index('<div class="ar-l2" data-l2><span>일반재정지원</span>') < tree.index(">은하수 개척 사업<span>")
    unknown = tree[tree.index('data-l1="장부에 없는 이름 (확인 필요)"') - 40:]
    assert 'data-open="0"' in unknown[:200] and ">별빛 부트랩 사업<span>" in unknown   # 장부 밖 이름은 접어 둔다
    picked = _tree(client.get("/archive?program=program:b").text)                    # 고른 사업이 든 대분류는 펼친다
    assert 'data-open="1"' in picked[picked.index('data-l1="장부에 없는 이름'):][:120]


def test_view_toggle_and_table_columns(client):
    page = client.get("/archive").text
    assert 'id="arLayout" data-view="table"' in page                                 # 기본은 「목록」(표)
    views = page[page.index('aria-label="보기 방식"'):page.index("</div>", page.index('aria-label="보기 방식"'))]
    assert 'data-view-pick="table" aria-pressed="true">목록</a>' in views and 'data-view-pick="card" aria-pressed="false">카드</a>' in views
    head = page[page.index('class="ar-row ar-hrow"'):page.index("</li>", page.index('class="ar-row ar-hrow"'))]
    assert [re.sub(r"<[^>]+>", "", c) for c in re.findall(r"<span(?: class=\"[^\"]*\")?>([^<]*)</span>", head)] == \
        ["형식", "이름", "사업", "연도", "종류", "상태", "크기", "동작"]
    assert "localStorage" in page and "aura.archive.view" in page                    # 고른 보기는 브라우저에 남긴다
    card = client.get("/archive?view=card&q=은하수").text
    assert 'id="arLayout" data-view="card"' in card and 'data-view-pick="card" aria-pressed="true"' in card
    assert "view=card" in card[card.index('class="ar-chips"'):]                        # 조건 칩·쪽 주소가 보기를 이어 간다
    assert _count(card) == _count(client.get("/archive?q=은하수").text)               # 보기는 조건이 아니다
    assert not any(c["key"] == "view" for c in archive_view.chips(archive_view.parse_filters(view="card"), {}, {}))
    assert archive_view.parse_filters(view="bogus")["view"] == "table"
    part = client.get("/archive?view=card&partial=1&q=은하수").text
    assert 'id="archive-browse"' in part and 'id="archive-list"' in part and 'id="arLayout"' not in part


def test_cleanup_card_starts_folded(client):
    page = client.get("/archive").text
    card = page[page.index('id="archive-cleanup"'):page.index("</section>", page.index('id="archive-cleanup"'))]
    assert 'id="acFold" aria-expanded="false" aria-controls="acBody">펼치기</button>' in card
    assert '<div id="acBody" hidden>' in card and 'id="acRows"' in card and "<details" not in card


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
    assert re.search(r"처리 제외<span>1</span>", states) and re.search(r"실패<span>1</span>", states)


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


def test_layout_stacks_on_narrow_screens(client):
    page = client.get("/archive").text
    assert page.index('class="ar-rail"') < page.index('id="archive-search"') < page.index('id="archive-list"')
    css = page[page.index("@media(max-width:900px)"):]
    assert ".ar-layout{grid-template-columns:1fr}" in css and ".ar-rail{position:static" in css
    assert ".ar-list{list-style:none;margin:0;padding:0;border-top:1px solid var(--line);container-type:inline-size}" in page


def test_picked_program_header_has_actions(client):
    page = client.get("/archive?program=program:a").text
    pick = page[page.index('class="ar-pick"'):page.index('class="ar-resbar"')]
    assert ">은하수 개척 사업</h2>" in pick and "원본 5건" in pick
    assert "/projects/archived?q=" in pick and "/graph/explore?id=program%3Aa" in pick and ">전체 사업 보기</a>" in pick
    assert 'aria-pressed="true"' in _tree(page)[_tree(page).index(">은하수 개척 사업<") - 400:_tree(page).index(">은하수 개척 사업<")]


def test_unclassified_row_has_no_project_link(client):
    page = client.get("/archive?program=none").text
    pick = page[page.index('class="ar-pick"'):page.index('class="ar-resbar"')]
    assert ">사업 미분류</h2>" in pick and "프로젝트 검색" not in pick and "그래프" not in pick and "전체 사업 보기" in pick


def _graph_program(db, key, label, acronyms=()):
    import json
    from zzaimy.graph import kg_store
    kg_store.ensure(db)
    with db._conn() as conn:
        conn.execute("INSERT INTO kg_nodes (id, type, label, props) VALUES (?, 'program', ?, ?)",
                     (key, label, json.dumps({"acronyms": list(acronyms)})))


def test_renamed_program_keeps_its_files_and_every_way_of_filtering(client):
    """장부·그래프가 사업 이름을 「…체계(옛 이름)」처럼 「사업」 낱말 없는 정식 이름으로 바꿔도 묶음이 사라지지 않는다."""
    db = client.app.state.db
    with db._conn() as conn:                                 # 동기화가 장부 이름을 그래프의 새 이름으로 바꾼 상태
        conn.execute("UPDATE archive_files SET program_name = '별누리 인재양성체계(구 ZETA)' WHERE program = 'program:b'")
    _graph_program(db, "program:b", "별누리 인재양성체계(구 ZETA)", ["ZETA"])
    page = client.get("/archive").text
    assert ">별누리 인재양성체계(구 ZETA)<span>61</span></a>" in _tree(page)
    for value in ("program:b", "별누리 인재양성체계(구 ZETA)", "별누리인재양성체계(구ZETA)", "ZETA", "별누리"):
        assert _count(client.get(f"/archive?program={value}").text) == "61건 중 1–50", value
    row_tag = client.get("/archive?program=ZETA").text
    assert 'value="별누리 인재양성체계(구 ZETA)"' in row_tag                       # 칸에는 보일 이름, 주소는 열쇠
    assert "program=program%3Ab" in page


def test_every_program_keeps_its_count_after_name_normalisation(client):
    """정리 전(장부 열쇠별 원본 수)과 정리 뒤(묶음별 원본 수)의 합이 같고, 그래프에 있는 사업은 미분류로 떨어지지 않는다."""
    db = client.app.state.db
    _graph_program(db, "program:a2", "해양미래부 은하수 개척 사업")
    names, progs = archive_view.load_programs(db, set())
    view = archive_view.browse(archive_view.program_years(archive_view.facet_rows(db, archive_view.parse_filters(), set()), progs),
                               archive_view.parse_filters(), progs, archive_view.kind_labels())
    shown = {p["value"]: p["n"] for p in view["programs"]}
    unclassified = (view["unclassified"] or {}).get("n", 0)
    assert sum(shown.values()) + unclassified == sum(n for _name, n in names.values())
    for key, (_name, n) in names.items():
        if key and key in archive_view.graph_match(db, names)[1]:
            assert progs.of(key), key
    for gid, n in shown.items():
        assert n == sum(names[k][1] for k in progs.keys_of(gid))
    dropped = [k for k in names if k and not progs.of(k)]
    assert dropped == ["program:x"]                                                # 사업 아닌 이름(통합공고 사업)만


def test_shared_vocabulary_and_format_groups(client):
    from zzaimy.app import vocab
    db = client.app.state.db
    archive.load(db, [{"rel": "회계/서식/ACA0040.rdf", "size": 1, "mtime": 1, "ext": "rdf"},
                      {"rel": "웹/style.css", "size": 1, "mtime": 1, "ext": "css"},
                      {"rel": "설치/setup.exe", "size": 1, "mtime": 1, "ext": "exe"}])
    page = client.get("/archive").text
    fmt = page[page.index('name="ext"'):page.index("</select>", page.index('name="ext"'))]
    for bad in ("RDF", "CSS", "EXE", "다운로드"):
        assert bad not in fmt, bad
    assert ">그 밖의 형식<" in fmt and ">HWP<" in fmt and "AI(일러스트)" in fmt
    assert sorted(_titles(client.get("/archive?ext=other&q=.").text)) == sorted(
        ["ACA0040.rdf", "공고 100%_안내.다운로드", "setup.exe", "style.css"])
    # 원본의 「문서 연결됨」과 문서의 「처리 완료」는 다른 말 — 같은 이름이면 서로 다른 수가 모순처럼 보인다
    assert vocab.ARCHIVE_STATE_LABELS["registered"] == "문서 연결됨" and vocab.STATUS_LABELS["reviewed"] == "처리 완료"
    assert f"<dt>{vocab.ARCHIVE_STATE_LABELS['registered']}</dt>" in page and "등록됨" not in page
    from zzaimy.app import main
    assert main.STATUS_LABELS is vocab.STATUS_LABELS


def test_counts_per_archive_key_survive_a_graph_rebuild_with_new_node_ids(client):
    """그래프를 다시 지어 사업 노드 id 가 바뀌어도(이름은 같음) 장부 열쇠별 건수·묶음·보일 이름이 그대로다."""
    db = client.app.state.db
    with db._conn() as conn:
        conn.execute("UPDATE archive_files SET program_name = '별누리 인재양성체계(구 ZETA)' WHERE program = 'program:b'")
    _graph_program(db, "program:b", "별누리 인재양성체계(구 ZETA)", ["ZETA"])

    def snapshot():
        names, progs = archive_view.load_programs(db, set())
        return {k: (progs.of(k), progs.label(k), n) for k, (_nm, n) in names.items()}
    before = snapshot()
    with db._conn() as conn:                                  # 다시 지은 그래프 — 같은 사업이 다른 id 로
        conn.execute("DELETE FROM kg_nodes WHERE id = 'program:b'")
    _graph_program(db, "program:zeta", "별누리 인재양성체계(구 ZETA)", ["ZETA"])
    after = snapshot()
    assert before == after and after["program:b"][1] == "별누리 인재양성체계(구 ZETA)"
    assert _count(client.get("/archive?program=ZETA").text) == "61건 중 1–50"
    with db._conn() as conn:                                  # 그래프가 아예 비어도 장부 이름으로 남는다(미분류로 안 떨어짐)
        conn.execute("DELETE FROM kg_nodes")
    gone = snapshot()
    assert gone["program:b"][0] == "program:b" and gone["program:b"][2] == before["program:b"][2]


def test_program_box_ranks_exact_acronym_over_word_and_part_matches(client):
    """낱말 「NOVA」가 세 사업 이름에 들어 있을 때 — 약칭이 그대로 맞는 사업이 이기고, 낱말만 맞는 값은 후보를 보인다."""
    db = client.app.state.db
    rows = []
    for key, name, n in (("program:nova", "새빛 인재양성체계(구 NOVA)", 30), ("program:dgnova", "대구 NOVA 협력 사업", 8),
                         ("program:novalab", "경북 NOVA 실험실 사업", 5)):
        rows += [{"rel": f"{key[8:]}/{i}.hwp", "size": 1, "mtime": 1, "ext": "hwp", "program": key, "program_name": name}
                 for i in range(n)]
    archive.load(db, rows)
    _graph_program(db, "program:nova", "새빛 인재양성체계(구 NOVA)", ["NOVA"])
    for value in ("NOVA", "NOVA사업", "nova 사업", "program:nova", "새빛 인재양성체계(구 NOVA)"):
        page = client.get(f"/archive?program={value}").text
        assert _count(page) == "30건 중 1–30", value
        assert "이 사업을 찾으셨나요?" not in page
    page = client.get("/archive?program=협력").text                                  # 이름 일부가 하나만 맞음
    assert _count(page) == "8건 중 1–8"
    page = client.get("/archive?program=NOV").text                                   # 셋에 맞는 일부 — 0건 대신 후보(많은 순)
    assert "이 사업을 찾으셨나요?" in page
    chooser = page[page.index("이 사업을 찾으셨나요?"):page.index("</ul>", page.index("이 사업을 찾으셨나요?"))]
    assert chooser.index("새빛 인재양성체계(구 NOVA)") < chooser.index("대구 NOVA 협력 사업") < chooser.index("경북 NOVA 실험실 사업")
    assert "program=program%3Adgnova" in chooser
    full = client.get("/archive").text
    assert '<option value="새빛 인재양성체계(구 NOVA)" data-key="program:nova" label="30건">' in full


def test_polluted_graph_aliases_never_steal_another_programs_acronym(client):
    """큰 사업 A 의 그래프 카드에 다른 사업 B 의 약칭이 섞여 있어도 B 의 약칭을 치면 B 로 간다."""
    db = client.app.state.db
    rows = [{"rel": f"a/{i}.hwp", "size": 1, "mtime": 1, "ext": "hwp", "program": "program:orbit",
             "program_name": "별빛 대학(ORBIT+) 육성사업"} for i in range(40)]
    rows += [{"rel": f"b/{i}.hwp", "size": 1, "mtime": 1, "ext": "hwp", "program": "program:nova",
              "program_name": "새빛 인재양성체계(구 NOVA)"} for i in range(9)]
    rows += [{"rel": f"c/{i}.hwp", "size": 1, "mtime": 1, "ext": "hwp", "program": "program:새빛누리",
              "program_name": "누리 연계 체계(구 VEGA)"} for i in range(4)]
    archive.load(db, rows)
    # A 의 카드에 B·C 의 약칭과 자기 약칭의 한 글자 오기(ORBT+)가 섞여 들어옴
    _graph_program(db, "program:orbit", "별빛 대학(ORBIT+) 육성사업", ["ORBIT+", "NOVA", "VEGA", "ORBT+"])
    _graph_program(db, "program:nova", "새빛 인재양성체계(구 NOVA)", ["NOVA"])
    for value, want in (("NOVA", "9건 중 1–9"), ("NOVA사업", "9건 중 1–9"), ("VEGA", "4건 중 1–4"),
                        ("ORBIT", "40건 중 1–40"), ("ORBIT+", "40건 중 1–40")):
        assert _count(client.get(f"/archive?program={value}").text) == want, value
    names, progs = archive_view.load_programs(db, set())
    assert not any("NOVA" in a or "VEGA" in a for a in progs.alias[progs.of("program:orbit")])
