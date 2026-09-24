"""열람 등급은 문서 경로 전부에 강제된다 — 다른 부서 담당자는 화면·원본·내보내기·삭제 어디로도 못 들어간다(절대 규칙 4)."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app.db import Database
from zzaimy.app.main import create_app


def _client(tmp_path, uid: str, pw: str) -> TestClient:
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(), password="boot-pass-1")
    c = TestClient(app)
    r = c.post("/login", data={"username": uid, "pw": pw}, follow_redirects=False)
    assert r.status_code == 303
    return c


def test_department_restricted_document_is_invisible_to_other_departments(tmp_path):
    (tmp_path / "accounts.json").write_text(json.dumps({
        "kim": {"pw": "kim-pass", "role": "staff", "dept": "학생처"},
        "lee": {"pw": "lee-pass", "role": "staff", "dept": "산학협력단"},
        "zzdev": {"pw": "dev-pass", "role": "dev"},
    }))
    db = Database(tmp_path / "t.db")
    src = tmp_path / "x.txt"; src.write_text("학생처 접수 서류")
    did = db.add_document("학생처 서류.txt", str(src), doc_type="auto", owner="kim", dept="학생처", access_level="dept")
    db.update_document(did, status="reviewed", masked_text="학생처 접수 서류", draft="## 초안")
    pub = db.add_document("공고.txt", str(src), doc_type="regulation")
    db.update_document(pub, status="reviewed", masked_text="공개 공고")

    lee = _client(tmp_path, "lee", "lee-pass")
    for path in (f"/doc/{did}", f"/doc/{did}/original", f"/doc/{did}/export.md", f"/doc/{did}/status", f"/doc/{did}/draft.md"):
        assert lee.get(path).status_code == 404, path
    assert lee.post(f"/doc/{did}/delete", follow_redirects=False).status_code == 404
    assert lee.get(f"/doc/{pub}").status_code == 200                       # 공개 문서는 누구나
    assert db.get_document(did) is not None                                # 지워지지 않았다

    kim = _client(tmp_path, "kim", "kim-pass")
    assert kim.get(f"/doc/{did}").status_code == 200 and kim.get(f"/doc/{did}/original").status_code == 200
    dev = _client(tmp_path, "zzdev", "dev-pass")
    assert dev.get(f"/doc/{did}/status").status_code == 200

    # 담당자 한정 문서는 올린 사람만 — 같은 부서라도 못 본다
    own = db.add_document("개인 서류.txt", str(src), doc_type="auto", owner="kim", dept="학생처", access_level="owner")
    db.update_document(own, status="reviewed", masked_text="x")
    park_accounts = json.loads((tmp_path / "accounts.json").read_text())
    park_accounts["park"] = {"pw": "park-pass", "role": "staff", "dept": "학생처"}
    (tmp_path / "accounts.json").write_text(json.dumps(park_accounts))
    park = _client(tmp_path, "park", "park-pass")
    assert park.get(f"/doc/{own}").status_code == 404 and kim.get(f"/doc/{own}").status_code == 200
