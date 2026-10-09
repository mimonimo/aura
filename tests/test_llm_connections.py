"""LLM 연결 설정 규칙."""


def test_external_connection_only_for_public_reading(tmp_path):
    """외부 서버(클로드 등)는 공개 자료 판독에만 — 교내 문서가 지나가는 용도에는 지정되지 않는다."""
    import pytest
    from zzaimy.generate import llm_connections as lc

    lc.configure(tmp_path / "c.json")
    ext = lc.add("클로드", "anthropic", "https://api.anthropic.com/v1/", "claude-sonnet-5", "sk-test")
    for role in ("answer", "review", "vision"):
        with pytest.raises(ValueError):
            lc.set_role(role, ext["id"], "claude-sonnet-5")
    lc.set_role("vision_public", ext["id"], "claude-sonnet-5")
    assert lc.role_is_set("vision_public") and not lc.role_is_set("vision")


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
    lc.add("클로드", "anthropic", "https://api.anthropic.com/v1/", "zzaimy-writer", "sk-test")
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
