"""규정 검색 품질 측정 — 지표 산식·정답 본문 재결선·산출물 형태·대시보드 빈 상태.

모델(KURE·bge·Kiwi)은 쓰지 않는다 — 랭커는 가짜를 주입하고 산식·재결선·형태만 검증한다.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from tests.test_app import FakeDrafter, FakeProcessor

from zzaimy.app.main import create_app
from zzaimy.eval import retrieval_eval as rev


def _chunk(cid: int, content: str, title: str = "합성규정", heading: str = "제1조") -> dict:
    return {"id": cid, "doc_id": 1, "reg_title": title, "heading": heading,
            "content": content, "sector": "common", "dept": "공통"}


# 재결선 시험용 본문 — 소재를 달리해 서로 겹치는 글자 n-그램이 없게 한다
TEXT_A = ("제3조(회피) 평가위원은 이해관계가 있는 대학의 평가를 회피하여야 하며, "
          "회피 사유가 생기면 지체 없이 위원장에게 알려야 한다. ") * 3
TEXT_B = ("제7조(정산) 사업비는 회계연도 종료 후 30일 이내에 정산하고 집행 잔액은 "
          "전액 반납한다. 정산 보고서에는 증빙 서류를 첨부한다. ") * 3
TEXT_C = "제12조(휴학) 일반휴학은 학기 단위로 신청하며 통산 4학기를 초과할 수 없다. " * 3


# ---------------------------------------------------------------- 지표 산식

def test_metrics_on_tiny_ranking():
    runs = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
    golds = [{1}, {6}, {99}]  # 1위 · 3위 · 순위 밖
    m = rev.metrics(runs, golds)
    assert m["recall_at_1"] == pytest.approx(1 / 3, abs=1e-4)
    assert m["recall_at_5"] == pytest.approx(2 / 3, abs=1e-4)
    assert m["recall_at_10"] == pytest.approx(2 / 3, abs=1e-4)
    assert m["mrr_at_10"] == pytest.approx((1 + 1 / 3) / 3, abs=1e-4)


def test_metrics_cut_at_top_k():
    m = rev.metrics([list(range(1, 15))], [{12}])  # 정답이 12위 → @10 밖
    assert m["recall_at_10"] == 0.0 and m["mrr_at_10"] == 0.0
    assert rev.metrics([[12]], [{12}])["recall_at_1"] == 1.0


# ---------------------------------------------------------------- 정답 본문 재결선

def test_gold_remap_exact_id_then_content():
    m = rev.ChunkMatcher([_chunk(101, TEXT_C), _chunk(102, TEXT_A)])
    assert m.resolve(102, TEXT_A) == {102}  # id·본문 일치
    assert m.resolve(7, TEXT_A) == {102}  # id 소실 → 본문으로 되찾는다
    assert m.resolve(101, TEXT_A) == {102}  # id가 다른 조각을 가리켜도 본문이 이긴다


def test_gold_remap_merge_split_loss():
    # 병합: 옛 정답 A가 새 조각 A+B 안에 들어감
    m = rev.ChunkMatcher([_chunk(201, TEXT_A + " " + TEXT_B), _chunk(202, TEXT_C)])
    assert m.resolve(1, TEXT_A) == {201}
    # 분할: 옛 정답 A+B가 새 조각 A, B로 나뉨 → 둘 다 정답
    m = rev.ChunkMatcher([_chunk(301, TEXT_A), _chunk(302, TEXT_B), _chunk(303, TEXT_C)])
    assert m.resolve(1, TEXT_A + " " + TEXT_B) == {301, 302}
    # 소실: 본문이 어디에도 없으면 빈 집합 — 살아 있는 id도 믿지 않는다
    m = rev.ChunkMatcher([_chunk(1, TEXT_C)])
    assert m.resolve(1, TEXT_A) == set()
    # 본문 없는 옛 행: id가 살아 있으면 그대로, 없으면 제외
    assert m.resolve(1, None) == {1}
    assert m.resolve(9, None) == set()


def test_resolve_golds_uses_snapshot_for_old_rows():
    rows = [
        {"chunk_id": 5, "practical": "q1"},  # 본문 없음 → 재분할 전 스냅샷으로
        {"chunk_id": 6, "gold_text": TEXT_B, "practical": "q2"},  # 본문 있음
        {"chunk_id": 7, "practical": "q3"},  # 스냅샷에도 없고 id도 죽음 → 제외
    ]
    snapshot = {5: _chunk(5, TEXT_A)}  # 75_rechunk가 남기는 목록과 같은 형태
    matcher = rev.ChunkMatcher([_chunk(31, TEXT_A), _chunk(32, TEXT_B)])
    golds, stats = rev.resolve_golds(rows, matcher, snapshot)
    assert golds == [{31}, {32}, set()]
    assert stats["snapshot"] == 1 and stats["gold_text"] == 1 and stats["chunk_id"] == 1
    assert stats["remapped"] == 2 and stats["unmapped"] == 1


def test_load_snapshot_gz(tmp_path):
    import gzip

    p = tmp_path / "regulation_chunks-20260914-120000.json.gz"
    with gzip.open(p, "wt", encoding="utf-8") as f:
        json.dump([_chunk(5, TEXT_A)], f, ensure_ascii=False)
    assert rev.load_snapshot(p)[5]["content"] == TEXT_A
    assert rev.latest_snapshot(tmp_path) == p
    assert rev.latest_snapshot(tmp_path / "없음") is None


def test_backfill_gold_text_is_idempotent(tmp_path):
    p = tmp_path / "synth_queries.jsonl"
    p.write_text("\n".join([
        json.dumps({"chunk_id": 1, "practical": "q", "requirement": "", "keyword": "k"}),
        json.dumps({"chunk_id": 99, "practical": "없는 조각"}),
    ]) + "\n", encoding="utf-8")
    src = {1: _chunk(1, "  제1조  본문\n\n둘째 줄 ", heading="제1조")}
    stats = rev.backfill_gold_text(p, src)
    assert stats == {"rows": 2, "filled": 1, "missing": 1, "already": 0}
    rows = rev.load_rows(p)
    assert rows[0]["gold_text"] == "제1조 본문 둘째 줄"
    assert rows[0]["gold_title"] == "합성규정" and rows[0]["gold_heading"] == "제1조"
    assert "gold_text" not in rows[1]  # 출처에 없는 id는 건드리지 않고 센다
    assert list(tmp_path.glob("synth_queries.jsonl.bak-*"))  # 원본 보관
    again = rev.backfill_gold_text(p, src)
    assert again["filled"] == 0 and again["already"] == 1


# ---------------------------------------------------------------- 산출물 형태

def _fake_retrievers(with_rerank: bool = True) -> rev.Retrievers:
    """질의 "q<id>"의 정답은 <id>. 어휘는 정답을 1위, 임베딩은 2위, 하이브리드는 3위에 두고
    리랭커가 다시 1위로 올린다 — 행별 수치가 서로 달라야 표가 검증된다."""

    def gold_of(q: str) -> int:
        return int(q[1:])

    def lexical(q):
        g = gold_of(q)
        return [g, g + 100, g + 200]

    def dense(q):
        g = gold_of(q)
        return [g + 100, g, g + 200]

    def hybrid(q, lex, den):
        g = gold_of(q)
        return [g + 100, g + 200, g]

    def rerank(q, cands):
        g = gold_of(q)
        return [g] + [c for c in cands if c != g]

    return rev.Retrievers(
        lexical, dense, hybrid, rerank if with_rerank else None,
        meta={"embedding_model": "fake-embed", "rerank_model": "fake-rerank",
              "embedding_active": True},
    )


def _write_queries(path, n: int = 4) -> None:
    lines = [
        json.dumps({"chunk_id": i, "gold_text": f"조각 {i} 본문 " * 5,
                    "practical": f"q{i}", "requirement": "", "keyword": f"q{i}"},
                   ensure_ascii=False)
        for i in range(1, n + 1)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_run_eval_writes_artifact_shape(tmp_path):
    chunks = [_chunk(i, f"조각 {i} 본문 " * 5) for i in range(1, 5)]
    qp = tmp_path / "synth_queries.jsonl"
    _write_queries(qp)
    eval_dir = tmp_path / "eval"
    result = rev.run_eval(
        None, queries_path=qp, eval_dir=eval_dir, retrievers=_fake_retrievers(),
        chunks=chunks, rerank_sample=3, seed=1, write_md=tmp_path / "mini.md", log=None,
    )
    latest = json.loads((eval_dir / "retrieval-latest.json").read_text(encoding="utf-8"))
    assert latest == result
    for key in ("schema", "measured_at", "n_queries", "n_chunks", "embedding_model",
                "rerank_model", "rows", "notes"):
        assert key in latest
    assert latest["n_queries"] == 8 and latest["n_chunks"] == 4  # 행 4 × (실무형+키워드)
    assert latest["embedding_model"] == "fake-embed"
    assert [r["method"] for r in latest["rows"]] == [
        rev.METHOD_LEXICAL, rev.METHOD_DENSE, rev.METHOD_HYBRID, rev.METHOD_PRODUCTION]
    lex, den, hyb, prod = latest["rows"]
    assert lex["recall_at_1"] == 1.0 and lex["n"] == 8 and lex["production"] is False
    assert den["recall_at_1"] == 0.0 and den["recall_at_5"] == 1.0 and den["mrr_at_10"] == 0.5
    assert hyb["recall_at_1"] == 0.0 and hyb["mrr_at_10"] == pytest.approx(1 / 3, abs=1e-4)
    assert prod["production"] is True and prod["n"] == 3 and prod["recall_at_1"] == 1.0
    assert prod["sample"] == {"seed": 1, "of": 8}
    assert prod["hybrid_on_sample"]["recall_at_1"] == 0.0  # 같은 표본의 리랭크 전 수치
    assert rev.SELF_RETRIEVAL_NOTE in latest["notes"]
    assert list(eval_dir.glob("retrieval-2*.json"))  # 날짜본
    md = (tmp_path / "mini.md").read_text(encoding="utf-8")
    assert "| 어휘(Kiwi) | 1.000 |" in md and "(운영 구성)" in md


def test_run_eval_marks_production_unmeasured_without_reranker(tmp_path):
    chunks = [_chunk(i, f"조각 {i} 본문 " * 5) for i in range(1, 5)]
    qp = tmp_path / "q.jsonl"
    _write_queries(qp)
    r1 = rev.run_eval(None, queries_path=qp, eval_dir=tmp_path / "eval",
                      retrievers=_fake_retrievers(with_rerank=False), chunks=chunks,
                      write_md=None, log=None)
    prod = r1["rows"][-1]
    assert prod["production"] is True and prod["recall_at_1"] is None
    assert prod["note"] == "리랭커 미적재"  # 하이브리드 순서를 리랭크 수치로 적지 않는다
    r2 = rev.run_eval(None, queries_path=qp, eval_dir=tmp_path / "eval",
                      retrievers=_fake_retrievers(), chunks=chunks, no_rerank=True,
                      write_md=None, log=None)
    assert r2["rows"][-1]["recall_at_1"] is None and "--no-rerank" in r2["rows"][-1]["note"]


def test_run_eval_refuses_without_query_set(tmp_path):
    with pytest.raises(FileNotFoundError, match="51_synth_queries"):
        rev.run_eval(None, queries_path=tmp_path / "없음.jsonl", eval_dir=tmp_path / "eval",
                     retrievers=_fake_retrievers(), chunks=[_chunk(1, TEXT_A)], log=None)
    assert not (tmp_path / "eval").exists()  # 산출물도 만들지 않는다


def test_dashboard_state_without_artifact(tmp_path):
    st = rev.dashboard_state(tmp_path / "eval", queries_path=tmp_path / "없음.jsonl",
                             log_path=tmp_path / "eval.log")
    assert st["result"] is None and st["running"] is None
    assert st["query_set_missing"] and "51_synth_queries" in st["query_set_message"]
    # 죽은 프로세스의 .running 잔재는 실행 중으로 보지 않는다
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / rev.RUNNING_NAME).write_text(
        json.dumps({"pid": 2 ** 22 + 12345, "started_at": "2026-09-14T10:00:00+09:00"}))
    assert rev.dashboard_state(tmp_path / "eval", queries_path=tmp_path / "없음.jsonl",
                               log_path=tmp_path / "eval.log")["running"] is None


# ---------------------------------------------------------------- 대시보드

def _client(tmp_path) -> TestClient:
    app = create_app(
        db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(),
    )
    return TestClient(app)


def test_dev_shows_no_measurement_without_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(rev, "QUERIES_PATH", tmp_path / "없는_질의세트.jsonl")
    monkeypatch.setattr(rev, "LOG_PATH", tmp_path / "eval.log")
    c = _client(tmp_path)
    r = c.get("/dev/quality")
    assert r.status_code == 200
    assert "규정 검색 품질" in r.text
    assert "아직 측정 없음" in r.text and "51_synth_queries" in r.text
    assert "0.583" not in r.text and "한 번에 정답" not in r.text  # 손으로 쓴 수치·표현 제거
    assert "embed-v0-report.md" in c.get("/dev").text  # 리허설 결과는 대시보드 모델 트랙 표에서만 링크
    # 질의 세트가 없으면 재측정은 깨끗이 거절 — 실행도, 가짜 수치도 없다
    r2 = c.post("/dev/eval/run", follow_redirects=False)
    assert r2.status_code == 409 and "51_synth_queries" in r2.text
    assert not (tmp_path / "eval").exists()


def test_dev_renders_artifact_table(tmp_path, monkeypatch):
    monkeypatch.setattr(rev, "QUERIES_PATH", tmp_path / "없음.jsonl")
    monkeypatch.setattr(rev, "LOG_PATH", tmp_path / "eval.log")
    eval_dir = tmp_path / "eval"
    eval_dir.mkdir()
    (eval_dir / "retrieval-latest.json").write_text(json.dumps({
        "schema": 1, "measured_at": "2026-09-14T10:00:00+09:00",
        "n_queries": 8, "n_chunks": 4, "embedding_model": "fake-embed",
        "rows": [
            {"method": "어휘(Kiwi)", "production": False, "n": 8, "recall_at_1": 0.5,
             "recall_at_5": 0.75, "recall_at_10": 1.0, "mrr_at_10": 0.625, "note": None},
            {"method": "하이브리드+리랭커", "production": True, "n": 0, "recall_at_1": None,
             "recall_at_5": None, "recall_at_10": None, "mrr_at_10": None,
             "note": "리랭커 미적재"},
        ],
        "notes": [rev.SELF_RETRIEVAL_NOTE],
    }, ensure_ascii=False), encoding="utf-8")
    r = _client(tmp_path).get("/dev/quality")
    assert r.status_code == 200
    assert "0.500" in r.text and "0.625" in r.text and "운영 구성" in r.text
    assert "미측정" in r.text and "리랭커 미적재" in r.text
    assert "2026-09-14 10:00" in r.text and rev.SELF_RETRIEVAL_NOTE in r.text
    assert "재측정 불가" in r.text  # 질의 세트가 없으면 버튼 대신 사유
