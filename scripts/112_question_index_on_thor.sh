#!/bin/bash
# 조각별 질문(111 산출물)을 토르 GPU 로 임베딩해 보조 색인을 만든다.
#
# 규칙: **조각 색인과 같은 모델로** 만들어야 한다(같은 벡터 공간이어야 질의와 비교된다).
# 기본은 운영 임베딩 모델(zzaimy-embed-v2). 결과는 VM 의 data/platform/question_embeddings.npz
# (--apply) 또는 /tmp (미리보기). 색인 형식: ids=질문 행 id, chunks=조각 id, vectors=(n,dim).
#
# 사용 (맥에서):  bash scripts/112_question_index_on_thor.sh [--apply]
set -euo pipefail
VM=aura@192.168.16.226
THOR=thor-03@211.170.162.121
TP=8022
IMAGE=ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor
MODEL="${MODEL:-/models/zzaimy-embed-v2}"
APPLY="${1:-}"
STAMP=$(date +%Y%m%d-%H%M%S)
WORK=/tmp/zz-qidx-$STAMP

echo "[$(date +%T)] 1/3 VM 에서 질문 내보내기"
ssh "$VM" "cd ~/zzaimy-capstone && .venv/bin/python -c '
import json, sqlite3
c = sqlite3.connect(\"data/platform/platform.db\"); c.row_factory = sqlite3.Row
rows = c.execute(\"SELECT id, chunk_id, question FROM chunk_questions ORDER BY id\").fetchall()
for r in rows:
    print(json.dumps({\"id\": r[\"id\"], \"chunk\": r[\"chunk_id\"], \"text\": r[\"question\"]}, ensure_ascii=False))
' > /tmp/questions.jsonl; wc -l < /tmp/questions.jsonl"
ssh "$VM" "cat /tmp/questions.jsonl" | ssh -p $TP "$THOR" "mkdir -p $WORK && cat > $WORK/questions.jsonl"

echo "[$(date +%T)] 2/3 토르 GPU 로 임베딩 — $MODEL"
ssh -p $TP "$THOR" "cat > $WORK/embed.py" <<'PY'
import json, os, time
import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer

MODEL = os.environ.get("MODEL", "/models/zzaimy-embed-v2")
rows = [json.loads(ln) for ln in open("/work/questions.jsonl", encoding="utf-8") if ln.strip()]
tok = AutoTokenizer.from_pretrained(MODEL)
model = AutoModel.from_pretrained(MODEL, dtype=torch.float32).cuda().eval()
t0, out = time.time(), []
with torch.no_grad():
    for s in range(0, len(rows), 64):
        b = tok([r["text"] for r in rows[s:s + 64]], padding=True, truncation=True,
                max_length=128, return_tensors="pt").to("cuda")
        v = F.normalize(model(**b).last_hidden_state[:, 0], dim=-1).float().cpu().numpy()
        out.append(v)
vecs = np.concatenate(out).astype(np.float32) if out else np.zeros((0, 1024), dtype=np.float32)
np.savez_compressed("/work/question_embeddings.npz",
                    ids=np.array([r["id"] for r in rows]),
                    chunks=np.array([r["chunk"] for r in rows]),
                    vectors=vecs,
                    model=np.array(MODEL.rsplit("/", 1)[-1]))   # 조각 색인과 같은 모델인지 확인용
print(f"질문 {len(rows)} · {vecs.shape} · {time.time() - t0:.1f}초 · 모델 {MODEL}")
PY
ssh -p $TP "$THOR" "docker run --rm --runtime nvidia --ipc host -e MODEL=$MODEL \
  -v \$HOME/zzaimy/models:/models -v $WORK:/work --entrypoint python3 $IMAGE /work/embed.py" 2>&1 | grep -v Warning

echo "[$(date +%T)] 3/3 VM 으로 받기"
ssh -p $TP "$THOR" "cat $WORK/question_embeddings.npz" | ssh "$VM" "cat > /tmp/question_embeddings.npz"
if [ "$APPLY" = "--apply" ]; then
  ssh "$VM" "cd ~/zzaimy-capstone && mv /tmp/question_embeddings.npz data/platform/question_embeddings.npz \
    && systemctl --user restart zzaimy.service && echo '적용·재시작 완료'"
else
  ssh "$VM" "ls -la /tmp/question_embeddings.npz | awk '{print \$5, \$9}'; echo '미리보기 — 적용하려면 --apply'"
fi
ssh -p $TP "$THOR" "rm -rf $WORK"
