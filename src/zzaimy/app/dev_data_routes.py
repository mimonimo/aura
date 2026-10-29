"""LLM 연결 관리(/dev/llm/*)와 근거 문답 검수 반영(/dev/train/grounded-pull) — 연결 등록·확인·기본 지정, Label Studio 검수 결과 반영.

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
from fastapi.responses import RedirectResponse

router = APIRouter()


def _live_models_cache() -> dict:
    from zzaimy.app import main as _main   # 모델 목록 캐시는 main 에 있다(/dev/tools 가 읽는 것과 같은 것)
    return _main._live_models_cache


# ---- LLM 연결 관리 — 내부 vLLM·외부 API 등록, 확인, 기본 지정 (키는 서버 파일에만) ----

def _llm_redirect(msg: str, ok: bool = True) -> RedirectResponse:
    from urllib.parse import urlencode
    from urllib.parse import quote
    return RedirectResponse("/dev/tools?" + urlencode({"ok" if ok else "err": msg}, quote_via=quote), status_code=303)

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
    return _llm_redirect("기본 연결을 해제했습니다. 환경변수 설정을 씁니다")

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
        return _llm_redirect(f"「{conn['name']}」 연결 확인, 모델 {len(r['models'])}개 ({shown})")
    llm_connections.record_check(cid, False, r["error"])
    tail = f" {r['hint']}" if r.get("hint") else ""
    return _llm_redirect(f"「{conn['name']}」 연결 실패: {r['error']}.{tail}", ok=False)

# ---- Label Studio 자동 연동 (API) — 페이지 버튼 한 번으로 왕복 ----
# 주소·토큰은 서버 구축 때 scripts/68_labelstudio_token.sh 가 설정에 넣는다.
# 사람이 토큰을 붙여넣거나 파일을 주고받는 경로는 두지 않는다.
_LS_PROJECT = "ZZAIMY 검수"
_LS_STATUS_TTL = 30.0   # 연결 상태 캐시(초) — 화면마다 Label Studio 를 두드리지 않게


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

    _projects_cache: dict = {"at": 0.0, "value": None}

    def _ls_projects() -> dict:
        """검수 프로젝트 전부와 진행(완료/전체) — 30초 캐시. 데이터·학습 「문답 검수」 카드가 읽고, 화면이 1분마다 다시 묻는다."""
        from zzaimy.dataset.ls_client import LabelStudioError
        now = _time.monotonic()
        if _projects_cache["value"] is not None and now - _projects_cache["at"] < _LS_STATUS_TTL:
            return _projects_cache["value"]
        try:
            value = _ls_client().projects_progress()
        except LabelStudioError:
            value = {"ok": False, "error": "연결 설정 없음", "projects": []}
        _projects_cache.update(at=now, value=value)
        return value

    return SimpleNamespace(ls_client=_ls_client, ls_status=_ls_status, ls_projects=_ls_projects, ls_projects_cache=_projects_cache)


def _grounded_status_note(st: dict, ls_url: str | None) -> dict | None:
    """Label Studio 에 닿지 않을 때 무엇이 안 됐는지(짧은 상태 표시)와 어디서 고치는지. 닿으면 None — 카드는 프로젝트 목록을 보인다.
    chips: {text, level, title} — level 은 화면 pill 색(reviewed=정상·processing=확인 필요)."""
    if st.get("ok"):
        return None
    return {"chips": [{"text": f"Label Studio 미연결({st.get('error') or '응답 오류'})", "level": "processing", "title": ""}],
            "href": "/dev/tools", "action": "도구·모델 연결", "external": False}


# ---- 근거 문답 검수(Label Studio) 반영 — 데이터·학습 화면의 「문답 검수」 카드 ----

@router.post("/dev/train/grounded-pull")
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
        st.ls_projects_cache['value'] = None          # 반영 뒤 검수 진행 표를 바로 다시 묻게
        params = {'tab': 'data', 'ok': f"검수 확인: 승인 {report['approved']}건 · 보류 {report['held']}건. 같은 승인본은 중복 등록하지 않습니다."}
    except (LabelStudioError, ValueError, TypeError, KeyError):
        params = {'tab': 'data', 'err': '검수 결과를 가져오지 못했습니다. 연결·원문·검수 형식을 확인하세요. 기존 승인본은 보존됩니다.'}
    return RedirectResponse('/dev/train?' + urlencode(params), status_code=303)
