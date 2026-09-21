#!/bin/bash
# 학습본을 서빙 장비로 옮기고 서비스까지 바꾼다 — 사람이 손으로 이어 붙이던 단계를 한 번에.
#
# 흐름: (학습 장비에서 복사) → 서빙 서비스 교체 → 임베딩이면 재색인 → 리랭커면 근거 하한 재측정
#      → VM 설정 갱신 → 서비스 재시작 → 점검(110)
#
# 왜: 학습이 끝나도 서빙까지 손으로 옮기면 "학습은 했는데 서비스는 옛 모델" 상태가 남는다.
# 하한(RERANK_MIN)·색인처럼 모델에 딸린 값도 같이 바뀌어야 하는데 그걸 잊기 쉽다.
#
# 사용 (맥에서):
#   bash scripts/116_ship_and_serve.sh rerank zzaimy-rerank-v2            # 토르에 이미 있는 학습본
#   bash scripts/116_ship_and_serve.sh embed  zzaimy-embed-v3
#   bash scripts/116_ship_and_serve.sh rerank zzaimy-rerank-v2 --from dgx-01@211.170.162.110:~/models
#   bash scripts/116_ship_and_serve.sh writer zzaimy-writer-v1 --from dgx-01@211.170.162.110:~/models   # 토르 02·03 둘 다
set -euo pipefail
VM=aura@192.168.16.226
THOR=thor-03@211.170.162.121
TP=8022
KIND="${1:?rerank | embed 중 하나}"
NAME="${2:?학습본 폴더 이름 (예: zzaimy-rerank-v2)}"
FROM=""
[ "${3:-}" = "--from" ] && FROM="${4:?원본 주소}"

case "$KIND" in
  rerank) PORT=8017; ENV_KEY=ZZAIMY_RERANK_URL; PATH_SUFFIX=/score ;;
  embed)  PORT=8018; ENV_KEY=ZZAIMY_EMBED_URL;  PATH_SUFFIX=/embed ;;
  writer) ;;
  *) echo "종류는 rerank · embed · writer" >&2; exit 2 ;;
esac

# ③Writer 학습본(bf16 병합본 — 양자화 판은 젯슨 vLLM 에서 적재 실패, ADR-0022) → 토르 02·03 둘 다.
# 토르 02 = 대화·초안, 토르 03 = 반입 검토·판독. 같은 판을 두 대에 올려야 '뒤죽박죽'이 안 된다.
# 젯슨은 큰 파일을 받은 뒤 페이지 캐시가 CUDA 메모리를 막는다(K-47) — 서빙 전에 CUDA 가용 메모리를
# 재고 모자라면 root 한 줄을 안내하고 멈춘다(같은 인자로 다시 돌리면 복사는 건너뛴다).
if [ "$KIND" = "writer" ]; then
  IMAGE=ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor
  for HOST in thor-02 thor-03; do
    case "$HOST" in thor-02) THOR=thor-02@211.170.162.120 ;; thor-03) THOR=thor-03@211.170.162.121 ;; esac
    echo "[$(date +%T)] $HOST 1/3 학습본 준비 — ~/zzaimy/models/$NAME"
    if ! ssh -p $TP "$THOR" "test -d ~/zzaimy/models/$NAME"; then
      [ -n "$FROM" ] || { echo "$HOST 에 학습본이 없고 --from 도 없습니다" >&2; exit 3; }
      # 학습 장비 → 맥 → 서빙 장비 (두 장비가 서로 직접 닿지 않아도 된다)
      ssh "${FROM%%:*}" "cd ${FROM#*:} && tar -cf - $NAME" | ssh -p $TP "$THOR" "mkdir -p ~/zzaimy/models && cd ~/zzaimy/models && tar -xf -"
    fi
    need_gb=$(ssh -p $TP "$THOR" "du -sb ~/zzaimy/models/$NAME | awk '{printf \"%d\", \$1/1073741824 + 20}'")
    free_gb=$(ssh -p $TP "$THOR" "docker run --rm --runtime nvidia --entrypoint python3 $IMAGE -c 'import torch; print(int(torch.cuda.mem_get_info()[0]/2**30))' 2>/dev/null | tail -1")
    echo "   CUDA 가용 ${free_gb}GB · 필요 ${need_gb}GB(가중치 + 20)"
    if [ "${free_gb:-0}" -lt "$need_gb" ]; then
      echo "   페이지 캐시가 CUDA 메모리를 막고 있습니다. $HOST 에서 root 로 실행한 뒤 같은 명령을 다시 돌리십시오:" >&2
      echo "     sudo sync && sudo sysctl vm.drop_caches=3" >&2
      exit 5
    fi
    echo "[$(date +%T)] $HOST 2/3 서빙 교체 (:8001)"
    HOST=$HOST MODEL=/models/$NAME PORT=8001 bash "$(dirname "$0")/126_serve_writer.sh" | tail -3
    ssh "$VM" "curl -s -m 5 http://${THOR#*@}:8001/v1/models" | grep -q zzaimy-writer \
      || { echo "$HOST 새 서빙이 응답하지 않습니다" >&2; exit 4; }
    echo "[$(date +%T)] $HOST 3/3 완료"
  done
  echo "역할은 화면의 '단계별 모델' 그대로다(문서 작업 → 토르 02, 검토·판독 → 토르 03). 채택 판단은 검토·판독 대결로."
  bash "$(dirname "$0")/110_serving_check.sh" | tail -3
  exit 0
fi

if [ -n "$FROM" ]; then
  echo "[$(date +%T)] 1/6 학습 장비에서 서빙 장비로 옮기기 — $FROM/$NAME"
  ssh -p $TP "$THOR" "mkdir -p ~/zzaimy/models"
  # 학습 장비 → 맥 → 서빙 장비 (두 장비가 서로 직접 닿지 않아도 된다)
  tmp=$(mktemp -d)
  scp -q -r "$FROM/$NAME" "$tmp/"
  scp -q -r "$tmp/$NAME" -P $TP "$THOR:~/zzaimy/models/"
  rm -rf "$tmp"
else
  echo "[$(date +%T)] 1/6 이미 서빙 장비에 있는 학습본을 쓴다 — ~/zzaimy/models/$NAME"
fi
ssh -p $TP "$THOR" "test -d ~/zzaimy/models/$NAME" || { echo "학습본을 찾지 못했습니다: $NAME" >&2; exit 3; }

echo "[$(date +%T)] 2/6 서비스 올리기 — 포트 $PORT"
if [ "$KIND" = "rerank" ]; then
  MODEL=/models/$NAME bash "$(dirname "$0")/102_serve_reranker_on_thor.sh" $PORT >/dev/null
else
  MODEL=/models/$NAME bash "$(dirname "$0")/103_serve_embed_on_thor.sh" $PORT >/dev/null
fi
ssh "$VM" "curl -s -m 5 http://211.170.162.121:$PORT/health" | grep -q '"ok":true' \
  || { echo "새 서비스가 응답하지 않습니다" >&2; exit 4; }

if [ "$KIND" = "embed" ]; then
  echo "[$(date +%T)] 3/6 색인 다시 만들기 (모델이 바뀌면 벡터 공간이 달라진다)"
  MODEL=/models/$NAME OUT_NAME=chunk_embeddings.$NAME.npz bash "$(dirname "$0")/96_embed_on_thor.sh" --apply >/dev/null
else
  echo "[$(date +%T)] 3/6 색인은 그대로 (리랭커는 색인을 바꾸지 않는다)"
fi

echo "[$(date +%T)] 4/6 VM 설정 갱신"
ssh "$VM" "cd ~/zzaimy-capstone && sed -i 's|^$ENV_KEY=.*|$ENV_KEY=http://211.170.162.121:$PORT$PATH_SUFFIX|' .env.local \
  && grep -q '^$ENV_KEY=' .env.local || echo '$ENV_KEY=http://211.170.162.121:$PORT$PATH_SUFFIX' >> ~/zzaimy-capstone/.env.local"

if [ "$KIND" = "rerank" ]; then
  echo "[$(date +%T)] 5/6 근거 하한 재측정 — 모델마다 눈금이 다르다"
  FLOOR=$(ssh "$VM" "cd ~/zzaimy-capstone && set -a; . ./.env.local; set +a; \
    env PYTHONPATH=src .venv/bin/python scripts/105_rerank_floor.py --sample 150 2>/dev/null \
    | sed -n 's/^권하는 하한(RERANK_MIN) \\([0-9.]*\\).*/\\1/p'")
  if [ -n "$FLOOR" ]; then
    ssh "$VM" "cd ~/zzaimy-capstone && sed -i 's|^ZZAIMY_RERANK_MIN=.*|ZZAIMY_RERANK_MIN=$FLOOR|' .env.local \
      && grep -q '^ZZAIMY_RERANK_MIN=' .env.local || echo 'ZZAIMY_RERANK_MIN=$FLOOR' >> ~/zzaimy-capstone/.env.local"
    echo "   하한 $FLOOR 적용"
  else
    echo "   하한을 정하지 못했습니다 — 기존 값을 둡니다(scripts/105 로 직접 확인하십시오)"
  fi
else
  echo "[$(date +%T)] 5/6 하한은 리랭커에만 딸린 값이다 — 건너뜀"
fi

echo "[$(date +%T)] 6/6 재시작·점검"
ssh "$VM" "systemctl --user restart zzaimy.service" && sleep 6
bash "$(dirname "$0")/110_serving_check.sh"
