"""대화 기억 — 최근 창은 문맥 한도 몫만큼, 그 앞은 작업 메모로 접고, 답 앞 단계 추론은 답에서 걷어 낸다."""
from __future__ import annotations

import json
from pathlib import Path

from zzaimy.app import chat_memory as cm
from zzaimy.app import llm_budget
from zzaimy.app.db import Database
from zzaimy.app.responder import split_reasoning, strip_reasoning


def _db(tmp_path: Path) -> tuple[Database, int]:
    db = Database(tmp_path / "t.db")
    sid = db.create_chat_session("시험", owner="kim")
    for i in range(30):
        db.add_chat(sid, "user", f"질문 {i} " + "가" * 300)
        db.add_chat(sid, "assistant", f"답 {i} " + "나" * 300)
    return db, sid


def test_window_scales_with_model_context(tmp_path):
    db, sid = _db(tmp_path)
    _, small = cm.window(db, sid, 4000)
    _, big = cm.window(db, sid, 32000)
    assert 0 < len(small) < len(big) and big[-1]["content"].startswith("답 29")      # 큰 모델이면 저절로 더 담는다


class _Fake:
    def __init__(self, reply):
        self.reply, self.sent, self.model = reply, [], "m"
        self.client = self
        self.chat = self
        self.completions = self
        self._extra = {}

    def create(self, **kw):
        self.sent.append(kw)

        class R:
            pass
        r = R()
        msg = R()
        msg.content = self.reply
        ch = R()
        ch.message = msg
        r.choices = [ch]
        return r


def test_compact_folds_everything_new_into_structured_memo(tmp_path, monkeypatch):
    db, sid = _db(tmp_path)
    monkeypatch.setattr(llm_budget, "context_len", lambda client: 16384)
    fake = _Fake("지금 목표:\n- 2차년도 사업계획서 완성\n확정된 것:\n- 담당 기획처")
    assert cm.compact(db, sid, fake)
    memo = cm.load(db, sid)
    assert memo["text"].startswith("지금 목표") and memo["upto"] > 0
    sent = fake.sent[0]["messages"]
    assert "지난 작업 메모" in sent[1]["content"] and "지금 목표" in sent[0]["content"]
    summary, recent = cm.window(db, sid, 16384)
    assert summary.startswith("지금 목표") and recent and "작업 메모" in cm.block(summary)
    assert not cm.compact(db, sid, fake) or cm.load(db, sid)["upto"] >= memo["upto"]   # 새 메시지가 없으면 그대로


def test_reasoning_is_split_from_answer():
    t = "[1단계: 질문 파악] 예산 표\n[2단계: 근거 확인] 공고 2쪽\n[3단계: 답 구성] 표 하나\n[답]\n| 비목 | 금액 |"
    r, a = split_reasoning(t)
    assert r.startswith("[1단계") and a.startswith("| 비목")
    assert split_reasoning("[1단계: 질문 파악] 쓰는 중")[1] is None
    assert strip_reasoning("[1단계: 질문 파악] x\n본문만") == "본문만"
    assert json.dumps(cm.SUMMARY_PROMPT.format(limit=100, final=cm.FINAL_PROMPT))


def test_final_line_kept_unless_user_changes_it(monkeypatch):
    """최종 결과물 줄 관문 — 단계 이름으로 바뀌거나 사라지면 이전 값을 지키고, 다듬은 것·담당자가 바꾸자고 한 것은 받는다."""
    old = "최종 결과물:\n- 2027 AI 교육 사업계획서(1학년 대상, 국고 사업 신청용)\n지금 목표:\n- 사업계획서 작성\n확정된 것:\n- 예산 5억 원"
    step = "최종 결과물:\n- 추진 일정 표로 정리\n지금 목표:\n- 일정 표\n확정된 것:\n- 예산 5억 원"
    kept = cm.keep_final(old, step, ["추진 일정은 표로 정리해 줘."])
    assert "2027 AI 교육 사업계획서" in cm.parts(kept)["최종 결과물"] and cm.parts(kept)["지금 목표"] == "- 일정 표"
    gone = cm.keep_final(old, "지금 목표:\n- 사업계획서 작성", ["계속 써 줘."])
    assert "2027 AI 교육 사업계획서" in cm.parts(gone)["최종 결과물"]
    finer = "최종 결과물:\n- 2027 AI 교육 사업계획서(1학년 대상, 국고 사업 신청용, 마감 11월)\n지금 목표:\n- 작성"
    assert "마감 11월" in cm.parts(cm.keep_final(old, finer, ["마감은 11월이야"]))["최종 결과물"]
    changed = "최종 결과물:\n- 2027 AI 교육 결과보고서\n지금 목표:\n- 작성"
    assert "결과보고서" in cm.parts(cm.keep_final(old, changed, ["계획서 말고 결과보고서로 바꾸자"]))["최종 결과물"]
    assert cm.keep_final("", step, []) == step                                   # 이전 줄이 없으면 새 것
    assert cm.parts("최종 결과물:\n- 가\n지금 목표:\n- 나")["최종 결과물"] == "- 가"
    assert "최종 결과물" in cm.SUMMARY_PROMPT.format(limit=100, final=cm.FINAL_PROMPT)
    monkeypatch.setenv("ZZAIMY_MEMO_GOAL_LOCK", "0")
    assert not cm.goal_lock()


def test_keep_final_accepts_concrete_line_over_unsettled_one():
    old = "최종 결과물:\n- 내년 AI 교육 사업 준비 관련 문서(구체적 문서 이름·용도·대상·마감 미정)\n지금 목표:\n- 자료 찾기"
    new = "최종 결과물:\n- 「2027 AI 교육 사업계획서」 한글 문서(대상 1학년 전체, 예산 5억 원, 2027. 3. ~ 2028. 2.)\n지금 목표:\n- 절 채우기"
    assert "사업계획서" in cm.parts(cm.keep_final(old, new, ["양식부터 잡아 줘."]))["최종 결과물"]
    settled = "최종 결과물:\n- 「2027 AI 교육 사업계획서」 한글 문서\n지금 목표:\n- 절 채우기"
    step = "최종 결과물:\n- 표 서식 고치기\n지금 목표:\n- 표"
    assert "사업계획서" in cm.parts(cm.keep_final(settled, step, ["표 머리줄 색 바꿔"[:5]]))["최종 결과물"]
