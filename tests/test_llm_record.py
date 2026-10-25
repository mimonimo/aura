"""모델 요청 기록(ZZAIMY_LLM_RECORD, K61) — 켜면 보낸 메시지·답·부른 곳을 한 줄씩, 끄면 아무것도 남기지 않는다."""
from __future__ import annotations

import json
from types import SimpleNamespace

from zzaimy.generate import client as gc
from zzaimy.generate import model_config


def _resp(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None), finish_reason="stop")])


def test_record_on(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZAIMY_LLM_RECORD", str(tmp_path))
    gc._record({"model": "m", "messages": [{"role": "user", "content": "안녕"}], "max_tokens": 9}, _resp("반갑습니다"))
    rows = [json.loads(x) for f in tmp_path.glob("llm-*.jsonl") for x in f.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["answer"] == "반갑습니다" and rows[0]["messages"][0]["content"] == "안녕"
    assert rows[0]["caller"].endswith(".test_record_on")


def test_record_off(tmp_path, monkeypatch):
    monkeypatch.delenv("ZZAIMY_LLM_RECORD", raising=False)
    gc._record({"model": "m", "messages": []}, _resp("x"))
    assert not list(tmp_path.iterdir())


def test_record_keeps_only_final_retry_response(tmp_path, monkeypatch):
    calls = []

    def create(*args, **kwargs):
        calls.append(kwargs["max_tokens"])
        if len(calls) == 1:
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="", reasoning="생각 중", tool_calls=None),
                finish_reason="length")], usage=None)
        return _resp("최종 답")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.base_url = kwargs["base_url"]
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
            self.models = SimpleNamespace(list=lambda: SimpleNamespace(data=[]))

    monkeypatch.setenv("ZZAIMY_LLM_RECORD", str(tmp_path))
    monkeypatch.setattr(gc, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(gc, "is_ollama", lambda *_: False)
    monkeypatch.setattr(model_config, "current", lambda role="": {
        "base_url": "http://example.invalid/v1", "api_key": "test", "model": "m", "kind": "vllm",
    })
    monkeypatch.setattr(model_config, "record_usage", lambda *args, **kwargs: None)

    client = gc.VllmClient()
    result = client.client.chat.completions.create(
        model="m", messages=[{"role": "user", "content": "질문"}], max_tokens=64,
    )

    assert result.choices[0].message.content == "최종 답"
    assert len(calls) == 2 and calls[1] > calls[0]
    rows = [json.loads(line) for file in tmp_path.glob("llm-*.jsonl")
            for line in file.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["answer"] == "최종 답"
    assert rows[0]["max_tokens"] == calls[1]
