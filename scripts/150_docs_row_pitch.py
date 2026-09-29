"""독스·LibreOffice 표 행 간격 실험(2026-09-29 실측용) — VM 에서 env PYTHONPATH=src .venv/bin/python scripts/150_docs_row_pitch.py, 입력 docx 는 /tmp/rowpitch.docx. 기록: docs/notes/2026-09-29-line-spacing-measurement.md"""
import json, subprocess, tempfile, sqlite3
from pathlib import Path
import httpx, numpy as np
from PIL import Image
from zzaimy.app import office_pdf
from zzaimy.ingest import gdrive_files, gdrive
src = Path("/tmp/rowpitch.docx"); out = Path("/tmp/rowpitch"); out.mkdir(exist_ok=True)
for f in out.glob("*.png"): f.unlink()
with tempfile.TemporaryDirectory() as tmp:
    lo = office_pdf.to_pdf(src, Path(tmp)); (out / "lo.pdf").write_bytes(lo.read_bytes())
db = sqlite3.connect("data/platform/platform.db")
acct = json.loads(db.execute("select value from settings where key='chat_google_doc:18'").fetchone()[0])["account"]
h = httpx.Client(timeout=httpx.Timeout(600, connect=30))
hdr = lambda: {"Authorization": f"Bearer {gdrive.access_token(acct, h)}"}
up = gdrive_files.upload_file(acct, src.read_bytes(), "행간격실험.docx", gdrive_files.CONVERT[".docx"][0], None,
                              convert_to="application/vnd.google-apps.document", http=h, reuse=False)
try:
    r = h.get(f"{gdrive.API}/files/{up['id']}/export", headers=hdr(), params={"mimeType": "application/pdf"})
    (out / "docs.pdf").write_bytes(r.content)
finally:
    h.delete(f"{gdrive.API}/files/{up['id']}", headers=hdr())
def pitches(pdf, tag):
    subprocess.run(["pdftoppm", "-r", "100", "-png", str(pdf), str(out / tag)], check=True)
    res = {}
    for png in sorted(out.glob(f"{tag}-*.png")):
        im = np.array(Image.open(png).convert("L")); dark = im < 128
        rows = np.where(dark.sum(axis=1) > im.shape[1] * 0.55)[0]
        lines = []
        for y in rows:
            if lines and y - lines[-1][-1] <= 2: lines[-1].append(y)
            else: lines.append([y])
        ys = [float(np.mean(l)) for l in lines]
        groups = [[ys[0]]] if ys else []
        for y in ys[1:]:
            if y - groups[-1][-1] > 100 / 25.4 * 20: groups.append([y])
            else: groups[-1].append(y)
        res[png.name] = [round(float(np.median(np.diff(g))) / 100 * 25.4, 2) for g in groups if len(g) >= 3]
    return res
print("LO   :", pitches(out / "lo.pdf", "lo"))
print("DOCS :", pitches(out / "docs.pdf", "docs"))
print("variants: A atleast12/mar28/trH  B atleast12/mar0/trH  C atleast12/mar28/noTrH  D exact12/mar28/trH  E mult0.92/mar28/trH  F exact12/mar0/noTrH | pages: NBG, Nanum Gothic, Nanum Gothic Coding, Sunflower | HWP 기준 5.9mm")
