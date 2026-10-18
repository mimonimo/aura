"""데이터·학습(/dev/train)과 도구·모델 연결(/dev/tools) — 학습 도구 연결·반출 묶음·학습 상태·학습셋 보기.

도구는 각자 프로세스로 돌고 플랫폼은 연결·임베드만 한다(ADR-0011). GPU 도구는 DGX 에서 돈다. 주소는 설정에 저장
(없으면 미연결로 표시). 반출 묶음은 선택 항목만 개방 표준으로 내보낸다(ADR-0012).

- 개발자(dev)만 — /dev 경로는 main.check_auth 가 막는다.
- DB·화면 틀·화면 공통 맥락(page_ctx)과 다른 화면과 함께 쓰는 도우미(Label Studio 연결 상태·검수 현황·LLM 연결 안내 이동·
  무거운 조회 대기열 dev_slow)는 main.create_app 이 app.state 에 올려 둔 것을 읽는다.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from zzaimy.app import serving_plan

router = APIRouter()


def _train_redirect(request: Request, msg: str, ok: bool = True) -> RedirectResponse:
    return request.app.state.llm_redirect(msg, ok)


# ---- 모델 학습 — 기성 오픈소스 도구 연결 (ADR-0011) ----
# 도구는 각자 프로세스로 돌고 플랫폼은 연결·임베드만 한다. GPU 도구는 DGX 에서
# 돈다. 주소는 설정에 저장(없으면 미연결로 표시).

_TRAIN_TOOLS = [
    {"key": "labelstudio", "name": "Label Studio", "step": "검수",
     "role": "학습 예시 검수",
     "gpu": False, "setting": "labelstudio_url"},
    {"key": "tensorboard", "name": "TensorBoard", "step": "진행 확인",
     "role": "DGX 학습의 손실·학습률·GPU 그래프(학습은 DGX 학습 스크립트로 실행)",
     "gpu": True, "setting": "tensorboard_url"},
]


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
        return {"ok": False, "detail": f"응답 없음({type(e).__name__})"}


@router.get("/dev/train", response_class=HTMLResponse)
def dev_train(request: Request, err: str = "", ok: str = "", tab: str = "data", ds_page: int = 0):
    # 데이터·학습 화면. 도구·모델 연결은 /dev/tools 로 옮겼다 — 옛 주소(tab=settings)는 안내·오류 문구를
    # 그대로 들고 새 주소로 보낸다. 옛 tab 값(models·exports)이나 없는 값은 data 로 보낸다
    from urllib.parse import urlencode
    if tab == "settings":
        keep = {k: v for k, v in (("err", err), ("ok", ok)) if v}
        return RedirectResponse("/dev/tools" + ("?" + urlencode(keep) if keep else ""), status_code=303)
    if tab != "data":
        keep = {k: v for k, v in (("err", err), ("ok", ok), ("ds_page", ds_page or "")) if v}
        return RedirectResponse("/dev/train?" + urlencode({"tab": "data", **keep}), status_code=303)
    return _dev_data_view(request, err, ok, ds_page)


@router.get("/dev/tools", response_class=HTMLResponse)
def dev_tools(request: Request, err: str = "", ok: str = ""):
    """도구·모델 연결 — 학습 도구 주소·계정, LLM 연결·단계별 모델, 계획 모델 서빙, 외부 연결(웹 검색·구독 CLI)."""
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
    tb = _tensorboard_status()
    tools = [
        {**t, "url": db.get_setting(t["setting"], ""),
         "connected": ls["ok"] if t["key"] == "labelstudio" else (tb["ok"] if t["key"] == "tensorboard" else None),
         "detail": tb.get("detail", "") if t["key"] == "tensorboard" else ""}
        for t in _TRAIN_TOOLS
    ]
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
        "subscription_checks": json.loads(db.get_setting("subscription_checks", "{}")),
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
        checks = tm.judge(cur)
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
        return _train_redirect(request, f"Label Studio 비밀번호 재설정 실패 — {msg}", ok=False)
    return _train_redirect(
        request, "Label Studio 비밀번호를 재설정했습니다 — 새 비밀번호로 로그인하세요")


@router.post("/dev/train/url")
def dev_tool_url(request: Request, setting: str = Form(...), url: str = Form(""), back: str = Form("/dev/train?tab=settings")):
    # Label Studio 주소는 구축 스크립트(scripts/68_labelstudio_token.sh)가 넣는다 —
    # 화면에서는 GPU 도구(TensorBoard) 주소만 바꾼다
    valid = {t["setting"] for t in _TRAIN_TOOLS if t["key"] != "labelstudio"}
    if setting not in valid:
        raise HTTPException(400, "알 수 없는 도구입니다")
    url = url.strip()
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(400, "http(s):// 주소여야 합니다")
    request.app.state.db.set_setting(setting, url)
    dest = back if back.startswith("/dev/") else "/dev/train?tab=settings"
    return RedirectResponse(dest, status_code=303)


# 무거운 개발 화면 조회 — 웹 작업자를 몇 분씩 붙잡지 않게 뒤에서 돌리고, 정해진 시간 안에 끝나지 않으면
# 화면은 「계산 중 — 잠시 뒤 다시」를 보인다. 결과는 잠시 재사용하고, 낡으면 옛 값을 보이며 뒤에서 다시 센다.
# 대기열·결과는 앱마다 하나 — create_app 이 make_dev_slow() 로 만들어 app.state.dev_slow 에 둔다.
def make_dev_slow():
    import concurrent.futures as _slow_cf
    _slow_pool = _slow_cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="dev-slow")
    _slow_cache: dict = {}
    _slow_lock = __import__("threading").Lock()

    def _dev_slow(key: str, fn, *, wait_s: float = 4.0, ttl_s: float = 600.0):
        """(값, 상태) — 상태는 "ok"·"pending"(아직 계산 중)·"error". 같은 조회는 동시에 한 번만 돈다."""
        now = time.time()
        with _slow_lock:
            ent = _slow_cache.get(key) or {}
            fut = ent.get("fut")
            fresh = bool(ent.get("at")) and now - ent["at"] < ttl_s
            if fut is None or (fut.done() and not fresh):
                fut = _slow_pool.submit(fn)
                ent = {"fut": fut, "at": now, "last": ent.get("last")}
                _slow_cache[key] = ent
        try:
            val = fut.result(timeout=wait_s)
        except _slow_cf.TimeoutError:
            return ent.get("last"), "pending"
        except Exception:
            logging.getLogger("zzaimy.app.web").exception("개발 화면 조회 실패: %s", key)
            return ent.get("last"), "error"
        ent["last"] = val
        return val, "ok"

    return _dev_slow


def _dev_data_view(request: Request, err: str = "", ok: str = "", ds_page: int = 0):
    from zzaimy.dataset.build import preview_sources, rag_status
    from zzaimy.export.bundle import preview_bundle

    st = request.app.state
    db = st.db
    show_legacy = (request.query_params.get('view') == 'legacy'
                   or bool(request.query_params.get('ls_pushed'))
                   or bool(request.query_params.get('ls_pulled')))

    try:
        export_preview = preview_bundle(db, model_dir=os.environ.get("ZZAIMY_MODEL_DIR"))
    except Exception:
        export_preview = []

    ds_page = max(ds_page, 0)
    DS_PER = 20
    ds_total = db.count_datasets()
    datasets = db.list_datasets(limit=DS_PER, offset=ds_page * DS_PER)
    from zzaimy.dataset.privacy import approved_bytes
    for dataset in datasets:
        try:
            approved_bytes(Path(dataset["path"]))
            dataset["export_ready"] = True
        except (ValueError, OSError):
            dataset["export_ready"] = False
    # 「다시 확인」 — 30초 캐시를 비우고 Label Studio 에 바로 다시 묻는다
    if request.query_params.get("recheck"):
        st.grounded_cache["value"] = None
    grounded_ls = st.grounded_ls_status()
    labelstudio_url = db.get_setting("labelstudio_url")
    try:
        review_report = json.loads(db.get_setting('grounded_review_report', '{}') or '{}')
    except ValueError:
        review_report = {}
    # 문서별 상태(문서마다 조각을 센다)·원천 미리보기(변환기를 실제로 돌린다)는 문서 수만큼 무거워 뒤에서 센다
    rag_rows, rag_state, source_preview, source_state = [], "ok", [], "ok"
    if show_legacy:
        rag_rows, rag_state = st.dev_slow("rag_status", lambda: rag_status(db))
        source_preview, source_state = st.dev_slow("preview_sources", lambda: preview_sources(db))
    return st.templates.TemplateResponse(request, "dev_data.html", st.page_ctx(request, {
        "rag_state": rag_state, "source_state": source_state,
        "grounded_review_report": review_report,
        "development_tab": "data",
        "export_preview": export_preview,
        "tensorboard_url": db.get_setting("tensorboard_url", ""),
        "grounded_ls": grounded_ls,
        "grounded_note": st.grounded_status_note(grounded_ls, labelstudio_url),
        "rag_rows": rag_rows or [],
        "source_preview": source_preview or [],
        "datasets": datasets,
        "ds_page": ds_page, "ds_total": ds_total, "ds_per": DS_PER,
        "ds_has_next": (ds_page + 1) * DS_PER < ds_total,
        "err": err, "ok": ok,
        "ls": st.ls_status() if show_legacy else {"ok": False},
        "labelstudio_url": labelstudio_url,
    }))
