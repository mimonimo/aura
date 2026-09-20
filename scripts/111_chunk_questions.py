#!/usr/bin/env python3
"""조각마다 '이 조각으로 답할 수 있는 질문'을 만들어 둔다 — 검색용 보조 축(doc2query).

왜: 용어를 모르고 상황으로 묻는 질의는 조각 본문과 말이 겹치지 않는다. 실측(2026-09-20)에서
상황 질문의 22.7%는 어휘·조밀 두 축 모두 상위 20 안에 정답을 못 넣었고, 그 몫은 리랭커나
후보 수로는 넘을 수 없다. 조각 쪽에 '담당자가 쓸 표현의 질문'을 붙여 두면 그 말끼리 맞는다.

지키는 선:
  · **질문만 만든다. 답이나 수치는 만들지 않는다**(절대규칙 1·8). 생성물은 검색에만 쓰고
    화면의 근거로 내보이지 않는다 — 근거는 항상 원문 조각이다.
  · 저장은 별도 표(chunk_questions). 조각 본문은 건드리지 않는다.
  · 이미 만든 조각은 건너뛴다(중단·재개 가능).

사용 (VM 에서):
  env PYTHONPATH=src .venv/bin/python scripts/111_chunk_questions.py \
      --url http://211.170.162.121:11434/v1 --model qwen3:30b-a3b-instruct-2507-q4_K_M \
      [--limit 50] [--workers 4]
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

PROMPT = (
    "다음은 대학 행정 규정·공고의 한 조각이다. 이 조각을 근거로 답할 수 있는 질문 3개를 쓰라.\n"
    "규칙:\n"
    "- 담당자가 실제로 물을 말투로, 조각의 용어를 몰라도 나올 수 있는 표현으로 쓴다.\n"
    "- 질문만 쓴다. 답·수치·설명을 쓰지 않는다.\n"
    "- 한 줄에 하나, 번호나 기호 없이 쓴다.\n\n"
    "조각 출처: {title} / {heading}\n조각:\n{body}\n\n질문 3개:"
)
_DDL = """
CREATE TABLE IF NOT EXISTS chunk_questions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  chunk_id   INTEGER NOT NULL,
  question   TEXT NOT NULL,
  model      TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunk_questions_chunk ON chunk_questions(chunk_id);
"""
_BAD = re.compile(r"^(?:\d+[.)]|[-•*])\s*")


def clean(line: str) -> str:
    s = _BAD.sub("", line.strip())
    s = re.sub(r"\s+", " ", s).strip(" \"'“”·")
    return s if 6 <= len(s) <= 120 and "?" in s + "?" else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    from zzaimy.generate import client as gen

    if gen.OpenAI is None:
        print("openai 패키지가 없습니다")
        return 1
    api = gen.OpenAI(base_url=args.url, api_key="none", timeout=120, max_retries=1)
    extra = ({"reasoning_effort": "none"} if gen.is_ollama(args.url)
             else {"chat_template_kwargs": {"enable_thinking": False}})

    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    with conn:
        conn.executescript(_DDL)
    todo = conn.execute(
        "SELECT c.id, c.reg_title, c.heading, c.content FROM regulation_chunks c"
        " WHERE NOT EXISTS (SELECT 1 FROM chunk_questions q WHERE q.chunk_id = c.id)"
        " ORDER BY c.id" + (f" LIMIT {args.limit}" if args.limit else "")).fetchall()
    print(f"질문을 만들 조각 {len(todo)}개 · 모델 {args.model}", flush=True)
    if not todo:
        return 0

    def ask(row: sqlite3.Row) -> tuple[int, list[str]]:
        try:
            r = api.chat.completions.create(
                model=args.model, temperature=0.3, max_tokens=180, extra_body=extra,
                messages=[{"role": "user", "content": PROMPT.format(
                    title=row["reg_title"] or "", heading=row["heading"] or "",
                    body=(row["content"] or "")[:1200])}])
            text = r.choices[0].message.content or ""
        except Exception as e:
            print(f"  조각 {row['id']} 실패: {type(e).__name__}", flush=True)
            return row["id"], []
        qs = [q for q in (clean(ln) for ln in text.splitlines()) if q]
        return row["id"], qs[:3]

    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    done = made = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for cid, qs in pool.map(ask, todo):
            done += 1
            if qs:
                with conn:
                    conn.executemany(
                        "INSERT INTO chunk_questions (chunk_id, question, model, created_at)"
                        " VALUES (?,?,?,?)", [(cid, q, args.model, now) for q in qs])
                made += len(qs)
            if done % 50 == 0:
                rate = done / max(1e-9, time.time() - t0)
                print(f"  {done}/{len(todo)} · 질문 {made}개 · {rate:.2f}조각/초"
                      f" · 남은 시간 {(len(todo) - done) / max(rate, 1e-9) / 60:.0f}분", flush=True)
    print(f"끝 — 조각 {done}개, 질문 {made}개 ({time.time() - t0:.0f}초)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
