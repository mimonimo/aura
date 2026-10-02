"""반입 현황(개발 현황 탭·/dev/api/intake)의 원천 — 수치는 DB·장부에서 바로 센다."""
import json

from zzaimy.app import archive, intake_status
from zzaimy.app.db import Database


def test_snapshot_counts_archive_store_and_sync(tmp_path):
    db = Database(tmp_path / "t.db")
    pid = db.create_project("grant", archive.dgx_project_label("RISE사업"), owner="zzdev")
    a = db.add_document("a.pdf", "dgx://p/a.pdf", doc_type="grant", project_id=pid)
    db.update_document(a, status="reviewed", parse_note="글자층 직독 (쪽수 3) · DGX 보관(가벼운 처리: 검토 의견 없음, OCR 품질 미검사)")
    b = db.add_document("b.hwp", "/local/b.hwp", doc_type="grant")
    archive.load(db, [dict(rel="p/a.pdf", size=1, mtime=1, area="링크", program_name="RISE사업", status="auto"),
                      dict(rel="p/c.pdf", size=2, mtime=1, area="앵커", status="agent")], {"p/a.pdf": a})
    (tmp_path / "sync_status.json").write_text(json.dumps({"parsed": {"at": "2026-10-02 17:00", "summary": "들임 3"}}), encoding="utf-8")
    got = intake_status.snapshot(db, force=True)
    assert got["archive"]["total"] == 2 and got["archive"]["linked"] == 1
    assert got["store"]["total"] == 2 and got["store"]["dgx"] == 1 and got["store"]["full"] == 1
    assert {"label": "글자층 직독", "n": 1} in got["store"]["paths"]
    assert {"label": "OCR 품질 미검사", "n": 1} in got["store"]["flags"]
    assert got["programs"] == [{"label": "RISE사업", "n": 1}]
    assert got["sync"]["parsed"]["summary"] == "들임 3"
    assert b
