#!/bin/bash
# Label Studio 고정 토큰 프로비저닝 — 사람이 토큰을 복사해 붙여넣지 않게 한다.
#
# 운영 VM(aura@192.168.16.226)에서 저장소 루트 기준으로 실행한다. 하는 일:
#   1) 토큰 결정: 유닛의 LABEL_STUDIO_USER_TOKEN → 플랫폼 설정 labelstudio_token → 새로 생성
#   2) label-studio 유저 유닛에 고정 토큰·레거시 토큰 허용 env 기록 (바뀐 경우만 재시작)
#   3) Label Studio DB 의 부트스트랩 사용자 토큰을 그 값으로 맞춤 (ls-venv 의 Django ORM)
#      — --user-token 은 사용자를 처음 만들 때만 먹기 때문 (scripts/68_labelstudio_token.py 설명)
#   4) 플랫폼 설정 저장: labelstudio_token, 그리고 labelstudio_url 이 비어 있으면 이 VM 주소
#   5) GET /api/current-user/whoami 로 검증 → PASS / FAIL
# 몇 번을 다시 실행해도 같은 결과(멱등). 토큰은 어디에도 출력하지 않는다.
#
# 사용: bash scripts/68_labelstudio_token.sh
#   LS_URL=http://192.168.16.226:8080   플랫폼에 저장할 Label Studio 주소를 강제 (서버 이전 시)
#   LS_UNIT=…  LS_VENV=…                유닛 파일·ls-venv 경로
#                                       (기본 ~/.config/systemd/user/label-studio.service, ~/ls-venv)
set -euo pipefail
cd "$(dirname "$0")/.."

UNIT="${LS_UNIT:-$HOME/.config/systemd/user/label-studio.service}"
UNIT_NAME="$(basename "$UNIT")"
LS_PY="${LS_VENV:-$HOME/ls-venv}/bin/python"
HELPER="scripts/68_labelstudio_token.py"
PLAT_PY=".venv/bin/python"
PLATFORM_DB="data/platform/platform.db"

fail() { echo "FAIL: $*" >&2; exit 1; }

[ -f "$UNIT" ] || fail "유닛 파일이 없습니다: $UNIT"
[ -x "$LS_PY" ] || fail "ls-venv 의 python 이 없습니다: $LS_PY"
[ -x "$PLAT_PY" ] || fail "플랫폼 .venv 가 없습니다 — 저장소 루트에서 실행"
# 플랫폼 DB 가 없으면 빈 DB 를 만들게 되므로 멈춘다 (2026-09-01 사고의 교훈)
[ -f "$PLATFORM_DB" ] || fail "플랫폼 DB 가 없습니다: $PLATFORM_DB"

PORT="$("$LS_PY" "$HELPER" unit-port "$UNIT")"

# 1) 토큰 결정 — 이미 있는 것을 재사용해야 다시 실행해도 아무것도 안 바뀐다
TOKEN="$("$LS_PY" "$HELPER" unit-get "$UNIT")"
SRC="유닛"
if [ -z "$TOKEN" ]; then
  TOKEN="$(PYTHONPATH=src "$PLAT_PY" -c \
    'from zzaimy.app.db import Database; print(Database("data/platform/platform.db").get_setting("labelstudio_token"))')"
  SRC="플랫폼 설정"
fi
if [ -z "$TOKEN" ]; then
  TOKEN="$("$PLAT_PY" -c 'import secrets; print(secrets.token_hex(20))')"   # 40자 = DRF Token 키 길이
  SRC="새로 생성"
fi
echo "토큰: $SRC (값은 표시하지 않음)"

# 2) 유닛 기록 → 바뀐 경우(또는 죽어 있으면) 재시작
CHANGED="$(LS_TOKEN="$TOKEN" "$LS_PY" "$HELPER" unit-set "$UNIT")"
echo "유닛 $UNIT_NAME: $CHANGED"
systemctl --user daemon-reload
if [ "$CHANGED" = "changed" ] || ! systemctl --user is-active --quiet "$UNIT_NAME"; then
  systemctl --user restart "$UNIT_NAME"
  echo "$UNIT_NAME 재시작"
fi
code=000
for _ in $(seq 1 60); do   # 기동 대기 최대 2분 (마이그레이션 점검이 느리다)
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/health" || true)"
  [ "$code" != "000" ] && break
  sleep 2
done
[ "$code" != "000" ] || fail "Label Studio 가 응답하지 않습니다 (포트 $PORT) — journalctl --user -u $UNIT_NAME"

# 3) Label Studio 쪽: 사용자 토큰을 고정값으로, 조직의 레거시 토큰 인증을 켠다
LS_TOKEN="$TOKEN" "$LS_PY" "$HELPER" apply "$UNIT"

# 4) 플랫폼 설정 — 토큰, 그리고 주소가 비어 있으면 이 VM 의 첫 IP
LS_USERNAME="$("$LS_PY" "$HELPER" user-get "$UNIT")"
LS_TOKEN="$TOKEN" LS_USERNAME="$LS_USERNAME" LS_URL="${LS_URL:-}" LS_PORT="$PORT" PYTHONPATH=src "$PLAT_PY" - <<'PY'
import os
import subprocess

from zzaimy.app.db import Database

db = Database("data/platform/platform.db")
db.set_setting("labelstudio_token", os.environ["LS_TOKEN"])
if os.environ.get("LS_USERNAME"):
    db.set_setting("labelstudio_username", os.environ["LS_USERNAME"])   # 화면 표시용(아이디만)
url = os.environ.get("LS_URL") or db.get_setting("labelstudio_url")
if not url:
    ips = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()
    url = f"http://{ips[0] if ips else '127.0.0.1'}:{os.environ['LS_PORT']}"
db.set_setting("labelstudio_url", url.rstrip("/"))
print(f"플랫폼 설정: labelstudio_url={url.rstrip('/')}, labelstudio_token 저장")
PY

# 5) 검증 — 플랫폼이 실제로 쓰는 주소로 whoami
URL="$(PYTHONPATH=src "$PLAT_PY" -c \
  'from zzaimy.app.db import Database; print(Database("data/platform/platform.db").get_setting("labelstudio_url"))')"
code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Token $TOKEN" \
  "$URL/api/current-user/whoami" || true)"
if [ "$code" = "200" ]; then
  echo "PASS: whoami 200 ($URL) — /dev/data 에서 연결됨으로 보입니다"
  exit 0
fi
local_code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Token $TOKEN" \
  "http://127.0.0.1:$PORT/api/current-user/whoami" || true)"
echo "FAIL: whoami $code ($URL), 127.0.0.1:$PORT 로는 $local_code" >&2
if [ "$local_code" = "200" ]; then
  echo "  토큰은 맞습니다. 주소가 문제 — LS_URL=http://<VM IP>:$PORT bash $0 로 다시 실행" >&2
elif [ "$local_code" = "401" ]; then
  echo "  토큰 불일치 — journalctl --user -u $UNIT_NAME 확인 후 다시 실행" >&2
fi
exit 1
