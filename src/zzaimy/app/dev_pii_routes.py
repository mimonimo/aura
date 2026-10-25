"""개인정보 마스킹 감사(/dev/pii*, 절대 규칙 3) — 기록·자가 점검·잔여 검사, 내부 마스킹 설정.

- 개발자(dev)만 — /dev 경로는 main.check_auth 가 막는다.
- DB·데이터 폴더(data_dir)·계정(accounts)·화면 틀·화면 공통 맥락(page_ctx)은 main.create_app 이 app.state 에 올려 둔 것을 읽는다.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from zzaimy.app import privacy_policy

router = APIRouter()


@router.get("/dev/pii", response_class=HTMLResponse)
def dev_pii(request: Request, q: str = "", entity: str = "", page: int = 1):
    """개인정보 마스킹 감사 — 기록·자가 점검·잔여 검사 (절대 규칙 3). 원천은 플랫폼 DB 하나.
    자가 점검 결과는 DB와 무관하므로 플랫폼 DB settings에만 둔다. 외부 연결(웹 검색)은 도구·모델 연결(/dev/tools).
    """
    from zzaimy.app import pii_audit

    st = request.app.state
    db = st.db
    sources = [pii_audit.source_view(db, name="플랫폼 DB", linkable=True, q=q, entity=entity, page=page)]
    return st.templates.TemplateResponse(request, "dev_pii.html", st.page_ctx(request, {
        "internal_policy": privacy_policy.load(db),
        "masking_entities": privacy_policy.ENTITIES,
        "history_q": q, "history_entity": entity,
        "sources": sources,
        "selftest": pii_audit.load_json(db, pii_audit.SELFTEST_KEY),
        "type_names": {t: name for t, name, *_ in pii_audit.MASK_POLICY},
        "entity_labels": pii_audit.ENTITY_LABELS,
    }))

@router.post("/dev/pii/policy")
def dev_privacy_policy(request: Request, enabled: str = Form(...), entities: list[str] = Form([])):
    if enabled not in {"on", "off"}:
        raise HTTPException(400, "켜짐 또는 꺼짐을 선택하세요.")
    try:
        privacy_policy.save(request.app.state.db, enabled == "on", entities, request.state.user)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return RedirectResponse("/dev/pii?view=policy&saved=1", status_code=303)

@router.post("/dev/pii/selftest")
def dev_pii_selftest(request: Request):
    """합성 표본으로 실제 마스커를 검증 — 결과는 settings에 남는다."""
    from zzaimy.app import pii_audit

    pii_audit.run_selftest(request.app.state.db)
    return RedirectResponse("/dev/pii?view=checks", status_code=303)

@router.post("/dev/pii/scan")
def dev_pii_scan(request: Request):
    """저장·색인된 본문에 탐지 정규식을 독립 실행 — 잔여 개인정보 검사."""
    from zzaimy.app import pii_audit

    db = request.app.state.db
    pii_audit.run_scan(db)
    return RedirectResponse("/dev/pii?view=checks", status_code=303)
