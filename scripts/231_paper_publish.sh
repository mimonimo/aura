#!/usr/bin/env bash
# 논문 반영 한 번에(11-15) — 원고 → 웹 기안기 2313(서식째 쓰기·논문 양식·쪽 아래 틀) → 바탕화면 사본 두 개(2313 hwpx · 워드) → 렌더본 검사.
# 셋이 늘 같은 판이 되게 한다(한 곳만 고쳐 올리던 것). 개발 PC 에서: bash scripts/231_paper_publish.sh [바탕화면 폴더]
set -euo pipefail
cd "$(dirname "$0")/.."
DEF="$HOME/Desktop/AURA-논문"; [ -d "$HOME/Desktop/AURA/02_논문" ] && DEF="$HOME/Desktop/AURA/02_논문/AURA-논문"   # 바탕화면 AURA 정리(11-17) 뒤 자리
OUT="${1:-$DEF}"
VM="${AURA_VM:-aura@192.168.16.226}"
PY="${AURA_PY:-$(pwd)/.venv/bin/python}"; [ -x "$PY" ] || PY=/Users/jun/aigen/aura/zzaimy-capstone/.venv/bin/python
if [ -z "${WORD_ONLY:-}" ]; then                     # WORD_ONLY=1: 워드본만 다시(빌더만 고쳤을 때)
echo "── 웹 기안기 문서에 쓰기"
ssh "$VM" 'cd ~/zzaimy-capstone && set -a && . ./.env.local && set +a && env PYTHONPATH=src .venv/bin/python scripts/214_paper_write.py '"${REMAKE:+--remake}"' --rich --overwrite 2>&1 | grep -E "건너뜀|끝|양식|새로" ; env PYTHONPATH=src .venv/bin/python scripts/214_paper_write.py --relayout 2>&1 | tail -1'
echo "── 바탕화면 사본"
mkdir -p "$OUT"
P=$(ssh "$VM" 'cd ~/zzaimy-capstone && set -a && . ./.env.local && set +a && env PYTHONPATH=src .venv/bin/python -c "
from pathlib import Path
from zzaimy.app.db import Database
from zzaimy.app import paper_hwp as ph
print((ph.current(Database(Path(\"data/platform/platform.db\"))) or {}).get(\"path\",\"\"))"')
scp -q "$VM:zzaimy-capstone/$P" "$OUT/AURA-논문.hwpx" 2>/dev/null || scp -q "$VM:$P" "$OUT/AURA-논문.hwpx"
fi
mkdir -p "$OUT"
T=$(mktemp -d)
echo "── 워드 목차 쪽 번호 미리 채우기(운영 서버 LibreOffice 로 쪽을 재서 — 보기 전용 워드에서도 쪽 번호가 보이게)"
PYTHONPATH=src "$PY" scripts/228_paper_docx.py --out "$OUT/AURA-논문.docx" --entries "$T/e.json"
ssh "$VM" 'mkdir -p /tmp/aura_toc && rm -f /tmp/aura_toc/*'
scp -q "$OUT/AURA-논문.docx" "$VM:/tmp/aura_toc/p.docx"
ssh "$VM" 'cd /tmp/aura_toc && timeout 600 soffice --headless --convert-to pdf p.docx >/dev/null 2>&1 && pdftotext -layout p.pdf p.txt && echo "PDF $(pdfinfo p.pdf 2>/dev/null | grep Pages)"'
scp -q "$VM:/tmp/aura_toc/p.txt" "$T/p.txt" && ssh "$VM" 'rm -rf /tmp/aura_toc'
PYTHONPATH=src "$PY" scripts/228_paper_docx.py --out "$OUT/AURA-논문.docx" --pages-from "$T/e.json" "$T/p.txt" > "$T/pages.json"
PYTHONPATH=src "$PY" scripts/228_paper_docx.py --out "$OUT/AURA-논문.docx" --toc-pages "$T/pages.json" | grep -E "목차|→"
rm -rf "$T"
echo "── 렌더본 검사(원고에 없는 글·코드 흔적)"
"$PY" - "$OUT" <<'PYEOF'
import glob, html, re, sys, zipfile
out = sys.argv[1]
norm = lambda t: re.sub(r"[^가-힣A-Za-z0-9]", "", t)
src = norm(" ".join(open(p).read() for p in glob.glob("docs/paper/논문/*.md")))
for f, pre in ((f"{out}/AURA-논문.hwpx", "Contents/section"), (f"{out}/AURA-논문.docx", "word/document")):
    z = zipfile.ZipFile(f)
    x = "".join(z.read(n).decode() for n in sorted(z.namelist()) if n.startswith(pre))
    x = re.sub(r"<hp:parameters\b.*?</hp:parameters>", "", x, flags=re.S)
    paras = [html.unescape(re.sub(r"<[^>]+>", "", p)) for p in re.findall(r"<(?:hp|w):p\b(?:(?!<(?:hp|w):p\b).)*?</(?:hp|w):p>", x, re.S)]
    stale = [p for p in paras if len(norm(p)) >= 30 and norm(p)[:30] not in src and norm(p)[-30:] not in src]
    junk = [p for p in paras if re.search(r"docs/|scripts/\d|app/\w+\.py|이 장의 그림", p)]
    print(f"{f.rsplit('/', 1)[-1]}: 원고에 없는 문단 {len(stale)}, 흔적 {len(junk)}")
    for p in (stale + junk)[:5]:
        print("   ", " ".join(p.split())[:90])
PYEOF
ls -la "$OUT"/AURA-논문.*
