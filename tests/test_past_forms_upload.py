"""문서함에서 지난 양식 올리기 — 「지난 사업 문서」·「공통 양식」 에서 올리면 past_forms.classify 로 제자리(잠긴 양식)에 두고,
개인 정보·채워진 문서·같은 본문은 쉬운 까닭과 함께 거절한다. 다른 자리 올리기는 그대로. 이름·사업은 모두 지어낸 것."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_accounts import _login
from tests.test_past_forms_tidy import _hwpx, _p, _table
from tests.test_webhwp import _setup
from zzaimy.app import past_forms as pf
from zzaimy.app import webhwp, webhwp_routes

PW = "pass-12345678"
J = {"Accept": "application/json"}
ROWS = [{"key": "k1", "name": "가나다라 혁신사업", "terms": ["가나혁신"], "ids": {"k1", "program:가나다라혁신사업"},
         "merged_into": "", "period": "2020~2022", "years": {}}]


def plan() -> bytes:
    return _hwpx(_p("사업 계획서") + _p("※ 작성 요령: ○○ 칸은 기관에 맞게 적는다")
                 + _table([["사업명", ""], ["기간", ""], ["예산", ""], ["담당 부서", ""]]), fields=False)


def program_report() -> bytes:
    return _hwpx(_p("가나혁신 결과보고서") + _p("가나혁신 사업의 결과를 ○○ 에 맞게 적는다")
                 + _table([["과제명", ""], ["성과", ""], ["비고", ""]]), fields=False)


def with_account() -> bytes:
    return _hwpx(_p("지급 신청서") + _p("※ 작성 요령: ○○ 칸을 적는다")
                 + _table([["입금계좌", "123-456-7890123"], ["소속", ""], ["비고", ""]]), fields=False)


def filled() -> bytes:
    return _hwpx(_p("참가 신청서")
                 + _table([["신청자", "박다라"], ["소속", "가나학과"], ["신청일", "2024. 3. 15."], ["금액", "1,250,000"],
                           ["기간", "2024. 4. 1. ~ 2024. 5. 30."], ["담당자", "이마바"]]), fields=False)


def _app(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(webhwp_routes, "_programs", lambda request: pf.Programs(app.state.db, tmp_path, rows=ROWS))
    kim = TestClient(app)
    assert _login(kim, "kim", PW)
    return app, kim


def _post(c, name, data, folder=pf.COMMON_ROOT, dept=webhwp.COMMON, json=True):
    return c.post("/hwp/upload", data={"dept": dept, "folder": folder}, files={"file": (name, data, "x")},
                  headers=J if json else {}, follow_redirects=False)


def test_kept_form_goes_to_its_place_locked(tmp_path, monkeypatch):
    app, kim = _app(tmp_path, monkeypatch)
    r = _post(kim, "사업 계획서.hwpx", plan(), folder=f"{pf.ROOT}")
    j = r.json()
    assert r.status_code == 200 and j["ok"] and j["folder"] == "공통 양식/계획서"
    assert j["message"] == "「공통 양식/계획서」에 넣었습니다"
    f = webhwp.get(app.state.db, j["fid"])
    assert webhwp.folder_of(tmp_path, f["path"]) == "공통 양식/계획서" and f["path"].endswith(".hwpx")
    assert webhwp.locked(app.state.db, j["fid"]) and webhwp.meta(app.state.db, j["fid"])["source"] == "양식:upload-kim"
    assert not pf.platform_made("양식:upload-kim") and pf.in_scope("공통 양식/계획서", "양식:upload-kim") == ""
    # 잠긴 양식 — 올린 사람도 고치지 못하고 사본으로 쓴다
    assert kim.post(f"/hwp/{j['fid']}/save", content=plan()).status_code == 403
    # 사업 전용 양식은 지난 사업 문서/<사업>/<목적>
    j = _post(kim, "가나혁신 결과보고서.hwpx", program_report(), folder="공통 양식/계획서").json()
    assert j["ok"] and j["folder"] == "지난 사업 문서/가나다라 혁신사업/결과보고서"


def test_rejections_in_plain_words(tmp_path, monkeypatch):
    app, kim = _app(tmp_path, monkeypatch)
    before = len(app.state.db.list_files(kind="webhwp"))
    j = _post(kim, "지급 신청서.hwpx", with_account()).json()
    assert not j["ok"] and j["message"] == "개인 정보(계좌번호)가 있어 올리지 않았습니다"
    j = _post(kim, "참가 신청서.hwpx", filled()).json()
    assert not j["ok"] and "채워진" in j["message"]
    j = _post(kim, "깨진.hwpx", b"PKnot a zip").json()
    assert not j["ok"] and "열리지 않아" in j["message"]
    assert len(app.state.db.list_files(kind="webhwp")) == before
    # 같은 본문은 이름이 달라도 한 번만
    assert _post(kim, "사업 계획서.hwpx", plan()).json()["ok"]
    j = _post(kim, "계획서 사본.hwpx", plan(), folder=pf.ROOT).json()
    assert not j["ok"] and j["message"] == "이미 있는 양식입니다"


def test_form_post_redirects_with_message_and_page_marks_sorting(tmp_path, monkeypatch):
    app, kim = _app(tmp_path, monkeypatch)
    r = _post(kim, "지급 신청서.hwpx", with_account(), json=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    page = kim.get("/hwp", params={"dept": webhwp.COMMON, "folder": pf.COMMON_ROOT}).text
    assert "data-sort" in page and " multiple" in page
    page = kim.get("/hwp", params={"dept": "기획처"}).text
    assert "data-sort" not in page


def test_other_places_unchanged_and_students_blocked(tmp_path, monkeypatch):
    app, kim = _app(tmp_path, monkeypatch)
    # 부서 폴더 — 판정 없이 그대로(채워진 문서도), 잠그지 않는다
    r = _post(kim, "참가 신청서.hwpx", filled(), dept="기획처", folder="", json=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    fid = max(f["id"] for f in app.state.db.list_files(kind="webhwp"))
    assert not webhwp.locked(app.state.db, fid) and webhwp.meta(app.state.db, fid)["source"] == "upload"
    # 공통의 다른 폴더도 그대로
    r = _post(kim, "메모.hwpx", filled(), folder="", json=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    # 학생은 문서함에 올리지 못한다
    app.state.accounts["stu"] = app.state.new_account("stu", PW, "student")
    stu = TestClient(app)
    assert _login(stu, "stu", PW)
    assert _post(stu, "사업 계획서.hwpx", plan()).status_code in (302, 303, 403)
    assert not any(webhwp.meta(app.state.db, f["id"]).get("owner") == "stu" for f in app.state.db.list_files(kind="webhwp"))


def test_small_program_folder_kept_flat(tmp_path, monkeypatch):
    app, kim = _app(tmp_path, monkeypatch)
    d = webhwp.folder_dir(tmp_path, webhwp.COMMON, "지난 사업 문서/가나다라 혁신사업")
    d.mkdir(parents=True)
    (d / "옛 양식.hwpx").write_bytes(b"x")
    j = _post(kim, "가나혁신 결과보고서.hwpx", program_report(), folder=pf.ROOT).json()
    assert j["ok"] and j["folder"] == "지난 사업 문서/가나다라 혁신사업"
    assert Path(webhwp.get(app.state.db, j["fid"])["path"]).parent == d
