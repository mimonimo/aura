# 장비 접속 정보 불러오기 — 각 스크립트가 `. "$(dirname "$0")/_hosts.sh"` 로 부른다.
# 주소·계정·포트는 공개 저장소에 두지 않는다. 저장소 밖 값은 두 곳에서 읽는다:
#   .env.local          앱 설정(옛 이름 THOR02_SSH·THOR03_SSH 도 받는다)
#   configs/hosts.env   장비 접속 정보(형식은 configs/hosts.env.example)
# 이미 환경에 있는 값이 먼저다.
_ZZ_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

_zz_load() {
  local f="$1" line key
  [ -f "$f" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    key="${line%%=*}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [ -n "${!key+x}" ] && continue
    eval "export $line"
  done < "$f"
}

_zz_load "$_ZZ_ROOT/.env.local"
_zz_load "$_ZZ_ROOT/configs/hosts.env"
: "${ZZAIMY_VM_SSH:=}"
: "${ZZAIMY_THOR02_SSH:=${THOR02_SSH:-}}"
: "${ZZAIMY_THOR03_SSH:=${THOR03_SSH:-}}"
: "${ZZAIMY_THOR_SSH_PORT:=22}"
: "${ZZAIMY_DGX_SSH:=}"
: "${ZZAIMY_DGX_SSH_PORT:=22}"
export ZZAIMY_VM_SSH ZZAIMY_THOR02_SSH ZZAIMY_THOR03_SSH ZZAIMY_THOR_SSH_PORT ZZAIMY_DGX_SSH ZZAIMY_DGX_SSH_PORT

# need_host 변수이름... — 값이 비었으면 어디에 무엇을 넣을지 알려 주고 멈춘다.
# 명령 치환 $(...) 안이 아니라 그대로 부른다(그래야 스크립트 전체가 멈춘다).
need_host() {
  local name
  for name in "$@"; do
    if [ -z "${!name:-}" ]; then
      echo "접속 정보 $name 가 없습니다 — configs/hosts.env 에 $name=계정@주소 를 넣으십시오(형식: configs/hosts.env.example)." >&2
      exit 2
    fi
  done
}
