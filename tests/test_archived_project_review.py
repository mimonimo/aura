"""보관 묶음과 담당자 업무 공간의 분리 회귀 검사."""

from zzaimy.app import archive
from zzaimy.app.db import Database


def test_archive_group_does_not_reuse_active_user_project(tmp_path):
    db = Database(tmp_path / "review.db")
    active = db.create_project("grant", "2026 신규 업무", owner="kim", program="program:rise")
    grouped = archive.program_project(db, "program:rise", "과거 RISE 자료")
    assert grouped != active
    assert db.get_project(grouped)["archived"] == 1


def test_sync_preserves_manually_archived_empty_project(tmp_path):
    db = Database(tmp_path / "review.db")
    manual = db.create_project("grant", "담당자가 보관한 업무", owner="zzdev", archived=True)
    archive.align_archived_projects(db)
    assert db.get_project(manual) is not None


def test_unarchived_group_is_not_reused_for_new_intake(tmp_path):
    db = Database(tmp_path / "review.db")
    original = archive.program_project(db, "program:rise", "RISE 자료")
    db.set_project_archived(original, False)
    new = archive.program_project(db, "program:rise", "RISE 자료")
    assert new != original
    assert db.get_project(original)["archived"] == 0
    assert db.get_project(new)["archive_source"] == "dgx"
    assert archive.program_project(db, "program:rise", "다른 표시명") == new
