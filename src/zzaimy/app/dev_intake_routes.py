"""문서 가져오기(/dev/intake*) — 원본 → 문서함 → 색인 → 그래프의 빈틈과 조치(맞추기·다시 점검).

- 개발자(dev)만 — /dev 경로는 main.check_auth 가 막는다.
- DB·DB 경로(db_path)·처리기(processor)·받은 문서 폴더(inbox_dir)·화면 틀·화면 공통 맥락(page_ctx)과
  반입 작업 프로세스 표(intake_proc)는 main.create_app 이 app.state 에 올려 둔 것을 읽는다.
- 구글 앱 등록·계정 연결(/dev/gdrive/*)은 app/dev_gdrive_routes, 화면은 사용자·계정(/dev/users#google-links).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

router = APIRouter()


# 반입 연동 관리 — 원본 → 문서함 → 색인 → 그래프의 빈틈(scripts/175)과 조치(맞추기·다시 점검). 개발자만(/dev 미들웨어)
_INTAKE_JOBS = {"reconcile": ("zz_reconcile.sh", Path("/tmp/intake_reconcile.log")),
                "audit": ("175_pipeline_audit.py", Path("/tmp/intake_audit.log"))}


def _intake_job_running(request: Request, kind: str) -> bool:
    p = request.app.state.intake_proc.get(kind)
    if p is not None and p.poll() is None:
        return True
    import subprocess
    # 실행 중인 그 스크립트만 — 명령 줄 맨 앞이 셸·파이썬인 프로세스. 이름이 글로 들어간 다른 명령(ssh 로 상태를 묻는 셸,
    # grep 등)까지 잡으면 놀고 있어도 「맞추는 중…」으로 보인다
    pat = (r"^([^ ]*/)?(ba)?sh .*zz_reconcile\.sh" if kind == "reconcile"
           else r"^([^ ]*/)?python[0-9.]* .*scripts/175_pipeline_audit")
    return subprocess.run(["pgrep", "-f", pat], capture_output=True).returncode == 0


@router.get("/dev/intake", response_class=HTMLResponse)
def dev_intake(request: Request, tab: str = ""):
    from zzaimy.app import intake_status
    from zzaimy.app import metrics
    st = request.app.state
    db, db_path = st.db, st.db_path
    if tab == "drive":
        # 예전 Google Drive 가져오기 탭 — 앱 등록·계정 연결은 사용자·계정 화면에
        return RedirectResponse(_GOOGLE, status_code=303)
    if tab == "areas":
        # 폴더별 담당 부서는 라이브러리 폴더 목록의 「공개 범위 바꾸기」 한 곳에서 바꾼다(같은 설정) — RAG 공간에서 본다
        return RedirectResponse("/dev/rag", status_code=303)
    data = intake_status.pipeline(db_path)
    return st.templates.TemplateResponse(request, "dev_intake.html", st.page_ctx(request, {
        "p": data, "live": metrics.live(db), "lg": intake_status.ledger_breakdown(db), "running": {k: _intake_job_running(request, k) for k in _INTAKE_JOBS},
        "missed": intake_status.listing(db_path, "missed")[:50], "failed": intake_status.listing(db_path, "failed")[:50]}))


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


@router.post("/dev/intake/{kind}")
def dev_intake_run(request: Request, kind: str):
    import subprocess
    if kind not in _INTAKE_JOBS:
        raise HTTPException(404)
    if _intake_job_running(request, kind):
        return RedirectResponse("/dev/intake?err=이미 실행 중입니다", status_code=303)
    root = Path(__file__).resolve().parents[3]
    script, log_path = _INTAKE_JOBS[kind]
    cmd = (["bash", str(root / "scripts" / script)] if script.endswith(".sh") else
           [str(root / "scripts" / "zz_run.sh"), "4G", "env", "PYTHONPATH=src", str(root / ".venv" / "bin" / "python"),
            str(root / "scripts" / script)])
    with log_path.open("w") as log_f:
        from zzaimy.app.detach import detached
        request.app.state.intake_proc[kind] = subprocess.Popen(detached(["nohup", *cmd], kind), stdout=log_f, stderr=subprocess.STDOUT,
                                                               cwd=str(root), start_new_session=True)
    return RedirectResponse("/dev/intake?ok=" + ("맞추기를 시작했습니다" if kind == "reconcile" else "점검을 시작했습니다"), status_code=303)


# ---- 예전 Google Drive 가져오기 탭 — 폴더 연결 기능은 뺐다(문서함·채팅이 파일 단위로 가져온다).
#      앱 등록·계정 연결 현황은 사용자·계정 화면에 있다. 옛 주소는 그리로 보낸다.

_GOOGLE = "/dev/users#google-links"


@router.get("/dev/nas")
def dev_nas():
    return RedirectResponse(_GOOGLE, status_code=303)
