"""11-10 운영 동시 4건 실측(대화 173~176)에서 나온 품질·속도 문제의 일반 메커니즘 시험.

- 프로젝트 밖(문서함) 근거가 섞이면 대상(행사·연도) 확인 지시가 붙는다
- 날짜 범위·요일 붙은 월.일에는 「(근거 확인 필요)」를 끼우지 않는다
- 꼼꼼하게(기본)라도 단순 사실 질문은 답 쓰기 생각을 끈다
- 같은 모델을 내는 내부 서버 둘에 호출을 나눈다(외부·키 다른 서버로는 넘기지 않음)"""
from __future__ import annotations

import pytest

from zzaimy.app import effort
from zzaimy.app.responder import answer_think, needs_reasoning, target_notes
from zzaimy.generate import llm_pool
from zzaimy.verify.numbers import FLAG, flag_unsupported


# ── 대상 확인 ───────────────────────────────────────────────────────────

def test_target_notes_none_without_outside():
    assert target_notes("2018 LINC+ 결과보고", []) == []


def test_target_notes_flags_outside_and_year_mismatch():
    notes = target_notes("2018 LINC+ 프로그램 결과보고", ["2023 종합설계 페스티벌 결과", "2018 LINC+ 캠프"])
    assert len(notes) == 1
    n = notes[0]
    assert "2023 종합설계 페스티벌 결과" in n and "이 프로젝트 자료에는 없습니다" in n
    assert "연도가 이 프로젝트와 다릅니다" in n
    # 같은 연도 자료는 연도 다름 목록에 들지 않는다
    tail = n.split("연도가 이 프로젝트와 다릅니다")[0].split("특히")[-1]
    assert "2018 LINC+ 캠프" not in tail


def test_target_notes_same_year_no_year_clause():
    n = target_notes("2018 LINC+ 결과보고", ["2018 다른 행사"])[0]
    assert "연도가 이 프로젝트와 다릅니다" not in n


# ── 날짜 범위에 표시 끼우지 않기 ────────────────────────────────────────

@pytest.mark.parametrize("text", ["캠프는 11.30.~12.1. 에 열렸다.", "행사는 6.25(월)~6.26(화) 이틀이다.", "기간 3.2~3.15 동안"])
def test_dates_not_flagged(text):
    out, flagged = flag_unsupported(text, ["관련 없는 근거"])
    assert FLAG not in out, out


def test_counts_still_flagged():
    out, flagged = flag_unsupported("참가 인원은 273명이다.", ["관련 없는 근거"])
    assert FLAG in out


# ── 답 쓰기 생각 켜기 ──────────────────────────────────────────────────

SIMPLE = ["페스티벌 참가 인원 구성은?", "두드림 캠프 장소는 어디야?", "특강 강사는 누구였어?"]
REASON = ["세 특강의 CQI 점수를 비교해 줘", "명예퇴직 신청 자격이 되는지 따져 줘", "2018년과 2019년 참가자 수 차이는?"]


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv("ZZAIMY_ANSWER_THINK", raising=False)
    monkeypatch.delenv("ZZAIMY_EFFORT_DEEP_THINK", raising=False)


@pytest.mark.parametrize("q", SIMPLE)
def test_simple_question_no_think_on_deep(no_env, q):
    assert not needs_reasoning(q)
    with effort.use(effort.DEEP):
        assert answer_think(q) is False


@pytest.mark.parametrize("q", REASON)
def test_reasoning_question_thinks_on_deep(no_env, q):
    with effort.use(effort.DEEP):
        assert answer_think(q) is True


def test_fast_never_thinks(no_env):
    with effort.use(effort.FAST):
        assert answer_think(REASON[0]) is False


def test_env_forces_think(monkeypatch):
    monkeypatch.setenv("ZZAIMY_ANSWER_THINK", "1")
    with effort.use(effort.DEEP):
        assert answer_think(SIMPLE[0]) is True


def test_no_question_keeps_old_rule(no_env):
    with effort.use(effort.DEEP):
        assert answer_think() is True


# ── 쌍둥이 서버 나누기 ─────────────────────────────────────────────────

A = {"id": "a", "kind": "vllm", "external": False, "base_url": "http://a:8001/v1", "model": "w", "vision_model": "w"}
B = {"id": "b", "kind": "vllm", "external": False, "base_url": "http://b:8001/v1/", "model": "", "vision_model": "w"}
EXT = {"id": "x", "kind": "vllm", "external": True, "base_url": "http://x/v1", "model": "w"}
OTHER = {"id": "o", "kind": "vllm", "external": False, "base_url": "http://o/v1", "model": "other"}
KEYED = {"id": "k", "kind": "vllm", "external": False, "base_url": "http://k/v1", "model": "w", "key": "s"}


def _key(c):
    return c.get("key", "")


def test_twins_same_model_only():
    cfg = {"base_url": "http://a:8001/v1", "model": "w", "api_key": "", "kind": "vllm", "external": False}
    urls = llm_pool.twins(cfg, [A, B, EXT, OTHER, KEYED], _key)
    assert urls == ["http://a:8001/v1", "http://b:8001/v1"]


def test_external_cfg_never_pooled():
    cfg = {"base_url": "http://x/v1", "model": "w", "api_key": "", "kind": "vllm", "external": True}
    assert llm_pool.twins(cfg, [A, B], _key) == ["http://x/v1"]


def test_pick_least_inflight_and_round_robin():
    urls = ["http://p1", "http://p2"]
    with llm_pool.track("http://p1"):
        assert llm_pool.pick(urls) == "http://p2"
        with llm_pool.track("http://p2"), llm_pool.track("http://p2"):
            assert llm_pool.pick(urls) == "http://p1"
    picks = {llm_pool.pick(urls) for _ in range(4)}
    assert picks == set(urls)
    assert llm_pool.inflight().get("http://p1", 0) == 0


def test_pool_disabled_env(monkeypatch):
    monkeypatch.setenv("ZZAIMY_LLM_POOL", "0")
    assert not llm_pool.enabled()


def test_answer_timing_report(tmp_path, capsys):
    import importlib.util
    import json
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("atr", Path(__file__).resolve().parents[1] / "scripts" / "answer_timing_report.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    p = tmp_path / "ep.jsonl"
    recs = [{"at": "2026-11-10T08:30:00", "session": 176, "steps": [{}, {}], "request": "q",
             "answer_timing": {"think": True, "completion_tokens": 8170, "generate_sec": 370.1, "total_sec": 380, "server": "a:8001"}},
            {"at": "2026-11-10T09:30:00", "session": 177, "steps": [{}], "request": "q2",
             "answer_timing": {"think": False, "completion_tokens": 300, "generate_sec": 15.0, "total_sec": 20, "server": "b:8001"}},
            {"at": "2026-11-09T09:30:00", "session": 1, "steps": [], "answer_timing": {}}]
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\nnot json\n", encoding="utf-8")
    rs = list(m.rows(str(p), since="2026-11-10"))
    assert [r["session"] for r in rs] == [176, 177]
    assert rs[0]["tok_s"] == 22.1 and rs[0]["steps"] == 2
    assert m.main([str(p), "--since", "2026-11-10"]) == 0
    out = capsys.readouterr().out
    assert "생각 켬 1건" in out and "생각 끔 1건" in out and "a:8001 1" in out


def test_answer_timing_server_has_no_credentials():
    """답 기록의 서버 칸은 호스트·포트만 — 주소에 박힌 계정·비밀값이 기록에 남지 않는다(11-10 보안 검토)."""
    import inspect
    from zzaimy.app import responder
    src = inspect.getsource(responder)
    assert '.netloc' not in src.split('last_timing["server"]')[0][-400:]
    assert "_u.hostname" in src
