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


def _bomb(n_mb: int) -> bytes:
    """section0.xml 을 풀면 n_mb MB 가 되는 작은 hwpx — 압축 폭탄."""
    buf = io.BytesIO()
    src = zipfile.ZipFile(io.BytesIO(_doc(["제목"])))
    with zipfile.ZipFile(buf, "w") as z:
        for info in src.infolist():
            if info.filename == "Contents/section0.xml":
                z.writestr(info.filename, b"<hs:sec>" + b" " * (n_mb * 1024 * 1024) + b"</hs:sec>", compress_type=zipfile.ZIP_DEFLATED)
            else:
                z.writestr(info, src.read(info.filename))
    return buf.getvalue()


def _many_entries(n: int) -> bytes:
    buf = io.BytesIO()
    src = zipfile.ZipFile(io.BytesIO(_doc(["제목"])))
    with zipfile.ZipFile(buf, "w") as z:
        for info in src.infolist():
            z.writestr(info, src.read(info.filename))
        for i in range(n):
            z.writestr(f"BinData/x{i}.bin", b"")
    return buf.getvalue()


def test_merge_refuses_zip_bomb_and_too_many_entries():
    base = _doc(["제목", "첫째 문단"])
    bomb = _bomb(20)
    assert len(bomb) < 200 * 1024                                 # 작게 보내도
    merged, rep = wm.merge_outside(base, bomb, base)
    assert merged is None and rep["too_big"] and rep["error"]    # 풀지 않고 거절
    merged, rep = wm.merge_outside(base, _many_entries(wm.MERGE_MAX_ENTRIES + 1), base)
    assert merged is None and rep["too_big"] and "항목" in rep["error"]
    merged, rep = wm.merge_outside(_bomb(20), _doc(["제목", "첫째 문단", "새 글"]), base)   # base 도 같은 한도
    assert merged is None and rep["too_big"]


def test_merge_streaming_read_stops_when_declared_size_lies(monkeypatch):
    """선언 크기를 믿지 않는다 — 실제로 풀린 양이 한도를 넘으면 그 자리에서 멈춘다."""
    big = _bomb(1)                                                 # 풀면 1MB
    monkeypatch.setattr(wm, "_open", lambda data: zipfile.ZipFile(io.BytesIO(data)))   # 선언 검사가 속은 것처럼(거짓 file_size)
    monkeypatch.setattr(wm, "MERGE_MAX_UNZIPPED", 512 * 1024)
    merged, rep = wm.merge_outside(_doc(["제목"]), big, _doc(["제목"]))
    assert merged is None and rep["too_big"]


def test_rescue_with_bomb_keeps_copy_not_silent(tmp_path, monkeypatch):
    """한도를 넘는 판은 합치지 않고 keep 으로 돌려준다(화면이 내 판 전체를 사본으로) — 파일은 그대로, 기록도 남는다."""
    app, kim, park, fid = _app(tmp_path, monkeypatch, ["제목", "첫째 문단"])
    base = _file(app, fid)
    j = kim.post(f"/hwp/{fid}/rescue", files={"base": ("b", base), "mine": ("m", _bomb(20))}).json()
    assert j["ok"] is False and j["keep"] and j["error"]
    assert _file(app, fid) == base
    rows = kim.get(f"/hwp/{fid}/conflicts").json()
    rows = rows.get("conflicts", rows) if isinstance(rows, dict) else rows
    assert any("옮기지 못함" in (r.get("text") or "") for r in rows)


def test_quiet_save_on_stale_base_is_refused_and_save_returns_new_sha(tmp_path, monkeypatch):
    """서식만 남기는 조용한 저장도 바탕 판이 바뀌었으면 덮지 않는다(11-10 9명 시험: 옛 판 전체가 남의 새 판을 덮음).
    저장에 성공하면 새 판 지문을 돌려줘 같은 화면의 다음 저장이 제 판을 남의 판으로 알지 않게 한다."""
    app, kim, park, fid = _app(tmp_path, monkeypatch, ["제목", "첫째 문단"])
    sha = hashlib.sha256(_file(app, fid)).hexdigest()
    r = park.post(f"/hwp/{fid}/save", content=_doc(["제목", "첫째 문단", "박의 새 문단"]), headers={"X-Base-Sha": sha})
    assert r.status_code == 200 and r.json()["sha"] == hashlib.sha256(_file(app, fid)).hexdigest()
    r = kim.post(f"/hwp/{fid}/save?quiet=1", content=_doc(["제목", "첫째 문단"]), headers={"X-Base-Sha": sha})
    assert r.status_code == 409 and "박의 새 문단" in _texts(_file(app, fid))
    new_sha = park.post(f"/hwp/{fid}/save?quiet=1", content=_doc(["제목", "첫째 문단", "박의 새 문단", "더"]),
                        headers={"X-Base-Sha": hashlib.sha256(_file(app, fid)).hexdigest()}).json()["sha"]
    assert park.post(f"/hwp/{fid}/save", content=_doc(["제목", "첫째 문단", "박의 새 문단", "더", "또"]),
                     headers={"X-Base-Sha": new_sha}).status_code == 200


def test_save_that_wipes_almost_all_text_is_refused(tmp_path, monkeypatch):
    """본문이 넉넉한 문서를 거의 빈 판으로 덮는 저장은 거절한다 — 다시 불러오는 도중의 빈 편집기 내용(11-10, 2만8천 자 → 0자)."""
    long = [f"{i}번째 문단 — 사업 운영 결과와 성과를 적은 본문입니다." for i in range(30)]
    app, kim, park, fid = _app(tmp_path, monkeypatch, long)
    for q in ("", "?quiet=1"):
        r = kim.post(f"/hwp/{fid}/save{q}", content=_doc([""]))
        assert r.status_code == 409 and r.json()["detail"].startswith("wiped")
    assert len(_texts(_file(app, fid))) == 30
    assert kim.post(f"/hwp/{fid}/save?quiet=1", content=_doc(["짧게"])).status_code == 409   # 조용한 저장은 크게 줄이지 못한다
    assert kim.post(f"/hwp/{fid}/save", content=_doc(long[:20])).status_code == 200   # 일부를 지우는 보통 저장은 그대로
    assert kim.post(f"/hwp/{fid}/save", content=_doc(["새로 쓴 한 줄"])).status_code == 200   # 사람이 누른 저장은 글이 있으면 그대로


def test_edit_screen_does_not_save_while_reloading():
    """다시 여는 동안은 조용한 저장을 하지 않고, 조용한 저장도 바탕 판 지문을 보낸다."""
    html = (Path(__file__).resolve().parents[1] / "src/zzaimy/app/templates/hwp_edit.html").read_text(encoding="utf-8")
    q = html[html.index("function quietSave()"):]
    q = q[:q.index("function touched()")]
    assert "reloading" in q and "reloadPending" in q and "X-Base-Sha" in q
    assert "reloading = true" in html and "function rebase(" in html


def test_wipe_check_does_not_unpack_zip_bombs():
    """빈 판 거절의 글자 세기는 합치기와 같은 풀기 한도를 쓴다 — 압축 폭탄은 세지 않고(-1) 넘긴다."""
    from zzaimy.app.webhwp_routes import _plain_len
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", "<hp:t>" + "가" * (60 * 1024 * 1024) + "</hp:t>")
    assert _plain_len(buf.getvalue()) == -1
    assert _plain_len(_doc(["가나다"])) == 3


# ── 칸 든 문단의 칸 밖 글(11-13 운영 동시 편집: SetPos+MoveParaEnd 로 절 칸 문단·표 문단 끝에 친 첫 문장 셋이 소리 없이 사라짐) ──
def _fb(bid: int, name: str) -> str:
    return (f'<hp:ctrl><hp:fieldBegin id="{bid}" type="CLICK_HERE" name="{name}" editable="1" dirty="0" zorder="-1" fieldid="{bid + 1}">'
            '<hp:parameters cnt="1" name=""><hp:integerParam name="Prop">9</hp:integerParam></hp:parameters></hp:fieldBegin></hp:ctrl>')


def _fe(bid: int) -> str:
    return f'<hp:ctrl><hp:fieldEnd beginIDRef="{bid}" fieldid="{bid + 1}"/></hp:ctrl>'


def _run_p(pid: int, inner: str) -> str:
    return f'<hp:p id="{pid}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0">{inner}</hp:run></hp:p>'


def _cell_doc(cells: list[str], after: str = "") -> str:
    tcs = "".join(f'<hp:tc><hp:subList>{_run_p(900 + i, _fb(500 + 10 * i, f"문단:{40 + i}") + f"<hp:t>{c}</hp:t>" + _fe(500 + 10 * i))}</hp:subList></hp:tc>'
                  for i, c in enumerate(cells))
    tail = f"<hp:t>{after}</hp:t>" if after else ""
    return _run_p(30, f'<hp:tbl id="77" rowCnt="1" colCnt="{len(cells)}"><hp:tr>{tcs}</hp:tr></hp:tbl>{tail}')


def _sec4(lines: list[str]) -> str:
    """여러 문단에 걸친 절 칸 — 첫 문단에 여는 표시, 끝 문단에 닫는 표시, 가운데 문단은 표시 없음."""
    if len(lines) == 1:
        return _run_p(40, _fb(400, "절:4. 운영") + f"<hp:t>{lines[0]}</hp:t>" + _fe(400))
    out = [_run_p(40, _fb(400, "절:4. 운영") + f"<hp:t>{lines[0]}</hp:t>")]
    out += [_run_p(41 + i, f"<hp:t>{t}</hp:t>") for i, t in enumerate(lines[1:-1])]
    out.append(_run_p(49, f"<hp:t>{lines[-1]}</hp:t>" + _fe(400)))
    return "".join(out)


def _coedit_doc(*, sec3_after: str = "", cells=("구분", "인원"), table_after: str = "", sec4=("",), sec9_after: str = "", extra: str = "") -> bytes:
    sec3 = _run_p(20, _fb(300, "절:3. 참여 현황") + "<hp:t/>" + _fe(300) + (f"<hp:t>{sec3_after}</hp:t>" if sec3_after else ""))
    sec9 = _run_p(60, _fb(600, "절:9. 개선") + "<hp:t>본문</hp:t>" + _fe(600) + (f"<hp:t>{sec9_after}</hp:t>" if sec9_after else ""))
    return _hwpx(_p("3. 참여 현황", 10) + sec3 + _cell_doc(list(cells), table_after) + _p("4. 운영 내용", 35) + _sec4(list(sec4))
                 + _p("9. 개선 사항", 55) + sec9 + extra)


def test_text_typed_outside_field_in_field_paragraph_survives_stale_rescue():
    """칸 끝 뒤·표 뒤에 친 글은 칸 밖 글이다 — 그 사이 AURA 가 다른 절을 여러 문단으로 써 새 판을 저장해도(stale) 옮겨져야 하고,
    내 화면에 흘러 들어온 AURA 의 절 글(여러 문단 칸의 가운데 문단)은 내 글로 잘못 알아 「옮기지 못함」 에 넣지 않는다."""
    aura = ("1) 수료 인원", "가) 수료 인원: 84명", "2) 만족도", "가) 만족도: 4.6점")
    base = _coedit_doc()
    theirs = _coedit_doc(sec4=aura, cells=("구분", "84명"))                              # AURA 저장 + 칸 하나는 칸 길로 바뀜
    mine = _coedit_doc(sec3_after=" 참석 대상은 행정직·조교 전원이다.", table_after=" 표 아래 덧붙임.", sec4=aura[:3] + ("가) 만족",),
                       sec9_after=" 다음 연수는 실습 비중을 늘린다.", extra=_p("불참자는 9월 보충 연수로 이수한다.", 70))
    merged, rep = wm.merge_outside(base, mine, theirs)
    assert merged is not None, rep
    t = _texts(merged)
    assert rep["lost"] == [], rep["lost"]
    assert any(x.endswith("참석 대상은 행정직·조교 전원이다.") for x in t)
    assert any(x.endswith("표 아래 덧붙임.") and "84명" in x for x in t)               # 표 칸 글은 지금 판(theirs) 것
    assert any(x == "본문 다음 연수는 실습 비중을 늘린다." for x in t)
    assert t[-1] == "불참자는 9월 보충 연수로 이수한다."
    assert [x for x in t if x.startswith(("1)", "가)", "2)"))] == list(aura)              # AURA 가 쓴 절 글은 theirs 그대로


def test_outside_text_in_field_paragraph_clashing_with_theirs_is_kept_as_lost():
    """같은 칸 문단의 칸 밖 글을 theirs 도 고쳤으면 덮지 않고 내 글을 「옮기지 못함」 으로 돌려준다 — 소리 없이 버리지 않는다."""
    base = _coedit_doc()
    theirs = _coedit_doc(sec3_after=" 남이 먼저 덧붙임.")
    mine = _coedit_doc(sec3_after=" 내가 덧붙임.")
    merged, rep = wm.merge_outside(base, mine, theirs)
    assert rep["lost"] == ["내가 덧붙임."]
    assert merged is None or any(x.endswith("남이 먼저 덧붙임.") for x in _texts(merged))


def _sec_edit(data: bytes, fn) -> bytes:
    zin, buf = zipfile.ZipFile(io.BytesIO(data)), io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            body = zin.read(info.filename)
            if info.filename == "Contents/section0.xml":
                body = fn(body.decode()).encode()
            z.writestr(info, body)
    return buf.getvalue()


def test_rescue_route_keeps_text_typed_after_field_end_when_aura_saved_meanwhile(tmp_path, monkeypatch):
    """운영 재현 꼴(11-13) — 김이 절 칸 문단 끝(칸 끝 뒤)에 글을 치는 동안 AURA 가 새 판을 저장했다. 김의 문서 저장은 stale 로
    거절되고 살리기(/rescue)로 가는데, 그 글이 지금 파일에 들어가고 충돌 사본도 남지 않아야 한다(전에는 둘 다 없이 사라졌다)."""
    app = _setup(tmp_path, monkeypatch)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    fid = _up(kim, app, "기획처", "같이.hwpx", _coedit_doc())
    base = _file(app, fid)
    theirs = _sec_edit(base, lambda x: x.replace("<hp:t>본문</hp:t>", "<hp:t>본문(AURA 가 씀)</hp:t>"))
    webhwp.save(app.state.db, Path(app.state.data_dir), fid, theirs, user="AURA")

    def type_after_sec3(x: str) -> str:
        k = x.index('name="절:3. 참여 현황"')
        e = x.index("</hp:ctrl>", x.index("<hp:fieldEnd", k)) + len("</hp:ctrl>")
        return x[:e] + "<hp:t> 참석 대상은 행정직·조교 전원이다.</hp:t>" + x[e:]
    mine = _sec_edit(base, type_after_sec3)
    j = kim.post(f"/hwp/{fid}/rescue", files={"base": ("b", base), "mine": ("m", mine)}).json()
    assert j["ok"] and j["saved"] and j["moved"] == 1 and j["lost"] == [], j
    t = _texts(_file(app, fid))
    assert any(x.endswith("참석 대상은 행정직·조교 전원이다.") for x in t) and "본문(AURA 가 씀)" in t
