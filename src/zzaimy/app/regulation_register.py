"""반입한 규정을 기준 문서로 — 후보 찾기·등록·해제(라이브러리 「기준」 칩의 관리자 도구, criteria_regs_routes).

반입 자료(DGX 원본)의 교내 규정·지침은 사업 문서로 들어와 사업 문서 검색에만 쓰였다. 규정 검색(조문 단위 조각·재순위)과
학생 공개는 기준 문서(doc_type regulation)만 된다. 그래서 담당자가 후보 가운데 골라 기준 문서로 올린다 — 다시 판독하지 않고
저장된 본문(masked_text)을 반입과 같은 조문 청킹·잡음 관문(regulations.chunk_document·index_ready)으로 조각낸다(scripts/123 과 같은 길).

후보: 처리 완료 사업 문서 가운데 제목이 규정 갈래 낱말(규정·규칙·내규·세칙·학칙·정관·지침)로 끝나는 것
(서류 갈래만 규정인 것은 조문 머리가 있는 협약서·업체 서류일 수 있어 후보로 두지 않는다). 협약서·공문·개정안 대비표처럼 규정을 다루는 다른 서류는 뺀다. 같은 규정의 판 묶음은 최신 판 하나만 후보로
보이고 판 수를 붙인다(옛 판은 사업 문서로 남아 연도를 묻는 질문에 쓰인다).
판 묶음은 반입의 family(원래 파일 이름을 정규화한 값)가 아니라 규정 이름 열쇠(rule_key)로 짓는다 — 파일 이름에는
규정집 번호(4-37)·제정일(2025년02월28일)·학교 이름이 섞여 같은 규정의 판마다 family 가 갈린다(같은 규정이 판 수만큼
기준 문서로 올라간 까닭). 판의 새것은 제목·파일 이름·머리(통과일자)·부칙에 적힌 날짜 가운데 가장 늦은 것으로 가린다.
제목 첫머리가 우리 학교가 아닌 기관(…대학교·…대학·…재단)이면 그 기관의 규정이다 — 참고로 보관한 자료일 뿐
우리 기준 문서가 아니므로 후보로 두지 않는다.
등록·해제는 기록(regulation_register.jsonl)에 남고, 등록 뒤에는 규정 임베딩을 다시 만들어야 뜻 검색에 든다(.reindex-needed).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

# 정규식은 모두 수량에 상한을 두고(역추적이 제목 길이의 제곱으로 늘지 않게), 제목은 TITLE_MAX 자까지만 본다
TITLE_MAX = 300
Q_MAX, Q_WORDS = 100, 8      # 후보 찾기 낱말 — 글자 수·낱말 수 상한
IDS_MAX = 500                # 한 번에 올리거나 해제하는 문서 수 상한
GENRE = re.compile(r"(규정|규칙|내규|세칙|학칙|(?:^|\s|법인)정관|지침)(?:\s{0,3}\([^()]{0,120}\))?(?:\s{0,3}·[^·]{0,120}){0,3}$")
GENRE_WORDS = ("규정", "규칙", "내규", "세칙", "학칙", "정관", "지침")
NOT_RULE = re.compile(r"협약|계약서|공문|요청|안내|개정\s*\(?안\)?|신구\s*조문|대비표|검토|회의|결과|신청서")
KINDS = ("regulation", "guideline")
MIN_CHARS = 80


OURS = ("영남이공대학교", "영남이공대학")
# 제목 첫머리의 발행 기관 — '금오공과대학교연구비관리규정' · '대전보건대학 교외 …' · '한국연구재단 …'
_ISSUER = re.compile(r"^[\s(\[]{0,5}([가-힣]{1,30}?(?:대학교|대학|재단))")
# 기관 이름이 아니라 대학의 갈래를 이르는 말 — '전문대학 교육역량강화사업' · '산학협력선도전문대학 육성사업'
_GENERIC_SCHOOL = re.compile(r"(?:선도|중심|혁신|협력|육성|지원|특성화|사업|우리|본|타|해당|국내|외국|교류|일반|전문)(?:대학교|대학)$")
# 규정집 번호 앞머리 — '4-37 ' · '10-02 ' · '3-1.' · '1-1. ' (번호 뒤에 띄어쓰기나 점이 있을 때만; '3단계'·'3-3규정' 은 이름)
_NUM_HEAD = re.compile(r"^\s{0,5}\d{1,6}(?:\s{0,2}-\s{0,2}\d{1,6}){0,5}(?:\.\s{0,5}|\s{1,5})")
_PAREN = re.compile(r"[(（\[]([^()（）\[\]]*)[)）\]]")
# 판·초안을 가리키는 괄호 — 숫자(날짜·차수·LINC 3.0)가 있거나 개정·상정·공시·시행·초안·안·령·붙임
_REV_PAREN = re.compile(r"\d|개정|상정|공시|시행|초안|최종|수정|령$|^\s*안\s*$|붙임|별첨")
_ACAD_YEAR = re.compile(r"(?:19|20)\d{2}\s*학년도")
# 법령 번호 — '교육부훈령 제511호' · '(교육부 훈령 제381호)' 는 판 표시다(훈령은 개정마다 번호가 바뀐다)
_ORDER_NO = re.compile(r"(?:[가-힣]{1,10}부\s{0,3})?(?:훈령|예규|고시)\s{0,3}제\s{0,3}\d{1,6}\s{0,3}호")
# 라틴 약어만 든 괄호 — '(RISE)' · '(LINC+)' 는 앞 이름의 다른 표기다
_ABBR_PAREN = re.compile(r"^[A-Za-z0-9+＋.\s]*$")
_DATE_SEG = re.compile(r"^[\d\s.\-/년월일()]+$")


def issuer(title: str) -> str:
    """제목 첫머리의 발행 기관 이름(우리 학교면 빈 문자열). 대학의 갈래를 이르는 말은 기관이 아니다."""
    t = _NUM_HEAD.sub("", (title or "")[:TITLE_MAX])
    m = _ISSUER.match(t)
    if not m:
        return ""
    name = m.group(1)
    if name in OURS or _GENERIC_SCHOOL.search(name):
        return ""
    return name


def rule_key(title: str) -> str:
    """같은 규정의 판을 묶는 열쇠 — 규정집 번호·제정일·학년도·판 괄호·학교 이름·띄어쓰기를 뗀 규정 이름."""
    s = (title or "")[:TITLE_MAX].strip()
    s = re.sub(r"\.(hwpx?|pdf|docx?)$", "", s, flags=re.I)
    segs = [x for x in re.split(r"\s+·\s+", s)]
    while len(segs) > 1 and _DATE_SEG.match(segs[-1]) and re.search(r"\d", segs[-1]):
        segs.pop()                                            # '… 규정 · 2012년 2월 23일'
    s = " · ".join(segs)
    prev = None
    while prev != s:
        prev = s
        s = _NUM_HEAD.sub("", s)
    s = _ORDER_NO.sub("", s)
    s = _PAREN.sub(lambda m: "" if (_REV_PAREN.search(m.group(1)) or m.group(1).strip() in OURS
                                    or _ABBR_PAREN.match(m.group(1))) else m.group(0), s)
    s = _ACAD_YEAR.sub("", s)
    for o in OURS:
        s = s.replace(o, "")
    s = re.sub(r"[^가-힣A-Za-z0-9()]", "", s)          # '＋'·'+' 도 뗀다('LINC＋' = 'LINC(＋)')
    return s.lower()


_DATES = (re.compile(r"((?:19|20)\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]?\s*(\d{1,2})?"),
          re.compile(r"(?<!\d)((?:19|20)\d{2})(0[1-9]|1[0-2])([0-3]\d)(?!\d)"))
_BUCHIK = re.compile(r"부\s*칙")


def _dates(text: str, cap: int) -> list[tuple]:
    out = []
    for rx in _DATES:
        for m in rx.finditer(text or ""):
            y, mo, d = int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)
            if y <= cap and mo <= 12 and d <= 31:
                out.append((y, mo, d))
    return out


def revision(title: str, family: str = "", text: str = "") -> tuple:
    """판의 순서 (학년도, 법령 번호, (연, 월, 일)) — 학년도는 제목에 적힌 것(해마다 새로 내는 지침), 법령 번호는 제목의
    '훈령 제553호'(개정마다 커진다), 날짜는 제목·파일 이름(family)·
    머리(통과일자)·부칙에 적힌 날짜 가운데 가장 늦은 것. 없으면 0.

    본문 날짜는 머리 600자와 첫 「부칙」 뒤만 본다 — 조문 속 날짜(경과 규정·기한)는 판의 날짜가 아니다.
    학년도를 날짜와 섞지 않는다 — '2014학년도' 지침은 2013년 12월에 나오므로 섞으면 날짜 없는 판이 앞선다."""
    cap = int(time.strftime("%Y")) + 1
    body = text or ""
    m = _BUCHIK.search(body)
    title, family = (title or "")[:TITLE_MAX], (family or "")[:TITLE_MAX]
    found = _dates(title, cap) + _dates(family, cap) + _dates(body[:600], cap) + (_dates(body[m.end():], cap) if m else [])
    acad = [int(x.group(0)[:4]) for x in _ACAD_YEAR.finditer(title or "")]
    order = [int(re.sub(r"\D", "", x.group(0))) for x in _ORDER_NO.finditer(title or "")]
    return (max(acad) if acad else 0, max(order) if order else 0, max(found) if found else (0, 0, 0))


_DRAFT = re.compile(r"\(\s*안\s*\)|초안|상정\s*안|개정\s*\(?안")


def body_size(text: str) -> int:
    """조문 몸통 길이 — 첫 「부칙」 앞까지(뒤에 붙은 별지·서식은 판의 온전함과 상관없다)."""
    t = text or ""
    m = _BUCHIK.search(t)
    return len(t[: m.start()] if m else t)


COMPLETE = 0.6         # 가장 새 판의 조문 몸통이 바로 앞 판의 이 비율보다 짧으면 잘린 판독으로 보고 다음 판을 쓴다


def pick_current(group: list[dict]) -> dict:
    """한 규정의 판들 가운데 기준 문서로 둘 판 — 가장 새 판(초안보다 확정본), 같으면 조각·본문이 많은 것.

    가장 새 판의 조문 몸통이 바로 앞 판의 COMPLETE 배보다 짧으면 잘린 판독으로 보고 그다음 판을 본다(옛 판들 가운데
    가장 긴 것과 견주지 않는다 — 여러 규정을 한 파일에 담은 옛 합본이 늘 이기게 된다). group 의 항목: {id, title, family, text, chunks?}."""
    def order(d: dict) -> tuple:
        return (revision(d.get("title") or "", d.get("family") or "", d.get("text") or ""),
                not _DRAFT.search(d.get("title") or ""), d.get("chunks") or 0, body_size(d.get("text") or ""), -int(d["id"]))

    ranked = sorted(group, key=order, reverse=True)
    for d, nxt in zip(ranked, ranked[1:]):
        if body_size(d.get("text") or "") >= COMPLETE * body_size(nxt.get("text") or ""):
            return d
    return ranked[-1] if len(ranked) > 1 and body_size(ranked[-1].get("text") or "") else ranked[0]


def _texts(db, ids) -> dict[int, str]:
    ids = [int(i) for i in ids]
    out: dict[int, str] = {}
    with db._conn() as conn:
        for i in range(0, len(ids), 200):
            part = ids[i:i + 200]
            for r in conn.execute("SELECT id, masked_text FROM documents WHERE id IN (" + ",".join("?" * len(part)) + ")",
                                  part).fetchall():
                out[int(r["id"])] = r["masked_text"] or ""
    return out


def candidates(db, q: str = "", limit: int = 400) -> dict:
    """{items:[{id,title,kind,versions,dept}], total, registered:[...]} — 후보는 규정마다(rule_key) 최신 판 하나."""
    words = [w for w in (q or "")[:Q_MAX].split() if w][:Q_WORDS]
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, filename, family, kind, dept, created_at, LENGTH(COALESCE(masked_text, '')) AS n FROM documents"
            " WHERE doc_type = 'grant' AND status = 'reviewed' AND (" + " OR ".join("filename LIKE ?" for _ in GENRE_WORDS) + ")",
            [f"%{w}%" for w in GENRE_WORDS]).fetchall()]
        reg = [dict(r) for r in conn.execute(
            "SELECT d.id, d.filename, d.kind, d.audience, d.dept, COUNT(c.id) AS chunks FROM documents d"
            " LEFT JOIN regulation_chunks c ON c.doc_id = d.id WHERE d.doc_type = 'regulation' AND d.status = 'reviewed'"
            " GROUP BY d.id, d.filename, d.kind, d.audience, d.dept ORDER BY d.filename").fetchall()]
        done = [dict(r) for r in conn.execute(
            "SELECT id, family, filename FROM documents WHERE doc_type = 'regulation'").fetchall()]
    # 제목이 규정 갈래 이름으로 끝나는 것만 — 서류 갈래(kind)가 규정이어도 조문 머리만 있는 협약서·업체 서류일 수 있다.
    # 다른 기관의 규정(제목 첫머리가 우리 학교가 아닌 대학·재단)은 참고 자료다 — 우리 기준 문서가 아니다
    pool = [r for r in rows if (r["n"] or 0) >= MIN_CHARS and not NOT_RULE.search((r["filename"] or "")[:TITLE_MAX])
            and GENRE.search((r["filename"] or "")[:TITLE_MAX]) and not issuer(r["filename"] or "")]
    fam: dict[str, list[dict]] = {}
    for r in pool:
        fam.setdefault(rule_key(r["filename"] or "") or f"#{r['id']}", []).append(r)
    done_by: dict[str, list[dict]] = {}
    for r in done:
        k = rule_key(r["filename"] or "")
        if k in fam:
            done_by.setdefault(k, []).append(r)
    # 판 날짜는 본문(머리·부칙)까지 본다 — 견줄 판이 있는 묶음만 본문을 읽는다
    need = [r["id"] for k, g in fam.items() if len(g) > 1 or k in done_by for r in g]
    need += [r["id"] for g in done_by.values() for r in g]
    text = _texts(db, need)

    rr_pick = pick_current

    def entry(r: dict) -> dict:
        return {"id": r["id"], "title": r["filename"] or "", "family": r.get("family") or "", "text": text.get(r["id"], "")}

    items = []
    for k, group in fam.items():
        # 판 고르기는 정리(superseded)와 같은 규칙(pick_current) — 같은 규정이 이미 기준 문서면 그것과 함께 견줘,
        # 기준 문서가 이기면(더 새 판이 없거나 새 판이 잘린 판독) 후보가 아니다. 나중에 들어온 개정판만 후보로 남는다
        mates = done_by.get(k, [])
        best = rr_pick([entry(r) for r in group + mates]) if (len(group) > 1 or mates) else entry(group[0])
        if any(best["id"] == r["id"] for r in mates):
            continue
        if mates and revision(best["title"], best["family"], best["text"]) <= max(
                revision(r["filename"] or "", r.get("family") or "", text.get(r["id"], "")) for r in mates):
            continue                                   # 기준 문서와 같은 판(사본) — 새 판이 아니다
        top = next(r for r in group if r["id"] == best["id"])
        title = top["filename"] or ""
        if words and not all(w.replace(" ", "") in title.replace(" ", "") for w in words):
            continue
        items.append({"id": top["id"], "title": title, "kind": top["kind"] or "", "versions": len(group), "dept": top["dept"] or ""})
    items.sort(key=lambda x: x["title"])
    return {"items": items[:limit], "total": len(items), "registered": reg}


def _log(data_dir: Path, rec: dict) -> None:
    with (data_dir / "regulation_register.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), **rec}, ensure_ascii=False) + "\n")


def register(db, doc_ids: list[int], data_dir: Path, by: str = "") -> dict:
    """고른 사업 문서를 기준 문서로 — 조각을 만들고 doc_type 을 regulation 으로. 본문이 없거나 조각이 안 나오면 건너뛴다.

    같은 규정(rule_key)의 옛 판이 기준 문서로 있으면 사업 문서로 되돌린다 — 한 규정에 기준 문서는 한 판이다."""
    from zzaimy.app.regulations import chunk_document, index_ready
    done, skipped, made = [], [], 0
    for did in list(dict.fromkeys(int(x) for x in doc_ids))[:IDS_MAX]:
        d = db.get_document(did)
        if not d or d.get("doc_type") != "grant" or d.get("status") != "reviewed" or issuer(d.get("filename") or ""):
            skipped.append(did)
            continue
        chunks = chunk_document(d.get("masked_text") or "")
        if not chunks:
            skipped.append(did)
            continue
        use, _ = index_ready(did, chunks)
        db.add_regulation_chunks(did, d.get("filename") or "", use, sector=d.get("sector") or "common",
                                 dept=d.get("dept"), access_level=d.get("access_level") or "public")
        db.set_document_type(did, "regulation")
        done.append(did)
        made += len(use)
    replaced = []
    if done:
        (data_dir / ".reindex-needed").touch()
        _log(data_dir, {"action": "register", "by": by, "docs": done, "chunks": made})
        replaced = superseded(db, done)
        if replaced:
            demote(db, replaced, data_dir, by=by, reason="옛 판. 같은 규정의 새 판을 기준 문서로 올림")
    return {"registered": len(done), "skipped": len(skipped), "chunks": made, "replaced": len(replaced)}


def manual_unregistered(data_dir: Path) -> set[int]:
    """사람이(화면·도우미에서) 등록 해제한 문서 — 자동 등록이 다시 올리지 않는다. 판 정리(reason 이 있는 해제)·스크립트 해제는 뺀다."""
    out: set[int] = set()
    log = Path(data_dir) / "regulation_register.jsonl"
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("action") == "unregister" and not rec.get("reason") and not str(rec.get("by") or "").startswith(("scripts/", "auto")):
                out |= {int(x) for x in rec.get("docs") or []}
    return out


def auto_register(db, data_dir: Path) -> dict:
    """반입 자료에서 찾은 새 판·미등록 규정을 기준 문서로 저절로 올린다(10-21 사용자: 반입할 때 알아서 등록돼야).
    후보 고르기(candidates)는 화면과 같은 규칙 — 기준 문서보다 새 판일 때만 후보이고, 올리면 옛 판은 사업 문서로 돌아간다(register).
    학교 원본(DGX)에서 들어온 전체 공개 문서만 — 사용자가 올린 문서는 관리자가 고른다. 사람이 등록 해제한 문서는 다시 올리지 않는다."""
    skip = manual_unregistered(data_dir)
    ids = []
    for x in candidates(db)["items"]:
        if int(x["id"]) in skip:
            continue
        d = db.get_document(int(x["id"])) or {}
        # 학교 원본(DGX)에서 들어온 전체 공개 문서만 저절로 — 사용자가 올린 문서는 제목이 「…규정」이어도 기준 문서(모두의 답 근거)로
        # 올리지 않는다(누구나 올린 글을 규정으로 만들 수 있게 되지 않게, 10-21 보안 검토). 그런 문서는 관리자가 화면에서 고른다
        if str(d.get("stored_path") or "").startswith("dgx://") and (d.get("access_level") or "public") == "public":
            ids.append(int(x["id"]))
    if not ids:
        return {"registered": 0, "skipped": 0, "chunks": 0, "replaced": 0}
    return register(db, ids, data_dir, by="auto")


def current_groups(db) -> dict[str, list[dict]]:
    """기준 문서를 규정(rule_key)마다 묶는다 — {열쇠: [{id,title,family,text,chunks}]}."""
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT d.id, d.filename AS title, d.family, d.masked_text AS text,"
            " (SELECT COUNT(*) FROM regulation_chunks c WHERE c.doc_id = d.id) AS chunks"
            " FROM documents d WHERE d.doc_type = 'regulation'").fetchall()]
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(rule_key(r["title"] or "") or f"#{r['id']}", []).append(r)
    return out


def superseded(db, keep_ids=()) -> list[int]:
    """같은 규정의 기준 문서가 여럿이면 기준으로 둘 판(pick_current) 말고 나머지 id.

    keep_ids 가 있으면 그 문서가 든 묶음만 보고, 그 문서를 남긴다(방금 올린 판)."""
    keep = {int(x) for x in keep_ids}
    out = []
    for group in current_groups(db).values():
        if len(group) < 2:
            continue
        mine = [d for d in group if int(d["id"]) in keep]
        if keep and not mine:
            continue
        cur = mine[0] if mine else pick_current(group)
        out += [int(d["id"]) for d in group if d["id"] != cur["id"]]
    return out


def demote(db, doc_ids: list[int], data_dir: Path, by: str = "", reason: str = "") -> list[int]:
    """기준 문서를 사업 문서로 되돌린다 — 규정 조각을 지우고 doc_type 을 grant 로, 기록에 남기고 재색인 표시."""
    done = []
    for did in dict.fromkeys(int(x) for x in doc_ids):
        d = db.get_document(did)
        if not d or d.get("doc_type") != "regulation":
            continue
        with db._conn() as conn:
            conn.execute("DELETE FROM regulation_chunks WHERE doc_id = ?", (did,))
        db.set_document_type(did, "grant")
        done.append(did)
    if done:
        (data_dir / ".reindex-needed").touch()
        rec = {"action": "unregister", "by": by, "docs": done}
        if reason:
            rec["reason"] = reason
        _log(data_dir, rec)
    return done


def unregister(db, doc_ids: list[int], data_dir: Path, by: str = "") -> dict:
    """등록 해제 — 규정 조각을 지우고 사업 문서로 되돌린다. 처음부터 기준 문서로 올린 문서(기록에 없는 것)는 건드리지 않는다."""
    mine: set[int] = set()
    log = data_dir / "regulation_register.jsonl"
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ids = set(rec.get("docs") or [])
            mine = (mine | ids) if rec.get("action") == "register" else (mine - ids)
    ids = list(dict.fromkeys(int(x) for x in doc_ids))[:IDS_MAX]
    done = demote(db, [i for i in ids if i in mine], data_dir, by=by)
    return {"unregistered": len(done), "kept": len(ids) - len(done)}
