"""문답 검수(/review) — 검수 권한(SFT 문답 검수) 관문, 매뉴얼 그리기, 라벨 스튜디오 진행 세기, 검수자 만들기 스크립트(218) 미리 보기(10-27)."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest
from tests.test_staff_grades import _add, _client, _dev, _team

from zzaimy.app import grades, passwords, review_routes
from zzaimy.dataset.ls_client import LabelStudioError
from zzaimy.ingest import gdocs, gdrive


@pytest.fixture(autouse=True)
def _no_google(monkeypatch):
    monkeypatch.setattr(gdrive, "list_accounts", lambda: [])
    monkeypatch.setattr(gdocs, "has_docs_scope", lambda email: True)
    monkeypatch.setattr(gdrive, "configured", lambda: True)


class FakeLS:
    """라벨 스튜디오 대역 — 할 일 data 와 annotation 수만 든다. count_tasks 는 거르기 항목을 실제로 해석한다."""

    def __init__(self, tasks=None, users=None):
        self.tasks = tasks or []
        self._users = users or []
        self.calls = []
        self.patched = []
        self.created = []

    def _match(self, t, f):
        if f["filter"] == "filter:tasks:data.assignee":
            return (t["data"].get("assignee") or "") == f["value"]
        if f["filter"] == "filter:tasks:total_annotations":
            return t.get("ann", 0) > f["value"]
        raise AssertionError(f)

    def count_tasks(self, project_id, filters, timeout=None):
        self.calls.append((project_id, filters))
        return sum(1 for t in self.tasks if all(self._match(t, f) for f in filters))

    def _projects(self, timeout=None):
        return [{"id": 8, "title": review_routes.DEFAULT_PROJECT}]

    def iter_tasks(self, project_id, page_size=500):
        yield from self.tasks

    def set_task_data(self, task_id, data):
        self.patched.append((task_id, data))

    def users(self):
        return self._users

    def create_user(self, email, username, first_name=""):
        self.created.append(email)


def _ls_setup(app, fake):
    app.state.db.set_setting("labelstudio_url", "http://ls.test")
    app.state.ls_projects = lambda: {"ok": True, "projects": [{"id": 8, "title": review_routes.DEFAULT_PROJECT, "total": 5, "done": 2}]}
    app.state.ls_client = lambda: fake


def _guide(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    guide = docs / "guides" / "sft-review-guide.md"
    imgs = docs / "images" / "guides" / "sft-review"
    imgs.mkdir(parents=True)
    guide.parent.mkdir(parents=True)
    (imgs / "01-list.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (docs / "secret.png").write_bytes(b"x")
    guide.write_text("# SFT 학습·검수 매뉴얼\n\n## 1. SFT 학습이 무엇이고 왜 검수하나\n\n목록에서 **ZZAIMY SFT 5000 검수** 를 연다.\n\n"
                     "![프로젝트 목록](../images/guides/sft-review/01-list.png)\n\n![밖](../secret.png)\n\n"
                     "<script>alert(1)</script>\n", encoding="utf-8")
    monkeypatch.setattr(review_routes, "GUIDE_PATH", guide)
    monkeypatch.setattr(review_routes, "GUIDE_IMG_DIR", imgs)
    return guide


def _tasks():
    return [{"id": 1, "data": {"assignee": "kim", "track": "a"}, "ann": 1},
            {"id": 2, "data": {"assignee": "kim", "track": "a"}, "ann": 0},
            {"id": 3, "data": {"assignee": "kim", "track": "b"}, "ann": 2},
            {"id": 4, "data": {"assignee": "lee", "track": "b"}, "ann": 0},
            {"id": 5, "data": {"track": "a"}, "ann": 1}]


def test_perm_is_admin_only_and_gates_review_not_dev(tmp_path, monkeypatch):
    app, dev = _dev(tmp_path)
    _guide(tmp_path, monkeypatch)
    _ls_setup(app, FakeLS(_tasks()))
    _add(app, "boss", dept="기획처", grade="head")
    _add(app, "kim", dept="기획처", grade="member")
    _add(app, "lee", dept="기획처", grade="member")
    assert "sft_review" in grades.PERMS and "sft_review" in grades.ADMIN_ONLY_PERMS
    # 총괄 책임자는 구성원 화면에서 줄 수 없고(고를 수 있는 목록에도 없다), 관리자는 준다
    boss = _client(app, "boss")
    assert "sft_review" not in boss.get("/api/org/members", params={"dept": "기획처"}).json()["grantable"]
    _team(boss, "kim", perm=["sft_review"])
    assert "sft_review" not in (app.state.accounts["kim"].get("perms") or [])
    _team(dev, "kim", perm=["sft_review"])
    assert app.state.accounts["kim"]["perms"] == ["sft_review"]
    _team(boss, "kim", perm=["create_project"])                         # 총괄 책임자가 다른 권한을 바꿔도 남는다
    assert set(app.state.accounts["kim"]["perms"]) == {"create_project", "sft_review"}

    kim = _client(app, "kim")
    page = kim.get("/review")
    assert page.status_code == 200
    assert 'href="http://ls.test/projects/8/data"' in page.text and "라벨 스튜디오 열기" in page.text
    assert 'data-mine-done>2</span> / <span data-mine-assigned>3</span>' in page.text   # kim: 맡은 3 · 제출 2
    menu = page.text.split('id="userPop"', 1)[1].split('</form>', 1)[0]
    assert 'href="/review"' in menu and 'data-aura="menu-review"' in menu   # 사용자 메뉴 「문답 검수」(10-27 — 사이드바 본 메뉴에는 없다)
    assert 'href="/review"' not in page.text.split('class="side-nav side-top"', 1)[1].split('</nav>', 1)[0]
    assert kim.get("/review/guide/img/01-list.png").status_code == 200
    assert kim.get("/dev").status_code == 403 and kim.get("/dev/train").status_code == 403
    assert kim.get("/dev/train/review-guide", follow_redirects=False).status_code == 403

    lee = _client(app, "lee")                                              # 권한 없는 교직원
    assert lee.get("/review").status_code == 403
    assert lee.get("/review/guide/img/01-list.png").status_code == 403
    assert 'href="/review"' not in lee.get("/settings").text

    admin = dev.get("/review")
    assert admin.status_code == 200 and 'id="reviewerProgress"' in admin.text and 'data-reviewer="kim"' in admin.text
    assert 'data-reviewer="lee"' not in admin.text                       # 권한 받은 사람만 줄


def test_progress_counts_assigned_and_annotated():
    fake = FakeLS(_tasks())
    got = review_routes.reviewer_progress(fake, 8, ["kim", "lee", "park"])
    assert got["ok"] and got["rows"] == [
        {"uid": "kim", "assigned": 3, "done": 2, "pct": 67},
        {"uid": "lee", "assigned": 1, "done": 0, "pct": 0},
        {"uid": "park", "assigned": 0, "done": 0, "pct": 0}]
    assert len(fake.calls) == 5                                           # 맡은 것이 없으면 제출 수는 묻지 않는다

    class Down(FakeLS):
        def count_tasks(self, *a, **k):
            raise LabelStudioError("x", kind="unreachable")
    bad = review_routes.reviewer_progress(Down(), 8, ["kim"])
    assert not bad["ok"] and bad["error"] == "라벨 스튜디오 응답 없음" and bad["rows"] == []


def test_progress_is_cached(tmp_path, monkeypatch):
    app, dev = _dev(tmp_path)
    _guide(tmp_path, monkeypatch)
    fake = FakeLS(_tasks())
    _ls_setup(app, fake)
    _add(app, "kim", dept="기획처", grade="member")
    app.state.accounts["kim"]["perms"] = ["sft_review"]
    kim = _client(app, "kim")
    assert kim.get("/review").status_code == 200
    n = len(fake.calls)
    assert kim.get("/review").status_code == 200 and len(fake.calls) == n


def test_manual_renders_safely_with_only_its_images(tmp_path, monkeypatch):
    app, dev = _dev(tmp_path)
    guide = _guide(tmp_path, monkeypatch)
    page = dev.get("/review").text
    assert "SFT 학습·검수 매뉴얼" in page and "<b>ZZAIMY SFT 5000 검수</b>" in page
    assert '<img class="guide-img" src="/review/guide/img/01-list.png" alt="프로젝트 목록"' in page
    assert "![밖](../secret.png)" in page and "<script>alert(1)</script>" not in page
    assert 'class="rv-toc"' in page and "SFT 학습이 무엇이고 왜 검수하나" in page
    r = dev.get("/review/guide/img/01-list.png")
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
    for bad in ("..%2Fsecret.png", "secret.png", ".hidden.png", "01-list.txt"):
        assert dev.get(f"/review/guide/img/{bad}").status_code == 404
    assert dev.get("/dev/train/review-guide", follow_redirects=False).headers["location"] == "/review#manual"
    guide.unlink()
    assert "아직 없습니다" in dev.get("/review").text


def test_real_manual_has_required_sections_and_images():
    text = review_routes.GUIDE_PATH.read_text(encoding="utf-8")
    for h in ("SFT 학습이 무엇이고 왜 검수하나", "내 몫 찾기", "하루 분량과 속도", "자주 틀리는 판정", "다 하면"):
        assert f". {h}" in text
    assert "**" not in text                                                # 굵게 서식 쓰지 않음
    for name in ("01-projects.jpg", "02-task-list.jpg", "03-review.jpg", "04-decide.jpg"):
        assert name in text and (review_routes.GUIDE_IMG_DIR / name).is_file()


# ---------------------------------------------------------------- scripts/218

def _script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "218_make_reviewers.py"
    spec = importlib.util.spec_from_file_location("make_reviewers", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_quotas_follow_track_ratio():
    m = _script()
    assert m.quotas({"program_retrieval": 3464, "institutional_policy": 1536}, 500) == {"program_retrieval": 346, "institutional_policy": 154}
    tasks = [{"id": i, "data": {"track": "program_retrieval" if i < 3464 else "institutional_policy",
                                **({"assignee": "old"} if i in (0, 1, 2) else {})}} for i in range(5000)]
    a = m.plan_assign(tasks, ["kim", "lee", "park"], 500)
    b = m.plan_assign(tasks, ["kim", "lee", "park"], 500)
    assert [t["id"] for t, _ in a["assign"]] == [t["id"] for t, _ in b["assign"]]   # 시드 고정 — 같은 표본
    assert a["sample"] == 500 and len(a["assign"]) == 500 - a["already"]
    assert sorted(a["per"].values())[-1] - sorted(a["per"].values())[0] <= 1
    picked = {t["id"] for t, _ in a["assign"]}
    assert not picked & {0, 1, 2}
    big = m.plan_assign(tasks, ["kim"], 1000)                              # 늘리면 앞 표본을 품는다
    assert picked <= {t["id"] for t, _ in big["assign"]}


def test_script_dry_run_changes_nothing_and_apply_writes_secrets(tmp_path, capsys):
    m = _script()
    data = tmp_path / "platform"
    data.mkdir()
    acc = data / "accounts.json"
    acc.write_text(json.dumps({"zzdev": {"role": "dev", "pw_hash": "x", "salt": "00"},
                               "lee": {"role": "staff", "grade": "member", "pw_hash": "y", "salt": "00"}}))
    before = acc.read_text()
    tasks = [{"id": i, "data": {"track": "a" if i % 3 else "b", "input": f"q{i}"}} for i in range(1, 31)]
    fake = FakeLS(tasks, users=[{"email": "lee@aura.local"}])
    out = tmp_path / "secret" / "reviewers.txt"
    args = ["kim:김검수", "lee:이검수", "--out", str(out), "--data", str(data), "--assign", "9"]
    assert m.run(args, client=fake, setup_user=lambda e, p: (True, "PASS")) == 0
    printed = capsys.readouterr().out
    assert acc.read_text() == before and not out.exists() and not fake.patched and not fake.created
    assert "미리 보기" in printed and "kim: 새로 만듦" in printed and "lee: 검수 권한 더함" in printed

    seen = {}
    assert m.run(args + ["--apply"], client=fake, setup_user=lambda e, p: seen.setdefault(e, p) and (True, "PASS")) == 0
    printed = capsys.readouterr().out
    accounts = json.loads(acc.read_text())
    kim = accounts["kim"]
    assert kim["role"] == "staff" and kim["grade"] == "member" and kim["perms"] == ["sft_review"] and kim["must_change"] is True
    assert grades.can_sft_review(kim) and grades.can_sft_review(accounts["lee"])
    secret = out.read_text()
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    kim_line = next(ln for ln in secret.splitlines() if ln.startswith("kim\t"))
    pw = kim_line.split("\t")[2]
    assert passwords.hash_pw(pw, bytes.fromhex(kim["salt"]))[0] == kim["pw_hash"]
    assert pw not in printed and seen["kim@aura.local"] not in printed      # 비밀번호는 화면에 찍지 않는다
    assert kim_line.split("\t")[4] == seen["kim@aura.local"]
    assert fake.created == ["kim@aura.local"]                              # lee 는 이미 있음
    assert "pw_hash" in accounts["lee"] and accounts["lee"]["pw_hash"] == "y"   # 있는 계정은 비밀번호 그대로
    assert len(fake.patched) == 9 and {d["assignee"] for _, d in fake.patched} == {"kim", "lee"}
    assert all(d["input"].startswith("q") and "track" in d for _, d in fake.patched)   # 다른 data 칸은 그대로
