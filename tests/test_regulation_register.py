"""반입한 규정을 기준 문서로 — 후보(판 묶음마다 최신 판, 규정을 다루는 다른 서류 제외)·등록·해제."""
from pathlib import Path

from zzaimy.app import regulation_register as rr
from zzaimy.app.db import Database

BODY = "\n".join(f"제{i}조(목적) 이 규정은 산학협력단의 운영에 관한 사항을 정한다. 세부 사항은 따로 정한다." for i in range(1, 6))


def _doc(db, name, kind="regulation", family=None, text=BODY):
    did = db.add_document(name, "x", "grant")
    with db._conn() as conn:
        conn.execute("UPDATE documents SET status = 'reviewed', kind = ?, family = ?, masked_text = ? WHERE id = ?",
                     (kind, family, text, did))
    return did


def test_candidates_newest_version_and_excludes_agreements(tmp_path):
    db = Database(tmp_path / "r.db")
    old = _doc(db, "영남이공대학교 연구비 관리 규정 · 2012년 2월 23일", family="연구비관리규정")
    new = _doc(db, "영남이공대학교 연구비 관리 규정 · 2024년 3월 1일", family="연구비관리규정")
    _doc(db, "LINC3.0 협약서", kind="regulation")
    _doc(db, "현장실습 운영 지침", kind="guideline")
    _doc(db, "(주)대영코어텍", kind="regulation")                     # 갈래만 규정 — 제목이 규정 이름이 아니다
    _doc(db, "사업 계획서", kind="plan")
    got = rr.candidates(db)
    titles = {i["title"]: i for i in got["items"]}
    assert set(titles) == {"영남이공대학교 연구비 관리 규정 · 2024년 3월 1일", "현장실습 운영 지침"}
    assert titles["영남이공대학교 연구비 관리 규정 · 2024년 3월 1일"]["id"] == new and titles["영남이공대학교 연구비 관리 규정 · 2024년 3월 1일"]["versions"] == 2
    assert old not in {i["id"] for i in got["items"]}
    assert [i["title"] for i in rr.candidates(db, "현장 실습")["items"]] == ["현장실습 운영 지침"]


def test_register_then_unregister_only_what_we_registered(tmp_path):
    db = Database(tmp_path / "r.db")
    did = _doc(db, "산학협력단 운영 규정")
    native = db.add_document("기본계획", "x", "regulation")
    with db._conn() as conn:
        conn.execute("UPDATE documents SET status = 'reviewed' WHERE id = ?", (native,))
    out = rr.register(db, [did], tmp_path)
    assert out["registered"] == 1 and out["chunks"] > 0 and db.get_document(did)["doc_type"] == "regulation"
    assert (tmp_path / ".reindex-needed").exists() and rr.candidates(db)["registered"][0]["id"] in (did, native)
    back = rr.unregister(db, [did, native], tmp_path)
    assert back == {"unregistered": 1, "kept": 1}
    assert db.get_document(did)["doc_type"] == "grant" and db.get_document(native)["doc_type"] == "regulation"
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM regulation_chunks WHERE doc_id = ?", (did,)).fetchone()[0] == 0
