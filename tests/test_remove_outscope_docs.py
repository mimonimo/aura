"""지출·계약 증빙 갈래 문서를 라이브러리에서 뺀다 — 백업 파일로 되돌리면 문서·조각·원본 이음이 그대로 돌아온다."""
import importlib.util
from pathlib import Path

from tests.test_accounts import _app
from zzaimy.app import archive

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rm187", ROOT / "scripts" / "187_remove_outscope_docs.py")
rm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rm)


def test_remove_and_restore(tmp_path):
    db = _app(tmp_path).state.db
    archive.ensure(db)
    out = db.add_document("영수증.pdf", "dgx:링크/지출/영수증.pdf", doc_type="grant")
    keep = db.add_document("계획서.hwp", "dgx:링크/계획서.hwp", doc_type="grant")
    mixed = db.add_document("결과.hwp", "dgx:링크/결과.hwp", doc_type="grant")
    with db._conn() as conn:
        for rel, did in (("링크/지출/영수증.pdf", out), ("링크/계획서.hwp", keep), ("링크/결과.hwp", mixed), ("링크/정산/계획서2.hwp", keep),
                         ("링크/지출/결과.hwp", mixed)):
            conn.execute("INSERT INTO archive_files (rel, size, mtime, ext, dup_of, doc_id) VALUES (?, 1, 0, ?, '', ?)",
                         (rel, rel.rsplit(".", 1)[1], did))
        conn.execute("UPDATE documents SET kind = 'evidence' WHERE id = ?", (out,))
        conn.execute("UPDATE documents SET kind = 'plan' WHERE id IN (?, ?)", (keep, mixed))
        conn.execute("INSERT INTO doc_chunks (doc_id, seq, kind, page_no, content, bbox) VALUES (?, 0, 'text', 1, '영수증', '')", (out,))
    assert rm.targets(db) == [out]                       # 정산 폴더에 있어도 계획서는 남긴다
    bak = tmp_path / "b.jsonl.gz"
    assert rm.backup(db, [out], bak) >= 2
    rm.remove(db, [out])
    assert db.get_document(out) is None
    with db._conn() as conn:
        assert conn.execute("SELECT doc_id FROM archive_files WHERE rel = ?", ("링크/지출/영수증.pdf",)).fetchone()[0] is None
        assert conn.execute("SELECT COUNT(*) FROM doc_chunks WHERE doc_id = ?", (out,)).fetchone()[0] == 0
    rm.restore(db, bak)
    assert db.get_document(out)["filename"] == "영수증.pdf"
    with db._conn() as conn:
        assert conn.execute("SELECT doc_id FROM archive_files WHERE rel = ?", ("링크/지출/영수증.pdf",)).fetchone()[0] == out
        assert conn.execute("SELECT COUNT(*) FROM doc_chunks WHERE doc_id = ?", (out,)).fetchone()[0] == 1
