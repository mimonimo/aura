"""여럿이 같이 쓰는 문서 — 다른 사람이 문서 틀을 고쳐 새 판을 저장해도 내가 칸 밖에 쓴 글이 사라지지 않는다(11-10 9명 동시 실측: [동시2]·[동시3] 유실)."""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_accounts import _login
from tests.test_webhwp import _setup, _staff, _up
from tests.test_webhwp_fields import _hwpx, _p
from zzaimy.app import webhwp
from zzaimy.app import webhwp_merge as wm


def _texts(data: bytes) -> list[str]:
    z = zipfile.ZipFile(io.BytesIO(data))
    x = z.read("Contents/section0.xml").decode()
    return [wm.para_text(x[a:b]) for a, b in wm._top_paras(x) if wm.para_text(x[a:b])]


def _doc(paras: list[str]) -> bytes:
    return _hwpx("".join(_p(t, 100 + i) for i, t in enumerate(paras)))


def test_merge_moves_my_new_paragraphs_into_their_new_version():
    base = _doc(["제목", "첫째 문단", "둘째 문단"])
    theirs = _doc(["제목", "첫째 문단", "A가 문서 틀로 넣은 문단", "둘째 문단"])        # 남이 문서 틀로 저장한 새 판
    mine = _doc(["[동시3]", "제목", "첫째 문단", "둘째 문단", "[동시2]"])             # 그 사이 내 화면에서 칸 밖에 쓴 글
    merged, rep = wm.merge_outside(base, mine, theirs)
    assert merged is not None and rep["moved"] == 2 and rep["lost"] == []
    t = _texts(merged)
    assert t[:2] == ["[동시3]", "제목"] and "A가 문서 틀로 넣은 문단" in t and t[-1] == "[동시2]"


def test_merge_edits_untouched_paragraph_but_keeps_clash_as_lost():
    base = _doc(["제목", "첫째 문단", "둘째 문단"])
    theirs = _doc(["제목", "첫째 문단(A가 고침)", "둘째 문단"])
    mine = _doc(["제목", "첫째 문단(내가 고침)", "둘째 문단 끝에 덧붙임"])
    merged, rep = wm.merge_outside(base, mine, theirs)
    t = _texts(merged)
    assert "둘째 문단 끝에 덧붙임" in t and "첫째 문단(A가 고침)" in t      # 남이 안 고친 문단은 내 글로, 둘 다 고친 문단은 남의 글 그대로
    assert rep["lost"] == ["첫째 문단(내가 고침)"]                           # 겹친 내 글은 버리지 않고 돌려준다(사본으로 남길 것)


def test_merge_without_my_outside_edits_changes_nothing():
    base = _doc(["제목", "첫째 문단"])
    theirs = _doc(["제목", "첫째 문단", "A의 새 문단"])
    merged, rep = wm.merge_outside(base, base, theirs)
    assert merged is None and rep["moved"] == 0 and rep["lost"] == []


def _app(tmp_path, monkeypatch, paras):
    app = _setup(tmp_path, monkeypatch)
    _staff(app, "park", "기획처")
    kim, park = TestClient(app), TestClient(app)
    assert _login(kim, "kim", "pass-12345678") and _login(park, "park", "pass-12345678")
    fid = _up(kim, app, "기획처", "같이.hwpx", _doc(paras))
    return app, kim, park, fid


def _file(app, fid) -> bytes:
    return Path(webhwp.get(app.state.db, fid)["path"]).read_bytes()


def test_whole_save_on_stale_base_is_refused_not_overwriting(tmp_path, monkeypatch):
    """내가 연 판 뒤에 남이 새 판을 저장했으면 문서 전체로 덮지 않는다(409 stale) — 덮으면 그 사람이 칸 밖에 쓴 글이 사라진다."""
    app, kim, park, fid = _app(tmp_path, monkeypatch, ["제목", "첫째 문단"])
    base = _file(app, fid)
    sha = hashlib.sha256(base).hexdigest()
    theirs = _doc(["제목", "첫째 문단", "박의 새 문단"])
    assert park.post(f"/hwp/{fid}/save", content=theirs, headers={"X-Base-Sha": sha}).status_code == 200
    r = kim.post(f"/hwp/{fid}/save", content=_doc(["제목", "첫째 문단", "김의 새 문단"]), headers={"X-Base-Sha": sha})
    assert r.status_code == 409 and r.json()["detail"].startswith("stale")
    assert "박의 새 문단" in _texts(_file(app, fid))


def test_rescue_moves_text_and_waits_while_other_holds_structure(tmp_path, monkeypatch):
    app, kim, park, fid = _app(tmp_path, monkeypatch, ["제목", "첫째 문단"])
    base = _file(app, fid)
    # 박이 문서 틀을 쥐고 있는 동안 김의 살리기는 기다리라고 돌려받는다(박의 저장이 덮지 않게)
    assert park.post(f"/hwp/{fid}/lock").json()["ok"]
    mine = _doc(["제목", "첫째 문단", "[김 칸 밖 글]"])
    files = {"base": ("base.hwpx", base), "mine": ("mine.hwpx", mine)}
    w = kim.post(f"/hwp/{fid}/rescue", files=files).json()
    assert w["ok"] is False and w["wait"] and w["by"] == "park"
    # 박이 새 판을 저장하고 놓으면 김의 글이 그 새 판에 옮겨진다 — 박의 글도 그대로
    assert park.post(f"/hwp/{fid}/save", content=_doc(["제목", "박이 넣은 문단", "첫째 문단"])).status_code == 200
    park.post(f"/hwp/{fid}/unlock")
    j = kim.post(f"/hwp/{fid}/rescue", files=files).json()
    assert j["ok"] and j["saved"] and j["moved"] == 1 and j["lost"] == []
    t = _texts(_file(app, fid))
    assert "박이 넣은 문단" in t and "[김 칸 밖 글]" in t


def test_rescue_records_unmovable_text_as_conflict_copy(tmp_path, monkeypatch):
    app, kim, park, fid = _app(tmp_path, monkeypatch, ["제목", "첫째 문단"])
    base = _file(app, fid)
    assert park.post(f"/hwp/{fid}/save", content=_doc(["제목", "첫째 문단(박 고침)"])).status_code == 200
    j = kim.post(f"/hwp/{fid}/rescue", files={"base": ("b", base), "mine": ("m", _doc(["제목", "첫째 문단(김 고침)"]))}).json()
    assert j["ok"] and j["lost"] == ["첫째 문단(김 고침)"]
    rows = kim.get(f"/hwp/{fid}/conflicts").json()
    rows = rows.get("conflicts", rows) if isinstance(rows, dict) else rows
    assert any("김 고침" in (r.get("text") or "") for r in rows)


def test_edit_screen_wires_rescue_before_reload():
    """편집 화면 — 다시 열기 전에 살리고, 남이 문서 틀을 쥐면 칸 안만, 저장은 연 판 표시를 보낸다."""
    src = Path(__file__).resolve().parents[1].joinpath("src/zzaimy/app/templates/hwp_edit.html").read_text(encoding="utf-8")
    assert "flushed.then(rescueOutside)" in src
    assert re.search(r"if \(u && u !== me\) hwp\.setFormMode\(true\)", src)
    assert "hd['X-Base-Sha'] = baseSha" in src
    assert "holdOutside()" in src


def test_edit_screen_rescue_flow_node():
    """편집 화면 살리기 흐름 — node 시험(tests/coedit_rescue.test.cjs)."""
    import shutil
    import subprocess
    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음")
    r = subprocess.run([node, "--test", str(Path(__file__).resolve().parent / "coedit_rescue.test.cjs")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
