#!/bin/bash
# ①ZZAIMY-Embed v2 — 오답을 '운영 하이브리드가 실제로 올린 후보'에서 뽑아 다시 학습한다.
#
# v1 의 실패에서 배운 것(2026-09-20): 조밀 검색 단독 홀드아웃은 올랐는데(R@5 0.722→0.811)
# 운영 하이브리드에서는 이득이 사라졌다. 어휘 검색이 이미 잡던 건들만 나아진 것이다.
# 그래서 이번에는 어휘·조밀을 융합한 뒤 남은 후보(= 어휘가 못 걸러 준 경쟁자)를 오답으로 쓴다.
# 평가도 조밀 단독이 아니라 '후보 20 안에 정답이 들어오는 비율'로 본다 — 리랭커가 못 살리는
# 그 천장이 검색의 몫이다.
#
# 자료는 104 가 쓰는 것과 같은 내보내기(질의·정답·하이브리드 후보)를 재사용한다.
# 사용 (맥에서):  bash scripts/108_train_embed_v2_on_thor.sh [에폭수]
set -euo pipefail
VM=aura@192.168.16.226
THOR=thor-03@211.170.162.121
TP=8022
IMAGE=ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor
EPOCHS="${1:-2}"
STAMP=$(date +%Y%m%d-%H%M%S)
WORK=/tmp/zz-embed2-$STAMP

echo "[$(date +%T)] 1/3 VM 에서 질의·정답·후보 내보내기"
ssh "$VM" "cd ~/zzaimy-capstone && env PYTHONPATH=src .venv/bin/python - <<'PY' > /tmp/embed2-train.json
import json
from pathlib import Path
from zzaimy.app.db import Database
from zzaimy.eval import retrieval_eval as rev

db = Database('data/platform/platform.db')
chunks = db.list_regulation_chunks()
by_id = {c['id']: c for c in chunks}
matcher = rev.ChunkMatcher(chunks)
prod = rev.production_retrievers(db, chunks)
out = []

def text_of(c):
    return f\"{c['reg_title']} {c['heading']}\n{(c['content'] or '')[:1200]}\"

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
            if not q:
                continue
            cand = prod.hybrid(q, prod.lexical(q), prod.dense(q))[:20]
            out.append({'q': q, 'set': tag, 'doc_id': doc,
                        'gold': sorted(golds[i]),
                        'negs': [c for c in cand if c not in golds[i]][:6]})

add('data/interim/synth_queries.jsonl', 'synth')
add('data/interim/synth_queries_paraphrase.jsonl', 'paraphrase')
print(json.dumps({'queries': out,
                  'chunks': [{'id': c['id'], 'doc_id': c['doc_id'], 'text': text_of(c)} for c in chunks]},
                 ensure_ascii=False))
PY"
ssh "$VM" "python3 -c \"
import json; d=json.load(open('/tmp/embed2-train.json'))
print('질의', len(d['queries']), '· 조각', len(d['chunks']),
      '· 오답 평균', round(sum(len(q['negs']) for q in d['queries'])/max(1,len(d['queries'])), 1))\""

echo "[$(date +%T)] 2/3 토르 GPU 학습 (에폭 $EPOCHS)"
ssh "$VM" "cat /tmp/embed2-train.json" | ssh -p $TP "$THOR" "mkdir -p $WORK && cat > $WORK/data.json"
ssh -p $TP "$THOR" "cat > $WORK/train.py" <<'PY'
import json, os, random, time
import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer

BASE, OUT = "/models/KURE-v1", "/models/zzaimy-embed-v2"
MAXLEN, BATCH, EPOCHS, HOLDOUT = 512, 12, int(os.environ.get("EPOCHS", "2")), 0.2
d = json.load(open("/work/data.json"))
rows, chunks = d["queries"], d["chunks"]
text_by_id = {c["id"]: c["text"] for c in chunks}
ids = [c["id"] for c in chunks]
pos_of = {cid: i for i, cid in enumerate(ids)}
docs = sorted({c["doc_id"] for c in chunks})
rng = random.Random(20260920)
hold = set(rng.sample(docs, max(1, int(len(docs) * HOLDOUT))))
train = [r for r in rows if r["set"] == "synth" and r["doc_id"] not in hold and r["negs"]]
ev = {t: [r for r in rows if r["set"] == t and r["doc_id"] in hold] for t in ("synth", "paraphrase")}
print(f"문서 {len(docs)} (홀드아웃 {len(hold)}) · 학습 질의 {len(train)}"
      f" · 평가 합성 {len(ev['synth'])} 상황 {len(ev['paraphrase'])}", flush=True)

tok = AutoTokenizer.from_pretrained(BASE)


def encode(model, texts, bs=24, grad=False):
    outs = []
    for s in range(0, len(texts), bs):
        b = tok(texts[s:s + bs], padding=True, truncation=True, max_length=MAXLEN,
                return_tensors="pt").to("cuda")
        ctx = torch.enable_grad() if grad else torch.no_grad()
        with ctx:
            v = F.normalize(model(**b).last_hidden_state[:, 0], dim=-1)
        outs.append(v if grad else v.float().cpu())
    return torch.cat(outs)


def evaluate(model, tag):
    """조밀 단독 지표 + 상위 20 안에 정답이 드는 비율(후보 천장의 조밀 축 몫)."""
    model.eval()
    rows_ = ev[tag]
    if not rows_:
        return {}
    V = encode(model, [text_by_id[i] for i in ids]).cuda()
    Q = encode(model, [r["q"] for r in rows_]).cuda()
    top = (Q @ V.T).topk(20, dim=1).indices.cpu().tolist()
    r1 = r5 = r20 = mrr = 0.0
    for row, r in zip(top, rows_):
        gold = {pos_of[g] for g in r["gold"] if g in pos_of}
        hit = next((k for k, p in enumerate(row) if p in gold), None)
        if hit is not None:
            r20 += 1
            mrr += 1 / (hit + 1) if hit < 10 else 0.0
            r5 += hit < 5; r1 += hit < 1
    n = len(rows_)
    return {"R@1": round(r1 / n, 4), "R@5": round(r5 / n, 4), "R@20": round(r20 / n, 4),
            "MRR@10": round(mrr / n, 4), "n": n}


model = AutoModel.from_pretrained(BASE, dtype=torch.float32).cuda()
before = {t: evaluate(model, t) for t in ev}
print("베이스", json.dumps(before, ensure_ascii=False), flush=True)

opt = torch.optim.AdamW(model.parameters(), lr=1e-5, weight_decay=0.01)
steps = max(1, (len(train) // BATCH) * EPOCHS)
sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=1e-5, total_steps=steps, pct_start=0.1)
t0 = time.time()
for ep in range(EPOCHS):
    model.train()
    rng.shuffle(train)
    total, seen = 0.0, 0
    for s in range(0, len(train) - BATCH + 1, BATCH):
        batch = train[s:s + BATCH]
        docs_text = [text_by_id[sorted(r["gold"])[0]] for r in batch]
        neg_text = [text_by_id[n] for r in batch for n in r["negs"] if n in text_by_id]
        with torch.autocast("cuda", dtype=torch.bfloat16):
            A = encode(model, [r["q"] for r in batch], bs=BATCH, grad=True)
            P = encode(model, docs_text + neg_text, bs=BATCH, grad=True)
            logits = A @ P.T / 0.05
            loss = F.cross_entropy(logits, torch.arange(len(batch), device="cuda"))
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step(); sched.step(); opt.zero_grad(set_to_none=True)
        total += loss.item(); seen += 1
        if (s // BATCH) % 20 == 0:
            print(f"  에폭 {ep + 1} 스텝 {s // BATCH} 손실 {loss.item():.4f}", flush=True)
    print(f"에폭 {ep + 1} 평균 손실 {total / max(1, seen):.4f} ({time.time() - t0:.0f}초)", flush=True)

after = {t: evaluate(model, t) for t in ev}
print("학습본", json.dumps(after, ensure_ascii=False), flush=True)
model.save_pretrained(OUT); tok.save_pretrained(OUT)
json.dump({"base": before, "trained": after, "epochs": EPOCHS, "train_rows": len(train),
           "holdout_docs": sorted(hold)}, open("/work/report.json", "w"), ensure_ascii=False, indent=2)
print("저장:", OUT, flush=True)
PY
ssh -p $TP "$THOR" "docker run -d --name zzaimy-embed2-train --rm --runtime nvidia --ipc host \
  -e EPOCHS=$EPOCHS -v \$HOME/zzaimy/models:/models -v $WORK:/work \
  --entrypoint python3 $IMAGE /work/train.py > /dev/null && echo '컨테이너 시작 — $WORK'"

echo "[$(date +%T)] 3/3 결과 기다리는 중 (docker logs -f zzaimy-embed2-train)"
ssh -p $TP "$THOR" "docker logs -f zzaimy-embed2-train 2>&1 | tee $WORK/train.log | grep -E '문서 |에폭 .* 평균|베이스|학습본|저장:'" || true
ssh -p $TP "$THOR" "cat $WORK/report.json 2>/dev/null | head -40 || echo '보고 없음'"
