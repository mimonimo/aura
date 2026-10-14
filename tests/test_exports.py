"""산출물 내려받기(/dev/export) — 관리자만, 없는 산출물은 「아직 없음」, CSV 머리줄·행, RAG 목록에 본문 없음, 그래프 거르기."""
import csv
import gzip
import io
import json

import pytest
from fastapi.testclient import TestClient
from tests.test_app import FakeDrafter, FakeProcessor

from zzaimy.app import exports
from zzaimy.app.main import create_app
from zzaimy.graph import kg_store

SECRET = "주민번호900101-1234567 비밀 본문"


def _app(tmp_path):
    (tmp_path / "accounts.json").write_text(json.dumps({
        "zzaimy": {"pw": "staffpass", "role": "staff"},
        "zzdev": {"pw": "devpass", "role": "dev"},
    }))
    return create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                      processor=FakeProcessor(), drafter=FakeDrafter(), password="boot-pass-1")


def _login(c, uid, pw):
    r = c.post("/login", data={"username": uid, "pw": pw}, follow_redirects=False)
    assert r.status_code == 303


@pytest.fixture
def dev(tmp_path, monkeypatch):
    monkeypatch.setattr(exports, "_index_ids", lambda: None)
    app = _app(tmp_path)
    c = TestClient(app)
    _login(c, "zzdev", "devpass")
    return c, app.state.db, tmp_path


def _seed_graph(db):
    kg_store.ensure(db)
    with db._conn() as conn:
        kg_store.put_node(conn, "program:A", "program", "산학연 협력 선도전문대학 육성사업", {"names": ["가"]})
        kg_store.put_node(conn, "program:B", "program", "나 사업")
        kg_store.put_node(conn, "doc:1", "doc", "문서 하나", {"kind": "plan"}, doc_id=1)
        kg_store.put_edge(conn, "program:A", "doc:1", "contains", "분류", ["폴더"])
        kg_store.put_edge(conn, "program:A", "program:B", "related", "식별자 일치", ["같은 과제명"], 0.5)


def test_non_admin_is_blocked(tmp_path):
    c = TestClient(_app(tmp_path))
    _login(c, "zzaimy", "staffpass")
    for path in ("/dev/export", "/dev/export/kg_nodes", "/dev/export/rag_docs", "/dev/export/ontology"):
        assert c.get(path).status_code == 403
    assert 'id="gxExportModal"' not in c.get("/graph/explore").text


def test_missing_outputs_show_not_yet(dev):
    c, _db, _tmp = dev
    page = c.get("/dev/export?groups=ontology,graph").text
    for key in ("ontology", "instances", "shacl_report", "kg_nodes", "kg_edges"):
        row = page.split(f'data-key="{key}"')[1].split("data-key=")[0]
        assert "아직 없음" in row and "disabled" in row
    # 검사 규칙은 원본(src/zzaimy/graph/shapes.ttl)이 있어 늘 받을 수 있다
    assert 'href="/dev/export/shapes"' in page
    assert c.get("/dev/export/ontology").status_code == 404
    assert c.get("/dev/export/nope").status_code == 404
    # 그래프 탐색 화면의 내려받기 창
    g = c.get("/graph/explore").text
    assert 'id="gxExportModal"' in g and 'data-export-groups="ontology,graph"' in g


def test_ontology_file_is_served_with_size(dev):
    c, _db, tmp = dev
    (tmp / "ontology").mkdir()
    (tmp / "ontology" / "ontology.ttl").write_text("@prefix z: <urn:z#> .\n", encoding="utf-8")
    page = c.get("/dev/export?groups=ontology").text
    assert 'href="/dev/export/ontology"' in page
    r = c.get("/dev/export/ontology")
    assert r.status_code == 200 and r.text.startswith("@prefix") and "attachment" in r.headers["content-disposition"]


def test_graph_csv_header_rows_and_filters(dev, monkeypatch):
    c, db, _tmp = dev
    monkeypatch.setattr(exports, "PAGE", 1)          # 쪽 넘김(keyset)도 거친다
    _seed_graph(db)
    assert 'href="/dev/export/kg_nodes"' in c.get("/dev/export?groups=graph").text

    def rows(path):
        r = c.get(path)
        assert r.status_code == 200 and r.headers["content-type"] == "application/gzip"
        return list(csv.reader(io.StringIO(gzip.decompress(r.content).decode("utf-8"))))

    nodes = rows("/dev/export/kg_nodes")
    assert nodes[0] == ["id", "type", "label", "props", "doc_id"]
    assert sorted(r[0] for r in nodes[1:]) == ["doc:1", "program:A", "program:B"]
    assert [r[0] for r in rows("/dev/export/kg_nodes?type=program")[1:]] == ["program:A", "program:B"]
    edges = rows("/dev/export/kg_edges")
    assert edges[0] == ["src", "dst", "kind", "basis", "evidence", "weight"] and len(edges) == 3
    only = rows("/dev/export/kg_edges?kind=related")
    assert len(only) == 2 and only[1][:3] == ["program:A", "program:B", "related"]
    # 사업 목록 — 문서함에 있는 문서가 걸린 사업만, 장부 분류 칸과 함께
    did = db.add_document("가 사업 계획서.pdf", "unused", doc_type="grant")
    with db._conn() as conn:
        kg_store.put_node(conn, f"doc:{did}", "doc", "가 사업 계획서", {}, doc_id=did)
        kg_store.put_edge(conn, "program:A", f"doc:{did}", "contains", "분류", ["폴더"])
    from zzaimy.app import kg_explore
    kg_explore._cache.clear()
    prog = list(csv.reader(io.StringIO(c.get("/dev/export/programs").content.decode("utf-8-sig"))))
    assert prog[0] == exports._PROG_HEAD and any(r[5] == "program:A" and r[6] == "1" for r in prog[1:])


def test_rag_exports_have_no_body_text(dev):
    c, db, _tmp = dev
    did = db.add_document("학사 규정.txt", "unused", doc_type="regulation")
    with db._conn() as conn:
        conn.execute("UPDATE documents SET status='reviewed', masked_text=?, dept='교무처' WHERE id=?", (SECRET, did))
        conn.execute("INSERT INTO regulation_chunks (doc_id, reg_title, heading, content) VALUES (?, ?, ?, ?)",
                     (did, "학사 규정", "제1조", SECRET))
    db.append_doc_chunks(did, [{"kind": "text", "content": SECRET}])
    page = c.get("/dev/export?groups=rag,train").text
    assert 'href="/dev/export/rag_docs"' in page and "학습 데이터(승인본)" in page

    r = c.get("/dev/export/rag_docs")
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    text = r.content.decode("utf-8-sig")
    assert "비밀" not in text and "900101" not in text
    got = list(csv.reader(io.StringIO(text)))
    assert got[0] == exports._DOC_HEAD
    assert got[1][0] == str(did) and got[1][5] == "교무처" and got[1][8] == "1" and got[1][9] == "1"

    text = c.get("/dev/export/regulations").content.decode("utf-8-sig")
    assert "비밀" not in text
    got = list(csv.reader(io.StringIO(text)))
    assert got[0] == exports._REG_HEAD and got[1][1] == "학사 규정" and got[1][4] == "아니오" and got[1][5] == "1"


def test_download_is_logged(dev, caplog):
    c, db, _tmp = dev
    _seed_graph(db)
    with caplog.at_level("INFO", logger="zzaimy.app.exports"):
        c.get("/dev/export/kg_edges?kind=related")
    assert any("key=kg_edges" in m and "zzdev" in m and "related" in m for m in caplog.messages)
