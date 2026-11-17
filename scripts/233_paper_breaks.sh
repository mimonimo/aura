#!/bin/bash
# 논문 워드 쪽 배치 — 「다음 문단과 함께」(■ 편집 기호) 없이 쪽 끝에 제목·표 머리줄만 남거나 그림과 캡션이 갈라지는 곳을 찾아
# 그 앞에만 쪽 나눔 글자를 넣는다(11-18 사용자: ■ 없애고 배치는 잘 되게). 만들기 → 운영 서버 LibreOffice 로 그리기 → 찾기를
# 더할 것이 없을 때까지(최대 4번) 되풀이한다. 사용: bash scripts/233_paper_breaks.sh OUT.docx BREAKS.json
set -e
OUT="$1"; BJ="$2"; VM="${AURA_VM:-aura@192.168.16.226}"
PY="${AURA_PY:-$(pwd)/.venv/bin/python}"; [ -x "$PY" ] || PY=/Users/jun/aigen/aura/zzaimy-capstone/.venv/bin/python
[ -f "$BJ" ] || echo '{"h": [], "f": [], "t": []}' > "$BJ"
T=$(mktemp -d)
for i in 1 2 3 4; do
  PYTHONPATH=src "$PY" scripts/228_paper_docx.py --out "$OUT" --breaks "$BJ" | tail -1 >/dev/null
  ssh "$VM" 'mkdir -p /tmp/aura_brk && rm -f /tmp/aura_brk/*'
  scp -q "$OUT" "$VM:/tmp/aura_brk/p.docx"
  ssh "$VM" 'cd /tmp/aura_brk && timeout 600 soffice --headless --convert-to pdf p.docx >/dev/null 2>&1 && pdftotext -layout p.pdf p.txt && ~/zzaimy-capstone/.venv/bin/python - <<PYE
import json, pdfplumber
out = []
with pdfplumber.open("p.pdf") as pdf:
    for pg in pdf.pages:
        ws = [w for w in pg.extract_words() if 60 < w["top"] < pg.height - 60]
        first = min((w["top"] for w in ws), default=pg.height)
        out.append(any(im["top"] < first for im in pg.images))
json.dump(out, open("p.img.json", "w"))
PYE'
  scp -q "$VM:/tmp/aura_brk/p.txt" "$T/p.txt"; scp -q "$VM:/tmp/aura_brk/p.img.json" "$T/p.img.json"
  n=$(PYTHONPATH=src "$PY" scripts/228_paper_docx.py --out "$OUT" --find-breaks "$T/p.txt" "$BJ" | grep -oE "[0-9-]+$")
  echo "쪽 배치 $i 번째: 쪽 나눔 $n 곳 더함"
  [ "$n" = "0" ] && break
done
ssh "$VM" 'rm -rf /tmp/aura_brk'; rm -rf "$T"
