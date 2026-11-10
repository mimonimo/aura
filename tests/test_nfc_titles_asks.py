"""11-10 대화 186·187 — 맥에서 올린 자모 풀린 이름(NFD)을 글자 수로 잘라 「육성사ᄋ」 처럼 음절이 갈리고, 에이전트가 그 이름으로
문서를 고르지 못해 답 없이 끝났다. 물었던 값을 받은 뒤 「기억했습니다」 만 따로 남기고 멈추던 흐름도 함께."""
from __future__ import annotations

import unicodedata
from pathlib import Path

from zzaimy.app import storage
from zzaimy.app.db import Database
from zzaimy.app.doc_naming import clean_filename

NFC = "[붙임] 전문대학 특성화 선도대학 육성사업 공고문"
NFD = unicodedata.normalize("NFD", NFC)
MAIN = (Path(__file__).resolve().parents[1] / "src/zzaimy/app/main.py").read_text(encoding="utf-8")


def test_clip_never_leaves_split_syllable():
    cut = storage.clip(NFD, 22)
    assert unicodedata.is_normalized("NFC", cut) and cut == NFC[:22]
    assert not any("ᄀ" <= ch <= "ᇿ" for ch in storage.clip(NFD[:41], 60))   # 이미 갈린 글을 받아도 끝 낱자를 뗀다


def test_match_key_matches_split_and_nfd_titles():
    split = NFD[:40]                                     # 모델이 옮겨 적은 갈린 이름(「…육성사ᄋ」)
    assert storage.match_key(split) and storage.match_key(split) in storage.match_key(NFD)
    assert storage.match_key(NFD) == storage.match_key(NFC)
    assert storage.title_of(NFD + ".hwpx") == NFC


def test_new_documents_and_renames_are_stored_as_syllables(tmp_path):
    db = Database(tmp_path / "t.db")
    did = db.add_document(NFD + ".hwpx", str(tmp_path / "x.hwpx"))
    assert db.get_document(did)["filename"] == NFC + ".hwpx"


def test_naming_rules_work_on_nfd_names():
    raw = unicodedata.normalize("NFD", "[붙임]+전문대학+특성화+선도대학+육성사업+공고문.hwpx")
    got = clean_filename(raw)
    assert unicodedata.is_normalized("NFC", got) and "+" not in got and "전문대학 특성화" in got


def test_tools_pick_documents_by_syllable_key_and_number():
    pick = MAIN[MAIN.index("def pick_document(title: str)"):]
    pick = pick[:pick.index("\n        def ", 10)]
    assert "storage.match_key" in pick and "doc:" in pick and "storage.clip(" in pick
    found = MAIN[MAIN.index("def pick_found(want: str)"):]
    found = found[:found.index("\n        def ", 10)]
    assert "storage.match_key" in found


def test_failed_pick_in_read_document_is_a_retry_not_an_empty_answer():
    t = MAIN[MAIN.index("def t_read_doc(a: dict)"):]
    t = t[:t.index("\n        def ", 10)]
    head = t[:t.index("_read_named_document_result")]
    assert "raise doc_agent.Retry(why)" in head and "return why" not in head


def test_values_given_mid_run_are_applied_and_work_continues():
    """에이전트가 받은 값을 기억할 때 대화에 「기억했습니다」 를 따로 남기거나 선택지를 바꾸지 않고, 값을 넣고 하던 일을 잇게 알린다."""
    rem = MAIN[MAIN.index("def t_remember(a: dict)"):]
    rem = rem[:rem.index("\n        def ", 10)]
    assert "_remember_given(session_id, given, post=False)" in rem and "_APPLY_GIVEN" in rem
    rg = MAIN[MAIN.index("def _remember_given(session_id: int, given: dict, post: bool = True)"):]
    rg = rg[:rg.index("\n    def ", 10)]
    assert rg.index("if not post:") < rg.index("_set_options(") < rg.index("db.add_chat(")
    assert "기억만 하고 끝내지 않는다" in MAIN and "_APPLY_GIVEN)" in MAIN       # 채팅 답(told_now) 줄에도
