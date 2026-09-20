#!/bin/bash
# 학습 결과(LoRA 어댑터)를 서빙 장비(젯슨 토르)로 옮긴다.
#
# 83_sft_writer_qlora.py 가 만든 폴더를 그대로 보낸다. 어댑터만 보내므로 수백 MB 다.
# 받는 쪽: 토르 ~/zzaimy/adapters/<이름>/ — 82_serve_writer.sh 가 여기서 읽는다.
# 무엇을 학습했는지 추적할 수 있게 manifest.json(보낸 시각·원본 경로·파일 해시)을 함께 둔다.
#
# 사용 (학습 장비에서):
#   bash scripts/94_ship_adapter.sh data/train/writer-qlora writer-sft-01 thor-03@211.170.162.121
set -euo pipefail

SRC="${1:?어댑터 폴더}"
NAME="${2:?토르에 둘 이름}"
DEST="${3:?토르 계정@주소}"
PORT="${PORT:-8022}"

for f in adapter_config.json adapter_model.safetensors; do
  [ -f "$SRC/$f" ] || { echo "$SRC/$f 가 없습니다 — 학습이 끝난 어댑터 폴더인지 확인하십시오." >&2; exit 2; }
done

python3 - "$SRC" "$NAME" <<'PY'
import hashlib, json, sys, time
from pathlib import Path
src, name = Path(sys.argv[1]), sys.argv[2]
files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
         for p in sorted(src.iterdir()) if p.is_file() and p.name != "manifest.json"}
cfg = json.loads((src / "adapter_config.json").read_text())
(src / "manifest.json").write_text(json.dumps({
    "name": name, "shipped_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "source": str(src.resolve()), "base_model": cfg.get("base_model_name_or_path"),
    "rank": cfg.get("r"), "files": files}, ensure_ascii=False, indent=2))
PY

ssh -p "$PORT" "$DEST" "mkdir -p ~/zzaimy/adapters/$NAME"
rsync -az -e "ssh -p $PORT" --exclude 'checkpoint-*' "$SRC/" "$DEST:zzaimy/adapters/$NAME/"
ssh -p "$PORT" "$DEST" "cd ~/zzaimy/adapters/$NAME && python3 -c \"
import hashlib,json,pathlib
m=json.load(open('manifest.json'))
bad=[f for f,h in m['files'].items() if hashlib.sha256(pathlib.Path(f).read_bytes()).hexdigest()!=h]
print('대조 실패: '+', '.join(bad) if bad else '대조 통과 — 파일 %d개' % len(m['files']))
raise SystemExit(1 if bad else 0)\""
echo "옮겼습니다 — 토르에서: BASE=<베이스> ADAPTER=$NAME bash scripts/82_serve_writer.sh"
