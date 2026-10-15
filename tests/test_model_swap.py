"""생성 모델 교체 뼈대 — 모델 정보 기록, 계열별 요청 규약(생각 끄기·구조화 출력 물러남·문맥 한도), 후보 점검, 교체 점검 표."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from zzaimy.generate import model_info

ROOT = Path(__file__).resolve().parents[1]


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(autouse=True)
def _restore_connections():
    """연결 설정 위치·캐시를 시험 뒤 되돌린다 — 다른 시험이 이 시험의 임시 설정을 읽지 않게."""
    from zzaimy.generate import llm_connections

    saved = (llm_connections._path, llm_connections._cache, llm_connections._mtime)
    yield
    llm_connections._path, llm_connections._cache, llm_connections._mtime = saved


# ---------------- 모델 정보 ----------------

VLLM_MODELS = [
    {"id": "writer", "object": "model", "owned_by": "vllm", "root": "/models/Base-27B-Q4", "parent": None, "max_model_len": 16384},
    {"id": "writer-ft1", "object": "model", "owned_by": "vllm", "root": "/adapters/ft1", "parent": "writer"},
]


def test_served_model_keeps_weights_name_without_path():
    info = model_info.from_models("writer", VLLM_MODELS)
    assert info == {"name": "writer", "base": "Base-27B-Q4", "max_model_len": 16384, "engine": "vllm"}
    assert "/models" not in json.dumps(info)
    assert model_info.label(info) == "writer (Base-27B-Q4)"


def test_lora_adapter_records_adapter_and_base():
    info = model_info.from_models("writer-ft1", VLLM_MODELS)
    assert info["adapter"] == "writer-ft1" and info["parent"] == "writer" and info["base"] == "Base-27B-Q4"
    assert info["max_model_len"] == 16384
    assert "어댑터" in model_info.label(info)


def test_ollama_digest_recorded(monkeypatch):
    monkeypatch.setattr(model_info, "_is_ollama", lambda url, timeout=0: True)
    monkeypatch.setattr(model_info, "_ollama_digest", lambda url, model, timeout=0: "abc123def456")
    model_info.reset_cache()
    info = model_info.describe_url("http://dgx:11434", "big:70b")
    assert info == {"name": "big:70b", "engine": "ollama", "digest": "abc123def456"}
    assert model_info.label(info) == "big:70b @abc123def456"
    model_info.reset_cache()


def test_old_records_read_as_unknown():
    assert model_info.of({"rel": "a", "kind": "plan"}) is None
    assert model_info.label(model_info.of({"rel": "a"})) == "모름"
    assert model_info.label({}) == "모름"
    assert not model_info.same(None, {"name": "writer"})


def test_same_model_compares_weights_not_only_name():
    a = model_info.from_models("writer", VLLM_MODELS)
    b = model_info.from_models("writer", [dict(VLLM_MODELS[0], root="/models/Bigger-70B")])
    assert model_info.same(a, dict(a))
    assert not model_info.same(a, b)                    # 같은 서빙 이름에 다른 가중치


def test_describe_reads_server_once_and_drops_url(monkeypatch):
    calls = []

    class _R:
        def __init__(self, body):
            self.body = body

        def read(self, *a):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout=0):
        calls.append(getattr(req, "full_url", req))
        return _R(json.dumps({"data": VLLM_MODELS}).encode())

    monkeypatch.setattr(model_info, "_is_ollama", lambda url, timeout=0: False)
    monkeypatch.setattr(model_info.urllib.request, "urlopen", fake_open)
    model_info.reset_cache()
    c = NS(model="writer", client=NS(base_url="http://10.0.0.9:8000/v1/", api_key="k"))
    a, b = model_info.describe(c), model_info.describe(c)
    assert a == b and len(calls) == 1
    assert "10.0.0.9" not in json.dumps(a)
    model_info.reset_cache()


# ---------------- 계열별 요청 규약 ----------------

class _Rejected(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status_code = status


def _client(monkeypatch, tmp_path, behaviour, conn_opts=None, base="http://vllm.example:8000/v1/"):
    from zzaimy.generate import client as cl
    from zzaimy.generate import llm_connections

    seen = []

    class _Comp:
        def create(self, **kw):
            seen.append(kw)
            return behaviour(kw, len(seen))

    class _Fake:
        base_url = base

        def __init__(self, **kw):
            self.chat = NS(completions=_Comp())

        def with_options(self, **kw):
            return _Fake()

    llm_connections.configure(tmp_path / "llm_connections.json")
    if conn_opts is not None:
        conn = llm_connections.add("시험", "vllm", "http://vllm.example:8000/v1", "m", "")
        llm_connections.activate(conn["id"])
        if conn_opts:
            llm_connections.set_options(conn["id"], **conn_opts)
    monkeypatch.setattr(cl, "OpenAI", _Fake)
    monkeypatch.setattr(cl, "is_ollama", lambda url: False)
    return cl.VllmClient(model="m"), seen


def _ok(text='{"a": 1}'):
    return NS(choices=[NS(message=NS(content=text), finish_reason="stop")], usage=None)


def test_default_requests_unchanged(monkeypatch, tmp_path):
    """운영 기본 — 연결 설정이 없으면 Qwen 식 생각 끄기 인자와 json_schema 를 지금처럼 보낸다."""
    c, seen = _client(monkeypatch, tmp_path, lambda kw, n: _ok())
    assert c._extra == {"chat_template_kwargs": {"enable_thinking": False}}
    assert c.think_extra == {"chat_template_kwargs": {"enable_thinking": True}}
    rf = {"type": "json_schema", "json_schema": {"name": "X", "schema": {"type": "object"}}}
    c.client.chat.completions.create(model="m", messages=[{"role": "user", "content": "질문"}], response_format=rf)
    assert seen[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert seen[0]["response_format"] == rf and seen[0]["messages"][0]["content"] == "질문"


def test_connection_can_set_other_family_thinking_args(monkeypatch, tmp_path):
    c, seen = _client(monkeypatch, tmp_path, lambda kw, n: _ok(),
                      conn_opts={"no_think": {"reasoning": {"enabled": False}}, "think": {"reasoning": {"enabled": True}}})
    c2 = __import__("zzaimy.generate.client", fromlist=["x"]).VllmClient()
    assert c2._extra == {"reasoning": {"enabled": False}} and c2.think_extra == {"reasoning": {"enabled": True}}
    c2.client.chat.completions.create(model="m", messages=[])
    assert seen[-1]["extra_body"] == {"reasoning": {"enabled": False}}
    from zzaimy.app import responder
    monkeypatch.setenv("ZZAIMY_ANSWER_THINK", "1")
    _, gen = responder.answer_gen_options(c2)
    assert gen["extra_body"] == {"reasoning": {"enabled": True}}


def test_connection_can_send_no_thinking_args(monkeypatch, tmp_path):
    _, seen = _client(monkeypatch, tmp_path, lambda kw, n: _ok(), conn_opts={"no_think": {}})
    from zzaimy.generate.client import VllmClient
    c = VllmClient()
    c.client.chat.completions.create(model="m", messages=[])
    assert not seen[-1].get("extra_body") and c.think_extra is None


def test_schema_rejection_falls_back_to_json_instruction(monkeypatch, tmp_path):
    def server(kw, n):
        if kw.get("response_format"):
            raise _Rejected("response_format json_schema is not supported")
        return _ok('```json\n{"a": 1}\n```')

    c, seen = _client(monkeypatch, tmp_path, server)
    rf = {"type": "json_schema", "json_schema": {"name": "X", "schema": {"type": "object", "properties": {"a": {"type": "integer"}}}}}
    from zzaimy.generate.client import _strip_fences
    for _ in range(2):
        r = c.client.chat.completions.create(model="m", messages=[{"role": "user", "content": "질문"}], response_format=rf)
        assert json.loads(_strip_fences(r.choices[0].message.content)) == {"a": 1}
    assert "response_format" in seen[0] and "response_format" not in seen[1]
    assert "JSON 스키마" in seen[1]["messages"][-1]["content"] and '"a"' in seen[1]["messages"][-1]["content"]
    assert len(seen) == 3 and "response_format" not in seen[2]          # 한 번 거절당하면 다음부터는 처음부터 지시로


def test_schema_strict_setting_does_not_fall_back(monkeypatch, tmp_path):
    def server(kw, n):
        raise _Rejected("response_format not supported")

    _client(monkeypatch, tmp_path, server, conn_opts={"structured": "json_schema"})
    from zzaimy.generate.client import VllmClient
    c = VllmClient()
    with pytest.raises(_Rejected):
        c.client.chat.completions.create(model="m", messages=[{"role": "user", "content": "q"}],
                                         response_format={"type": "json_object"})


def test_with_options_copy_goes_through_wrapper(monkeypatch, tmp_path):
    c, seen = _client(monkeypatch, tmp_path, lambda kw, n: _ok())
    c.client.with_options(timeout=5).chat.completions.create(model="m", messages=[])
    assert seen[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_context_follows_server_then_connection_setting(monkeypatch):
    from zzaimy.app import gdocs_agent, llm_budget

    llm_budget.reset_cache()
    no_server = NS(model="m", context_default=0, client=NS(base_url="http://a/v1", models=None))
    assert llm_budget.served_context_len(no_server) is None
    assert gdocs_agent._context_of(no_server) is None                  # 호출부 기본값(CONTEXT_TOKENS)을 쓴다
    set_conn = NS(model="m", context_default=32768, client=NS(base_url="http://b/v1", models=None))
    assert llm_budget.context_len(set_conn) == 32768 and llm_budget.served_context_len(set_conn) == 32768
    served = NS(model="big", context_default=0, client=NS(base_url="http://c/v1", models=NS(
        list=lambda: NS(data=[NS(id="big", max_model_len=65536)]))))
    assert llm_budget.served_context_len(served) == 65536
    llm_budget.reset_cache()


# ---------------- 후보 점검(운영 용도 지정은 그대로) ----------------

def test_trial_env_overrides_generation_roles_only(monkeypatch, tmp_path):
    from zzaimy.generate import llm_connections, model_config

    llm_connections.configure(tmp_path / "llm_connections.json")
    monkeypatch.setenv("ZZAIMY_LLM_FAILOVER", "0")
    prod = llm_connections.add("운영", "vllm", "http://prod:8000/v1", "writer", "")
    cand = llm_connections.add("후보", "vllm", "http://cand:8001/v1", "writer-big", "")
    llm_connections.activate(prod["id"])
    llm_connections.set_roles({"answer": (prod["id"], "writer"), "review": (prod["id"], "writer"),
                               "vision": (prod["id"], "writer")})
    before = (tmp_path / "llm_connections.json").read_text()
    monkeypatch.setenv("ZZAIMY_LLM_TRY_CONN", cand["id"])
    assert model_config.current("answer")["connection_id"] == cand["id"]
    assert model_config.current("review")["model"] == "writer-big"
    assert model_config.current("")["connection_id"] == cand["id"]
    assert model_config.current("vision")["connection_id"] == prod["id"]
    monkeypatch.delenv("ZZAIMY_LLM_TRY_CONN")
    monkeypatch.setenv("ZZAIMY_LLM_TRY_MODEL", "writer-ft1")                # 같은 연결의 어댑터
    got = model_config.current("answer")
    assert got["connection_id"] == prod["id"] and got["model"] == "writer-ft1"
    monkeypatch.delenv("ZZAIMY_LLM_TRY_MODEL")
    assert model_config.current("answer")["model"] == "writer"
    assert (tmp_path / "llm_connections.json").read_text() == before


def test_set_options_validates():
    from zzaimy.generate import llm_connections

    with pytest.raises(ValueError):
        llm_connections.set_options("없음", structured="prompt")


# ---------------- 갈래 판정 재판정 ----------------

def test_kind_redo_by_model_picks_other_and_unknown_models():
    s191 = _load("s191", "191_kind_read_all.py")
    now = {"name": "writer", "base": "Big-70B"}
    last = {"a": {"rel": "a", "kind": "plan"},                              # 예전 줄 — 모름
            "b": {"rel": "b", "kind": "plan", "llm": {"name": "writer", "base": "Base-27B"}},
            "c": {"rel": "c", "kind": "plan", "llm": dict(now)},
            "d": {"rel": "d", "kind": "plan", "llm": {"name": "writer", "base": "Base-27B"}}}
    docs = {"a": {}, "b": {}, "c": {}, "d": {}, "e": {}}
    assert s191.redo_by_model(last, docs, ["d", "e"], now) == ["a", "b", "d", "e"]


def test_kind_sample_summary():
    s190 = _load("s190", "190_kind_read_sample.py")
    rows = [{"group": "규칙 있음", "rule": "plan", "model": "plan", "sec": 1.0},
            {"group": "규칙 있음", "rule": "plan", "model": "report", "sec": 2.0},
            {"group": "규칙 비움", "rule": "", "model": "plan", "sec": 3.0},
            {"group": "규칙 비움", "rule": "", "model": None, "sec": 4.0}]
    s = s190.summarize(rows)
    assert s["ruled"]["same_as_rule"] == 0.5 and s["empty"]["filled"] == 0.5 and s["fail"] == 1


# ---------------- 교체 점검(scripts/195) ----------------

@pytest.fixture
def s195(tmp_path, monkeypatch):
    m = _load("s195", "195_model_swap_check.py")
    monkeypatch.setattr(m, "ROOT", tmp_path)
    return m


def test_plan_commands_quick_and_skip(s195):
    full = [s for s, _, _ in s195.plan_commands("t", "cur", False, set())]
    assert full == ["search_reader", "search_named", "answer", "kind"]
    quick = s195.plan_commands("t", "cur", True, {"kind"})
    assert [s for s, _, _ in quick] == ["search_reader", "answer"]
    ans = dict((s, c) for s, c, _ in quick)["answer"]
    assert ans[ans.index("--answer-sec") + 1] == "2" and "graph_read" in ans


def test_env_file_parsing(s195, tmp_path):
    p = tmp_path / ".env.local"
    p.write_text('# 주석\nexport A_B="x y"\nC=1\n잘못된 줄\nD=\'q\'\n', encoding="utf-8")
    assert s195.load_env_file(p) == {"A_B": "x y", "C": "1", "D": "q"}


def _fake_runner(tmp_path, scale):
    def run(cmd, env):
        script = Path(cmd[1]).name
        tag = cmd[cmd.index("--tag") + 1] if "--tag" in cmd else ""
        ev = tmp_path / "data" / "eval"
        ev.mkdir(parents=True, exist_ok=True)
        if script.startswith("173"):
            (ev / f"rag_probe_{tag}.json").write_text(json.dumps({"summary": {
                "n": 10, "doc_at_k": 0.5 * scale, "twin_at_k": 0.6, "prog_at_k": 0.9, "search_sec_median": 2.0, "search_errors": 0}}))
        elif script.startswith("188"):
            (ev / f"graphrag_vs_fulltext_{tag}.json").write_text(json.dumps({"summary": {"graph_read": {"answer": {
                "n": 3, "errors": 0, "empty": 0, "with_unsupported_numbers": 1, "unsupported_number_ratio": 0.1,
                "fact_recall_mean": 0.4 * scale, "wall_sec_median": 20.0 / scale, "generate_sec_median": 15.0}}}}))
        elif script.startswith("190"):
            assert "--role" in cmd and env.get("ZZAIMY_LLM_TRY_CONN", "") == env.get("_EXPECT_TRY", "")
            return 0, "판정 …\nKIND_READ " + json.dumps({"n": 6, "fail": 0, "ruled": {"same_as_rule": 0.8},
                                                       "empty": {"filled": 0.5}, "sec_median": 3.0})
        return 0, ""
    return run


def test_first_run_saves_baseline_then_compares(s195, tmp_path, monkeypatch):
    snaps = {"": {"answer": {"llm": {"name": "writer", "base": "Base-27B"}, "server": {"conn": "운영"}},
                  "review": {"llm": {"name": "writer", "base": "Base-27B"}, "server": {"conn": "운영"}}},
             "c1": {"answer": {"llm": {"name": "writer-big", "base": "Big-70B"}, "server": {"conn": "후보"}},
                    "review": {"llm": {"name": "writer-big", "base": "Big-70B"}, "server": {"conn": "후보"}}}}
    monkeypatch.setattr(s195, "model_snapshot", lambda extra: snaps[extra.get("ZZAIMY_LLM_TRY_CONN", "")])
    runner = {"scale": 1.0}

    def _runner(cmd, env):
        return _fake_runner(tmp_path, 2.0 if env.get("ZZAIMY_LLM_TRY_CONN") else runner["scale"])(
            cmd, dict(env, _EXPECT_TRY=env.get("ZZAIMY_LLM_TRY_CONN", "")))
    monkeypatch.setattr(s195, "_subprocess_runner", _runner)
    base = tmp_path / "data" / "eval" / "model_baseline.json"
    args = ["--baseline", str(base), "--env-file", str(tmp_path / "없음"), "--tag", "t1"]
    assert s195.main(args) == 0
    saved = json.loads(base.read_text())
    assert saved["models"]["answer"]["llm"]["base"] == "Base-27B"
    assert saved["metrics"]["search_reader"]["doc_at_k"] == 0.5 and saved["metrics"]["kind"]["ruled_same"] == 0.8
    first = base.read_text()
    # 후보와 함께 — 기준은 그대로, 표는 기준·운영·후보를 나란히
    assert s195.main(["--baseline", str(base), "--env-file", str(tmp_path / "없음"), "--tag", "t2", "--candidate-conn", "c1"]) == 0
    assert base.read_text() == first
    out = json.loads((tmp_path / "data" / "eval" / "model_swap_t2.json").read_text())
    assert set(out["runs"]) == {"운영", "후보"} and out["baseline_saved"] is False
    assert out["runs"]["후보"]["metrics"]["answer"]["fact_recall_mean"] == 0.8
    tbl = out["table"]
    head = tbl.splitlines()[0]
    assert "기준(" in head and "운영" in head and "후보" in head
    assert "writer-big (Big-70B)" in tbl and "writer (Base-27B)" in tbl
    recall = next(line for line in tbl.splitlines() if line.startswith("| 정답 사실 재현율"))
    assert recall.endswith("0.8 (나아짐) |")
    wall = next(line for line in tbl.splitlines() if line.startswith("| 답 전체 시간"))
    assert "10 (나아짐)" in wall
    assert "http" not in json.dumps(out)


def test_candidate_only_does_not_save_baseline(s195, tmp_path, monkeypatch):
    monkeypatch.setattr(s195, "model_snapshot", lambda extra: {"answer": {"llm": {"name": "x"}}})
    monkeypatch.setattr(s195, "_subprocess_runner", lambda cmd, env: (0, ""))
    base = tmp_path / "b.json"
    assert s195.main(["--baseline", str(base), "--env-file", str(tmp_path / "x"), "--tag", "t", "--candidate-model", "m",
                      "--no-current", "--skip", "search,answer,kind"]) == 0
    assert not base.exists()


def test_save_baseline_overwrites(s195, tmp_path):
    p = tmp_path / "b.json"
    s195.ensure_baseline(p, {"metrics": {"a": {"x": 1}}})
    b, saved = s195.ensure_baseline(p, {"metrics": {"a": {"x": 2}}})
    assert not saved and b["metrics"]["a"]["x"] == 1
    b, saved = s195.ensure_baseline(p, {"metrics": {"a": {"x": 2}}}, force=True)
    assert saved and json.loads(p.read_text())["metrics"]["a"]["x"] == 2
