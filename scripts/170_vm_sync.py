#!/usr/bin/env python3
"""VM 혼자 도는 원본 갱신 — DGX 원본 보관소를 읽기 전용 rsync 로 보고(파일 내용은 고른 것만), 원본 목록 장부·사업 분류·뼈대 반입·그래프·양식을 갱신한다.

사용자 2026-10-02: "계속 업데이트 해야지.. 새로 받아오는중인데", "지금 계정으로는 안되는거야?" — DGX aura 계정에 VM 전용 키를
「rrsync -ro ~/data」로 묶어 등록했다(명령 실행·쓰기 불가, 확인함). 그래서 운영 PC 없이 VM 의 cron 으로 돈다.

순서: ① rsync --list-only 로 경로·크기·시각 ② 원본 목록 장부(app/archive) ③ 사업 분류·뼈대 고르기(scripts/161 규칙)
      ④ 아직 안 들인 뼈대만 rsync 로 받아 받은 편지함에 ⑤ 164(반입·그래프 --full·양식·docx)
겹쳐 돌지 않게 cron 에서 flock 으로 감싼다.

사용(VM): env PYTHONPATH=src .venv/bin/python scripts/170_vm_sync.py [--max 200] [--dry]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import archive  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402
from zzaimy.graph import programs  # noqa: E402

KEY = str(Path.home() / ".ssh" / "id_ed25519_dgx_ro")
REMOTE = "aura@211.170.162.110:"
RSH = f"ssh -i {KEY} -p 8022 -o BatchMode=yes"
_LINE = re.compile(r"^-\S+\s+([\d,]+)\s+(\d{4}/\d{2}/\d{2})\s+(\d{2}:\d{2}:\d{2})\s+(.+)$")
PRI = {"evaluation": 0, "plan": 1, "report": 2, "basic_plan": 3, "announcement": 4, "criteria": 5, "guideline": 6}


def _sel():
    spec = importlib.util.spec_from_file_location("select_core", ROOT / "scripts" / "161_select_core.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def listing() -> list[dict]:
    out = subprocess.run(["rsync", "-e", RSH, "-r", "--list-only", REMOTE + "./"], capture_output=True, check=True).stdout.decode("utf-8", "replace")
    files = []
    for line in out.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        rel = m.group(4)
        name = rel.rsplit("/", 1)[-1]
        if name.startswith(".") or name.startswith("~$"):
            continue
        mt = int(time.mktime(time.strptime(m.group(2) + " " + m.group(3), "%Y/%m/%d %H:%M:%S")))
        files.append({"rel": rel, "filename": name, "size": int(m.group(1).replace(",", "")), "mtime": mt,
                      "ext": name.rsplit(".", 1)[-1].lower() if "." in name else "", "area": rel.split("/", 1)[0],
                      "path": rel.rsplit("/", 1)[0] if "/" in rel else ""})
    return files


UPKEY = str(Path.home() / ".ssh" / "id_ed25519_dgx_up")
UPRSH = f"ssh -i {UPKEY} -p 8022 -o BatchMode=yes"


def push_uploads(db, origins: dict[str, int], cards=None) -> None:
    """플랫폼(문서함·에이전트 채팅)에 올라온 원본을 DGX ~/zzaimy_uploads 로 올리고 원본 목록 장부에 「플랫폼 업로드」로 넣는다 —
    들어온 길이 달라도 원본은 한 보관소·한 장부에(사용자 2026-10-02: "제각각이면 안되거든"). DGX 쪽 키는 그 폴더 쓰기 전용."""
    docs_root = ROOT / "data" / "platform" / "documents"
    from_dgx = set(origins.values())
    pushed_path = ROOT / "data" / "platform" / "uploads_pushed.jsonl"
    pushed = set()
    if pushed_path.is_file():
        for line in pushed_path.read_text(encoding="utf-8").splitlines():
            try:
                pushed.add(int(json.loads(line)["doc_id"]))
            except (ValueError, KeyError):
                continue
    with db._conn() as conn:
        rows = conn.execute("SELECT id, filename, stored_path, kind FROM documents WHERE status != 'failed'").fetchall()
    todo = []
    for did, name, sp, kind in rows:
        did = int(did)
        if did in from_dgx or did in pushed or not sp or sp.startswith("dgx://"):
            continue
        f = Path(sp)
        if not f.is_file():
            continue
        try:
            rel = str(f.relative_to(docs_root))
        except ValueError:
            continue
        todo.append((did, name, rel, f, kind or ""))
    if not todo:
        return
    lst = docs_root / ".push-from"
    lst.write_text("\n".join(t[2] for t in todo) + "\n", encoding="utf-8")
    r = subprocess.run(["rsync", "-e", UPRSH, "-a", "--files-from", str(lst), str(docs_root) + "/", "aura@211.170.162.110:./"],
                       capture_output=True)
    if r.returncode != 0:
        print("업로드 원본 DGX 올리기 실패:", r.stderr.decode("utf-8", "replace")[-200:], flush=True)
        return
    stamp = time.strftime("%Y-%m-%d %H:%M")
    # 반입 원본과 같은 분류 — 같은 사업 카드·같은 분류기에 파일 이름·접수 폴더 이름·문서 앞머리(조각)를 넣는다
    cls = {}
    if cards:
        pdocs = []
        for t in todo:
            head = "\n".join(str(c["content"]) for c in db.list_doc_chunks(t[0])[:30])
            pdocs.append({"id": t[0], "filename": t[1], "path": str(Path(t[2]).parent), "head": head})
        cls = {a.doc_id: a for a in programs.classify(pdocs, cards)}
    rows_ar = []
    for t in todo:
        a = cls.get(t[0])
        r = {"rel": archive.UPLOAD_PREFIX + t[2], "size": t[3].stat().st_size, "mtime": int(t[3].stat().st_mtime),
             "ext": t[3].suffix.lower().lstrip("."), "area": "플랫폼 업로드", "kind": t[4]}
        if a is not None:
            r.update({"program": a.program, "program_name": a.program_name, "status": a.status, "kind": a.kind or t[4],
                      "year": a.year, "round": a.round})
        rows_ar.append(r)
    archive.load(db, rows_ar, {archive.UPLOAD_PREFIX + t[2]: t[0] for t in todo})
    with pushed_path.open("a", encoding="utf-8") as fh:
        for t in todo:
            fh.write(json.dumps({"doc_id": t[0], "rel": t[2], "at": stamp}, ensure_ascii=False) + "\n")
    print(f"플랫폼 업로드 원본 {len(todo)}건 → DGX ~/zzaimy_uploads·원본 장부", flush=True)


PKEY = str(Path.home() / ".ssh" / "id_ed25519_dgx_parsed")


def import_parsed() -> None:
    """DGX 가 가볍게 처리한 원본(167 → ~/parsed, 읽기 전용 키)을 받아 'DGX 보관 문서'로 들인다(168, 이미 들인 원본은 건너뜀).

    5분 주기(--parsed)와 10분 주기 전체 동기화가 둘 다 부른다 — 들이기 전용 잠금으로 한 번에 하나만(2026-10-02 실측:
    둘이 같은 결과 파일을 동시에 읽어 같은 원본을 두 번 들였다, 3,507건)."""
    import fcntl
    lock = open("/tmp/zz_parsed_import.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("DGX 처리 결과 들이기: 다른 회차가 들이는 중 — 건너뜀", flush=True)
        return
    dest = ROOT / "data" / "inbox" / "parsed"
    dest.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["rsync", "-e", f"ssh -i {PKEY} -p 8022 -o BatchMode=yes", "-a", "--include=parsed-*.jsonl", "--exclude=*",
                        "aura@211.170.162.110:./", str(dest) + "/"], capture_output=True)
    if r.returncode != 0:
        print("DGX 처리 결과 받기 실패:", r.stderr.decode("utf-8", "replace")[-160:], flush=True)
        return
    files = sorted(str(f) for f in dest.glob("parsed-*.jsonl"))
    if not files:
        return
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "168_import_parsed.py"), *files], cwd=ROOT, capture_output=True)
    last = (out.stdout.decode("utf-8", "replace").strip().splitlines() or ["출력 없음"])[-1]
    print("DGX 처리 결과:", last, flush=True)
    mark("parsed", last)
    if out.returncode != 0:
        print(out.stderr.decode("utf-8", "replace")[-400:], flush=True)
    m = re.match(r"들임 (\d+) · 새 판 갱신 (\d+)", last) or re.match(r"들임 (\d+)()", last)
    if m and (int(m.group(1)) or int(m.group(2) or 0)):
        (ROOT / "data" / "platform" / ".kg-dirty").touch()   # 1분 주기가 그래프·색인을 맞춘다


def mark(kind: str, summary: str) -> None:
    """주기마다 마지막 실행 시각·결과 한 줄 — 개발 현황의 반입 현황 카드가 읽는다(app/intake_status)."""
    path = ROOT / "data" / "platform" / "sync_status.json"
    try:
        cur = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        cur = {}
    cur[kind] = {"at": time.strftime("%Y-%m-%d %H:%M"), "summary": summary[:200]}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


class _IndexBusy(Exception):
    pass


def index_catchup(db, budget_s: int = 50 * 60) -> int:
    """사업 문서 색인을 밀린 만큼 따라잡는다(--index, 자기 잠금). 그래프 재구축이 몇십 분 걸려도 색인은 따로 는다."""
    import fcntl
    from zzaimy.app import grant_search
    lock = open("/tmp/zz_index.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0
    t0 = time.time()
    while time.time() - t0 < budget_s:
        t1 = time.time()
        try:
            got = grant_search.build_increment(db, limit=30000)
        except Exception as e:
            print("사업 문서 색인 갱신 실패:", type(e).__name__, str(e)[:120], flush=True)
            return 1
        if got["added"] or got["removed"]:
            print(f"사업 문서 색인: 더함 {got['added']} · 뺌 {got['removed']} · 전체 {got['total']} · 남음 {got['pending']}"
                  f" · {time.time() - t1:.0f}초", flush=True)
        mark("index", f"전체 {got['total']} · 남음 {got['pending']}")
        if not got["pending"]:
            break
    return 0


def post_if_changed(db) -> int:
    """문서함이 바뀌었으면(어느 길로 들어왔든 — DGX 동기화·문서함 업로드·채팅 첨부) 그래프·양식·색인을 다시 짓는다.

    후속 잠금(/tmp/zz_post.lock)은 반입 잠금과 따로다 — 몇 시간 걸리는 반입 중에도 1분 주기가 그래프·색인을 따라 맞춘다."""
    import fcntl
    lockf = open("/tmp/zz_post.lock", "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        (Path(db.path).parent / ".kg-dirty").touch()     # 다른 후속 처리가 돌고 있다 — 다음 회차에
        return 0
    with db._conn() as conn:
        n, hi = conn.execute("SELECT COUNT(*), MAX(id) FROM documents WHERE status = 'reviewed'").fetchone()
        nc = conn.execute("SELECT COUNT(*) FROM doc_chunks").fetchone()[0]
    now = {"docs": int(n or 0), "max_id": int(hi or 0), "chunks": int(nc or 0)}
    mpath = ROOT / "data" / "platform" / "kg_marker.json"
    old = json.loads(mpath.read_text(encoding="utf-8")) if mpath.is_file() else {}
    # 사업 문서 색인은 매번(새 조각만) — 색인 전용 주기(--index)가 돌고 있으면 그쪽에 맡긴다(npz 를 둘이 동시에 쓰지 않게)
    try:
        from zzaimy.app import grant_search
        idx_lock = open("/tmp/zz_index.lock", "w")
        try:
            fcntl.flock(idx_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise _IndexBusy()
        got = grant_search.build_increment(db)
        fcntl.flock(idx_lock, fcntl.LOCK_UN)
        if got["added"] or got["removed"] or got["pending"]:
            print(f"사업 문서 색인: 더함 {got['added']} · 뺌 {got['removed']} · 전체 {got['total']} · 남음 {got['pending']}", flush=True)
        if got["pending"]:
            (Path(db.path).parent / ".kg-dirty").touch()
    except _IndexBusy:
        pass
    except Exception as e:
        print("사업 문서 색인 갱신 실패:", type(e).__name__, str(e)[:120], flush=True)
        (Path(db.path).parent / ".kg-dirty").touch()     # 임베딩 서비스가 돌아오면 다음 회차에 다시
    if {k: v for k, v in old.items() if k != "at"} == now:
        return 0
    # 그래프 전체 재구축은 15분에 한 번까지 — 1분마다 다시 지으면 쉬지 않고 돈다. 그 사이 바뀐 것은 다음 회차에 모아서
    if time.time() - float(old.get("at", 0)) < 15 * 60:
        (Path(db.path).parent / ".kg-dirty").touch()
        return 0
    now["at"] = time.time()
    print(f"문서함 바뀜 {old} → {now} — 그래프·양식 다시 짓기", flush=True)
    # 164 가 같은 후속 잠금을 스스로 잡는다(기다리며) — 넘기기 전에 놓아야 부모·자식이 서로 막지 않는다
    fcntl.flock(lockf, fcntl.LOCK_UN)
    lockf.close()
    rc = subprocess.call([sys.executable, str(ROOT / "scripts" / "164_sync_apply.py"), "--post-only"], cwd=ROOT)
    if rc == 0:
        mpath.write_text(json.dumps(now), encoding="utf-8")
        mark("graph", f"문서 {now['docs']} · 조각 {now['chunks']}")
    else:
        print(f"그래프·양식 다시 짓기 실패(종료 코드 {rc}) — 다음 회차에 다시", flush=True)
        (Path(db.path).parent / ".kg-dirty").touch()
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=200)
    ap.add_argument("--min-files", type=int, default=10)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--ledger-only", action="store_true", help="목록·분류·연도 보정·원본 장부·보관 묶음 맞춤까지만(반입 없음) — 분류 규칙을 고친 뒤 바로 반영할 때")
    ap.add_argument("--index", action="store_true", help="사업 문서 색인만 밀린 만큼 따라잡는다(자기 잠금, 그래프 재구축과 따로)")
    ap.add_argument("--parsed", action="store_true", help="DGX 가벼운 처리 결과만 받아 들인다(5분 주기, 긴 반입과 따로)")
    ap.add_argument("--quick", action="store_true", help="DGX 훑기 없이 업로드 원본 올리기·그래프·색인만(문서함이 바뀌었을 때 1분 주기)")
    args = ap.parse_args()
    t0 = time.time()
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    if args.parsed:
        import_parsed()
        return 0
    if args.index:
        return index_catchup(db)
    if args.quick:
        dirty = Path(db.path).parent / ".kg-dirty"
        if not dirty.exists():
            return 0
        dirty.unlink()
        origins = {}
        led = ROOT / "data" / "platform" / "origins.jsonl"
        if led.is_file():
            for line in led.read_text(encoding="utf-8").splitlines():
                try:
                    o = json.loads(line)
                    origins[o["origin"]] = int(o["doc_id"])
                except (ValueError, KeyError):
                    continue
        push_uploads(db, origins, None)
        return post_if_changed(db)
    files = listing()
    first: dict = {}
    for i, f in enumerate(files):
        f["id"] = i
        f["head"] = ""
        key = (f["filename"], f["size"])
        f["dup_of"] = first.get(key, "")
        first.setdefault(key, f["rel"])
    docs = [f for f in files if not f["dup_of"]]
    cards = programs.build_cards(docs)
    res = {a.doc_id: a for a in programs.classify(docs, cards)}
    programs.inherit_by_folder(docs, [res[d["id"]] for d in docs])
    # 규칙이 확정하지 못한 파일은 폴더 검토 장부(에이전트·사람 판정)로 — 그래프(157)와 같은 장부
    reviews = programs.load_reviews(ROOT / "data" / "platform" / "class_review.jsonl")
    n_rev = programs.apply_reviews(docs, [res[d["id"]] for d in docs], reviews, cards)
    if n_rev:
        print(f"폴더 검토 판정 반영 {n_rev}건(장부 {len(reviews)}줄)", flush=True)
    # 연차·연도: 폴더 경로로 채우고, 외부 확인 장부의 사업 기간으로 환산, 기간 밖이면 확정하지 않음(보관 묶음이 사업 × 연도라서)
    ext = ROOT / "data" / "platform" / "kg_external.json"
    ledger = json.loads(ext.read_text(encoding="utf-8")) if ext.is_file() else {}
    link = programs.ledger_link(cards, ledger)
    st = programs.fill_period(docs, [res[d["id"]] for d in docs], link["periods"], link["spans"], link["aliases"],
                              {c.node_id: c.name for c in cards})
    print(f"연차·연도 보정 {st}(사업 기간 {len(link['periods'])}개 · 같은 사업 합침 {link['aliases']})", flush=True)
    by_rel = {d["rel"]: res[d["id"]] for d in docs}
    rows = []
    for f in files:
        a = by_rel.get(f["dup_of"] or f["rel"])
        r = {k: f[k] for k in ("rel", "size", "mtime", "ext", "area", "dup_of")}
        if a is not None:
            r.update({"program": a.program, "program_name": a.program_name, "status": a.status, "kind": a.kind, "year": a.year, "round": a.round})
        rows.append(r)
    origins = {}
    led = ROOT / "data" / "platform" / "origins.jsonl"
    if led.is_file():
        for line in led.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(line)
                origins[o["origin"]] = int(o["doc_id"])
            except (ValueError, KeyError):
                continue
    diff = {"added": [], "changed": [], "moved": [], "removed": [], "reclassified": 0}
    if not args.dry:
        diff = archive.sync(db, rows, origins)
        stamp = time.strftime("%Y-%m-%d %H:%M")
        with led.open("a", encoding="utf-8") as fh:
            for old, new, did in diff["moved"]:          # 옮긴 원본 — 문서 번호를 새 경로로 잇는다
                if did:
                    fh.write(json.dumps({"doc_id": did, "origin": new, "at": stamp, "moved_from": old}, ensure_ascii=False) + "\n")
                    origins[new] = did
        for rel, did in diff["removed"]:                   # 없어진 원본 — 문서는 두고 표시만
            if did:
                d = db.get_document(did) or {}
                note = (d.get("parse_note") or "")
                if "원본 없음" not in note:
                    db.update_document(did, parse_note=(note + f" · 원본 없음(DGX 에서 {stamp} 사라짐: {rel})").strip(" ·"))
    if not args.dry:
        got = archive.align_archived_projects(db)         # 과거 사업 보관 묶음 — 분류가 고쳐지면 따라간다
        if got["moved"] or got["removed_projects"]:
            print(f"과거 사업 보관 묶음 맞춤: 옮김 {got['moved']} · 빈 묶음 지움 {got['removed_projects']}", flush=True)
    print(f"원본 {len(files)} · 중복 제외 {len(docs)} · 사업 카드 {len(cards)} · 목록 {time.time() - t0:.0f}초 — "
          f"새로 {len(diff['added'])} · 바뀜 {len(diff['changed'])} · 옮김 {len(diff['moved'])} · 없어짐 {len(diff['removed'])} · "
          f"분류 바뀜 {diff['reclassified']}", flush=True)
    if not args.dry:
        mark("full", f"원본 {len(files)} · 새로 {len(diff['added'])} · 바뀜 {len(diff['changed'])} · 옮김 {len(diff['moved'])} · 없어짐 {len(diff['removed'])}")
    if args.ledger_only:
        return 0
    # 뼈대 고르기 — 161 과 같은 규칙(사업마다 갈래·연차·줄기별 최신판)
    sel = _sel()
    per_prog = defaultdict(list)
    for d in docs:
        a = by_rel[d["rel"]]
        if a.program:
            per_prog[a.program].append((d, a))
    chosen = []
    for pid, items in per_prog.items():
        if len(items) < args.min_files:
            continue
        card = next((c for c in cards if c.node_id == pid), None)
        surfaces = card.surfaces() if card else set()
        groups = defaultdict(list)
        for d, a in items:
            ext = d["ext"]
            if a.kind not in sel.CORE or ext not in sel.DOC_RANK or sel.EVIDENCE.search(d["rel"]) or not sel.is_core(d, a.kind, surfaces):
                continue
            when = a.round or a.year or (programs._PATH_YEAR.findall(d["path"]) or [""])[-1]
            groups[(a.kind, str(when), sel.stem(d["filename"]))].append((d, a))
        for cands in groups.values():
            best = max(cands, key=lambda t: (sel.latest_key(t[0]), -sel.DOC_RANK[t[0]["ext"]]))
            chosen.append({"program": pid, "name": best[1].program_name, "kind": best[1].kind, "rel": best[0]["rel"]})
    new = [c for c in chosen if c["rel"] not in origins]
    # 내용이 바뀐 원본 중 문서함에 있는 것 — 같은 문서 번호로 다시 처리(164 의 update 목록)
    updates = [{"rel": rel, "doc_id": did} for rel, did in diff["changed"] if did]
    new.sort(key=lambda c: (PRI.get(c["kind"], 9), c["program"], c["rel"]))
    new = new[: args.max]
    print(f"뼈대 {len(chosen)} · 새로 {len(new)}(최대 {args.max})", flush=True)
    if not args.dry:
        push_uploads(db, origins, cards)
        import_parsed()
    if args.dry:
        return 0                                          # 훑어보기만 — 장부·그래프·색인을 쓰지 않는다
    if not (new or updates):
        return post_if_changed(db)
    by_prog = defaultdict(list)
    for c in new:
        by_prog[c["program"]].append(c)
    manifest = []
    for pid, items in by_prog.items():
        inbox = f"data/inbox/core/{pid.split(':', 1)[1]}"
        (ROOT / inbox).mkdir(parents=True, exist_ok=True)
        lst = ROOT / inbox / ".files-from"
        lst.write_text("\n".join(i["rel"] for i in items) + "\n", encoding="utf-8")
        subprocess.run(["rsync", "-e", RSH, "-a", "--files-from", str(lst), REMOTE + "./", str(ROOT / inbox) + "/"], check=False)
        manifest.append({"program": pid, "project": items[0]["name"][:80], "inbox": inbox, "files": [i["rel"] for i in items]})
        print(f"  받음 {items[0]['name'][:30]}: {len(items)}건", flush=True)
    if updates:
        inbox = "data/inbox/core/_updates"
        (ROOT / inbox).mkdir(parents=True, exist_ok=True)
        lst = ROOT / inbox / ".files-from"
        lst.write_text("\n".join(u["rel"] for u in updates) + "\n", encoding="utf-8")
        subprocess.run(["rsync", "-e", RSH, "-a", "--files-from", str(lst), REMOTE + "./", str(ROOT / inbox) + "/"], check=False)
        manifest.append({"program": "", "project": "", "inbox": inbox, "files": [], "updates": updates})
        print(f"  바뀐 원본 다시 처리: {len(updates)}건", flush=True)
    (ROOT / "data" / "inbox" / "core" / "sync_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    rc = subprocess.call([sys.executable, str(ROOT / "scripts" / "164_sync_apply.py")], cwd=ROOT)
    post_if_changed(db)                                  # 164 가 이미 지었으면 표시만 맞춘다
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
