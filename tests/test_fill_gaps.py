"""못 채운 절의 까닭 — 원인 갈래(규칙), 담당자 안내, 다음 작성의 맥락."""
from zzaimy.app import drafting, fill_gaps

MAT_MIN = "[이 절의 양식 안내·작성방법]\n작성 지침 — 안건지를 줄여 옮긴다. 근거 — 회의 자료(안건지)의 제안 이유·주요 내용.\n《지난 회의록》 (문서함 검색)\n…"
MAT_PLAN = "[이 절의 양식 안내·작성방법]\n작성 지침 — 정책을 쓴다. 근거 — 공고·기본계획의 추진 배경·목적."


def test_reasons_follow_rules():
    assert fill_gaps.analyze("가. 제안 이유", [], MAT_MIN)["reason"] == "needs_user_document"
    assert fill_gaps.analyze("1. 추진 배경", [], MAT_PLAN)["reason"] == "no_material"
    assert fill_gaps.analyze("1. 추진 배경", [], MAT_PLAN + "\n《공고》 (문서함 검색)")["reason"] == "material_mismatch"
    part = fill_gaps.analyze("1. 추진 배경", [{"op": "insert", "text": "□ 배경 ○ 수요 (확인 필요)"}], MAT_PLAN)
    assert part["reason"] == "partial" and part["checks"] == 1
    assert fill_gaps.analyze("1. 추진 배경", [{"op": "insert", "text": "□ 배경 ○ 수요 증가"}], MAT_PLAN) is None
    full_table = [{"op": "fill", "cells": [{"row": 1, "col": 1, "text": "값"}]}]
    assert fill_gaps.analyze("2. 상정 안건", full_table, MAT_MIN) is None


def test_record_message_and_hint(tmp_path):
    rec = fill_gaps.analyze("가. 제안 이유", [], MAT_MIN, reply="근거 조각이 비어 있어")
    fill_gaps.record(tmp_path, rec)
    fill_gaps.record(tmp_path, rec)
    assert "안건지" in fill_gaps.user_message(rec) and "올리" in fill_gaps.user_message(rec)
    hint = fill_gaps.context_hint(tmp_path, "가. 제안 이유")
    assert "2번" in hint and "안건지" in hint and "asks" in hint
    assert fill_gaps.context_hint(tmp_path, "다른 절") == ""
    assert "[지난 작성에서 이 절이 빈 까닭]" in drafting.render_materials({"gap_hint": hint})
    s = fill_gaps.summary(tmp_path)
    assert s[0]["heading"] == "가. 제안 이유" and s[0]["needs_user_document"] == 2


def test_transport_mask_hides_long_number_runs_only():
    from zzaimy.app.embed_search import transport_mask
    assert transport_mask("카드번호 4009 0403 9142 1004 사용") == "카드번호 ○○○ 사용"
    assert transport_mask("주민 900101-1234567") == "주민 ○○○"
    assert transport_mask("2024학년도 101명 2025. 10. 29. 1,234,000원") == "2024학년도 101명 2025. 10. 29. 1,234,000원"
