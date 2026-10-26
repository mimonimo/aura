"""라이브러리 사업 한눈에 보기 — 사업 찾기, 연도 × 서류 갈래 표(SQL 집계), 주요 문서, 공개 범위 부서 찾아 고르기.

모두 합성 사업·합성 문서다(임시 DB, 임시 장부)."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import library
from zzaimy.app.main import create_app


def _client(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(),
                     password="boot-pass-1")
    c = TestClient(app)
    assert c.post("/login", data={"username": "zzaimy", "pw": "boot-pass-1"}, follow_redirects=False).status_code == 303
    return app, c


def _ledger():
    """임시 장부 — 합성 사업 하나(정식 이름·별칭·지원기관·기간·업무 부서)."""
    path = Path(os.environ["ZZAIMY_DATA_DIR"]) / "kg_external.json"
    path.write_text(json.dumps({"categories": {}, "programs": [
        {"name": "가상 인재양성 사업", "terms": ["가상인재", "가상 인재 양성"], "ministry": "교육부", "category": "특수목적",
         "agency": "가상 재단", "period": "2023.03.01~2025.02.28", "sources": ["synthetic"],
         "school": {"years": [2023, 2024], "names": ["가상 인재양성"], "depts": ["가상사업단"]}}]}, ensure_ascii=False),
        encoding="utf-8")


def _seed(app):
    db = app.state.db
    ids = {}
    with db._conn() as conn:
        p = {y: conn.execute("INSERT INTO projects (sector, name, created_at, owner, archived, program) VALUES "
                             "('grant', ?, '2026-01-01', 'zzaimy', 1, ?) RETURNING id",
                             (f"{y}년 가상 인재양성 사업", f"program:gasang|{y}")).fetchone()[0] for y in (2023, 2024)}
    plan = [("plan", 2023, "가상 2023 계획서 초안.pdf"), ("plan", 2023, "가상 2023 계획서 수정.pdf"), ("report", 2023, "가상 2023 결과보고서.pdf"),
            ("minutes", 2023, "가상 2023 회의록.pdf"), ("plan", 2024, "가상 2024 계획서.pdf"), (None, 2024, "가상 2024 메모.pdf")]
    for kind, y, name in plan:
        did = db.add_document(name, "x", doc_type="grant", project_id=p[y])
        db.set_document_kind(did, kind)
        ids.setdefault((y, kind), []).append(did)
    db.add_document("다른 문서.pdf", "x", doc_type="grant")
    return ids


def _ids_in(page: str) -> set[int]:
    table = page.split("data-doc-list>", 1)[-1].split("<!-- /lib-rows -->", 1)[0]
    return {int(x) for x in re.findall(r'href="/doc/(\d+)"', table)}


def test_matrix_counts_by_year_and_kind(tmp_path):
    app, _c = _client(tmp_path)
    ids = _seed(app)
    cond, args = library.rail_sql("prog:program:gasang", "")
    with app.state.db._conn() as conn:
        m = library.matrix(conn, " WHERE " + cond, args, {"plan": "계획서", "report": "결과보고서", "minutes": "회의 자료"})
    rows = {y["tail"]: y for y in m["years"]}
    assert [y["tail"] for y in m["years"]] == ["2024", "2023"]              # 최근 해 먼저
    assert rows["2023"]["cells"] == {"plan": 2, "report": 1, "minutes": 1, "": 0}
    assert rows["2024"]["cells"]["plan"] == 1 and rows["2024"]["cells"][""] == 1 and rows["2024"]["n"] == 2
    assert m["n"] == 6 and m["span"] == (2023, 2024) and m["cols"][0]["kind"] == "plan"
    assert m["key_docs"]["2023"]["plan"] == max(ids[(2023, "plan")])       # 그해 가장 최근 계획서
    assert m["key_docs"]["2023"]["report"] == ids[(2023, "report")][0]


def test_overview_renders_for_selected_program(tmp_path):
    _ledger()
    app, c = _client(tmp_path)
    ids = _seed(app)
    page = c.get("/?type=all&prog=prog:program:gasang").text
    ov = page.split("data-overview", 1)[1].split("</section>", 1)[0]
    assert "가상 인재양성 사업" in ov and "가상인재" in ov                     # 정식 이름·별칭(장부)
    assert "교육부 재정지원" in ov and "2023–2024" in ov and "가상사업단" in ov and "가상 재단" in ov
    assert "2023.03.01~2025.02.28" in ov
    mx = ov.split("data-matrix", 1)[1].split("</table>", 1)[0]
    cell = re.search(r'year=2023&amp;kind=plan#libList" data-cell="plan">(\d+)<', mx)
    assert cell and cell.group(1) == "2"
    assert f'href="/doc/{max(ids[(2023, "plan")])}"' in ov and f'href="/doc/{ids[(2023, "report")][0]}"' in ov
    assert 'aria-label="이 사업 문서에서 찾기"' in ov and 'name="prog" value="prog:program:gasang"' in ov
    # 칸을 누른 목록 — 그 해 그 갈래 문서만, 표는 그대로 사업 전체
    picked = c.get("/?type=all&prog=prog:program:gasang&year=2023&kind=plan").text
    assert _ids_in(picked) == set(ids[(2023, "plan")]) and "data-matrix" in picked
    assert _ids_in(page) == {i for v in ids.values() for i in v}             # 아래 목록은 사업 문서 전체


def test_program_search_matches_alias_and_redirects_when_single(tmp_path):
    _ledger()
    app, c = _client(tmp_path)
    _seed(app)
    r = c.get("/?type=all&pq=가상인재", follow_redirects=False)              # 장부 별칭(띄어쓰기 무시)으로 찾아 하나면 바로 그 사업
    assert r.status_code == 303 and "prog=prog%3Aprogram%3Agasang" in r.headers["location"]
    miss = c.get("/?type=all&pq=없는사업이름").text
    assert "맞는 사업이 없습니다" in miss
    landing = c.get("/?type=all").text
    assert 'id="libFind"' in landing and 'class="lib-pcard"' in landing     # 첫 화면은 사업 찾기와 문서 많은 사업
    assert "가상인재" in landing.split('id="libRail"', 1)[1]                # 사업 목록 거르기·사업 찾기가 별칭으로도 맞춘다


def test_bulk_scope_uses_searchable_department_picker(tmp_path, monkeypatch):
    app, c = _client(tmp_path)
    assert c.post("/login", data={"username": "zzdev", "pw": "devpass"}, follow_redirects=False).status_code == 303   # 공개 범위는 관리자만
    app.state.db.add_document("가상 문서.pdf", "x", doc_type="grant")
    from zzaimy.app import user_admin
    depts = [f"가상부서{i:02d}" for i in range(80)]
    monkeypatch.setattr(user_admin, "known_depts", lambda *a, **k: depts)
    page = c.get("/?type=all").text
    sel = page.split('id="libBulkAct"', 1)[1].split("</select>", 1)[0]
    assert "가상부서00" not in sel and 'value="scope:"' in sel             # 메뉴에는 부서를 늘어놓지 않는다
    picker = page.split('id="libScopePicker"', 1)[1].split("</ul>", 1)[0]
    assert 'id="libScopeFind"' in picker and picker.count('role="option"') == 81   # 전체 공개 + 부서 80
