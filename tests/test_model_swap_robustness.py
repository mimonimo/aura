"""모델 교체 내성 — 서빙 모델을 바꿔도(생각 글·울타리·파이썬 꼴·도구 호출 꼴·출력 잘림·임베딩 차원) 같은 길로 동작한다."""
import json
import logging
from types import SimpleNamespace

import numpy as np

from zzaimy.app import doc_agent, embed_search
from zzaimy.generate import model_json


def test_model_json_reads_variants_of_other_models():
    want = {"action": "search", "args": {"query": "정년"}}
    body = json.dumps(want, ensure_ascii=False)
    for raw in (body,
                f"<think>먼저 {{생각}}을 한다</think>\n{body}",                # 닫힌 생각 글(안에 괄호가 있어도)
                f"생각만 하고 여는 표시는 떼어 냄 {{x}}</think>{body}",         # 서버가 여는 표시를 뗀 꼴
                f"```json\n{body}\n```",                                     # 울타리
                f"다음 걸음입니다.\n{body}\n이상입니다.",                       # 앞뒤 설명 글
                body + "}}",                                                 # 닫는 괄호를 더 붙인 꼴
                "{'action': 'search', 'args': {'query': '정년'}}",            # 파이썬 꼴(작은따옴표)
                '{"action": "search", "args": {"query": "정년", "all": True}}'):   # 파이썬 True 섞임
        got = model_json.loads(raw)
        assert got and got["action"] == "search" and got["args"]["query"] == "정년", raw
    assert model_json.loads("<think>잘린 생각 {\"action\": \"x\"") is None          # 닫히지 않은 생각 안의 괄호는 답이 아니다
    assert model_json.loads("JSON 없음") is None
    assert model_json.loads('{"a": {"action": "inner"}}', key="action") == {"action": "inner"}


def test_step_parser_normalizes_tool_call_shapes():
    for raw in ('{"tool": "search", "arguments": {"query": "q"}}',
                '{"name": "search", "parameters": {"query": "q"}}',
                '{"function": {"name": "search", "arguments": "{\\"query\\": \\"q\\"}"}}',
                '{"action": "search", "args": "{\\"query\\": \\"q\\"}"}'):
        step = doc_agent._parse(raw)
        assert step and step["action"] == "search" and step["args"] == {"query": "q"}, raw
    assert doc_agent._parse('{"thought": "도구 이름 없음"}') is None


def _resp(content="", *, reasoning="", tool_calls=None, finish="stop"):
    msg = SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)])


def _client(replies, seen):
    it = iter(replies)

    class C:
        def create(self, **kw):
            seen.append(kw.get("max_tokens"))
            return next(it)
    return SimpleNamespace(model="m", client=SimpleNamespace(chat=SimpleNamespace(completions=C())))


def _tools():
    return [doc_agent.Tool("search", "찾기", lambda a: "결과 " + a.get("query", "")),
            doc_agent.Tool("finish", "끝", lambda a: a.get("message", ""), final=True)]


def test_steps_in_tool_calls_or_reasoning_channel_are_not_lost():
    fn = SimpleNamespace(name="search", arguments='{"query": "정년"}')
    replies = [_resp("", tool_calls=[SimpleNamespace(function=fn)]),                       # 도구 호출 꼴로 온 걸음
               _resp("", reasoning='생각 끝에 {"action": "finish", "args": {"message": "끝"}}')]   # 생각 칸으로 샌 답
    ep = doc_agent.run(_client(replies, []), "정년 찾아 줘", lambda: "s", _tools())
    assert ep.steps[0]["action"] == "search" and ep.steps[0]["observation"] == "결과 정년"
    assert ep.finished and ep.final_message == "끝"


def test_truncated_step_retries_with_bigger_output_budget():
    seen: list = []
    step = json.dumps({"action": "finish", "args": {"message": "끝"}})
    replies = [_resp("<think>길게 생각하다 잘림", finish="length"), _resp(step)]
    ep = doc_agent.run(_client(replies, seen), "q", lambda: "s", _tools())
    assert ep.finished and len(seen) == 2 and seen[1] > seen[0]          # 잘리면 다음 청의 출력 한도를 늘린다


def test_embedding_dimension_change_falls_back_to_lexical(caplog):
    vectors = np.ones((5, 8), dtype=np.float32)
    assert embed_search.dims_ok(np.ones(8, dtype=np.float32), vectors, "시험-같음")
    embed_search._DIM_WARNED.discard("시험-다름")
    with caplog.at_level(logging.WARNING):
        assert not embed_search.dims_ok(np.ones(12, dtype=np.float32), vectors, "시험-다름")
        assert not embed_search.dims_ok(np.ones(12, dtype=np.float32), vectors, "시험-다름")
    assert sum("차원 불일치" in r.getMessage() for r in caplog.records) == 1   # 경고는 한 번만

    idx = embed_search.EmbedIndex()
    idx._load = lambda: True
    idx._ids, idx._vectors = np.arange(5), vectors
    idx._encode = lambda texts: np.ones((1, 12), dtype=np.float32)       # 새 임베딩 모델(차원 다름)
    assert idx.search("질문") == []                                        # 깨지지 않고 의미 검색만 빠진다
