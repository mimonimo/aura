"""docx 파일들을 독스에 올려 PDF 로 내보내 쪽수를 잰다(148 과 같은 길) — 배포 전에 변환기 변경을 잴 때."""
import json, sqlite3, sys
from pathlib import Path
import httpx
from pypdf import PdfReader
from zzaimy.ingest import gdrive_files, gdrive
from zzaimy.app import office_pdf
import tempfile
db = sqlite3.connect("data/platform/platform.db")
acct = json.loads(db.execute("select value from settings where key='chat_google_doc:18'").fetchone()[0])["account"]
h = httpx.Client(timeout=httpx.Timeout(600, connect=30))
hdr = lambda: {"Authorization": f"Bearer {gdrive.access_token(acct, h)}"}
for f in sys.argv[1:]:
    src = Path(f); lo_pages = None
    if src.name.startswith("lo_"):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = office_pdf.to_pdf(src, Path(tmp)); lo_pages = len(PdfReader(str(pdf)).pages) if pdf else None
        print(src.name, "LO pages", lo_pages); continue
    up = gdrive_files.upload_file(acct, src.read_bytes(), f"쪽수측정 {src.name}", gdrive_files.CONVERT[".docx"][0], None,
                                  convert_to="application/vnd.google-apps.document", http=h, reuse=False)
    try:
        r = h.get(f"{gdrive.API}/files/{up['id']}/export", headers=hdr(), params={"mimeType": "application/pdf"})
        out = Path("/tmp") / (src.stem + ".docs.pdf"); out.write_bytes(r.content)
        print(src.name, "DOCS pages", len(PdfReader(str(out)).pages), "->", out)
    finally:
        h.delete(f"{gdrive.API}/files/{up['id']}", headers=hdr())
