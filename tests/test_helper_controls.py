"""업무 도우미 — 안내 자료에 없는 칸·단추도 지금 화면 목록(번호)으로 다루고, 이름으로 준 요소를 알아듣는다(10-27)."""
import pytest

from zzaimy.app import platform_ops


def _ctx(tmp_path, said="", controls=None):
    from zzaimy.app.db import Database

    db = Database(tmp_path / "t.db")
    sid = db.create_chat_session("도우미", owner="zzaimy")
    ctx = platform_ops.Ctx(db=db, data_dir=tmp_path, user="zzaimy", role="staff", said=said, page="/settings",
                           controls=platform_ops.clean_controls(controls or []))
    return ctx, sid


def test_name_resolves_to_guide_element(tmp_path):
    ctx, sid = _ctx(tmp_path, said="성명에 홍길동 입력해줘")
    item = platform_ops.propose_open(ctx, sid, "/settings", "성명 칸에 홍길동", now=True, steps=[{"element": "성명 입력 칸", "text": "홍길동"}])
    st = item["act"]["steps"]
    assert st and st[0]["sel"] == '[data-aura="settings-name"]' and st[0]["ok"] and st[0]["text"] == "홍길동"


def test_screen_controls_fill_and_button_needs_command(tmp_path):
    controls = [{"id": "c1", "kind": "field", "label": "메모", "value": ""}, {"id": "c2", "kind": "button", "label": "보관"},
                {"id": "zz", "kind": "button", "label": "나쁜 번호"}, {"id": "c3", "kind": "weird", "label": "x"}]
    ctx, sid = _ctx(tmp_path, said="메모에 회의 내용 넣어줘", controls=controls)
    assert [c["id"] for c in ctx.controls] == ["c1", "c2"]                      # 번호·갈래가 맞는 것만
    item = platform_ops.propose_open(ctx, sid, "/settings", "메모 채우기", now=True,
                                     steps=[{"element": "c1", "text": "회의 내용"}, {"element": "c2"}])
    a, b = item["act"]["steps"]
    assert a["sel"] == '[data-aura-c="c1"]' and a["ok"] and not a["final"]
    assert b["final"] and not b["ok"]                                            # 「보관」 하라고 시키지 않았다 — 가리키기만
    ctx2, sid2 = _ctx(tmp_path / "b", said="메모에 회의 내용 넣고 보관해 줘", controls=controls)
    item2 = platform_ops.propose_open(ctx2, sid2, "/settings", "메모 채우고 보관", now=True,
                                      steps=[{"element": "메모", "text": "회의 내용"}, {"element": "보관 단추"}])
    assert [x["ok"] for x in item2["act"]["steps"]] == [True, True]


def test_same_screen_unknown_element_lists_choices(tmp_path):
    ctx, sid = _ctx(tmp_path, said="여기 뭐 넣어줘", controls=[{"id": "c1", "kind": "field", "label": "메모"}])
    with pytest.raises(platform_ops.OpRefused) as e:
        platform_ops.propose_open(ctx, sid, "/settings", "x", now=True, steps=[{"element": "없는 칸", "text": "a"}])
    assert "c1 [칸] 메모" in str(e.value)


def test_controls_only_count_on_current_screen(tmp_path):
    ctx, sid = _ctx(tmp_path, said="문서함 열어줘", controls=[{"id": "c1", "kind": "button", "label": "새 문서"}])
    item = platform_ops.propose_open(ctx, sid, "/hwp", "문서함", now=True, steps=[{"element": "c1"}])
    assert all("data-aura-c" not in x["sel"] for x in item["act"]["steps"])     # 다른 화면에서는 이 화면 번호를 쓰지 않는다
