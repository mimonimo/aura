#!/bin/bash
# 배치 작업 실행기 — 메모리 상한(넘으면 그 작업만 죽는다)과 강제 종료 우선순위(웹·DB 보다 먼저)를 건다.
# 10/4 VM 메모리 부족: 크론 파이썬이 12~16GB 를 써 OOM 세 번, 23:32 에는 스왑 2GB 로 버티다 VM 전체가 멈췄다.
# 사용: zz_run.sh <상한, 예 24G> <명령...>   상한을 넘겨 죽으면 종료 코드 137 과 한 줄 기록을 남긴다.
cap="$1"; shift
echo 800 > /proc/self/oom_score_adj 2>/dev/null
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if systemd-run --user --scope -q true 2>/dev/null; then
    systemd-run --user --scope -q -p MemoryMax="$cap" -p MemorySwapMax=0 "$@"
else
    "$@"
fi
rc=$?
[ "$rc" = 137 ] && echo "$(date '+%F %T') 메모리 상한($cap)을 넘어 끝남: $*" >&2
exit $rc
