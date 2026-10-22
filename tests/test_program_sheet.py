"""사업 관리 — 사업 현황 엑셀에서 연도별 값 채우기(program_sheet)와 전체·현행·지난 세 갈래, 연도별 보기·엑셀 내려받기.
엑셀은 모두 가짜 값으로 만든다(실제 현황 표의 이름·금액을 쓰지 않는다)."""

from __future__ import annotations

import datetime as dt
import json

import openpyxl
import pytest

from tests.test_staff_grades import _add, _client, _no_google  # noqa: F401  (구글 끄기 고정물)
from tests.test_accounts import _app
from zzaimy.app import program_admin, program_sheet

LEDGER = {"programs": [
    {"name": "가나다 혁신사업", "terms": ["가나다"], "ministry": "교육부", "category": "일반재정지원",
     "school": {"years": [2024, 2026], "depts": ["기획처"]}},
    {"name": "라마바 지원사업", "terms": [], "ministry": "대구광역시", "category": "교내 장부",
     "school": {"years": [2025], "depts": ["산학협력단"]}},
    {"name": "가가가가 특화사업", "terms": ["가가가가"], "category": "교내 장부"},
    {"name": "나나나나 특화사업", "terms": ["나나나나"], "category": "교내 장부"},
    {"name": "사업 아닌 것 모음", "terms": ["사업 아닌 행사"], "category": "사업 아님"},
]}


def book_2024(path):
    """2024 꼴 — 순·사업명·…·사업 책임자·기타사항(업무진행부서)·국고·합계·상반기 합계, 합계 줄은 가로 병합."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "2024"
    ws["A1"] = "2024학년도 하반기 재정지원사업 수혜 실적"
    ws.merge_cells("A1:K1")
    head = ["순", "사업명", "세부 사업(과제)명", "지원기관", "총사업기간", "사업 책임자", "행정  담당자",
            "기타사항\n(업무진행부서)", "국고(A)", "합계", "상반기 합계"]
    for j, h in enumerate(head, 1):
        ws.cell(4, j, h)
    data = [
        [1, "2024년 가나다 혁신사업", "1단계", "교육부", "20240301-\n20270228", "BOSS", "김행정", "기획처", 700, 1000, 400],
        [2, "가나다 혁신사업", "2단계", "교육부", "20240301-\n20270228", "홍길순", "", "기획처", 100, 200, 50],
        [3, "라마바지원사업", "", "대구광역시", "20230101-20241231", "박책임", "", "산학협력단", 0, 300, 0],
        [None, None, None, None, None, None, None, None, None, None, None],
        [4, "새로운 교육 프로젝트 사업", "", "교육청", "2024.3.1~2024.12.31", "최새로", "", "교무처", 0, 50, 0],
        [5, "가가가가 나나나나 연계사업", "", "", "", "이모호", "", "", 0, 10, 0],
        [6, "사업 아닌 행사", "", "", "", "", "", "", 0, 5, 0],
    ]
    for i, row in enumerate(data, 5):
        for j, v in enumerate(row, 1):
            ws.cell(i, j, v)
    ws.cell(12, 1, "합계")
    ws.merge_cells("A12:H12")
    ws.cell(12, 10, 1565)
    wb.save(path)


def book_2025(path):
    """2025 꼴 — 앞에 구분 열, 열 순서가 다르고 총사업기간이 시작·끝 두 칸(가로 병합 머리줄, 날짜 값), 합계가 둘(그해·이월 포함)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "260220"
    ws["A1"] = "2025학년도 재정지원사업 수혜실적"
    head = ["기존,신규,종료", "순", "사업명", "지원기관", "기타사항 (업무진행부서)", "총사업기간", None, "사업 책임자",
            "합계 (A+B)", "이월금(G)", "합계"]
    for j, h in enumerate(head, 1):
        ws.cell(4, j, h)
    ws.merge_cells("F4:G4")
    rows = [
        ["기존", 1, "가나다혁신사업", "교육부", "기획처", dt.datetime(2024, 3, 1), dt.datetime(2027, 2, 28), "BOSS", 1500, 100, 1600],
        ["신규", 2, "새로운 교육 프로젝트 사업", "교육청", "교무처", dt.datetime(2025, 3, 1), dt.datetime(2025, 12, 31), "최새로", 70, 0, 70],
        ["", "※ 금액 단위 원", None, None, None, None, None, None, None, None, None],
    ]
    for i, row in enumerate(rows, 5):
        for j, v in enumerate(row, 1):
            ws.cell(i, j, v)
    ws.cell(8, 1, "합계")
    ws.merge_cells("A8:E8")
    wb.save(path)


@pytest.fixture()
def world(tmp_path):
    app = _app(tmp_path)
    (tmp_path / "kg_external.json").write_text(json.dumps(LEDGER, ensure_ascii=False), encoding="utf-8")
    _add(app, "boss", dept="기획처", grade="head")
    _add(app, "lead1", dept="기획처", grade="lead")
    x24, x25 = tmp_path / "현황(2024).xlsx", tmp_path / "현황(2025).xlsx"
    book_2024(x24)
    book_2025(x25)
    return app, tmp_path, x24, x25


def test_read_book_matches_columns_by_header_words(world):
    _, _, x24, x25 = world
    a = program_sheet.read_book(x24)
    b = program_sheet.read_book(x25)
    assert {r["year"] for r in a} == {2024} and {r["year"] for r in b} == {2025}
    assert [r["name"] for r in a] == ["가나다 혁신사업", "가나다 혁신사업", "라마바지원사업", "새로운 교육 프로젝트 사업",
                                      "가가가가 나나나나 연계사업", "사업 아닌 행사"]       # 빈 줄·합계 줄 빠짐, 앞 연도 떼기
    first = a[0]
    assert first["manager"] == "BOSS" and first["dept"] == "기획처" and first["officer"] == "김행정"
    assert first["period"] == "2024.03.01~2027.02.28" and first["budget_won"] == 1000   # 「상반기 합계」가 아닌 「합계」
    assert first["national_won"] == 700 and first["sub"] == "1단계"
    g = b[0]                                                       # 열 순서가 달라도 같은 열쇠
    assert g["name"] == "가나다혁신사업" and g["manager"] == "BOSS" and g["dept"] == "기획처"
    assert g["period"] == "2024.03.01~2027.02.28"                  # 가로 병합 머리줄 = 시작·끝 두 칸
    assert g["budget_won"] == 1500                                 # 맨 왼쪽 합계(그해 합계)
    assert g["status"] == "기존" and g["carry_won"] == 100 and g["budget_all_won"] == 1600   # 이월금·이월 포함 합계도 제 열쇠로
    assert len(b) == 2                                             # ※ 메모 줄·합계 줄 빠짐


def test_plan_links_names_and_reports(world):
    app, tmp_path, x24, x25 = world
    items = program_sheet.read_book(x24) + program_sheet.read_book(x25)
    rows = program_admin.merged_rows(tmp_path)
    store = program_admin.load_store(tmp_path)
    sm, new = program_sheet.plan(items, rows, store, not_program=program_admin.not_program_keys(LEDGER))
    assert store == {"rows": {}, "added": {}}                      # 미리보기는 원래 값을 고치지 않는다
    matched = dict(sm["matched"])
    assert set(matched) == {"가나다 혁신사업", "라마바 지원사업"}
    assert [n["name"] for n in sm["new"]] == ["새로운 교육 프로젝트 사업"] and sm["new"][0]["years"] == [2024, 2025]
    assert sm["ambiguous"][0][0] == "가가가가 나나나나 연계사업"
    assert sm["not_program"] == ["사업 아닌 행사"]
    y = new["rows"]["program:가나다혁신사업"]["years"]
    assert y["2024"]["budget_won"] == 1200 and y["2024"]["lines"] == 2          # 세부 과제 두 줄은 더한다
    assert y["2024"]["manager"] == "BOSS, 홍길순" and y["2025"]["budget"] == "1,500원"
    key = sm["new"][0]["key"]
    assert new["added"][key]["source"] == "엑셀" and new["rows"][key]["years"]["2025"]["dept"] == "교무처"
    assert sm["cells"]["fill"] > 0 and sm["cells"]["change"] == 0 and sm["blank_top"] > 0
    # 한 번 더 채우면 새 줄은 이름으로 이어지고 바뀌는 칸이 없다
    rows2 = program_admin.merged_rows(tmp_path, ledger=LEDGER, store=new)
    sm2, _ = program_sheet.plan(items, rows2, new, not_program=program_admin.not_program_keys(LEDGER))
    assert not sm2["new"] and sm2["cells"]["fill"] == 0 and sm2["cells"]["change"] == 0


def _upload(c, path, **form):
    with open(path, "rb") as f:
        return c.post("/programs/admin/sheet/preview", data=form,
                      files={"files": (path.name, f.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


def test_preview_apply_and_year_view(world):
    app, tmp_path, x24, x25 = world
    dev = _client(app, "zzdev", "devpass")
    doc = app.state.db.add_document("현황(2025)", str(x25), doc_type="grant")
    r = _upload(dev, x24, doc_ids=str(doc))
    assert r.status_code == 200 and "data-pa-preview" in r.text and "새로운 교육 프로젝트 사업" in r.text
    assert not (tmp_path / "program_admin.json").exists()          # 미리보기는 저장하지 않는다
    token = r.text.split('name="token" value="')[1].split('"')[0]
    r = dev.post("/programs/admin/sheet/apply", data={"doc": str(doc), "token": token}, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    assert not list((tmp_path / "tmp" / "program_sheet").glob(f"{token}-*"))   # 올린 임시 파일은 지운다
    store = json.loads((tmp_path / "program_admin.json").read_text())
    assert set(store["rows"]["program:가나다혁신사업"]["years"]) == {"2024", "2025"}
    # 연도를 고르면 그해 진행한 사업만, 칸은 그해 값
    p24 = dev.get("/programs/admin?tab=all&year=2024&unit=won").text
    p25 = dev.get("/programs/admin?tab=all&year=2025&unit=won").text
    assert ">1,500<" in p25
    assert ">1,200<" in p24 and ">1,500<" not in p24 and "라마바 지원사업" in p24          # 연도별 표의 합계 칸(원, 쉼표)
    p25 = dev.get("/programs/admin?tab=all&year=2025").text
    assert "새로운 교육 프로젝트 사업" in p25 and 'class="pa-src src-excel"' in p25
    assert "계정 있음" in p25                                        # 책임자 이름 BOSS = 계정 이름
    assert "lead1" not in json.dumps(store["rows"]["program:가나다혁신사업"].get("heads", []))   # 권한은 주지 않는다
    assert "BOSS" not in program_admin.load_store(tmp_path)["rows"]["program:가나다혁신사업"].get("heads", [])


def test_three_tabs_and_past_read_only(world):
    app, tmp_path, *_ = world
    dev = _client(app, "zzdev", "devpass")
    allp = dev.get("/programs/admin?tab=all").text
    assert "가나다 혁신사업" in allp and "라마바 지원사업" in allp and 'class="pa-past">지난</span>' in allp
    body = allp.split("<tbody>")[1].split("</tbody>")[0]
    lamaba = [tr for tr in body.split("<tr") if "라마바 지원사업" in tr][0]
    assert "data-pa-edit-open" not in lamaba and '"edit": false' in lamaba   # 전체 갈래에서도 지난 사업 줄은 읽기만
    assert "현행 사업 <span>" in allp and "전체 <span>" in allp
    past = dev.get("/programs/admin?tab=past").text
    assert "data-pa-edit-open" not in past.split("<tbody>")[1].split("</tbody>")[0] and "data-pa-sheet" not in past
    r = dev.post("/programs/admin/save", data={"key": "program:라마바지원사업", "name": "라마바 지원사업", "budget": "1원"},
                 follow_redirects=False)
    assert "err=" in r.headers["location"]                            # 지난 사업은 저장도 막는다


def test_hand_edit_not_overwritten_by_default(world):
    app, tmp_path, x24, _ = world
    dev = _client(app, "zzdev", "devpass")
    key = "program:가나다혁신사업"
    r = dev.post("/programs/admin/save", data={"key": key, "name": "가나다 혁신사업", "y_year": "2024", "y_budget": "9억 원",
                                                "y_manager": "손입력"}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    y = program_admin.load_store(tmp_path)["rows"][key]["years"]["2024"]
    assert y["budget"] == "9억 원" and set(y["by_hand"]) == {"budget", "manager"}
    token = _upload(dev, x24).text.split('name="token" value="')[1].split('"')[0]
    dev.post("/programs/admin/sheet/apply", data={"token": token})
    y = program_admin.load_store(tmp_path)["rows"][key]["years"]["2024"]
    assert y["budget"] == "9억 원" and y["manager"] == "손입력" and y["dept"] == "기획처"   # 손으로 고친 칸은 그대로, 빈칸만
    token = _upload(dev, x24).text.split('name="token" value="')[1].split('"')[0]
    dev.post("/programs/admin/sheet/apply", data={"token": token, "overwrite": "1"})
    y = program_admin.load_store(tmp_path)["rows"][key]["years"]["2024"]
    assert y["budget"] == "1,200원" and "by_hand" not in y                # 덮기를 고르면 덮는다
    # 손으로 더한 줄도 연도별 값을 가진다
    dev.post("/programs/admin/save", data={"key": "", "name": "손 사업", "y_year": "2026", "y_budget": "3억 원"})
    assert program_admin.load_store(tmp_path)["rows"]["program:손사업"]["years"]["2026"]["budget"] == "3억 원"
    assert "3억 원" in dev.get("/programs/admin?year=2026").text


def test_sheet_fill_scope(world):
    """가져오기 — 관리자는 전체, 총괄 책임자는 자기 부서 사업 줄만(다른 부서·새 사업은 건너뜀), 사업 책임자는 못 한다."""
    app, tmp_path, x24, _ = world
    lead = _client(app, "lead1")
    assert _upload(lead, x24).status_code == 403
    assert lead.post("/programs/admin/sheet/apply", data={"doc_ids": "1"}).status_code == 403
    assert "data-pa-sheet" not in lead.get("/programs/admin").text
    assert not (tmp_path / "program_admin.json").exists()
    boss = _client(app, "boss")                                   # 기획처 총괄 책임자
    assert "data-pa-sheet" in boss.get("/programs/admin").text
    r = _upload(boss, x24)
    assert r.status_code == 200 and "다른 부서 사업 — 건너뜀" in r.text and "우리 부서 사업 줄만" in r.text
    token = r.text.split('name="token" value="')[1].split('"')[0]
    loc = boss.post("/programs/admin/sheet/apply", data={"token": token}, follow_redirects=False).headers["location"]
    assert "done=" not in loc                                     # 되돌리기는 관리자만
    store = program_admin.load_store(tmp_path)
    assert set(store["rows"]) == {"program:가나다혁신사업"} and not store["added"]   # 기획처 사업만, 새 줄 없음


def test_clerks_link_projects_and_detail(world):
    """행정 담당자 지정(clerks) · 프로젝트 잇기·끊기 · 새 프로젝트 · 줄 상세 · choices()."""
    app, tmp_path, *_ = world
    _add(app, "clerk1", dept="기획처", grade="clerk")
    _add(app, "lead9", dept="산학협력단", grade="lead")
    key = "program:가나다혁신사업"
    boss = _client(app, "boss")
    r = boss.post("/programs/admin/save", data={"key": key, "leads": ["lead1"], "clerks": ["clerk1"]}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    assert "err=" in boss.post("/programs/admin/save", data={"key": key, "clerks": ["lead9"]},
                               follow_redirects=False).headers["location"]        # 다른 부서
    roles = program_admin.roles_for_project({"program": key + "|2026"}, tmp_path)
    assert roles["clerks"] == ["clerk1"] and roles["leads"] == ["lead1"]
    assert "가나다 혁신사업" in _client(app, "clerk1").get("/programs/admin").text   # 지정된 사업을 본다
    # 잇기 — 관리자는 아무 프로젝트나, 사업 책임자는 자기가 만든 것만
    db = app.state.db
    p1 = db.create_project("grant", "남의 프로젝트", owner="zzdev")
    p2 = db.create_project("grant", "내 프로젝트", owner="lead1")
    lead = _client(app, "lead1")
    assert lead.post("/programs/admin/link", data={"key": key, "project_id": str(p1), "year": "2026"}).status_code == 403
    r = lead.post("/programs/admin/link", data={"key": key, "project_id": str(p2), "year": "2025"}, follow_redirects=False)
    assert "ok=" in r.headers["location"] and db.get_project(p2)["program"] == key + "|2025"
    page = lead.get("/programs/admin").text
    assert 'href="/project/%d"' % p2 in page and "프로젝트 연결" in page
    lead.post("/programs/admin/link", data={"key": key, "project_id": str(p2), "unlink": "1"})
    assert db.get_project(p2)["program"] == ""
    assert lead.post("/programs/admin/link", data={"key": "program:라마바지원사업", "project_id": str(p2)}).status_code == 404
    r = lead.post("/programs/admin/new-project", data={"key": key, "year": "2026"}, follow_redirects=False)
    assert r.status_code == 303
    pid = int(r.headers["location"].rsplit("/", 1)[1])
    assert db.get_project(pid)["program"] == key + "|2026" and db.get_project(pid)["owner"] == "lead1"
    _add(app, "plain", dept="기획처", grade="member")
    assert _client(app, "plain").post("/programs/admin/new-project", data={"key": key}).status_code == 403
    # 줄 상세
    d = lead.get("/programs/admin/row", params={"key": key}).json()
    assert d["name"] == "가나다 혁신사업" and d["roles"]["clerks"] == ["CLERK1"] and any(p["id"] == pid for p in d["projects"])
    assert lead.get("/programs/admin/row", params={"key": "program:라마바지원사업"}).status_code == 404
    assert boss.get("/programs/admin/row", params={"key": "program:라마바지원사업"}).status_code == 200   # 지난 사업은 읽기로
    # 프로젝트 쪽에서 쓸 사업 목록
    ch = program_admin.choices("all", data_dir=tmp_path)
    assert ch[0]["stage"] != "past" and {c["key"] for c in ch} >= {key, "program:라마바지원사업"}
    assert [c["key"] for c in program_admin.choices("own", user="clerk1", data_dir=tmp_path)] == [key]
    assert program_admin.program_value(key, 2026) == key + "|2026"



def test_trim_projects_hides_unviewable_names():
    """보안: 사업 관리 상세·목록의 프로젝트·문서는 볼 수 있는 것만(관리자 밖)."""
    import inspect
    from zzaimy.app import program_admin as pa_
    src = inspect.getsource(pa_.programs_admin_row)
    assert "trim_projects" in src and "visible(" in src


# ---------------------------------------------------------------- 원본 꼴 전체 열(2단 머리줄·세부 과제·합계 줄)

MONEY_HEAD = ["국고(A)", "지자체(B)\n(대구/경북)", "기업체(C)", "교비대응(D)", "기타(E)\n(교육청 등)"]


def book_full(path, year, rows, *, status=True, account=True, incentive=True, fiscal=False, label_total=True):
    """원본 현황 표 꼴 — 제목, ▣ 대상기간·기준일 줄, 2단 머리줄(사업현황 / OOOO년 사업예산(원)), 세부 과제 줄, 맨 아래 합계 줄.
    rows: [(구분, 사업명, 과제명, 지원기관, 주관기관, 총기간, 당해 기간, 책임자, 참여학과, 행정 담당자, 업무부서, 회계부서, [국고, 지자체, 기업체, 교비, 기타, 인센티브])]."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "시트1"
    head = (["기존,신규,종료"] if status else []) + ["순"] + (["회계연도"] if fiscal else []) + \
        ["사업명", "세부 사업(과제)명", "지원기관", "주관(업무)기관", "총사업기간", "당해연도\n사업기간", "사업\n책임자",
         "사업 참여학과\n(대상학과)", "행정\n 담당자", "기타사항\n(업무진행부서)"] + (["회계처리부서"] if account else [])
    money_head = MONEY_HEAD + (["인센티브(F)"] if incentive else []) + ["합계"]
    fm = len(head) + 1
    head += money_head
    ws.cell(1, 1, f"{year}학년도 재정지원사업 수혜 현황")
    ws.cell(2, 1, f"▣ 사업선정 대상기간 : {year}.01.01. ~ {year}.10.08.\n▣ 안내 문구")
    ws.cell(2, len(head), f"{year}.08.19.기준")
    if status:
        ws.cell(3, 1, "구분(예)")
    ws.cell(3, 2 if status else 1, "사업현황")
    ws.merge_cells(start_row=3, start_column=2 if status else 1, end_row=3, end_column=fm - 1)
    ws.cell(3, fm, f" {year}년 사업예산(원)")
    ws.merge_cells(start_row=3, start_column=fm, end_row=3, end_column=len(head))
    for j, h in enumerate(head, 1):
        ws.cell(4, j, h)
    sums = [0] * (len(money_head))
    for i, (st, name, sub, mi, ag, per, yp, man, maj, off, dept, acc, ns) in enumerate(rows, 1):
        vals = ([st] if status else []) + [i] + ([year] if fiscal else []) + [name, sub, mi, ag, per, yp, man, maj, off, dept] + \
            ([acc] if account else [])
        ns = list(ns[:5]) + (list(ns[5:6]) if incentive else [])
        ns.append(sum(ns))
        for j, v in enumerate(vals + ns, 1):
            ws.cell(4 + i, j, v)
        sums = [a + b for a, b in zip(sums, ns)]
    tr = 5 + len(rows)
    if label_total:
        ws.cell(tr, 1, "합계")
    ws.merge_cells(start_row=tr, start_column=1, end_row=tr, end_column=fm - 1)
    for j, v in enumerate(sums, fm):
        ws.cell(tr, j, v)
    ws.cell(tr + 2, 1, "※ 금액 단위 원")
    wb.save(path)
    return sums


R25 = [
    ("기존", "가나다 혁신사업", "1단계 과제", "교육부", "가재단", "20250301-20280229", "20250301-\n20260228", "홍길순", "전체 학과",
     "김행정", "기획처", "산학협력단회계", [1_234_567_000, 100_000_000, 0, 50_000_000, 0, 0]),
    ("기존", "가나다 혁신사업", "2단계 과제", "교육부", "가재단", "20250301-20280229", "20250301-\n20260228", "이책임", "가나과",
     "박행정", "기획처", "산학협력단회계", [500_000_000, 0, 0, 0, 0, 20_000_000]),
    ("신규", "마바사 지원사업", "마바사 지원사업", "노동부", "나센터", "20250301-20270228", "20250301-20260228", "최새로", "다라과",
     "정행정", "교무처", "교비회계", [0, 0, 30_000_000, 0, 10_000_000, 0]),
    ("종료", "아자차 연계사업", "아자차 과제", "대구광역시", "다진흥원", "20250101-20251231", "20250101-20251231", "윤책임", "",
     "", "산학협력단", "산학협력단회계", [200_000_000, 0, 0, 0, 0, 0]),
]
R26 = [
    ("기존", "가나다 혁신사업", "1단계 과제", "교육부", "가재단", "20250301-20280229", "20260301-20270228", "홍길순", "전체 학과",
     "강행정", "기획처", "산학협력단회계", [2_000_000_000, 0, 0, 0, 0, 0]),
    ("기존", "마바사 지원사업", "마바사 지원사업", "노동부", "나센터", "20250301-20270228", "20260301-20270228", "최새로", "다라과",
     "정행정", "교무처", "교비회계", [0, 0, 40_000_000, 0, 0, 0]),
]
R22 = [
    ("", "가나다 혁신사업", "옛 과제", "교육부", "가재단", "20220301-20250228", "20220301-20230228", "홍길순", "", "김행정", "기획처", "",
     [300_000_000, 0, 0, 0, 0, 0]),
]


@pytest.fixture()
def full(tmp_path, world):
    app, tmp_path, *_ = world
    x22, x25, x26 = tmp_path / "f22.xlsx", tmp_path / "f25.xlsx", tmp_path / "f26.xlsx"
    s22 = book_full(x22, 2022, R22, status=False, account=False, incentive=False, label_total=True)
    s25 = book_full(x25, 2025, R25, label_total=False)                 # 합계 줄에 글이 없는 꼴(병합만)
    s26 = book_full(x26, 2026, R26, fiscal=True)
    return app, tmp_path, {2022: (x22, s22), 2025: (x25, s25), 2026: (x26, s26)}


def _apply(dev, *paths, overwrite=False):
    files = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in paths]
    r = dev.post("/programs/admin/sheet/preview", files=files)
    assert r.status_code == 200, r.text[:300]
    token = r.text.split('name="token" value="')[1].split('"')[0]
    data = {"token": token}
    if overwrite:
        data["overwrite"] = "1"
    return dev.post("/programs/admin/sheet/apply", data=data, follow_redirects=False)


def test_read_sheets_all_columns_and_total(full):
    _, _, books = full
    items, metas = program_sheet.read_sheets(books[2025][0])
    m = metas[0]
    keys = [k for k, _ in m["columns"]]
    assert keys == ["status", "seq", "name", "sub", "ministry", "agency", "period", "year_period", "manager", "majors", "officer",
                    "dept", "account_dept", "national", "local", "company", "school", "other", "incentive", "budget"]
    assert m["base_date"] == "2025.08.19" and m["target"] == "2025.01.01. ~ 2025.10.08"
    assert m["heads"] == {"info": "사업현황", "money": "2025년 사업예산(원)", "status": "구분(예)"}
    assert dict(m["columns"])["local"] == "지자체(B)\n(대구/경북)"               # 원본 머리줄 글(줄바꿈 포함)
    assert len(items) == 4 and m["lines"] == 4                            # ※ 메모 줄·합계 줄은 줄이 아니다
    a = items[0]
    assert (a["status"], a["seq"], a["sub"], a["agency"], a["officer"], a["account_dept"], a["majors"]) == \
        ("기존", "1", "1단계 과제", "가재단", "김행정", "산학협력단회계", "전체 학과")
    assert a["year_period"] == "2025.03.01~2026.02.28" and a["local_won"] == 100_000_000 and a["school_won"] == 50_000_000
    assert items[1]["incentive_won"] == 20_000_000 and items[2]["company_won"] == 30_000_000 and items[2]["other_won"] == 10_000_000
    total = m["total"]                                                    # 글 없는 합계 줄도 합계로 읽는다
    for k, v in zip(("national", "local", "company", "school", "other", "incentive", "budget"), books[2025][1]):
        assert total[k + "_won"] == v == sum(it[k + "_won"] for it in items)
    # 구분·회계처리부서·인센티브가 없는 해, 회계연도가 있는 해
    i22, m22 = program_sheet.read_sheets(books[2022][0])
    k22 = [k for k, _ in m22[0]["columns"]]
    assert "status" not in k22 and "account_dept" not in k22 and "incentive" not in k22 and "status" not in m22[0]["heads"]
    assert i22[0]["status"] == "" and i22[0]["incentive_won"] is None and m22[0]["total"]["budget_won"] == 300_000_000
    i26, m26 = program_sheet.read_sheets(books[2026][0])
    assert "fiscal_year" in [k for k, _ in m26[0]["columns"]] and i26[0]["fiscal_year"] == "2026" and i26[0]["name"] == "가나다 혁신사업"


def test_tasks_preserved_and_folded(full, tmp_path):
    app, tmp_path, books = full
    items, metas = program_sheet.read_sheets(books[2025][0])
    rows = program_admin.merged_rows(tmp_path)
    sm, new = program_sheet.plan(items, rows, program_admin.load_store(tmp_path), sheets=metas,
                                 not_program=program_admin.not_program_keys(LEDGER))
    y = new["rows"]["program:가나다혁신사업"]["years"]["2025"]
    assert [t["sub"] for t in y["tasks"]] == ["1단계 과제", "2단계 과제"] and sm["tasks"] == 4
    assert y["tasks"][1]["incentive_won"] == 20_000_000 and y["tasks"][0]["officer"] == "김행정"
    assert y["budget_won"] == 1_384_567_000 + 520_000_000 and y["national_won"] == 1_734_567_000
    assert y["local_won"] == 100_000_000 and y["incentive_won"] == 20_000_000 and y["officer"] == "김행정, 박행정"
    assert y["status"] == "기존" and y["account_dept"] == "산학협력단회계" and y["seq"] == "1" and y["order"] == 5
    assert new["sheets"]["2025"]["base_date"] == "2025.08.19" and new["sheets"]["2025"]["total"]["budget_won"] == sum(books[2025][1][-1:])
    assert y["budget"] == "1,904,567,000원"                               # 옛 칸(budget·manager·dept·period)도 계속 채운다
    assert y["manager"] == "홍길순, 이책임" and y["dept"] == "기획처" and y["period"] == "2025.03.01~2028.02.29"


def test_year_view_table_units_and_summary(full):
    app, tmp_path, books = full
    dev = _client(app, "zzdev", "devpass")
    assert "ok=" in _apply(dev, books[2025][0], books[2026][0]).headers["location"]
    page = dev.get("/programs/admin?tab=all&year=2025&unit=won").text
    body = page.split("<tbody>")[1].split("</tbody>")[0]
    assert body.index("가나다 혁신사업") < body.index("아자차 연계사업") < body.index("마바사 지원사업")   # 기본은 합계 큰 순
    assert 'data-o="1"' in body and 'data-o="3"' in body                                   # 엑셀 순서는 「순」 머리로 정렬
    assert "세부 과제 2개 펼치기" in body and body.count('class="pa-task"') == 2              # 세부 과제 줄(접힌 채)
    assert '<span class="pa-st st-new">신규</span>' in body and '<span class="pa-st st-end">종료</span>' in body
    assert 'data-pa-group="org"' in page and 'data-pa-group="money"' in page and "기준일 2025.08.19" in page
    foot = page.split("<tfoot>")[1].split("</tfoot>")[0]
    total25 = books[2025][1][-1]
    assert f">{total25:,}<" in foot and f">{books[2025][1][0]:,}<" in foot                 # 맨 아래 합계 줄(합계·국고)
    ysum = page.split("data-pa-ysum")[1].split("</div>\n</div>")[0]
    assert f"{total25:,}" in ysum and "신규 · 종료" in ysum and ">1 · 1<" in ysum and "2024년 대비" in ysum
    # 단위 바꾸기 — 천원은 정수, 백만원은 소수 첫째 자리
    k = dev.get("/programs/admin?tab=all&year=2025&unit=thousand").text.split("<tfoot>")[1]
    assert f">{round(total25 / 1000):,}<" in k
    mm = dev.get("/programs/admin?tab=all&year=2025&unit=million").text
    assert f">{total25 / 1e6:,.1f}<" in mm.split("<tfoot>")[1] and '>백만원</a>' in mm
    assert program_sheet.fmt_unit(1_234_567_000, "thousand") == "1,234,567" and program_sheet.fmt_unit(1_234_567_000, "million") == "1,234.6"
    # 지난해 대비(2026 ← 2025) · 전체 연도 막대
    p26 = dev.get("/programs/admin?tab=all&year=2026&unit=won").text.split("data-pa-ysum")[1].split("</div>\n</div>")[0]
    delta = sum(books[2026][1][-1:]) - total25
    assert f"{delta:,}" in p26 and "사업 -1" in p26
    allp = dev.get("/programs/admin?tab=all&year=0").text
    assert "data-pa-trend" in allp and "2025</span>" in allp and "data-pa-ytable>" not in allp


def test_xlsx_download_matches_original_layout(full):
    app, tmp_path, books = full
    dev = _client(app, "zzdev", "devpass")
    _apply(dev, books[2025][0])
    assert dev.get("/programs/admin.xlsx").status_code == 400
    r = dev.get("/programs/admin.xlsx", params={"year": "2025", "tab": "all"})
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"]
    out = tmp_path / "down.xlsx"
    out.write_bytes(r.content)
    ws = openpyxl.load_workbook(out).active
    src = openpyxl.load_workbook(books[2025][0]).active
    n = src.max_column
    flat = lambda v: "".join(str(v or "").split())
    assert [flat(ws.cell(4, j).value) for j in range(1, n + 1)] == [flat(src.cell(4, j).value) for j in range(1, n + 1)]   # 머리줄·열 순서
    assert ws.cell(4, 15).value == "지자체(B)\n(대구/경북)"                  # 줄바꿈도 원본대로
    assert ws.cell(3, 1).value == "구분(예)" and ws.cell(3, 2).value == "사업현황" and ws.cell(3, n - 6).value == "2025년 사업예산(원)"
    assert "2025.08.19" in ws.cell(2, n).value and "대상기간" in ws.cell(2, 1).value
    assert [ws.cell(i, 4).value for i in range(5, 9)] == ["1단계 과제", "2단계 과제", "마바사 지원사업", "아자차 과제"]
    assert ws.cell(9, 1).value == "합계" and [ws.cell(9, j).value for j in range(n - 6, n + 1)] == books[2025][1]
    assert ws.cell(5, n).number_format == "#,##0" and ws.cell(9, n).number_format == "#,##0"
    # 다시 읽어도 같은 값(내려받은 파일을 그대로 다시 채울 수 있다)
    again, meta = program_sheet.read_sheets(out)
    assert len(again) == 4 and meta[0]["total"]["budget_won"] == books[2025][1][-1]


def test_xlsx_download_same_scope_as_page(full):
    app, tmp_path, books = full
    dev = _client(app, "zzdev", "devpass")
    _apply(dev, books[2025][0])
    _add(app, "plain", dept="기획처", grade="member")

    def subs(c):
        r = c.get("/programs/admin.xlsx", params={"year": "2025", "tab": "all"})
        assert r.status_code == 200
        out = tmp_path / "s.xlsx"
        out.write_bytes(r.content)
        ws = openpyxl.load_workbook(out).active
        return [ws.cell(i, 4).value for i in range(5, ws.max_row) if ws.cell(i, 4).value]
    boss = subs(_client(app, "boss"))                                   # 총괄 책임자는 모든 사업을 읽는다
    assert "1단계 과제" in boss and "아자차 과제" in boss and "마바사 지원사업" in boss
    assert subs(_client(app, "lead1")) == []                            # 지정된 사업이 없다
    assert _client(app, "plain").get("/programs/admin.xlsx", params={"year": "2025"}).status_code == 403
    page = _client(app, "boss").get("/programs/admin?tab=all&year=2025").text
    assert "마바사 지원사업" in page and "1단계 과제" in page
    lead = _client(app, "lead1")
    assert "마바사 지원사업" not in lead.get("/programs/admin?tab=all&year=2025").text


def test_reapply_fills_new_cells_and_keeps_hand(full):
    """예전 저장값(세부 과제·예산 6갈래 없음)에 다시 적용하면 새 칸만 채워지고 손으로 고친 칸은 그대로."""
    app, tmp_path, books = full
    dev = _client(app, "zzdev", "devpass")
    _apply(dev, books[2025][0])
    key = "program:가나다혁신사업"
    store = json.loads((tmp_path / "program_admin.json").read_text())
    y = store["rows"][key]["years"]["2025"]
    for f in ("tasks", "local", "local_won", "officer", "account_dept", "incentive", "incentive_won"):
        y.pop(f, None)
    y["manager"], y["by_hand"] = "손입력", ["manager"]
    store.pop("sheets")
    (tmp_path / "program_admin.json").write_text(json.dumps(store, ensure_ascii=False))
    loc = _apply(dev, books[2025][0]).headers["location"]
    assert "ok=" in loc
    y = program_admin.load_store(tmp_path)["rows"][key]["years"]["2025"]
    assert y["manager"] == "손입력" and y["by_hand"] == ["manager"]          # 손 칸은 그대로
    assert len(y["tasks"]) == 2 and y["local_won"] == 100_000_000 and y["officer"] == "김행정, 박행정"
    assert program_admin.load_store(tmp_path)["sheets"]["2025"]["base_date"] == "2025.08.19"


def test_file_kinds_html_and_lock(tmp_path):
    """엑셀이 웹 페이지로 저장한 html(.xls 이름) — colspan·rowspan 머리줄도 읽는다. 잠금 파일·옛 xls 는 안내."""
    html = """<html><head><meta charset="utf-8"></head><body><table>
    <tr><td colspan="6">2024학년도 재정지원사업 수혜 현황</td></tr>
    <tr><td colspan="5">▣ 사업선정 대상기간 : 2024.1.1~12.31</td><td>2024.12.31기준</td></tr>
    <tr><td colspan="3">사업현황</td><td colspan="3"> 2024년 사업예산(원)</td></tr>
    <tr><td>순</td><td>사업명</td><td>사업<br>책임자</td><td>국고(A)</td><td>교비대응(D)</td><td>합계</td></tr>
    <tr><td>1</td><td>가나다 혁신사업</td><td>홍길순</td><td>1,000</td><td>200</td><td>1,200</td></tr>
    <tr><td>2</td><td rowspan="2">라마바 지원사업</td><td>박책임</td><td>300</td><td>0</td><td>300</td></tr>
    <tr><td>3</td><td>이책임</td><td>50</td><td>0</td><td>50</td></tr>
    <tr><td colspan="3">합계</td><td>1,350</td><td>200</td><td>1,550</td></tr></table></body></html>"""
    p = tmp_path / "현황.xls"
    p.write_text(html, encoding="utf-8")
    assert program_sheet.file_kind(p) == "html"
    items, metas = program_sheet.read_sheets(p)
    assert [(i["name"], i["manager"], i["budget_won"]) for i in items] == [
        ("가나다 혁신사업", "홍길순", 1200), ("라마바 지원사업", "박책임", 300), ("라마바 지원사업", "이책임", 50)]
    assert metas[0]["total"]["budget_won"] == 1550 and metas[0]["base_date"] == "2024.12.31" and items[0]["year"] == 2024
    lock = tmp_path / "~$현황.xlsx"
    lock.write_bytes(b"\x00" * 165)
    with pytest.raises(ValueError):
        program_sheet.read_sheets(lock)
    ole = tmp_path / "옛.xls"
    ole.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 504)
    assert program_sheet.file_kind(ole) == "xls"
    try:
        import xlrd  # noqa: F401
    except ImportError:
        with pytest.raises(ValueError, match="xlsx"):
            program_sheet.read_sheets(ole)
    assert program_sheet.parse_won("12억 3,000만 원") == 1_230_000_000 and program_sheet.parse_won("1,200원") == 1200
    assert program_sheet.parse_won("미정") is None

