#!/usr/bin/env bash
# 폐쇄망용 설치 묶음 만들기 — 인터넷 되는 PC(Docker 필요)에서 돌려 파일 하나를 만든다. 서버로 옮겨 풀고 설치한다.
#
#   bash deploy/bundle.sh                 # dist/aura-<판>.tar.gz (리눅스 x86_64 · Python 3.12)
#   bash deploy/bundle.sh --no-models     # 검색 모델 캐시 빼고(서빙 서버를 쓰는 곳)
#
# 묶음 안: aura/(코드와 깃 이력) · wheels/(파이썬 패키지) · hf/(CPU 비상용 검색 모델) · kordoc/·node/(한글 읽기 도구) · 설치방법.txt
# 서버에서:  tar -xzf aura-<판>.tar.gz && cd aura-<판>/aura && bash deploy/install.sh --offline ..
# 업데이트:  bash deploy/update.sh --bundle aura-<새 판>.tar.gz
# 한컴 웹 기안기 패키지·라이선스는 기관마다 따로 받는 상용 파일이라 넣지 않는다(deploy/webhwp/).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
MODELS=1; [ "${1:-}" = --no-models ] && MODELS=0
command -v docker >/dev/null || { echo "Docker 가 필요합니다(리눅스용 패키지를 받기 위해)" >&2; exit 2; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "커밋하지 않은 변경이 있습니다 — 묶음은 커밋한 판으로만 만듭니다" >&2; exit 2; }
VER=$(git describe --tags --always)
OUT="$ROOT/dist/aura-$VER"
rm -rf "$OUT" && mkdir -p "$OUT"/{wheels,hf,kordoc}
echo "── 코드 ($VER)"
git clone -q --no-local "$ROOT" "$OUT/aura"
git -C "$OUT/aura" remote remove origin 2>/dev/null || true   # 원격 주소(계정 정보가 섞일 수 있음)는 묶음에 넣지 않는다 — 업데이트는 새 묶음으로

echo "── 파이썬 패키지(리눅스 x86_64 · 3.12)"
docker run --rm --platform linux/amd64 -v "$OUT/aura:/src:ro" -v "$OUT/wheels:/w" python:3.12-slim bash -c '
  set -e; pip -q download -d /w pip setuptools wheel
  pip -q download -d /w "/src[app,postgres]"
  pip -q download -d /w torch --index-url https://download.pytorch.org/whl/cpu
  pip -q download -d /w sentence-transformers'
rm -f "$OUT"/wheels/zzaimy-*                      # 우리 코드 자체는 aura/ 에서 설치

if [ $MODELS = 1 ]; then
  echo "── CPU 비상용 검색 모델"
  docker run --rm --platform linux/amd64 -v "$OUT/wheels:/w:ro" -v "$OUT/hf:/hf" -e HF_HOME=/hf python:3.12-slim bash -c '
    set -e; pip -q install --no-index --find-links /w sentence-transformers torch
    python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer(\"nlpai-lab/KURE-v1\", device=\"cpu\"); CrossEncoder(\"BAAI/bge-reranker-v2-m3\", device=\"cpu\")"'
fi

echo "── 한글 읽기 도구(kordoc)·Node.js"
docker run --rm --platform linux/amd64 -v "$OUT/kordoc:/k" node:22-slim bash -c '
  set -e; cd /k && npm init -y >/dev/null && npm install -s kordoc >/dev/null
  cp -r /usr/local /k/.node-runtime'
mv "$OUT/kordoc/.node-runtime" "$OUT/node"

cat > "$OUT/설치방법.txt" <<EOF
AURA 설치 묶음 $VER ($(date +%F))

1. 이 폴더를 서버의 설치할 계정 홈에 둔다 (예: ~/aura-$VER)
2. cd aura-$VER/aura && bash deploy/install.sh --offline ..
   (443 포트·TLS: --port 443 --host 0.0.0.0 --tls-cert <인증서> --tls-key <키> --public-url https://<주소>)
3. 끝에 나오는 안내대로 zzdev 로 로그인해 「도구·모델 연결」 에서 GPU 서버를 잇고, 「조직 관리」 에서 사용자를 등록한다
4. 한글 웹 기안기: bash deploy/webhwp/install.sh <한컴 패키지 zip> <tomcat zip> <license.dat>

자세한 설명: aura/docs/ops/README.md
EOF
echo "── 묶기"
tar -C "$ROOT/dist" -czf "$ROOT/dist/aura-$VER.tar.gz" "aura-$VER"
rm -rf "$OUT"
ls -lh "$ROOT/dist/aura-$VER.tar.gz"
