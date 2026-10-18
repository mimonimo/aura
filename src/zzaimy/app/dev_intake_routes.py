"""문서 반입(/dev/intake*) — 반입 연동 관리(원본 → 문서함 → 색인 → 그래프의 빈틈과 조치)와 Google Drive 가져오기 탭(/dev/nas*).

- 개발자(dev)만 — /dev 경로는 main.check_auth 가 막는다.
- DB·DB 경로(db_path)·처리기(processor)·받은 문서 폴더(inbox_dir)·화면 틀·화면 공통 맥락(page_ctx)과
  반입 작업 프로세스 표(intake_proc)는 main.create_app 이 app.state 에 올려 둔 것을 읽는다.
- 구글 드라이브 연결 라우트(/dev/gdrive/*)는 app/dev_gdrive_routes 에 있고, 여기 있는 탭 주소·되돌아갈 주소 도우미를 함께 쓴다.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote as _q

from fastapi import APIRouter, Form, HTTPException, Request
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
    pat = "zz_reconcile.sh" if kind == "reconcile" else "python.* scripts/175_pipeline_audit"
    return subprocess.run(["pgrep", "-f", pat], capture_output=True).returncode == 0


@router.get("/dev/intake", response_class=HTMLResponse)
def dev_intake(request: Request, tab: str = "", ok: str = "", err: str = "", probe: str = "", plan: str = ""):
    from zzaimy.app import intake_status
    from zzaimy.app import metrics
    st = request.app.state
    db, db_path = st.db, st.db_path
    if tab == "drive":
        # Google Drive 가져오기 — 문서 반입의 탭(예전 /dev/nas 는 이리로 보낸다)
        return _nas_page(request, ok, err, probe, plan)
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


# ---- NAS 수집 — 읽기 전용 공유 폴더에서 기준 문서·문서 추출로 자동 반입 (개발자 전용) ----

_DRIVE_TAB = "/dev/intake?tab=drive"


def _nas_redirect(msg: str, ok: bool = True) -> RedirectResponse:
    return RedirectResponse(f"{_DRIVE_TAB}&{'ok' if ok else 'err'}={_q(msg)}", status_code=303)


@router.get("/dev/nas")
def dev_nas(request: Request):
    """예전 주소 — 문서 반입의 Google Drive 탭으로(결과 글·확인 결과 인자는 그대로 넘긴다)."""
    qs = str(request.url.query or "")
    return RedirectResponse(_DRIVE_TAB + ("&" + qs if qs else ""), status_code=303)


def _nas_page(request: Request, ok: str = "", err: str = "", probe: str = "", plan: str = ""):
    from zzaimy.ingest import nas_sync

    st = request.app.state
    db = st.db
    # 연결 확인·미리보기 결과는 그 폴더 카드 바로 아래에 둔다(되돌아오는 주소의 #result-{id} 로 그 자리까지 내려간다)
    probe_result = plan_result = None
    from datetime import datetime as _dt

    checked_at = _dt.now().strftime("%H:%M:%S")
    if probe:
        src = nas_sync.get(probe)
        probe_result = {"id": src["id"], "name": src["name"], "at": checked_at, **nas_sync.probe(src)} if src else None
    if plan:
        src = nas_sync.get(plan)
        plan_result = {"id": src["id"], "name": src["name"], "at": checked_at, **nas_sync.plan(src)} if src else None
    smb_ok = True
    try:
        import smbclient  # noqa: F401
    except ImportError:
        smb_ok = False
    from zzaimy.app import storage_status
    from zzaimy.app.access_policy import LEVELS
    from zzaimy.ingest import gdrive

    return st.templates.TemplateResponse(request, "dev_nas.html", st.page_ctx(request, {
        "ok": ok, "err": err, "sources": nas_sync.list_public(),
        "backends": nas_sync.BACKENDS, "targets": nas_sync.TARGETS, "sectors": nas_sync.SECTORS,
        "probe_result": probe_result,
        "plan_result": plan_result, "type_groups": nas_sync.TYPE_GROUPS, "smb_ok": smb_ok,
        "gdrive": {**gdrive.public_status(), "redirect_uri": _gdrive_redirect_uri(request)},
        "levels": LEVELS, "dept_choices": [d.get("dept") for d in db.department_counts() if d.get("dept")],
        "storage": storage_status.snapshot(st.inbox_dir),
    }))


# ---- 구글 드라이브 원천 — 관리자가 클라이언트 ID·비밀을 넣고, 담당자가 자기 계정으로 한 번 허용한다(읽기 전용, ADR-0028) ----

def _gdrive_redirect_uri(request: Request) -> str:
    """구글에 등록해야 하는 되돌아올 주소 — 플랫폼의 공개 주소 + /dev/gdrive/callback."""
    base = os.environ.get("ZZAIMY_PUBLIC_URL", "").rstrip("/") or str(request.base_url).rstrip("/")
    return f"{base}/dev/gdrive/callback"


def _nas_form_bits(ext_group: list[str], interval_min: int) -> tuple[str, bool, int]:
    # 확장자 묶음 칩 → 확장자 목록, 자동 반입 선택(0=끔) → (auto, interval)
    exts = " ".join(ext_group)
    return exts, interval_min > 0, (interval_min if interval_min > 0 else 60)


@router.post("/dev/nas/probe-draft")
def dev_nas_probe_draft(backend: str = Form("local"), root: str = Form(""), username: str = Form(""),
                        password: str = Form(""), domain: str = Form(""), ext_group: list[str] = Form([]),
                        recursive: str = Form("1")):
    """저장 전에 입력한 값으로 연결을 확인한다 — 목록만 읽고 아무것도 저장하지 않는다."""
    from zzaimy.ingest import nas_sync

    exts, _auto, _iv = _nas_form_bits(ext_group, 0)
    draft = {"id": "draft", "name": "확인", "backend": backend, "root": root.strip(), "username": username.strip(),
             "password": password, "domain": domain.strip(), "extensions": nas_sync._norm_exts(exts),
             "recursive": recursive == "1"}
    if backend not in nas_sync.BACKENDS or not draft["root"]:
        return {"ok": False, "n_all": 0, "n_match": 0, "sample": [], "error": "방식과 경로를 먼저 적어 주세요"}
    return nas_sync.probe(draft, limit=5)


def _nas_draft(backend, root, username, password, domain, ext_group, recursive, since="", exclude="", max_mb=0,
               sid: str = "") -> dict:
    from zzaimy.ingest import nas_sync

    exts, _a, _i = _nas_form_bits(ext_group, 0)
    return {"id": sid or "draft", "name": "확인", "backend": backend, "root": root.strip(),
            "username": username.strip(), "password": password, "domain": domain.strip(),
            "extensions": nas_sync._norm_exts(exts), "recursive": recursive == "1",
            "since": since.strip(), "exclude": nas_sync._norm_excludes(exclude), "max_mb": int(max_mb or 0)}


@router.post("/dev/nas/browse-draft")
def dev_nas_browse_draft(backend: str = Form("local"), root: str = Form(""), username: str = Form(""),
                         password: str = Form(""), domain: str = Form(""), recursive: str = Form("1"),
                         rel: str = Form("")):
    """입력한 경로의 하위 폴더와 파일 종류별 개수 — 목록만 읽는다."""
    from zzaimy.ingest import nas_sync

    if backend not in nas_sync.BACKENDS or not root.strip():
        return {"ok": False, "dirs": [], "counts": {}, "n_files": 0, "other": 0, "error": "방식과 경로를 먼저 적어 주세요"}
    draft = _nas_draft(backend, root, username, password, domain, [], recursive)
    rel = rel.strip().strip("/\\")
    if ".." in rel.split("/") or ".." in rel.split("\\"):
        return {"ok": False, "dirs": [], "counts": {}, "n_files": 0, "other": 0, "error": "잘못된 경로"}
    return nas_sync.browse(draft, rel)


@router.post("/dev/nas/plan-draft")
def dev_nas_plan_draft(backend: str = Form("local"), root: str = Form(""), username: str = Form(""),
                       password: str = Form(""), domain: str = Form(""), ext_group: list[str] = Form([]),
                       recursive: str = Form("1"), since: str = Form(""), exclude: str = Form(""),
                       max_mb: int = Form(0), sid: str = Form("")):
    """저장 전 반입 미리보기 — 새로 들어올 파일 수와 예시(읽지 않음)."""
    from zzaimy.ingest import nas_sync

    if backend not in nas_sync.BACKENDS or not root.strip():
        return {"ok": False, "new": 0, "same": 0, "skipped": 0, "sample": [], "error": "방식과 경로를 먼저 적어 주세요"}
    return nas_sync.plan(_nas_draft(backend, root, username, password, domain, ext_group, recursive,
                                    since, exclude, max_mb, sid=sid))


@router.post("/dev/nas/{sid}/plan")
def dev_nas_plan(sid: str):
    return RedirectResponse(f"{_DRIVE_TAB}&plan={_q(sid)}#result-{_q(sid)}", status_code=303)


@router.post("/dev/nas/add")
def dev_nas_add(request: Request, name: str = Form(""), backend: str = Form("local"), root: str = Form(""),
                target: str = Form("regulation"), sector: str = Form("common"),
                username: str = Form(""), password: str = Form(""), domain: str = Form(""),
                ext_group: list[str] = Form([]), extensions: str = Form(""), recursive: str = Form("1"),
                auto: str = Form(""), interval_min: int = Form(0), since: str = Form(""),
                exclude: str = Form(""), max_mb: int = Form(0), dept: str = Form(""), access_level: str = Form("")):
    from zzaimy.ingest import nas_sync

    st = request.app.state
    exts, is_auto, iv = _nas_form_bits(ext_group, interval_min)
    if not exts:
        exts = extensions
    if auto == "1":
        is_auto, iv = True, (interval_min or 60)
    try:
        src = nas_sync.add(name, backend, root, target, sector=sector, username=username,
                           password=password, domain=domain, extensions=exts,
                           recursive=(recursive == "1"), auto=is_auto, interval_min=iv,
                           since=since, exclude=exclude, max_mb=max_mb, dept=dept, access_level=access_level)
    except ValueError as e:
        return _nas_redirect(str(e), ok=False)
    nas_sync.ensure_scheduler(st.db, st.processor, st.inbox_dir)
    return _nas_redirect(f"폴더 「{src['name']}」을 연결했습니다")


@router.post("/dev/nas/{sid}/update")
def dev_nas_update(request: Request, sid: str, name: str = Form(""), root: str = Form(""), target: str = Form(""),
                   sector: str = Form(""), username: str = Form(""), password: str = Form(""),
                   domain: str = Form(""), ext_group: list[str] = Form([]), extensions: str = Form(""),
                   recursive: str = Form("1"), auto: str = Form(""), interval_min: int = Form(0),
                   since: str = Form(""), exclude: str = Form(""), max_mb: int = Form(0),
                   dept: str = Form(""), access_level: str = Form("")):
    from zzaimy.ingest import nas_sync

    st = request.app.state
    exts, is_auto, iv = _nas_form_bits(ext_group, interval_min)
    if not exts:
        exts = extensions
    if auto == "1":
        is_auto, iv = True, (interval_min or 60)
    try:
        src = nas_sync.update(sid, name=name, root=root, target=target or None, sector=sector or None,
                              username=username, password=password, domain=domain,
                              extensions=exts or None, recursive=(recursive == "1"),
                              auto=is_auto, interval_min=iv, since=since, exclude=exclude, max_mb=max_mb,
                              dept=dept, access_level=access_level)
    except ValueError as e:
        return _nas_redirect(str(e), ok=False)
    nas_sync.ensure_scheduler(st.db, st.processor, st.inbox_dir)
    return _nas_redirect(f"폴더 「{src['name']}」 설정을 저장했습니다")


@router.post("/dev/nas/{sid}/delete")
def dev_nas_delete(sid: str):
    from zzaimy.ingest import nas_sync

    nas_sync.delete(sid)
    return _nas_redirect("폴더 연결을 지웠습니다 (이미 가져온 문서는 그대로)")


@router.post("/dev/nas/{sid}/probe")
def dev_nas_probe(sid: str):
    return RedirectResponse(f"{_DRIVE_TAB}&probe={_q(sid)}#result-{_q(sid)}", status_code=303)


@router.post("/dev/nas/{sid}/sync")
def dev_nas_sync(request: Request, sid: str, wait: str = Form("")):
    """반입 시작 — 보통은 백그라운드, wait=1 이면 끝날 때까지 기다린다(점검·테스트용)."""
    from zzaimy.ingest import nas_sync

    st = request.app.state
    db, processor, inbox_dir = st.db, st.processor, st.inbox_dir
    src = nas_sync.get(sid)
    if src is None:
        return _nas_redirect("없는 폴더입니다", ok=False)
    if wait == "1":
        r = nas_sync.sync(src, db, processor, inbox_dir)
        nas_sync._runs[sid] = {"running": False, "started": "", "progress": "", "last": r}
        return _nas_redirect(f"「{src['name']}」 가져오기 완료 — {r.get('summary') or r.get('error')}")
    if not nas_sync.start_sync(src, db, processor, inbox_dir):
        return _nas_redirect(f"「{src['name']}」은 이미 가져오는 중입니다", ok=False)
    return _nas_redirect(f"「{src['name']}」 가져오기를 시작했습니다 — 끝나면 이 화면에 결과가 남습니다")
