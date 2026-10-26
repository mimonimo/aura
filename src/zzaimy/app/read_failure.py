"""읽지 못한 문서 — 사유 갈래, 사유마다 다시 하는 길, 글자 읽기 대기열, 원본 보관소 실패 원본 다시 시도.

사람이 올린 문서(라이브러리·문서함·프로젝트·채팅 첨부·드라이브 가져오기)는 모두 처리기(pipeline.process)를 거치고, 실패하면
documents.status = 'failed' 와 error 가 남는다. 화면(목록·문서 화면·알림)은 여기 info() 로 「읽지 못함 · 사유」와 단추를 그린다.
사유 갈래는 연동 점검(scripts/175)과 같은 이름을 쓴다(reason_label).

글자 읽기 대기열(read_queue): 스캔본이 판독 모델이 없을 때 들어와 실패하면 대기열에 넣고, 판독 모델(또는 GPU 구조 추출)이
닿을 때 한 건씩 다시 읽는다. 앱 안의 작업 줄 하나가 맡는다(start_worker).

원본 보관소(archive_retry): 정리함 「처리 실패」에서 고른 원본을 정리함에서 빼고 장부에 적어 두면, 다음 동기화(scripts/217)가
DGX 원본을 하나씩 받아(archive_original.fetch) 이 서버 처리기로 다시 읽는다. 다시 실패하면 실패 장부에 새 사유를 적고
정리함으로 돌아간다.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

# (갈래, 이름, 오류 글에 든 말) — 앞에서부터 맞춘다. 이름은 연동 점검(175)의 실패 사유와 같다
REASONS = (
    ("locked", "암호 문서", ("암호",)),
    ("empty", "빈 파일", ("빈 파일",)),
    ("mismatch", "형식 불일치", ("형식이 아닙니다", "웹 페이지가 저장돼", "문서 형식이 아님")),
    ("timeout", "시간 초과", ("시간 초과",)),
    ("dup", "같은 내용 중복", ("같은 내용",)),
    ("scan", "스캔 판독 없음", ("스캔 PDF", "판독 결과 없음")),
    ("env", "판독 환경 오류", ("ModuleNotFound",)),
    ("notext", "글 없는 문서", ("글이 없는",)),
)
OTHER = ("other", "기타 판독 실패")
LABEL = {k: lab for k, lab, _ in REASONS} | {OTHER[0]: OTHER[1]}
HINT = {
    "locked": "열람 암호가 걸려 있습니다",
    "empty": "파일에 내용이 없습니다",
    "mismatch": "확장자와 실제 형식이 다릅니다",
    "timeout": "읽는 데 시간이 너무 걸렸습니다",
    "dup": "같은 내용의 문서가 이미 있습니다",
    "scan": "글자층이 없는 스캔본입니다",
    "env": "읽기 도구에 문제가 있었습니다",
    "notext": "본문 글자가 없습니다",
    "other": "읽는 중 문제가 생겼습니다",
}
HWP_EXT = (".hwp", ".hwpx")
QUEUE_KEY = "read_queue"              # {doc_id: {at, by, tries, gave_up}}
ARCHIVE_KEY = "archive_retry"         # {rel: {at, by, mode}}
MAX_TRIES = 3


def classify(error: str, state: str = "") -> str:
    """오류 글 → 사유 갈래. 처리기는 읽었지만 글이 없는 문서를 오류 글 없이 state 「empty」로만 적기도 한다 — 그것도 글 없는 문서."""
    e = error or ""
    for key, _label, needles in REASONS:
        if any(n in e for n in needles):
            return key
    if state == "empty":
        return "notext"
    return OTHER[0]


def reason_label(error: str, state: str = "") -> str:
    return LABEL[classify(error, state)]


# ---------------------------------------------------------------- 대기열 장부(설정 표의 JSON 하나)

_lock = threading.Lock()


def _load(db, key: str) -> dict:
    try:
        v = json.loads(db.get_setting(key, "{}") or "{}")
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def _save(db, key: str, v: dict) -> None:
    db.set_setting(key, json.dumps(v, ensure_ascii=False))


def queued(db) -> dict:
    return _load(db, QUEUE_KEY)


def queue_ocr(db, doc_id: int, by: str = "") -> bool:
    """글자 읽기 대기열에 넣는다 — 이미 있으면 그대로(포기한 것은 다시 맡기면 횟수를 새로). 넣었으면 True."""
    with _lock:
        q = _load(db, QUEUE_KEY)
        cur = q.get(str(doc_id))
        if cur and not cur.get("gave_up"):
            return False
        q[str(doc_id)] = {"at": time.strftime("%Y-%m-%d %H:%M"), "by": by, "tries": 0}
        _save(db, QUEUE_KEY, q)
    _kick(db)
    return True


def dequeue(db, doc_id: int) -> None:
    with _lock:
        q = _load(db, QUEUE_KEY)
        if q.pop(str(doc_id), None) is not None:
            _save(db, QUEUE_KEY, q)


def after_failure(db, doc_id: int, error: str) -> None:
    """처리기가 실패를 적은 뒤 — 스캔본(판독 없음)은 글자 읽기 대기열로 저절로(사람이 올린 문서만, DGX 원본은 동기화가 맡는다).
    대기열에서 다시 읽다 실패한 것은 횟수만 센다(drain)."""
    if classify(error) != "scan":
        return
    doc = db.get_document(doc_id) or {}
    if str(doc.get("stored_path") or "").startswith("dgx://"):
        return
    if str(doc_id) in queued(db):
        return
    queue_ocr(db, doc_id, by="자동")


def ocr_ready() -> bool:
    """지금 스캔본을 읽을 수 있는가 — 판독 모델이 닿거나 이 서버에서 구조 추출(GPU)을 돌릴 수 있을 때."""
    from zzaimy.app import pipeline
    try:
        return bool(pipeline._vision_available() or pipeline._mineru_structure_ok())
    except Exception:
        return False


def drain(db, processor, ready=None, limit: int = 1) -> list[int]:
    """대기열에서 한 건씩 다시 읽는다 — 준비가 안 됐으면(ready, 기본 ocr_ready) 아무것도 하지 않는다. 다시 읽은 문서 번호."""
    q = queued(db)
    todo = [k for k, v in sorted(q.items(), key=lambda kv: kv[1].get("at", "")) if not v.get("gave_up")][:limit]
    if not todo or not (ready or ocr_ready)():
        return []
    done = []
    for k in todo:
        doc = db.get_document(int(k))
        path = Path(str((doc or {}).get("stored_path") or ""))
        if not doc or doc.get("status") != "failed" or not path.is_file():
            dequeue(db, int(k))
            continue
        try:
            processor.process(db, int(k), path)
        except Exception:
            log.exception("글자 읽기 대기열 처리 실패 doc=%s", k)
        doc = db.get_document(int(k)) or {}
        with _lock:
            q = _load(db, QUEUE_KEY)
            ent = q.get(k)
            if ent is not None:
                if doc.get("status") != "failed" or classify(doc.get("error") or "") != "scan":
                    q.pop(k)                                  # 읽었거나 다른 사유가 됐다 — 대기열에서 뺀다
                else:
                    ent["tries"] = int(ent.get("tries") or 0) + 1
                    if ent["tries"] >= MAX_TRIES:
                        ent["gave_up"] = True
                _save(db, QUEUE_KEY, q)
        done.append(int(k))
    return done


_workers: dict[str, threading.Thread] = {}
_procs: dict[str, object] = {}


def start_worker(db, processor) -> None:
    """앱마다 처리기를 기억해 두고, 대기열에 일이 있으면 작업 줄을 띄운다(없으면 띄우지 않는다)."""
    _procs[str(db.path)] = processor
    if any(not v.get("gave_up") for v in queued(db).values()):
        _kick(db)


def _kick(db) -> None:
    key = str(db.path)
    proc = _procs.get(key)
    if proc is None or os.environ.get("ZZAIMY_READ_QUEUE", "1") == "0":
        return
    t = _workers.get(key)
    if t is not None and t.is_alive():
        return
    every = float(os.environ.get("ZZAIMY_READ_QUEUE_S", "60"))

    def loop():
        while True:
            try:
                if not any(not v.get("gave_up") for v in queued(db).values()):
                    return                                    # 할 일이 없으면 끝 — 다음에 넣을 때 다시 띄운다
                drain(db, proc)
            except Exception:
                log.exception("글자 읽기 대기열")
                return
            time.sleep(every)

    t = threading.Thread(target=loop, name="read-queue", daemon=True)
    _workers[key] = t
    t.start()


# ---------------------------------------------------------------- 화면 — 사유와 단추

def info(db, doc: dict | None) -> dict | None:
    """실패한 문서의 {code, label, hint, detail, queued, gave_up, actions:[{do, label}], twin} — 실패가 아니면 None.
    actions 의 첫째가 가장 먼저 권하는 길이다. do: replace·ocr·resave·retry·discard·twin."""
    if not doc or doc.get("status") != "failed":
        return None
    if db is not None and ("error" not in doc or "stored_path" not in doc):
        doc = db.get_document(int(doc["id"])) or doc       # 목록 줄은 열을 줄여 가져온다 — 사유·원본 자리는 문서에서
    err = str(doc.get("error") or "")
    code = classify(err)
    dgx = str(doc.get("stored_path") or "").startswith("dgx://")
    from zzaimy.app import storage
    ext = storage.original_ext(doc)
    ent = queued(db).get(str(doc["id"])) if db is not None else None
    waiting = bool(ent) and not ent.get("gave_up")
    gave_up = bool(ent) and bool(ent.get("gave_up"))
    acts: list[tuple[str, str]] = []
    replace = ("replace", "암호를 푼 파일로 바꾸기" if code == "locked" else "다른 파일로 바꾸기")
    resave = ("resave", "기안기로 열어 다시 저장") if ext in HWP_EXT else None
    if dgx:
        acts = [("retry", "다시 읽기")]                     # 원본 보관소 문서 — 파일은 보관소 것이라 바꾸거나 지우지 않는다
    elif code == "locked" or code == "empty" or code == "notext":
        acts = [replace]
    elif code == "scan":
        acts = ([] if waiting else [("ocr", "글자 읽기 맡기기")]) + [replace]
    elif code == "mismatch":
        acts = [replace] + ([resave] if resave else [])
    elif code == "dup":
        acts = [("twin", "같은 문서 열기")]
    else:
        acts = [("retry", "다시 읽기")] + ([resave] if resave else []) + [replace]
    if not dgx:
        acts.append(("discard", "지우기"))
    twin = None
    if code == "dup":
        import re
        m = re.search(r"#(\d+)", err)
        twin = int(m.group(1)) if m else None
        if twin is None:
            acts = [a for a in acts if a[0] != "twin"]
    hint = HINT[code]
    if waiting:
        hint = "글자 읽기 대기 중 — 판독 서버가 닿으면 읽습니다"
    elif gave_up:
        hint = f"글자 읽기를 {MAX_TRIES}번 맡겼지만 읽지 못했습니다"
    return {"code": code, "label": LABEL[code], "hint": hint, "detail": err[:300], "queued": waiting, "gave_up": gave_up,
            "actions": [{"do": d, "label": lab} for d, lab in acts], "twin": twin}


def replace_file(db, doc: dict, name: str, src, base: Path | None = None) -> Path:
    """같은 문서 자리에 파일을 바꿔 넣는다 — 옛 원본은 지우고 새 파일을 문서 폴더의 원본으로, 이름은 새 파일 이름으로.
    src 는 읽을 수 있는 파일 객체. 새 경로를 돌려준다(처리는 호출부가)."""
    import shutil
    import uuid
    from zzaimy.app import storage

    base = Path(base) if base is not None else Path(db.path).parent
    old = Path(str(doc.get("stored_path") or ""))
    tmp = base / f".replace-{uuid.uuid4().hex}{Path(name).suffix.lower()}"
    with tmp.open("wb") as out:
        shutil.copyfileobj(src, out)
    with db._conn() as conn:
        conn.execute("UPDATE documents SET filename = ?, error = NULL WHERE id = ?", (name, int(doc["id"])))
    if old.is_file() and old.resolve() != tmp.resolve():
        old.unlink(missing_ok=True)
        db.remove_files(path=str(old))
    dequeue(db, int(doc["id"]))
    return storage.adopt_original(db, int(doc["id"]), tmp, base)


def resave_hwp(path: Path) -> Path:
    """한글 파일을 기안기 서버로 열어 hwpx 로 다시 저장한다(open → save) — 깨진 판·옛 판도 한글이 열면 새 파일로 나온다.
    같은 폴더에 「<이름>.hwpx」로 쓰고 그 경로를 돌려준다. 기안기가 열지 못하면 RuntimeError."""
    from zzaimy.app import webhwp, webhwp_convert

    if not webhwp.status().get("ok"):
        raise RuntimeError("기안기 서버에 닿지 않습니다")
    src = "HWPX" if path.read_bytes()[:2] == b"PK" else "HWP"
    out = webhwp_convert.convert(path.read_bytes(), src, "HWPX", "x.hwpx")
    if out[:2] != b"PK":
        raise RuntimeError("기안기가 hwpx 로 저장하지 못했습니다")
    dest = path.with_name(path.stem + ".resaved.hwpx")
    dest.write_bytes(out)
    return dest


# ---------------------------------------------------------------- 원본 보관소 — 정리함 「처리 실패」 다시 시도

def archive_pending(db) -> dict:
    return _load(db, ARCHIVE_KEY)


def archive_retry(db, rels: list[str], mode: str = "auto", by: str = "") -> int:
    """정리함 「처리 실패」의 원본을 정리함에서 빼고 다시 읽기 장부에 적는다 — 다음 동기화(217)가 읽는다. 적은 수."""
    from zzaimy.app import archive_cleanup

    from zzaimy.app import archive

    rels = [r for r in dict.fromkeys(rels) if r][:500]
    if not rels:
        return 0
    archive.ensure(db)
    with db._conn() as conn:
        have = {r[0]: r[1] or "" for r in conn.execute(
            "SELECT c.rel, f.analysis FROM archive_cleanup c LEFT JOIN archive_files f ON f.rel = c.rel"
            f" WHERE c.category = 'failed' AND c.rel IN ({','.join('?' * len(rels))})", rels).fetchall()}
        rels = [r for r in rels if r in have]
        for r in rels:
            conn.execute("DELETE FROM archive_cleanup WHERE rel = ?", (r,))
    with _lock:
        led = _load(db, ARCHIVE_KEY)
        now = time.strftime("%Y-%m-%d %H:%M")
        for r in rels:
            # auto — 스캔 판독 없음은 글자 읽기(판독이 닿을 때), 그 밖은 바로 다시 읽기
            m = mode if mode in ("ocr", "retry") else ("ocr" if classify(have[r]) == "scan" else "retry")
            led[r] = {"at": now, "by": by, "mode": m}
        _save(db, ARCHIVE_KEY, led)
    archive_cleanup._cache.update(v=None)
    if rels:
        try:                                                  # 동기화(zz_sync)가 한 시간을 기다리지 않고 다음 1분에 돌게
            (Path(db.path).parent / ".archive-retry").touch()
        except OSError:
            pass
    return len(rels)


def archive_failed(db, q: str = "", page: int = 1, per: int = 20) -> dict:
    """정리함 「처리 실패」 원본 목록 — 사유(원본 장부의 실패 표시)와 함께, 찾기·쪽 나눔. {rows, total, page, pages, pending, by_reason}."""
    from zzaimy.app import archive, archive_state

    archive.ensure(db)
    like = f"%{q.strip()}%" if q.strip() else "%"
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT c.rel AS rel, c.at AS at, COALESCE(f.analysis, '') AS analysis, COALESCE(f.ext, '') AS ext FROM archive_cleanup c"
            " LEFT JOIN archive_files f ON f.rel = c.rel WHERE c.category = 'failed' AND c.rel LIKE ? ORDER BY c.rel", (like,)).fetchall()]
    by: dict[str, int] = {}
    for r in rows:
        why = r["analysis"][len(archive_state.FAILED_MARK) + 2:] if r["analysis"].startswith(archive_state.FAILED_MARK) else r["analysis"]
        r["code"] = classify(why)
        r["reason"] = LABEL[r["code"]]
        r["name"] = r["rel"].rsplit("/", 1)[-1]
        r["folder"] = r["rel"].rsplit("/", 1)[0] if "/" in r["rel"] else ""
        by[r["reason"]] = by.get(r["reason"], 0) + 1
        del r["analysis"]
    pages = max(1, -(-len(rows) // per))
    page = min(max(1, page), pages)
    return {"rows": rows[(page - 1) * per: page * per], "total": len(rows), "page": page, "pages": pages,
            "pending": len(archive_pending(db)), "by_reason": by}


def run_archive_retry(db, processor, fetch=None, ready=None, limit: int = 20, data_dir: Path | None = None) -> dict:
    """다시 읽기 장부의 원본을 받아 처리기로 읽는다 — 읽으면 문서함에(168 과 같은 자리: 사업 보관 묶음·dgx:// 원본),
    못 읽으면 실패 장부에 새 사유를 적고 장부에서 뺀다(다음 정리에서 정리함으로 돌아간다). 글자 읽기(ocr)는 판독이 닿을 때만."""
    import shutil
    import tempfile
    from zzaimy.app import archive, archive_state, rag_spaces

    if fetch is None:
        from zzaimy.app.archive_original import fetch
    data_dir = Path(data_dir) if data_dir is not None else Path(db.path).parent
    led = archive_pending(db)
    out = {"read": 0, "failed": 0, "waiting": 0}
    can_ocr = None
    for rel, ent in list(led.items())[:limit]:
        if ent.get("mode") == "ocr":
            can_ocr = (ready or ocr_ready)() if can_ocr is None else can_ocr
            if not can_ocr:
                out["waiting"] += 1
                continue
        with db._conn() as conn:
            row = conn.execute("SELECT rel, size, mtime, program, program_name, year, round, doc_id, removed_at FROM archive_files"
                               " WHERE rel = ?", (rel,)).fetchone()
        row = dict(zip(("rel", "size", "mtime", "program", "program_name", "year", "round", "doc_id", "removed_at"), row)) if row else None
        ver = f"{int((row or {}).get('size') or 0)}:{int(float((row or {}).get('mtime') or 0))}"
        err = ""
        did = None
        made = False
        tmpdir = Path(tempfile.mkdtemp(prefix="zz-retry-"))
        try:
            if not row or row["removed_at"]:
                raise LookupError("원본이 보관소에서 없어졌습니다")
            got = Path(fetch(row))
            path = tmpdir / Path(rel).name
            shutil.move(str(got), str(path))
            did = row["doc_id"]
            if not did:
                dept = rag_spaces.dept_of_rel(rel, data_dir)
                did = db.add_document(filename=Path(rel).name, stored_path=str(path), doc_type="grant", sector="grant", dept=dept,
                                      access_level="public" if dept == "공통" else "dept", owner="zzdev",
                                      project_id=archive.program_project(db, row["program"] or "", row["program_name"] or "", None,
                                                                         row["year"], row["round"]))
                made = True
            processor.process(db, int(did), path)
            doc = db.get_document(int(did)) or {}
            if doc.get("status") == "failed":
                err = str(doc.get("error") or "읽지 못함")
            else:
                db.set_stored_path(int(did), f"dgx://{rel}")
                with db._conn() as conn:
                    conn.execute("UPDATE archive_files SET doc_id = ?, analysis = ? WHERE rel = ?", (int(did), "다시 시도로 읽음", rel))
                with (data_dir / "origins.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"doc_id": int(did), "origin": rel, "version": ver, "state": "parsed",
                                         "at": time.strftime("%Y-%m-%d %H:%M"), "via": "retry"}, ensure_ascii=False) + "\n")
                out["read"] += 1
        except LookupError as e:
            err = str(e)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        if err:
            out["failed"] += 1
            if made and did:
                db.delete_document(int(did))
            with (data_dir / "parse_failures.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"rel": rel, "version": ver, "state": "failed", "error": err[:200],
                                     "at": time.strftime("%Y-%m-%d %H:%M"), "via": "retry"}, ensure_ascii=False) + "\n")
            try:
                archive_state.mark_failures(db, {rel: err})
            except Exception:
                pass
        with _lock:
            cur = _load(db, ARCHIVE_KEY)
            cur.pop(rel, None)
            _save(db, ARCHIVE_KEY, cur)
    return out
