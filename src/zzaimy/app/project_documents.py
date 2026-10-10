"""프로젝트·대화 문서의 세 갈래 — 사용자 첨부 문서 / 참고 문서 / 규정·지침.

대화 문서함 패널, 프로젝트 화면의 문서 목록, 지침·기준 탭이 모두 이 목록 하나를 본다.
예전에는 화면마다 따로 모아(소유자 거르기·섹터 거르기·검토 완료만 …) 같은 프로젝트인데 문서가 달라 보였다.

- attached  사용자 첨부 문서: 프로젝트에 올린 문서와 대화에 첨부한 문서
- reference 참고 문서: 문서함의 기존 문서를 끌어온 것 — 에이전트가 답·초안에 근거로 불러온 문서(chat_sources),
            작업 원본으로 이은 문서
- rules     규정·지침: 프로젝트에 연결된 기준 문서(공고·기본계획·지침·심사 기준)와 프로젝트에 올린 기준 문서

프로젝트 대화면 프로젝트 단위(그 프로젝트의 모든 대화)로 모아 어느 화면에서 봐도 같다. 프로젝트 없는 대화는 그 대화만.
열람 권한(access_policy.visible)을 통과한 문서만 담는다.
"""
from __future__ import annotations

from zzaimy.app.access_policy import visible

GROUPS = (("attached", "사용자 첨부 문서"), ("reference", "참고 문서"), ("rules", "규정·지침"))
GROUP_LABELS = dict(GROUPS)
AGENT_NOTE = "에이전트가 불러옴"
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


def collect(db, project: dict | None = None, session_id: int | None = None, *,
            dept: str | None = None, user: str | None = None, role: str = "") -> dict[str, list[dict]]:
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
        if not d or int(d["id"]) in seen or not visible(d, dept=dept, user=user, role=role):
            return
        seen.add(int(d["id"]))
        groups[group].append(dict(d, group=group, group_label=GROUP_LABELS[group], via=via))

    project_docs = db.list_documents(project_id=pid) if pid else []
    project_docs.sort(key=lambda d: int(d["id"]), reverse=True)
    # ③ 규정·지침 — 연결된 기준이 먼저, 프로젝트에 올렸지만 연결이 풀린 기준 문서도 여기에
    if pid:
        for did in db.get_project_criteria_ids(pid):
            _add(_doc(int(did)), "rules", "프로젝트에 연결")
        for d in project_docs:
            if d.get("doc_type") == "regulation":
                _add(d, "rules", "프로젝트에 올림")
    # ① 사용자 첨부 문서 — 대화 첨부를 먼저, 그다음 프로젝트에 올린 문서
    attached_ids = _session_files(db, sids, "attachment")
    for did in attached_ids:
        d = _doc(did)
        if d and d.get("doc_type") == "regulation":
            _add(d, "rules", "대화 첨부")
        else:
            _add(d, "attached", "대화 첨부")
    for d in project_docs:
        if d.get("doc_type") != "regulation":
            _add(d, "attached", "프로젝트에 올림")
    # ② 참고 문서 — 작업 원본으로 이은 문서, 에이전트가 불러온 문서(최근 것부터)
    for did in _session_files(db, sids, "google"):
        if len(groups["reference"]) >= REFERENCE_LIMIT:
            break
        _add(_doc(did), "reference", "작업 원본으로 불러옴")
    for did, origin in agent_doc_ids(db, sids):
        if len(groups["reference"]) >= REFERENCE_LIMIT:
            break
        _add(_doc(did), "reference", AGENT_NOTE + (f" · {origin}" if origin else ""))
    return groups


def counts(groups: dict[str, list[dict]]) -> dict[str, int]:
    return {key: len(groups.get(key, [])) for key, _ in GROUPS}
