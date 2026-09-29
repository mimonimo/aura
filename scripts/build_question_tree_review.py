#!/usr/bin/env python3
"""실문서 완성본의 질문–정답 후보를 새 Label Studio 검수 묶음으로 생성. 삭제 없음."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--form', required=True, type=int)
    ap.add_argument('--done', required=True, type=int)
    ap.add_argument('--push', action='store_true')
    args = ap.parse_args()
    from zzaimy.app.db import Database
    from zzaimy.dataset import real_pairs as rp, tree_cot as tc
    from zzaimy.dataset.question_tree_review import build_tasks, REVIEW_CONFIG
    db = Database(ROOT / 'data/platform/platform.db')
    form = db.get_document(args.form)
    done = db.get_document(args.done)
    if not form or not done:
        raise SystemExit('양식 또는 완성본 문서를 찾을 수 없습니다.')
    sections = rp.form_model(ROOT / form['stored_path'])
    chunks = db.list_doc_chunks(args.done)
    roots = tc.build_tree(sections, tc.part_titles(chunks), rp.section_parts(sections, chunks))
    nodes = []
    index = 0
    def walk(node, parents):
        nonlocal index
        path = parents + [node.heading]
        if node.level:
            index += 1
            nodes.append({'index': sections[index-1].index, 'path': path, 'children': [c.heading for c in node.children]})
        for child in node.children:
            walk(child, path)
    for root in roots:
        walk(root, [])
    # 기존 완성본 추출물을 재사용한다. 원본과 기존 태스크는 덮어쓰지 않는다.
    real = [json.loads(s) for s in (ROOT / 'data/training/real_pairs.jsonl').read_text().splitlines() if s.strip()]
    real = [p for p in real if p.get('meta', {}).get('doc_id') == args.done and p.get('meta', {}).get('form_id') == args.form]
    tree = [json.loads(s) for s in (ROOT / 'data/training/tree_cot_pairs.jsonl').read_text().splitlines() if s.strip()]
    names = {p.get('meta', {}).get('program') for p in tree if p.get('meta', {}).get('doc_id') == args.done}
    names.discard(None)
    if len(names) != 1 or not real:
        raise SystemExit('사업명 또는 원본 학습쌍을 유일하게 연결하지 못했습니다.')
    tasks = build_tasks(names.pop(), nodes, real, args.form, args.done)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output = ROOT / 'data/training' / ('question-tree-review-' + stamp)
    output.mkdir(mode=0o700)
    (output / 'tasks.json').write_text(json.dumps([{'data': t} for t in tasks], ensure_ascii=False, indent=2))
    (output / 'tree.json').write_text(json.dumps(nodes, ensure_ascii=False, indent=2))
    report = {'tasks': len(tasks), 'nodes': len(nodes), 'output': str(output), 'reviewed': 0}
    if args.push and tasks:
        from zzaimy.dataset.ls_client import LabelStudioClient
        client = LabelStudioClient(db.get_setting('labelstudio_url'), db.get_setting('labelstudio_token'))
        title = 'ZZAIMY 사업 트리 질문·정답 ' + stamp
        pid = client.ensure_project(title, label_config=REVIEW_CONFIG, description='완성본 기반 구조화 문답 후보. 검수 전/SFT 미승인. 기존 태스크 보존.')
        client._req('POST', f'/api/projects/{pid}/import', json=[{'data': t} for t in tasks])
        report.update(project_id=pid, title=title, url=f'{client.base}/projects/{pid}/data')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
