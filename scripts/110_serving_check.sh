#!/bin/bash
# 서빙 점검 — 토르 서비스가 살아 있는지, VM 설정이 그걸 가리키는지, 하한이 모델과 맞는지 본다.
#
# 왜: 검색 품질이 이제 토르의 서비스 두 개에 걸려 있다(리랭커·질의 임베딩). 서비스가 죽으면
# VM 이 조용히 CPU 로 물러나 느려지고, 리랭커 모델만 바꾸고 하한(ZZAIMY_RERANK_MIN)을 안 고치면
# '근거 약함' 표시가 틀어진다. 사람 눈으로 확인하던 것을 한 번에 본다.
#
# 사용 (맥에서):  bash scripts/110_serving_check.sh
set -uo pipefail
VM=aura@192.168.16.226
THOR_HOST=211.170.162.121
fail=0

echo "[$(date +%T)] 1/3 토르 서비스"
for port in 8013 8014 8015; do
  got=$(ssh -o BatchMode=yes "$VM" "curl -s -m 5 http://$THOR_HOST:$port/health" 2>/dev/null)
  if echo "$got" | grep -q '"ok":true'; then
    echo "  $port  $got"
  else
    echo "  $port  응답 없음"
  fi
done

echo "[$(date +%T)] 2/3 VM 설정이 가리키는 곳"
ssh -o BatchMode=yes "$VM" 'cd ~/zzaimy-capstone && grep -E "^ZZAIMY_(RERANK|EMBED)" .env.local | sed "s/^/  /"'

echo "[$(date +%T)] 3/3 플랫폼이 실제로 쓰는 것 (앱과 같은 환경으로)"
# 점검 본문은 VM 에 파일로 두고 부른다 — 따옴표를 겹쳐 넘기면 이스케이프가 깨진다
ssh -o BatchMode=yes "$VM" "cat > /tmp/zz_serving_check.py" <<'PY'
import os

from zzaimy.app import search_serving
from zzaimy.app.rerank import RERANK_MIN

KNOWN = {"bge-reranker-v2-m3": 0.271, "zzaimy-rerank-v1": 0.005}   # scripts/105 로 잰 값
floor = os.environ.get("ZZAIMY_RERANK_MIN", "").strip()
bad = 0
for p in search_serving.status(ttl=0):
    mark = "정상" if p["ok"] else "멈춤"
    model = p["model"] or "-"
    print(f"  {p['label']}: {mark} · {p['where']} · {model} · {p['detail'] or '-'}")
    if not p["ok"]:
        bad = 1
    if p["key"] != "rerank":
        continue
    if model in KNOWN:
        now = float(floor) if floor else RERANK_MIN
        if abs(now - KNOWN[model]) > 1e-9:
            print(f"     ! 하한이 이 모델에 맞지 않습니다: 지금 {now} · 잰 값 {KNOWN[model]}"
                  f" — scripts/105 로 다시 재고 .env.local 을 갱신하십시오")
            bad = 1
    elif model != "-":
        print("     ! 처음 보는 리랭커 모델입니다 — scripts/105 로 하한을 재십시오")
        bad = 1
raise SystemExit(bad)
PY
ssh -o BatchMode=yes "$VM" 'cd ~/zzaimy-capstone && set -a; . ./.env.local 2>/dev/null; set +a; env PYTHONPATH=src .venv/bin/python /tmp/zz_serving_check.py' || fail=1

if [ "$fail" = 0 ]; then echo "점검 통과"; else echo "점검에서 문제를 찾았습니다 (위 ! 줄)"; fi
exit $fail
