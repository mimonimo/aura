#!/usr/bin/env bash
# AURA 백업 — DB 덤프(날짜별, 최근 N개) + 데이터 폴더 거울(바뀐 것만 더함) + 설정 파일. cron 이 매일 02:30 에 부른다.
#
#   bash deploy/backup.sh                      # $AURA_BACKUP_DIR(기본 ~/aura-backups) 에
#   AURA_BACKUP_DIR=/mnt/backup AURA_BACKUP_KEEP=14 bash deploy/backup.sh
#
# 되살리기: bash deploy/restore.sh <날짜>  (예: 20261115-0230)
# 거울(data-mirror)은 지운 파일을 따라 지우지 않는다 — 실수로 지운 원본도 남는다. 공간이 모자라면 오래된 덤프부터 지운다.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
set -a; . ./.env.local; set +a
DEST="${AURA_BACKUP_DIR:-$HOME/aura-backups}"
KEEP="${AURA_BACKUP_KEEP:-7}"
TS=$(date +%Y%m%d-%H%M)
mkdir -p "$DEST/db" "$DEST/config" "$DEST/data-mirror"
exec 9>/tmp/aura_backup.lock
flock -n 9 || { echo "$(date '+%F %T') 다른 백업이 도는 중 — 건너뜀"; exit 0; }
echo "$(date '+%F %T') 백업 시작 → $DEST"

# 1) DB — PostgreSQL 은 앱 스키마만(pg_dump -Fc), SQLite 는 온라인 백업 API
if [ -n "${ZZAIMY_DATABASE_URL:-}" ]; then
  pg_dump --format=custom --no-owner ${ZZAIMY_DATABASE_SCHEMA:+--schema="$ZZAIMY_DATABASE_SCHEMA"} \
    --file="$DEST/db/aura-$TS.dump.part" "${ZZAIMY_DATABASE_URL%%\?*}"
  mv "$DEST/db/aura-$TS.dump.part" "$DEST/db/aura-$TS.dump"
else
  .venv/bin/python - "$DEST/db/aura-$TS.sqlite" <<'PY'
import sqlite3, sys
src = sqlite3.connect("data/platform/platform.db"); dst = sqlite3.connect(sys.argv[1])
src.backup(dst); dst.close(); src.close()
PY
fi
echo "   DB: $(ls -sh "$DEST/db/" | grep "$TS" | awk '{print $1, $2}')"

# 2) 설정 — .env.local(비밀 값 포함, 0600)·계정·장비 접속 정보
install -m 600 .env.local "$DEST/config/env.local-$TS"
[ -f configs/hosts.env ] && install -m 600 configs/hosts.env "$DEST/config/hosts.env-$TS"
[ -f data/platform/accounts.json ] && install -m 600 data/platform/accounts.json "$DEST/config/accounts-$TS.json"

# 3) 데이터 거울 — 원본·생성 문서·장부. 다시 만들 수 있는 캐시·임시는 뺀다
rsync -a --exclude 'tmp/' --exclude 'cache/' --exclude '*.part' --exclude '.busy.json' data/ "$DEST/data-mirror/"
echo "   데이터 거울: $(du -sh "$DEST/data-mirror" | cut -f1)"

# 4) 오래된 것 지우기 — 덤프는 최근 KEEP 개, 설정은 회차마다 셋까지라 최근 KEEP×3 개
prune() { ls -1t "$1" | grep -v '\.part$' | tail -n +$(( $2 + 1 )) | while read -r f; do rm -f "$1/$f"; done; }
prune "$DEST/db" "$KEEP"
prune "$DEST/config" $(( KEEP * 3 ))
date +%s > "$DEST/.last_ok"
echo "$(date '+%F %T') 백업 끝 · 남은 공간 $(df -h "$DEST" | awk 'NR==2{print $4}')"
