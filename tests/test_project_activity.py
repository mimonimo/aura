"""프로젝트 진행 현황(/project/{id}/activity) — 권한, 사람별 요청 집계, 답마다 사용량 합계(옛 대화는 마지막 답 한 건), 작업 요약 저장."""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from tests.test_accounts import _app, _login

from zzaimy.app import project_access as pa


def _staff(app, uid: str, dept: str, role: str = "staff", grade: str = "") -> None:
    app.state.accounts[uid] = app.state.new_account(uid, "pass-12345678", role)
    app.state.accounts[uid]["dept"] = dept
    if grade:
        app.state.accounts[uid]["grade"] = grade


def _world(tmp_path):
    app = _app(tmp_path)
    _staff(app, "kim", "기획처")
    _staff(app, "lee", "기획처")
    _staff(app, "park", "입학처")
    db = app.state.db
    pid = db.create_project("grant", "AI 사업", owner="kim")
    pa.set_shares(db, pid, "shared", [{"to": "user:lee", "level": "edit"}])
    s1 = db.create_chat_session("계획서 초안", project_id=pid, owner="kim")
    db.add_chat(s1, "user", "사업 개요 절을 써 줘\n예산은 빼고")
    a1 = db.add_chat(s1, "assistant", "사업 개요 초안입니다.")
    db.add_chat(s1, "user", "성과 지표 표도")
    a2 = db.add_chat(s1, "assistant", "성과 지표 표입니다.")
    s2 = db.create_chat_session("예산 검토", project_id=pid, owner="lee")
    db.add_chat(s2, "user", "예산 항목 검토")
    db.add_chat(s2, "assistant", "검토 의견입니다.")
    secret = db.create_chat_session("비공개", project_id=pid, owner="lee")
    db.set_chat_private(secret, True)
    db.add_chat(secret, "user", "숨긴 요청")
    usage = {"calls": 3, "prompt": 1000, "peak": 5000, "limit": 16384, "trimmed": False, "items": [], "at": 1760000000}
    db.set_setting(f"chat_ctx_msg:{a1}", json.dumps(usage))
    db.set_setting(f"chat_ctx_msg:{a2}", json.dumps({**usage, "prompt": 2000, "peak": 7000, "trimmed": True}))
    db.set_setting(f"chat_ctx:{s1}", json.dumps({**usage, "prompt": 2000}))         # 마지막 답 — 답별 기록이 있으면 겹쳐 세지 않는다
    db.set_setting(f"chat_ctx:{s2}", json.dumps({**usage, "prompt": 400, "calls": 1}))   # 옛 대화 — 한 건으로 대신
    return app, pid, {"s1": s1, "s2": s2, "secret": secret}


def _client(app, uid):
    c = TestClient(app)
    assert _login(c, uid, "pass-12345678")
    return c


def test_only_graded_people_can_see_activity(tmp_path):
    app, pid, _ = _world(tmp_path)
    db = app.state.db
    assert _client(app, "kim").get(f"/api/project/{pid}/activity").status_code == 200            # 만든 사람(사업 책임자)
    assert _client(app, "park").get(f"/api/project/{pid}/activity").status_code == 403          # 남의 부서 프로젝트
    assert _client(app, "park").post(f"/api/project/{pid}/activity/summary", json={"user": "kim"}).status_code == 403
    assert _client(app, "lee").get(f"/api/project/{pid}/activity").status_code == 403           # 함께 작업이어도 책임자가 아니면
    db.set_setting(f"project_lead:{pid}", "lee")                                                 # 사업 책임자로 지정되면 본다
    assert _client(app, "lee").get(f"/api/project/{pid}/activity").status_code == 200
    _staff(app, "boss", "기획처", grade="head")                                                   # 총괄 책임자 — 자기 부서 프로젝트
    assert _client(app, "boss").get(f"/api/project/{pid}/activity").status_code == 200
    _staff(app, "boss2", "입학처", grade="head")
    assert _client(app, "boss2").get(f"/api/project/{pid}/activity").status_code == 403
    _staff(app, "mem", "기획처", grade="member")                                                  # 일반 — 공유받아도 못 본다
    pa.set_shares(db, pid, "shared", [{"to": "user:lee", "level": "edit"}, {"to": "user:mem", "level": "edit"}])
    assert _client(app, "mem").get(f"/api/project/{pid}/activity").status_code == 403
    mine = db.create_project("grant", "내 일", owner="mem")
    assert _client(app, "mem").get(f"/api/project/{mine}/activity").status_code == 403         # 일반은 자기 것도
    _staff(app, "stu", "기획처", role="student")
    assert _client(app, "stu").get(f"/api/project/{pid}/activity").status_code == 403


def test_people_and_requests_per_person(tmp_path):
    app, pid, ids = _world(tmp_path)
    d = _client(app, "kim").get(f"/api/project/{pid}/activity").json()
    people = {p["user"]: p for p in d["people"]}
    assert people["kim"]["roles"] == ["담당자"] and people["kim"]["requests"] == 2
    assert "공유 · 함께 작업" in people["lee"]["roles"] and people["lee"]["requests"] == 1        # 비공개 대화는 세지 않는다
    assert all("숨긴" not in r["text"] for r in d["requests"])
    assert d["requests"][0]["session"] == ids["s2"]                                            # 새 것부터
    assert d["requests"][-1]["head"] == "사업 개요 절을 써 줘\n예산은 빼고"
    page = _client(app, "kim").get(f"/project/{pid}/activity").text
    assert "진행 현황" in page and f'href="/chat/{ids["s1"]}"' in page and 'class="project-tabs pa-tabs"' in page
    assert f'href="/project/{pid}"' in page and 'data-pa-tab="usage"' in page


def test_usage_totals_per_answer_and_legacy(tmp_path):
    app, pid, _ = _world(tmp_path)
    u = _client(app, "kim").get(f"/api/project/{pid}/activity").json()["usage"]
    per = {r["key"]: r for r in u["per_user"]}
    assert per["kim"]["prompt"] == 3000 and per["kim"]["answers"] == 2 and per["kim"]["calls"] == 6
    assert per["kim"]["peak"] == 7000 and per["kim"]["trimmed"] == 1
    assert per["lee"]["prompt"] == 400 and per["lee"]["answers"] == 1
    assert u["legacy"] == 1 and u["answers"] == 3
    assert sum(r["prompt"] for r in u["per_day"]) == 3400
    page = _client(app, "kim").get(f"/project/{pid}/activity").text
    assert "대화별 마지막 답만" in page


class _FakeLLM:
    def __init__(self, text="- 사업 개요와 성과 지표 초안을 만들었다", fail=False):
        self.calls, self.text, self.fail = [], text, fail
        self.model, self._extra = "fake", {}
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self._create)), models=None)

    def _create(self, **kw):
        if self.fail:
            raise ConnectionError("down")
        self.calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.text))])


def test_summary_saved_and_new_activity_count(tmp_path, monkeypatch):
    from zzaimy.app import llm_budget
    monkeypatch.setattr(llm_budget, "context_len", lambda client: 16384)
    app, pid, ids = _world(tmp_path)
    db = app.state.db
    db.set_setting(f"chat_summary:{ids['s1']}", json.dumps({"text": "지금 목표: 계획서 개요", "n": 2}))
    fake = _FakeLLM()
    app.state.activity_client = lambda: fake
    kim = _client(app, "kim")
    r = kim.post(f"/api/project/{pid}/activity/summary", json={"user": "kim"}).json()
    assert r["ok"] and r["text"].startswith("- 사업 개요") and r["n"] == 2
    sent = fake.calls[0]["messages"][1]["content"]
    assert "사업 개요 절을 써 줘" in sent and "지금 목표: 계획서 개요" in sent and "성과 지표 표입니다." in sent
    saved = json.loads(db.get_setting(f"project_activity_summary:{pid}:kim"))
    assert saved["text"] == r["text"] and saved["n"] == 2
    db.add_chat(ids["s1"], "user", "추진 체계도")                                                 # 새 요청 하나
    p = {x["user"]: x for x in kim.get(f"/api/project/{pid}/activity").json()["people"]}["kim"]
    assert p["summary"]["new"] == 1
    assert "새 활동 1건 — 다시 요약" in kim.get(f"/project/{pid}/activity").text
    app.state.activity_client = lambda: _FakeLLM(fail=True)                                       # 모델이 안 닿으면 오류 문구만
    r2 = kim.post(f"/api/project/{pid}/activity/summary", json={"user": "kim"}).json()
    assert r2["ok"] is False and r2["error"]
    assert json.loads(db.get_setting(f"project_activity_summary:{pid}:kim"))["text"] == saved["text"]


def test_head_sees_department_project_page_and_chats(tmp_path):
    app, pid, ids = _world(tmp_path)
    _staff(app, "boss", "기획처", grade="head")
    boss = _client(app, "boss")
    d = boss.get(f"/api/project/{pid}/activity").json()
    assert {p["user"]: p["requests"] for p in d["people"]}.get("kim") == 2                      # 공유 대상이 아니어도 비공개 아닌 대화는 센다
    assert all("숨긴" not in r["text"] for r in d["requests"])
    assert boss.get(f"/project/{pid}/activity").status_code == 200


def test_stale_lead_falls_back_to_owner():
    """공유에서 빠진·꺼진 계정의 옛 책임자 지정은 권한으로 남지 않는다(만든 사람으로 돌아감)."""
    from zzaimy.app import project_access as pa

    class DB:
        def __init__(self):
            self.s = {}

        def get_setting(self, k, d=""):
            return self.s.get(k, d)

        def set_setting(self, k, v):
            self.s[k] = v

    db, proj = DB(), {"id": 7, "owner": "kim"}
    accounts = {"kim": {"role": "staff", "dept": "기획처"}, "lee": {"role": "staff", "dept": "기획처"}}
    pa.set_shares(db, 7, "shared", [{"to": "user:lee", "level": "edit"}])
    choices = pa.lead_choices(proj, pa.shares(db, proj), accounts)
    pa.set_lead(db, proj, "lee", choices)
    assert pa.lead(db, proj, accounts) == "lee"
    pa.set_shares(db, 7, "shared", [{"to": "user:lee", "level": "view"}])      # 참조로 낮춤
    assert pa.lead(db, proj, accounts) == "kim"
    pa.set_shares(db, 7, "shared", [{"to": "user:lee", "level": "edit"}])
    accounts["lee"]["active"] = False                                                         # 계정 끔
    assert pa.lead(db, proj, accounts) == "kim"
