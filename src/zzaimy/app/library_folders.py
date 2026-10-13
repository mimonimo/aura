"""라이브러리 폴더 — 문서를 담는 묶음과 그 묶음의 검색 범위(공통 RAG 또는 부서 RAG).

폴더는 두 가지다.
- 원본 폴더: 학교 원본 보관소의 최상위 폴더(링크·앵커·산단 …). 반입이 만든다. 검색 범위는 RAG 공간 설정의 폴더별 부서
  (dept_of_area, 비어 있으면 공통)
- 만든 폴더: 라이브러리에서 담당자가 만든 폴더(library_folders). 「문서 올리기」로 올린 문서가 담긴다(doc_folders)
폴더의 검색 범위를 바꾸면 그 폴더의 문서가 모두 함께 옮겨 간다(rag_spaces.move_docs). 기본은 공통 RAG.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

AREA = "area:"
MADE = "f:"
_NAME = re.compile(r"^[0-9A-Za-z가-힣][0-9A-Za-z가-힣 _.·()\-+&]{0,39}$")


def _cfg(data_dir: Path) -> dict:
    from zzaimy.app import rag_spaces
    return rag_spaces.config(data_dir)


def folders(db, data_dir: Path) -> list[dict]:
    """[{key, name, kind(원본|만든), dept, docs}] — 원본 폴더 먼저(문서 많은 순), 만든 폴더는 이름순."""
    from zzaimy.app import rag_spaces
    cfg = _cfg(data_dir)
    areas = {f["folder"] for f in rag_spaces.area_folders(db)}
    out = []
    with db._conn() as conn:
        n_area = {str(r[0]): int(r[1]) for r in conn.execute(
            "SELECT area, COUNT(DISTINCT doc_id) FROM archive_files WHERE doc_id IS NOT NULL AND removed_at = '' AND area <> ''"
            " GROUP BY area") if str(r[0]) in areas}
        made = [dict(r) for r in conn.execute("SELECT id, name, dept FROM library_folders ORDER BY name")]
        n_made = {int(r[0]): int(r[1]) for r in conn.execute("SELECT folder_id, COUNT(*) FROM doc_folders GROUP BY folder_id")}
    for a in sorted(areas, key=lambda a: -n_area.get(a, 0)):
        if n_area.get(a):
            out.append({"key": AREA + a, "name": a, "kind": "원본", "dept": cfg.get("dept_of_area", {}).get(a) or "공통",
                        "docs": n_area[a]})
    for r in made:
        out.append({"key": f"{MADE}{r['id']}", "name": r["name"], "kind": "만든", "dept": r["dept"] or "공통",
                    "docs": n_made.get(int(r["id"]), 0)})
    return out


def filter_sql(key: str) -> tuple[str, list]:
    """라이브러리 목록의 폴더 조건(문서 표 별칭 d)."""
    if key.startswith(AREA):
        return "d.id IN (SELECT doc_id FROM archive_files WHERE doc_id IS NOT NULL AND area = ?)", [key[len(AREA):]]
    if key.startswith(MADE) and key[len(MADE):].isdigit():
        return "d.id IN (SELECT doc_id FROM doc_folders WHERE folder_id = ?)", [int(key[len(MADE):])]
    return "", []


def create(db, data_dir: Path, name: str, dept: str, by: str = "", allowed: list[str] | None = None) -> tuple[int | None, str]:
    """폴더 하나 — (번호, 오류). 검색 범위는 공통 또는 있는 부서 RAG."""
    from zzaimy.app import rag_spaces
    name = " ".join((name or "").split())
    dept = (dept or "공통").strip()
    if not _NAME.match(name):
        return None, "폴더 이름은 1~40자의 한글·영문·숫자로 적습니다"
    if dept != "공통" and dept not in (allowed if allowed is not None else rag_spaces.known_depts(_cfg(data_dir), {})):
        return None, f"없는 부서 RAG: {dept} — RAG 공간에서 먼저 만듭니다"
    if name in {f["name"] for f in folders(db, data_dir)}:
        return None, f"「{name}」 폴더가 이미 있습니다"
    with db._conn() as conn:
        conn.execute("INSERT INTO library_folders (name, dept, created_at, created_by) VALUES (?, ?, ?, ?)",
                     (name, dept, time.strftime("%Y-%m-%d %H:%M:%S"), by))
        fid = conn.execute("SELECT id FROM library_folders WHERE name = ?", (name,)).fetchone()[0]
    return int(fid), ""


def folder_dept(db, data_dir: Path, key: str) -> str:
    """폴더의 검색 범위 — 모르는 폴더면 공통."""
    return next((f["dept"] for f in folders(db, data_dir) if f["key"] == key), "공통")


def put(db, key: str, doc_ids: list[int]) -> int:
    """만든 폴더에 문서를 담는다(한 문서는 한 폴더) — 담은 수."""
    if not key.startswith(MADE):
        return 0
    fid = int(key[len(MADE):])
    with db._conn() as conn:
        for did in doc_ids:
            conn.execute("DELETE FROM doc_folders WHERE doc_id = ?", (int(did),))
            conn.execute("INSERT INTO doc_folders (doc_id, folder_id) VALUES (?, ?)", (int(did), fid))
    return len(doc_ids)


def set_dept(db, data_dir: Path, key: str, dept: str, allowed: list[str] | None = None) -> tuple[int, str]:
    """폴더의 검색 범위를 바꾸고 폴더의 문서를 함께 옮긴다 — (옮긴 문서 수, 오류)."""
    from zzaimy.app import rag_spaces
    dept = (dept or "공통").strip()
    cfg = dict(_cfg(data_dir))
    if dept != "공통" and dept not in (allowed if allowed is not None else rag_spaces.known_depts(cfg, {})):
        return 0, f"없는 부서 RAG: {dept}"
    where, args = filter_sql(key)
    if not where:
        return 0, "없는 폴더"
    if key.startswith(AREA):
        area = key[len(AREA):]
        pairs = dict(cfg.get("dept_of_area", {}))
        if dept == "공통":
            pairs.pop(area, None)
        else:
            pairs[area] = dept
        cfg["dept_of_area"] = pairs
        rag_spaces.save_config(cfg, data_dir)        # 앞으로 이 폴더로 반입되는 문서도 같은 범위로
    else:
        with db._conn() as conn:
            conn.execute("UPDATE library_folders SET dept = ? WHERE id = ?", (dept, int(key[len(MADE):])))
    with db._conn() as conn:
        ids = [int(r[0]) for r in conn.execute(f"SELECT d.id FROM documents d WHERE {where}", args)]
    return rag_spaces.move_docs(db, ids, dept), ""


def delete(db, key: str) -> int:
    """만든 폴더를 지운다 — 문서는 지우지 않고 폴더에서만 뺀다(검색 범위는 그대로). 뺀 문서 수."""
    if not key.startswith(MADE) or not key[len(MADE):].isdigit():
        return -1
    fid = int(key[len(MADE):])
    with db._conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM doc_folders WHERE folder_id = ?", (fid,)).fetchone()[0]
        conn.execute("DELETE FROM doc_folders WHERE folder_id = ?", (fid,))
        conn.execute("DELETE FROM library_folders WHERE id = ?", (fid,))
    return int(n)
