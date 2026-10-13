"""검색 낱말 규칙 하나 — 한 낱말 질의(영문 약칭·붙여 쓴 복합 명사)도 본문을 찾고, 전각 표기는 반각과 같이 찾는다.

옛 규칙은 Kiwi 가 띄어 쓴 꼴로 내는 복합 명사를 버리고 영문 약칭은 명사가 아니라 뽑지 않아, 「부트캠프」·「RISE」 한 낱말
검색의 본문 대목이 0건이었다. 색인과 질의가 같은 함수(grant_lex.lex_terms)를 쓰고, 옛 규칙의 줄은 sync 가 다시 뽑는다."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import grant_lex
from zzaimy.app.chat_history import query_forms
from zzaimy.app.main import create_app


def test_lex_terms_keeps_acronyms_and_compounds():
    got = grant_lex.lex_terms("ＬＩＦＥ 사업과 마이크로캠퍼스 운영")
    assert "life" in got and "사업" in got and "운영" in got
    assert all(" " not in t and t == t.lower() for t in got)
    assert "부트캠프" in grant_lex.lex_terms("부트캠프")        # Kiwi 가 「부트 캠프」로 띄어 내는 복합 명사도 한 낱말로
    assert grant_lex.lex_terms("KOSAF") == {"kosaf"} and grant_lex.lex_terms("") == frozenset()


def _app(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(),
                     password="boot-pass-1")
    c = TestClient(app)
    c.post("/login", data={"username": "zzaimy", "pw": "boot-pass-1"})
    return app, c


def test_single_word_query_finds_body_and_old_rows_are_refreshed(tmp_path):
    app, c = _app(tmp_path)
    db = app.state.db
    d = db.add_document("2024 산학 운영 보고.hwp", "x", doc_type="grant", sector="grant")
    db.update_document(d, status="reviewed")
    db.replace_doc_chunks(d, [{"kind": "text", "content": "KOSAF 연계 과정과 메이커스페이스 운영 실적을 정리하였다."}])
    assert grant_lex.sync(db)["added"] == 1
    for word in ("KOSAF", "kosaf"):
        page = c.get("/search", params={"q": word}).text
        ev = page.split('id="srEvidence"', 1)[1].split('id="srProjects"', 1)[0]
        assert f'/doc/{d}#q=' in ev, word
    # 옛 규칙으로 뽑힌 줄(v=1)은 다음 sync 가 새 규칙으로 다시 뽑는다
    with db._conn() as conn:
        conn.execute("UPDATE grant_lex SET nouns = '과정 실적', v = 1")
    got = grant_lex.sync(db)
    assert got["refreshed"] == 1 and got["stale"] == 0
    assert grant_lex.rank(db, grant_lex.lex_terms("KOSAF"), None, None)


def test_search_shows_titles_and_padded_receipt_numbers(tmp_path):
    app, c = _app(tmp_path)
    db = app.state.db
    d = db.add_document("현장실습 안내문.hwpx", "x", doc_type="grant")
    receipt = db.get_document(d)["receipt_no"]                         # 저장은 4자리
    page = c.get("/search", params={"q": "현장실습"}).text
    docs = page.split('id="srDocuments"', 1)[1].split('id="srEvidence"', 1)[0]
    assert "현장실습 안내문.hwpx" not in docs and "현장실습 안내문" in docs
    head, seq = receipt.rsplit("-", 1)
    assert f"{head[:4]}-{seq.zfill(5)}" in docs                          # 사업 문서 기본 코드(국고)는 표시에서 뺀다


def test_chat_search_matches_full_and_half_width_in_both_places(tmp_path):
    assert "ＡＩＤ" in query_forms("aid") and query_forms("ＡＩＤ")[0] == "AID" and query_forms("  ") == []
    app, c = _app(tmp_path)
    db = app.state.db
    wide = db.create_chat_session("[인공지능] ＡＩＤ 사업과 유사한 사례", owner="zzaimy")
    half = db.create_chat_session("AID 신청서 점검", owner="zzaimy")
    dialog = {s["id"] for s in c.get("/api/chat/sessions", params={"q": "AID"}).json()["sessions"]}
    assert dialog == {wide, half}
    assert {s["id"] for s in c.get("/api/chat/sessions", params={"q": "ＡＩＤ"}).json()["sessions"]} == {wide, half}
    chats = c.get("/search", params={"q": "AID"}).text.split('id="srChats"', 1)[1]
    assert f'href="/chat/{wide}"' in chats and f'href="/chat/{half}"' in chats
