#!/bin/bash
# 기준 저장소 야간 갱신 체인 (Spark에서 실행).
#
# 1) 임베딩 학습(54)이 돌고 있으면 끝날 때까지 기다린다 (CPU 경합 방지)
# 2) 마스킹 정책 변경 이전에 등록된 기준 문서를 원본 파일로 재처리한다
#    — 기준 문서는 판단 근거이므로 마스킹하지 않는다 (2026-09-02 결정)
# 3) 전체 조각 임베딩을 재계산한다 (52)
# 4) 플랫폼을 재시작해 새 인덱스를 물린다
#
# 실행: nohup bash scripts/57_criteria_refresh_chain.sh > /tmp/criteria-refresh.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/.."

echo "[$(date +%T)] 학습(54) 종료 대기"
while pgrep -f "54_train_embed" > /dev/null; do sleep 60; done

echo "[$(date +%T)] 구 파이프라인 등록분 재처리 시작"
OMP_NUM_THREADS=10 MKL_NUM_THREADS=10 .venv/bin/python - <<'EOF'
from pathlib import Path
import sqlite3, sys
sys.path.insert(0, "src")
from zzaimy.app.db import Database
from zzaimy.app.pipeline import DocumentProcessor

db = Database(Path("data/platform/platform.db"))
conn = sqlite3.connect("data/platform/platform.db"); conn.row_factory = sqlite3.Row
# 오늘 교체한 정제 페이지(.txt, id>103)는 이미 새 파이프라인 산출물 — 그 외만
rows = [dict(r) for r in conn.execute(
    "SELECT id, filename, stored_path FROM documents"
    " WHERE doc_type='regulation' AND (filename NOT LIKE '%.txt' OR id = 13)"
).fetchall()]
conn.close()
proc = DocumentProcessor()
ok = fail = 0
for r in rows:
    p = Path(r["stored_path"])
    if not p.exists():
        print(f"원본 없음, 건너뜀: {r['filename']}"); fail += 1; continue
    proc.process(db, r["id"], p)
    doc = db.get_document(r["id"])
    ok += 1 if doc and doc["status"] == "reviewed" else 0
    print(f"재처리 {r['id']} {r['filename']} -> {doc and doc['status']}", flush=True)
print(f"재처리 완료 {ok}/{len(rows)}")
EOF

echo "[$(date +%T)] 조각 임베딩 재계산 (52)"
OMP_NUM_THREADS=10 MKL_NUM_THREADS=10 ZZAIMY_EMBED_DEVICE=cpu \
  .venv-train/bin/python scripts/52_embed_chunks.py

echo "[$(date +%T)] 플랫폼 재시작"
pkill -f "[z]zaimy.app.main"; sleep 1
(nohup ~/start-platform.sh > /tmp/platform.log 2>&1 &)
sleep 8
PASS=$(cat ~/.zzaimy-pass)
curl -sk -u "zzaimy:$PASS" -o /dev/null -w "대시보드: %{http_code}\n" https://localhost:8800/
echo "[$(date +%T)] 체인 완료"
