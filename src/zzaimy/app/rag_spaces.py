"""RAG 공간 — 누가 어떤 자료로 답을 받는가(ADR-0053). 검색 범위는 이 공간을 통해서만 정한다.

열람 권한(access_policy: 누가 이 문서를 볼 수 있나)과 공간(어느 RAG 에 들어가나)은 다르다. 공간은 검색 후보를 고르는 규칙이다.
- student : 학생 계정 — 학생 공개로 지정한 학사 규정만(ADR-0052). 사업 문서·그래프·외부 검색 없음
- dept:<부서> : 그 부서 교직원 — 그 부서 사업 문서 + 공통·그 부서 규정
- staff : 부서가 정해지지 않은 교직원 — 사업 문서 전부 + 규정(열람 등급 규칙)
- dev : 관리자 — 전부
설정은 data/platform/rag_spaces.json(없으면 기본값). 원본 최상위 폴더 → 부서 짝(dept_of_area)도 여기 — 사업 문서의 부서는
반입 때 원본 경로의 최상위 폴더로 붙는다. 새 부서 RAG 는 설정에 공간을 더하면 생긴다(코드를 고치지 않는다).
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT = {
    "dept_of_area": {"링크": "LINC사업단", "앵커": "앵커사업단", "산단": "산학협력단"},
    "spaces": [
        {"id": "student", "label": "학생 — 학사 규정", "roles": ["student"],
         "regulation": {"audience": "student"}, "grant": False},
        {"id": "staff", "label": "교직원 전체", "roles": ["staff", "head"], "depts": [""],
         "regulation": {}, "grant": True},
    ],
    # 부서가 정해진 교직원은 공간을 따로 적지 않아도 dept:<부서> 공간을 받는다 — 그 부서 사업 문서 + 공통·그 부서 규정
    "dept_default": {"regulation": {}, "grant": True},
    # 계정별 추가 권한 — {"계정": ["dept:LINC사업단", …]}. 그 공간의 사업 문서 부서가 검색 범위에 더해진다
    "grants": {},
}


def config(data_dir: str | Path | None = None) -> dict:
    if data_dir is not None:
        p = Path(data_dir) / "rag_spaces.json"
        try:
            got = json.loads(p.read_text(encoding="utf-8"))
            return {**DEFAULT, **got}
        except (OSError, ValueError):
            pass
    return DEFAULT


def dept_of_rel(rel: str, data_dir: str | Path | None = None) -> str:
    """원본 경로(최상위 폴더) → 부서. 짝이 없으면 '공통'."""
    area = (rel or "").split("/", 1)[0]
    return config(data_dir)["dept_of_area"].get(area, "공통")


def resolve(role: str, dept: str | None, data_dir: str | Path | None = None) -> dict:
    """역할·부서 → 공간 {id, label, regulation{audience?}, grant(bool), grant_depts(None=전부 | [부서…])}."""
    cfg = config(data_dir)
    dept = (dept or "").strip()
    if role == "dev":
        return {"id": "dev", "label": "관리자 — 전부", "regulation": {}, "grant": True, "grant_depts": None}
    for sp in cfg["spaces"]:
        if role in sp.get("roles", []) and ("depts" not in sp or dept in sp["depts"]):
            return {**sp, "grant_depts": sp.get("grant_depts")}
    if dept and role in ("staff", "head"):
        d = cfg.get("dept_default", {})
        return {"id": f"dept:{dept}", "label": f"{dept} — 부서 사업 문서·규정", "regulation": d.get("regulation", {}),
                "grant": d.get("grant", True), "grant_depts": [dept, "공통"]}
    # 모르는 역할은 가장 좁게 — 공개 규정만
    return {"id": "public", "label": "공개 규정", "regulation": {}, "grant": False, "grant_depts": []}


def search_scope(role: str, dept: str | None, user: str | None = None, data_dir: str | Path | None = None) -> dict:
    """검색에 넘길 범위 — 공간에서 만든다. 규정은 levels·dept·user(열람 등급 규칙), 사업 문서는 grant(쓰나)·grant_depts(부서).
    계정별 추가 권한(grants)이 있으면 그 공간들의 사업 문서 부서를 더한다(학생에게는 더하지 않는다)."""
    sp = resolve(role, dept, data_dir)
    extra = [] if role == "student" else config(data_dir).get("grants", {}).get(user or "", [])
    if extra and sp.get("grant_depts") is not None:
        depts = list(sp["grant_depts"])
        for sid in extra:
            if sid.startswith("dept:") and sid[5:] not in depts:
                depts.append(sid[5:])
        sp = {**sp, "grant": True, "grant_depts": depts, "id": sp["id"] + "+" + ",".join(extra)}
    scope: dict = {"space": sp["id"], "role": role}
    if sp["regulation"].get("audience") == "student":
        scope["levels"] = ("student",)
    elif role != "dev":
        scope.update({"user": user or "", "dept": dept or "공통"})
    scope["grant"] = bool(sp.get("grant"))
    scope["grant_depts"] = sp.get("grant_depts")
    return scope


def save_config(cfg: dict, data_dir: str | Path) -> None:
    """설정을 저장한다 — 이전 판은 rag_spaces.json.bak-<시각> 으로 남긴다."""
    import shutil
    import time
    p = Path(data_dir) / "rag_spaces.json"
    if p.is_file():
        shutil.copy(p, p.with_name(f"rag_spaces.json.bak-{time.strftime('%Y%m%d%H%M%S')}"))
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def backfill_depts(db, data_dir: str | Path) -> dict:
    """DGX 원본 문서(stored_path dgx://)의 부서를 원본 최상위 폴더로 다시 붙인다. 열람은 교직원 전체(공개 등급)로 두고 부서는
    RAG 공간의 범위로만 쓴다 — 부서 없는 교직원 계정에서 자료가 사라지지 않게. {부서: 건수}."""
    cfg = config(data_dir)
    out: dict[str, int] = {}
    with db._conn() as conn:
        for area, dept in cfg["dept_of_area"].items():
            cur = conn.execute("UPDATE documents SET dept = ?, access_level = 'public' WHERE stored_path LIKE ? AND doc_type = 'grant'",
                               (dept, f"dgx://{area}/%"))
            out[dept] = out.get(dept, 0) + int(cur.rowcount or 0)
    return out


def stats(db, data_dir: str | Path, accounts: dict) -> list[dict]:
    """공간마다 실제로 들어 있는 것 — 규정 조각 수·사업 문서 수, 쓰는 계정."""
    cfg = config(data_dir)
    depts = sorted({v for v in cfg["dept_of_area"].values()} | {a.get("dept") for a in accounts.values() if a.get("dept")})
    rows = []
    cases = [("student", {"role": "student", "dept": ""}), ("staff", {"role": "staff", "dept": ""})] + \
            [(f"dept:{d}", {"role": "staff", "dept": d}) for d in depts]
    for sid, who in cases:
        sc = search_scope(who["role"], who["dept"], None, data_dir)
        reg = db.list_regulation_chunks(**{k: sc[k] for k in ("levels", "dept") if k in sc})
        n_grant = 0
        if sc.get("grant"):
            with db._conn() as conn:
                if sc.get("grant_depts") is None:
                    n_grant = conn.execute("SELECT COUNT(*) FROM documents WHERE doc_type = 'grant' AND status = 'reviewed'").fetchone()[0]
                elif sc["grant_depts"]:
                    ph = ",".join("?" * len(sc["grant_depts"]))
                    n_grant = conn.execute(f"SELECT COUNT(*) FROM documents WHERE doc_type = 'grant' AND status = 'reviewed'"
                                           f" AND COALESCE(dept, '공통') IN ({ph})", sc["grant_depts"]).fetchone()[0]
        users = [u for u, a in accounts.items() if resolve(a.get("role", "staff"), a.get("dept"), data_dir)["id"] == sid]
        rows.append({"id": sid, "label": resolve(who["role"], who["dept"], data_dir)["label"], "reg_chunks": len(reg),
                     "reg_docs": len({r["doc_id"] for r in reg}), "grant": bool(sc.get("grant")),
                     "grant_depts": sc.get("grant_depts"), "grant_docs": int(n_grant), "users": users})
    return rows
