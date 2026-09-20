#!/bin/bash
# 학습→서빙 경로 자가 점검 (토르에서) — 실제 학습 없이 82·94 가 도는지 확인한다.
#
# 1) 작은 베이스(기본 Qwen/Qwen3-0.6B)의 설정으로 '영(0) LoRA 어댑터'를 만든다.
#    B 행렬이 0 이라 출력은 베이스와 같다 — 어댑터가 실리는지만 본다.
#    (학습본의 형식은 83 이 저장하는 peft 형식과 같다: adapter_config.json + adapter_model.safetensors)
# 2) 82_serve_writer.sh 로 베이스+어댑터를 띄운다(127.0.0.1 에서만).
# 3) zzaimy-base · zzaimy-writer 두 이름으로 같은 질문을 보내 둘 다 답하는지, 답이 같은지 본다.
# 4) 컨테이너를 내린다.
#
# 사용 (토르):  bash ~/zzaimy/95_serving_selftest.sh [베이스]   (82_serve_writer.sh 가 같은 폴더에 있어야 한다)
set -uo pipefail
BASE="${1:-Qwen/Qwen3-0.6B}"
DIR="$HOME/zzaimy"
IMAGE="${IMAGE:-ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor}"
NAME=zzaimy-selftest
PORT=8011
mkdir -p "$DIR/hf" "$DIR/adapters/selftest-zero"

echo "[$(date +%T)] 영 어댑터 만들기 — $BASE"
docker run -i --rm --runtime nvidia -e HF_HOME=/root/.cache/huggingface \
  -v "$DIR/hf:/root/.cache/huggingface" -v "$DIR/adapters:/adapters" \
  --entrypoint python3 "$IMAGE" - "$BASE" <<'PY' || exit 1
import json, sys, torch
from huggingface_hub import hf_hub_download
from safetensors.torch import save_file
base = sys.argv[1]
cfg = json.load(open(hf_hub_download(base, "config.json")))
h, n_layers = cfg["hidden_size"], cfg["num_hidden_layers"]
hd = cfg.get("head_dim") or h // cfg["num_attention_heads"]
q_out, kv_out, r = cfg["num_attention_heads"] * hd, cfg["num_key_value_heads"] * hd, 8
t = {}
for i in range(n_layers):
    for name, out in (("q_proj", q_out), ("v_proj", kv_out)):
        k = f"base_model.model.model.layers.{i}.self_attn.{name}"
        t[f"{k}.lora_A.weight"] = torch.randn(r, h, dtype=torch.bfloat16) * 0.01
        t[f"{k}.lora_B.weight"] = torch.zeros(out, r, dtype=torch.bfloat16)
save_file(t, "/adapters/selftest-zero/adapter_model.safetensors")
json.dump({"base_model_name_or_path": base, "peft_type": "LORA", "task_type": "CAUSAL_LM",
           "r": r, "lora_alpha": 2 * r, "lora_dropout": 0.0, "target_modules": ["q_proj", "v_proj"],
           "bias": "none", "fan_in_fan_out": False, "inference_mode": True},
          open("/adapters/selftest-zero/adapter_config.json", "w"), indent=2)
print("어댑터 텐서", len(t))
PY

echo "[$(date +%T)] 베이스+어댑터 서빙"
BASE="$BASE" ADAPTER=selftest-zero PORT=$PORT MEM=0.2 MAXLEN=4096 NAME=$NAME \
  bash "$DIR/82_serve_writer.sh" || { docker logs $NAME 2>&1 | tail -20; exit 1; }

ask() {
  curl -s -m 120 "http://127.0.0.1:$PORT/v1/chat/completions" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$1\",\"temperature\":0,\"max_tokens\":20,\"messages\":[{\"role\":\"user\",\"content\":\"대한민국의 수도는? 한 단어로. /no_think\"}],\"chat_template_kwargs\":{\"enable_thinking\":false}}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["choices"][0]["message"]["content"].strip())'
}
A=$(ask zzaimy-base); B=$(ask zzaimy-writer)
echo "베이스:   $A"
echo "어댑터:   $B"
[ -n "$A" ] && [ "$A" = "$B" ] && echo "결과: 통과 — 두 이름 모두 답하고, 영 어댑터라 답이 같다" || echo "결과: 확인 필요"
docker rm -f $NAME >/dev/null
