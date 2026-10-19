"""그래프 입력 지문 — 그래프 다시 짓기(scripts/157)가 읽는 것 전부를 한 곳에 적는다.

1분 주기(scripts/170 --quick)가 이 지문을 지난번 지은 때와 견주어 다르면 다시 짓는다. 그래서 무엇이 어느 길로 바뀌었든
(반입·문서함 업로드·27B 갈래 판정·사업 재판정·장부 검토·문서 정리·프로젝트 이름) 바꾼 쪽이 따로 알릴 필요가 없다.
157 이 새 입력을 읽게 되면 여기에도 더한다 — 입력 목록이 이 파일 하나에만 있다.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

# 그래프가 읽는 장부 파일(data/platform 아래) — 사업 장부·폴더/파일 검토 판정·교차 검수·원본 경로
FILES = ("kg_external.json", "class_review.jsonl", "class_review_astra.jsonl", "origins.jsonl")


def _rows_sum(rows) -> int:
    """행마다 지문을 더해 순서와 무관하게 합친다 — 이름 정렬은 16만 행에서 20초가 넘어 1분 주기에 못 쓴다."""
    acc = 0
    for r in rows:
        acc = (acc + int.from_bytes(hashlib.blake2b("\x1f".join(str(x or "") for x in r).encode("utf-8"),
                                                     digest_size=8).digest(), "big")) % (1 << 64)
    return acc


def fingerprint(conn, data_dir: Path) -> dict:
    from zzaimy.app.database_backend import table_names
    names = table_names(conn)
    n, hi = conn.execute("SELECT COUNT(*), MAX(id) FROM documents WHERE status = 'reviewed'").fetchone()
    # 조각은 수와 최대 id — 다시 처리하면 새 id 가 붙으니 수가 같아도 바뀐 것을 안다
    nc, hc = conn.execute("SELECT COUNT(*), MAX(id) FROM doc_chunks").fetchone()
    docs = _rows_sum(conn.execute("SELECT id, filename, stored_path, project_id, kind FROM documents WHERE status = 'reviewed'"))
    projs = _rows_sum(conn.execute("SELECT id, name FROM projects")) if "projects" in names else 0
    arch = _rows_sum(conn.execute("SELECT rel, program, year, round FROM archive_files WHERE removed_at = ''")) \
        if "archive_files" in names else 0
    h = hashlib.sha1(f"{docs}|{projs}".encode())
    p = hashlib.sha1(str(arch).encode())
    for name in FILES:
        f = Path(data_dir) / name
        if f.is_file():
            p.update(name.encode() + f.read_bytes())
    return {"docs": int(n or 0), "max_id": int(hi or 0), "chunks": int(nc or 0), "max_chunk": int(hc or 0),
            "sig": h.hexdigest()[:16], "prog": p.hexdigest()[:16]}
