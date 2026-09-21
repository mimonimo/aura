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
