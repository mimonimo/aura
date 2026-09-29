"""문서 구조 단계 문답 — 나무·뼈대·요구 항목·단계 답·수치 규칙."""

import json

from zzaimy.dataset import real_pairs as rp
from zzaimy.dataset import tree_cot as tc


def _sections():
    return [rp.FormSection(index=1, heading="1. 대학의 여건", level=1),
            rp.FormSection(index=2, heading="1.1. 교육여건 분석", level=2, instructions="【작성방법】 1) 지역 동향을 쓴다 2) 대학의 여건과 SWOT 를 쓴다 ※ 5쪽 이내"),
            rp.FormSection(index=3, heading="1. 추진체계", level=1),
            rp.FormSection(index=4, heading="1.1. 거버넌스", level=2, instructions="【작성방법】 1) 위원회 구성을 쓴다")]


def _plan():
    return [{"heading": "1.1. 교육여건 분석", "parts": [("text", "(1) 대외여건\n본문 문장은 길어서 뼈대가 아니다 그러니까 빠진다 정말로\n❑ 정책 동향"),
                                              ("table", {"rows": [["구분", "동향", "출처"], ["국가", "AI 3개 강국", "2025"]], "cells": []}),
                                              ("figure", {"title": "여건 분석", "layout": "cards", "blocks": [{"title": "정책", "items": ["a"]}]})]},
            {"heading": "1.1. 거버넌스", "parts": []}]


def test_build_tree_splits_parts_where_numbering_restarts():
    roots = tc.build_tree(_sections(), ["Ⅰ. 사업추진 목표", "Ⅲ. 추진체계"], _plan())
    assert [r.heading for r in roots] == ["Ⅰ. 사업추진 목표", "Ⅲ. 추진체계"]
    assert [c.heading for c in roots[0].children] == ["1. 대학의 여건"]
    assert roots[0].children[0].children[0].heading == "1.1. 교육여건 분석"
    assert roots[1].children[0].children[0].part == "Ⅲ. 추진체계"


def test_skeleton_keeps_subheads_table_heads_and_figures_only():
    sk = tc.skeleton_of(_plan()[0])
    assert sk == ["소제목: (1) 대외여건", "소제목: ❑ 정책 동향", "표: 구분 | 동향 | 출처", "도식: 여건 분석 (정책)"]


def test_required_items_split_on_numbers_and_drop_box_label():
    items = tc.required_items("【작성방법】 1) 지역 동향을 쓴다 2) 대학의 여건과 SWOT 를 쓴다 ※ 5쪽 이내")
    assert items == ["지역 동향을 쓴다", "대학의 여건과 SWOT 를 쓴다", "5쪽 이내"]


def test_overview_lines_drop_reference_notes_and_source_labels():
    lines = tc.overview_lines(["(공고 · 사업 목적) ◦ AI 역량을 갖춘 인재 양성 2. 사업 개요 ※ 상세내용은 사업 기본계획 참조[붙임]",
                               "(기본계획 · 사업 개요) □ (선정 규모) 총 24개 내외 사업단"])
    assert lines == ["AI 역량을 갖춘 인재 양성", "(선정 규모) 총 24개 내외 사업단"]


def test_steps_chain_and_number_rule():
    secs, plan = _sections(), _plan()
    roots = tc.build_tree(secs, ["Ⅰ. 사업추진 목표", "Ⅲ. 추진체계"], plan)
    program = "2026학년도 AID 전환 중점 전문대학 지원사업"
    s1 = tc.step1(program, ["(공고 제목) " + program + " 공고", "(기본계획 · 사업 개요) □ (선정 규모) 총 24개 내외 사업단"])
    assert s1["gpt"].startswith("[1단계: 질문 파악]") and "[3단계: 정리]" in s1["gpt"] and "\n[답] 사업명:" in s1["gpt"]
    assert "총 24개 내외 사업단" in s1["gpt"] and tc.missing_numbers(s1) == set()
    overview = s1["gpt"].split("개요:", 1)[-1]
    s2 = tc.step2(program, overview, roots, ["(평가편람) Ⅰ. 사업추진 목표(15)"])
    assert "Ⅰ. 사업추진 목표\n  1. 대학의 여건" in s2["gpt"] and tc.missing_numbers(s2) == set()
    node = roots[0].children[0].children[0]
    assert tc.step3(program, overview, roots, node, []) is None      # 착안점 근거가 없으면 단정하지 않는다
    s3 = tc.step3(program, overview, roots, node, ["평가 착안점 | 지역 동향"])
    assert "1. 지역 동향을 쓴다" in s3["gpt"] and "3. 5쪽 이내" in s3["gpt"]
    assert "[근거: 양식의 작성방법 상자]" in s3["human"] and "SWOT 를 쓴다" in s3["human"]   # 답이 입력에서 풀린다
    assert tc.missing_numbers(s3) == set()                       # '5쪽' 은 입력의 작성방법에 있다
    view = tc.to_pair(s3, program, 562)["meta"]["view"]
    assert view["question"].startswith("「1.1. 교육여건 분석」") and view["reasoning"].startswith("[1단계") and view["answer"].startswith("이 절이 다룰 항목")
    s4 = tc.step4(program, overview, roots, node)
    assert "- 표: 구분 | 동향 | 출처" in s4["gpt"] and "- 도식: 여건 분석" in s4["gpt"]
    sp = tc.step_part(program, overview, roots, roots[0], ["Ⅰ. 사업추진 목표(15)"])
    assert "1.1. 교육여건 분석 — 지역 동향을 쓴다" in sp["gpt"] and tc.missing_numbers(sp) == set()
    chain = tc.chain_conversation([s1, s2, sp, s3, s4], program, 562)
    assert len(chain["conversations"]) == 10 and chain["conversations"][2]["value"].startswith("[근거")
    msgs = tc.to_messages(chain)["messages"]
    assert msgs[0]["role"] == "user" and msgs[-1]["role"] == "assistant"
    assert tc.to_alpaca(chain) is None
    alp = tc.to_alpaca(tc.to_pair(s3, program, 562))
    assert alp["instruction"].startswith("「1.1. 교육여건 분석」 절에는") and "[근거: 이 절의 평가 착안점]" in alp["input"] and alp["output"] == s3["gpt"]
    # 사실 수치가 입력에 없으면 폐기
    bad = dict(s3, gpt=s3["gpt"] + "\n예산 1,250백만원, 참여자 27명, 위원 9명")
    assert tc.missing_numbers(bad) == {"1250", "27", "9"} and tc.to_pair(bad, program, 562) is None
