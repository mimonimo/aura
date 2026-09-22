"""판본 묶음·중복 판정·붙임 잇기 — 실측 2026-09-22(같은 양식이 해마다 세 판 들어옴)를 일반 규칙으로."""
from pathlib import Path

from zzaimy.app.db import Database
from zzaimy.app.doc_family import (attachment_no, batch_key, family_key, judge, link_attachments,
                                   similarity, year_of)


def test_family_key_ignores_attachment_prefix_spacing_and_extension():
    a = family_key("(붙임2) 첨단분야 혁신융합대학 사업 가 신청서.hwpx")
    b = family_key("붙임 2. 첨단분야 혁신융합대학 사업 가 신청서.hwp")
    c = family_key("붙임2. 첨단분야 혁신융합대학 사업 가 신청서.hwpx")
    assert a == b == c
    assert family_key("복학원") == "복학원"
    assert family_key("사업 본 신청서") != a


def test_attachment_batch_groups_forms_of_one_announcement():
    forms = ["(붙임2) 첨단분야 혁신융합대학 사업 가 신청서.hwpx",
             "(붙임6) 첨단분야 혁신융합대학 사업 핵심성과지표 정의서 및 산출근거.hwpx",
             "(붙임3) 첨단분야 혁신융합대학 사업 본 신청서.hwpx"]
    assert len({batch_key(f) for f in forms}) == 1
    assert batch_key("붙임 2. 첨단분야 혁신융합대학 사업 가 신청서.hwp") != batch_key(forms[0])   # 다른 해의 공고
    assert batch_key("복학원") == ""
    assert attachment_no("[붙임3] BRIDGE3.0사업 양식.hwp") == 3 and attachment_no("복학원") is None
    assert year_of("", "붙임1. 2024년 첨단분야 사업 추진계획.hwpx") == "2024"
    assert year_of("2023년 사업 공고\n…") == "2023" and year_of("연도 없음") == ""


def test_similarity_is_blind_to_spacing_and_sensitive_to_content():
    base = "위 본인은 아래와 같은 사유로 복학하고자 하오니 허가하여 주시기 바랍니다. 휴학기간 년 월 일"
    assert similarity(base, base.replace(" ", "  ")) == 1.0
    assert similarity(base, "전혀 다른 문서의 본문입니다. 신청 기간과 지원 규모를 적는다.") < 0.1


def test_judge_rejects_same_content_and_keeps_versions():
    body = "가 신청서 신청분야 ① 항공드론 ② 반도체소부장 컨소시엄 구성 주관 대학 대학명 " * 20
    sibs = [{"id": 490, "masked_text": body}, {"id": 518, "masked_text": "다른 내용 " * 50}]
    assert judge(body + " ", sibs)["duplicate_of"] == 490
    other = body.replace("항공드론 ② 반도체소부장", "그린바이오 ② 첨단소재 ③ 데이터보안 ④ 차세대디스플레이 ⑤ 사물인터넷")
    j = judge(other, sibs)
    assert j["duplicate_of"] is None and j["version_of"] == 490


def test_link_attachments_points_forms_to_their_head(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    head = db.add_document("붙임1. 2024년 첨단분야 혁신융합대학 사업 추진계획.hwpx", "x", doc_type="regulation")
    db.set_document_kind(head, "plan")
    f2 = db.add_document("붙임2. 첨단분야 혁신융합대학 사업 가 신청서.hwpx", "x", doc_type="regulation")
    f3 = db.add_document("붙임3. 첨단분야 혁신융합대학 사업 본 신청서.hwpx", "x", doc_type="regulation")
    other = db.add_document("(붙임2) 첨단분야 혁신융합대학 사업 가 신청서.hwpx", "x", doc_type="regulation")
    assert link_attachments(db, f2) == head and link_attachments(db, f3) == head
    assert link_attachments(db, head) is None
    assert link_attachments(db, other) is None                    # 앞머리 모양이 다르면 다른 공고
    assert db.get_document(f2)["related_criteria_id"] == head
    assert db.same_family(db.get_document(f2)["family"], f2)[0]["id"] == other
    assert db.family_counts("regulation")[db.get_document(f2)["family"]] == 2


def test_family_gate_blocks_same_content_and_links_versions(tmp_path: Path):
    from zzaimy.app.pipeline import DocumentProcessor

    db = Database(tmp_path / "t.db")
    body = "첨단분야 혁신융합대학 사업 가 신청서 신청분야 항공드론 반도체소부장 컨소시엄 구성 " * 30
    first = db.add_document("(붙임2) 첨단분야 혁신융합대학 사업 가 신청서.hwpx", "x", doc_type="regulation")
    db.update_document(first, status="reviewed", masked_text=body)
    proc = DocumentProcessor.__new__(DocumentProcessor)
    proc._last_parse_note = ""
    dup = db.add_document("붙임 2. 첨단분야 혁신융합대학 사업 가 신청서.hwp", "x", doc_type="regulation")
    assert proc._family_gate(db, dup, db.get_document(dup), body + "\n") is True
    d = db.get_document(dup)
    assert d["status"] == "failed" and "같은 내용" in d["error"] and f"#{first}" in d["error"]
    ver = db.add_document("붙임2. 첨단분야 혁신융합대학 사업 가 신청서.hwpx", "x", doc_type="regulation")
    changed = body.replace("항공드론 반도체소부장", "그린바이오 첨단소재 데이터보안 차세대디스플레이 사물인터넷 양자")
    assert proc._family_gate(db, ver, db.get_document(ver), changed) is False
    assert db.get_document(ver)["version_of"] == first and "2판" in proc._last_parse_note
