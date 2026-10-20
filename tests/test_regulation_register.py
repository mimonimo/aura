"""반입한 규정을 기준 문서로 — 후보(판 묶음마다 최신 판, 규정을 다루는 다른 서류 제외)·등록·해제."""
from pathlib import Path

from zzaimy.app import regulation_register as rr
from zzaimy.app.db import Database

BODY = "\n".join(f"제{i}조(목적) 이 규정은 산학협력단의 운영에 관한 사항을 정한다. 세부 사항은 따로 정한다." for i in range(1, 6))


def _doc(db, name, kind="regulation", family=None, text=BODY, stored="x"):
    did = db.add_document(name, stored, "grant")
    with db._conn() as conn:
        conn.execute("UPDATE documents SET status = 'reviewed', kind = ?, family = ?, masked_text = ? WHERE id = ?",
                     (kind, family, text, did))
    return did


def test_candidates_newest_version_and_excludes_agreements(tmp_path):
    db = Database(tmp_path / "r.db")
    old = _doc(db, "영남이공대학교 연구비 관리 규정 · 2012년 2월 23일", family="연구비관리규정")
    new = _doc(db, "영남이공대학교 연구비 관리 규정 · 2024년 3월 1일", family="연구비관리규정")
    _doc(db, "LINC3.0 협약서", kind="regulation")
    _doc(db, "현장실습 운영 지침", kind="guideline")
    _doc(db, "(주)대영코어텍", kind="regulation")
    _doc(db, "05-06 컴정관", kind="regulation")                          # 낱말 속 「정관」 은 정관이 아니다                     # 갈래만 규정 — 제목이 규정 이름이 아니다
    _doc(db, "사업 계획서", kind="plan")
    got = rr.candidates(db)
    titles = {i["title"]: i for i in got["items"]}
    assert set(titles) == {"영남이공대학교 연구비 관리 규정 · 2024년 3월 1일", "현장실습 운영 지침"}
    assert titles["영남이공대학교 연구비 관리 규정 · 2024년 3월 1일"]["id"] == new and titles["영남이공대학교 연구비 관리 규정 · 2024년 3월 1일"]["versions"] == 2
    assert old not in {i["id"] for i in got["items"]}
    assert [i["title"] for i in rr.candidates(db, "현장 실습")["items"]] == ["현장실습 운영 지침"]


def test_register_then_unregister_only_what_we_registered(tmp_path):
    db = Database(tmp_path / "r.db")
    did = _doc(db, "산학협력단 운영 규정")
    native = db.add_document("기본계획", "x", "regulation")
    with db._conn() as conn:
        conn.execute("UPDATE documents SET status = 'reviewed' WHERE id = ?", (native,))
    out = rr.register(db, [did], tmp_path)
    assert out["registered"] == 1 and out["chunks"] > 0 and db.get_document(did)["doc_type"] == "regulation"
    assert (tmp_path / ".reindex-needed").exists() and rr.candidates(db)["registered"][0]["id"] in (did, native)
    assert rr.candidates(db)["total"] == 0
    old = _doc(db, "산학협력단 운영 규정 · 2012년", family=db.get_document(did).get("family") or "fam")
    with db._conn() as conn:
        conn.execute("UPDATE documents SET family = ? WHERE id IN (?, ?)", ("fam", did, old))
    assert old not in {i["id"] for i in rr.candidates(db)["items"]}           # 최신 판을 올렸으면 옛 판은 후보가 아니다
    back = rr.unregister(db, [did, native], tmp_path)
    assert back == {"unregistered": 1, "kept": 1}
    assert db.get_document(did)["doc_type"] == "grant" and db.get_document(native)["doc_type"] == "regulation"
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM regulation_chunks WHERE doc_id = ?", (did,)).fetchone()[0] == 0


def test_newer_revision_stays_candidate_after_older_registered(tmp_path):
    db = Database(tmp_path / "r.db")
    old = _doc(db, "가상 연구비 규정 · 2023년 3월 1일", family="연구비")
    rr.register(db, [old], tmp_path)
    new = _doc(db, "가상 연구비 규정 · 2025년 3월 1일", family="연구비")
    older = _doc(db, "가상 연구비 규정 · 2020년 3월 1일", family="연구비")
    ids = {i["id"] for i in rr.candidates(db)["items"]}
    assert new in ids and older not in ids                                  # 개정판은 후보, 옛 판은 아니다


def test_rule_key_groups_revisions_and_issuer_flags_other_schools():
    same = ["영남이공대학교 연봉계약제전임교원 계약임용에 관한 규정 · 2018년 05월 24일", "연봉계약제 전임교원 계약임용에 관한 규정",
            "4-37 연봉계약제전임교원 계약임용에 관한 규정(2013-11-27)"]
    assert len({rr.rule_key(t) for t in same}) == 1
    assert rr.rule_key("자체운영지침(가족회사)") != rr.rule_key("자체운영지침(캡스톤디자인)")   # 판이 아닌 괄호는 이름이다
    assert rr.rule_key("3-3규정&지침") != rr.rule_key("3-5규정&지침")                       # 띄어쓰기 없는 번호는 이름의 일부
    assert rr.rule_key("교육부훈령 제 553 호 지역혁신중심 대학지원체계 운영규정") == rr.rule_key(
        "지역혁신중심 대학지원체계(RISE) 운영규정(교육부훈령 제511호)")
    assert rr.rule_key("사회맞춤형 LINC＋사업단 규정") == rr.rule_key("사회맞춤형 LINC(＋)사업단 규정")
    assert rr.revision("교육부훈령 제553호 운영규정") > rr.revision("운영규정(교육부훈령 제511호)", "", "부 칙 2024. 12. 30.")
    assert rr.issuer("금오공과대학교연구비관리규정") == "금오공과대학교"
    assert rr.issuer("대전보건대학 교외 연구비 관리규정") == "대전보건대학"
    for ours in ("영남이공대학교 학칙", "2-2.(영남이공대학교)사업자체규정", "전문대학 교육역량강화사업 지침",
                 "산학협력선도전문대학육성사업 지침", "지역혁신중심 대학지원체계 운영규정"):
        assert rr.issuer(ours) == "", ours


def test_candidates_skip_other_institutions_and_group_by_rule(tmp_path):
    db = Database(tmp_path / "r.db")
    _doc(db, "영남대학교외부연구비간접비운영에관한세칙")
    old = _doc(db, "4-37 연봉계약제전임교원 계약임용에 관한 규정", family="a2018", text="머리\n" + BODY + "\n부 칙(2018. 5. 24)\n시행")
    new = _doc(db, "연봉계약제 전임교원 계약임용에 관한 규정", family="b", text="머리\n" + BODY + "\n부 칙(2025. 2. 28)\n시행")
    got = rr.candidates(db)["items"]
    assert [(i["id"], i["versions"]) for i in got] == [(new, 2)]
    rr.register(db, [new], tmp_path)
    _doc(db, "영남이공대학교 연봉계약제전임교원 계약임용에 관한 규정", text="머리\n" + BODY + "\n부 칙(2025. 2. 28)\n시행")
    assert rr.candidates(db)["total"] == 0                                 # 기준 문서와 같은 판의 사본은 후보가 아니다
    assert old != new


def test_register_newer_demotes_older_and_pick_skips_clipped(tmp_path):
    db = Database(tmp_path / "r.db")
    old = _doc(db, "가상 운영 규정 · 2020년 3월 1일", family="x1")
    rr.register(db, [old], tmp_path)
    new = _doc(db, "4-1 가상 운영 규정 · 2024년 3월 1일", family="x2")
    out = rr.register(db, [new], tmp_path)
    assert out["replaced"] == 1 and db.get_document(old)["doc_type"] == "grant"
    assert rr.superseded(db) == []
    long, short = {"id": 1, "title": "규정 · 2020년 3월 1일", "text": "가" * 1000}, {"id": 2, "title": "규정 · 2024년 3월 1일", "text": "가" * 100}
    assert rr.pick_current([long, short])["id"] == 1                       # 가장 새 판이 잘린 판독이면 다음 판
    bundle = {"id": 3, "title": "규정 · 2004년 3월 1일", "text": "가" * 5000}           # 옛 합본은 새 판을 밀어내지 못한다
    mid = {"id": 4, "title": "규정 · 2012년 3월 1일", "text": "가" * 900}
    assert rr.pick_current([long, mid, bundle])["id"] == 1
    a = {"id": 5, "title": "2014학년도 지침(안)", "family": "20131126", "text": ""}
    b = {"id": 6, "title": "2014학년도 지침", "family": "20131217", "text": ""}
    c = {"id": 7, "title": "1-1. 2014학년도 지침", "text": ""}
    d = {"id": 8, "title": "2013학년도 지침", "family": "20121115", "text": ""}
    assert rr.pick_current([a, b, c, d])["id"] == 6                       # 학년도 먼저, 그 안에서 날짜


def test_clipped_filename_yields_to_body_title():
    from zzaimy.app.doc_naming import choose_name, clipped_title
    text = "영남이공대학교 비정년계열전임교원 계약임용에 관한 규정\n\n학과장회 통과일자 : 2021년 03월 25일\n\n제1조(목적) 이 규정은"
    got = choose_name("04_남이공대학교 비정년계열전임교원 계약임용에 관한 규정.hwp", text, prefer_file=True)
    assert got["name"].startswith("영남이공대학교") and got["source"] == "body"
    assert not clipped_title("비정년계열전임교원 계약임용에 관한 규정", "영남이공대학교 비정년계열전임교원 계약임용에 관한 규정")


def test_patterns_and_query_stay_cheap_on_hostile_input(tmp_path):
    """긴 제목·되풀이 글자·긴 찾기 낱말에서도 정규식이 역추적으로 늘어지지 않는다(제목 상한·수량 상한·낱말 상한)."""
    import time
    hostile = ["가" * 50000, "규정(" * 20000 + "x", "규정 ·" * 20000 + "(", "1-" * 20000 + "x", "(" * 50000,
               "교육부" * 20000 + "훈령", "대학" * 25000]
    t0 = time.perf_counter()
    for t in hostile:
        rr.GENRE.search(t[:rr.TITLE_MAX]); rr.issuer(t); rr.rule_key(t); rr.revision(t, t, t[:20000])
    assert time.perf_counter() - t0 < 2.0
    db = Database(tmp_path / "r.db")
    _doc(db, "가" * 5000 + " 규정")
    t0 = time.perf_counter()
    got = rr.candidates(db, "가 " * 100000)
    assert time.perf_counter() - t0 < 2.0 and got["total"] <= 1


def test_auto_register_takes_new_versions_but_respects_manual_unregister(tmp_path):
    """반입 자료의 새 판은 저절로 기준 문서가 되고 옛 판은 물러난다 — 사람이 해제한 문서는 다시 올리지 않는다(10-21)."""
    db = Database(tmp_path / "r.db")
    old = _doc(db, "가상 연구비 규정 · 2023년 3월 1일", family="연구비", stored="dgx://규정/2023.hwp")
    rr.register(db, [old], tmp_path)
    upload = _doc(db, "가상 출장 규정", stored="data/platform/documents/x.hwp")      # 사용자가 올린 문서 — 저절로 올리지 않는다
    new = _doc(db, "가상 연구비 규정 · 2025년 3월 1일", family="연구비", stored="dgx://규정/2025.hwp")
    got = rr.auto_register(db, tmp_path)
    assert got["registered"] == 1 and got["replaced"] == 1
    assert db.get_document(new)["doc_type"] == "regulation" and db.get_document(old)["doc_type"] == "grant"
    assert db.get_document(upload)["doc_type"] == "grant"
    other = _doc(db, "가상 장학 규정", stored="dgx://규정/장학.hwp")
    rr.register(db, [other], tmp_path)
    rr.unregister(db, [other], tmp_path, by="dev")                     # 사람이 뺐다
    assert rr.auto_register(db, tmp_path)["registered"] == 0 and db.get_document(other)["doc_type"] == "grant"
