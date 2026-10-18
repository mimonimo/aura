#!/usr/bin/env python3
"""근거 기록이 없는 예전 대화에 근거를 채운다 — 대화 주제(chat_topics.py)를 정하기 위해.

예전 답변의 근거는 메모리에만 있어 남지 않았다. 각 대화의 질문으로 운영 검색 경로
(find_relevant)를 다시 돌려 근거를 남긴다. LLM 은 부르지 않는다. 이미 근거가 있는 대화는 건너뛴다.

사용:  PYTHONPATH=src .venv/bin/python scripts/92_backfill_chat_topics.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from zzaimy.app.chat_topics import ChatTopics
    from zzaimy.app.db import Database
    from zzaimy.app.regulations import find_relevant

    path = ROOT / "data" / "platform" / "platform.db"
    db = Database(path)
    topics = ChatTopics(path)
    conn = sqlite3.connect(path)
    have = {r[0] for r in conn.execute("SELECT DISTINCT session_id FROM chat_sources")}
    sessions = conn.execute(
        "SELECT s.id, s.title FROM chat_sessions s WHERE EXISTS"
        " (SELECT 1 FROM chat_messages m WHERE m.session_id = s.id AND m.role = 'user')"
        " ORDER BY s.id").fetchall()
    filled = 0
    for sid, title in sessions:
        if sid in have:
            continue
        questions = [r[0] for r in conn.execute(
            "SELECT content FROM chat_messages WHERE session_id = ? AND role = 'user' ORDER BY id",
            (sid,))]
        for q in questions:
            hits = find_relevant(db, q[:500], top_k=3)
            sources = [{"doc_id": h.get("doc_id"), "title": h.get("reg_title") or "문서",
                        "heading": h.get("heading") or "", "snippet": (h.get("content") or "")[:160],
                        "origin": "backfill", "weak": bool(h.get("weak_evidence"))} for h in hits]
            if sources and not args.dry_run:
                topics.record(sid, sources)
        filled += 1
        name = topics.topics([sid]).get(sid) if not args.dry_run else "-"
        print(f"  [{sid}] {title[:30]} → {name}")
    conn.close()
    print(f"{'미리보기' if args.dry_run else '채움'} — 대화 {filled}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
