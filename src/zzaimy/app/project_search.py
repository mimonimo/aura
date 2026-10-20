"""계정별 프로젝트 검색. 사이드바의 최근 20개 제한과 독립적으로 조회한다.

프로젝트 이름뿐 아니라 그 프로젝트에 든 문서 제목·대화 제목에서도 찾는다 — 이름을 줄여 지은 프로젝트(「인공지능」)도
사업 이름(「부트캠프」)으로 찾을 수 있게. 찾은 까닭(matched)을 함께 돌려 화면이 알린다."""
from fastapi import APIRouter, HTTPException, Query, Request, Response

router = APIRouter()
SECTORS = {"grant": "국고사업", "recruit": "채용", "admission": "입학", "auto": "일반 행정", "common": "공통"}


def search(db, owner: str, q: str = "", offset: int = 0, peers: tuple[str, ...] = (), shared: tuple[int, ...] | list[int] = ()) -> dict:
    """진행 중인 내 프로젝트와 같은 부서 동료(peers)의 프로젝트(읽기 전용 — readonly). 동료 프로젝트는 이름·업무 영역으로만 찾는다 —
    문서 제목·대화 제목으로 찾으면 볼 수 없는 문서나 비공개 대화가 있는지를 검색으로 떠볼 수 있다."""
    owners = [owner, *[p for p in peers if p and p != owner]]
    shared = [int(x) for x in shared] or [-1]          # 공유받은 프로젝트(project_access) — 이름·업무 영역으로만 찾는다
    term = q.strip()
    pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    sector = next((key for key, label in SECTORS.items() if label == term), "")
    with db._conn() as conn:
        latest = 'GREATEST' if getattr(conn, 'dialect', '') == 'postgres' else 'MAX'
        rows = conn.execute(
            f"""SELECT p.id, p.name, p.sector, p.created_at, p.owner,
                (SELECT COUNT(*) FROM documents d WHERE d.project_id=p.id AND d.owner=p.owner) AS n_docs,
                (SELECT COUNT(*) FROM chat_sessions c WHERE c.project_id=p.id AND c.owner=p.owner
                    AND (p.owner=? OR COALESCE(c.is_private, 0)=0)) AS n_chats,
                {latest}(p.created_at,
                    COALESCE((SELECT MAX(d.created_at) FROM documents d
                              WHERE d.project_id=p.id AND d.owner=p.owner), p.created_at),
                    COALESCE((SELECT MAX(m.created_at) FROM chat_messages m
                              JOIN chat_sessions c ON c.id=m.session_id
                              WHERE c.project_id=p.id AND c.owner=p.owner), p.created_at)) AS last_activity
                ,CASE WHEN p.name LIKE ? ESCAPE '\\' OR p.sector=? THEN 'name'
                      WHEN EXISTS (SELECT 1 FROM documents d WHERE d.project_id=p.id AND d.owner=p.owner AND p.owner=?
                                   AND d.filename LIKE ? ESCAPE '\\') THEN 'document'
                      ELSE 'chat' END AS matched
                FROM projects p
                WHERE (p.owner IN ({','.join('?' * len(owners))}) OR p.id IN ({','.join('?' * len(shared))})) AND p.archived = 0 AND (p.name LIKE ? ESCAPE '\\' OR p.sector=?
                      OR (p.owner=? AND EXISTS (SELECT 1 FROM documents d WHERE d.project_id=p.id AND d.owner=p.owner
                                 AND d.filename LIKE ? ESCAPE '\\'))
                      OR (p.owner=? AND EXISTS (SELECT 1 FROM chat_sessions c WHERE c.project_id=p.id AND c.owner=p.owner
                                 AND c.title LIKE ? ESCAPE '\\')))
                ORDER BY CASE WHEN p.name LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END, last_activity DESC, p.id DESC
                LIMIT 31 OFFSET ?""",
            (owner, pattern, sector, owner, pattern, *owners, *shared, pattern, sector, owner, pattern, owner, pattern, pattern, offset),
        ).fetchall()
    return {"projects": [dict(row, sector_label=SECTORS.get(row["sector"], row["sector"]), readonly=row["owner"] != owner)
                         for row in rows[:30]], "has_more": len(rows) > 30}


@router.get("/api/projects/search")
def project_search(request: Request, response: Response,
                   q: str = Query("", max_length=200), offset: int = Query(0, ge=0)):
    owner = getattr(request.state, "user", None)
    if not owner:
        raise HTTPException(401, "로그인이 필요합니다.")
    response.headers["Cache-Control"] = "no-store"
    shared = getattr(request.app.state, "shared_project_ids", None)
    return search(request.app.state.db, owner, q, offset, (), shared(request) if shared else ())
