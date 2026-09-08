"""데이터 공방 — 기록→학습 쌍 변환과 수치 정제 규칙 검증."""

from __future__ import annotations

import json

import pytest

from zzaimy.app.db import Database
from zzaimy.dataset.build import (
    build_chat_pairs,
    build_review_pairs,
    export_dataset,
    rag_status,
)

BODY = "사업 예산은 300,000천원이며 참여 인원은 25명이다. " + "실적 상세 내용. " * 10
GOOD_REVIEW = "예산 300,000천원과 인원 25명 기재를 확인했다. 형식 적합. " + "보완점 없음. " * 5
BAD_REVIEW = "예산이 500,000천원으로 기재되어 있어 확인이 필요하다. " + "보완 요망. " * 5


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # SFT_DIR(data/interim/sft)이 임시 폴더에 생기게
    return Database(tmp_path / "ds.db")


def test_review_pairs_keep_grounded_numbers_only(db):
    """출력 수치가 근거에 있으면 통과, 없으면 폐기 — 정제 규칙 그대로."""
    d1 = db.add_document("a.pdf", "/x", doc_type="grant")
    db.update_document(d1, status="reviewed", masked_text=BODY, ai_review=GOOD_REVIEW)
    d2 = db.add_document("b.pdf", "/x", doc_type="grant")
    db.update_document(d2, status="reviewed", masked_text=BODY, ai_review=BAD_REVIEW)

    out = build_review_pairs(db)
    assert len(out.pairs) == 1
    assert out.n_dropped_numbers == 1
    assert out.pairs[0]["meta"]["doc_id"] == d1
    # 형태: 지시+근거(human) → 산출(gpt)
    conv = out.pairs[0]["conversations"]
    assert conv[0]["from"] == "human" and "[접수 문서]" in conv[0]["value"]
    assert conv[1]["from"] == "gpt"


def test_regulation_docs_are_not_training_pairs(db):
    """기준 문서는 지식 공급원이지 학습 쌍 재료가 아니다."""
    r = db.add_document("학칙.pdf", "/x", doc_type="regulation")
    db.update_document(r, status="reviewed", masked_text=BODY, ai_review=GOOD_REVIEW)
    assert build_review_pairs(db).pairs == []


def test_chat_pairs_are_multiturn(db):
    s = db.create_chat_session("휴학 문의")
    db.add_chat(s, "user", "일반휴학 처리 절차를 알려줘. 서류는 뭐가 필요하지?")
    db.add_chat(s, "assistant", "일반휴학은 신청서 접수 후 지도교수 확인을 거칩니다. " * 3)
    db.add_chat(s, "user", "복학은?")
    db.add_chat(s, "assistant", "복학은 개강 전 복학원을 제출하면 됩니다. " * 3)

    out = build_chat_pairs(db)
    assert len(out.pairs) == 1
    conv = out.pairs[0]["conversations"]
    assert [t["from"] for t in conv] == ["human", "gpt", "human", "gpt"]


def test_export_writes_jsonl_and_ledger(db):
    d = db.add_document("a.pdf", "/x", doc_type="grant")
    db.update_document(d, status="reviewed", masked_text=BODY, ai_review=GOOD_REVIEW)

    ds = export_dataset(db, ["review", "draft"], "smoke")
    assert ds["n_pairs"] == 1
    lines = open(ds["path"], encoding="utf-8").read().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["conversations"][1]["from"] == "gpt"
    assert db.list_datasets()[0]["id"] == ds["id"]


def test_export_rejects_empty_sources(db):
    with pytest.raises(ValueError):
        export_dataset(db, ["nonsense"], "x")


def test_rag_status_counts_chunks(db):
    d = db.add_document("a.pdf", "/x", doc_type="grant")
    db.replace_doc_chunks(d, [{"kind": "text", "page_no": 1, "content": "본문"}])
    rows = rag_status(db)
    assert rows[0]["n_chunks"] == 1
