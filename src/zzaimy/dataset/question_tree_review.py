"""완성본 기반 질문–정답을 사업 트리에 연결하는 검수 후보 생성기.

LLM으로 사실이나 사고 과정을 만들지 않는다. 답변은 기존 완성본 추출,
구조 답변은 양식 트리에서 가져온다. 아직 검수 전이며 SFT 정답으로 승인하지 않는다.
"""
from __future__ import annotations

import hashlib
import json

REVIEW_CONFIG = '''<View>
  <Header value="사업 구조별 질문–정답 검수"/>
  <Header value="$program" size="4"/>
  <Text name="path" value="$path"/>
  <Text name="task_type" value="$task_type"/>
  <Header value="질문" size="4"/><Text name="question" value="$question"/>
  <Header value="정답 후보 — 완성본에서 추출, 검수 전" size="4"/>
  <Text name="output" value="$answer"/>
  <Header value="이 답을 고른 근거" size="4"/><Text name="reasoning" value="$reasoning"/>
  <Header value="원문 위치" size="4"/><Text name="source" value="$source"/>
  <Header value="원문 대조 자료" size="4"/><Text name="evidence" value="$evidence"/>
  <Choices name="decision" toName="output" choice="single" required="true" showInline="true">
    <Choice value="채택"/><Choice value="수정"/><Choice value="폐기"/>
  </Choices>
  <Header value="보완할 부분" size="4"/>
  <Choices name="issues" toName="output" choice="multiple" showInline="true">
    <Choice value="질문이 모호함"/><Choice value="답변 범위가 다름"/><Choice value="구조 연결 오류"/>
    <Choice value="원문 추출 오류"/><Choice value="근거 부족"/><Choice value="개인정보"/>
  </Choices>
  <TextArea name="corrected" toName="output" rows="6" editable="true" maxSubmissions="1" placeholder="수정한 정답"/>
</View>'''


def build_tasks(program: str, nodes: list[dict], pairs: list[dict], form_id: int, done_id: int) -> list[dict]:
    """nodes: index, path, children. 경로는 실제 양식 나무에서 호출자가 제공한다."""
    tasks = []
    by_index = {n['index']: n for n in nodes}

    def add(node, kind, question, answer, evidence, doc_id, locator):
        if not answer.strip():
            return
        path = [program] + node['path']
        key = hashlib.sha256(json.dumps([path, kind, question, answer], ensure_ascii=False).encode()).hexdigest()[:20]
        tasks.append({'sample_id': key, 'program': program, 'path': ' → '.join(path),
                      'task_type': kind, 'question': question, 'answer': answer,
                      'reasoning': f"질문의 대상은 ‘{node['path'][-1]}’이며, 아래 원문 위치에 있는 내용을 답변 범위로 삼았습니다.",
                      'source': f'문서 #{doc_id} · {locator} (쪽 번호는 미검증)', 'evidence': evidence,
                      'status': '검수 전',
                      '_record': {'program_id': f'docset:{form_id}:{done_id}', 'node_path': path,
                                  'question': question, 'answer': answer, 'task_type': kind,
                                  'source_doc_id': doc_id, 'source_locator': locator,
                                  'evidence': evidence, 'reviewed': False}})

    for node in nodes:
        if node['children']:
            answer = '\n'.join(f'- {c}' for c in node['children'])
            add(node, '하위 항목 탐색', f"{program}의 ‘{node['path'][-1]}’은 어떤 하위 항목으로 구성되어 있나요?",
                answer, answer, form_id, '양식 목차 / ' + ' / '.join(node['path']))
    for pair in pairs:
        if pair.get('meta', {}).get('source') != 'real-section':
            continue
        try:
            plan = json.loads(pair['conversations'][-1]['value'])
        except (ValueError, KeyError, TypeError):
            continue
        ops = plan.get('ops') or []
        index = next((o.get('section') for o in ops if o.get('section') in by_index), None)
        node = by_index.get(index)
        if node is None:
            continue  # 이름이 같다는 이유만으로 다른 장의 절을 연결하지 않는다
        paragraphs = [o['text'] for o in ops if o.get('op') == 'insert' and o.get('text')]
        if paragraphs:
            # 대량 시스템 프롬프트 대신 완성본에서 추출한 본문만 정답·대조 자료로 제시.
            answer = '\n\n'.join(paragraphs)
            add(node, '절 내용 설명', f"{program}의 ‘{node['path'][-1]}’에는 어떤 내용이 제시되어 있나요?",
                answer, answer, done_id, ' / '.join(node['path']))
        tables = [o['text'] for o in ops if o.get('op') == 'table' and o.get('text')]
        for i, table in enumerate(tables, 1):
            header = table.splitlines()[0]
            add(node, '표 내용 확인', f"같은 사업의 ‘{node['path'][-1]}’ 중 표 {i}의 항목과 내용을 알려주세요. 표 머리: {header}",
                table, table, done_id, ' / '.join(node['path']) + f' / 추출 표 {i}')
    return tasks
