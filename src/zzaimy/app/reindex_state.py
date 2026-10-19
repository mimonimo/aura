"""규정 임베딩 색인이 지금 규정 조각과 맞는가 — 조각 지문(수·최대 id·id 합)을 색인 메타와 견준다.

색인을 만들 때(scripts/52) 쓴 조각 지문을 메타(chunks_sig)에 적고, 앱의 자동 재색인이 지금 지문과 다르면 다시 만든다.
등록·해제·재분할·문서 삭제·갈래 변경·코퍼스 반입 등 어느 길로 조각이 바뀌었든 바꾼 쪽이 알릴 필요가 없다.
.reindex-needed 표시는 「곧바로 다시 만들어 달라」는 뜻으로만 남는다(옛 경로 호환).
"""
from __future__ import annotations

import json
from pathlib import Path

from zzaimy.app import paths as _paths


def chunks_sig(conn) -> str:
    n, hi, s = conn.execute("SELECT COUNT(*), MAX(id), SUM(id) FROM regulation_chunks").fetchone()
    return f"{int(n or 0)}|{int(hi or 0)}|{int(s or 0)}"


def indexed_sig(base: Path | None = None) -> str | None:
    try:
        return json.loads(_paths.index_meta(base).read_text(encoding="utf-8")).get("chunks_sig")
    except (OSError, ValueError):
        return None


def needed(db, base: Path | None = None) -> bool:
    """다시 만들어야 하나 — 표시가 있거나, 색인이 만든 때의 조각 지문이 지금과 다르다(조각이 하나도 없으면 아니다)."""
    data_dir = Path(db.path).parent if base is None else Path(base)
    if (data_dir / ".reindex-needed").exists():
        return True
    try:
        with db._conn() as conn:
            now = chunks_sig(conn)
    except Exception:
        return False
    if now.startswith("0|"):
        return False
    return indexed_sig(base) != now
