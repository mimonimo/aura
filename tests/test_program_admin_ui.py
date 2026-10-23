"""사업 관리 화면 — 연도 고르기(쿠키로 보던 연도, 깜빡임 없는 바꿔 끼우기 자리), 엑셀 연번 열은 기본으로 숨김,
다른 사업과 합치기(미리보기·떼어 내기), 목록에서 빼기(까닭)·뺀 사업 목록·다시 넣기. 엑셀·사람은 모두 가짜 값."""

from __future__ import annotations

import copy
import json

from tests.test_staff_grades import _client, _no_google  # noqa: F401
from tests.test_program_sheet import world  # noqa: F401
from tests.test_program_admin_flow import KEY, OTHER, filled  # noqa: F401
from zzaimy.app import program_admin


def _state(page: str) -> dict:
    raw = page.split("data-pa-state='")[1].split("'")[0]
    return json.loads(raw.replace("&#34;", '"').replace("&quot;", '"'))


def test_year_control_and_cookie_view(filled):
    app, tmp_path, dev, *_ = filled
    page = dev.get("/programs/admin?tab=all&year=2025").text
    assert 'role="radiogroup" aria-label="연도"' in page and 'data-y="2025" aria-checked="true"' in page
    assert "data-pa-main" in page and 'data-pa-part="body"' in page          # 제자리에서 갈아 끼울 자리
    dev.cookies.set("pa_view", "all.2026.won", path="/programs")
    st = _state(dev.get("/programs/admin").text)                         # 주소에 없으면 보던 갈래·연도·단위로(다시 불러오지 않는다)
    assert st == {"tab": "all", "year": 2026, "unit": "won"}
    st = _state(dev.get("/programs/admin?tab=current&year=2025").text)    # 주소가 먼저
    assert st["tab"] == "current" and st["year"] == 2025
    dev.cookies.set("pa_view", "x<y.20250.zz", path="/programs")          # 엉뚱한 값은 버린다
    assert _state(dev.get("/programs/admin").text)["tab"] == "current"


def test_seq_column_hidden_by_default(filled):
    app, tmp_path, dev, *_ = filled
    page = dev.get("/programs/admin?tab=all&year=2025").text
    assert ">순<" not in page and 'class="n g-seq"' in page and 'data-pa-group="seq"' in page
    assert "엑셀 연번" in page


def test_menu_wording(filled):
    app, tmp_path, dev, *_ = filled
    body = dev.get("/programs/admin?tab=all&year=0").text.split("<tbody>")[1].split("</tbody>")[0]
    assert "다른 사업과 합치기" in body and "목록에서 빼기" in body
    assert "같은 사업으로 합치기" not in body and "숨기기" not in body


def test_hide_with_reason_and_restore(filled):
    app, tmp_path, dev, *_ = filled
    r = dev.post("/programs/admin/hide", data={"key": OTHER, "hidden": "1", "reason": "dup"}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    o = program_admin.load_store(tmp_path)["rows"][OTHER]
    assert o["hidden"] and o["hide_reason"] == "dup" and not o.get("not_program")
    page = dev.get("/programs/admin?view=removed").text
    rm = page.split("data-pa-removed ")[1].split("</table>")[0]
    assert "마바사 지원사업" in rm and "중복" in rm and "다시 넣기" in rm
    assert "마바사 지원사업" not in dev.get("/programs/admin?tab=all&year=0").text.split("<tbody>")[1]
    dev.post("/programs/admin/hide", data={"key": OTHER, "hidden": "0"})
    o = program_admin.load_store(tmp_path)["rows"][OTHER]
    assert not o.get("hidden") and "hide_reason" not in o
    dev.post("/programs/admin/hide", data={"key": OTHER, "hidden": "1", "reason": "not_program"})
    assert program_admin.load_store(tmp_path)["rows"][OTHER]["not_program"]
    assert _client(app, "boss").post("/programs/admin/hide", data={"key": OTHER, "hidden": "0"}).status_code == 403


def test_merge_preview_then_unmerge_restores(filled):
    app, tmp_path, dev, *_ = filled
    pid = app.state.db.create_project("grant", "가짜 프로젝트", owner="zzdev", program=OTHER + "|2025")
    before = copy.deepcopy(program_admin.load_store(tmp_path)["rows"])
    d = dev.get("/programs/admin/merge/preview", params={"key": OTHER, "into": KEY}).json()
    assert d["dst"]["key"] == KEY and d["src"]["key"] == OTHER and "마바사 지원사업" in d["aliases"]
    assert {y["how"] for y in d["years"]} <= {"add", "move"} and d["projects"][0]["id"] == pid
    assert program_admin.load_store(tmp_path)["rows"] == before                 # 미리보기는 저장하지 않는다
    assert _client(app, "boss").get("/programs/admin/merge/preview", params={"key": OTHER, "into": KEY}).status_code == 403
    dev.post("/programs/admin/merge", data={"key": OTHER, "into": KEY})
    assert app.state.db.get_project(pid)["program"] == KEY + "|2025"
    page = dev.get("/programs/admin?view=removed").text
    assert "떼어 내기" in page and "가나다 혁신사업" in page.split("data-pa-removed ")[1]
    r = dev.post("/programs/admin/unmerge", data={"key": OTHER}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    after = program_admin.load_store(tmp_path)["rows"]
    for k in (KEY, OTHER):
        for f in ("years", "terms_add", "heads", "leads", "clerks"):
            assert after.get(k, {}).get(f) == before.get(k, {}).get(f), (k, f)
    assert not after[OTHER].get("hidden") and not after[OTHER].get("merged_into")
    assert app.state.db.get_project(pid)["program"] == OTHER + "|2025"
    rows = {r["key"]: r for r in program_admin.merged_rows(tmp_path)}
    assert "마바사 지원사업" not in rows[KEY]["terms"]
    assert _client(app, "boss").post("/programs/admin/unmerge", data={"key": OTHER}).status_code == 403


def test_unmerge_after_target_changed_keeps_target_years(filled):
    app, tmp_path, dev, *_ = filled
    dev.post("/programs/admin/merge", data={"key": OTHER, "into": KEY})
    dev.post("/programs/admin/save", data={"key": KEY, "name": "가나다 혁신사업", "l3": "고친 값"})
    s = program_admin.load_store(tmp_path)
    s["rows"][KEY].setdefault("years", {})["2030"] = {"budget": "1원"}      # 합친 뒤 받은 줄의 연도 값이 바뀜
    program_admin.save_store(s, tmp_path)
    loc = dev.post("/programs/admin/unmerge", data={"key": OTHER}, follow_redirects=False).headers["location"]
    assert "ok=" in loc
    after = program_admin.load_store(tmp_path)["rows"]
    assert "2030" in after[KEY]["years"] and after[OTHER].get("years")         # 받은 줄은 그대로, 떼어 낸 줄은 제 값으로
    assert "마바사 지원사업" not in (after[KEY].get("terms_add") or [])
