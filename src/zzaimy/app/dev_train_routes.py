"""데이터·학습(/dev/train)과 도구·모델 연결(/dev/tools) — 학습 도구 연결·반출 묶음·학습 상태·학습셋 보기.

데이터·학습은 탭 둘: 「검수·학습」(기본)과 「수집 자료」(tab=collect — 운영 기록의 가린 사본 묶음, 값은 collect.page_context).

도구는 각자 프로세스로 돌고 플랫폼은 연결·임베드만 한다(ADR-0011). GPU 도구는 DGX 에서 돈다. 주소는 설정에 저장
(없으면 미연결로 표시). 반출 묶음은 선택 항목만 개방 표준으로 내보낸다(ADR-0012).

- 개발자(dev)만 — /dev 경로는 main.check_auth 가 막는다.
- DB·화면 틀·화면 공통 맥락(page_ctx)과 다른 화면과 함께 쓰는 도우미(Label Studio 연결 상태·검수 현황·LLM 연결 안내 이동)는
  main.create_app 이 app.state 에 올려 둔 것을 읽는다.
"""
from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from zzaimy.app import serving_plan

router = APIRouter()


def _train_redirect(request: Request, msg: str, ok: bool = True) -> RedirectResponse:
    return request.app.state.llm_redirect(msg, ok)


# ---- 모델 학습 — 기성 오픈소스 도구 연결 (ADR-0011) ----
# 도구는 각자 프로세스로 돌고 플랫폼은 연결·임베드만 한다. GPU 도구는 DGX 에서
# 돈다. 주소는 설정에 저장(없으면 미연결로 표시).

# 도구·모델 연결(/dev/tools)에는 Label Studio 계정만. TensorBoard(진행 그래프)는 링크·상태·주소 모두 데이터·학습(/dev/train)
# 「학습·평가」 한 곳에 둔다(10-25 — 학습 도구는 모델 학습 화면에서).
_TRAIN_TOOLS = [
    {"key": "labelstudio", "name": "Label Studio", "step": "검수",
     "role": "학습 예시 검수",
     "gpu": False, "setting": "labelstudio_url"},
]
_TB_SETTING = "tensorboard_url"


def _tensorboard_status() -> dict:
    """TensorBoard 가 같은 서버에서 도는지 — 기록 목록(runs) 주소를 짧게 묻는다. 주소는 ZZAIMY_TENSORBOARD_PROBE(기본 VM 의 /tb/)."""
    import json as _json
    import urllib.request as _ur

    url = os.environ.get("ZZAIMY_TENSORBOARD_PROBE", "http://127.0.0.1:6006/tb/data/runs")
    try:
        opener = _ur.build_opener(_ur.ProxyHandler({}))
        with opener.open(url, timeout=2) as r:
            runs = _json.loads(r.read() or b"[]")
        return {"ok": True, "detail": f"학습 기록 {len(runs)}개" if isinstance(runs, list) else ""}
    except Exception as e:  # noqa: BLE001 — 상태 표시일 뿐
        return {"ok": False, "detail": "응답 없음", "error": type(e).__name__}   # 까닭은 툴팁(관리자 화면)


@router.get("/dev/train", response_class=HTMLResponse)
def dev_train(request: Request, err: str = "", ok: str = "", tab: str = "data"):
    # 도구·모델 연결은 /dev/tools. 수집 자료는 이 화면의 탭 — 모르는 tab 값은 첫 탭
    if tab == "collect":
        from zzaimy.app import collect
        st = request.app.state
        return st.templates.TemplateResponse(request, "dev_data.html", st.page_ctx(request, {
            "tab": "collect", "err": err, "ok": ok, **collect.page_context(request)}))
    return _dev_data_view(request, err, ok)


@router.get("/dev/tools", response_class=HTMLResponse)
def dev_tools(request: Request, err: str = "", ok: str = ""):
    """도구·모델 연결 — 학습 도구 주소·계정, LLM 연결·단계별 모델, 계획 모델 서빙, 외부 연결(웹 검색)."""
    # 연결마다 서버가 지금 내어 주는 모델 목록 — 저장된 값이 아니라 실시간(짧게 캐시)
    # Label Studio 는 실제 연결 여부(connected)로 표시 — 주소만 있다고 연결됨이 아니다.
    from zzaimy.app import main as _main   # 모델 목록 캐시(_live_models_cached)는 main 에 있다 — 시험이 바꿔 끼운다
    from zzaimy.app import web_search
    from zzaimy.dataset import ls_admin
    from zzaimy.generate import llm_connections, model_config

    st = request.app.state
    db = st.db

    # 연결 확인은 동시에 돌리고, 연결마다 한 번만 묻는다.
    from concurrent.futures import ThreadPoolExecutor
    public_connections = llm_connections.list_public()
    with ThreadPoolExecutor(max_workers=4) as probes:
        live_results = list(probes.map(_main._live_models_cached, [c['id'] for c in public_connections]))
    live_by_id = dict(zip([c['id'] for c in public_connections], live_results))

    ls = st.ls_status()
    tools = [{**t, "url": db.get_setting(t["setting"], ""), "connected": ls["ok"]} for t in _TRAIN_TOOLS]
    llm = model_config.current()
    llm_probe = (live_by_id.get(llm.get("connection_id")) or model_config.probe()) if llm["configured"] else {"ok": False, "models": [], "error": "미확인"}
    return st.templates.TemplateResponse(request, "dev_tools.html", st.page_ctx(request, {
        "err": err, "ok": ok,
        "tools": tools,
        "ls": ls,
        "ls_username": db.get_setting("labelstudio_username", ""),
        "ls_token_set": bool(db.get_setting("labelstudio_token")),
        "ls_admin_available": ls_admin.available(),
        "llm": llm, "llm_probe": llm_probe,
        "connections": [dict(c, usage=model_config.usage_today(c["id"]),
                             live=live_by_id[c["id"]])
                        for c in public_connections],
        "llm_kinds": llm_connections.KINDS,
        "usage_all": model_config.usage_today(),
        "llm_roles": llm_connections.roles_public(),
        "role_labels": llm_connections.ROLES,
        "serving_plan": serving_plan.status(lambda cid: live_by_id.get(cid) or _main._live_models_cached(cid)),
        "train_host": serving_plan.training_box(),
        # 입력 예시는 계획서 모델에서(절대 규칙 9 — 계획 밖 모델 이름을 예로 들지 않는다)
        "plan_writer": next((p["base"] for p in serving_plan.PLAN if p["key"] == "answer"), ""),
        # 외부 연결 — 개인정보 화면에서 옮겨 온 칸
        "search_provider": web_search.provider(),
    }))


@router.get("/dev/train/export.zip")
def dev_train_export(
    request: Request, rag: int | None = None, datasets: int | None = None, model: int | None = None,
):
    """학습 산출물 반출 번들 — 선택 항목만 개방 표준으로 (ADR-0012).

    폼은 항목마다 hidden 0 + checkbox 1 을 보내므로 해제한 항목은 0으로 온다.
    인자가 하나도 없는 맨 URL은 전체 반출."""
    from datetime import datetime as _dt

    from zzaimy.export.bundle import build_bundle

    flags = (("rag", rag), ("datasets", datasets), ("model", model))
    if all(v is None for _, v in flags):
        include = {k for k, _ in flags}
    else:
        include = {k for k, on in flags if on}
    if not include:
        return RedirectResponse(
            "/dev/train?tab=data&err=반출할 항목을 하나 이상 선택하세요", status_code=303
        )
    model_dir = os.environ.get("ZZAIMY_MODEL_DIR")
    data, _manifest = build_bundle(request.app.state.db, model_dir=model_dir, include=include)
    from fastapi.responses import Response as _Resp

    stamp = _dt.now().strftime("%Y%m%d")
    return _Resp(
        data, media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="zzaimy-artifacts-{stamp}.zip"'},
    )


@router.get("/dev/train/export/file")
def dev_train_export_file(request: Request, path: str):
    """반출 목록의 파일 하나를 그대로 내려받는다 — 묶음이 아니면 zip 을 거칠 이유가 없다."""
    from zzaimy.export.bundle import single_file

    data = single_file(request.app.state.db, path, model_dir=os.environ.get("ZZAIMY_MODEL_DIR"))
    if data is None:
        raise HTTPException(404, "반출 목록에 없는 파일입니다")
    from fastapi.responses import Response as _Resp

    name = path.rsplit("/", 1)[-1]
    return _Resp(data, media_type="application/octet-stream",
                 headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/dev/train/run", response_class=HTMLResponse)
def dev_train_run(request: Request, run: str = ""):
    """학습 상태 — DGX 학습 기록기의 숫자로 손실 추세·튐·검증 손실·메모리 여유·멈춤을 판정한다(app/train_monitor)."""
    import time as _t
    from zzaimy.app import train_monitor as tm

    st = request.app.state
    rs = tm.runs()
    cur = tm.load(run or (rs[0]["name"] if rs else ""))
    charts, checks, updated = [], [], ""
    if cur:
        checks = tm.judge(cur, all_runs=rs)
        updated = _t.strftime("%m-%d %H:%M", _t.localtime(cur["mtime"]))
        loss = cur["loss"]
        charts = [
            {"title": "학습 손실", "points": tm.spark([r["loss"] for r in loss]),
             "note": f"{len(loss)}개 · 처음 {loss[0]['loss']:.3f} → 최근 {loss[-1]['loss']:.3f}" if loss else ""},
            {"title": "검증 손실", "points": tm.spark([r["eval_loss"] for r in cur["eval"]]),
             "note": f"{len(cur['eval'])}개 · 최저 {min(r['eval_loss'] for r in cur['eval']):.3f}" if cur["eval"] else ""},
            {"title": "학습률", "points": tm.spark([r["lr"] for r in loss]), "note": f"최근 {loss[-1]['lr']:.2e}" if loss and loss[-1]["lr"] else ""},
            {"title": "시스템 메모리(GB)", "points": tm.spark([g["mem"] for g in cur["gpu"]]),
             "note": (lambda m: f"최고 {max(m) / 1024:.0f}GB · 지금 {m[-1] / 1024:.0f}GB / 전체 {tm.MEM_TOTAL_MIB / 1024:.0f}GB" if m else "")(
                 [g["mem"] for g in cur["gpu"] if g["mem"] is not None])},
        ]
    return st.templates.TemplateResponse(request, "dev_train_run.html", st.page_ctx(request, {
        "runs": rs, "run": cur, "checks": checks, "charts": charts, "updated": updated,
        "tensorboard_url": st.db.get_setting("tensorboard_url", "")}))


@router.get("/dev/train/trainset/{name}/{file}")
def dev_trainset_file(name: str, file: str):
    """DGX 학습셋 파일 내려받기(개발자만 — /dev 전역 검사). 문답에는 학교 원문 발췌가 들어 있어 내려받은 기록을 남긴다."""
    from fastapi.responses import FileResponse
    from zzaimy.export.bundle import trainset_file

    p = trainset_file(name, file)
    if p is None:
        raise HTTPException(404, "목록에 없는 학습셋 파일입니다")
    logging.getLogger("zzaimy.app").info("학습셋 내려받기 %s/%s", name, file)
    return FileResponse(p, media_type="application/octet-stream", filename=f"{name}-{file}")


@router.get("/dev/train/trainset/{name}/{file}/view", response_class=HTMLResponse)
def dev_trainset_view(request: Request, name: str, file: str, start: int = 0):
    """학습셋 표본 보기 — 20건씩 질문·답(역할별)을 읽는다."""
    from zzaimy.export.bundle import trainset_file, trainset_sample

    st = request.app.state
    p = trainset_file(name, file)
    if p is None or not file.endswith(".jsonl"):
        raise HTTPException(404, "목록에 없는 학습셋 파일입니다")
    start = max(0, start)
    rows, total = trainset_sample(p, start, 20)
    return st.templates.TemplateResponse(request, "dev_trainset_view.html", st.page_ctx(request, {
        "name": name, "file": file, "rows": rows, "start": start, "total": total}))


@router.post("/dev/train/ls-password")
def dev_train_ls_password(request: Request, new_pw: str = Form(...), confirm: str = Form("")):
    """Label Studio 계정 비밀번호 재설정 — 값은 환경변수로 헬퍼에만 전달한다."""
    from zzaimy.dataset import ls_admin

    if len(new_pw) < 8:
        return _train_redirect(request, "비밀번호는 8자 이상이어야 합니다", ok=False)
    if new_pw != confirm:
        return _train_redirect(request, "비밀번호 확인이 일치하지 않습니다", ok=False)
    ok, msg = ls_admin.set_password(new_pw)
    if not ok:
        return _train_redirect(request, f"Label Studio 비밀번호 재설정 실패: {msg}", ok=False)
    return _train_redirect(
        request, "Label Studio 비밀번호를 재설정했습니다. 새 비밀번호로 로그인하세요")


@router.post("/dev/train/url")
def dev_tool_url(request: Request, setting: str = Form(...), url: str = Form(""), back: str = Form("/dev/train#training-tools")):
    # Label Studio 주소는 구축 스크립트(scripts/68_labelstudio_token.sh)가 넣는다 —
    # 화면에서는 GPU 도구(TensorBoard) 주소만 바꾼다(데이터·학습 「학습·평가」 카드)
    if setting != _TB_SETTING:
        raise HTTPException(400, "알 수 없는 도구입니다")
    url = url.strip()
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(400, "http(s):// 주소여야 합니다")
    request.app.state.db.set_setting(setting, url)
    dest = back if back.startswith("/dev/") else "/dev/train#training-tools"
    return RedirectResponse(dest, status_code=303)


def _review_status(request: Request) -> dict:
    """「문답 검수」 카드의 값 — Label Studio 프로젝트 전부와 진행(완료/전체), 각 프로젝트 주소, 닿지 않을 때의 상태 표시.
    「완료한 검수 결과 반영」 은 근거 문답 검수 프로젝트(authored_review.PROJECT)가 있을 때만."""
    from zzaimy.dataset.authored_review import PROJECT

    st = request.app.state
    url = (st.db.get_setting("labelstudio_url") or "").rstrip("/")
    ls = st.ls_projects()
    projects = [{**p, "href": f"{url}/projects/{p['id']}/data" if url else "",
                 "pct": round(100 * p["done"] / p["total"]) if p["total"] else 0} for p in ls.get("projects") or []]
    return {"ok": bool(ls.get("ok")), "projects": projects,
            "note": st.grounded_status_note(ls, url),
            "can_pull": any(p["title"] == PROJECT for p in projects),
            "list_href": f"{url}/projects" if url else ""}


@router.get("/dev/train/review-status")
def dev_train_review_status(request: Request):
    """문답 검수 진행 — 화면이 1분마다 다시 읽는다(30초 캐시)."""
    r = _review_status(request)
    return {"ok": r["ok"], "projects": r["projects"]}


def _dev_data_view(request: Request, err: str = "", ok: str = ""):
    from zzaimy.app.model_tracks import DECISIONS
    from zzaimy.dataset.tracks import NAMES as TRACK_NAMES
    from zzaimy.export.bundle import preview_bundle, set_state, trainset_track_samples, trainsets

    st = request.app.state
    db = st.db
    try:
        export_preview = preview_bundle(db, model_dir=os.environ.get("ZZAIMY_MODEL_DIR"))
    except Exception:
        export_preview = []
    sets = next((g.get("sets") or [] for g in export_preview if g.get("group") == "datasets"), None)
    if sets is None:
        sets = trainsets()
    rows = [{**t, "state": set_state(t, sets, DECISIONS)} for t in sets]
    # 학습셋 구성 — 지금 학습셋(승인된 것 먼저)의 갈래 × 학습·검증·시험 수와 갈래마다 표본 몇 건
    staging = sorted((t for t in sets if t.get("kind") == "staging"), key=lambda t: t.get("status") not in ("sft_approved", "approved_for_sft"))
    mix = staging[0] if staging else None
    samples = trainset_track_samples(mix["name"]) if mix and "train.jsonl" in (mix.get("files") or []) else {}
    try:
        review_report = json.loads(db.get_setting('grounded_review_report', '{}') or '{}')
    except ValueError:
        review_report = {}
    return st.templates.TemplateResponse(request, "dev_data.html", st.page_ctx(request, {
        "grounded_review_report": review_report,
        "development_tab": "data",
        "tab": "data",
        "export_preview": export_preview,
        "set_rows": rows, "mix": mix, "mix_samples": samples, "track_names": TRACK_NAMES,
        "tensorboard_url": db.get_setting(_TB_SETTING, ""),
        "tensorboard_status": _tensorboard_status(),
        "review": _review_status(request),
        "err": err, "ok": ok,
    }))


# 검수 가이드(매뉴얼)는 문답 검수 화면(/review, app/review_routes)으로 옮겼다 — 예전 주소 /dev/train/review-guide 는 거기서 넘긴다
