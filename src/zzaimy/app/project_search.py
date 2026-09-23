"""계정별 프로젝트 검색. 사이드바의 최근 20개 제한과 독립적으로 조회한다."""
from fastapi import APIRouter, HTTPException, Query, Request, Response

router = APIRouter()
SECTORS = {"grant": "국고사업", "recruit": "채용", "admission": "입학", "common": "공통"}


def search(db, owner: str, q: str = "", offset: int = 0) -> dict:
    term = q.strip()
    pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    sector = next((key for key, label in SECTORS.items() if label == term), "")
    with db._conn() as conn:
        rows = conn.execute(
            """SELECT p.id, p.name, p.sector, p.created_at,
                (SELECT COUNT(*) FROM documents d WHERE d.project_id=p.id AND d.owner=p.owner) AS n_docs,
                (SELECT COUNT(*) FROM chat_sessions c WHERE c.project_id=p.id AND c.owner=p.owner) AS n_chats,
                MAX(p.created_at,
                    COALESCE((SELECT MAX(d.created_at) FROM documents d
                              WHERE d.project_id=p.id AND d.owner=p.owner), p.created_at),
                    COALESCE((SELECT MAX(m.created_at) FROM chat_messages m
                              JOIN chat_sessions c ON c.id=m.session_id
                              WHERE c.project_id=p.id AND c.owner=p.owner), p.created_at)) AS last_activity
                FROM projects p
                WHERE p.owner=? AND (p.name LIKE ? ESCAPE '\\' OR p.sector=?)
                ORDER BY last_activity DESC, p.id DESC LIMIT 31 OFFSET ?""",
            (owner, pattern, sector, offset),
        ).fetchall()
    return {"projects": [dict(row, sector_label=SECTORS.get(row["sector"], row["sector"]))
                         for row in rows[:30]], "has_more": len(rows) > 30}


@router.get("/api/projects/search")
def project_search(request: Request, response: Response,
                   q: str = Query("", max_length=200), offset: int = Query(0, ge=0)):
    owner = getattr(request.state, "user", None)
    if not owner:
        raise HTTPException(401, "로그인이 필요합니다.")
    response.headers["Cache-Control"] = "no-store"
    return search(request.app.state.db, owner, q, offset)
