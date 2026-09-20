#!/bin/bash
# ②ZZAIMY-Rerank 학습 — 토르 GPU 에서 bge-reranker-v2-m3 를 우리 질의로 맞춘다.
#
# 절대규칙 2: 베이스라인 먼저. 베이스라인은 운영과 같은 조건(GPU·512토큰·문서 이름 포함)에서
# 이미 쟀다(docs/llm-rerank-eval.md). 여기서도 같은 홀드아웃에서 베이스 점수를 먼저 재고 나란히 적는다.
#
# 학습 자료: 운영 하이브리드가 실제로 올려 준 상위 10 후보. 그 안의 정답이 양성,
# 나머지가 음성이다 — 검색이 헷갈리는 바로 그 후보들로 배운다(무작위 음성은 너무 쉽다).
# 문서 단위로 홀드아웃을 떼어 같은 문서의 질의가 학습·평가에 함께 들어가지 않게 한다.
# 상황 질의(97 로 만든 패러프레이즈)는 학습에 쓰지 않고 평가에만 쓴다.
#
# 사용 (맥에서):  bash scripts/104_train_rerank_on_thor.sh [에폭수]
set -euo pipefail
VM=aura@192.168.16.226
THOR=thor-03@211.170.162.121
TP=8022
IMAGE=ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor
EPOCHS="${1:-2}"
STAMP=$(date +%Y%m%d-%H%M%S)
WORK=/tmp/zz-rerank-train-$STAMP

echo "[$(date +%T)] 1/3 VM 에서 후보·정답 내보내기 (운영 하이브리드 후보 전체)"
ssh "$VM" "cd ~/zzaimy-capstone && env PYTHONPATH=src .venv/bin/python - <<'PY' > /tmp/rerank-train.json
import json
from pathlib import Path
from zzaimy.app import regulations as reg
from zzaimy.app.db import Database
from zzaimy.eval import retrieval_eval as rev

db = Database('data/platform/platform.db')
chunks = db.list_regulation_chunks()
by_id = {c['id']: c for c in chunks}
matcher = rev.ChunkMatcher(chunks)
out = []

def add(path, tag):
    p = Path(path)
    if not p.exists():
        return
    rows = rev.load_rows(p)
    golds, _ = rev.resolve_golds(rows, matcher, None)
    prod = rev.production_retrievers(db, chunks)
    for i, r in enumerate(rows):
        if not golds[i]:
            continue
        doc = by_id[sorted(golds[i])[0]]['doc_id']
        for qt in rev.QUERY_TYPES:
            q = (r.get(qt) or '').strip()
            if not q:
                continue
            # 운영과 같은 후보 수로 뽑는다(CANDIDATE_LIMIT=20). 리랭커가 실제로 보게 되는 목록이어야
            # 학습 조건이 맞는다 — 예전에는 10 이었다(2026-09-20 후보 확대 반영).
            cand = prod.hybrid(q, prod.lexical(q), prod.dense(q))[: reg.CANDIDATE_LIMIT]
            if not any(c in golds[i] for c in cand):
                continue                      # 후보 안에 정답이 없으면 리랭커가 할 일이 없다
            out.append({'q': q, 'set': tag, 'doc_id': doc,
                        'gold': sorted(golds[i]),
                        'cands': [{'id': cid,
                                   'text': f\"{by_id[cid]['reg_title']} {by_id[cid]['heading']}\n{(by_id[cid]['content'] or '')[:900]}\"}
                                  for cid in cand if cid in by_id]})

add('data/interim/synth_queries.jsonl', 'synth')
add('data/interim/synth_queries_paraphrase.jsonl', 'paraphrase')
print(json.dumps({'queries': out}, ensure_ascii=False))
PY"
ssh "$VM" "python3 -c \"
import json; d=json.load(open('/tmp/rerank-train.json'))
print('질의', len(d['queries']), '· 후보 평균', round(sum(len(q['cands']) for q in d['queries'])/max(1,len(d['queries'])), 1))\""

echo "[$(date +%T)] 2/3 토르로 옮기고 학습·평가 (에폭 $EPOCHS)"
ssh "$VM" "cat /tmp/rerank-train.json" | ssh -p $TP "$THOR" "mkdir -p $WORK && cat > $WORK/data.json"
ssh -p $TP "$THOR" "cat > $WORK/train.py" <<'PY'
import json, os, random, time
import torch
import torch.nn.functional as F
from transformers import AutoModelForSequenceClassification, AutoTokenizer

BASE = "/models/bge-reranker-v2-m3"
OUT = "/models/zzaimy-rerank-v1"
MAXLEN, EPOCHS, HOLDOUT = 512, int(os.environ.get("EPOCHS", "2")), 0.2
d = json.load(open("/work/data.json"))["queries"]
docs = sorted({q["doc_id"] for q in d})
rng = random.Random(20260920)
hold = set(rng.sample(docs, max(1, int(len(docs) * HOLDOUT))))
train = [q for q in d if q["set"] == "synth" and q["doc_id"] not in hold]
ev = {t: [q for q in d if q["set"] == t and q["doc_id"] in hold] for t in ("synth", "paraphrase")}
print(f"문서 {len(docs)} (홀드아웃 {len(hold)}) · 학습 질의 {len(train)}"
      f" · 평가 합성 {len(ev['synth'])} 상황 {len(ev['paraphrase'])}", flush=True)

tok = AutoTokenizer.from_pretrained(BASE)


def scores(model, q, texts):
    b = tok([q] * len(texts), texts, padding=True, truncation=True,
            max_length=MAXLEN, return_tensors="pt").to("cuda")
    return model(**b).logits.view(-1)


def evaluate(model, tag):
    model.eval()
    rows = ev[tag]
    if not rows:
        return {}
    r1 = r5 = mrr = 0.0
    with torch.no_grad():
        for q in rows:
            s = scores(model, q["q"], [c["text"] for c in q["cands"]]).float().cpu().tolist()
            order = sorted(range(len(s)), key=lambda i: (-s[i], i))
            gold = set(q["gold"])
            hit = next((k for k, i in enumerate(order) if q["cands"][i]["id"] in gold), None)
            if hit is not None:
                mrr += 1 / (hit + 1); r5 += hit < 5; r1 += hit < 1
    n = len(rows)
    return {"R@1": round(r1 / n, 4), "R@5": round(r5 / n, 4), "MRR": round(mrr / n, 4), "n": n}


model = AutoModelForSequenceClassification.from_pretrained(BASE, dtype=torch.float32).cuda()
before = {t: evaluate(model, t) for t in ev}
print("베이스", json.dumps(before, ensure_ascii=False), flush=True)

# 질의마다 후보 목록 전체에 소프트맥스 — 정답이 그 안에서 1위가 되도록 배운다(목록 단위 학습).
opt = torch.optim.AdamW(model.parameters(), lr=8e-6, weight_decay=0.01)
steps = max(1, len(train) * EPOCHS)
sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=8e-6, total_steps=steps, pct_start=0.1)
t0 = time.time()
for ep in range(EPOCHS):
    model.train()
    rng.shuffle(train)
    total, seen = 0.0, 0
    for n, q in enumerate(train):
        gold = set(q["gold"])
        pos = [i for i, c in enumerate(q["cands"]) if c["id"] in gold]
        if not pos:
            continue
        with torch.autocast("cuda", dtype=torch.bfloat16):
            s = scores(model, q["q"], [c["text"] for c in q["cands"]])
            target = torch.zeros(len(s), device="cuda")
            target[pos] = 1.0 / len(pos)
            loss = -(target * F.log_softmax(s.float(), dim=0)).sum()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step(); sched.step(); opt.zero_grad(set_to_none=True)
        total += loss.item(); seen += 1
        if n % 50 == 0:
            print(f"  에폭 {ep + 1} {n}/{len(train)} 손실 {loss.item():.4f}", flush=True)
    print(f"에폭 {ep + 1} 평균 손실 {total / max(1, seen):.4f} ({time.time() - t0:.0f}초)", flush=True)

after = {t: evaluate(model, t) for t in ev}
print("학습본", json.dumps(after, ensure_ascii=False), flush=True)
model.save_pretrained(OUT); tok.save_pretrained(OUT)
json.dump({"base": before, "trained": after, "epochs": EPOCHS, "train_queries": len(train),
           "holdout_docs": len(hold)}, open("/work/report.json", "w"), ensure_ascii=False, indent=2)
print("저장:", OUT, flush=True)
PY
ssh -p $TP "$THOR" "docker run -d --name zzaimy-rerank-train --rm --runtime nvidia --ipc host \
  -e EPOCHS=$EPOCHS -v \$HOME/zzaimy/models:/models -v $WORK:/work \
  --entrypoint python3 $IMAGE /work/train.py > /dev/null && echo '컨테이너 시작 — 기록 $WORK/train.log'"

echo "[$(date +%T)] 3/3 결과 기다리는 중 (끊겨도 토르에서 계속 돕니다: docker logs -f zzaimy-rerank-train)"
ssh -p $TP "$THOR" "docker logs -f zzaimy-rerank-train 2>&1 | tee $WORK/train.log | grep -E '문서 |에폭 .* 평균|베이스|학습본|저장:'" || true
ssh -p $TP "$THOR" "cat $WORK/report.json 2>/dev/null || echo '보고 없음 — docker logs zzaimy-rerank-train 확인'"
