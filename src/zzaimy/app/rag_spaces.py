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
import re
from pathlib import Path

DEFAULT = {
    # 원본 폴더 → 부서 RAG. 비어 있으면 모든 반입 문서가 공통 RAG(2026-10-13 결정) — 폴더를 부서 RAG 에 짝지을 때만 적는다
    "dept_of_area": {},
    # 만든 부서 RAG — [{"dept": 이름, "at": 날짜, "by": 계정}]. 계정에 부서가 없어도 문서를 먼저 넣어 둘 수 있다
    "dept_rags": [],
    "spaces": [
        {"id": "student", "label": "학생 — 학사 규정", "roles": ["student"],
         "regulation": {"audience": "student"}, "grant": False},
        {"id": "staff", "label": "교직원 — 공통 RAG", "roles": ["staff", "head"], "depts": [""],
         "regulation": {}, "grant": True, "grant_depts": ["공통"]},
    ],
    # 부서가 정해진 교직원은 공간을 따로 적지 않아도 dept:<부서> 공간을 받는다 — 그 부서 사업 문서 + 공통·그 부서 규정
    "dept_default": {"regulation": {}, "grant": True},
    # 계정별 추가 권한 — {"계정": ["dept:산학협력단", …]}. 그 공간의 사업 문서 부서가 검색 범위에 더해진다
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
        return {"id": f"dept:{dept}", "label": f"{dept} — 공통 RAG + 부서 RAG", "regulation": d.get("regulation", {}),
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
    """DGX 원본 문서(stored_path dgx://)의 부서를 원본 최상위 폴더로 다시 붙인다 — 폴더를 부서 RAG 에 짝지었으면 부서 제한,
    공통이면 전체 공개. {부서: 건수}."""
    cfg = config(data_dir)
    out: dict[str, int] = {}
    with db._conn() as conn:
        for area, dept in cfg["dept_of_area"].items():
            cur = conn.execute("UPDATE documents SET dept = ?, access_level = ? WHERE stored_path LIKE ? AND doc_type = 'grant'",
                               (dept, "public" if dept == "공통" else "dept", f"dgx://{area}/%"))
            out[dept] = out.get(dept, 0) + int(cur.rowcount or 0)
    return out


def stats(db, data_dir: str | Path, accounts: dict) -> list[dict]:
    """공간마다 실제로 들어 있는 것 — 규정 조각 수·사업 문서 수, 쓰는 계정."""
    cfg = config(data_dir)
    depts = known_depts(cfg, accounts)
    rows = []
    cases = [("student", {"role": "student", "dept": ""}), ("staff", {"role": "staff", "dept": ""})] + \
            [(f"dept:{d}", {"role": "staff", "dept": d}) for d in depts]
    for sid, who in cases:
        sc = search_scope(who["role"], who["dept"], None, data_dir)
        reg = db.list_regulation_chunks(**{k: sc[k] for k in ("levels", "dept") if k in sc})
        n_grant = n_grant_reg = 0
        where = _grant_where(sc)
        if where is not None:
            with db._conn() as conn:
                n_grant = conn.execute(f"SELECT COUNT(*) FROM documents WHERE {where[0]}", where[1]).fetchone()[0]
                n_grant_reg = conn.execute(f"SELECT COUNT(*) FROM documents WHERE {where[0]} AND kind = 'regulation'",
                                           where[1]).fetchone()[0]
        users = [u for u, a in accounts.items() if resolve(a.get("role", "staff"), a.get("dept"), data_dir)["id"] == sid]
        rows.append({"id": sid, "label": resolve(who["role"], who["dept"], data_dir)["label"], "reg_chunks": len(reg),
                     "reg_docs": len({r["doc_id"] for r in reg}), "grant": bool(sc.get("grant")),
                     "grant_depts": sc.get("grant_depts"), "grant_docs": int(n_grant), "grant_reg_docs": int(n_grant_reg),
                     "users": users})
    return rows


# ---------------------------------------------------------------- 관리 화면(/dev/rag) 도구 — 권한 규칙은 위 함수 그대로 쓴다

def known_depts(cfg: dict, accounts: dict) -> list[str]:
    """고를 수 있는 부서 — 폴더 짝·계정·공간 설정에 이미 쓰인 부서. 새 부서는 계정에 부서를 정하면 생긴다(코드에 이름을 두지 않는다)."""
    out = set(cfg.get("dept_of_area", {}).values())
    out |= {str(r.get("dept") or "").strip() for r in cfg.get("dept_rags", []) if str(r.get("dept") or "").strip()}
    out |= {str(a.get("dept")).strip() for a in accounts.values() if str(a.get("dept") or "").strip()}
    for sp in cfg.get("spaces", []):
        out |= {d for d in sp.get("depts", []) if d}
    return sorted(out - {"공통"})


def dept_spaces(cfg: dict, accounts: dict) -> list[str]:
    """계정별 추가 권한으로 줄 수 있는 부서 공간."""
    return [f"dept:{d}" for d in known_depts(cfg, accounts)]


def area_folders(db) -> list[dict]:
    """원본 보관소의 최상위 폴더와 파일 수 — 원본 장부(archive_files) 경로의 첫 단계. 플랫폼 업로드처럼 경로와 다른 묶음은 뺀다."""
    try:
        from zzaimy.app import archive
        archive.ensure(db)
        with db._conn() as conn:
            rows = conn.execute("SELECT area, COUNT(*) FROM archive_files WHERE removed_at = '' AND area <> ''"
                                " AND rel LIKE area || '/%' GROUP BY area ORDER BY 2 DESC").fetchall()
    except Exception:
        return []
    return [{"folder": str(r[0]), "files": int(r[1])} for r in rows]


def grant_changes(cfg: dict, accounts: dict, cells: list[str], was: list[str], on: list[str],
                  data_dir: str | Path | None = None) -> tuple[dict, list, list]:
    """계정 × 부서 공간 표의 저장 — 화면에 보인 칸(cells) 가운데 처음 값(was)과 지금 값(on)이 다른 칸만 지금 설정에 반영한다.
    칸 열쇠는 「계정\\t공간」. 돌려주는 것: (새 grants, 반영한 [(계정, 공간, 켬?)], 거절한 [(칸, 사유)])."""
    grants = {u: list(v) for u, v in cfg.get("grants", {}).items()}
    allowed = set(dept_spaces(cfg, accounts))
    was_s, on_s = set(was), set(on)
    done, bad = [], []
    for key in dict.fromkeys(cells):
        want = key in on_s
        if want == (key in was_s):
            continue
        user, _, sid = key.partition("\t")
        role = accounts.get(user, {}).get("role")
        if user not in accounts:
            bad.append((key, "없는 계정"))
        elif role in ("student", "dev"):
            bad.append((key, "학생·관리자 계정은 추가 권한 대상이 아님"))
        elif sid not in allowed:
            bad.append((key, "없는 부서 공간"))
        elif want and resolve(role or "staff", accounts[user].get("dept"), data_dir).get("grant_depts") is None:   # 전부 검색(관리자 꼴)
            bad.append((key, "이미 사업 문서 전부를 검색하는 계정"))
        else:
            cur = grants.setdefault(user, [])
            if want and sid not in cur:
                cur.append(sid)
            elif not want and sid in cur:
                cur.remove(sid)
            done.append((user, sid, want))
    grants = {u: sorted(v) for u, v in grants.items() if v}
    return grants, done, bad


def area_changes(cfg: dict, accounts: dict, folders_known: list[str], folders: list[str], depts: list[str]) -> tuple[dict, list, list]:
    """폴더별 담당 부서 저장 — 고른 값만 받는다(원본 장부의 폴더·이미 설정된 폴더, 알려진 부서 또는 빈 값=정하지 않음).
    돌려주는 것: (새 dept_of_area, 바뀐 [(폴더, 전, 후)], 거절한 [(폴더, 사유)]). 거절이 있으면 부른 쪽이 저장하지 않는다."""
    cur = dict(cfg.get("dept_of_area", {}))
    ok_folders = set(folders_known) | set(cur)
    ok_depts = set(known_depts(cfg, accounts))
    new, changed, bad = dict(cur), [], []
    if len(folders) != len(depts):
        return cur, [], [("", "폴더와 부서 수가 맞지 않음")]
    for f, d in zip(folders, depts):
        f, d = f.strip(), d.strip()
        if f not in ok_folders:
            bad.append((f, "원본 장부에 없는 폴더"))
            continue
        before = cur.get(f, "")
        if d == before:
            continue                                   # 그대로 둔 줄 — 예전에 손으로 적은 부서도 그대로 남는다
        if d and d not in ok_depts:
            bad.append((f, f"알 수 없는 부서 「{d}」"))
            continue
        if d:
            new[f] = d
        else:
            new.pop(f, None)
        changed.append((f, before, d))
    return new, changed, bad


def backfill_preview(db, data_dir: str | Path) -> dict:
    """「이미 반입된 문서에도 적용」을 누르면 바뀌는 문서 수 — backfill_depts 와 같은 조건에서 부서·열람 등급이 지금과 다른 것만. {부서: 건수}."""
    cfg = config(data_dir)
    out: dict[str, int] = {}
    with db._conn() as conn:
        for area, dept in cfg["dept_of_area"].items():
            n = conn.execute("SELECT COUNT(*) FROM documents WHERE stored_path LIKE ? AND doc_type = 'grant'"
                             " AND (COALESCE(dept, '') <> ? OR COALESCE(access_level, '') <> ?)",
                             (f"dgx://{area}/%", dept, "public" if dept == "공통" else "dept")).fetchone()[0]
            out[dept] = out.get(dept, 0) + int(n or 0)
    return out


def _grant_where(sc: dict, a: str = "") -> tuple[str, list] | None:
    """공간의 사업 문서 집합 조건(공간 목록과 같은 집합) — a 는 documents 표 별칭(「d.」). 사업 문서를 쓰지 않는 공간이면 None."""
    if not sc.get("grant"):
        return None
    base = f"{a}doc_type = 'grant' AND {a}status = 'reviewed'"
    if sc.get("grant_depts") is None:
        return base, []
    if not sc["grant_depts"]:
        return None
    ph = ",".join("?" * len(sc["grant_depts"]))
    return f"{base} AND COALESCE({a}dept, '공통') IN ({ph})", list(sc["grant_depts"])


def target_scope(target: str, accounts: dict, data_dir: str | Path | None = None) -> tuple[str, dict] | None:
    """「space:<공간>」 또는 「user:<계정>」 → (보이는 이름, 검색 범위). 범위는 search_scope 그대로(채팅·검색창과 같다)."""
    kind, _, val = (target or "").partition(":")
    if kind == "user":
        a = accounts.get(val)
        if a is None:
            return None
        role, dept = a.get("role", "staff"), a.get("dept") or None
        return f"{val} 계정", search_scope(role, dept, val, data_dir)
    if kind == "space":
        if val == "dev":
            role, dept = "dev", None
        elif val == "student":
            role, dept = "student", None
        elif val == "staff":
            role, dept = "staff", None
        elif val.startswith("dept:") and val[5:]:
            role, dept = "staff", val[5:]
        else:
            return None
        return f"{val} 공간", search_scope(role, dept, None, data_dir)
    return None


def composition(db, sc: dict, limit: int = 8) -> dict:
    """공간의 문서 구성 — 사업 문서는 사업별(원본 장부 사업 분류)·갈래별 상위, 규정 색인은 문서별 조각 수 상위."""
    from zzaimy.app.doc_routing import KINDS
    from zzaimy.app.program_names import display_name

    out: dict = {"programs": [], "kinds": [], "regulations": [], "grant_docs": 0}
    where = _grant_where(sc)
    if where is not None:
        cond, args = where
        dcond = _grant_where(sc, "d.")[0]
        try:
            from zzaimy.app import archive
            archive.ensure(db)                        # 원본 장부 표가 없는 새 설치에서도 사업별 줄이 나오게
        except Exception:
            pass
        with db._conn() as conn:
            out["grant_docs"] = int(conn.execute(f"SELECT COUNT(*) FROM documents WHERE {cond}", args).fetchone()[0] or 0)
            kinds = conn.execute(f"SELECT kind, COUNT(*) FROM documents WHERE {cond} GROUP BY kind ORDER BY 2 DESC", args).fetchall()
            try:
                progs = conn.execute(
                    "SELECT a.program_name, COUNT(DISTINCT d.id) FROM documents d LEFT JOIN archive_files a"
                    f" ON a.doc_id = d.id AND a.removed_at = '' WHERE {dcond} GROUP BY a.program_name", args).fetchall()
            except Exception:                         # 원본 장부를 못 읽으면 사업 분류 없이 센다
                progs = [("", out["grant_docs"])]
        merged: dict[str, int] = {}
        for name, n in progs:
            label = display_name(str(name or "")) or "사업 미분류"
            merged[label] = merged.get(label, 0) + int(n or 0)
        out["programs"] = [{"name": k, "n": v} for k, v in sorted(merged.items(), key=lambda kv: -kv[1])[:limit]]
        out["kinds"] = [{"name": KINDS.get(str(k or ""), str(k or "") or "갈래 미정"), "n": int(n or 0)} for k, n in kinds[:limit]]
    reg = db.list_regulation_chunks(**{k: sc[k] for k in ("levels", "dept") if k in sc})
    per: dict[int, list] = {}
    for r in reg:
        per.setdefault(int(r["doc_id"]), [r.get("reg_title") or "", 0])[1] += 1
    out["regulations"] = [{"doc_id": d, "name": t, "n": n} for d, (t, n) in sorted(per.items(), key=lambda kv: -kv[1][1])[:limit]]
    return out


def preview_search(db, sc: dict, question: str, k: int = 5) -> dict:
    """그 범위로 실제 검색한 상위 결과 — 규정은 운영 후보 단계(어휘·임베딩 융합, regulations.hybrid_candidates), 사업 문서는
    운영 검색(grant_search.search). 범위 인자는 응답기(responder)가 넘기는 것과 같다. 생성 모델은 부르지 않는다."""
    from zzaimy.app.doc_routing import KINDS
    from zzaimy.app.storage import title_of

    out: dict = {"regulation": [], "grant": [], "notes": []}
    titles: dict[int, dict] = {}

    def _doc(did: int) -> dict:
        if did not in titles:
            titles[did] = db.get_document(did) or {}
        return titles[did]

    try:
        from zzaimy.app.regulations import hybrid_candidates
        for h in hybrid_candidates(db, question, 2, None, sc.get("dept"), user=sc.get("user"), levels=sc.get("levels"))[:k]:
            d = _doc(int(h["doc_id"]))
            out["regulation"].append({"doc_id": int(h["doc_id"]), "title": h.get("reg_title") or title_of(d.get("filename") or ""),
                                      "where": h.get("heading") or "", "head": (h.get("content") or "")[:160]})
    except Exception as e:
        out["notes"].append(f"규정 검색 실패: {type(e).__name__}")
    if sc.get("role") == "student" or not sc.get("grant"):
        out["notes"].append("이 범위는 사업 문서를 쓰지 않음")
    else:
        try:
            from zzaimy.app import grant_search
            for h in grant_search.search(db, question, k=k, user=sc.get("user"), depts=sc.get("grant_depts"))["hits"]:
                d = _doc(int(h["doc_id"]))
                out["grant"].append({"doc_id": int(h["doc_id"]), "title": title_of(d.get("filename") or h.get("filename") or ""),
                                     "where": " > ".join(h.get("path", [])[:-1]) or KINDS.get(d.get("kind") or "", ""),
                                     "dept": d.get("dept") or "공통", "head": (h.get("content") or "")[:160]})
        except Exception as e:
            out["notes"].append(f"사업 문서 검색 실패: {type(e).__name__}")
    return out


# ---------------------------------------------------------------- 부서 RAG — 공통 RAG 옆에 부서마다 따로 두는 검색 범위

_DEPT_NAME = re.compile(r"^[0-9A-Za-z가-힣][0-9A-Za-z가-힣 .·()\-+&]{1,29}$")


def dept_rag_rows(db, cfg: dict, accounts: dict, depts: list[str] | None = None) -> list[dict]:
    """부서 RAG 목록 — 부서마다 든 문서 수, 쓰는 계정(그 부서 계정), 추가 권한으로 쓰는 계정, 만든 기록.
    depts: 부서 목록(사용자 관리의 부서 공용까지 — user_admin.known_depts). 없으면 이 설정이 아는 부서."""
    made = {str(r.get("dept")): r for r in cfg.get("dept_rags", [])}
    depts = depts if depts is not None else known_depts(cfg, accounts)
    counts: dict[str, int] = {}
    if depts:
        with db._conn() as conn:
            counts = {str(r[0]): int(r[1]) for r in conn.execute(
                f"SELECT dept, COUNT(*) FROM documents WHERE dept IN ({','.join('?' * len(depts))}) GROUP BY dept", depts)}
    grants = cfg.get("grants", {})
    return [{"dept": d, "docs": counts.get(d, 0),
             "members": sorted(u for u, a in accounts.items() if str(a.get("dept") or "").strip() == d),
             "granted": sorted(u for u, g in grants.items() if f"dept:{d}" in g),
             "made": made.get(d, {}), "folders": sorted(f for f, v in cfg.get("dept_of_area", {}).items() if v == d)}
            for d in depts]


def create_dept_rag(cfg: dict, name: str, by: str = "") -> tuple[dict, str]:
    """부서 RAG 하나를 만든다(빈 공간) — (새 설정, 오류). 이름은 계정의 부서 이름과 같게 적는다."""
    import time
    name = " ".join((name or "").split())
    if not _DEPT_NAME.match(name) or name == "공통":
        return cfg, "부서 이름은 2~30자의 한글·영문·숫자로 적습니다(「공통」은 쓸 수 없음)"
    if any(str(r.get("dept")) == name for r in cfg.get("dept_rags", [])):
        return cfg, f"「{name}」 부서 RAG 는 이미 있습니다"
    new = dict(cfg)
    new["dept_rags"] = [*cfg.get("dept_rags", []), {"dept": name, "at": time.strftime("%Y-%m-%d"), "by": by}]
    return new, ""


def move_docs(db, ids: list[int], dept: str) -> int:
    """문서를 부서 RAG(dept) 또는 공통 RAG(「공통」)로 — 부서 RAG 는 부서 제한, 공통은 전체 공개. 조각까지 바로 따라간다."""
    level = "public" if dept in ("", "공통") else "dept"
    dept = dept or "공통"
    n = 0
    for did in dict.fromkeys(int(x) for x in ids):
        if db.get_document(did) is None:
            continue
        db.set_document_scope(did, dept=dept, access_level=level)
        n += 1
    return n


def delete_dept_rag(db, cfg: dict, name: str) -> tuple[dict, int]:
    """부서 RAG 를 지운다 — 든 문서는 공통 RAG 로 돌리고, 추가 권한·폴더 짝에서도 뺀다. (새 설정, 공통으로 돌린 문서 수)."""
    with db._conn() as conn:
        ids = [int(r[0]) for r in conn.execute("SELECT id FROM documents WHERE dept = ?", (name,))]
    moved = move_docs(db, ids, "공통")
    new = dict(cfg)
    new["dept_rags"] = [r for r in cfg.get("dept_rags", []) if str(r.get("dept")) != name]
    new["grants"] = {u: [s for s in g if s != f"dept:{name}"] for u, g in cfg.get("grants", {}).items()}
    new["grants"] = {u: g for u, g in new["grants"].items() if g}
    new["dept_of_area"] = {f: v for f, v in cfg.get("dept_of_area", {}).items() if v != name}
    return new, moved
