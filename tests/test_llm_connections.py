"""LLM 연결 설정 규칙."""


def test_external_connection_is_never_assigned_to_a_role(tmp_path):
    """외부 서버는 어떤 용도에도 직접 지정되지 않는다 — 단건·일괄 저장 모두."""
    import pytest
    from zzaimy.generate import llm_connections as lc

    lc.configure(tmp_path / "c.json")
    ext = lc.add("외부 기관", "partner", "https://llm.partner.example/v1", "m-x", "k-1234")
    for role in lc.ROLES:
        with pytest.raises(ValueError):
            lc.set_role(role, ext["id"], "m-x")
        with pytest.raises(ValueError):
            lc.set_roles({role: (ext["id"], "m-x")})
        assert not lc.role_is_set(role)


def test_retired_kind_and_role_in_saved_config_are_ignored(tmp_path, caplog):
    """폐기된 상용 API 연결·공개 자료 판독 용도가 든 옛 설정을 읽어도 죽지 않고, 쓰이지도 보이지도 않는다(ADR-0061)."""
    import json
    import logging
    from zzaimy.generate import llm_connections as lc

    path = tmp_path / "c.json"
    path.write_text(json.dumps({
        "connections": [
            {"id": "in1", "name": "토르", "kind": "vllm", "external": False, "base_url": "http://t:8001/v1", "model": "w"},
            {"id": "old1", "name": "클로드", "kind": "anthropic", "external": True,
             "base_url": "https://api.example.invalid/v1/", "model": "x", "api_key": "sk-test"},
        ],
        "active": "old1", "external": "old1",
        "roles": {"vision_public": {"id": "old1", "model": "x"}, "vision": {"id": "old1", "model": "x"},
                  "answer": {"id": "in1", "model": "w"}},
    }), encoding="utf-8")
    lc.configure(path)
    lc._warned.clear()
    with caplog.at_level(logging.WARNING, logger=lc.__name__):
        names = [c["name"] for c in lc.list_public()]
        lc.list_public()                                          # 두 번 읽어도 기록은 한 번
    assert names == ["토르"]
    assert "anthropic" not in lc.KINDS and "vision_public" not in lc.ROLES
    assert lc.active() is None and lc.external() is None
    assert lc.role_conn("vision") is None                         # 빠진 연결로 가지 않는다
    assert lc.role_conn("answer")["name"] == "토르"
    assert [r["role"] for r in lc.roles_public()] == list(lc.ROLES)
    assert sum("anthropic" in r.getMessage() for r in caplog.records) == 1
    assert sum("vision_public" in r.getMessage() for r in caplog.records) == 1
    lc.set_role("review", "in1", "w")                             # 다음 저장에서 파일에서도 빠진다
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [c["id"] for c in saved["connections"]] == ["in1"] and "vision_public" not in saved["roles"]


def test_role_change_is_seen_without_restart(tmp_path):
    """다른 프로세스가 설정 파일을 바꾸면 이 프로세스도 다음 호출에서 새 지정을 본다."""
    import json, os, time
    from zzaimy.generate import llm_connections as lc

    path = tmp_path / "c.json"
    lc.configure(path)
    a = lc.add("A", "vllm", "http://a:8000/v1", "m-a", "")
    b = lc.add("B", "vllm", "http://b:8000/v1", "m-b", "")
    lc.set_role("review", a["id"], "m-a")
    assert lc.role_conn("review")["name"] == "A"
    data = json.loads(path.read_text()); data["roles"]["review"] = {"id": b["id"], "model": "m-b"}
    path.write_text(json.dumps(data)); os.utime(path, (time.time() + 5, time.time() + 5))   # 다른 프로세스가 고친 것처럼
    assert lc.role_conn("review")["name"] == "B"


def test_update_keeps_fields_that_were_not_given(tmp_path):
    """이름만 바꾸는 update 가 비전 모델을 지우면 판독이 꺼진다 — 안 준 값은 그대로 둔다."""
    from zzaimy.generate import llm_connections as lc

    lc.configure(tmp_path / "c.json")
    c = lc.add("토르", "vllm", "http://t:8001/v1", "zzaimy-writer", "", vision_model="zzaimy-writer")
    lc.update(c["id"], name="토르 03 · Writer")
    got = lc.get(c["id"])
    assert got["name"] == "토르 03 · Writer" and got["vision_model"] == "zzaimy-writer" and got["model"] == "zzaimy-writer"
    lc.update(c["id"], vision_model="clear")
    assert lc.get(c["id"])["vision_model"] == ""


def test_role_fails_over_to_internal_server_with_same_model(tmp_path, monkeypatch):
    """역할의 서버가 연결을 받지 않으면 같은 모델을 내어 주는 다른 교내 서버로 — 외부·다른 모델로는 넘기지 않는다."""
    from zzaimy.generate import llm_connections as lc

    monkeypatch.setenv("ZZAIMY_LLM_FAILOVER", "1")
    lc.configure(tmp_path / "c.json")
    t2 = lc.add("토르 02", "vllm", "http://t2:8001/v1", "", "")
    other = lc.add("다른 모델", "vllm", "http://o:8001/v1", "", "")
    t3 = lc.add("토르 03", "vllm", "http://t3:8001/v1", "", "")
    lc.add("외부 기관", "partner", "https://llm.partner.example/v1", "zzaimy-writer", "k-1234")
    lc.set_role("answer", t2["id"], "zzaimy-writer")
    up = {"http://t2:8001/v1": True, "http://o:8001/v1": True, "http://t3:8001/v1": True}
    served = {"http://o:8001/v1": ("gemma",), "http://t3:8001/v1": ("zzaimy-writer",)}
    monkeypatch.setattr(lc, "_alive", lambda c, timeout=1.0: up.get(c["base_url"], True))
    monkeypatch.setattr(lc, "probe", lambda c, timeout=3.0: {"ok": True, "models": list(served.get(c["base_url"], ()))})
    lc._models_cache.clear()
    assert lc.role_conn("answer")["name"] == "토르 02"                  # 살아 있으면 그대로
    up["http://t2:8001/v1"] = False
    got = lc.role_conn("answer")
    assert got["name"] == "토르 03" and got["model"] == "zzaimy-writer" and got["failover_from"] == "토르 02"
    up["http://t3:8001/v1"] = False
    assert lc.role_conn("answer")["name"] == "토르 02"                  # 넘길 곳이 없으면 원래 연결(오류는 거기서 난다)
    assert t3["id"] and other["id"]
