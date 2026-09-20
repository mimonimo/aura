#!/bin/bash
# 규정 조각 임베딩을 젯슨 토르 GPU 로 계산한다 — 52_embed_chunks.py 의 GPU 판.
#
# 왜: VM CPU 로는 조각 3,603개에 한 시간이 넘게 걸린다(2026-09-20 실측). 토르는 서비스 포트를
# 새로 열지 않고 배치로만 쓴다 — 조각 글을 보내고, 벡터 파일을 받아 온다.
# 결과 형식(ids·vectors npz, meta json)과 글 구성(제목 표제\n본문 1200자)은 52 와 같다.
# 색인 메타의 모델 이름은 실제 쓴 모델을 적는다(후보 모델로 만들면 그 이름).
# 질의 임베딩은 서빙 서비스(103)가 같은 모델로 만든다 — 색인과 질의 모델이 다르면 공간이 어긋난다 — 같은 모델·같은 풀링(CLS+정규화)이라
# 벡터 공간이 같다. 끝에 VM CPU 로 표본을 다시 계산해 코사인 유사도로 대조한다.
#
# 준비: 토르 ~/zzaimy/models/KURE-v1 (VM 의 HF 캐시에서 옮긴 사본).
# 사용 (맥에서 — 맥만 VM·토르 양쪽에 키가 있다):
#   bash scripts/96_embed_on_thor.sh [--apply]      # --apply 없으면 대조까지만 하고 바꾸지 않는다
set -euo pipefail
VM=aura@192.168.16.226
THOR=thor-03@211.170.162.121
TP=8022
IMAGE=ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor
APPLY="${1:-}"
MODEL="${MODEL:-/models/KURE-v1}"          # 후보 모델로 색인을 만들 때 바꾼다(예: /models/zzaimy-embed-v1)
OUT_NAME="${OUT_NAME:-chunk_embeddings.npz}"   # VM 에 둘 이름 — 후보는 다른 이름으로 두고 평가한다
STAMP=$(date +%Y%m%d-%H%M%S)
WORK=/tmp/zz-embed-$STAMP

echo "[$(date +%T)] 1/4 VM 에서 조각 글 내보내기"
ssh "$VM" "cd ~/zzaimy-capstone && .venv/bin/python -c '
import json, sqlite3
c = sqlite3.connect(\"data/platform/platform.db\"); c.row_factory = sqlite3.Row
for r in c.execute(\"SELECT id, reg_title, heading, content FROM regulation_chunks ORDER BY id\"):
    print(json.dumps({\"id\": r[\"id\"], \"text\": f\"{r[\"reg_title\"]} {r[\"heading\"]}\n{r[\"content\"][:1200]}\"}, ensure_ascii=False))
'" | ssh -p $TP "$THOR" "mkdir -p $WORK && cat > $WORK/chunks.jsonl && wc -l < $WORK/chunks.jsonl"

echo "[$(date +%T)] 2/4 토르 GPU 로 임베딩"
ssh -p $TP "$THOR" "docker run -i --rm --runtime nvidia -e EMBED_MODEL=$MODEL -v \$HOME/zzaimy/models:/models:ro -v $WORK:/work \
  --entrypoint python3 $IMAGE -" <<'PY'
import json, time, numpy as np, torch
from transformers import AutoModel, AutoTokenizer
rows = [json.loads(l) for l in open("/work/chunks.jsonl", encoding="utf-8")]
import os
BASE = os.environ.get("EMBED_MODEL", "/models/KURE-v1")
tok = AutoTokenizer.from_pretrained(BASE)
model = AutoModel.from_pretrained(BASE, torch_dtype=torch.float32).cuda().eval()
print("모델", BASE, flush=True)
t0, out = time.time(), []
order = sorted(range(len(rows)), key=lambda i: len(rows[i]["text"]))   # 길이순 묶음 — 패딩 낭비를 줄인다
with torch.no_grad():
    for s in range(0, len(order), 32):
        idx = order[s:s + 32]
        b = tok([rows[i]["text"] for i in idx], padding=True, truncation=True, max_length=8192, return_tensors="pt").to("cuda")
        cls = model(**b).last_hidden_state[:, 0]                       # 풀링 = CLS (1_Pooling 설정)
        v = torch.nn.functional.normalize(cls, dim=-1).float().cpu().numpy()
        out += list(zip(idx, v))
out.sort()
vecs = np.stack([v for _, v in out]).astype(np.float32)
np.savez_compressed("/work/chunk_embeddings.npz", ids=np.array([r["id"] for r in rows]), vectors=vecs)
print(f"조각 {len(rows)} · {vecs.shape} · {time.time() - t0:.1f}초")
PY

echo "[$(date +%T)] 3/4 VM 으로 받아 CPU 표본과 대조"
ssh -p $TP "$THOR" "cat $WORK/chunk_embeddings.npz" | ssh "$VM" "cat > /tmp/$OUT_NAME"
if [ "$MODEL" = "/models/KURE-v1" ]; then
  # 베이스 모델일 때만 VM CPU 계산과 대조한다(같은 모델이어야 맞춰 볼 수 있다)
  ssh "$VM" "cd ~/zzaimy-capstone && HF_HUB_OFFLINE=1 OMP_NUM_THREADS=2 .venv/bin/python - <<'PYV'
import random, sqlite3, numpy as np
from sentence_transformers import SentenceTransformer
d = np.load('/tmp/$OUT_NAME'); ids, V = list(d['ids']), d['vectors']
c = sqlite3.connect('data/platform/platform.db'); c.row_factory = sqlite3.Row
rows = {r['id']: r for r in c.execute('SELECT id, reg_title, heading, content FROM regulation_chunks')}
pick = random.Random(7).sample(range(len(ids)), 20)
m = SentenceTransformer('nlpai-lab/KURE-v1', device='cpu')
texts = [f\"{rows[ids[i]]['reg_title']} {rows[ids[i]]['heading']}\n{rows[ids[i]]['content'][:1200]}\" for i in pick]
cpu = m.encode(texts, normalize_embeddings=True)
cos = [float(cpu[k] @ V[i]) for k, i in enumerate(pick)]
print('대조 코사인 최소 %.5f · 평균 %.5f' % (min(cos), sum(cos) / len(cos)))
raise SystemExit(0 if min(cos) > 0.999 else 1)
PYV"
else
  echo "후보 모델($MODEL) — 베이스와 벡터가 달라 대조는 생략합니다. 평가로 판단합니다."
  ssh "$VM" "ls -la /tmp/$OUT_NAME | awk '{print \$5, \$9}'"
fi

if [ "$APPLY" = "--apply" ]; then
  echo "[$(date +%T)] 4/4 적용 — 옛 색인은 백업"
  ssh "$VM" "cd ~/zzaimy-capstone && cp data/platform/chunk_embeddings.npz data/platform/backup/chunk_embeddings-$STAMP.npz \
    && mv /tmp/$OUT_NAME data/platform/chunk_embeddings.npz \
    && MODEL_NAME='$MODEL' .venv/bin/python -c 'import json, os, numpy as np; d = np.load(\"data/platform/chunk_embeddings.npz\"); \
json.dump({\"model\": os.environ[\"MODEL_NAME\"].rsplit(\"/\", 1)[-1], \"n_chunks\": int(len(d[\"ids\"])), \"dim\": int(d[\"vectors\"].shape[1]), \"device\": \"thor-gpu\"}, \
open(\"data/platform/chunk_embeddings.meta.json\", \"w\"))' \
    && rm -f data/platform/.reindex-needed && systemctl --user restart zzaimy.service && echo 적용·재시작 완료"
else
  echo "[$(date +%T)] 4/4 미리보기 — 적용하려면 --apply"
fi
ssh -p $TP "$THOR" "rm -rf $WORK"
