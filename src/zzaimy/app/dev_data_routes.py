"""LLM 연결 관리(/dev/llm/*)와 데이터 공방(/dev/data*) — 연결 등록·확인·기본 지정, 기록 → 학습 데이터(JSONL), Label Studio 왕복.

- 개발자(dev)만 — /dev 경로는 main.check_auth 가 막는다.
- DB·데이터 폴더(data_dir)와 Label Studio 연결 도우미(make_ls_helpers 가 앱마다 만든다)는 main.create_app 이
  app.state 에 올려 둔 것을 읽는다. 연결 상태·검수 현황·LLM 연결 안내 이동은 데이터·학습 화면(app/dev_train_routes)도 함께 쓴다.
"""
from __future__ import annotations

import hashlib
import json as _aj
import time as _time
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()


def _live_models_cache() -> dict:
    from zzaimy.app import main as _main   # 모델 목록 캐시는 main 에 있다(/dev/tools 가 읽는 것과 같은 것)
    return _main._live_models_cache


# ---- LLM 연결 관리 — 내부 vLLM·외부 API 등록, 확인, 기본 지정 (키는 서버 파일에만) ----

def _llm_redirect(msg: str, ok: bool = True) -> RedirectResponse:
    from urllib.parse import urlencode
    from urllib.parse import quote
    return RedirectResponse("/dev/train?" + urlencode({"tab": "settings", "ok" if ok else "err": msg}, quote_via=quote), status_code=303)

@router.post("/dev/llm/add")
def dev_llm_add(name: str = Form(""), kind: str = Form("vllm"), base_url: str = Form(""),
                model: str = Form(""), api_key: str = Form(""), vision_model: str = Form("")):
    from zzaimy.generate import llm_connections, model_config

    try:
        conn = llm_connections.add(name, kind, base_url, model, api_key, vision_model=vision_model)
    except ValueError as e:
        return _llm_redirect(str(e), ok=False)
    _live_models_cache().clear()
    model_config.reset_status_cache()
    return _llm_redirect(f"연결 「{conn['name']}」을 추가했습니다")

@router.post("/dev/llm/{cid}/update")
def dev_llm_update(cid: str, name: str = Form(""), base_url: str = Form(""),
                   model: str = Form(""), api_key: str = Form(""), vision_model: str = Form("")):
    from zzaimy.generate import llm_connections, model_config

    try:
        conn = llm_connections.update(cid, name=name, base_url=base_url, model=model, api_key=api_key,
                                      vision_model=vision_model)
    except ValueError as e:
        return _llm_redirect(str(e), ok=False)
    _live_models_cache().clear()
    model_config.reset_status_cache()
    return _llm_redirect(f"연결 「{conn['name']}」을 저장했습니다")

@router.post("/dev/llm/{cid}/delete")
def dev_llm_delete(cid: str):
    from zzaimy.generate import llm_connections, model_config

    llm_connections.delete(cid)
    _live_models_cache().clear()
    model_config.reset_status_cache()
    return _llm_redirect("연결을 삭제했습니다")

@router.post("/dev/llm/{cid}/activate")
def dev_llm_activate(cid: str, ack: str = Form("")):
    from zzaimy.generate import llm_connections, model_config

    try:
        conn = llm_connections.activate(cid, ack_external=(ack == "1"))
    except ValueError as e:
        return _llm_redirect(str(e), ok=False)
    model_config.reset_status_cache()
    return _llm_redirect(f"「{conn['name']}」을 문서 작업 기본 연결로 지정했습니다")

@router.post("/dev/llm/deactivate")
def dev_llm_deactivate():
    from zzaimy.generate import llm_connections, model_config

    llm_connections.deactivate()
    model_config.reset_status_cache()
    return _llm_redirect("기본 연결을 해제했습니다 — 환경변수 설정을 씁니다")

@router.post("/dev/llm/role")
def dev_llm_role(role: str = Form(...), cid: str = Form(""), model: str = Form("")):
    """용도마다 쓸 서버와 모델을 정한다 — 문서 작업·이미지 판독·임베딩·학습."""
    from zzaimy.generate import llm_connections, model_config

    try:
        llm_connections.set_role(role, cid.strip(), model.strip())
    except ValueError as e:
        return _llm_redirect(str(e), ok=False)
    model_config.reset_status_cache()
    _live_models_cache().clear()
    label = llm_connections.ROLES.get(role, role)
    if not cid.strip():
        return _llm_redirect(f"「{label}」은(는) 문서 작업 기본 서버를 씁니다")
    conn = llm_connections.get(cid.strip()) or {}
    picked = f" · 모델 {model.strip()}" if model.strip() else ""
    tail = (" 임베딩을 바꾸면 벡터 공간이 달라져 전체 재색인이 필요합니다."
            if role == "embed" else "")
    return _llm_redirect(f"「{label}」은(는) 「{conn.get('name', '')}」{picked} 로 정했습니다.{tail}")

@router.post("/dev/llm/roles")
async def dev_llm_roles(request: Request):
    """단계별 모델을 한 번에 저장한다 — 화면의 '변경사항 저장' 하나에 대응한다.

    단건 저장(/dev/llm/role)을 단계마다 부르면 중간 실패 때 절반만 바뀐 상태가 남고,
    화면에도 줄마다 저장 버튼이 붙어 손이 많이 간다. 여기서는 전부 검증한 뒤 한 번에 쓴다.
    """
    from zzaimy.generate import llm_connections, model_config

    form = await request.form()
    roles = form.getlist("role")
    # 화면에서 고르는 것은 '어느 서버의 어느 모델' 하나다 — "연결id|모델" 로 온다.
    # 빈 값이면 그 단계는 기본 서버를 따른다.
    raw = form.getlist("pick")
    if not roles or len(roles) != len(raw):
        return _llm_redirect("보낸 값이 맞지 않습니다", ok=False)
    picks = {}
    for role, value in zip(roles, raw):
        cid, _, model = str(value).partition("|")
        picks[role] = (cid.strip(), model.strip())
    before = {r["role"]: (r["id"], r["model"]) for r in llm_connections.roles_public()}
    try:
        llm_connections.set_roles(picks)
    except ValueError as e:
        return _llm_redirect(str(e), ok=False)
    model_config.reset_status_cache()
    _live_models_cache().clear()
    changed = [llm_connections.ROLES.get(r, r) for r, (c, m) in picks.items()
               if before.get(r, ("", "")) != (c, m)]
    if not changed:
        return _llm_redirect("바뀐 것이 없습니다")
    tail = (" 임베딩을 바꾸면 벡터 공간이 달라져 전체 재색인이 필요합니다."
            if "embed" in picks else "")
    return _llm_redirect(f"{', '.join(changed)} 을(를) 저장했습니다.{tail}")


@router.post("/dev/llm/models-live")
def dev_llm_models_live(kind: str = Form("vllm"), base_url: str = Form(""),
                        api_key: str = Form(""), cid: str = Form("")):
    """저장 전에 서버가 지금 내어 주는 모델 목록만 받아 본다 — 연결 추가·수정 창이 쓴다."""
    from zzaimy.generate import llm_connections

    conn = {"kind": kind, "base_url": base_url.strip(), "api_key": api_key}
    if cid:                                   # 수정 창에서 키를 다시 넣지 않았을 때
        saved = llm_connections.get(cid)
        if saved is None:
            return {"ok": False, "models": [], "error": "없는 연결입니다"}
        conn = {"kind": saved.get("kind", kind),
                "base_url": (base_url.strip() or saved.get("base_url", "")),
                "api_key": api_key or saved.get("api_key", "")}
    if not conn["base_url"]:
        return {"ok": False, "models": [], "error": "주소를 먼저 넣으세요"}
    return llm_connections.live_models(conn)


@router.post("/dev/llm/{cid}/test")
def dev_llm_test(cid: str):
    from zzaimy.generate import llm_connections

    conn = llm_connections.get(cid)
    if conn is None:
        return _llm_redirect("없는 연결입니다", ok=False)
    r = llm_connections.probe(conn)
    if r["ok"]:
        shown = ", ".join(r["models"][:6]) + (" …" if len(r["models"]) > 6 else "")
        llm_connections.record_check(cid, True, f"모델 {len(r['models'])}개")
        return _llm_redirect(f"「{conn['name']}」 연결 확인 — 모델 {len(r['models'])}개 ({shown})")
    llm_connections.record_check(cid, False, r["error"])
    tail = f" {r['hint']}" if r.get("hint") else ""
    return _llm_redirect(f"「{conn['name']}」 연결 실패 — {r['error']}.{tail}", ok=False)

# ---- Label Studio 자동 연동 (API) — 페이지 버튼 한 번으로 왕복 ----
# 주소·토큰은 서버 구축 때 scripts/68_labelstudio_token.sh 가 설정에 넣는다.
# 사람이 토큰을 붙여넣거나 파일을 주고받는 경로는 두지 않는다.
_LS_PROJECT = "ZZAIMY 검수"
_LS_STATUS_TTL = 30.0   # 연결 상태 캐시(초) — 화면마다 Label Studio 를 두드리지 않게
_GROUNDED_PROJECT = "ZZAIMY 근거 기반 문답 검수"


def make_ls_helpers(db) -> SimpleNamespace:
    """Label Studio 연결·상태 도우미 — 앱마다 하나(캐시도 앱마다). create_app 이 만들어 app.state 에 올린다."""
    _ls_status_cache: dict = {"at": 0.0, "value": None}

    def _ls_client():
        from zzaimy.dataset.ls_client import LabelStudioClient

        url = db.get_setting("labelstudio_url")
        token = db.get_setting("labelstudio_token")
        return LabelStudioClient(url, token)

    def _ls_status() -> dict:
        """연결 상태 — 2초 제한으로 한 번 묻고 30초 캐시. 설정이 없으면 묻지 않는다."""
        from zzaimy.dataset.ls_client import LabelStudioError

        now = _time.monotonic()
        cached = _ls_status_cache["value"]
        if cached is not None and now - _ls_status_cache["at"] < _LS_STATUS_TTL:
            return cached
        try:
            value = _ls_client().status(_LS_PROJECT)
        except LabelStudioError:   # kind=config — 주소·토큰이 아직 없다 (캐시하지 않음)
            return {"ok": False, "error": "설정 없음", "project": _LS_PROJECT,
                    "project_id": None, "total": 0, "done": 0, "pending": 0}
        _ls_status_cache.update(at=now, value=value)
        return value

    def _ls_status_reset() -> None:
        _ls_status_cache["value"] = None

    _grounded_cache: dict = {"at": 0.0, "value": None}

    def _grounded_ls_status():
        from zzaimy.dataset.ls_client import LabelStudioError
        now = _time.monotonic()
        if _grounded_cache["value"] is not None and now - _grounded_cache["at"] < 30:
            return _grounded_cache["value"]
        try:
            value = _ls_client().status(_GROUNDED_PROJECT)
        except LabelStudioError:
            value = {"ok": False, "error": "연결 설정 없음", "total": 0, "done": 0, "pending": 0, "project_id": None}
        _grounded_cache.update(at=now, value=value)
        return value

    return SimpleNamespace(ls_client=_ls_client, ls_status=_ls_status, ls_status_reset=_ls_status_reset,
                           grounded_cache=_grounded_cache, grounded_ls_status=_grounded_ls_status)


def _grounded_status_note(st: dict, ls_url: str | None) -> dict | None:
    """검수 현황을 못 보일 때 무엇이 안 됐는지(한 줄)와 어디서 확인하는지.
    연결은 됐는데 프로젝트가 없는 경우와 연결 자체가 안 된 경우를 나눈다."""
    if st.get("ok") and st.get("project_id"):
        return None
    if st.get("ok"):
        return {"what": f"Label Studio 연결됨 · 「{_GROUNDED_PROJECT}」 프로젝트 없음 — 사람 검수를 시작할 때 만든다. "
                        "지금 SFT 학습셋은 모델 전수 점검·경고 직접 판정으로 승인했다(아래 학습 데이터)",
                "href": f"{ls_url.rstrip('/')}/projects" if ls_url else "/dev/train?tab=settings",
                "action": "Label Studio 프로젝트 목록" if ls_url else "도구·모델 연결",
                "external": bool(ls_url)}
    return {"what": f"Label Studio 미연결({st.get('error') or '응답 오류'})",
            "href": "/dev/train?tab=settings", "action": "도구·모델 연결", "external": False}


# ---- 데이터 공방 — 기록 → 학습 데이터(JSONL) 변환 (개발자 전용) ----

@router.get("/dev/data", response_class=HTMLResponse)
def dev_data(request: Request, err: str = "", ok: str = "", ds_page: int = 0):
    from urllib.parse import urlencode
    params = dict(request.query_params)
    params["tab"] = "data"
    return RedirectResponse("/dev/train?" + urlencode(params), status_code=303)

@router.post("/dev/data/build-and-push")
def dev_data_build_and_push(request: Request, sources: list[str] = Form([])):
    """예시 만들기 + Label Studio 자동 주입을 한 번에 — 연결돼 있으면 바로 푸시,
    아니면 데이터셋만 만들고 연결 방법(구축 스크립트)을 안내한다."""
    st = request.app.state
    db = st.db
    from datetime import datetime as _dt

    from zzaimy.dataset.build import _BUILDERS, export_dataset
    from zzaimy.dataset.ls_client import LabelStudioError

    srcs = [s for s in sources if s in _BUILDERS]
    if not srcs:
        return RedirectResponse("/dev/data?err=기록 종류를 하나 이상 고르세요", status_code=303)
    # 1) 예시 묶음 생성(수치검증 통과분만) — 대장에 기록
    try:
        export_dataset(db, srcs, _dt.now().strftime("예시-%Y%m%d-%H%M"))
    except ValueError as exc:
        return RedirectResponse(f"/dev/data?err={exc}", status_code=303)
    # 2) Label Studio 연결이 있으면 자동 주입 (연결은 scripts/68_labelstudio_token.sh 가 맺는다)
    if not (db.get_setting("labelstudio_url") and db.get_setting("labelstudio_token")):
        return RedirectResponse(
            "/dev/data?ok=예시 묶음을 만들었습니다. Label Studio가 연결되지 않아 보내지는"
            " 않았습니다 — 서버에서 scripts/68_labelstudio_token.sh 를 실행하면"
            " 다음부터 자동으로 보냅니다.",
            status_code=303)
    pairs = []
    for s in srcs:
        pairs.extend(_BUILDERS[s](db).pairs)
    if not pairs:
        return RedirectResponse("/dev/data?err=보낼 예시가 없습니다", status_code=303)
    try:
        cli = st.ls_client()
        pid = cli.ensure_project(_LS_PROJECT)
        n = cli.push_tasks(pid, pairs)
    except LabelStudioError as e:
        return RedirectResponse(f"/dev/data?err=Label Studio: {e}", status_code=303)
    st.ls_status_reset()   # 진행 수치가 바뀌었다 — 캐시를 비워 바로 반영
    return RedirectResponse(f"/dev/data?ls_pushed={n}", status_code=303)

@router.post("/dev/data/build")
def dev_data_build(request: Request, sources: list[str] = Form([])):
    db = request.app.state.db
    from datetime import datetime as _dt

    from zzaimy.dataset.build import export_dataset

    # 이름은 자동 부여(사용자가 'sft' 같은 걸 타이핑하지 않게) — 필요 시 대장에서 구분
    nm = _dt.now().strftime("예시-%Y%m%d-%H%M")
    try:
        export_dataset(db, sources, nm)
    except ValueError as exc:
        return RedirectResponse(f"/dev/data?err={exc}", status_code=303)
    return RedirectResponse(f"/dev/data?ok=예시 묶음 「{nm}」을 만들었습니다", status_code=303)

@router.post("/dev/data/ls-pull")
def dev_data_ls_pull(request: Request, name: str = Form("검수완료")):
    """Label Studio 검수 결과를 API로 가져와 학습 데이터로 확정."""
    st = request.app.state
    db = st.db
    from pathlib import Path as _P

    from zzaimy.dataset.build import SFT_DIR
    from zzaimy.dataset.privacy import protect_candidate
    from zzaimy.dataset.ls_client import LabelStudioError

    try:
        cli = st.ls_client()
        pid = cli.ensure_project(_LS_PROJECT)
        pairs = cli.pull_reviewed(pid)
        protected = protect_candidate(pairs)
        if protected != pairs:
            for pair in protected:
                pair.setdefault("meta", {}).pop("review", None)
        pairs = protected
    except LabelStudioError as e:
        return RedirectResponse(f"/dev/data?err=Label Studio: {e}", status_code=303)
    if not pairs:
        return RedirectResponse("/dev/data?err=검수 통과분이 없습니다", status_code=303)
    from datetime import datetime, timezone

    SFT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    safe = "".join(c for c in name if c.isalnum() or c in "-_") or "reviewed"
    path = _P(SFT_DIR) / f"{safe}-{stamp}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for p in pairs:
            f.write(_aj.dumps(p, ensure_ascii=False) + "\n")
    db.add_dataset(name=name, sources="labelstudio", path=str(path),
                   n_pairs=len(pairs))
    return RedirectResponse(f"/dev/data?ls_pulled={len(pairs)}", status_code=303)

@router.post("/dev/data/grounded-review-config")
def dev_grounded_review_config(request: Request):
    st = request.app.state
    from zzaimy.dataset.authored_review import PROJECT, upgrade_config
    from zzaimy.dataset.ls_client import LabelStudioError
    from datetime import datetime, timezone
    from urllib.parse import urlencode
    from xml.etree.ElementTree import ParseError
    try:
        cli = st.ls_client()
        status = cli.status(PROJECT)
        if not status.get('ok') or not status.get('project_id'):
            raise ValueError('검수 프로젝트 연결을 확인하세요.')
        pid = status['project_id']
        project = cli._req('GET', f'/api/projects/{pid}')
        old = project.get('label_config', '')
        new = upgrade_config(old)
        if new != old:
            backup = Path(st.data_dir) / 'labelstudio-backup'
            backup.mkdir(parents=True, exist_ok=True, mode=0o700)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
            target = backup / f'project-{pid}-config-{stamp}.json'
            with target.open('x', encoding='utf-8') as out:
                _aj.dump({'project_id': pid, 'label_config': old}, out, ensure_ascii=False)
            target.chmod(0o600)
            cli._req('PATCH', f'/api/projects/{pid}', json={'label_config': new})
        params = {'tab': 'data', 'ok': '검수 4항목을 준비했습니다. 기존 주석은 보존하며 자동 승인하지 않습니다.'}
    except (LabelStudioError, ValueError, ParseError):
        params = {'tab': 'data', 'err': '검수 설정을 변경하지 못했습니다. 연결 상태와 기존 라벨 설정을 확인하세요.'}
    return RedirectResponse('/dev/train?' + urlencode(params), status_code=303)

@router.post("/dev/data/grounded-pull")
def dev_grounded_pull(request: Request):
    st = request.app.state
    db = st.db
    from zzaimy.dataset.authored_review import PROJECT, convert_tasks
    from zzaimy.dataset.build import SFT_DIR
    from zzaimy.dataset.ls_client import LabelStudioError
    from zzaimy.dataset.privacy import approved_bytes
    from urllib.parse import urlencode
    source_cache = {}

    def resolve(doc_id, chunk_id):
        if doc_id not in source_cache:
            source_cache[doc_id] = {c['id']: c for c in db.list_doc_chunks(doc_id)} if db.get_document(doc_id) else {}
        chunk = source_cache[doc_id].get(chunk_id)
        if not chunk:
            return None
        content = chunk['content']
        if chunk['kind'] == 'table':
            content = _aj.loads(content).get('text', content)
        return content

    try:
        cli = st.ls_client()
        status = cli.status(PROJECT)
        if not status.get('ok') or not status.get('project_id'):
            raise ValueError('검수 프로젝트 미연결')
        pairs, report = convert_tasks(cli.export_tasks(status['project_id']), resolve)
        from datetime import datetime, timezone
        report['checked_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        SFT_DIR.mkdir(parents=True, exist_ok=True)
        active_filename = ''
        if pairs:
            raw = ''.join(_aj.dumps(p, ensure_ascii=False, sort_keys=True) + '\n' for p in pairs).encode('utf-8')
            digest = hashlib.sha256(raw).hexdigest()
            SFT_DIR.mkdir(parents=True, exist_ok=True)
            path = SFT_DIR / f'grounded-reviewed-{digest}.jsonl'
            try:
                with path.open('xb') as out:
                    out.write(raw)
                path.chmod(0o600)
            except FileExistsError:
                if path.read_bytes() != raw:
                    raise ValueError('기존 승인 파일 불일치')
            approved_bytes(path, check_current=False)
            active_filename = path.name
            # 내용 해시로 이름을 지으므로 같은 승인본은 두 번 등록하지 않는다.
            if not db.get_setting('grounded_import:' + digest):
                dsid = db.add_dataset(name='근거 문답 검수 승인', sources='labelstudio', path=str(path), n_pairs=len(pairs))
                db.set_setting('grounded_import:' + digest, str(dsid))
        # 검수가 바뀌거나 철회되면(승인 0건 포함) 이번 결과가 이전 승인본을 대신한다.
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', dir=SFT_DIR, suffix='.tmp', delete=False) as active:
            _aj.dump({'filename': active_filename}, active)
            active_path = Path(active.name)
        active_path.replace(SFT_DIR / 'grounded-current.json')
        db.set_setting('grounded_review_report', _aj.dumps(report, ensure_ascii=False))
        st.grounded_cache['value'] = None
        params = {'tab': 'data', 'ok': f"검수 확인: 승인 {report['approved']}건 · 보류 {report['held']}건. 같은 승인본은 중복 등록하지 않습니다."}
    except (LabelStudioError, ValueError, TypeError, KeyError):
        params = {'tab': 'data', 'err': '검수 결과를 가져오지 못했습니다. 연결·원문·검수 형식을 확인하세요. 기존 승인본은 보존됩니다.'}
    return RedirectResponse('/dev/train?' + urlencode(params), status_code=303)

@router.get("/dev/data/{dataset_id}.jsonl")
def dev_data_download(request: Request, dataset_id: int):
    from fastapi.responses import Response
    from zzaimy.dataset.privacy import approved_bytes

    ds = request.app.state.db.get_dataset(dataset_id)
    if ds is None or not Path(ds["path"]).exists():
        raise HTTPException(404, "데이터셋 파일이 없습니다")
    try:
        payload = approved_bytes(Path(ds["path"]))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(payload, media_type="application/jsonl",
                    headers={"Content-Disposition": f'attachment; filename="dataset-{dataset_id}.jsonl"'})
