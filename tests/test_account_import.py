"""사용자 일괄 등록(11-15) — 엑셀·CSV 로 계정 여러 개, 임시 비밀번호는 응답에만, 관리자만, 없는 부서는 고른 때만 만든다."""
from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import account_import
from zzaimy.app.main import create_app


@pytest.fixture
def app(tmp_path):
    (tmp_path / "accounts.json").write_text(json.dumps({
        "zzdev": {"pw": "devpass", "role": "dev"},
        "kim": {"pw": "kim-pass-1", "role": "staff", "dept": "기획처"},
    }))
    return create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                      drafter=FakeDrafter(), password="boot-pass-1")


def _as(app, uid, pw) -> TestClient:
    c = TestClient(app)
    assert c.post("/login", data={"username": uid, "pw": pw}, follow_redirects=False).status_code == 303
    return c


CSV = "아이디,이름,부서,역할,등급\nlee01,이영희,기획처,교직원,사업 책임자\npark02,박철수,신설팀,,일반\nBad!,잘못,기획처,,\n"


def test_import_creates_accounts_and_returns_temp_passwords_once(app):
    c = _as(app, "zzdev", "devpass")
    r = c.post("/team/accounts/import", files={"file": ("u.csv", CSV.encode("utf-8"), "text/csv")})
    j = r.json()
    assert [d["uid"] for d in j["done"]] == ["lee01"]
    assert {f["line"] for f in j["failed"]} == {3, 4}                     # 없는 부서(고르지 않음)·잘못된 아이디
    a = app.state.accounts["lee01"]
    assert a["role"] == "staff" and a["dept"] == "기획처" and a.get("grade") == "lead"
    assert j["done"][0]["pw"] and j["done"][0]["pw"] not in json.dumps(a)   # 해시로만 저장

    r = c.post("/team/accounts/import", files={"file": ("u.csv", CSV.encode("utf-8"), "text/csv")}, data={"make_depts": "1"})
    j = r.json()
    assert [d["uid"] for d in j["done"]] == ["park02"]                     # 신설팀을 만들고 등록, lee01 은 이미 있음
    assert app.state.accounts["park02"]["dept"] == "신설팀"


def test_import_reads_cp949_csv_and_xlsx():
    rows = account_import.parse(CSV.encode("cp949"), "u.csv")
    assert rows[0]["uid"] == "lee01" and rows[0]["grade"] == "사업 책임자"
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in [["아이디", "이름"], ["choi03", "최"]]:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    rows = account_import.parse(buf.getvalue(), "u.xlsx")
    assert rows == [{"uid": "choi03", "name": "최", "dept": "", "role": "", "grade": "", "parent": "", "line": 2}]
    with pytest.raises(ValueError, match="아이디"):
        account_import.parse("이름\n홍\n".encode("utf-8"), "u.csv")


def test_import_is_admin_only(app):
    c = _as(app, "kim", "kim-pass-1")
    r = c.post("/team/accounts/import", files={"file": ("u.csv", CSV.encode("utf-8"), "text/csv")}, follow_redirects=False)
    assert r.status_code == 403 and "lee01" not in app.state.accounts
    assert c.get("/team/accounts/import-template.csv", follow_redirects=False).status_code == 403


def test_temp_password_shape():
    for _ in range(20):
        pw = account_import.temp_password()
        assert len(pw) == 12 and any(ch.isdigit() for ch in pw) and not set(pw) & set("0O1lI")
