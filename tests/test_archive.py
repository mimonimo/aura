from zzaimy.app import archive
from zzaimy.app.db import Database


def test_archive_load_summary_find(tmp_path):
    db = Database(tmp_path / "t.db")
    rows = [{"rel": "링크/LINC3.0 모음/1차년도/계획서.hwp", "size": 10, "mtime": 1, "ext": "hwp", "area": "링크",
             "program": "program:linc30", "program_name": "LINC3.0", "status": "auto", "kind": "plan", "year": 2022, "round": 1},
            {"rel": "링크/LINC3.0 모음/1차년도/실적보고서.hwp", "size": 20, "mtime": 1, "ext": "hwp", "area": "링크",
             "program": "program:linc30", "program_name": "LINC3.0", "status": "auto", "kind": "report", "year": 2023, "round": 1}]
    got = archive.load(db, rows, origins={"링크/LINC3.0 모음/1차년도/계획서.hwp": 577})
    assert got["rows"] == 2
    s = {r["kind"]: r for r in archive.summary(db)}
    assert s["plan"]["in_store"] == 1 and s["report"]["in_store"] == 0
    assert archive.find(db, program="program:linc30", kind="report")[0]["rel"].endswith("실적보고서.hwp")
    archive.load(db, rows[:1])                       # 다시 들여도 문서 번호는 남는다
    assert archive.find(db, text="계획서")[0]["doc_id"] == 577


def test_archive_page_renders(tmp_path):
    from fastapi.testclient import TestClient

    from tests.test_accounts import _app, _login

    app = _app(tmp_path)
    archive.load(app.state.db, [{"rel": "앵커/1차년도/계획서.hwp", "size": 1, "mtime": 1, "ext": "hwp", "area": "앵커",
                                 "program": "program:rise", "program_name": "RISE사업", "status": "auto", "kind": "plan"}])
    c = TestClient(app)
    assert _login(c, "zzaimy", "boot-pass-1")
    r = c.get("/archive?program=program:rise")
    assert r.status_code == 200 and "앵커/1차년도/계획서.hwp" in r.text and "RISE사업" in r.text
    assert "/archive" in c.get("/criteria").text


def test_archive_sync_add_change_move_remove(tmp_path):
    db = Database(tmp_path / "t.db")
    base = {"ext": "hwp", "area": "링크", "program": "program:linc30", "program_name": "LINC3.0", "status": "auto", "kind": "plan"}
    a = {"rel": "링크/a/계획서.hwp", "size": 10, "mtime": 1, **base}
    b = {"rel": "링크/a/보고서.hwp", "size": 20, "mtime": 1, **base}
    c = {"rel": "링크/a/평가.hwp", "size": 30, "mtime": 1, **base}
    archive.sync(db, [a, b, c], origins={a["rel"]: 577})
    moved_a = {**a, "rel": "링크/옮김/계획서.hwp"}                 # 옮김(이름·크기·시각 같음)
    b2 = {**b, "size": 21, "mtime": 2}                           # 바뀜
    d = {"rel": "링크/a/새.hwp", "size": 5, "mtime": 3, **base}    # 새로
    got = archive.sync(db, [moved_a, b2, d])                       # c 는 없어짐
    assert got["moved"] == [(a["rel"], moved_a["rel"], 577)]
    assert got["changed"] == [(b["rel"], None)] and got["added"] == [d["rel"]] and got["removed"] == [(c["rel"], None)]
    rels = {r["rel"] for r in archive.find(db, limit=10)}
    assert rels == {moved_a["rel"], b["rel"], d["rel"]}
    assert archive.find(db, text="옮김")[0]["doc_id"] == 577
    again = archive.sync(db, [moved_a, b2, d])                     # 바뀐 것 없음
    assert not (again["added"] or again["changed"] or again["moved"] or again["removed"])
