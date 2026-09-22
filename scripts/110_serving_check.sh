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
# 8015 리랭커 · 8016 임베딩(학습본 서비스, /health) · 8001 Writer 27B(vLLM, /v1/models)
for port in 8015 8016 8001; do
  path=/health; [ "$port" = 8001 ] && path=/v1/models
  got=$(ssh -o BatchMode=yes "$VM" "curl -s -m 5 http://$THOR_HOST:$port$path" 2>/dev/null)
  if echo "$got" | grep -q '"ok":true\|"object":"list"'; then
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
import json
import os

from zzaimy.app import search_serving
from zzaimy.app.rerank import RERANK_MIN

KNOWN = {"bge-reranker-v2-m3": 0.271, "zzaimy-rerank-v1": 0.005}   # scripts/105 로 잰 값
floor = os.environ.get("ZZAIMY_RERANK_MIN", "").strip()
bad = 0

# 색인과 질의 임베딩 모델이 한 짝인지 — 어긋나면 오류 없이 엉뚱한 결과가 나온다(ADR-0021)
index_model = ""
try:
    with open("data/platform/chunk_embeddings.meta.json", encoding="utf-8") as fh:
        meta = json.load(fh)
    index_model = str(meta.get("model") or "")
    print(f"  조각 색인: {index_model} · 조각 {meta.get('n_chunks')}개 · 차원 {meta.get('dim')}")
except (OSError, ValueError):
    print("  조각 색인: 메타 파일 없음 — 색인 모델을 확인할 수 없습니다")
    bad = 1
for p in search_serving.status(ttl=0):
    mark = "정상" if p["ok"] else "멈춤"
    model = p["model"] or "-"
    print(f"  {p['label']}: {mark} · {p['where']} · {model} · {p['detail'] or '-'}")
    if not p["ok"]:
        bad = 1
    if p["key"] == "embed" and model != "-" and index_model:
        # 이름 표기가 조금 달라도(경로·대소문자) 서로를 포함하면 같은 모델로 본다
        a, b = model.lower(), index_model.lower()
        if a not in b and b not in a:
            print(f"     ! 색인({index_model})과 질의 모델({model})이 다릅니다 —"
                  f" 한쪽만 바꾸면 검색이 조용히 망가집니다(ADR-0021)")
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
