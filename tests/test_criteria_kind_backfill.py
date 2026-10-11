"""공고 갈래 기준 문서 소급 연결(scripts/182) — 본 적 없는 합성 프로젝트로 확인한다."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from zzaimy.app.db import Database

_SPEC = importlib.util.spec_from_file_location(
    "criteria_kind_backfill", Path(__file__).resolve().parents[1] / "scripts" / "182_criteria_kind_backfill.py")
backfill = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(backfill)


def _setup(tmp_path):
    db = Database(tmp_path / "b.db")
    pid = db.create_project("grant", "가상 갯벌 복원 사업", owner="kim")
    other_pid = db.create_project("grant", "가상 다른 사업", owner="lee")
    up = db.add_document("모집 공고.pdf", "/tmp/a.pdf", doc_type="grant", project_id=pid)
    db.set_document_kind(up, "announcement")
    form = db.add_document("신청 양식.hwpx", "/tmp/b.hwpx", doc_type="grant", project_id=pid)
    db.set_document_kind(form, "form")
    sid = db.create_chat_session("질문", project_id=pid, owner="kim")
    att = db.add_document("평가 지표 안내.pdf", "/tmp/c.pdf", doc_type="grant", dept="공통", access_level="dept")
    db.add_file("attachment", "/tmp/c.pdf", name="평가 지표 안내.pdf", session_id=sid, doc_id=att)
    db.set_document_kind(att, "criteria")
    done = db.add_document("이미 이은 지침.pdf", "/tmp/d.pdf", doc_type="grant", project_id=pid)
    db.set_document_kind(done, "guideline")
    db.add_project_criteria(pid, [done])
    # 남의 부서 한정 문서가 다른 소유자 프로젝트 대화에 첨부된 경우 — 소유자가 못 보면 잇지 않는다
    sid2 = db.create_chat_session("질문", project_id=other_pid, owner="lee")
    hidden = db.add_document("부서 한정 계획.pdf", "/tmp/e.pdf", doc_type="grant", dept="기획처", access_level="dept")
    db.set_document_kind(hidden, "basic_plan")
    db.add_file("attachment", "/tmp/e.pdf", name="부서 한정 계획.pdf", session_id=sid2, doc_id=hidden)
    loose = db.add_document("프로젝트 없는 공고.pdf", "/tmp/f.pdf", doc_type="grant")
    db.set_document_kind(loose, "announcement")
    return db, pid, other_pid, {"up": up, "form": form, "att": att, "done": done, "hidden": hidden, "loose": loose}


def test_preview_writes_nothing_and_apply_links_with_scope(tmp_path, capsys):
    db, pid, other_pid, d = _setup(tmp_path)
    accounts = {"kim": {"role": "staff", "dept": "산학협력단"}, "lee": {"role": "staff", "dept": "교무처"}}

    got = backfill.run(db, accounts)
    assert got == {"planned": 2, "added": 0, "projects": 0, "skipped": 1}
    assert set(db.get_project_criteria_ids(pid)) == {d["done"]}            # 미리보기는 쓰지 않는다
    assert "미리보기" in capsys.readouterr().out

    got = backfill.run(db, accounts, apply=True)
    assert got["added"] == 2 and got["projects"] == 1 and got["skipped"] == 1
    assert set(db.get_project_criteria_ids(pid)) == {d["done"], d["up"], d["att"]}
    assert db.get_project_criteria_ids(other_pid) == []                    # 소유자가 볼 수 없는 문서는 잇지 않는다

    assert backfill.run(db, accounts, apply=True)["planned"] == 0          # 두 번 돌려도 그대로

    accounts["lee"]["dept"] = "기획처"                                     # 같은 부서면 잇는다
    assert backfill.run(db, accounts, apply=True, project_ids={other_pid})["added"] == 1
    assert db.get_project_criteria_ids(other_pid) == [d["hidden"]]


def test_project_filter_limits_scope(tmp_path):
    db, pid, other_pid, d = _setup(tmp_path)
    got = backfill.run(db, {}, apply=True, project_ids={other_pid})
    assert got["added"] == 0 and set(db.get_project_criteria_ids(pid)) == {d["done"]}
