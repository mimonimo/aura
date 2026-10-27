"""반입 점검 — 원본 → 문서함 → 색인 → 그래프의 빈틈. 화면은 처리 흐름(/dev/flow#intake), 여기는 값·목록 내려받기·옛 주소.

- 개발자(dev)만 — /dev 경로는 main.check_auth 가 막는다.
- DB·DB 경로(db_path)는 main.create_app 이 app.state 에 올려 둔 것을 읽는다.
- 구글 앱 등록·계정 연결(/dev/gdrive/*)은 app/dev_gdrive_routes, 화면은 조직 관리(/team#google-links, 관리자만).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

router = APIRouter()


def intake_ctx(request: Request) -> dict:
    """반입 점검 칸(처리 흐름 화면, templates/_intake_panel.html)에 넘길 값 — 수치는 점검 파일·DB·원본 장부에서 바로 읽는다.
    점검과 빠진 원본 다시 들이기는 자동이라(동기화 때·매일 04:30) 화면에서 돌리는 단추는 두지 않는다(10-26)."""
    from zzaimy.app import intake_status, metrics
    st = request.app.state
    return {"p": intake_status.pipeline(st.db_path), "live": metrics.live(st.db), "lg": intake_status.ledger_breakdown(st.db),
            "missed": intake_status.listing(st.db_path, "missed")[:50], "failed": intake_status.listing(st.db_path, "failed")[:50]}


@router.get("/dev/intake")
def dev_intake(request: Request):
    """옛 「문서 가져오기」 주소 — 처리 흐름의 반입 점검 칸으로."""
    return RedirectResponse("/dev/flow#intake", status_code=303)


@router.get("/dev/intake/{name}.csv")
def dev_intake_csv(request: Request, name: str):
    import csv
    import io
    from zzaimy.app import intake_status
    if name not in ("missed", "failed"):
        raise HTTPException(404)
    rows = intake_status.listing(request.app.state.db_path, name)
    buf = io.StringIO()
    cols = ["rel", "ext", "seen_at"] if name == "missed" else ["rel", "reason", "error"]
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c, "") for c in cols])
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=intake_{name}.csv"})


# ---- 예전 Google Drive 가져오기 탭 — 폴더 연결 기능은 뺐다(문서함·채팅이 파일 단위로 가져온다).
#      앱 등록·계정 연결 현황은 사용자·계정 화면에 있다. 옛 주소는 그리로 보낸다.

