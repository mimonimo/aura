"""절 맡기기(서브 에이전트, ADR-0065 4번) — 지시서 필수 항목, 확인 필요 반환, 관문 거절, off 설정, 라벨 묶음, 근거 압축 폴백."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from tests.test_webhwp import _setup
from zzaimy.app import delegate, llm_budget, webhwp
from zzaimy.app import webhwp_agent as wa
from zzaimy.app import webhwp_templates as wt
from zzaimy.app import webhwp_write as ww


class FakeClient:
    """openai 꼴 가짜 연결 — 보낸 메시지를 남기고 정해 둔 답을 돌려준다(서버 없음 → 문맥 한도는 기본값)."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.sent: list[list[dict]] = []
        self.model = "fake"
        self._extra = {}
        outer = self

        class _Completions:
            def create(self, model, messages, max_tokens, **kw):
                outer.sent.append(messages)
                text = outer.answers.pop(0) if outer.answers else "[본문]\n1) 없음"
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

        class _Models:
            def list(self):
                raise RuntimeError("서버 없음")

        self.client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()), models=_Models(), base_url="")


def _work(tmp_path, monkeypatch, sid=91):
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    wt.ensure_all(db, base)
    hw = wa.HwpTools(db, base, sid, "kim", "기획처", {"id": 1, "name": "AI 사업"}, lambda: [])
    out = hw.forms({"query": "사업계획서"})
    tid = None
    for line in out.splitlines():                         # 절 필드가 둘 이상인 양식
        if not line.split() or not line.split()[0].startswith("tpl:"):
            continue
        t = webhwp.get(db, int(line.split()[0][4:]))
        if sum(1 for x in ww.fields(Path(t["path"]).read_bytes()) if x["kind"] == "절") >= 2:
            tid = line.split()[0]
            break
    if tid is None:
        for r in db.list_files(kind="webhwp", limit=8000):
            if webhwp.locked(db, r["id"]) and sum(1 for x in ww.fields(Path(r["path"]).read_bytes()) if x["kind"] == "절") >= 2:
                tid = f"tpl:{r['id']}"
                break
    assert tid, "절이 둘 이상인 공통 양식이 있어야 한다"
    hw.start({"form": tid, "title": "시험 계획서"})
    secs = [x["name"] for x in hw._fields() if x["kind"] == "절"]
    return app, db, hw, secs


def test_brief_fills_required_items_and_returns_unknowns(tmp_path, monkeypatch):
    monkeypatch.delenv("ZZAIMY_RERANK_URL", raising=False)
    app, db, hw, secs = _work(tmp_path, monkeypatch)
    sid = hw.sid
    db.create_chat_session("시험", owner="kim")
    db.add_chat(sid, "user", "AI 교육 사업계획서 써 줘. 대상은 1학년 320명이야.")
    db.set_setting(f"chat_summary:{sid}", json.dumps({"text": "지금 목표:\n- 2027 AI 교육 사업계획서 작성\n확정된 것:\n- 대상 1학년 320명\n진행 상황:\n- 작업본 만듦", "upto": 1}))
    assert hw.write({"field": secs[0], "text": "1) 사업 개요는 AI 기초 교육 확산임"}).startswith("「")
    client = FakeClient(["[본문]\n1) 1학년 320명 대상 AI 기초 교육 운영함\n2) 교과·비교과 연계함\n[확인 필요]\n- 예산 총액: 자료에 없음\n"])
    hw.client, hw.evidence, hw.delegating = client, (lambda: ["대상 1학년 320명"]), True
    dlg = delegate.Delegator(client, db, sid, hw, "AI 교육 사업계획서 써 줘", search=lambda q: [{"title": "공고", "text": "사업 목적은 AI 교육 확산임."}],
                             quality=False)
    out = dlg.run({"section": secs[1], "gist": "추진 방향", "form": "번호 문단", "length": "500자"})
    user = client.sent[0][1]["content"]
    # 코드가 채운 필수 항목 — 메모 목표·확정, 문서 개요, 앞 절 요지, 담당자 원문 인용
    assert "2027 AI 교육 사업계획서 작성" in user and "대상 1학년 320명" in user
    assert "[문서 개요]" in user and secs[0][2:] in user and "(이번 절)" in user
    assert "AI 기초 교육 확산" in user                              # 앞 절 요지(쓴 절의 gist)
    assert "「AI 교육 사업계획서 써 줘" in user                       # 담당자 원문 인용
    assert "맡겨 썼다" in out and "예산 총액" in out and "320" in out  # 짧은 보고 — 쓴 값·확인 필요
    vals = {x["name"]: x["text"] for x in ww.fields(Path(webhwp.get(db, hw.fid())["path"]).read_bytes())}
    assert vals[secs[1]].startswith("1) 1학년 320명")
    log = delegate.load_log(db, sid)
    assert log[-1]["unknown"] == ["예산 총액: 자료에 없음"] and log[-1]["compress"]["method"] == "fallback"


def test_gate_rejects_invented_numbers_and_redo_limit(tmp_path, monkeypatch):
    app, db, hw, secs = _work(tmp_path, monkeypatch, sid=92)
    client = FakeClient(["[본문]\n1) 참여 학생 4,500명 목표임\n[확인 필요]\n- 없음"] * 3)
    hw.client, hw.evidence, hw.delegating = client, (lambda: ["자료"]), True
    dlg = delegate.Delegator(client, db, hw.sid, hw, "써 줘", quality=False)
    out = dlg.run({"section": secs[0], "gist": "목표"})
    assert "관문에서 막혔다" in out and "4,500" in out
    assert not {x["name"]: x["text"] for x in hw._fields()}[secs[0]]       # 쓰지 않았다
    dlg.run({"section": secs[0], "gist": "목표", "redo_note": "수치 빼기"})
    assert "한 번까지" in dlg.run({"section": secs[0], "gist": "목표"})   # 다시 맡기기는 한 번
    # 양식 예시 글자도 같은 관문
    assert "예시 글자" in wa.check_body("1) ○○대학교 운영", None, 16384)


def test_main_long_write_rejected_when_context_low(tmp_path, monkeypatch):
    app, db, hw, secs = _work(tmp_path, monkeypatch, sid=93)
    hw.delegating = True
    d = llm_budget.track()
    d["items"].append({"label": "에이전트 · 9단계", "group": "메인", "in": 12000, "out": 700, "limit": 16384, "trimmed": False})
    long_text = "\n".join(f"{i}) 교육 과정 운영 내용을 자세히 적음" for i in range(1, 60))
    out = hw.write({"field": secs[0], "text": long_text})
    assert out.startswith("쓰지 않았다") and "delegate_section" in out
    assert hw.write({"field": secs[0], "text": "1) 짧은 본문임"}).startswith("「")      # 짧은 본문은 직접
    assert any("문맥: 남은 약 26%" in line for line in hw.state_lines())
    st = "\n".join(hw.state_lines())                                      # 빈 절 수는 범위가 아니다 — 절 하나 요청은 그 절만
    assert "절 하나·특정 절만 부탁했으면 그 절만" in st and "빈 절을 묶어 맡기는 것이 낫다" not in st
    hw.delegating = False                                                   # 맡기기 꺼짐 — 관문 없음, 상태 글에도 없음
    assert hw.write({"field": secs[0], "text": long_text}).startswith("「")
    assert not any("절 맡기기" in line for line in hw.state_lines())
    llm_budget._USAGE.set(None)


def test_off_setting(monkeypatch):
    monkeypatch.setenv("ZZAIMY_DELEGATE", "off")
    assert not delegate.enabled() and delegate.mode() == "off"
    monkeypatch.delenv("ZZAIMY_DELEGATE")
    assert delegate.enabled() and delegate.mode() == "auto"


def test_sub_call_label_groups_as_sub(tmp_path, monkeypatch):
    app, db, hw, secs = _work(tmp_path, monkeypatch, sid=94)
    client = FakeClient(["[본문]\n1) 내용임\n[확인 필요]\n- 없음"])
    hw.client, hw.delegating = client, True
    d = llm_budget.track()
    delegate.Delegator(client, db, hw.sid, hw, "써 줘", quality=False).run({"section": secs[0], "gist": "개요"})
    item = d["items"][-1]
    assert item["label"].startswith("서브 · 절 맡기기 · ") and item["group"] == "서브"
    llm_budget._USAGE.set(None)


def test_compress_fallback_and_rerank(monkeypatch):
    pieces = ["《공고》\n사업 목적은 지역 AI 교육 확산임. 날씨가 좋음. 총 사업비는 50,000천원임.",
              "《지침》\n점심 메뉴를 정함. 「AI 융합 인재 양성」 사업과 연계함."]
    monkeypatch.delenv("ZZAIMY_RERANK_URL", raising=False)
    monkeypatch.delenv("ZZAIMY_LLM_RERANK", raising=False)
    text, info = delegate.compress("AI 교육 목적", pieces)
    assert info["method"] == "fallback" and "주소 없음" in info["note"] and "날씨" in text      # 리랭커 없으면 원문 그대로
    from zzaimy.app import rerank
    monkeypatch.setenv("ZZAIMY_RERANK_URL", "http://reranker.invalid/score")

    def fake(query, chunks, text_key="content"):
        sc = [0.9 if "목적" in c["content"] else 0.01 for c in chunks]
        return sorted(zip(chunks, sc), key=lambda x: -x[1])
    monkeypatch.setattr(rerank, "rerank_scored", fake)
    text, info = delegate.compress("AI 교육 목적", pieces)
    assert info["method"] == "rerank" and info["after"] < info["before"]
    assert "목적" in text and "50,000" in text and "AI 융합 인재 양성" in text              # 관련 문장 + 숫자·고유명사 우선 보존
    assert "날씨" not in text and "점심" not in text and "《공고》" in text
    monkeypatch.setattr(rerank, "rerank_scored", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    text, info = delegate.compress("AI 교육 목적", pieces)
    assert info["method"] == "fallback" and "실패" in info["note"] and "날씨" in text


def test_guide_needs_shown_and_missing_noticed_once(tmp_path, monkeypatch):
    """작성방법 상자가 요구한 요소(표·도식·출처) — 상태 글에 보이고, 빠지면 한 번 알린다. 쓴 모양은 meta shape 에 남는다."""
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    wt.ensure_all(db, base)
    hw = wa.HwpTools(db, base, 95, "kim", "기획처", {"id": 1, "name": "AI 사업"}, lambda: [])
    tid = next(line.split()[0] for line in hw.forms({"query": "사업계획서"}).splitlines()
               if line.startswith("tpl:") and "사업계획서" in line)
    hw.start({"form": tid, "title": "계획서"})
    needs = hw.needs_map()
    assert needs and all(set(v) <= {"표", "도식", "출처"} for v in needs.values())
    field = next(k for k, v in needs.items() if "출처" in v and k.startswith("절:"))
    assert any("작성방법 요구:" in line for line in hw.state_lines())
    out = hw.write({"field": field, "text": "1) 배경을 적음"})
    assert "작성방법이 요구한" in out and "출처" in out
    out2 = hw.write({"field": field, "text": "1) 배경을 다시 적음"})
    assert "작성방법이 요구한" not in out2                                  # 고쳐 쓰기 안내는 한 번
    hw.write({"field": field, "text": "1) 배경(출처: 기본계획)\n| 구분 | 내용 |\n|---|---|\n| 가 | 나 |\n```도식\n총장\n사업단 → 위원회\n```"})
    sh = webhwp.meta(db, hw.fid())["shape"][field]
    assert sh["tables"] == 1 and sh["diagrams"] == 1 and sh["numbered"] and sh["sources"] and "출처" not in sh["missing"]
    assert wa.needs_of("목표는 성과지표와 연결") == []                        # 「지표」「목표」 의 표 글자는 표 요구가 아니다


def test_outline_then_write_with_numbered_evidence_and_self_check(tmp_path, monkeypatch):
    """쓰기 품질 켬 — 검색어 2~3개로 여러 번 찾고, 번호·쪽 붙인 근거로 [개요] → [본문], 자기 점검이 고친 본문을 쓴다."""
    monkeypatch.delenv("ZZAIMY_RERANK_URL", raising=False)
    app, db, hw, secs = _work(tmp_path, monkeypatch, sid=96)
    asked = []

    def search(q):
        asked.append(q)
        return [{"title": "기본계획", "text": f"{q} 관련 — 지원 대상은 1학년 학생임. 사업 기간은 2027. 3. ~ 2028. 2.임.", "doc_id": None, "chunk_id": None}]
    client = FakeClient(['{"검색어": ["지원 대상", "추진 일정", "평가 지표"]}',
                         "[개요]\n요지: 대상과 일정\n근거: [근거 1]\n표: 필요 없음\n[본문]\n1) 역량 강화함\n[확인 필요]\n- 없음",
                         "[판정] 고침\n[고칠 점]\n- 1 대상이 없음\n[본문]\n1) 1학년 학생 대상 AI 기초 교육 운영함(출처: 기본계획)\n2) 2027. 3. ~ 2028. 2. 운영함\n[확인 필요]\n- 없음"])
    hw.client, hw.evidence, hw.delegating = client, (lambda: ["써 줘"]), True
    dlg = delegate.Delegator(client, db, hw.sid, hw, "써 줘", search=search, quality=True)
    out = dlg.run({"section": secs[0], "gist": "대상과 일정"})
    assert len(asked) == 4 and "지원 대상" in asked and "평가 지표" in asked          # 절 제목+요지 + 27B 검색어 셋
    write_prompt = client.sent[1]
    assert "[근거 1]" in write_prompt[1]["content"] and "[근거 4]" in write_prompt[1]["content"]
    assert "[개요]" in write_prompt[0]["content"] and "출처" in write_prompt[0]["content"]
    assert "기준표" in client.sent[2][0]["content"]
    vals = {x["name"]: x["text"] for x in hw._fields()}
    assert vals[secs[0]].startswith("1) 1학년 학생 대상")                             # 자기 점검이 고친 본문
    rec = delegate.load_log(db, hw.sid)[-1]
    assert rec["queries"] == ["지원 대상", "추진 일정", "평가 지표"] and rec["check"]["used"] and "대상과 일정" in rec["outline"]
    assert "자기 점검 고침(고쳐 씀)" in out


def test_self_check_rewrite_with_invented_number_keeps_first_body(tmp_path, monkeypatch):
    app, db, hw, secs = _work(tmp_path, monkeypatch, sid=97)
    client = FakeClient(["[개요]\n요지\n[본문]\n1) 교육 운영함\n[확인 필요]\n- 없음",
                         "[판정] 고침\n[고칠 점]\n- 1 수치 없음\n[본문]\n1) 학생 4,500명 교육 운영함\n[확인 필요]\n- 없음"])
    hw.client, hw.evidence, hw.delegating = client, (lambda: ["써 줘"]), True
    dlg = delegate.Delegator(client, db, hw.sid, hw, "써 줘", quality=True)      # 찾기 없음 — 검색어 호출도 없다
    dlg.run({"section": secs[0], "gist": "운영"})
    assert {x["name"]: x["text"] for x in hw._fields()}[secs[0]] == "1) 교육 운영함"
    chk = delegate.load_log(db, hw.sid)[-1]["check"]
    assert chk["verdict"] == "고침" and chk["used"] is False and "4,500" in chk["kept_why"]


def test_parallel_delegation_respects_slots(tmp_path, monkeypatch):
    """여러 절 동시 맡기기 — 연결별 동시 상한의 합까지만 맡기고, 자리마다 연결을 나눠 쓴다. 쓰기는 차례대로 모두 들어간다."""
    app, db, hw, secs = _work(tmp_path, monkeypatch, sid=98)
    if len(secs) < 4:
        secs = secs + secs[:4 - len(secs)]
    a, b = FakeClient(["[본문]\n1) 가 내용임\n[확인 필요]\n- 없음"] * 4), FakeClient(["[본문]\n1) 나 내용임\n[확인 필요]\n- 없음"] * 4)
    hw.client, hw.delegating = a, True
    dlg = delegate.Delegator(a, db, hw.sid, hw, "써 줘", pool=[(b, 2), (a, 1)], quality=False)
    assert dlg.slots() == [b, a, b]
    uniq = list(dict.fromkeys(secs))
    out = dlg.run({"sections": [{"section": n, "gist": "내용"} for n in uniq[:4]]})
    wrote = [n for n in uniq[:3] if {x["name"]: x["text"] for x in hw._fields()}[n]]
    assert len(wrote) == min(3, len(uniq))
    assert len(b.sent) == 2 and len(a.sent) == 1
    if len(uniq) >= 4:
        assert "동시 상한(3개)" in out
    monkeypatch.setenv("ZZAIMY_DELEGATE_PARALLEL", "3")
    assert delegate.conn_cap(a) == 3 and delegate.parallel_on()
    monkeypatch.setenv("ZZAIMY_DELEGATE_PARALLEL", "0")
    assert not delegate.parallel_on()


def test_revise_checklist_gate_retries_then_writes(tmp_path, monkeypatch):
    """고쳐 쓰기 — 27B 가 지시를 점검표로, 코드는 바뀜·길이 방향·표 유무를 센다. 어기면 까닭을 붙여 한 번 다시 청한다."""
    app, db, hw, secs = _work(tmp_path, monkeypatch, sid=99)
    hw.evidence = lambda: ["일정은 표로 정리해 줘"]
    assert hw.write({"field": secs[0], "text": "1) 준비 단계 운영함\n2) 평가 단계 운영함"}).startswith("「")
    client = FakeClient(['{"의도": "일정을 표로", "길이": "그대로", "표": "넣음", "더할 항목": [], "검색어": []}',
                         "[본문]\n1) 준비 단계 운영함\n2) 평가 단계 운영함\n[확인 필요]\n- 없음",
                         "[본문]\n1) 추진 일정\n| 단계 | 내용 |\n|---|---|\n| 준비 | 과정 설계 |\n| 평가 | 성과 점검 |\n[확인 필요]\n- 없음"])
    hw.client = client
    rev = delegate.Reviser(client, db, hw.sid, hw, "일정은 표로 정리해 줘")
    out = rev.run({"field": secs[0], "instruction": "일정은 표로 정리"})
    assert "고쳐 썼다" in out and "표 0 → 1" in out
    retry = client.sent[2]
    assert "지금 글과 같다" in retry[-1]["content"]                                     # 첫 답이 그대로라 다시 청함
    assert "[지금 글" in client.sent[1][1]["content"] and "준비 단계 운영함" in client.sent[1][1]["content"]
    rec = delegate.load_log(db, hw.sid)[-1]
    assert rec["kind"] == "revise" and rec["written"] and len(rec["tries"]) == 2
    assert webhwp.meta(db, hw.fid())["shape"][secs[0]]["tables"] == 1
    # 비어 있는 절은 고쳐 쓰기가 아니다
    if len(secs) > 1:
        assert "아직 비어" in rev.run({"field": secs[1], "instruction": "더 구체적으로"})


def test_revise_problems_and_invented_numbers_block(tmp_path, monkeypatch):
    p = delegate.revise_problems({"길이": "늘림", "표": "그대로", "더할 항목": ["운영 시기"]}, "1) 교육 운영함", "1) 교육 운영함 더")
    assert any("길이를 늘려야" in x for x in p) and any("운영 시기" in x or "시기" in x for x in p)
    assert delegate.revise_problems({"길이": "줄임"}, "1) " + "가" * 100, "1) " + "가" * 50) == []
    app, db, hw, secs = _work(tmp_path, monkeypatch, sid=100)
    hw.evidence = lambda: ["더 구체적으로"]
    hw.write({"field": secs[0], "text": "1) 교육 운영함"})
    client = FakeClient(['{"길이": "늘림"}'] + ["[본문]\n1) 학생 4,500명 대상 교육을 매주 운영함\n[확인 필요]\n- 없음"] * 2)
    hw.client = client
    out = delegate.Reviser(client, db, hw.sid, hw, "더 구체적으로").run({"field": secs[0], "instruction": "더 구체적으로"})
    assert "고쳐 쓰지 못했다" in out and "4,500" in out
    assert {x["name"]: x["text"] for x in hw._fields()}[secs[0]] == "1) 교육 운영함"


def test_resolve_number_and_state_guidance(tmp_path, monkeypatch):
    app, db, hw, secs = _work(tmp_path, monkeypatch, sid=101)
    import re as _re
    nums = [_re.match(r"^절:\s*(\d+)\.\s", n).group(1) for n in secs if _re.match(r"^절:\s*\d+\.\s", n)]
    once = [k for k in nums if nums.count(k) == 1]
    twice = [k for k in nums if nums.count(k) > 1]
    if once:
        assert hw.resolve_number(f"{once[0]}번 절") == next(n for n in secs if n.startswith(f"절:{once[0]}. "))
    if twice:
        assert hw.resolve_number(f"{twice[0]}번 절") is None                 # 같은 번호 절이 여럿이면 정하지 않는다(에이전트가 이름으로 짚는다)
    assert hw.resolve_number("추진 일정") is None
    hw.revising, hw.delegating, hw.delegate_cap = True, True, 3
    st = "\n".join(hw.state_lines())
    assert "hwp_revise" in st and "sections" in st


def test_parse_sub_header_only_at_line_start():
    raw = ("[개요]\n- 비전: [확인 필요] 문구 없음\n[본문]\n| 구분 | 내용 |\n|---|---|\n| 비전 | [확인 필요] |\n[확인 필요]\n- 비전 문구: 근거 없음")
    body, unk = delegate.parse_sub(raw)
    assert body.startswith("| 구분 |") and "| 비전 | [확인 필요] |" in body and unk == ["비전 문구: 근거 없음"]
    assert delegate.parse_sub("1) 내용임\n[확인 필요]\n- 없음") == ("1) 내용임", [])
    assert delegate.revise_problems({"길이": "그대로", "더할 항목": ["취업 통계"]}, "1) 가", "1) 나", ["취업 통계: 근거 없음"]) == []
