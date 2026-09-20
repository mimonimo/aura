#!/bin/bash
# ②ZZAIMY-Rerank 베이스라인 — 계획서 장비 구성대로 GPU 에서 잰다.
#
# 왜 이 스크립트인가: 리랭커(bge-reranker-v2-m3)를 VM CPU 에서 재면 질의당 3.75초라
# 운영에 쓸 수 없는 조건의 숫자가 나온다(2026-09-20 실측). 파인튜닝 뒤 개선폭을 주장하려면
# 베이스라인도 실제 서빙 조건(GPU)에서 재야 한다 — 절대규칙 2.
# 검색·정답 결선은 운영 코드(retrieval_eval)를 그대로 쓰고, 점수만 토르 GPU 에 맡긴다.
#
# 흐름:  VM(후보 뽑기) → 맥 → 토르 GPU(점수) → 맥 → VM(지표 계산·보고)
# 사용 (맥에서):  bash scripts/101_rerank_eval_on_thor.sh [질의파일] [표본수]
#   예) bash scripts/101_rerank_eval_on_thor.sh "" 150
#       bash scripts/101_rerank_eval_on_thor.sh data/interim/synth_queries_paraphrase.jsonl 150
set -euo pipefail
VM=aura@192.168.16.226
THOR=thor-03@211.170.162.121
TP=8022
IMAGE=ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor
MODEL="${MODEL:-/models/bge-reranker-v2-m3}"
QUERIES="${1:-}"
SAMPLE="${2:-150}"
STAMP=$(date +%Y%m%d-%H%M%S)
WORK=/tmp/zz-rerank-$STAMP

echo "[$(date +%T)] 1/3 VM 에서 후보 뽑기 (운영 하이브리드 상위 10)"
ssh "$VM" "cd ~/zzaimy-capstone && env PYTHONPATH=src QUERIES='$QUERIES' SAMPLE='$SAMPLE' .venv/bin/python - > /tmp/rerank-pairs.json <<'PY'
import json, os, random
from pathlib import Path
from zzaimy.app.db import Database
from zzaimy.eval import retrieval_eval as rev

db = Database('data/platform/platform.db')
chunks = db.list_regulation_chunks()
by_id = {c['id']: c for c in chunks}
rows = rev.load_rows(Path(os.environ['QUERIES']) if os.environ.get('QUERIES') else rev.QUERIES_PATH)
golds_all, _ = rev.resolve_golds(rows, rev.ChunkMatcher(chunks), None)
pairs = [(t, golds_all[i]) for i, r in enumerate(rows) if golds_all[i]
         for qt in rev.QUERY_TYPES if (t := (r.get(qt) or '').strip())]
idx = sorted(random.Random(rev.SEED).sample(range(len(pairs)), min(int(os.environ['SAMPLE']), len(pairs))))
prod = rev.production_retrievers(db, chunks)
out = []
for i in idx:
    q, gold = pairs[i]
    cand = prod.hybrid(q, prod.lexical(q), prod.dense(q))[: rev.TOP_K]
    out.append({'q': q, 'gold': sorted(gold), 'cands': [
        {'id': cid, 'text': f\"{by_id[cid]['reg_title']} {by_id[cid]['heading']}\n{(by_id[cid]['content'] or '')[:900]}\"}
        for cid in cand if cid in by_id]})
print(json.dumps({'embedding': prod.meta['embedding_active'], 'queries': out}, ensure_ascii=False))
PY"

echo "[$(date +%T)] 2/3 토르 GPU 로 점수 매기기 — $MODEL"
ssh "$VM" "cat /tmp/rerank-pairs.json" | ssh -p $TP "$THOR" "mkdir -p $WORK && cat > $WORK/pairs.json"
ssh -p $TP "$THOR" "cat > $WORK/score.py" <<'PY'
import json, os, time
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

MODEL = os.environ.get("MODEL", "/models/bge-reranker-v2-m3")
d = json.load(open("/work/pairs.json"))
tok = AutoTokenizer.from_pretrained(MODEL)
model = AutoModelForSequenceClassification.from_pretrained(MODEL, dtype=torch.float16).cuda().eval()
t0 = time.time()
out = []
with torch.no_grad():
    for item in d["queries"]:
        pairs = [(item["q"], c["text"]) for c in item["cands"]]
        if not pairs:
            out.append([]); continue
        b = tok([p[0] for p in pairs], [p[1] for p in pairs], padding=True, truncation=True,
                max_length=512, return_tensors="pt").to("cuda")
        s = model(**b).logits.view(-1).float().cpu().tolist()
        out.append(s)
n = max(1, len(d["queries"]))
json.dump({"scores": out, "sec_per_query": round((time.time() - t0) / n, 3),
           "model": MODEL}, open("/work/scores.json", "w"))
print(f"질의 {n}건 · 질의당 {(time.time() - t0) / n:.3f}초", flush=True)
PY
ssh -p $TP "$THOR" "docker run --rm --runtime nvidia --ipc host -e MODEL=$MODEL \
  -v \$HOME/zzaimy/models:/models -v $WORK:/work --entrypoint python3 $IMAGE /work/score.py" 2>&1 | grep -v Warning
ssh -p $TP "$THOR" "cat $WORK/scores.json" | ssh "$VM" "cat > /tmp/rerank-scores.json"

echo "[$(date +%T)] 3/3 VM 에서 지표 계산 (운영 평가 코드와 같은 지표)"
ssh "$VM" "cd ~/zzaimy-capstone && env PYTHONPATH=src .venv/bin/python - <<'PY'
import json
from zzaimy.eval import retrieval_eval as rev

pairs = json.load(open('/tmp/rerank-pairs.json'))
sc = json.load(open('/tmp/rerank-scores.json'))
golds = [set(item['gold']) for item in pairs['queries']]
base = [[c['id'] for c in item['cands']] for item in pairs['queries']]
ranked = []
for ids, scores in zip(base, sc['scores']):
    order = sorted(range(len(ids)), key=lambda i: (-scores[i], i)) if scores else []
    ranked.append([ids[i] for i in order])
print('임베딩', pairs['embedding'], '· 질의', len(base), '· 모델', sc['model'])
print('하이브리드    ', rev.metrics(base, golds))
print('GPU 리랭커    ', rev.metrics(ranked, golds), '· 질의당', sc['sec_per_query'], '초')
PY"
