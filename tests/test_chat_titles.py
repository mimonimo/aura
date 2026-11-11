"""대화 제목 짧게 짓기(chat_titles, 11-12)."""
from types import SimpleNamespace

from zzaimy.app import chat_titles as ct


class _DB:
    def __init__(self, title):
        self.s = {"title": title}

    def get_chat_session(self, sid):
        return dict(self.s)

    def rename_chat_session(self, sid, title):
        self.s["title"] = title


def _client(answer):
    def create(**kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer))])
    return SimpleNamespace(model="m", _extra={}, client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))


LONG = "[2026 전문대학 특성화] 올린 「03 사업계획서 작성 서식」으로 한글 작업본을 만들어 줘. 서식의 표지·현황"


def test_shorten_keeps_project_prefix():
    db = _DB(LONG[:60])
    assert ct.shorten(db, 1, LONG, LONG[:60], client=_client("제목: 「특성화 사업계획서 작업본 만들기」.")) == "[2026 전문대학 특성화] 특성화 사업계획서 작업본 만들기"
    assert db.s["title"].endswith("작업본 만들기")


def test_shorten_skips_short_renamed_or_bad():
    assert ct.shorten(_DB("정년 질문"), 1, "정년 질문", "정년 질문", client=_client("x")) == ""          # 이미 짧다
    db = _DB("담당자가 바꾼 제목")
    assert ct.shorten(db, 1, LONG, LONG[:60], client=_client("특성화 계획서")) == "" and db.s["title"] == "담당자가 바꾼 제목"
    assert ct.shorten(_DB(LONG[:60]), 1, LONG, LONG[:60], client=_client("가" * 40)) == ""            # 너무 긴 답
    assert ct.clean("") == "" and ct.clean("\n") == ""
