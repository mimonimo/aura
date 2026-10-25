"""부서 사전 — 가짜 직제·사무분장·학칙 글에서 조직 단위·위계·사업 조직·별칭을 뽑고, 지문이 바뀌면 다시 만들고,
자동완성·정식 이름·API 권한·사용자·계정 화면(사전 후보·사전에서 고르기·상위 부서·겸직 부서)을 본다."""
from __future__ import annotations

import json
from urllib.parse import unquote

from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from zzaimy.app import dept_dictionary as dd
from zzaimy.app import rag_spaces
from zzaimy.app.db import Database

JIKJE = """가나대학교 직제 규정
1-1
제1조(목적) 이 규정은 우리 대학교의 직제에 관한 사항을 규정함을 목적으로 한다.
제5조(기획처) ① 총장 직속으로 기획처를 두고, 기획처장은 교원으로 보한다.
② 기획처에 기획팀, 정보팀을 두고, 팀장은 선임급 이상의 직원으로 보한다.
제6조(다라2.0사업단) ① 총장 직속으로 다라2.0사업단을 두며, 단장은 교원으로 보한다.
② 다라2.0사업단에는 인재양성팀(Y-인재팀), 행정지원팀을 두며, 팀장은 직원으로 보한다.
가나대학교 직제 규정
1-2
제7조(대학본부) ① 대학본부에 학생처, 사무국을 둔다.
제8조(학생처) 학생처에 학생복지팀, 상담센터, 진로센터를 두고, 상담센터에는 상담
팀을 둔다.
제9조(사무국) 사무국에 총무팀, 행정지원팀을 두고, 팀장은 직원으로 보한다.
제10조(교육원) ① 우리 대학교에 평생교육원을 둔다. ② 각 학과에 조교 또는 실험보조원을 두며, 학과의 업무를 돕는다.
제11조(명칭) 종전의 취업센터를 진로센터로 변경한다.
가나대학교 직제 규정
1-3
부 칙
제1조(시행일) 이 규정은 2026년 3월 1일부터 시행한다.
"""

SAMU = """가나대학교 사무분장 규정
제3조(총장 직속부서) 총장 직속부서는 다음 사항을 분장한다.
① 기획처 기획팀
1. 대학 운영계획 수립
2. 소관 위원회 : 기획위원회, 예산위원회 등
② 학생처 학생복지팀
1. 장학
"""

HAKCHIK = """가나대학교 학칙
제4조(설치 학과) 우리 대학교에 두는 학과는 별표 1과 같다.
제52조(부속기관) ① 우리 대학교에는 다음과 같은 부속기관을 둔다.
1. 도서관
2. 삭 제 <2015.2.6.>
3. 가나빌리지
② 부속기관의 운영에 관한 규정은 따로 정한다.
[별표1]
2025학년도 대학·학부 및 학과(계열), 수업연한 및 입학정원
대학·학부 및 학과(계열) | 수업 연한 | 입학정원(명)
가나대학 | 옛이름과 | 2년 | 30
2026학년도 대학·학부 및 학과(계열), 수업연한 및 입학정원
대학·학부 및 학과(계열) | 수업 연한 | 입학정원(명)
가나대학 | 새이름과 | 2년 | 30
기계계열 | 2년 | 40
"""

DOCS = [{"id": 1, "title": "1-1 가나대학교 직제 규정", "text": JIKJE},
        {"id": 2, "title": "가나대학교 사무분장 규정", "text": SAMU},
        {"id": 3, "title": "가나대학교 학칙", "text": HAKCHIK}]


def _units():
    asm = dd.assemble(dd.extract(DOCS))
    return {(u["name"], u["parent"]): u for u in asm["units"]}, asm


def test_extract_hierarchy_program_org_and_aliases():
    by, asm = _units()
    names = {n for n, _ in by}
    get = lambda n: next(u for (m, _), u in by.items() if m == n)
    assert get("기획팀")["parent"] == "기획처" and get("기획팀")["kind"] == "팀"
    assert get("기획처")["kind"] == "처" and get("기획처")["parent"] == ""
    assert get("학생처")["parent"] == "대학본부"
    assert get("상담팀")["parent"] == "상담센터"                 # 줄바꿈이 단어 중간에 든 「상담\n팀」
    assert get("다라2.0사업단")["kind"] == "사업 조직"
    assert "다라사업단" in get("다라2.0사업단")["aliases"]        # 판 번호를 뺀 짧은 이름
    assert get("인재양성팀")["aliases"] == ["Y-인재팀"] and get("인재양성팀")["parent"] == "다라2.0사업단"
    assert "취업센터" in get("진로센터")["aliases"]               # 「A를 B로 변경」 → 옛 이름
    assert get("기획위원회")["kind"] == "위원회" and get("기획위원회")["parent"] == "기획팀"
    assert get("평생교육원")["kind"] == "원"
    assert get("가나빌리지")["kind"] == "기관" and get("가나빌리지")["ambiguous"]      # 이름 끝으로 갈래를 모름
    assert get("새이름과")["kind"] == "학과" and get("새이름과")["parent"] == "가나대학"
    assert get("기계계열")["kind"] == "학과"
    assert "옛이름과" not in names                               # 같은 별표의 옛 해 표는 버린다
    assert not any(n.endswith(("보조원", "교원", "직원")) for n in names)    # 사람·직위는 조직이 아니다
    # 같은 이름 팀이 두 상위 아래 — 애매함, 둘 다 남는다
    dup = [u for (n, _), u in by.items() if n == "행정지원팀"]
    assert len(dup) == 1 and dup[0]["ambiguous"].startswith("상위 조직이 여럿")


def test_plan_create_skips_existing_alias_ambiguous_and_academic():
    _, asm = _units()
    d = dd.Dictionary({"units": [{**u, "id": u["name"]} for u in asm["units"]]})
    plan = dd.plan_create(d, ["기획처", "다라사업단"])            # 별칭으로 이미 있는 부서
    made = set(plan["create"])
    assert {"기획팀", "학생복지팀", "학생처", "상담센터", "평생교육원"} <= made
    assert "기획처" not in made and "다라2.0사업단" not in made
    assert "행정지원팀" not in made and "가나빌리지" not in made
    assert not made & {"새이름과", "기계계열", "가나대학", "기획위원회"}
    why = {s["name"]: s["why"] for s in plan["skip"]}
    assert why["다라2.0사업단"] == "이미 있는 부서" and why["행정지원팀"].startswith("애매함")


def _db_with_docs(tmp_path):
    db = Database(tmp_path / "t.db")
    ids = []
    for d in DOCS:
        i = db.add_document(d["title"] + ".hwp", "x", doc_type="regulation")
        db.update_document(i, masked_text=d["text"], status="reviewed")
        ids.append(i)
    other = db.add_document("가나대학교 학칙 시행세칙.hwp", "x", doc_type="regulation")       # 입력이 아님
    db.update_document(other, masked_text="제1조(목적) 세칙", status="reviewed")
    return db, ids


def test_build_follows_input_fingerprint(tmp_path):
    db, ids = _db_with_docs(tmp_path)
    assert [d["id"] for d in dd.source_docs(db)] == ids
    a = dd.build(db, tmp_path)
    assert (tmp_path / dd.FILE).is_file() and a["stats"]["units"] > 10
    b = dd.build(db, tmp_path)
    assert b["built_at"] == a["built_at"]                         # 지문이 같으면 그대로
    db.update_document(ids[0], masked_text=JIKJE.replace("정보팀", "정보전산팀"))
    c = dd.build(db, tmp_path)
    names = {u["name"] for u in c["units"]}
    assert "정보전산팀" in names and "정보팀" not in names and c["fingerprint"] != a["fingerprint"]
    assert list((tmp_path / "history").glob("dept_dictionary.*.json"))     # 옛 판은 history 에


def test_review_judges_only_unknown_kind_and_is_reused(tmp_path):
    db, _ = _db_with_docs(tmp_path)
    calls = []

    def complete(messages):
        calls.append(messages[1]["content"])
        return json.dumps({"is_unit": True, "kind": "원", "why": "부속기관 목록"})
    d = dd.build(db, tmp_path, complete=complete)
    u = next(x for x in d["units"] if x["name"] == "가나빌리지")
    assert u["kind"] == "원" and not u["ambiguous"] and u["review"]["why"]
    assert next(x for x in d["units"] if x["name"] == "다라2.0사업단")["kind"] == "사업 조직"   # 이름 끝 갈래는 모델이 바꾸지 않음
    n = len(calls)
    dd.build(db, tmp_path, complete=complete, force=True)
    assert len(calls) == n                                        # 근거가 같으면 지난 판정을 쓴다


def test_suggest_normalize_units(tmp_path):
    db, _ = _db_with_docs(tmp_path)
    dd.build(db, tmp_path)
    got = dd.suggest("학생", data_dir=tmp_path)
    assert got[0]["name"] == "학생처" and any(x["line"] == "학생복지팀 · 학생처 · 대학본부" for x in got)
    assert dd.suggest("인재", data_dir=tmp_path)[0]["line"] == "인재양성팀 · 다라2.0사업단"
    assert dd.suggest("Y-인재", data_dir=tmp_path)[0]["name"] == "인재양성팀"      # 별칭 앞부분
    assert dd.normalize("다라사업단", data_dir=tmp_path) == "다라2.0사업단"
    assert dd.normalize("취업센터", data_dir=tmp_path) == "진로센터"
    assert dd.normalize("없는부서", data_dir=tmp_path) == ""
    assert {u["name"] for u in dd.units("팀", data_dir=tmp_path)} >= {"기획팀", "상담팀"}
    assert dd.suggest("", kind="학과", data_dir=tmp_path) and all(x["kind"] == "학과" for x in dd.suggest("", kind="학과", data_dir=tmp_path))


def test_confirmed_parents_only_for_authority(tmp_path):
    db, _ = _db_with_docs(tmp_path)
    dd.build(db, tmp_path)
    assert dd.children_map(tmp_path) == {}                        # 규정 위계는 제안일 뿐 — 권한에 쓰지 않는다
    assert dd.suggested_parents(tmp_path)["기획팀"] == "기획처"
    assert dd.children_map(None) == {} and dd.parent_of("기획팀", None) == ""
    assert dd.set_parent(tmp_path, "다라2.0사업단", "기획처") == ""
    assert dd.children_map(tmp_path) == {"기획처": ["다라2.0사업단"]} and dd.parent_of("다라2.0사업단", tmp_path) == "기획처"
    assert "순환" in dd.set_parent(tmp_path, "기획처", "다라2.0사업단")
    assert dd.set_parent(tmp_path, "기획처", "기획처")
    assert dd.set_parent(tmp_path, "다라2.0사업단", "") == ""
    assert dd.children_map(tmp_path) == {}


# ---------------------------------------------------------------- 화면·API

def _client(tmp_path, who=("zzdev", "devpass")):
    app = _app(tmp_path)
    db = app.state.db
    for d in DOCS:
        i = db.add_document(d["title"] + ".hwp", "x", doc_type="regulation")
        db.update_document(i, masked_text=d["text"], status="reviewed")
    dd.build(db, tmp_path)
    c = TestClient(app)
    assert _login(c, *who)
    return app, c


def test_api_depts_staff_only_and_fields(tmp_path):
    app, c = _client(tmp_path, ("zzaimy", "boot-pass-1"))
    r = c.get("/api/depts", params={"q": "기획"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert items[0] == {"name": "기획처", "kind": "처", "kind_label": "처·국", "parent": "", "line": "기획처"}
    assert all(set(x) == {"name", "kind", "kind_label", "parent", "line"} for x in items)      # 근거는 싣지 않는다
    accounts = app.state.accounts
    accounts["stu"] = {**accounts["zzaimy"], "role": "student"}
    c2 = TestClient(app)
    c2.cookies.clear()
    app.state.accounts["stu"].pop("must_change", None)
    assert _login(c2, "stu", "boot-pass-1")
    assert c2.get("/api/depts", params={"q": "기획"}).status_code == 403


def test_dev_users_dictionary_candidates_and_bulk_create(tmp_path):
    app, c = _client(tmp_path)
    c.post("/dev/users/dept", data={"action": "create", "name": "기획처"})
    page = c.get("/dev/users").text
    # 화면에서는 사전 고르기를 뺐다(10-23 사용자: 쓰기 복잡). 부서 추가는 조직도(10-25). 서버의 여러 개 만들기 길은 남는다
    assert "사전에서 고르기" not in page and 'name="names"' not in page
    r = c.post("/dev/users/dept", data={"action": "create_many", "names": ["기획팀", "학생처", "새이름과", "기획처"]},
               follow_redirects=False)
    assert "ok=" in r.headers["location"] and "2개" in unquote(r.headers["location"])
    known = rag_spaces.known_depts(rag_spaces.config(tmp_path), {})
    assert {"기획처", "기획팀", "학생처"} <= set(known) and "새이름과" not in known
    # 별칭으로 만들면 정식 이름으로
    c.post("/dev/users/dept", data={"action": "create", "name": "다라사업단"})
    assert "다라2.0사업단" in rag_spaces.known_depts(rag_spaces.config(tmp_path), {})
    page = c.get("/dev/users").text
    assert "규정 제안" not in page and 'data-parent-open="기획팀"' not in page      # 상위 부서는 조직도에서 옮긴다(10-25)


def test_dept_parent_route_admin_only_and_no_cycle(tmp_path):
    app, c = _client(tmp_path)
    for n in ("기획처", "다라2.0사업단"):
        c.post("/dev/users/dept", data={"action": "create", "name": n})
    r = c.post("/dev/users/dept-parent", data={"dept": "다라2.0사업단", "parent": "기획처"}, follow_redirects=False)
    assert "ok=" in r.headers["location"] and dd.children_map(tmp_path) == {"기획처": ["다라2.0사업단"]}
    r = c.post("/dev/users/dept-parent", data={"dept": "기획처", "parent": "다라2.0사업단"}, follow_redirects=False)
    assert "err=" in r.headers["location"] and "순환" in unquote(r.headers["location"])
    r = c.post("/dev/users/dept-parent", data={"dept": "기획처", "parent": "없는부서"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    staff = TestClient(app)
    assert _login(staff, "zzaimy", "boot-pass-1")
    assert staff.post("/dev/users/dept-parent", data={"dept": "다라2.0사업단", "parent": ""}, follow_redirects=False).status_code == 403
    assert dd.children_map(tmp_path) == {"기획처": ["다라2.0사업단"]}


def test_dual_post_rows_save_grants_and_grades(tmp_path):
    app, c = _client(tmp_path)
    for n in ("기획처", "다라2.0사업단", "학생처"):
        c.post("/dev/users/dept", data={"action": "create", "name": n})
    page = c.get("/dev/users").text
    assert "겸직 부서" in page and "data-extra-add" in page and 'name="extra_grade"' in page
    r = c.post("/dev/users/save", data={"uid": "zzaimy", "role": "staff", "dept": "기획처", "active": "1", "grade": "member",
                                        "extra": ["다라2.0사업단", "학생처", "기획처", "없는부서"],
                                        "extra_grade": ["head", "member", "lead", "head"]}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    a = app.state.accounts["zzaimy"]
    assert a["dept_grades"] == {"다라2.0사업단": "head"}                       # 일반은 따로 적지 않음, 소속·모르는 부서는 뺌
    grants = rag_spaces.config(tmp_path)["grants"]["zzaimy"]
    assert sorted(grants) == ["dept:다라2.0사업단", "dept:학생처"]
    page = c.get("/dev/users").text
    assert "겸직: 다라2.0사업단(총괄 책임자), 학생처(일반)" in page
    # 겸직을 빼면 둘 다에서 빠진다
    c.post("/dev/users/save", data={"uid": "zzaimy", "role": "staff", "dept": "기획처", "active": "1", "grade": "member",
                                    "extra": ["학생처"], "extra_grade": ["clerk"]})
    a = app.state.accounts["zzaimy"]
    assert a["dept_grades"] == {"학생처": "clerk"}
    assert rag_spaces.config(tmp_path)["grants"]["zzaimy"] == ["dept:학생처"]


def test_deleting_dept_clears_stale_grants(tmp_path):
    """보안: 부서를 지우면 확정 위계와 계정의 겸직 등급에서도 빠진다(같은 이름으로 다시 만들어도 옛 권한이 되살아나지 않게)."""
    from zzaimy.app import dept_dictionary as dd
    (tmp_path / dd.PARENTS_FILE).write_text('{"가나사업단": "산학협력단", "다라팀": "가나사업단"}', encoding="utf-8")
    dd.forget_dept(tmp_path, "가나사업단")
    assert dd.parents_override(tmp_path) == {}
