"""모델 요청 기록(ZZAIMY_LLM_RECORD, K61) — 켜면 보낸 메시지·답·부른 곳을 한 줄씩, 끄면 아무것도 남기지 않는다."""
from __future__ import annotations

import json
from types import SimpleNamespace

from zzaimy.generate import client as gc


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
