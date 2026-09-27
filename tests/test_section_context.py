"""양식 작업본을 완성본으로 채우기 — 절 번호로 맞추고 쪽 머리말은 버리고 표는 행렬로."""

from zzaimy.app import section_context


def _chunks():
    hdr = "Ⅰ. 사업추진 목표"
    out = []
    seq = 0
    for page in range(11, 21):
        seq += 1
        out.append({"page_no": page, "seq": seq, "kind": "text", "content": f"{hdr}\r\n{page - 10}\r\n"})
    out += [
        {"page_no": 11, "seq": 100, "kind": "heading", "content": "1.1. 대학의 AI·DX 교육여건 분석"},
        {"page_no": 11, "seq": 101, "kind": "text", "content": "**국가 정책 동향**\nq 'AI 3개 강국' 도약\n§ 100조원 규모 투자"},
        {"page_no": 12, "seq": 102, "kind": "text", "content": "강점(S)\n지역 산업 연계 교육 기반"},
        {"page_no": 13, "seq": 103, "kind": "text", "content": f"{hdr}\r\n3\r\n1.2. 대학의 AIžDX 특성화 방향 \r\nq 선제적 노력\r\n§ VISION2030 수립"},
        {"page_no": 13, "seq": 104, "kind": "table", "content": '{"n_rows": 2, "n_cols": 2, "cells": [[0,0,1,1,1,"구분"],[0,1,1,1,1,"성과"],[1,0,1,1,0,"AI-X추진단"],[1,1,1,1,0,"총장 직속"]], "text": "구분 | 성과\\nAI-X추진단 | 총장 직속"}'},
        {"page_no": 15, "seq": 105, "kind": "text", "content": "2.1. 사업추진 목표\r\nq 비전 및 목표\r\n지역 산업을 혁신하는 X+AI 선도대학"},
    ]
    return out


SECTIONS = [
    {"index": 0, "level": 0, "heading": "(앞머리)", "start": 1, "end": 10, "chars": 30, "table_end": 0},
    {"index": 5, "level": 3, "heading": "1.1. 대학의 AIDX 교육여건 분석", "start": 100, "end": 110, "chars": 0, "table_end": 130},
    {"index": 10, "level": 2, "heading": "1) 강점(S)", "start": 130, "end": 140, "chars": 0, "table_end": 0},
    {"index": 11, "level": 2, "heading": "2) 약점(W)", "start": 140, "end": 150, "chars": 0, "table_end": 0},
    {"index": 14, "level": 3, "heading": "1.2. 대학의 AI･DX 특성화 방향", "start": 150, "end": 160, "chars": 0, "table_end": 170},
    {"index": 16, "level": 3, "heading": "2.1. 사업 추진목표", "start": 170, "end": 180, "chars": 200, "table_end": 0},
]


def test_plan_aligns_sections_by_number_and_drops_running_headers():
    plan = section_context.plan(SECTIONS, _chunks())
    by = {p["heading"][:3]: p for p in plan}
    s11 = by["1.1"]
    assert s11["matched"] and 11 in s11["pages"]
    text = s11["parts"][0][1]
    assert "국가 정책 동향" in text and "**" not in text and "○ 'AI 3개 강국' 도약" in text and "- 100조원" in text
    assert "사업추진 목표" not in text and "\n1\n" not in text                   # 쪽 머리말·쪽 번호는 버린다
    assert by["1) "]["matched"] and "지역 산업 연계" in by["1) "]["parts"][0][1]    # 소제목은 부모 범위 안에서
    assert not by["2) "]["matched"] and by["2) "]["parts"] == []
    s12 = by["1.2"]
    assert s12["matched"] and s12["tables"] == 1
    kinds = [k for k, _ in s12["parts"]]
    assert kinds == ["text", "table"] and s12["parts"][1][1] == [["구분", "성과"], ["AI-X추진단", "총장 직속"]]
    assert "선제적 노력" in s12["parts"][0][1] and "1.2." not in s12["parts"][0][1]
    assert by["2.1"]["matched"] and "X+AI 선도대학" in by["2.1"]["parts"][0][1]   # '사업 추진목표' ↔ '사업추진 목표'




def test_context_for_gives_section_text_and_tables_within_budget():
    plan = section_context.plan(SECTIONS, _chunks())
    sec = SECTIONS[4]
    ctx = section_context.context_for(sec, plan)
    assert "선제적 노력" in ctx and "구분 | 성과" in ctx
    assert len(section_context.context_for(sec, plan, budget=10)) <= 10
    assert section_context.context_for(SECTIONS[3], plan) == ""
