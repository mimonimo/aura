"""프로젝트·대화 문서의 세 갈래 — 첨부 문서 / 참고 문서 / 기준 문서.

대화 문서함 패널, 프로젝트 화면의 문서 목록, 지침·기준 탭이 모두 이 목록 하나를 본다.
화면마다 따로 모으면(소유자·섹터·검토 상태 거르기가 제각각) 같은 프로젝트인데 문서가 달라 보이기 때문이다.

- attached  첨부 문서: 프로젝트에 올린 문서와 대화에 첨부한 문서
- reference 참고 문서: 문서함의 기존 문서를 끌어온 것 — 에이전트가 답·초안에 근거로 불러온 문서(chat_sources),
            작업 원본으로 이은 문서
- rules     기준 문서: 프로젝트에 연결된 기준 문서(공고·기본계획·지침·심사 기준)와 프로젝트에 올린 기준 문서

프로젝트 대화면 프로젝트 단위(그 프로젝트의 모든 대화)로 모아 어느 화면에서 봐도 같다. 프로젝트 없는 대화는 그 대화만.
열람 권한(access_policy.visible)을 통과한 문서만 담는다.
"""
from __future__ import annotations

from zzaimy.app.access_policy import visible

GROUPS = (("attached", "첨부 문서"), ("reference", "참고 문서"), ("rules", "기준 문서"))
# 근거 기준이 되는 서류 갈래(doc_routing.KINDS 의 열쇠) — 공고·지침·심사 기준·규정·기본계획
CRITERIA_KINDS = frozenset({"announcement", "guideline", "criteria", "regulation", "basic_plan"})
GROUP_LABELS = dict(GROUPS)
AGENT_NOTE = "에이전트 참고"                # 에이전트가 답·초안의 근거로 불러온 문서(어떻게 찾았는지는 화면에 보이지 않는다)
REFERENCE_LIMIT = 40


def session_ids(db, project_id: int | None, session_id: int | None = None) -> list[int]:
    """모을 대화 — 프로젝트면 그 프로젝트의 모든 대화, 아니면 그 대화 하나."""
    ids: list[int] = []
    if project_id:
        with db._conn() as conn:
            ids = [int(r[0]) for r in conn.execute(
                "SELECT id FROM chat_sessions WHERE project_id = ? ORDER BY id DESC", (project_id,)).fetchall()]
    if session_id and int(session_id) not in ids:
        ids.insert(0, int(session_id))
    return ids


def agent_doc_ids(db, sids: list[int]) -> list[tuple[int, str]]:
    """에이전트가 답·초안의 근거로 불러온 문서 — 최근 것부터, 문서마다 한 번. 하한 미달(약한 근거)은 뺀다.

    근거는 답변마다 chat_sources 에 남는다(chat_topics.record). 표가 아직 없으면(앱을 거치지 않은 DB) 빈 목록."""
    if not sids:
        return []
    marks = ",".join("?" * len(sids))
    try:
        with db._conn() as conn:
            rows = conn.execute(
                f"SELECT doc_id, origin FROM chat_sources WHERE session_id IN ({marks})"
                " AND doc_id IS NOT NULL AND weak = 0 ORDER BY id DESC", list(sids)).fetchall()
    except Exception:
        return []
    out: dict[int, str] = {}
    for r in rows:
        try:
            did = int(r[0])
        except (TypeError, ValueError):
            continue
        out.setdefault(did, r[1] or "")
    return list(out.items())


def _session_files(db, sids: list[int], kind: str) -> list[int]:
    ids: list[int] = []
    for sid in sids:
        ids += [int(f["doc_id"]) for f in db.list_files(kind=kind, session_id=sid) if f.get("doc_id")]
    return list(dict.fromkeys(ids))


def _uploaded_via(d: dict) -> str:
    """프로젝트 문서가 어디서 왔는가 — 학교 원본에서 가져온 문서(dgx://)는 그 출처를 그대로 보인다."""
    return "학교 원본에서 가져옴" if str(d.get("stored_path") or "").startswith("dgx://") else "프로젝트에 올림"


def collect(db, project: dict | None = None, session_id: int | None = None, *,
            dept: str | None = None, user: str | None = None, role: str = "", also: tuple | list = (),
            lent: tuple | list = ()) -> dict[str, list[dict]]:
    """세 갈래 문서 목록. 항목은 문서 행(dict)에 group·group_label·via(어디서 왔는가)를 더한 것."""
    pid = int(project["id"]) if project and project.get("id") else None
    sids = session_ids(db, pid, session_id)
    groups: dict[str, list[dict]] = {key: [] for key, _ in GROUPS}
    seen: set[int] = set()
    cache: dict[int, dict | None] = {}

    def _doc(did: int) -> dict | None:
        if did not in cache:
            cache[did] = db.get_document(did)
        return cache[did]

    def _add(d: dict | None, group: str, via: str) -> None:
        if not d or int(d["id"]) in seen or not visible(d, dept=dept, user=user, role=role, also=also, lent=lent):
            return
        seen.add(int(d["id"]))
        groups[group].append(dict(d, group=group, group_label=GROUP_LABELS[group], via=via))

    project_docs = db.list_documents(project_id=pid) if pid else []
    project_docs.sort(key=lambda d: int(d["id"]), reverse=True)
    # ③ 기준 문서 — 연결된 기준이 먼저, 프로젝트에 올렸지만 연결이 풀린 기준 문서도 여기에
    if pid:
        for did in db.get_project_criteria_ids(pid):
            _add(_doc(int(did)), "rules", "프로젝트에 연결")
        for d in project_docs:
            if d.get("doc_type") == "regulation":
                _add(d, "rules", _uploaded_via(d))
    # ① 첨부 문서 — 대화 첨부를 먼저, 그다음 프로젝트에 올린 문서
    attached_ids = _session_files(db, sids, "attachment")
    for did in attached_ids:
        d = _doc(did)
        if d and d.get("doc_type") == "regulation":
            _add(d, "rules", "대화 첨부")
        else:
            _add(d, "attached", "대화 첨부")
    for d in project_docs:
        if d.get("doc_type") != "regulation":
            _add(d, "attached", _uploaded_via(d))
    # ② 참고 문서 — 작업 원본으로 이은 문서, 에이전트가 불러온 문서(최근 것부터)
    for did in _session_files(db, sids, "google"):
        if len(groups["reference"]) >= REFERENCE_LIMIT:
            break
        _add(_doc(did), "reference", "작업 원본으로 불러옴")
    for did, _origin in agent_doc_ids(db, sids):
        if len(groups["reference"]) >= REFERENCE_LIMIT:
            break
        _add(_doc(did), "reference", AGENT_NOTE)
    return groups


def projects_of(db, doc: dict) -> list[int]:
    """문서가 속한 프로젝트 — 프로젝트에 올린 문서면 그 프로젝트, 프로젝트 대화에 첨부한 문서면 그 대화의 프로젝트."""
    pids: list[int] = []
    if doc.get("project_id"):
        pids.append(int(doc["project_id"]))
    for f in db.list_files(kind="attachment", doc_id=int(doc["id"])):
        sess = db.get_chat_session(int(f["session_id"])) if f.get("session_id") else None
        if sess and sess.get("project_id") and int(sess["project_id"]) not in pids:
            pids.append(int(sess["project_id"]))
    return pids


def auto_criteria_project(project: dict | None) -> bool:
    """기준 자동 연결을 받는 프로젝트인가 — 담당자가 만든 진행 중 프로젝트만.

    보관 묶음은 받지 않는다: 보관 상태(archived)이거나, 학교 원본에서 자동으로 만든 묶음(archive_source 가 찬 것,
    archive.py 의 DGX 사업별 묶음은 'dgx')이다. 그런 묶음은 수천 건의 반입 원본을 담아 「근거 기준」의 뜻이 없다."""
    return bool(project) and not project.get("archived") and not (project.get("archive_source") or "").strip()


def auto_criteria_doc(doc: dict, project: dict, via: str) -> bool:
    """자동 연결할 문서인가 — 갈래 분류만으로는 정하지 않는다(분류 신뢰도 값이 없다). 둘째 단서로 출처를 본다.

    담당자가 그 프로젝트에 직접 올렸거나(문서 주인 = 프로젝트 주인) 그 프로젝트 대화에 첨부한 문서만.
    학교 원본에서 옮겨 온 문서(dgx://)는 잇지 않는다."""
    if str(doc.get("stored_path") or "").startswith("dgx://"):
        return False
    if via == "대화 첨부":
        return True
    return (doc.get("owner") or "") == (project.get("owner") or "")


def memberships(db, doc: dict) -> list[tuple[int, str]]:
    """(프로젝트 번호, 어떻게 들어왔나) — 「프로젝트에 올림」 또는 「대화 첨부」."""
    out: list[tuple[int, str]] = []
    if doc.get("project_id"):
        out.append((int(doc["project_id"]), "프로젝트에 올림"))
    for f in db.list_files(kind="attachment", doc_id=int(doc["id"])):
        sess = db.get_chat_session(int(f["session_id"])) if f.get("session_id") else None
        if sess and sess.get("project_id") and int(sess["project_id"]) not in {p for p, _ in out}:
            out.append((int(sess["project_id"]), "대화 첨부"))
    return out


def link_criteria_kind(db, doc_id: int, only: set[int] | None = None) -> list[int]:
    """공고·지침·기준으로 분류된 문서를 그 문서가 속한 프로젝트의 근거 기준으로 잇는다 — 연결한 프로젝트 번호.

    갈래(kind)는 반입 뒤 분류(doc_routing)가 정한다. 어느 문서든 같은 규칙이고, 사업 이름은 보지 않는다.
    이미 이어져 있으면 그대로 둔다. 연결을 풀면 첨부 문서로 돌아가고, 다시 분류될 때만 다시 잇는다.
    only 를 주면 그 프로젝트들만 잇는다(소급 연결이 열람 범위를 확인한 프로젝트만 고를 때).
    담당자가 만든 진행 중 프로젝트(auto_criteria_project)에, 담당자가 직접 올리거나 대화에 첨부한 문서(auto_criteria_doc)만 잇는다."""
    doc = db.get_document(int(doc_id))
    if not doc or (doc.get("kind") or "") not in CRITERIA_KINDS:
        return []
    done = []
    for pid, via in memberships(db, doc):
        if only is not None and pid not in only:
            continue
        project = db.get_project(pid)
        if not auto_criteria_project(project) or not auto_criteria_doc(doc, project, via):
            continue
        if int(doc_id) not in {int(x) for x in db.get_project_criteria_ids(pid)}:
            db.add_project_criteria(pid, [int(doc_id)])
            done.append(pid)
    return done


def criteria_kind_backlog(db, accounts: dict | None = None,
                          project_ids: set[int] | None = None) -> dict[str, list[dict]]:
    """기존 프로젝트에서 아직 기준 문서로 잇지 않은 공고·지침·기준 갈래 문서 — 소급 연결 계획.

    대상은 link_criteria_kind 와 같다 — 담당자가 만든 진행 중 프로젝트에 담당자가 올렸거나 그 대화에 첨부한 문서
    (보관 묶음·학교 원본에서 옮겨 온 문서는 빠진다).
    프로젝트 소유자가 열람할 수 없는 문서(access_policy.visible)는 잇지 않고 skipped 로 돌려준다.
    accounts 는 계정 → {role, dept}; 없는 계정은 담당자(staff)·부서 없음으로 본다.
    반환: {"link": [...], "skipped": [...]}, 항목은 project_id·doc_id·via."""
    kinds = sorted(CRITERIA_KINDS)
    marks = ",".join("?" * len(kinds))
    with db._conn() as conn:
        pairs = [(int(r[0]), int(r[1]), "프로젝트에 올림") for r in conn.execute(
            "SELECT d.project_id, d.id FROM documents d JOIN projects p ON p.id = d.project_id"
            f" WHERE p.archived = 0 AND COALESCE(p.archive_source, '') = '' AND d.kind IN ({marks})", kinds)]
        pairs += [(int(r[0]), int(r[1]), "대화 첨부") for r in conn.execute(
            "SELECT s.project_id, f.doc_id FROM files f JOIN chat_sessions s ON s.id = f.session_id"
            " JOIN documents d ON d.id = f.doc_id JOIN projects p ON p.id = s.project_id"
            " WHERE f.kind = 'attachment' AND p.archived = 0 AND COALESCE(p.archive_source, '') = ''"
            f" AND d.kind IN ({marks})", kinds)]
    out: dict[str, list[dict]] = {"link": [], "skipped": []}
    seen: set[tuple[int, int]] = set()
    linked: dict[int, set[int]] = {}
    for pid, did, via in sorted(pairs):
        if (pid, did) in seen or (project_ids is not None and pid not in project_ids):
            continue
        seen.add((pid, did))
        if pid not in linked:
            linked[pid] = {int(x) for x in db.get_project_criteria_ids(pid)}
        if did in linked[pid]:
            continue
        project, doc = db.get_project(pid), db.get_document(did)
        if not project or not doc or not auto_criteria_project(project) or not auto_criteria_doc(doc, project, via):
            continue
        acct = (accounts or {}).get(project.get("owner") or "", {})
        ok = visible(doc, dept=acct.get("dept") or None, user=project.get("owner"), role=acct.get("role", "staff"))
        out["link" if ok else "skipped"].append({"project_id": pid, "doc_id": did, "via": via})
    return out


def counts(groups: dict[str, list[dict]]) -> dict[str, int]:
    return {key: len(groups.get(key, [])) for key, _ in GROUPS}
