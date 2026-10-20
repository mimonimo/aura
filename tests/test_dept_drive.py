"""부서 드라이브 — 플랫폼이 만든 「AURA · <부서>」 폴더에 그 부서 사람(소속 + 추가 부서) 구글 계정만 편집 권한(10-21)."""
import json

import httpx

from zzaimy.ingest import dept_drive, gdrive_files


class FakeDB:
    def __init__(self):
        self.s = {}

    def get_setting(self, k, d=""):
        return self.s.get(k, d)

    def set_setting(self, k, v):
        self.s[k] = v


def _http(sent):
    def handler(req):
        body = json.loads(req.content or b"{}")
        sent.append((req.method, req.url.path, body))
        if req.method == "POST" and req.url.path.endswith("/files"):
            return httpx.Response(200, json={"id": "FOLDER1"})
        return httpx.Response(200, json={"id": "P-" + body.get("emailAddress", "x")})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_connect_creates_folder_and_sync_follows_members(monkeypatch, tmp_path):
    monkeypatch.setattr(gdrive_files, "has_file_scope", lambda e: True)
    monkeypatch.setattr(gdrive_files, "_headers", lambda e, h: {"Authorization": "Bearer x"})
    monkeypatch.setattr("zzaimy.app.access_policy.extra_depts", lambda u, d=None: ["기획처"] if u == "extra" else [])
    db, sent = FakeDB(), []
    http = _http(sent)
    accounts = {"a": {"role": "staff", "dept": "기획처"}, "b": {"role": "staff", "dept": "산학"},
                "extra": {"role": "staff", "dept": "산학"}, "new": {"role": "staff", "dept": "기획처", "must_change": True},
                "stu": {"role": "student", "dept": "기획처"}}
    for u in accounts:
        db.set_setting(f"google_account:{u}", f"{u}@ync.ac.kr")
    fid = dept_drive.connect(db, "기획처", "admin@ync.ac.kr", "PARENT", http=http)
    assert fid == "FOLDER1" and sent[0][2]["name"] == "AURA · 기획처" and sent[0][2]["parents"] == ["PARENT"]
    dept_drive.sync(db, "기획처", accounts, tmp_path, http=http)
    granted = {b["emailAddress"] for m, p, b in sent if m == "POST" and p.endswith("/permissions")}
    assert granted == {"a@ync.ac.kr", "extra@ync.ac.kr"}           # 소속 + 추가 부서, 첫 비번·학생·다른 부서 제외
    assert all(b["role"] == "writer" for m, p, b in sent if p.endswith("/permissions") and m == "POST")
    accounts["a"]["dept"] = "산학"                                   # 부서를 옮기면 다음 맞추기에서 거둔다
    sent.clear()
    got = dept_drive.sync(db, "기획처", accounts, tmp_path, http=http)
    assert got["removed"] == 1 and any(m == "DELETE" for m, _p, _b in sent)
    st = dept_drive.state(db, "기획처", accounts, tmp_path)
    assert st["connected"] and st["granted"] == 1 and st["members"] == ["extra"]
