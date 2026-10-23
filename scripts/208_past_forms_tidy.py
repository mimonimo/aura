#!/usr/bin/env python3
"""지난 문서 양식 정리 — 한글 문서함 공통 부서의 지난 문서 양식(scripts/200 register·common 이 둔 것)을 전수 점검해
쓸데없는 것은 휴지통으로, 나머지는 「공통 양식/<목적>」(어디서나 쓰는 빈 양식)과 「지난 사업 문서/<사업>/<목적>」(사업 전용)으로 나눈다.

왜(10-23 사용자): 「양식」 폴더에 1천여 건이 제목 첫 낱말로만 쌓였고, 개인 서류·채워진 제출본·중복이 섞였다. 「공통 양식」 에는
사업 전용 양식(양식:common-)도 섞였다. 없어진 양식은 담당자가 다시 올리므로 미심쩍은 것은 지운다(휴지통이라 되살릴 수 있다).
판정 규칙은 app/past_forms(classify) 하나 — 특정 파일·사업 규칙 없음. 플랫폼이 만든 공통 양식(webhwp_templates)은 건드리지 않는다.

- plan    : 대상 파일마다 사업·목적·옮길 폴더·지울 까닭과 잰 값 → data/platform/webhwp_forms/tidy_plan.json · tidy_plan.md
            (md 의 제목은 사람 이름을 가린다 — 「박○○ 이력서」). 운영 DB·파일은 읽기만 한다
- apply   : --plan 의 지문(경로·sha256)이 지금과 같은지 먼저 보고(다르면 아무것도 하지 않고 멈춘다), 바뀔 줄·설정·파일을 백업한 뒤
            남길 것은 옮기고(장부 files.path·이름, 앞 판(.판), 경로를 담은 설정 값까지) 지울 것은 휴지통(webhwp.trash)으로.
            빈 옛 폴더는 지운다. 다시 돌려도 이미 한 것은 건너뛴다
- restore : --backup 폴더로 되돌린다(옮긴 파일은 제자리로, 지운 파일은 백업에서 다시 놓고 장부·설정 줄을 되살린다)

사용(VM): set -a; . ./.env.local; set +a
          PYTHONPATH=src .venv/bin/python scripts/208_past_forms_tidy.py plan
          PYTHONPATH=src .venv/bin/python scripts/208_past_forms_tidy.py apply --plan data/platform/webhwp_forms/tidy_plan.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

from zzaimy.app import past_forms as pf  # noqa: E402
from zzaimy.app import webhwp  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402

OUT = ROOT_DIR / "data" / "platform" / "webhwp_forms"
TIDY_USER = "정리"


def _db() -> Database:
    return Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT_DIR / "data/platform/platform.db"))


def _base(db) -> Path:
    return Path(os.environ.get("ZZAIMY_DATA_DIR") or Path(db.path).parent)


def _sha(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return ""


# ── plan ──
def _rank(e: dict) -> tuple:
    """같은 묶음에서 남길 것 — 빈 자리(필드·빈 칸·예시 글자) 많은 것 > 지난 문서 양식(양식:doc-) > 최근 > 작은 id."""
    return (pf.blank_slots(e["measure"]), str(e["source"]).startswith("양식:doc-"), e.get("created", ""), -e["fid"])


def build_plan(db, base: Path, programs: pf.Programs | None = None) -> dict:
    programs = programs or pf.Programs(db, base)
    entries: list[dict] = []
    platform_titles: dict[str, str] = {}
    for r in db.list_files(kind="webhwp", limit=200000):
        if webhwp.dept_of_path(base, r["path"]) != webhwp.COMMON:
            continue
        folder = webhwp.folder_of(base, r["path"])
        m = webhwp.meta(db, r["id"])
        src = str(m.get("source", ""))
        if pf.platform_made(src):                         # 플랫폼이 만든 공통 양식 — 남기고, 같은 제목 비교에만 쓴다
            k = pf.norm_title(r["name"])
            if k:
                platform_titles[k] = r["name"]
            continue
        origin = pf.in_scope(folder, src)
        if not origin:
            continue
        p = Path(r["path"])
        title = webhwp.storage.title_of(r["name"])
        e = {"fid": int(r["id"]), "path": str(p), "name": r["name"], "title": title, "source": src, "origin": origin,
             "folder": folder, "doc_id": pf.doc_id_of(src), "created": m.get("created", "") or r.get("created_at", "")}
        if not p.exists():
            e.update(sha="", size=0, action=pf.TRASH, reason_code="missing", reason="디스크에 파일 없음", measure={"ok": False},
                     purpose=pf.OTHER, program="", stage="")
            entries.append(e)
            continue
        data = p.read_bytes()
        e["sha"], e["size"] = hashlib.sha256(data).hexdigest(), len(data)
        c = pf.classify(data, title, programs, e["doc_id"])
        e["_text"] = c.pop("_text", "")
        c.pop("folder", None)                                # classify 의 folder 는 자리 판정 — 지금 폴더(e["folder"])를 덮지 않는다
        e.update(c)
        entries.append(e)

    keeps = [e for e in entries if e["action"] == pf.KEEP]
    # 1) 같은 본문(공백을 지운 글의 지문) — 어디에 있든 하나만
    by_sha: dict[str, list[dict]] = defaultdict(list)
    for e in keeps:
        by_sha[e["measure"]["text_sha"]].append(e)
    for group in by_sha.values():
        if len(group) < 2:
            continue
        best = max(group, key=_rank)
        for e in group:
            if e is not best:
                e.update(action=pf.TRASH, reason_code="duplicate", reason=f"중복 — fid {best['fid']} 와 본문이 같음", dup_of=best["fid"])
    # 2) 같은 자리(사업)·같은 제목 — 가장 나은 하나만(본문 유사도도 적는다)
    keeps = [e for e in entries if e["action"] == pf.KEEP]
    by_title: dict[tuple, list[dict]] = defaultdict(list)
    for e in keeps:
        k = pf.norm_title(e["title"])
        if k:
            by_title[(e["program"], k)].append(e)
    for (prog, k), group in by_title.items():
        if len(group) >= 2:
            best = max(group, key=_rank)
            for e in group:
                if e is best:
                    continue
                sim = pf.similarity(e["_text"], best["_text"])
                code = "neardup" if sim >= pf.NEAR_DUP_RATIO else "sametitle"
                e.update(action=pf.TRASH, reason_code=code, dup_of=best["fid"],
                         reason=f"{pf.REASON_LABEL[code]} — fid {best['fid']} 와 같은 자리·같은 제목, 본문 유사도 {sim:.2f}")
        if not prog and k in platform_titles:                 # 플랫폼 공통 양식과 같은 제목 — 플랫폼 것을 남긴다
            for e in group:
                if e["action"] == pf.KEEP:
                    e.update(action=pf.TRASH, reason_code="sametitle",
                             reason=f"같은 제목 — 플랫폼 공통 양식 「{platform_titles[k]}」 이 있음")
    # 3) 작은 사업은 목적 폴더 없이
    per_prog = Counter(e["program"] for e in entries if e["action"] == pf.KEEP and e["program"])
    for e in entries:
        e.pop("_text", None)
        if e["action"] == pf.KEEP:
            e["target"] = pf.folder_for(e["program"], e["purpose"], small=0 < per_prog[e["program"]] <= pf.SMALL_PROGRAM)
        else:
            e["target"] = ""
        names = (e.get("measure") or {}).pop("names", [])
        e["title_masked"] = pf.mask_title(e["title"], names)
        e["label"] = pf.REASON_LABEL.get(e.get("reason_code", ""), e.get("reason_code", "")) if e["action"] == pf.TRASH else ""
    thresholds = {k: getattr(pf, k) for k in dir(pf) if k.isupper() and isinstance(getattr(pf, k), (int, float))}
    return {"created": datetime.now().isoformat(timespec="seconds"), "base": str(base), "db": str(db.path),
            "thresholds": thresholds, "entries": entries}


def _measure_line(m: dict) -> str:
    if not m.get("ok"):
        return m.get("error", "") or "-"
    bs = m.get("blank_share")
    return (f"글자 {m.get('chars')}·표 {m.get('tables')}·빈 칸 {'-' if bs is None else f'{bs:.0%}'}·필드 {m.get('fields')}·"
            f"이름 {m.get('name_count')}·휴대전화 {m.get('mobiles')}·날짜 {m.get('dates')}·양식 표시 {pf.template_signals(m)}")


def write_md(plan: dict, path: Path) -> None:
    es = plan["entries"]
    keep = [e for e in es if e["action"] == pf.KEEP]
    trash = [e for e in es if e["action"] == pf.TRASH]
    L = ["# 지난 문서 양식 정리 계획", "", f"만든 때 {plan['created']} · 대상 {len(es)}건 · 남김 {len(keep)} · 휴지통 {len(trash)}", ""]
    L += ["## 남길 자리", ""]
    common = Counter(e["purpose"] for e in keep if not e["program"])
    L.append(f"공통 양식(사업 전용 아님) {sum(common.values())}건 — " + " · ".join(f"{k} {v}" for k, v in common.most_common()))
    L.append("")
    progs: dict[str, Counter] = defaultdict(Counter)
    for e in keep:
        if e["program"]:
            progs[e["program"]][e["purpose"]] += 1
    L.append(f"지난 사업 문서 {sum(sum(c.values()) for c in progs.values())}건 · 사업 {len(progs)}개")
    L.append("")
    L += ["| 사업 | 건수 | 목적 |", "|---|---|---|"]
    for prog, c in sorted(progs.items(), key=lambda kv: -sum(kv[1].values())):
        L.append(f"| {prog} | {sum(c.values())} | {' · '.join(f'{k} {v}' for k, v in c.most_common())} |")
    L += ["", "## 휴지통으로", ""]
    by = Counter(e["label"] for e in trash)
    L.append(" · ".join(f"{k} {v}" for k, v in by.most_common()))
    for label, _n in by.most_common():
        L += ["", f"### {label} ({_n})", "", "| fid | 제목 | 까닭 | 잰 값 |", "|---|---|---|---|"]
        for e in sorted((x for x in trash if x["label"] == label), key=lambda x: x["title_masked"]):
            L.append(f"| {e['fid']} | {e['title_masked'][:60]} | {e['reason']} | {_measure_line(e.get('measure') or {})} |")
    L += ["", "## 문턱", "", " · ".join(f"{k}={v}" for k, v in sorted(plan["thresholds"].items())), ""]
    path.write_text("\n".join(L), encoding="utf-8")


def cmd_plan(db, base: Path, out: Path) -> dict:
    plan = build_plan(db, base)
    out.mkdir(parents=True, exist_ok=True)
    (out / "tidy_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    write_md(plan, out / "tidy_plan.md")
    es = plan["entries"]
    c = Counter(e["label"] or ("공통 양식" if not e["program"] else "지난 사업 문서") for e in es)
    print(f"대상 {len(es)}건 — " + " · ".join(f"{k} {v}" for k, v in c.most_common()), flush=True)
    print(f"계획: {out / 'tidy_plan.json'} · {out / 'tidy_plan.md'}", flush=True)
    return plan


# ── apply ──
def _state(db, base: Path, e: dict) -> str:
    """todo · done · mismatch — 계획 때와 지금이 같은가."""
    row = webhwp.get(db, e["fid"])
    if e["action"] == pf.TRASH:
        if row is None:
            return "done"
        if row["path"] == e["path"] and _sha(Path(row["path"])) == e["sha"]:
            return "todo"
        return "mismatch"
    if row is None:
        return "mismatch"
    cur = Path(row["path"])
    if _sha(cur) != e["sha"]:
        return "mismatch"
    if row["path"] == e["path"]:
        return "done" if e["folder"] == e["target"] else "todo"
    return "done" if webhwp.folder_of(base, row["path"]) == webhwp.clean_folder(e["target"]) else "mismatch"


def _versions(p: Path) -> list[Path]:
    vdir = p.parent / ".판"
    return webhwp._versions(vdir, p) if vdir.is_dir() else []


def _settings_for(conn, fids: list[int], paths: list[str]) -> list[tuple[str, str]]:
    out = {}
    for fid in fids:
        for k, v in conn.execute("SELECT key, value FROM settings WHERE key LIKE ? AND key LIKE ?", ("%hwp%", f"%:{fid}")):
            out[k] = v
    for p in paths:
        for needle in _needles(p):
            for k, v in conn.execute("SELECT key, value FROM settings WHERE instr(value, ?) > 0", (needle,)):
                out[k] = v
    return sorted(out.items())


def _needles(p: str) -> list[str]:
    return list(dict.fromkeys([p, json.dumps(p, ensure_ascii=False)[1:-1], json.dumps(p)[1:-1]]))


def backup(db, base: Path, plan: dict, todo: list[dict], dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    root = webhwp.root(base)
    files_dir = dest / "files"
    with db._conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(f"SELECT * FROM files WHERE id IN ({','.join('?' * len(todo)) or 'NULL'})",
                                              [e["fid"] for e in todo])]
        sets = _settings_for(conn, [e["fid"] for e in todo], [e["path"] for e in todo])
    for e in todo:
        p = Path(e["path"])
        for x in [p, *_versions(p)]:
            if x.exists():
                rel = x.resolve().relative_to(root.resolve())
                (files_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(x, files_dir / rel)
    (dest / "files_rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    (dest / "settings.json").write_text(json.dumps(sets, ensure_ascii=False, indent=1), encoding="utf-8")
    (dest / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    (dest / "manifest.json").write_text(json.dumps({"root": str(root), "base": str(base), "db": str(db.path), "moves": []},
                                                   ensure_ascii=False, indent=1), encoding="utf-8")
    return dest


def _free(d: Path, name: str) -> Path:
    p, stem, ext, n = d / name, Path(name).stem, Path(name).suffix, 2
    while p.exists():
        p, n = d / f"{stem} ({n}){ext}", n + 1
    return p


def move_file(db, base: Path, fid: int, target_folder: str) -> tuple[str, str]:
    """파일 하나를 공통 부서의 다른 폴더로 — 앞 판(.판)도 함께, 장부(files.path·name)와 경로를 담은 설정 값까지. (옛 경로, 새 경로)."""
    row = webhwp.get(db, fid)
    old = Path(row["path"])
    d = webhwp.folder_dir(base, webhwp.COMMON, target_folder)
    d.mkdir(parents=True, exist_ok=True)
    new = _free(d, old.name)
    vers = _versions(old)
    shutil.move(str(old), str(new))
    if vers:
        nv = d / ".판"
        nv.mkdir(exist_ok=True)
        for v in vers:
            shutil.move(str(v), str(_free(nv, new.stem + v.name[len(old.stem):])))
    with db._conn() as conn:
        conn.execute("UPDATE files SET path = ?, name = ? WHERE id = ?", (str(new), new.name, int(fid)))
        for a, b in zip(_needles(str(old)), _needles(str(new))):
            conn.execute("UPDATE settings SET value = replace(value, ?, ?) WHERE instr(value, ?) > 0", (a, b, a))
    return str(old), str(new)


def trash_file(db, base: Path, fid: int) -> None:
    """휴지통으로(webhwp.trash) — 앞 판도 휴지통 아래 .판 으로 옮겨 옛 폴더가 비게 한다."""
    row = webhwp.get(db, fid)
    p = Path(row["path"])
    vers = _versions(p)
    if vers:
        t = webhwp.root(base) / webhwp.COMMON / ".휴지통" / ".판"
        t.mkdir(parents=True, exist_ok=True)
        for v in vers:
            shutil.move(str(v), str(_free(t, v.name)))
    webhwp.trash(db, base, fid, user=TIDY_USER)


def prune_empty(base: Path) -> list[str]:
    """정리한 뿌리(옛 「양식」·지난 사업 문서·공통 양식) 아래 빈 폴더(빈 .판 만 남은 것 포함)를 지운다. 뿌리 「공통 양식」 은 남긴다."""
    top = webhwp.root(base) / webhwp.COMMON
    gone = []
    for r in (*pf.OLD_ROOTS, pf.ROOT, pf.COMMON_ROOT):
        d = top / r
        if not d.is_dir():
            continue
        for x in sorted((y for y in d.rglob("*") if y.is_dir()), key=lambda y: -len(y.parts)) + [d]:
            if x == top / pf.COMMON_ROOT:
                continue
            try:
                if not any(x.iterdir()):
                    x.rmdir()
                    gone.append(str(x.relative_to(top)))
            except OSError:
                pass
    return gone


def cmd_apply(db, base: Path, plan_path: Path, backup_dir: Path | None, out: Path) -> int:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    states = {e["fid"]: _state(db, base, e) for e in plan["entries"]}
    bad = [e for e in plan["entries"] if states[e["fid"]] == "mismatch"]
    if bad:
        print(f"계획과 지금이 다릅니다({len(bad)}건) — 아무것도 바꾸지 않았습니다. plan 을 다시 만드세요.", flush=True)
        for e in bad[:20]:
            print(f"  fid {e['fid']} {e['title_masked'][:40]}", flush=True)
        return 2
    todo = [e for e in plan["entries"] if states[e["fid"]] == "todo"]
    if not todo:
        print("할 일 없음 — 이미 계획대로입니다.", flush=True)
        return 0
    dest = backup_dir or Path(os.path.expanduser("~/ops/backup")) / f"past_forms_tidy-{time.strftime('%Y%m%d-%H%M%S')}"
    backup(db, base, plan, todo, dest)
    print(f"백업: {dest}", flush=True)
    moves, removed, cnt = [], {}, Counter()
    for e in todo:
        if e["action"] == pf.TRASH:
            trash_file(db, base, e["fid"])
            cnt["휴지통"] += 1
            if e.get("doc_id"):
                removed[str(e["doc_id"])] = e.get("reason_code", "")
        else:
            old, new = move_file(db, base, e["fid"], e["target"])
            moves.append({"fid": e["fid"], "old": old, "new": new})
            cnt["옮김"] += 1
    man = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    man["moves"] = moves
    man["trashed"] = [e["fid"] for e in todo if e["action"] == pf.TRASH]
    (dest / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    gone = prune_empty(base)
    out.mkdir(parents=True, exist_ok=True)
    rp = out / "tidy_removed.json"
    try:
        old_removed = json.loads(rp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old_removed = {}
    rp.write_text(json.dumps({**old_removed, **removed}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"옮김 {cnt['옮김']} · 휴지통 {cnt['휴지통']} · 이미 됨 {sum(1 for s in states.values() if s == 'done')} · "
          f"빈 폴더 지움 {len(gone)}", flush=True)
    print("PAST_FORMS_TIDY_DONE", flush=True)
    return 0


def cmd_restore(db, base: Path, backup_dir: Path) -> int:
    man = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    rows = {r["id"]: r for r in json.loads((backup_dir / "files_rows.json").read_text(encoding="utf-8"))}
    sets = json.loads((backup_dir / "settings.json").read_text(encoding="utf-8"))
    root = Path(man["root"])
    n = 0
    for mv in man.get("moves", []):
        cur = webhwp.get(db, mv["fid"])
        if cur and Path(cur["path"]).exists() and not Path(mv["old"]).exists():
            Path(mv["old"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(cur["path"], mv["old"])
            n += 1
    for fid in man.get("trashed", []):
        r = rows.get(fid)
        if not r:
            continue
        src = backup_dir / "files" / Path(r["path"]).resolve().relative_to(root.resolve())
        if src.exists() and not Path(r["path"]).exists():
            Path(r["path"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, r["path"])
            n += 1
    with db._conn() as conn:
        for r in rows.values():
            conn.execute("DELETE FROM files WHERE path = ? AND id != ?", (r["path"], r["id"]))
            conn.execute(f"INSERT OR REPLACE INTO files ({','.join(r)}) VALUES ({','.join('?' * len(r))})", list(r.values()))
        for k, v in sets:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))
    print(f"되돌림 — 파일 {n}건, 장부 줄 {len(rows)}, 설정 {len(sets)}", flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["plan", "apply", "restore"])
    ap.add_argument("--plan", type=Path, default=OUT / "tidy_plan.json")
    ap.add_argument("--backup", type=Path, default=None, help="apply 의 백업 자리(기본 ~/ops/backup/past_forms_tidy-<시각>) · restore 의 백업 폴더")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    db = _db()
    base = _base(db)
    if a.step == "plan":
        cmd_plan(db, base, a.out)
        return 0
    if a.step == "apply":
        return cmd_apply(db, base, a.plan, a.backup, a.out)
    if not a.backup:
        ap.error("restore 에는 --backup 이 필요합니다")
    return cmd_restore(db, base, a.backup)


if __name__ == "__main__":
    sys.exit(main())
