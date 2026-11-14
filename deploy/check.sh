#!/usr/bin/env bash
# AURA 점검 — 서비스·화면·DB·모델 서버·디스크·백업·주기 작업을 한 번에 보고 ✓/✗ 로 적는다. 문제가 있으면 끝 코드 1.
#
#   bash deploy/check.sh            # 전부
#   bash deploy/check.sh --quick    # 서비스·화면·DB 만
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
set -a; . ./.env.local 2>/dev/null; set +a
QUICK=0; [ "${1:-}" = --quick ] && QUICK=1
BAD=0
row() { if [ "$2" = ok ]; then printf "  ✓ %-22s %s\n" "$1" "$3"; else printf "  ✗ %-22s %s\n" "$1" "$3"; BAD=1; fi; }
opt() { if [ "$2" = ok ]; then printf "  ✓ %-22s %s\n" "$1" "$3"; else printf "  · %-22s %s\n" "$1" "$3"; fi; }

echo "AURA 점검 $(date '+%F %T') · $(git log --oneline -1 2>/dev/null | cut -c1-60)"
s=$(systemctl --user is-active zzaimy.service 2>/dev/null); row "웹 서버 서비스" "$([ "$s" = active ] && echo ok)" "${s:-없음}"
for u in webhwp-tomcat webhwp-filter; do
  systemctl --user cat $u.service >/dev/null 2>&1 && { s=$(systemctl --user is-active $u.service); row "기안기 $u" "$([ "$s" = active ] && echo ok)" "$s"; }
done
SCHEME=http; [ -n "${ZZAIMY_TLS_CERT:-}" ] && SCHEME=https
code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 "$SCHEME://127.0.0.1:${ZZAIMY_PORT:-8800}/login")
row "로그인 화면" "$([ "$code" = 200 ] && echo ok)" "HTTP $code"
db=$(PYTHONPATH=src .venv/bin/python - <<'PY' 2>&1 | tail -1
import os
from pathlib import Path
from zzaimy.app.db import Database
d = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or "data/platform/platform.db"))
d.get_setting("_check", "")
print("ok", "PostgreSQL" if os.environ.get("ZZAIMY_DATABASE_URL") else "SQLite", "· 설정 읽기 됨")
PY
)
row "DB" "$([[ "$db" == ok* ]] && echo ok)" "${db#ok }"
[ $QUICK = 1 ] && exit $BAD

# 모델 서버 — 화면에 등록한 LLM 연결과 검색 서빙(임베딩·리랭커)
PYTHONPATH=src .venv/bin/python - <<'PY'
import json, os, urllib.request
from pathlib import Path
from zzaimy.app import sysconf
from zzaimy.app.db import Database
from zzaimy.generate import llm_connections as lc
db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or "data/platform/platform.db"))
sysconf.apply_all(db)
lc.configure(Path("data/platform/llm_connections.json"))
def get(url):
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            return json.loads(r.read().decode() or "{}")
    except Exception as e:
        return {"error": type(e).__name__}
conns = lc.list_public()
if not conns:
    print("  ✗ LLM 연결              없음 — /dev/tools 「LLM 연결」 에서 GPU 서버 추가")
for c in conns:
    j = get(c["base_url"].rstrip("/") + "/models")
    n = len(j.get("data") or [])
    print(f"  {'✓' if n else '✗'} LLM {c['name'][:18]:<18} {'모델 ' + str(n) + '개' if n else j.get('error', '응답 없음')}")
for key, name in (("ZZAIMY_EMBED_URL", "임베딩 서버"), ("ZZAIMY_RERANK_URL", "리랭커 서버")):
    u = os.environ.get(key, "").strip()
    if not u:
        print(f"  · {name:<22} 정하지 않음(앱 서버 CPU)")
        continue
    base = u.rstrip("/").removesuffix("/embed").removesuffix("/score")
    j = get(base + "/health")
    print(f"  {'✓' if j.get('ok') else '✗'} {name:<22} {j.get('model') or j.get('error', '응답 없음')}")
PY
[ $? = 0 ] || BAD=1

use=$(df -P "$ROOT" | awk 'NR==2{gsub("%","",$5); print $5}')
row "디스크" "$([ "$use" -lt 90 ] && echo ok)" "사용 ${use}% · 남음 $(df -h "$ROOT" | awk 'NR==2{print $4}')"
mem=$(free -m | awk '/^Mem:/{printf "%d", $7*100/$2}')
row "메모리" "$([ "$mem" -gt 10 ] && echo ok)" "쓸 수 있음 ${mem}%"
B="${AURA_BACKUP_DIR:-$HOME/aura-backups}"
if [ -f "$B/.last_ok" ]; then
  age=$(( ( $(date +%s) - $(cat "$B/.last_ok") ) / 3600 ))
  row "마지막 백업" "$([ $age -lt 30 ] && echo ok)" "${age}시간 전 ($B)"
else
  row "마지막 백업" "" "없음 — bash deploy/backup.sh (cron 은 매일 02:30)"
fi
crontab -l 2>/dev/null | grep -q "# >>> AURA" && opt "주기 작업(cron)" ok "설치됨" || opt "주기 작업(cron)" no "AURA 블록 없음 — deploy/install.sh"
src=${ZZAIMY_ARCHIVE_SOURCE:-}
opt "원본 폴더" "$([ -n "$src$(grep -s '^ZZAIMY_DGX_SSH=' configs/hosts.env)" ] && echo ok)" "${src:-원본 서버 또는 정하지 않음}"
err=$(grep -cE "Traceback|Error" /tmp/zz_sync.log 2>/dev/null || echo 0)
opt "원본 갱신 기록" "$([ "${err:-0}" = 0 ] && echo ok)" "/tmp/zz_sync.log 오류 줄 ${err:-0}"
exit $BAD
