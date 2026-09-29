import json
from xml.etree import ElementTree

from zzaimy.dataset.question_tree_review import build_tasks, REVIEW_CONFIG


def test_questions_answers_and_paths_are_separate():
    nodes = [{'index': 1, 'path': ['Ⅰ. 계획', '1. 개요'], 'children': ['1.1 배경']},
             {'index': 2, 'path': ['Ⅰ. 계획', '1. 개요', '1.1 배경'], 'children': []}]
    pairs = [{'meta': {'source': 'real-section'}, 'conversations': [{'from': 'gpt', 'value': json.dumps({'ops': [
        {'op': 'insert', 'section': 2, 'text': '완성본의 실제 배경 설명'},
        {'op': 'table', 'section': 2, 'text': '대상 | 인원\n학생 | 9명'}]})}]}]
    tasks = build_tasks('합성 사업', nodes, pairs, 1, 2)
    assert len(tasks) == 3
    assert tasks[1]['answer'] == '완성본의 실제 배경 설명'
    assert tasks[1]['path'] == '합성 사업 → Ⅰ. 계획 → 1. 개요 → 1.1 배경'
    assert all(t['_record']['reviewed'] is False for t in tasks)
    assert len({t['sample_id'] for t in tasks}) == len(tasks)
    assert tasks[2]['_record']['source_doc_id'] == 2
    ElementTree.fromstring(REVIEW_CONFIG)


def test_unmatched_section_is_not_guessed():
    pairs = [{'meta': {'source': 'real-section'}, 'conversations': [{'value': json.dumps({'ops': [
        {'op': 'insert', 'section': 99, 'text': '다른 절'}]})}]}]
    assert build_tasks('사업', [], pairs, 1, 2) == []
