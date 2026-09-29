"""근거 기록 — 입력 줄을 실제 조각에 잇고, 못 찾은 줄은 기록하지 않는다. 품질 관문과 함께 돈다."""

import json

from zzaimy.dataset import provenance as pv
from zzaimy.dataset.quality_gate import audit_pair


def _sources():
    table = json.dumps({"n_rows": 2, "n_cols": 2, "cells": [[0, 0, 1, 1, 0, "지표명"], [0, 1, 1, 1, 0, "기준값"], [1, 0, 1, 1, 0, "AI 기초 교육과정\n이수율"], [1, 1, 1, 1, 0, "4.6"]]}, ensure_ascii=False)
    return (pv.Sources()
            .add("form", [{"id": 11, "doc_id": 557, "content": "1.1. 대학의 여건 분석\n【작성방법】 1) 지역 동향을 기술"}])
            .add("done", [{"id": 21, "doc_id": 562, "content": "산학협력처는 새싹멘토링을 운영한다."}, {"id": 22, "doc_id": 562, "content": table}])
            .add("criteria", [{"id": 31, "doc_id": 564, "content": "평가 착안점 | 지역 동향을 구체적으로 제시하였는가"}], prefix="reg:"))


def test_records_link_lines_to_chunks_and_skip_unknown_lines():
    human = ("규칙 글은 근거가 아니다\n\n[이 절의 양식 안내·작성방법]\n지역 동향을 기술\n\n[평가지표·공고·기본계획 근거]\n- (평가편람) 평가 착안점 | 지역 동향을 구체적으로 제시하였는가\n\n"
             "[문서함의 지난 사업 자료 — 이 절과 관련된 사실 목록]\n- 산학협력처는 새싹멘토링을 운영한다.\n- AI 기초 교육과정 이수율 · 기준값: 4.6\n- 어디에도 없는 지어낸 줄 99건\n\n[담당자 지시]\n써 줘")
    recs = pv.records_for(human, _sources(), "docset:557:562")
    got = {(r["doc_id"], r["chunk_id"]): r["text"] for r in recs}
    assert got[(557, "11")] == "지역 동향을 기술"
    assert got[(564, "reg:31")].startswith("- (평가편람) 평가 착안점")
    assert got[(562, "21")] == "- 산학협력처는 새싹멘토링을 운영한다."
    assert got[(562, "22")] == "- AI 기초 교육과정 이수율 · 기준값: 4.6"          # 표 조각(JSON)의 칸 글로 찾는다
    assert not any("지어낸" in r["text"] for r in recs) and not any("규칙 글" in r["text"] for r in recs)
    assert all(r["text"] in human and r["turn"] == 0 for r in recs)


def test_attached_pair_passes_gate_except_review_and_holds_unsupported_numbers():
    human = "[문서함의 지난 사업 자료 — 이 절과 관련된 사실 목록]\n- AI 기초 교육과정 이수율 · 기준값: 4.6\n\n[질문] 기준값은?"
    pair = {"conversations": [{"from": "human", "value": human}, {"from": "gpt", "value": "[답] 기준값은 4.6 이다."}], "meta": {"source": "t"}}
    ok = pv.attach(pair, _sources(), "docset:557:562", ["사업", "Ⅰ. 목표", "1.1. 분석"])
    assert ok["meta"]["node_path"] == ["사업", "Ⅰ. 목표", "1.1. 분석"] and ok["meta"]["review"]["decision"] is None
    assert audit_pair(ok) == ["review_incomplete"]                       # 검수만 남는다 — 검수는 사람이 한다
    bad = pv.attach(dict(pair, conversations=[pair["conversations"][0], {"from": "gpt", "value": "[답] 기준값은 7.3 이다."}]), _sources(), "docset:557:562", ["사업"])
    assert "unsupported_number" in audit_pair(bad)
