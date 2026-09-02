#!/bin/bash
# 전 문서 재처리 + 점검 체인 (Spark).
#
# 파이프라인 표준(MinerU 기본·공백 복원·좌표 저장·속성 태깅)을 기존 문서 전체에
# 소급 적용하고, 임베딩 재계산·앱 재시작 후 품질 점검 요약을 남긴다.
#
# 실행: nohup bash scripts/61_reprocess_all.sh > /tmp/reprocess-all.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/.."

echo "[$(date +%T)] 1/4 전 문서 재처리"
OMP_NUM_THREADS=10 MKL_NUM_THREADS=10 .venv/bin/python - <<'PYEOF'
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, "src")
from zzaimy.app.db import Database
from zzaimy.app.pipeline import DocumentProcessor

db = Database(Path("data/platform/platform.db"))
conn = sqlite3.connect("data/platform/platform.db")
conn.row_factory = sqlite3.Row
rows = [dict(r) for r in conn.execute(
    "SELECT id, filename, stored_path, doc_type FROM documents ORDER BY id"
).fetchall()]
conn.close()

proc = DocumentProcessor()
ok = missing = failed = 0
for r in rows:
    p = Path(r["stored_path"])
    if not p.exists():
        missing += 1
        continue
    proc.process(db, r["id"], p)
    d = db.get_document(r["id"]) or {}
    state = d.get("status")
    print(f"[{r['id']:>3}] {r['doc_type']:<10} {r['filename'][:34]:<36}"
          f" → {state} · {d.get('parse_note') or '일반'}", flush=True)
    ok += 1 if state == "reviewed" else 0
    failed += 1 if state == "failed" else 0
print(f"재처리 결과: 성공 {ok} / 실패 {failed} / 원본 없음 {missing}")
PYEOF

echo "[$(date +%T)] 2/4 임베딩 재계산"
OMP_NUM_THREADS=10 MKL_NUM_THREADS=10 ZZAIMY_EMBED_DEVICE=cpu \
  .venv-train/bin/python scripts/52_embed_chunks.py > /tmp/embed-reproc.log 2>&1 \
  && grep -m1 "조각" /tmp/embed-reproc.log

echo "[$(date +%T)] 3/4 앱 재시작"
pkill -f "[z]zaimy.app.main"; sleep 1
(nohup ~/start-platform.sh > /tmp/platform.log 2>&1 &)
sleep 8
curl -sk -u zzaimy:password -o /dev/null -w "대시보드: %{http_code}\n" https://localhost:8800/

echo "[$(date +%T)] 4/4 점검 요약"
.venv/bin/python - <<'PYEOF'
import sqlite3

conn = sqlite3.connect("data/platform/platform.db")
q = lambda s: conn.execute(s).fetchall()  # noqa: E731
print("문서 상태:", q("SELECT status, COUNT(*) FROM documents GROUP BY status"))
print("파싱 경로:", q(
    "SELECT COALESCE(substr(parse_note,1,14),'일반/기타'), COUNT(*)"
    " FROM documents WHERE status='reviewed' GROUP BY 1 ORDER BY 2 DESC"))
print("조각:", q(
    "SELECT kind, COUNT(*), SUM(bbox IS NOT NULL) AS with_bbox"
    " FROM doc_chunks GROUP BY kind"))
print("기준 조각 수:", q("SELECT COUNT(*) FROM regulation_chunks"))
print("공백 빈약 조각(공백<5%):", q(
    "SELECT COUNT(*) FROM doc_chunks WHERE kind='text' AND length(content)>60"
    " AND (length(content)-length(replace(content,' ','')))*20 < length(content)"))
conn.close()
PYEOF
echo "REPROCESS_ALL_DONE"
