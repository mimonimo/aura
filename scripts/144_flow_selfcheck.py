#!/usr/bin/env python3
"""문서 작업 흐름 자가 점검 — 반입부터 에이전트 실행 재료까지 한 프로젝트를 놓고 스스로 확인한다.

사용자가 하나하나 지시하지 않아도 어디가 막혔는지 이 한 번으로 보이게 한다(2026-09-24). 항목마다 PASS·WARN·FAIL 과 이유.

 1 반입: 프로젝트 문서가 모두 추출 완료(reviewed)인가, 실패·처리 중은 없는가
 2 갈래·분리: 기준(공고·기본계획·지침)과 접수(양식·계획서·표)가 제대로 나뉘었는가, 갈래 미정은 없는가
 3 기준 조각: 기준 문서마다 regulation_chunks 가 있는가
 4 색인: 기준 조각이 임베딩 색인(chunk_embeddings.meta.json)에 들어 있는가(.reindex-needed 가 남아 있으면 WARN)
 5 검색: 공고 질문으로 find_relevant 를 돌려 이 프로젝트 기준 문서의 조각이 나오는가
 6 재료: 양식을 채울 접수 문서 조각(doc_chunks)이 있는가, 그림 쪽 판독 진행률
 7 구글: 허용 계정·파일 권한이 있는가(열람·작업본이 가능한가)
 8 제안: 다음 작업 제안이 만들어지는가
 9 대화: 이 프로젝트 대화에 답 없이 남은 질문이 없는가

실행(VM): set -a; . .env.local; set +a; env PYTHONPATH=src .venv/bin/python scripts/144_flow_selfcheck.py --project 4
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import paths as _paths  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402

CRITERIA_KINDS = {"announcement", "guideline", "criteria", "regulation"}


def say(level: str, item: str, why: str) -> None:
    print(f"{level:4s} {item} — {why}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--project", type=int, required=True)
    ap.add_argument("--question", default="이 사업의 신청 자격과 지원 규모는?")
    ap.add_argument("--ask", action="store_true", help="응답기를 직접 돌려 답과 근거를 본다 — 대화를 만들지 않는다(최근 대화를 어지럽히지 않게)")
    args = ap.parse_args()
    db = Database(Path(args.db))
    proj = db.get_project(args.project)
    if not proj:
        say("FAIL", "프로젝트", f"{args.project} 없음")
        return 1
    print(f"프로젝트 {proj['id']} 「{proj['name']}」 ({proj['sector']})")
    crit_ids = db.get_project_criteria_ids(proj["id"])
    crit = [db.get_document(i) for i in crit_ids]
    crit = [d for d in crit if d]
    intake = db.list_documents(proj["sector"], project_id=proj["id"])
    docs = crit + intake
    fails = 0

    # 1 반입
    bad = [f"{d['id']} {d['status']}" for d in docs if d.get("status") != "reviewed"]
    if bad:
        say("FAIL", "반입", "추출이 끝나지 않은 문서: " + ", ".join(bad)); fails += 1
    else:
        say("PASS", "반입", f"문서 {len(docs)}건 모두 추출 완료(기준 {len(crit)}·접수 {len(intake)})")

    # 2 갈래
    misplaced = [d["id"] for d in intake if (d.get("kind") in CRITERIA_KINDS)]
    unknown = [d["id"] for d in docs if not d.get("kind")]
    if misplaced:
        say("WARN", "갈래·분리", f"기준으로 보이는데 접수에 있는 문서: {misplaced} — 프로젝트 화면에서 기준으로 옮기거나 다시 접수")
    elif unknown:
        say("WARN", "갈래·분리", f"갈래 미정: {unknown}")
    else:
        say("PASS", "갈래·분리", "기준/접수 분리와 갈래 모두 정해짐")

    # 3 기준 조각
    with db._conn() as conn:
        counts = {r[0]: r[1] for r in conn.execute(
            "SELECT doc_id, COUNT(*) FROM regulation_chunks WHERE doc_id IN (%s) GROUP BY doc_id" % ",".join(str(i) for i in crit_ids)).fetchall()} if crit_ids else {}
    missing = [d["id"] for d in crit if not counts.get(d["id"])]
    if missing:
        say("FAIL", "기준 조각", f"기준 조각이 없는 기준 문서: {missing}"); fails += 1
    else:
        say("PASS", "기준 조각", ", ".join(f"{d['id']}:{counts.get(d['id'], 0)}" for d in crit))

    # 4 색인
    meta = _paths.index_dir() / "chunk_embeddings.npz"
    flag = Path(args.db).parent / ".reindex-needed"
    indexed = None
    if meta.exists():
        try:
            import numpy as np

            ids = {int(x) for x in np.load(meta, allow_pickle=False)["ids"].tolist()}
            if ids and crit_ids:
                with db._conn() as conn:
                    rows = conn.execute("SELECT id FROM regulation_chunks WHERE doc_id IN (%s)" % ",".join(str(i) for i in crit_ids)).fetchall()
                mine = {int(r[0]) for r in rows}
                indexed = len(mine & ids), len(mine)
        except Exception:
            indexed = None
    if flag.exists():
        say("WARN", "색인", "재색인 표시(.reindex-needed)가 남아 있음 — scripts/96_embed_on_thor.sh --apply")
    elif indexed is not None:
        say("PASS" if indexed[0] == indexed[1] else "WARN", "색인", f"기준 조각 {indexed[0]}/{indexed[1]} 색인됨 ({meta.name})")
    else:
        say("WARN", "색인", "색인 메타를 읽지 못함 — 어휘 검색만 될 수 있음")

    # 5 검색
    try:
        from zzaimy.app.regulations import find_relevant

        hits = find_relevant(db, args.question, top_k=5)
        mine = [h for h in hits if h.get("doc_id") in set(crit_ids)]
        if mine:
            say("PASS", "검색", f"질문 「{args.question}」 → 이 프로젝트 기준 조각 {len(mine)}/{len(hits)} (첫 조각: {(mine[0].get('content') or '')[:50]!r})")
        else:
            say("WARN", "검색", f"질문 「{args.question}」 에 이 프로젝트 기준 조각이 안 나옴(나온 것 {len(hits)}) — 색인·질문을 확인")
    except Exception as e:
        say("FAIL", "검색", f"find_relevant 실패 {type(e).__name__}: {e}"); fails += 1

    # 6 재료
    from zzaimy.app.pipeline import DocumentProcessor

    for d in intake:
        chunks = db.list_doc_chunks(d["id"])
        sparse = DocumentProcessor.sparse_pages(Path(d["stored_path"])) if d.get("stored_path") else []
        done = set(db.vision_pages(d["id"]))
        left = [p for p in sparse if p not in done]
        note = f"조각 {len(chunks)}"
        if sparse:
            note += f" · 그림 쪽 {len(sparse)} 중 판독 {len(sparse) - len(left)}"
        say("PASS" if chunks and not left else ("WARN" if chunks else "FAIL"), f"재료 {d['id']}", f"{note} — {d['filename'][:40]}")
        if not chunks:
            fails += 1

    # 7 구글
    try:
        from zzaimy.ingest import gdrive_files

        email = gdrive_files.account_for(db, proj.get("owner") or "zzaimy", None)
        if email and gdrive_files.has_file_scope(email):
            say("PASS", "구글", f"허용 계정 {email} · 파일 만들기 권한 있음")
        else:
            say("WARN", "구글", "허용 계정이나 파일 권한이 없음 — 열람본·작업본을 만들 수 없음")
    except Exception as e:
        say("WARN", "구글", f"확인 실패 {type(e).__name__}")

    # 8 제안(같은 규칙을 여기서 다시 계산)
    forms = [d for d in intake if d.get("kind") == "form"]
    say("PASS" if forms else "WARN", "제안", f"양식 {len(forms)}건 → '작성 시작' 제안" + ("" if forms else " — 양식이 없어 작성 시작 제안이 없음"))

    # 9 대화
    dangling = []
    for s in db.list_chat_sessions(limit=500):
        if s.get("project_id") == proj["id"]:
            last = db.list_chats(int(s["id"]), limit=1)
            if last and last[-1]["role"] == "user":
                dangling.append(int(s["id"]))
    say("PASS" if not dangling else "WARN", "대화", "답 없이 남은 질문 없음" if not dangling else f"답 없이 남은 대화: {dangling}")
    # 10 에이전트 실행(선택): 프로젝트 기준을 근거로 답하는지 — 대화 세션 없이 응답기만 부른다
    if args.ask:
        try:
            from zzaimy.app.responder import AgentResponder

            r = AgentResponder()
            answer = r.answer(db, args.question, criteria_ids=crit_ids, project=proj)
            srcs = getattr(r, "last_sources", []) or []
            docs_cited = {s.get("title") for s in srcs}
            say("PASS" if srcs else "WARN", "에이전트", f"근거 {len(srcs)}건({len(docs_cited)}개 문서) · 답 {len(answer)}자")
            print("    답:", answer[:500].replace("\n", " / "))
        except Exception as e:
            say("FAIL", "에이전트", f"응답기 실패 {type(e).__name__}: {e}"); fails += 1
    print("결과:", "FAIL" if fails else "OK")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
