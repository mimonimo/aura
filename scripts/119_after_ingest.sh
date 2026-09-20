#!/bin/bash
# 반입 뒤 마무리 — 검토 채우기 → 개체 추출 → 색인 재계산 → 점검.
#
# 왜 묶는가: 반입이 끝나면 늘 같은 네 가지를 해야 하는데, 하나를 빠뜨리면 조용히 어긋난다
# (색인을 안 만들면 검색이 옛 조각을 가리키고, 개체를 안 뽑으면 그래프가 비어 보인다).
#
# 사용 (맥에서):  bash scripts/119_after_ingest.sh
set -uo pipefail
VM=aura@192.168.16.226
fail=0

echo "[$(date +%T)] 1/4 검토 의견 채우기 (반입 중 놓친 것)"
ssh -o BatchMode=yes "$VM" 'cd ~/zzaimy-capstone && set -a; . ./.env.local 2>/dev/null; set +a;
  env PYTHONPATH=src .venv/bin/python scripts/118_fill_reviews.py --apply 2>&1 | tail -3' || fail=1

echo "[$(date +%T)] 2/4 개체 추출 (지식 그래프)"
ssh -o BatchMode=yes "$VM" 'cd ~/zzaimy-capstone &&
  env PYTHONPATH=src .venv/bin/python scripts/70_extract_entities.py 2>&1 | tail -3' || fail=1

echo "[$(date +%T)] 3/4 색인 재계산 (토르 GPU)"
MODEL=/models/zzaimy-embed-v2 OUT_NAME=chunk_embeddings.fresh.npz \
  bash "$(dirname "$0")/96_embed_on_thor.sh" --apply 2>&1 | tail -3 || fail=1

echo "[$(date +%T)] 4/4 반입 점검·서빙 점검"
ssh -o BatchMode=yes "$VM" 'cd ~/zzaimy-capstone &&
  env PYTHONPATH=src .venv/bin/python scripts/84_ingest_audit.py --limit 8 2>&1 | tail -12' || fail=1
bash "$(dirname "$0")/110_serving_check.sh" 2>&1 | tail -6 || fail=1

[ "$fail" = 0 ] && echo "마무리 완료" || echo "일부 단계에서 문제가 있었습니다 (위 출력 확인)"
exit $fail
