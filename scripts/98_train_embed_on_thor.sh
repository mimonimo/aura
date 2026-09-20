#!/bin/bash
# ZZAIMY-Embed 학습 — 토르 GPU 에서 KURE-v1 을 대조학습(MNR)하고 홀드아웃으로 개선폭을 잰다.
#
# 절대규칙 2(베이스라인 먼저)를 지킨다: 같은 홀드아웃에서 베이스 모델 점수를 먼저 재고,
# 학습본과 나란히 적는다. 문서 단위로 홀드아웃을 떼어 같은 문서의 질의가 양쪽에 가지 않게 한다.
# 상황으로 묻는 질의(97)는 학습에 쓰지 않고 평가에만 쓴다 — 용어를 모를 때의 성능을 정직하게 본다.
#
# 왜 토르인가: VM CPU 로는 한 에폭에 몇 시간이고 예전 실측에서 OOM 으로 죽었다(58 주석).
# 토르는 통합 메모리 122GB GPU 라 몇 분이면 끝난다. 서비스 포트는 열지 않고 배치로만 쓴다.
#
# 사용 (맥에서):  bash scripts/98_train_embed_on_thor.sh [에폭수]
set -euo pipefail
VM=aura@192.168.16.226
THOR=thor-03@211.170.162.121
TP=8022
IMAGE=ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor
EPOCHS="${1:-2}"
STAMP=$(date +%Y%m%d-%H%M%S)
WORK=/tmp/zz-embed-train-$STAMP

echo "[$(date +%T)] 1/3 VM 에서 학습 자료 내보내기 (정답은 본문으로 재결선)"
ssh "$VM" "cd ~/zzaimy-capstone && env PYTHONPATH=src .venv/bin/python - <<'PY'
import json
from pathlib import Path
from zzaimy.app.db import Database
from zzaimy.eval import retrieval_eval as rev

db = Database('data/platform/platform.db')
chunks = db.list_regulation_chunks()
matcher = rev.ChunkMatcher(chunks)
by_id = {c['id']: c for c in chunks}
out = {'chunks': [{'id': c['id'], 'doc_id': c['doc_id'],
                   'text': f\"{c['reg_title']} {c['heading']}\n{(c['content'] or '')[:1200]}\"}
                  for c in chunks], 'rows': []}

def add(path, tag):
    p = Path(path)
    if not p.exists():
        return
    rows = rev.load_rows(p)
    golds, _ = rev.resolve_golds(rows, matcher, None)
    for i, r in enumerate(rows):
        if not golds[i]:
            continue
        doc = by_id[sorted(golds[i])[0]]['doc_id']
        for qt in rev.QUERY_TYPES:
            q = (r.get(qt) or '').strip()
            if q:
                out['rows'].append({'q': q, 'gold': sorted(golds[i]), 'doc_id': doc, 'set': tag})

add('data/interim/synth_queries.jsonl', 'synth')
add('data/interim/synth_queries_paraphrase.jsonl', 'paraphrase')
print(json.dumps(out, ensure_ascii=False))
PY" | ssh -p $TP "$THOR" "mkdir -p $WORK && cat > $WORK/data.json && python3 -c \"
import json; d=json.load(open('$WORK/data.json'))
print('조각', len(d['chunks']), '질의', len(d['rows']))\""

echo "[$(date +%T)] 2/3 토르 GPU 학습·평가 (에폭 $EPOCHS)"
ssh -p $TP "$THOR" "docker run -i --rm --runtime nvidia --ipc host \
  -e EPOCHS=$EPOCHS -v \$HOME/zzaimy/models:/models -v $WORK:/work \
  --entrypoint python3 $IMAGE -" <<'PY'
import json, os, random, time
import torch, torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer

BASE, MAXLEN, BATCH = "/models/KURE-v1", 512, 24
EPOCHS = int(os.environ.get("EPOCHS", "2"))
HOLDOUT = 0.2
d = json.load(open("/work/data.json"))
chunks, rows = d["chunks"], d["rows"]
docs = sorted({c["doc_id"] for c in chunks})
rng = random.Random(20260920)
hold_docs = set(rng.sample(docs, max(1, int(len(docs) * HOLDOUT))))
train = [r for r in rows if r["set"] == "synth" and r["doc_id"] not in hold_docs]
ev = {tag: [r for r in rows if r["set"] == tag and r["doc_id"] in hold_docs] for tag in ("synth", "paraphrase")}
text_by_id = {c["id"]: c["text"] for c in chunks}
ids = [c["id"] for c in chunks]
pos_of = {cid: i for i, cid in enumerate(ids)}
print(f"문서 {len(docs)} (홀드아웃 {len(hold_docs)}) · 학습 질의 {len(train)} · 평가 합성 {len(ev['synth'])} 상황 {len(ev['paraphrase'])}", flush=True)

tok = AutoTokenizer.from_pretrained(BASE)

def encode(model, texts, bs=32, grad=False):
    outs = []
    for s in range(0, len(texts), bs):
        b = tok(texts[s:s + bs], padding=True, truncation=True, max_length=MAXLEN, return_tensors="pt").to("cuda")
        ctx = torch.enable_grad() if grad else torch.no_grad()
        with ctx:
            v = F.normalize(model(**b).last_hidden_state[:, 0], dim=-1)
        outs.append(v if grad else v.float().cpu())
    return torch.cat(outs)

def evaluate(model, tag):
    model.eval()
    V = encode(model, [text_by_id[i] for i in ids]).cuda()
    qs = [r["q"] for r in ev[tag]]
    if not qs:
        return {}
    Q = encode(model, qs).cuda()
    sims = Q @ V.T
    top = sims.topk(10, dim=1).indices.cpu().tolist()
    r1 = r5 = r10 = mrr = 0.0
    for row, r in zip(top, ev[tag]):
        gold = {pos_of[g] for g in r["gold"] if g in pos_of}
        hit = next((k for k, p in enumerate(row) if p in gold), None)
        if hit is not None:
            r10 += 1; mrr += 1 / (hit + 1)
            r5 += hit < 5; r1 += hit < 1
    n = len(qs)
    return {"R@1": r1 / n, "R@5": r5 / n, "R@10": r10 / n, "MRR@10": mrr / n, "n": n}

base = AutoModel.from_pretrained(BASE, dtype=torch.float32).cuda()
before = {t: evaluate(base, t) for t in ev}
print("베이스", json.dumps(before, ensure_ascii=False), flush=True)

# 헷갈리는 오답(hard negative) 고르기 — 베이스가 상위로 올리지만 정답이 아닌 조각.
# 한 묶음 안의 다른 정답만 오답으로 쓰면(in-batch) 쉬운 오답뿐이라 배울 것이 적다.
base.eval()
with torch.no_grad():
    V = encode(base, [text_by_id[i] for i in ids]).cuda()
    Q = encode(base, [r["q"] for r in train]).cuda()
    top = (Q @ V.T).topk(12, dim=1).indices.cpu().tolist()
for r, row in zip(train, top):
    gold = {pos_of[g] for g in r["gold"] if g in pos_of}
    negs = [ids[p] for p in row if p not in gold][:4]
    r["negs"] = negs
del Q, V
torch.cuda.empty_cache()
print(f"오답 표본 평균 {sum(len(r['negs']) for r in train) / max(1, len(train)):.1f}개", flush=True)

model = base
BATCH = 16                    # 묶음마다 오답 4배가 더 붙는다(실효 대조군 80)
opt = torch.optim.AdamW(model.parameters(), lr=1e-5, weight_decay=0.01)
steps = max(1, (len(train) // BATCH) * EPOCHS)
sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=1e-5, total_steps=steps, pct_start=0.1)
t0 = time.time()
for ep in range(EPOCHS):
    model.train()
    rng.shuffle(train)
    total = 0.0
    for s in range(0, len(train) - BATCH + 1, BATCH):
        batch = train[s:s + BATCH]
        docs_text = [text_by_id[sorted(r["gold"])[0]] for r in batch]
        neg_text = [text_by_id[n] for r in batch for n in r["negs"]]
        with torch.autocast("cuda", dtype=torch.bfloat16):
            A = encode(model, [r["q"] for r in batch], bs=BATCH, grad=True)
            P = encode(model, docs_text + neg_text, bs=BATCH, grad=True)
            logits = A @ P.T / 0.05
            labels = torch.arange(len(batch), device="cuda")
            loss = F.cross_entropy(logits, labels)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step(); sched.step(); opt.zero_grad(set_to_none=True)
        total += loss.item()
        if (s // BATCH) % 20 == 0:
            print(f"  에폭 {ep + 1} 스텝 {s // BATCH} 손실 {loss.item():.4f}", flush=True)
    print(f"에폭 {ep + 1} 평균 손실 {total / max(1, len(train) // BATCH):.4f} ({time.time() - t0:.0f}초)", flush=True)

after = {t: evaluate(model, t) for t in ev}
print("학습본", json.dumps(after, ensure_ascii=False), flush=True)
model.save_pretrained("/models/zzaimy-embed-v1")
tok.save_pretrained("/models/zzaimy-embed-v1")
json.dump({"base": before, "trained": after, "epochs": EPOCHS, "train_rows": len(train),
           "holdout_docs": len(hold_docs)}, open("/work/report.json", "w"), ensure_ascii=False, indent=2)
print("저장: /models/zzaimy-embed-v1", flush=True)
PY

echo "[$(date +%T)] 3/3 결과"
ssh -p $TP "$THOR" "cat $WORK/report.json"
