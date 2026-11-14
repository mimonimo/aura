#!/usr/bin/env bash
# AURA 되살리기 — deploy/backup.sh 가 만든 날짜의 DB 덤프와 데이터 거울로 되돌린다.
#
#   bash deploy/restore.sh                    # 고를 수 있는 날짜 목록
#   bash deploy/restore.sh 20261115-0230      # 그 날짜로(확인을 묻는다)
#   bash deploy/restore.sh 20261115-0230 --yes --with-data   # 데이터 폴더도 거울에서 되돌림
#
# 되돌리기 전에 지금 상태를 한 번 더 백업한다(되돌린 것을 다시 되돌릴 수 있게). 서비스는 멈췄다가 다시 띄운다.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
set -a; . ./.env.local; set +a
DEST="${AURA_BACKUP_DIR:-$HOME/aura-backups}"
TS="${1:-}"; YES=0; DATA=0
for a in "${@:2}"; do case "$a" in --yes) YES=1;; --with-data) DATA=1;; esac; done
if [ -z "$TS" ]; then
  echo "고를 수 있는 날짜($DEST/db):"; ls -1t "$DEST/db" 2>/dev/null | sed -E 's/^aura-(.*)\.(dump|sqlite)$/  \1/'; exit 0
fi
F=$(ls "$DEST/db/aura-$TS".dump "$DEST/db/aura-$TS".sqlite 2>/dev/null | head -1 || true)
[ -n "$F" ] || { echo "그 날짜의 덤프가 없습니다: $TS" >&2; exit 2; }
echo "되돌릴 것: DB ← $F$( [ $DATA = 1 ] && echo " · 데이터 폴더 ← $DEST/data-mirror")"
if [ $YES != 1 ]; then read -r -p "지금 DB 를 이 백업으로 바꿉니다. 계속할까요? (yes) " ans; [ "$ans" = yes ] || exit 1; fi

echo "── 지금 상태를 먼저 백업"; bash deploy/backup.sh
echo "── 서비스 멈춤"; systemctl --user stop zzaimy.service 2>/dev/null || true
if [ "${F##*.}" = dump ]; then
  pg_restore --clean --if-exists --no-owner ${ZZAIMY_DATABASE_SCHEMA:+--schema="$ZZAIMY_DATABASE_SCHEMA"} \
    --dbname="${ZZAIMY_DATABASE_URL%%\?*}" "$F"
else
  cp "$F" data/platform/platform.db
fi
echo "   DB 되돌림"
if [ $DATA = 1 ]; then rsync -a "$DEST/data-mirror/" data/ && echo "   데이터 폴더 되돌림(거울에 없는 새 파일은 그대로 둠)"; fi
echo "── 서비스 다시 시작"; systemctl --user start zzaimy.service 2>/dev/null || true
bash deploy/check.sh --quick || true
