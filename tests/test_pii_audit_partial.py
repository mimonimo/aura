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


def test_scan_reports_policy_and_enforced_hits(tmp_path):
    """내부 마스킹 정책이 꺼져 있으면 「가렸어야 할 잔여」는 0 — 켜면 그 항목의 잔여만 센다."""
    from zzaimy.app import privacy_policy

    db = Database(tmp_path / "t.db")
    r = pii_audit.run_scan(db)
    assert r["policy_enabled"] is False and r["enforced_hits"] == 0
    privacy_policy.save(db, True, ["KR_PHONE"], "admin")
    r2 = pii_audit.run_scan(db)
    assert r2["policy_enabled"] is True and r2["enforced_hits"] == r2["by_type"].get("KR_PHONE", 0)
