from pathlib import Path
import sqlite3, sys
sys.path.insert(0, "src")
from zzaimy.app.db import Database
from zzaimy.app.pipeline import DocumentProcessor

db = Database(Path("data/platform/platform.db"))
conn = sqlite3.connect("data/platform/platform.db")
conn.row_factory = sqlite3.Row
rows = [dict(r) for r in conn.execute(
    "SELECT id, filename, stored_path FROM documents WHERE filename LIKE '%.pdf'"
    " AND (filename LIKE '%장학%' OR filename LIKE '%기부%' OR filename LIKE '%희망사다리%'"
    " OR filename LIKE '%가구원%' OR filename LIKE '%공고%') ORDER BY id").fetchall()]
conn.close()
proc = DocumentProcessor()
for r in rows:
    p = Path(r["stored_path"])
    if not p.exists():
        continue
    proc.process(db, r["id"], p)
    d = db.get_document(r["id"]) or {}
    print(f"재처리 {r['id']} {r['filename'][:30]} → {d.get('parse_note')}", flush=True)
print("PY_DONE")
