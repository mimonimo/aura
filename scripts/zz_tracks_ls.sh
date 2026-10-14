#!/bin/bash
# 학습 후보(두 묶음) → Label Studio 올리기를 주기로. 후보는 생성 타이머(zzaimy-track-generation, 5분)가 만들고,
# 올리기(scripts/sync_track_candidates.py)는 새 후보만 올리며 자체 잠금이 있어 반복해도 같은 건을 두 번 올리지 않는다.
# Label Studio 주소가 설정되지 않았으면 아무것도 하지 않는다. 결과는 data/training/tracks/labelstudio-status.json.
cd ~/zzaimy-capstone || exit 1
set -a; . ./.env.local; set +a
exec flock -n /tmp/zz_tracks_ls.lock timeout 900 scripts/zz_run.sh 2G env PYTHONPATH=src .venv/bin/python scripts/sync_track_candidates.py >> /tmp/zz_tracks_ls.log 2>&1
