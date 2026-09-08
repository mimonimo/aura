"""데이터 공방 ↔ Label Studio 왕복 — 순수 함수 계약 테스트.

핵심 불변식:
- export: 학습 쌍 1개 → LS 태스크 1개, 검수용 입력·출력 노출 + 원쌍 보존.
- import: 폐기는 제외, 수정본은 반영, 되받은 출력도 수치 검증 통과분만 남긴다.
"""

from __future__ import annotations

from zzaimy.dataset.labelstudio import (
    LABEL_CONFIG,
    export_to_label_studio,
    import_from_label_studio,
)


def _pair(human: str, output: str, source: str = "draft", doc_id: int = 7) -> dict:
    return {
        "conversations": [
            {"from": "human", "value": human},
            {"from": "gpt", "value": output},
        ],
        "meta": {"source": source, "doc_id": doc_id},
    }


# 근거에 5,000,000이 있는 쌍 — 출력도 그 수치만 쓴다(빌드 시 이미 통과한 형태)
PAIR = _pair(
    "계획서 예산 항목을 작성하라\n\n[재료 문서]\n사업비 총액 5,000,000원",
    "예산 총액은 5,000,000원으로 편성한다.",
)


def test_export_one_task_per_pair():
    tasks = export_to_label_studio([PAIR])
    assert len(tasks) == 1
    t = tasks[0]
    assert "data" in t
    assert "5,000,000" in t["data"]["input"]      # 입력(문맥) 노출
    assert t["data"]["output"] == PAIR["conversations"][1]["value"]  # 출력 노출
    assert t["data"]["source"] == "draft"


def test_export_preserves_original_pair():
    tasks = export_to_label_studio([PAIR])
    assert tasks[0]["data"]["_pair"] == PAIR      # 정확 복원용 원쌍 보존


def _annot(task: dict, choice: str, corrected: str | None = None) -> dict:
    result = [{
        "from_name": "decision", "to_name": "output", "type": "choices",
        "value": {"choices": [choice]},
    }]
    if corrected is not None:
        result.append({
            "from_name": "corrected", "to_name": "output", "type": "textarea",
            "value": {"text": [corrected]},
        })
    return {"data": task["data"], "annotations": [{"result": result}]}


def test_import_discard_is_dropped():
    task = export_to_label_studio([PAIR])[0]
    out = import_from_label_studio([_annot(task, "폐기")])
    assert out == []


def test_import_accept_keeps_original():
    task = export_to_label_studio([PAIR])[0]
    out = import_from_label_studio([_annot(task, "채택")])
    assert len(out) == 1
    assert out[0]["conversations"][1]["value"] == PAIR["conversations"][1]["value"]
    assert out[0]["meta"]["doc_id"] == 7          # 메타 보존


def test_import_edit_uses_correction():
    task = export_to_label_studio([PAIR])[0]
    fixed = "예산 총액은 5,000,000원으로 편성하며 세부 항목을 명시한다."
    out = import_from_label_studio([_annot(task, "수정", fixed)])
    assert len(out) == 1
    assert out[0]["conversations"][1]["value"] == fixed


def test_import_correction_with_unsourced_number_is_dropped():
    """수정본이 근거 없는 수치를 넣으면 그 쌍은 폐기된다(수치 검증)."""
    task = export_to_label_studio([PAIR])[0]
    bad = "예산 총액은 9,999,999원으로 편성한다."   # 근거에 없는 수치
    out = import_from_label_studio([_annot(task, "수정", bad)])
    assert out == []


def test_import_skips_unannotated():
    task = export_to_label_studio([PAIR])[0]
    assert import_from_label_studio([{"data": task["data"], "annotations": []}]) == []


def test_roundtrip_accept_all_preserves_outputs():
    pairs = [PAIR, _pair("검토하라\n\n[접수 문서]\n인원 3명", "인원은 3명이다.", "review", 8)]
    tasks = export_to_label_studio(pairs)
    annots = [_annot(t, "채택") for t in tasks]
    out = import_from_label_studio(annots)
    got = [p["conversations"][1]["value"] for p in out]
    want = [p["conversations"][1]["value"] for p in pairs]
    assert got == want


def test_label_config_has_controls():
    assert isinstance(LABEL_CONFIG, str) and LABEL_CONFIG.strip()
    for token in ("채택", "수정", "폐기", "$input", "$output", "Choices", "TextArea"):
        assert token in LABEL_CONFIG
