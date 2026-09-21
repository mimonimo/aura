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
