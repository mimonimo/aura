"""에이전트가 모르는 값을 묻고 답을 기억하는 일반 경로 — 특정 값(기관 정보)에 한정하지 않는다."""

import json

from zzaimy.app import asks


def test_parse_pairs_only_for_asked_names_and_compose():
    names = ["사업단명", "총괄책임자 성명", "1차년도 예산"]
    got = asks.parse_pairs("사업단명: AI-X 사업단; 총괄책임자 성명은 홍길동입니다, 1차년도 예산은 10억원", names)
    assert got == {"사업단명": "AI-X 사업단", "총괄책임자 성명": "홍길동", "1차년도 예산": "10억원"}
    assert asks.parse_pairs("1.1 절을 다시 써 줘", names) == {}
    assert asks.parse_pairs("사업단명: (값)", names) == {}
    opt = asks.form_option([{"name": "사업단명", "hint": "요약서 표"}, {"name": "1차년도 예산", "hint": "재정 계획"}])
    assert opt["kind"] == "form" and [f["name"] for f in opt["fields"]] == ["사업단명", "1차년도 예산"]
    assert asks.compose(opt["template"], {"사업단명": "AI-X 사업단", "1차년도 예산": ""}) == "사업단명: AI-X 사업단"


def test_remember_routes_institution_keys_and_project_facts(tmp_path):
    from zzaimy.app.db import Database
    db = Database(tmp_path / "t.db")
    sess = {"id": 3, "project_id": 9}
    asks.remember(db, sess, {"사업단명": "AI-X 사업단", "총장": "홍길동"})
    f = asks.facts(db, sess)
    assert f["사업단명"] == "AI-X 사업단" and f["총장"] == "홍길동"
    assert json.loads(db.get_setting("project_facts:9", ""))["사업단명"] == "AI-X 사업단"
    assert db.get_setting("institution:총장", "") == "홍길동"
    asks.add_pending(db, 3, [{"name": "사업단명", "hint": "h"}]); asks.add_pending(db, 3, [{"name": "사업단명", "hint": "h"}, {"name": "예산", "hint": ""}])
    assert [a["name"] for a in asks.pending(db, 3)] == ["사업단명", "예산"]
