"""사업 정체가 섞이지 않는가 — 그래프 짓기(이름 바뀜·장부 주인·이름 조각)와 원본 보관소 묶음 이름(노드 맞추기·이름 겹침 점검).

이름은 모두 합성이다. 앞 단계 사업(ABCD+)과 다음 단계 사업(ABCD 3.0), 새 체계(ZETA)가 한 보관소에 있는 꼴을 재현한다."""

import json

import pytest
from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from zzaimy.app import archive, archive_view
from zzaimy.graph import programs as P

OLD = "별빛맞춤형 은하 선도대학(ABCD+) 육성사업"
NEW = "4단계 은하 선도대학 육성사업(ABCD 3.0)"


def test_rename_hits_skip_word_keys_and_keep_identities():
    title = "누리 인재체계(앵두) 추진방안(별빛중심 대학지원체계(ZETA) 재구조화) 안내"
    keys = ["n:재구조화", "n:인재체계", "n:대학지원체계", "n:별빛중심대학지원체계", "a:ZETA", "a:ZET", "a:앵두",
            "n:ZETA)", "n:추진방안"]
    got = set(P.rename_hits(title, keys))
    assert got == {"n:별빛중심대학지원체계", "a:ZETA", "a:앵두"}


def test_rename_title_does_not_pull_in_a_program_by_a_stray_name():
    """다른 사업 묶음에 섞여 든 이름 하나가 이름 바뀜 제목에 맞아도 그 묶음을 끌어들이지 않는다(정체 약칭으로만 센다)."""
    docs = [{"id": i, "filename": f"은하 선도대학(ABCD) 육성사업 결과보고서 {i}.hwp", "path": "", "head": ""} for i in range(6)]
    docs += [{"id": 10 + i, "filename": f"대구 ZETA사업 운영 지침 {i}.hwp", "path": "", "head": ""} for i in range(6)]
    docs += [{"id": 30, "filename": "은하혁신융합대학사업(ABCD) 협의.hwp", "path": "", "head": ""},
             {"id": 31, "filename": "누리체계(現 ZETA) 은하혁신융합대학 안내.hwp", "path": "", "head": ""}]
    cards = P.build_cards(docs)
    abcd = [c for c in cards if "ABCD" in c.acrs]
    zeta = [c for c in cards if "ZETA" in c.acrs]
    assert abcd and zeta and abcd[0] is not zeta[0]
    assert "ZETA" not in abcd[0].acrs and zeta[0].renamed == []


def test_clean_label_drops_cut_heads_and_masked_years():
    assert P.clean_label(") 4단계 은하 선도대학 육성사업") == "4단계 은하 선도대학 육성사업"
    assert P.clean_label("07) 4단계 은하 선도대학 육성사업") == "4단계 은하 선도대학 육성사업"
    assert P.clean_label("0000년 별빛맞춤형 은하 선도대학 육성사업") == "별빛맞춤형 은하 선도대학 육성사업"
    assert P.clean_label("+ (신규) 별빛 시범사업") == "(신규) 별빛 시범사업"
    assert P.clean_label("4단계 은하 선도대학 육성사업") == "4단계 은하 선도대학 육성사업"


def test_ledger_owner_follows_card_identity_over_longer_older_name():
    """다음 단계 카드(program:abcd30)에 앞 단계 긴 이름이 섞여 더 길게 맞아도 카드 id 약칭이 가리키는 항목의 주인이 된다."""

    class C:
        def __init__(self, node_id, surfaces):
            self.node_id, self._s = node_id, surfaces

        def surfaces(self):
            return set(self._s)

    cards = [C("program:abcd30", ["ABCD3.0", "4단계은하선도대학육성사업", "별빛맞춤형은하선도대학육성사업"]),
             C("program:abcd", ["ABCD+", "별빛맞춤형은하선도대학육성사업"]),
             C("program:abcd3", ["ABCD3"])]
    ledger = {"programs": [
        {"terms": ["ABCD+", "별빛맞춤형 은하 선도대학 육성사업"], "name": OLD, "period": "2015~2020", "sources": ["s"]},
        {"terms": ["ABCD 3.0", "4단계 은하 선도대학 육성사업"], "name": NEW, "period": "2021~2026", "sources": ["s"]}]}
    got = P.ledger_link(cards, ledger)
    assert got["display"]["program:abcd30"] == NEW and got["display"]["program:abcd"] == OLD
    assert got["aliases"] == {"program:abcd3": "program:abcd30"}
    assert got["periods"]["program:abcd30"] == (2021, 2026)


@pytest.fixture
def client(tmp_path):
    app = _app(tmp_path)
    c = TestClient(app)
    assert _login(c, "zzdev", "devpass")
    return c


def _rows(key, name, n):
    return [{"rel": f"{key[8:]}/{i}.hwp", "size": 1, "mtime": 1, "ext": "hwp", "program": key, "program_name": name} for i in range(n)]


def _node(db, nid, label, names=(), acronyms=()):
    from zzaimy.graph import kg_store
    kg_store.ensure(db)
    with db._conn() as conn:
        conn.execute("INSERT INTO kg_nodes (id, type, label, props) VALUES (?, 'program', ?, ?)",
                     (nid, label, json.dumps({"names": list(names), "acronyms": list(acronyms)})))


def test_archive_groups_never_share_a_shown_name_after_a_polluted_rebuild(client):
    """다시 지은 그래프에서 다음 단계 노드(program:abcd30)의 이름이 앞 단계 이름으로 바뀌고, 이름 목록에 조각과 남의 이름이
    섞여도 — 장부 열쇠마다 제 사업 이름이 보이고 두 묶음이 같은 이름을 갖지 않는다."""
    db = client.app.state.db
    archive.load(db, _rows("program:abcd", "별빛맞춤형 은하 선도대학(ABCD+)육성사업", 40)
                 + _rows("program:abcd30", "4단계 은하 선도대학 육성사업", 30)
                 + _rows("program:기술", "누리기술사관육성사업", 5))
    _node(db, "program:abcd30", OLD,
          names=[") 4단계 은하 선도대학 육성사업", "0000년 " + OLD, "4단계 은하 선도대학 육성사업", "누리기술사관육성사업", OLD],
          acronyms=["ABCD+", "ABCD 3.0", "ZETA", "ABCE+"])
    _node(db, "program:abcd3", NEW, names=["4단계 은하 선도대학 육성사업"], acronyms=["ABCD 3.0"])
    names, progs = archive_view.load_programs(db, set())
    assert progs.name_clashes() == []
    assert progs.label("program:abcd") == OLD
    assert progs.label("program:abcd30") == NEW
    assert progs.label("program:기술") == "누리기술사관육성사업"
    page = client.get("/archive").text
    table = page[page.index('aria-label="사업별 원본 수"'):]
    assert table.count(f">{OLD}</a>") == 1 and table.count(f">{NEW}</a>") == 1


def test_graph_match_trusts_node_id_only_when_names_agree(client):
    db = client.app.state.db
    _node(db, "program:abcd30", OLD, names=["4단계 은하 선도대학 육성사업"])
    _node(db, "program:abcd3", NEW, names=["4단계 은하 선도대학 육성사업"])
    _node(db, "program:zeta", "누리 인재체계(구 ZETA)", acronyms=["ZETA"])
    names = {"program:abcd30": ("4단계 은하 선도대학 육성사업", 3), "program:zeta": ("ZETA사업", 2),
             "program:abcd": ("별빛맞춤형 은하 선도대학(ABCD+)육성사업", 4)}
    _surf, label = archive_view.graph_match(db, names)
    assert label["program:abcd30"] == NEW                          # id 는 같지만 노드 이름이 다른 사업 — 이름이 같은 노드로
    assert label["program:zeta"] == "누리 인재체계(구 ZETA)"        # 옛 약칭 이름 ↔ 「(구 ZETA)」
    assert label["program:abcd"] == OLD                            # 노드 이름 일치(띄어쓰기만 다름)


def test_same_program_names():
    sp = archive_view.same_program
    assert sp("별빛맞춤형 은하 선도대학(ABCD+)육성사업", OLD)
    assert not sp("은하 선도대학(ABCD) 육성사업", "은하 선도대학(ABCD+) 육성사업")
    assert sp("4단계 은하 선도대학 육성사업", NEW) and sp("ABCD3.0 사업", NEW)
    assert not sp("4단계 은하 선도대학 육성사업", OLD)


def test_out_of_period_doc_moves_to_ledger_lineage_program():
    """다음 단계 사업 폴더의 앞 단계 시절 문서 — 장부의 앞 단계(predecessor)가 그 연도를 품으면 그 사업으로, 아니면 검토로 남긴다."""

    class C:
        def __init__(self, node_id, surfaces):
            self.node_id, self._s, self.name = node_id, surfaces, surfaces[0]

        def surfaces(self):
            return set(self._s)

    cards = [C("program:abcd30", ["ABCD3.0", "4단계은하선도대학육성사업"]), C("program:abcd", ["ABCD+", "별빛맞춤형은하선도대학육성사업"])]
    ledger = {"programs": [
        {"terms": ["ABCD+", "별빛맞춤형 은하 선도대학 육성사업"], "name": OLD, "period": "2015~2020", "sources": ["s"]},
        {"terms": ["ABCD 3.0", "4단계 은하 선도대학 육성사업"], "name": NEW, "period": "2021~2026", "sources": ["s"],
         "predecessor": ["ABCD+"]}]}
    link = P.ledger_link(cards, ledger)
    assert {sp["id"]: sp["lineage"] for sp in link["spans"]} == {"program:abcd": ["program:abcd30"], "program:abcd30": ["program:abcd"]}
    docs = [{"id": 1, "filename": "협의체 회의(2019.05.02).pdf", "path": "사업단/ABCD3.0 자료/협의체"},
            {"id": 2, "filename": "기술자문(2011).pdf", "path": "사업단/ABCD3.0 자료/기술자문"}]
    res = [P.Assignment(doc_id=1, program="program:abcd30", program_name=NEW, status="agent", year=2019),
           P.Assignment(doc_id=2, program="program:abcd30", program_name=NEW, status="agent", year=2011)]
    st = P.fill_period(docs, res, link["periods"], link["spans"], link["aliases"])
    assert (res[0].program, res[0].status, res[0].round) == ("program:abcd", "period", 5)
    assert "앞뒤 단계" in res[0].evidence[-1]
    assert (res[1].program, res[1].status) == ("", "review")          # 어느 단계 기간에도 없는 연도
    assert st["moved_to_lineage"] == 1
