#!/bin/bash
# KURE-v2(다중 벡터, ColBERT 계열) 색인을 토르 03 GPU 로 만든다 — ADR-0022 의 "바꾼다면" 조건을 재기 위한 준비.
#
# 왜 별도 색인인가: 지금 운영 색인(96)은 조각당 벡터 하나(1024차원)를 코사인으로 견준다. KURE-v2 는 조각의
# 토큰마다 128차원 벡터를 내고 질의 토큰과 MaxSim(질의 토큰별 최대 유사도의 합)으로 점수를 낸다. 색인 형식·
# 서비스·점수 계산이 모두 다르므로 운영과 나란히 두고 같은 질의 세트로 잰다(129 서비스 + 53 측정).
#
# 인코딩은 PyLate 의 ColBERT 구현을 그대로 옮겼다(pylate 를 젯슨 컨테이너에 깔면 sentence-transformers 가
# 통째로 바뀌어 위험): 질의 = <cls> [Q] 토큰… <sep> + <mask> 로 64토큰까지 확장(확장 토큰은 어텐션에서 보이지
# 않음), 문서 = <cls> [D] 토큰… <sep>, 구두점 토큰 제외, Dense 3층(잔차) → 토큰별 정규화.
# 조각 글 구성(제목 표제\n본문 1200자)은 96 과 같다.
#
# 사용 (맥에서):  bash scripts/128_kure2_index_on_thor.sh
# 산출: 토르 ~/zzaimy/index/kure2/{tokens.npy(fp16 [N,L,128]), lens.npy, ids.npy, meta.json}
set -euo pipefail
VM=aura@192.168.16.226
THOR=thor-03@211.170.162.121
TP=8022
IMAGE=ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor
MODEL="${MODEL:-/models/KURE-v2}"
DOC_LEN="${DOC_LEN:-1024}"          # 조각은 1200자 안팎이라 8192 까지 갈 일이 없다 — 색인 크기를 위해 상한
OUT=/home/thor-03/zzaimy/index/kure2
WORK=/tmp/zz-kure2-$(date +%Y%m%d-%H%M%S)

echo "[$(date +%T)] 1/3 VM 에서 조각 글 내보내기 (96 과 같은 구성)"
ssh "$VM" "cd ~/zzaimy-capstone && .venv/bin/python -c '
import json, sqlite3
c = sqlite3.connect(\"data/platform/platform.db\"); c.row_factory = sqlite3.Row
for r in c.execute(\"SELECT id, reg_title, heading, content FROM regulation_chunks ORDER BY id\"):
    print(json.dumps({\"id\": r[\"id\"], \"text\": f\"{r[\"reg_title\"]} {r[\"heading\"]}\n{r[\"content\"][:1200]}\"}, ensure_ascii=False))
'" | ssh -p $TP "$THOR" "mkdir -p $WORK $OUT && cat > $WORK/chunks.jsonl && wc -l < $WORK/chunks.jsonl"

echo "[$(date +%T)] 2/3 토르 GPU 로 토큰 임베딩 (문서 상한 $DOC_LEN 토큰)"
ssh -p $TP "$THOR" "mkdir -p ~/zzaimy/serve && cat > ~/zzaimy/serve/colbert_encoder.py" <<'PY'
"""KURE-v2(PyLate ColBERT) 인코더 — pylate 없이 같은 계산을 한다. 색인(128)과 서비스(129)가 함께 쓴다."""
import json
import os

import torch
import torch.nn.functional as F
from safetensors.torch import load_file
from transformers import AutoModel, AutoTokenizer


class ColbertEncoder:
    def __init__(self, path: str, doc_len: int = 1024, device: str = "cuda"):
        cfg = json.load(open(os.path.join(path, "config_sentence_transformers.json")))
        self.q_len = int(cfg.get("query_length", 64))
        self.d_len = min(int(cfg.get("document_length", 8192)), doc_len)
        self.expand = bool(cfg.get("do_query_expansion", True))
        self.attend_expansion = bool(cfg.get("attend_to_expansion_tokens", False))
        self.tok = AutoTokenizer.from_pretrained(path)
        self.q_prefix = self.tok.convert_tokens_to_ids(cfg.get("query_prefix", "[Q] "))
        self.d_prefix = self.tok.convert_tokens_to_ids(cfg.get("document_prefix", "[D] "))
        assert self.q_prefix != self.tok.unk_token_id and self.d_prefix != self.tok.unk_token_id, "접두 토큰이 어휘에 없다"
        self.skip = torch.tensor(sorted({self.tok.convert_tokens_to_ids(w) for w in cfg.get("skiplist_words", [])}
                                        - {self.tok.unk_token_id}), device=device)
        self.mask_id = self.tok.mask_token_id
        self.pad_id = self.tok.pad_token_id
        self.device = device
        self.model = AutoModel.from_pretrained(path, dtype=torch.float32).to(device).eval()
        # Dense 3층 — pylate.models.Dense: linear(무편향) (+ 잔차: 차원이 같으면 항등, 다르면 residual 선형)
        self.dense = []
        for name in sorted(d for d in os.listdir(path) if d.endswith("_Dense")):
            c = json.load(open(os.path.join(path, name, "config.json")))
            w = load_file(os.path.join(path, name, "model.safetensors"))
            lin = torch.nn.Linear(c["in_features"], c["out_features"], bias=c.get("bias", False)).to(device)
            lin.weight.data.copy_(w["linear.weight"])
            if c.get("bias") and "linear.bias" in w:
                lin.bias.data.copy_(w["linear.bias"])
            res = None
            if c.get("use_residual") and c["in_features"] != c["out_features"]:
                res = torch.nn.Linear(c["in_features"], c["out_features"], bias=False).to(device)
                res.weight.data.copy_(w["residual.weight"])
            self.dense.append((lin, res, bool(c.get("use_residual"))))
        self.dim = self.dense[-1][0].out_features if self.dense else self.model.config.hidden_size

    def _project(self, h):
        for lin, res, use_res in self.dense:
            out = lin(h)
            if use_res:
                out = out + (res(h) if res is not None else h)
            h = out
        return h

    def _insert(self, ids, value):
        col = torch.full((ids.size(0), 1), value, dtype=ids.dtype, device=ids.device)
        return torch.cat([ids[:, :1], col, ids[:, 1:]], dim=1)

    @torch.no_grad()
    def encode_queries(self, texts: list[str]) -> torch.Tensor:
        """[B, q_len, dim] — 확장 토큰까지 전부 쓴다(정규화)."""
        pad_backup = self.tok.pad_token_id
        self.tok.pad_token_id = self.mask_id                       # 확장 = <mask> 로 채움 (pylate 와 같음)
        b = self.tok(texts, padding="max_length" if self.expand else True, truncation=True,
                     max_length=self.q_len - 1, return_tensors="pt")
        self.tok.pad_token_id = pad_backup
        ids = self._insert(b["input_ids"], self.q_prefix).to(self.device)
        att = self._insert(b["attention_mask"], 1).to(self.device)
        if self.attend_expansion:
            att = torch.ones_like(att)
        h = self.model(input_ids=ids, attention_mask=att).last_hidden_state
        return F.normalize(self._project(h), dim=-1)

    @torch.no_grad()
    def encode_docs(self, texts: list[str]) -> tuple[torch.Tensor, torch.Tensor]:
        """[B, L, dim] 과 유효 토큰 마스크 [B, L] — 패딩·구두점 토큰은 마스크 0."""
        b = self.tok(texts, padding=True, truncation=True, max_length=self.d_len - 1, return_tensors="pt")
        ids = self._insert(b["input_ids"], self.d_prefix).to(self.device)
        att = self._insert(b["attention_mask"], 1).to(self.device)
        h = self.model(input_ids=ids, attention_mask=att).last_hidden_state
        emb = F.normalize(self._project(h), dim=-1)
        keep = att.bool() & ~torch.isin(ids, self.skip)
        return emb, keep


def maxsim(q: torch.Tensor, docs: torch.Tensor, keep: torch.Tensor) -> torch.Tensor:
    """q [Q, dim] · docs [N, L, dim] · keep [N, L] → [N] 점수 = 질의 토큰별 최대 유사도의 합."""
    s = torch.einsum("qd,nld->nql", q.to(docs.dtype), docs)          # [N, Q, L]
    s = s.masked_fill(~keep[:, None, :], -1e4)
    return s.max(dim=2).values.float().sum(dim=1)
PY

ssh -p $TP "$THOR" "docker run -i --rm --runtime nvidia -e MODEL=$MODEL -e DOC_LEN=$DOC_LEN -e OUT=/out \
  -v \$HOME/zzaimy/models:/models:ro -v \$HOME/zzaimy/serve:/serve:ro -v $WORK:/work -v $OUT:/out \
  --entrypoint python3 $IMAGE -" <<'PY'
import json, os, sys, time
import numpy as np, torch
sys.path.insert(0, "/serve")
from colbert_encoder import ColbertEncoder

rows = [json.loads(l) for l in open("/work/chunks.jsonl", encoding="utf-8")]
enc = ColbertEncoder(os.environ["MODEL"], doc_len=int(os.environ.get("DOC_LEN", "1024")))
print(f"모델 {os.environ['MODEL']} · 차원 {enc.dim} · 질의 {enc.q_len} · 문서 상한 {enc.d_len} · 제외 토큰 {len(enc.skip)}", flush=True)
# 확장 토큰이 살아 있는지(어텐션 마스크 0 인 자리도 벡터가 나와야 한다) 한 번 확인
qv = enc.encode_queries(["연구비 정산 절차"])
print("질의 확장 토큰 벡터 노름(뒤쪽 3개):", [round(float(x), 3) for x in qv[0, -3:].norm(dim=-1)], flush=True)

t0 = time.time()
order = sorted(range(len(rows)), key=lambda i: len(rows[i]["text"]))
embs, lens = [None] * len(rows), [0] * len(rows)
B = 16
for s in range(0, len(order), B):
    idx = order[s:s + B]
    e, keep = enc.encode_docs([rows[i]["text"] for i in idx])
    for j, i in enumerate(idx):
        v = e[j][keep[j]].to(torch.float16).cpu().numpy()   # 유효 토큰만 남긴다
        embs[i], lens[i] = v, len(v)
    if (s // B) % 50 == 0:
        print(f"  {s + len(idx)}/{len(rows)} · {time.time() - t0:.0f}초", flush=True)
L = max(lens)
tokens = np.zeros((len(rows), L, enc.dim), dtype=np.float16)
for i, v in enumerate(embs):
    tokens[i, :len(v)] = v
np.save("/out/tokens.npy", tokens)
np.save("/out/lens.npy", np.array(lens, dtype=np.int32))
np.save("/out/ids.npy", np.array([r["id"] for r in rows], dtype=np.int64))
meta = {"model": os.path.basename(os.environ["MODEL"]), "n_chunks": len(rows), "dim": enc.dim, "max_tokens": int(L),
        "mean_tokens": float(np.mean(lens)), "total_tokens": int(sum(lens)), "doc_len": enc.d_len,
        "bytes": int(tokens.nbytes), "seconds": round(time.time() - t0, 1), "device": "thor-gpu"}
json.dump(meta, open("/out/meta.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(meta, ensure_ascii=False))
PY

echo "[$(date +%T)] 3/3 산출물"
ssh -p $TP "$THOR" "ls -la $OUT && cat $OUT/meta.json && rm -rf $WORK"
