#!/bin/bash
# 재색인 — 규정 조각을 등록·해제·재분할한 뒤 조각 임베딩 색인을 맞춘다.
set -euo pipefail
cd "$(dirname "$0")/.."
# 오프라인 자립 — 모델은 로컬 캐시에서만. 미설정 시 허브 접속 재시도로
# 수십 분을 허비한다 (VM은 외부 인터넷이 막혀 있다)
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
# 무거운 작업 동시 1개 강제 — 겹치면 즉시 종료 (메모리 포화 방지)
exec 9>/tmp/zzaimy-heavy.lock
flock -n 9 || { echo "다른 무거운 작업이 실행 중 — 중단"; exit 1; }

# 코어 절반만 사용 (전 코어를 쓰면 서버가 오래 응답하지 않을 수 있다)
NCPU=$(nproc 2>/dev/null || echo 8)
OMP=$(( NCPU / 2 )); [ "$OMP" -lt 1 ] && OMP=1
PY=.venv/bin/python
[ -x .venv-train/bin/python ] && PY=.venv-train/bin/python
# 임베딩 인터프리터에도 PostgreSQL 드라이버가 있어야 한다 — 없으면 무효화 표시를 지우기 전에 멈춘다.
env PYTHONPATH=src "$PY" -c 'from zzaimy.app.database_backend import connect; c=connect("data/platform/platform.db", readonly=True); c.execute("SELECT 1"); c.close()'

# 임베딩 서비스 주소(ZZAIMY_EMBED_URL) 등 운영 설정 — 앱이 띄운 경우에도 손으로 돌린 경우에도 같게
[ -f .env.local ] && { set -a; . ./.env.local; set +a; }

# 합성 질의(51)는 검색 평가(53)용이다 — 27B 로 조각마다 묻느라 몇 시간이 걸리고 서빙 장비를 붙잡으므로
# 색인 갱신에서는 기본으로 돌리지 않는다. 평가 질의까지 새로 만들 때만 ZZAIMY_REINDEX_SYNTH=1.
if [ "${ZZAIMY_REINDEX_SYNTH:-0}" = "1" ]; then
  echo "[$(date +%T)] 합성 질의 재생성(평가용)"
  env PYTHONPATH=src nice -n 10 .venv/bin/python scripts/51_synth_queries.py
fi

# 조각 벡터 — 서빙 장비의 운영 학습본으로, 글이 그대로인 조각은 재사용(52). 앱은 색인 파일이 바뀌면
# 다음 검색에서 다시 읽으므로 재시작하지 않는다.
echo "[$(date +%T)] 규정 조각 임베딩 갱신"
env PYTHONPATH=src OMP_NUM_THREADS=$OMP MKL_NUM_THREADS=$OMP nice -n 10 "$PY" scripts/52_embed_chunks.py
rm -f data/platform/.reindex-needed
echo REINDEX_DONE
