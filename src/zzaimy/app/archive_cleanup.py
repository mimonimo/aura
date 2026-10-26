"""원본 보관소 정리 — 처리하지 않는 원본(사진·압축·범위 밖·중복·크기 초과)을 갈래별로 보여 주고 「정리함」으로 옮긴다.

정리함은 표시만 한다(archive_cleanup 표). 원본 파일은 DGX 에 그대로 있고, 보관소 목록·건수(archive_state.original_sql)에서만
빠진다. 되돌리면 다시 보인다. 동기화가 없어진 원본에 다는 removed_at 과 따로 둬서 동기화가 정리를 풀지 않는다.
DGX 의 실제 파일 삭제는 이 모듈이 하지 않는다 — 정리함을 담당자가 확인한 뒤 따로 한다.
「남기기」(archive_keep)는 정리 대상에서만 빼는 표시다 — 보관소 목록에는 그대로 보이고, 다음 정리 목록에 다시 뜨지 않는다.
정리 목록은 지금 남은 원본만 센다. 지난 정리 뒤에 새로 들어온 원본이 다시 뜨는 까닭을 알 수 있게 지난 정리 시각을 함께 준다.
"""

from __future__ import annotations

import time

IMAGE = ("jpg", "jpeg", "png", "bmp", "gif", "tif", "tiff", "jfif", "heic", "webp")
ARCHIVE = ("zip", "7z", "egg", "rar", "alz", "tar", "gz")
CATEGORIES = (
    ("photo", "사진·이미지", "글이 없는 사진 파일 — 사진대지·행사 사진 등. 검색·답변에 쓰이지 않음"),
    ("archive", "압축 파일", "압축 묶음 — 풀어 둔 문서는 따로 들어와 있음"),
    ("scope", "범위 밖(지출·증빙)", "경로에 지출·증빙·스캔·영수·정산·집행 — 처리 범위 밖"),
    ("dup", "같은 파일 중복", "이름·크기·내용 지문이 같은 다른 파일이 있는 사본 — 문서에 이어진 사본은 세지 않음"),
    ("big", "처리 상한 초과", "300MB 를 넘어 처리하지 않는 파일"),
    # 경로 규칙으로 못 거른 지출·계약 증빙 — 서류 갈래로 알아보고 라이브러리에서 뺀 문서의 원본(scripts/187 이 옮긴다)
    ("evidence", "지출·계약 증빙", "견적서·거래명세서·영수증 같은 증빙 서류 — 라이브러리에서 뺀 문서의 원본"),
    ("pruned", "라이브러리에서 뺀 문서", "같은 본문 여분·빈 문서·도면·개인 제출물 — 라이브러리에서 뺀 문서의 원본"),
)
MANUAL = ("evidence", "pruned")                                     # 규칙으로 고르지 않고 도구가 직접 옮기는 갈래
LABEL = {k: lab for k, lab, _ in CATEGORIES}
_cache: dict = {"at": 0.0, "v": None}


def category(row: dict) -> str:
    """처리 제외 원본의 정리 갈래(없으면 '')."""
    from zzaimy.app.archive_state import exclusion_reason
    why = exclusion_reason(row)
    ext = (row.get("ext") or "").lower().lstrip(".")
    if why == "문서 형식 아님":
        return "photo" if ext in IMAGE else ("archive" if ext in ARCHIVE else "")
    # 크기 초과이면서 범위 밖 경로(지출·증빙 폴더의 큰 스캔)는 처리할 대상이 아니므로 범위 밖으로 — 「처리 상한 초과」에는
    # 상한을 넘겨 따로 처리할 문서만 남긴다
    if why == "처리 상한 초과":
        from zzaimy.app.archive_state import OUT_OF_SCOPE
        if OUT_OF_SCOPE.search(row.get("rel") or ""):
            return "scope"
    return {"같은 파일 중복": "dup", "처리 상한 초과": "big", "범위 밖 경로": "scope"}.get(why, "")


def _rows(db) -> list[dict]:
    from zzaimy.app import archive_state as st
    orig, oa = st.original_sql()
    expr, ea = st.state_sql()
    with db._conn() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT rel, ext, size, dup_of FROM archive_files WHERE removed_at = '' AND {orig} AND {expr} = 'excluded'"
            " AND NOT EXISTS (SELECT 1 FROM archive_keep ak WHERE ak.rel = archive_files.rel)",
            [*oa, *ea]).fetchall()]


def summary(db, force: bool = False) -> dict:
    """{items:[{key,label,note,n,bytes,cleaned}], cleaned_total} — 처리 제외 원본을 갈래별로(5분 재사용)."""
    if not force and _cache["v"] is not None and time.time() - _cache["at"] < 300:
        return _cache["v"]
    count: dict[str, list] = {k: [0, 0] for k, _, _ in CATEGORIES}
    for r in _rows(db):
        k = category(r)
        if k:
            count[k][0] += 1
            count[k][1] += int(r.get("size") or 0)
    with db._conn() as conn:
        cleaned = {str(r[0]): int(r[1]) for r in conn.execute("SELECT category, COUNT(*) FROM archive_cleanup GROUP BY category").fetchall()}
        last = conn.execute("SELECT MAX(at) FROM archive_cleanup").fetchone()[0] or ""
        kept = int(conn.execute("SELECT COUNT(*) FROM archive_keep").fetchone()[0] or 0)
    out = {"items": [{"key": k, "label": lab, "note": note, "n": count[k][0], "bytes": count[k][1], "cleaned": cleaned.get(k, 0),
                      "manual": k in MANUAL}
                     for k, lab, note in CATEGORIES],
           "cleaned_total": sum(cleaned.values()),
           "restorable": sum(n for k, n in cleaned.items() if k not in MANUAL),
           "left": sum(c[0] for c in count.values()), "left_bytes": sum(c[1] for c in count.values()),
           "last_at": str(last)[:16], "kept_total": kept}
    _cache.update(at=time.time(), v=out)
    return out


def sample(db, key: str, limit: int = 40) -> list[str]:
    return [r["rel"] for r in _rows(db) if category(r) == key][:limit]


def move(db, key: str, by: str = "") -> int:
    """갈래 하나를 정리함으로 — 옮긴 수."""
    if key not in LABEL or key in MANUAL:
        return 0
    rels = [r["rel"] for r in _rows(db) if category(r) == key]
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with db._conn() as conn:
        for i in range(0, len(rels), 500):
            conn.executemany("INSERT INTO archive_cleanup (rel, category, at, by_user) VALUES (?, ?, ?, ?) ON CONFLICT (rel) DO NOTHING",
                             [(rel, key, now, by) for rel in rels[i:i + 500]])
    _cache.update(v=None)
    return len(rels)


def put(db, rels: list[str], key: str, by: str = "") -> int:
    """고른 원본을 정리함으로(MANUAL 갈래)."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with db._conn() as conn:
        for i in range(0, len(rels), 500):
            conn.executemany("INSERT INTO archive_cleanup (rel, category, at, by_user) VALUES (?, ?, ?, ?) ON CONFLICT (rel) DO NOTHING",
                             [(rel, key, now, by) for rel in rels[i:i + 500]])
    _cache.update(v=None)
    return len(rels)


def keep(db, key: str, by: str = "") -> int:
    """갈래 하나의 남은 원본을 「남기기」로 — 정리 목록에서만 빠진다(보관소 목록·건수는 그대로). 남긴 수."""
    if key not in LABEL or key in MANUAL:
        return 0
    rels = [r["rel"] for r in _rows(db) if category(r) == key]
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with db._conn() as conn:
        for i in range(0, len(rels), 500):
            conn.executemany("INSERT INTO archive_keep (rel, category, at, by_user) VALUES (?, ?, ?, ?) ON CONFLICT (rel) DO NOTHING",
                             [(rel, key, now, by) for rel in rels[i:i + 500]])
    _cache.update(v=None)
    return len(rels)


def restore(db, key: str = "") -> int:
    """정리함에서 되돌리기 — 갈래 하나 또는 전부(key=''). 도구가 옮긴 갈래(MANUAL)는 문서까지 되살리는 그 도구로만."""
    if key in MANUAL:
        return 0
    with db._conn() as conn:
        where = " WHERE category = ?" if key else f" WHERE category NOT IN ({','.join('?' * len(MANUAL))})"
        args = [key] if key else list(MANUAL)
        n = conn.execute("SELECT COUNT(*) FROM archive_cleanup" + where, args).fetchone()[0]
        conn.execute("DELETE FROM archive_cleanup" + where, args)
    _cache.update(v=None)
    return int(n)
