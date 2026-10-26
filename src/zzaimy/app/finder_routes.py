"""공용 디렉터리 창(static/aura-finder.js)의 읽기 경로 — 부서 폴더·구글 드라이브를 한 창에서 고르고, 전체에서 이름으로 찾는다.

- /api/finder/drive  드라이브 폴더 하나(accept=hwp 한글만, docs 문서 갈래 전부 — 구글 문서·시트·슬라이드 포함)
- /api/finder/recent 최근 기안기로 연 문서(지금 열 수 있는 것만, 새것 먼저)
- /api/finder/search 쓸 수 있는 부서 폴더 전부 + (연결돼 있으면) 드라이브에서 이름 낱말로 찾기, 결과마다 위치
쓰기·들이기는 각 화면의 경로가 맡는다(대화 작업본 묶기, 프로젝트 문서 들이기).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from zzaimy.app import webhwp
from zzaimy.app.webhwp_routes import _base, _depts, _label, _need_staff, recent_files

router = APIRouter()
ACCEPT = ("hwp", "docs")


def _email(request: Request) -> str:
    from zzaimy.ingest import gdrive_files
    return gdrive_files.account_for(request.app.state.db, getattr(request.state, "user", ""),
                                    getattr(request.state, "dept", "") or None) or ""


@router.get("/api/finder/drive")
def finder_drive(request: Request, folder: str = "", drive: str = "", accept: str = "hwp"):
    _need_staff(request)
    from zzaimy.app import webhwp_drive
    email = _email(request)
    if not email:
        raise HTTPException(409, "학교 구글 계정이 연결되어 있지 않습니다 — 설정에서 「내 학교 계정 연결」 뒤 다시 해 주세요")
    accept = accept if accept in ACCEPT else "hwp"
    try:
        out = webhwp_drive.browse(email, folder, drive, accept=accept)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    if not folder and not drive:
        out["drives"] = webhwp_drive.drives(email)
    return {"account": email, **out}


@router.get("/api/finder/recent")
def finder_recent(request: Request):
    """최근 연 문서 — 지금 쓸 수 있는 부서의, 디스크에 남아 있는 것만."""
    _need_staff(request)
    st = request.app.state
    base = _base(request)
    depts = set(_depts(request))
    out = []
    for fid in recent_files(request):
        r = webhwp.get(st.db, fid)
        if not r or not Path(r["path"]).exists() or any(x in webhwp.HIDDEN for x in Path(r["path"]).parts):
            continue
        dept = webhwp.dept_of_path(base, r["path"])
        if dept not in depts:
            continue
        m = webhwp.meta(st.db, fid)
        out.append({"id": fid, "name": r["name"], "dept": dept, "label": _label(request, dept), "folder": webhwp.folder_of(base, r["path"]),
                    "saved": (m.get("saved") or m.get("created") or r.get("created_at") or "")[:16].replace("T", " ")})
    return {"files": out}


@router.get("/api/finder/search")
def finder_search(request: Request, q: str = "", accept: str = "hwp"):
    """부서 폴더(쓸 수 있는 곳만)와 드라이브에서 이름으로 찾는다. 드라이브는 연결 안 됐거나 실패해도 부서 결과는 돌려준다."""
    _need_staff(request)
    words = [w for w in q.lower().split() if w][:6]
    if not words:
        return {"dept": [], "drive": [], "drive_state": "skip"}
    st = request.app.state
    base = _base(request)
    depts = set(_depts(request))
    hits = []
    for r in st.db.list_files(kind="webhwp", limit=8000):
        p = Path(r["path"])
        name = str(r["name"] or "")
        if not all(w in name.lower() for w in words):
            continue
        dept = webhwp.dept_of_path(base, r["path"])
        if dept not in depts or not p.exists() or any(x in webhwp.HIDDEN for x in p.parts):
            continue
        m = webhwp.meta(st.db, r["id"])
        hits.append({"id": r["id"], "name": name, "dept": dept, "folder": webhwp.folder_of(base, r["path"]),
                     "saved": (m.get("saved") or m.get("created") or r.get("created_at") or "")[:10]})
    hits.sort(key=lambda x: x["saved"], reverse=True)
    out = {"dept": hits[:60], "drive": [], "drive_state": "ok"}
    email = _email(request)
    if not email:
        out["drive_state"] = "no-account"
        return out
    from zzaimy.app import webhwp_drive
    try:
        out["drive"] = webhwp_drive.list_files(email, " ".join(words), accept=accept if accept in ACCEPT else "hwp")["files"]
    except Exception:                                    # 드라이브가 안 닿아도 부서 결과는 보인다
        out["drive_state"] = "fail"
    return out
