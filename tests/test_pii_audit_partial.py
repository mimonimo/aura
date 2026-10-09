"""개인정보 잔여 검사 표본(limit)은 저장된 전체 검사 결과를 덮지 않는다(배포 점검이 /dev/pii 수치를 바꾸지 않게)."""
from zzaimy.app import pii_audit
from zzaimy.app.db import Database


def test_partial_scan_does_not_overwrite_saved_full_scan(tmp_path):
    db = Database(tmp_path / "t.db")
    full = pii_audit.run_scan(db)
    saved = db.get_setting(pii_audit.SCAN_KEY, "")
    part = pii_audit.run_scan(db, limit=1)
    assert part["partial"] is True and full["partial"] is False
    assert db.get_setting(pii_audit.SCAN_KEY, "") == saved
