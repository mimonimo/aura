#!/usr/bin/env bash
# AURA 업데이트 — 서버에서 새 판을 받아 적용하고, 진행 중인 요청이 끝난 뒤 다시 시작한다. 뜨지 않으면 앞 판으로 저절로 되돌린다.
#
#   bash deploy/update.sh                       # 원격 저장소(origin)의 main 최신으로
#   bash deploy/update.sh --ref v1.2.0          # 정한 태그·커밋으로(되돌리기에도 같은 방법)
#   bash deploy/update.sh --bundle aura-v1.2.0.tar.gz   # 인터넷 없는 서버 — deploy/bundle.sh 로 만든 묶음
#   bash deploy/update.sh --no-restart          # 받기만(다시 시작은 나중에)
#
# 개발 PC 에서 운영 서버로 밀 때는 scripts/99_deploy.sh --restart(같은 일을 SSH 로).
# 데이터(data/)·.env.local 은 바꾸지 않는다. DB 표는 앱이 뜰 때 스스로 맞춘다(CREATE IF NOT EXISTS·열 더하기).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
REF="main"; BUNDLE=""; RESTART=1; WAIT_MAX="${AURA_UPDATE_WAIT_MAX:-1800}"
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="$2"; shift 2;;
    --bundle) BUNDLE="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"; shift 2;;
    --no-restart) RESTART=0; shift;;
    -h|--help) sed -n '2,11p' "$0"; exit 0;;
    *) echo "모르는 옵션: $1" >&2; exit 2;;
  esac
done

if [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  echo "이 서버에서 고친 파일이 있습니다 — 덮어쓰지 않습니다(git status 로 확인 뒤 커밋하거나 되돌리세요):" >&2
  git status --short --untracked-files=no | head -10 >&2; exit 3
fi
OLD=$(git rev-parse HEAD)
OLD_PYPROJECT=$(git show HEAD:pyproject.toml | sha1sum)

echo "── 새 판 받기"
if [ -n "$BUNDLE" ]; then
  T=$(mktemp -d); tar -xzf "$BUNDLE" -C "$T"
  SRC=$(ls -d "$T"/aura-*/ | head -1)
  [ -d "$SRC/aura/.git" ] || { echo "묶음에 코드(aura/)가 없습니다" >&2; exit 2; }
  git fetch -q "$SRC/aura" "+refs/heads/*:refs/remotes/bundle/*" "+refs/tags/*:refs/tags/*"
  NEW=$(git rev-parse "bundle/main")
  WHEELS="$SRC/wheels"
else
  git remote get-url origin >/dev/null 2>&1 && git fetch -q --tags origin   # 원격이 없는 서버(폐쇄망)는 받아 둔 판·묶음으로
  NEW=$(git rev-parse -q --verify "origin/$REF^{commit}" 2>/dev/null || git rev-parse -q --verify "$REF^{commit}" || true)
  [ -n "$NEW" ] || { echo "판을 찾지 못했습니다: $REF" >&2; exit 2; }
  WHEELS=""
fi
[ "$NEW" = "$OLD" ] && { echo "이미 최신입니다: $(git log --oneline -1)"; exit 0; }
git checkout -q -B main "$NEW"
echo "   $(git log --oneline -1 "$OLD" | cut -c1-70)"
echo "→  $(git log --oneline -1 | cut -c1-70)"

if [ "$(git show HEAD:pyproject.toml | sha1sum)" != "$OLD_PYPROJECT" ]; then
  echo "── 패키지 목록이 바뀌어 다시 설치"
  if [ -n "$WHEELS" ]; then .venv/bin/pip install -q --no-index --find-links "$WHEELS" -e ".[app,postgres]"
  else .venv/bin/pip install -q -e ".[app,postgres]"; fi
fi
[ -n "${T:-}" ] && rm -rf "$T"
[ $RESTART = 1 ] || { echo "받기만 했습니다 — 다시 시작: systemctl --user restart zzaimy"; exit 0; }

echo "── 진행 중인 요청이 끝나기를 기다림(최대 ${WAIT_MAX}초)"
f=data/platform/.busy.json; t=0
while [ -f $f ] && [ $t -lt "$WAIT_MAX" ]; do
  st=$(python3 -c "import json,time;d=json.load(open('$f'));q=d['running']+d['waiting'];idle=time.time()-d['last_active'];fresh=time.time()-d['at']<30;print('busy' if fresh and (q or idle<90) else 'idle', q, int(idle))" 2>/dev/null || echo idle)
  set -- $st; [ "$1" = idle ] && break
  echo "   모델 호출 $2건 진행 중(마지막 호출 $3초 전) — ${t}초째"; sleep 15; t=$((t + 15))
done

started() {
  set -a; . ./.env.local; set +a
  local s=http; [ -n "${ZZAIMY_TLS_CERT:-}" ] && s=https
  for i in $(seq 1 40); do
    [ "$(curl -sk -o /dev/null -w '%{http_code}' "$s://127.0.0.1:${ZZAIMY_PORT:-8800}/login")" = 200 ] && return 0; sleep 3
  done
  return 1
}
echo "── 다시 시작"
systemctl --user restart zzaimy.service
if started; then
  echo "✓ 업데이트 끝: $(git log --oneline -1 | cut -c1-70)"
else
  echo "✗ 새 판이 뜨지 않아 앞 판으로 되돌립니다 — 원인: journalctl --user -u zzaimy -n 80" >&2
  git checkout -q -B main "$OLD"
  [ "$(git show HEAD:pyproject.toml | sha1sum)" != "$(git show "$NEW":pyproject.toml | sha1sum)" ] && .venv/bin/pip install -q -e ".[app,postgres]" || true
  systemctl --user restart zzaimy.service
  started && echo "   앞 판으로 돌아옴: $(git log --oneline -1 | cut -c1-70)" || echo "   앞 판도 뜨지 않습니다 — journalctl 확인" >&2
  exit 1
fi
