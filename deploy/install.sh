#!/usr/bin/env bash
# AURA 앱 서버 한 번에 설치 — 새 서버(Ubuntu 22.04·24.04)에서 이 저장소를 받은 계정으로 실행한다.
#
#   bash deploy/install.sh                       # 대화 없이 기본값(127.0.0.1:8800, PostgreSQL 이 있으면 PostgreSQL)
#   bash deploy/install.sh --port 443 --host 0.0.0.0 --tls-cert /etc/ssl/aura.crt --tls-key /etc/ssl/aura.key \
#        --public-url https://aura.example.ac.kr --db local
#   bash deploy/install.sh --offline /media/usb/aura-bundle      # 인터넷 없는 서버 — deploy/bundle.sh 로 만든 묶음
#
# 하는 일(다시 돌려도 같은 결과): ① 점검 ② 파이썬 환경·패키지 ③ DB(PostgreSQL·pgvector 또는 시험용 SQLite)
#   ④ .env.local ⑤ 데이터 폴더 ⑥ CPU 비상용 검색 모델 ⑦ 한글 읽기 도구(kordoc, 선택) ⑧ 서비스(systemd 사용자 서비스)
#   ⑨ 주기 작업(cron: 원본 갱신·색인·백업) ⑩ 기동 확인과 첫 로그인 안내.
# sudo 가 필요한 일(apt 설치·PostgreSQL 역할 만들기·443 포트·재부팅 뒤 자동 시작)은 sudo 가 되면 하고, 안 되면 할 일을 알려 준다.
# 모델 서버(GPU)는 deploy/model-server/, 한글 웹 기안기는 deploy/webhwp/ 에서 따로 설치한다.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PORT=8800; HOST=127.0.0.1; TLS_CERT=""; TLS_KEY=""; PUBLIC_URL=""; DB="auto"; SCHEMA="aura_app_main"
OFFLINE=""; SERVICE=1; CRON=1; MODELS=1; KORDOC=1
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2;;
    --host) HOST="$2"; shift 2;;
    --tls-cert) TLS_CERT="$2"; shift 2;;
    --tls-key) TLS_KEY="$2"; shift 2;;
    --public-url) PUBLIC_URL="$2"; shift 2;;
    --db) DB="$2"; shift 2;;                    # auto | local | sqlite | postgresql://…
    --schema) SCHEMA="$2"; shift 2;;
    --offline) OFFLINE="$(cd "$2" && pwd)"; shift 2;;
    --no-service) SERVICE=0; shift;;
    --no-cron) CRON=0; shift;;
    --no-models) MODELS=0; shift;;
    --no-kordoc) KORDOC=0; shift;;
    -h|--help) sed -n '2,16p' "$0"; exit 0;;
    *) echo "모르는 옵션: $1 (--help)" >&2; exit 2;;
  esac
done

DONE=(); TODO=()
step() { echo; echo "── $*"; }
ok() { DONE+=("$*"); echo "   됨: $*"; }
todo() { TODO+=("$*"); echo "   남음: $*"; }
if [ "$(id -u)" = 0 ]; then SUDO=""; PGSU=(runuser -u postgres --); else SUDO=sudo; PGSU=(sudo -u postgres); fi
can_sudo() { [ -z "$SUDO" ] || sudo -n true 2>/dev/null; }
apt_get() { can_sudo && $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$@" >/dev/null; }

step "① 점검"
. /etc/os-release 2>/dev/null || true
echo "   OS: ${PRETTY_NAME:-알 수 없음} · 계정: $(id -un) · 자리: $ROOT"
[ "${ID:-}" = ubuntu ] || echo "   알림: Ubuntu 22.04·24.04 에서 시험했습니다. 다른 배포판은 패키지 이름이 다를 수 있습니다"
if [ "$HOST" != 127.0.0.1 ] && { [ -z "$TLS_CERT" ] || [ -z "$TLS_KEY" ]; }; then
  echo "127.0.0.1 밖으로 열려면 --tls-cert 와 --tls-key 가 필요합니다(앞단 프록시를 두면 --host 127.0.0.1)" >&2; exit 2
fi
PY=""
for c in python3.12 python3.13; do command -v $c >/dev/null && { PY=$(command -v $c); break; }; done
if [ -z "$PY" ] && [ -z "$OFFLINE" ] && can_sudo; then
  if [ "${VERSION_ID:-}" = "22.04" ]; then
    $SUDO apt-get update -q >/dev/null; apt_get software-properties-common
    $SUDO add-apt-repository -y ppa:deadsnakes/ppa >/dev/null
  fi
  $SUDO apt-get update -q >/dev/null; apt_get python3.12 python3.12-venv python3.12-dev && PY=$(command -v python3.12)
fi
[ -n "$PY" ] || { echo "Python 3.12 가 필요합니다. (Ubuntu 24.04: sudo apt install python3.12-venv)" >&2; exit 2; }
if ! "$PY" -c 'import venv, ensurepip' 2>/dev/null; then
  apt_get "$(basename "$PY")-venv" || true            # Ubuntu 의 python3.12 는 venv(ensurepip)가 따로 — python3.12-venv
  "$PY" -c 'import venv, ensurepip' 2>/dev/null || { echo "$PY 에 venv 가 없습니다: sudo apt install $(basename "$PY")-venv" >&2; exit 2; }
fi
ok "Python $("$PY" -V | cut -d' ' -f2)"

step "② 파이썬 환경·패키지"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
PIP=(.venv/bin/pip install -q)
if [ -n "$OFFLINE" ]; then
  PIP+=(--no-index --find-links "$OFFLINE/wheels")
else
  .venv/bin/pip install -q --upgrade pip
fi
"${PIP[@]}" -e ".[app,postgres]"
if [ "$MODELS" = 1 ]; then
  # CPU 비상용 임베딩·리랭커(서빙 서버가 꺼졌을 때) — torch 는 CPU 판(GPU 판은 수 GB)
  if [ -n "$OFFLINE" ]; then "${PIP[@]}" torch sentence-transformers
  else .venv/bin/pip install -q torch --index-url https://download.pytorch.org/whl/cpu && .venv/bin/pip install -q sentence-transformers; fi
fi
ok "패키지 설치(.venv)"

step "③ DB"
DATABASE_URL=""
if [ "$DB" = auto ]; then
  if command -v psql >/dev/null || can_sudo; then DB=local; else DB=sqlite; fi
fi
case "$DB" in
  sqlite) ok "시험용 SQLite(data/platform/platform.db) — 운영은 PostgreSQL 을 권합니다"; SCHEMA="";;
  local)
    if ! command -v psql >/dev/null; then
      apt_get postgresql postgresql-contrib || true
    fi
    command -v pg_isready >/dev/null && ! pg_isready -q 2>/dev/null && can_sudo && \
      { $SUDO systemctl start postgresql 2>/dev/null || $SUDO service postgresql start >/dev/null 2>&1 || true; }
    PGV=$(ls /usr/share/postgresql 2>/dev/null | sort -n | tail -1)
    [ -n "$PGV" ] && ! ls /usr/share/postgresql/$PGV/extension/vector.control >/dev/null 2>&1 && { apt_get "postgresql-$PGV-pgvector" || true; }
    ENVF=.env.local
    OLD_URL=$(sed -n 's/^ZZAIMY_DATABASE_URL=//p' $ENVF 2>/dev/null || true)
    if [ -n "$OLD_URL" ]; then
      DATABASE_URL="$OLD_URL"; ok "DB: 이미 정한 PostgreSQL 그대로"
    elif can_sudo && command -v psql >/dev/null; then
      ROLE="aura_$(id -un | tr -c 'a-z0-9_\n' '_')"; PW=$("$PY" -c 'import secrets;print(secrets.token_urlsafe(24))')
      "${PGSU[@]}" psql -qtAc "SELECT 1 FROM pg_roles WHERE rolname='$ROLE'" | grep -q 1 \
        && "${PGSU[@]}" psql -qc "ALTER ROLE $ROLE PASSWORD '$PW'" \
        || "${PGSU[@]}" psql -qc "CREATE ROLE $ROLE LOGIN PASSWORD '$PW'"
      "${PGSU[@]}" psql -qtAc "SELECT 1 FROM pg_database WHERE datname='aura'" | grep -q 1 || "${PGSU[@]}" createdb -O "$ROLE" aura
      "${PGSU[@]}" psql -q -d aura -c "CREATE EXTENSION IF NOT EXISTS vector" || todo "pgvector 확장 설치(sudo apt install postgresql-$PGV-pgvector)"
      "${PGSU[@]}" psql -q -d aura -c "CREATE SCHEMA IF NOT EXISTS $SCHEMA AUTHORIZATION $ROLE"
      DATABASE_URL="postgresql://$ROLE:$PW@127.0.0.1:5432/aura"
      ok "DB: PostgreSQL aura · 스키마 $SCHEMA · 역할 $ROLE"
    else
      todo "PostgreSQL 준비(sudo 필요) — 준비한 뒤 --db postgresql://계정:비밀번호@주소:5432/aura 로 다시 실행. 지금은 SQLite 로 띄웁니다"
      SCHEMA=""
    fi;;
  postgresql://*) DATABASE_URL="$DB"; ok "DB: 주어진 PostgreSQL";;
  *) echo "--db 는 auto·local·sqlite·postgresql://… 가운데 하나입니다" >&2; exit 2;;
esac
[ -n "$SCHEMA" ] && ! [[ "$SCHEMA" =~ ^aura_(app|test)_[a-z0-9_]{1,40}$ ]] && { echo "--schema 는 aura_app_<이름> 꼴이어야 합니다" >&2; exit 2; }

step "④ .env.local"
if [ -f .env.local ]; then
  cp -p .env.local ".env.local.bak-$(date +%Y%m%d%H%M%S)"
  BOOT=$(sed -n 's/^ZZAIMY_PASSWORD=//p' .env.local)
fi
BOOT=${BOOT:-$("$PY" -c 'import secrets;print(secrets.token_urlsafe(12))')}
DATA_DIR="$ROOT/data"
sed -e "s|@HOST@|$HOST|" -e "s|@PORT@|$PORT|" -e "s|@PUBLIC_URL@|$PUBLIC_URL|" -e "s|@TLS_CERT@|$TLS_CERT|" -e "s|@TLS_KEY@|$TLS_KEY|" \
    -e "s|@BOOT_PASSWORD@|$BOOT|" -e "s|@DATA_DIR@|$DATA_DIR|" -e "s|@DATABASE_URL@|$DATABASE_URL|" -e "s|@DATABASE_SCHEMA@|$SCHEMA|" \
    deploy/env.template | sed -E 's/^([A-Z_]+)=$/# \1=/' > .env.local.new
# 앞서 손으로 더한 값(틀에 없는 이름)은 그대로 이어 붙인다 — 다시 설치해도 지우지 않는다
if [ -f .env.local ]; then
  while IFS= read -r line; do
    k=${line%%=*}; [[ "$line" =~ ^[A-Z_][A-Z0-9_]*= ]] || continue
    grep -qE "^#? ?$k=" .env.local.new || echo "$line" >> .env.local.new
  done < .env.local
fi
mv .env.local.new .env.local && chmod 600 .env.local
ok ".env.local(0600) — 주소·키는 설치 뒤 화면 「서버 설정」 에서"

step "⑤ 데이터 폴더"
mkdir -p data/platform data/inbox data/tmp
ok "data/ — 원본·색인·생성 문서 자리(백업 대상)"

step "⑥ CPU 비상용 검색 모델"
if [ "$MODELS" = 1 ]; then
  if [ -n "$OFFLINE" ] && [ -d "$OFFLINE/hf" ]; then
    mkdir -p "$HOME/.cache/huggingface" && cp -rn "$OFFLINE/hf/." "$HOME/.cache/huggingface/"
    ok "모델 캐시를 묶음에서 복사"
  elif [ -z "$OFFLINE" ]; then
    .venv/bin/python - <<'PY' && ok "KURE-v1·bge-reranker-v2-m3 캐시" || todo "검색 모델 내려받기 실패 — 서빙 서버 주소를 「서버 설정」 에서 정하면 쓰지 않습니다"
from sentence_transformers import CrossEncoder, SentenceTransformer
SentenceTransformer("nlpai-lab/KURE-v1", device="cpu")
CrossEncoder("BAAI/bge-reranker-v2-m3", device="cpu")
PY
  fi
else
  todo "CPU 비상용 검색 모델 건너뜀(--no-models)"
fi

step "⑦ 한글 읽기 도구(kordoc)"
if [ "$KORDOC" = 1 ]; then
  K="$HOME/opt/kordoc"
  if [ -x "$K/node_modules/.bin/kordoc" ]; then ok "kordoc 있음"
  elif [ -n "$OFFLINE" ] && [ -d "$OFFLINE/kordoc" ]; then
    mkdir -p "$HOME/opt" && cp -r "$OFFLINE/kordoc" "$K"
    [ -d "$OFFLINE/node" ] && [ ! -x "$HOME/opt/node/bin/node" ] && cp -r "$OFFLINE/node" "$HOME/opt/node"
    ok "kordoc·Node.js 를 묶음에서 복사(~/opt)"
  elif command -v npm >/dev/null; then mkdir -p "$K" && (cd "$K" && npm install -s kordoc >/dev/null) && ok "kordoc 설치($K)"
  else todo "kordoc — Node.js 18+ 설치 뒤 mkdir -p ~/opt/kordoc && cd ~/opt/kordoc && npm install kordoc(.hwp 읽기에 필요)"; fi
fi

step "⑧ 서비스"
if [ "$SERVICE" = 1 ]; then
  U="$HOME/.config/systemd/user"; mkdir -p "$U"
  sed -e "s|@ROOT@|$ROOT|g" deploy/systemd/zzaimy.service.in > "$U/zzaimy.service"
  systemctl --user daemon-reload && systemctl --user enable -q zzaimy.service && ok "zzaimy.service(사용자 서비스)"
  if [ "$PORT" -lt 1024 ]; then
    REALPY=$(readlink -f .venv/bin/python)
    if can_sudo; then $SUDO setcap cap_net_bind_service=ep "$REALPY" && ok "$PORT 포트 열기 허용($REALPY)"
    else todo "$PORT 포트: sudo setcap cap_net_bind_service=ep $REALPY"; fi
  fi
  if loginctl show-user "$(id -un)" -p Linger 2>/dev/null | grep -q yes; then ok "재부팅 뒤 자동 시작(linger)"
  elif can_sudo; then $SUDO loginctl enable-linger "$(id -un)" && ok "재부팅 뒤 자동 시작(linger)"
  else todo "재부팅 뒤 자동 시작: sudo loginctl enable-linger $(id -un)"; fi
else
  todo "서비스 등록 건너뜀(--no-service) — 직접 띄우기: set -a; . ./.env.local; set +a; .venv/bin/python -m zzaimy.app.main"
fi

step "⑨ 주기 작업(cron)"
if [ "$CRON" = 1 ] && command -v crontab >/dev/null; then
  BLOCK=$(sed -e "s|@ROOT@|$ROOT|g" deploy/cron/aura.cron.in)
  { crontab -l 2>/dev/null | sed '/# >>> AURA/,/# <<< AURA/d'; echo "$BLOCK"; } | crontab -
  ok "cron: 원본 갱신·색인·정리·매일 백업(deploy/backup.sh)"
else
  todo "cron 건너뜀 — deploy/cron/aura.cron.in 을 crontab 에 넣는다"
fi

step "⑩ 기동 확인"
SCHEME=http; [ -n "$TLS_CERT" ] && SCHEME=https
if [ "$SERVICE" = 1 ]; then
  systemctl --user restart zzaimy.service
  code=000
  for i in $(seq 1 60); do
    code=$(curl -sk -o /dev/null -w '%{http_code}' "$SCHEME://127.0.0.1:$PORT/login" || true)
    [ "$code" = 200 ] && break; sleep 3
  done
  if [ "$code" = 200 ]; then ok "로그인 화면 응답(약 $((i * 3))초)"
  else todo "로그인 화면이 응답하지 않습니다 — journalctl --user -u zzaimy -n 50"; fi
fi

echo
echo "════════ 설치 결과"
for d in "${DONE[@]}"; do echo "  ✓ $d"; done
for t in "${TODO[@]+"${TODO[@]}"}"; do echo "  · $t"; done
PWF=$(ls data/platform/initial_dev_password.txt 2>/dev/null || true)
cat <<EOF

다음 순서:
  1. ${PUBLIC_URL:-$SCHEME://<서버 주소>:$PORT} 에 개발자 계정 zzdev 로 로그인${PWF:+ — 첫 비밀번호 파일: $PWF}
  2. 「도구·모델 연결」(/dev/tools) → LLM 연결에 GPU 서버 주소, 「서버 설정」 에 임베딩·리랭커·원본 폴더
  3. 「조직 관리」(/team) → 부서 만들기 → 전체 사용자 「일괄 등록」(엑셀·CSV)
  4. 한글 웹 기안기: bash deploy/webhwp/install.sh (한컴 패키지·라이선스 필요)
  5. 점검: bash deploy/check.sh · 업데이트: bash deploy/update.sh · 자세히: docs/ops/README.md
EOF
