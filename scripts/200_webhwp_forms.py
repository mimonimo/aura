#!/usr/bin/env python3
"""한글 양식 → 웹 한글 기안기 양식 — 문서함의 「양식」 갈래 한글 문서(hwp·hwpx)를 골라 기안기에서 바로 쓰는 hwpx 양식으로 만들고 점검한다.

왜(10-22 사용자): 기안기는 hwp·hwpx 를 그대로 다루므로 구글 문서를 거쳐 한글로 바꾸지 않고, 학교가 실제로 쓰는 한글 양식을 따로 모아
그 위에서 바로 쓴다. 같은 양식의 판·사본이 많아 대표 하나만 고르고, hwp 는 기안기 서버(필터 서버 open→save)로 hwpx 로 바꾼다.
- select : 문서함 kind=form 이고 원본이 hwp·hwpx 인 문서를 제목(연도·번호·괄호·○○ 를 뺀 꼴)으로 묶어 묶음마다 대표 하나
           (제목에 양식·서식이 있는 것 > hwpx > 최근). 결과 data/platform/webhwp_forms/select.json
- convert: 대표의 원본을 원본 보관소(DGX)에서 받아 hwpx 로(hwp 는 기안기 서버 변환). 이미 한 것(원본 지문 같음)은 건너뜀
- check  : 변환본마다 — 기안기에서 다시 열리는가(open), 표·문단 수, 절 제목 후보, 채우기 시험(첫 절에 문단·표, 서식 표 칸 채우기·행 늘리기),
           자가 점검(checks). 결과 report.json·report.md
- register: 점검을 통과한 것을 한글 문서함 「공통/양식/<첫 낱말 묶음>」 에 잠긴 양식(source 「양식:doc-<id>」)으로 둔다
운영 문서함·DB 의 문서는 읽기만 한다(register 만 기안기 문서함에 파일을 더한다).
사용(VM): set -a; . ./.env.local; set +a; PYTHONPATH=src .venv/bin/python scripts/200_webhwp_forms.py all [--limit N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import storage  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402

OUT = ROOT / "data" / "platform" / "webhwp_forms"
HWP_EXTS = (".hwp", ".hwpx")


def _db() -> Database:
    return Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))


def norm_title(t: str) -> str:
    t = storage.title_of(t or "")
    t = re.sub(r"[\(\[\{（【<「][^\)\]\}）】>」]*[\)\]\}）】>」]", "", t)
    t = re.sub(r"20\d\d|\d+\s*(차년도|차|회차|년|월|일|호|번)?", "", t)
    t = re.sub(r"(ㅇ|○|◯|o{2,}|O{2,})+", "", t)
    t = re.sub(r"(양식|서식|최종|수정|붙임|별지|별첨|제출용|작성용)", "", t)
    return re.sub(r"[^가-힣A-Za-z]", "", t)


def select(db) -> list[dict]:
    with db._conn() as c:
        rows = [dict(r) for r in c.execute("SELECT id, filename, stored_path, created_at, kind, status FROM documents "
                                           "WHERE kind = 'form' AND status = 'reviewed'").fetchall()]
    rows = [r for r in rows if storage.original_ext(r) in HWP_EXTS]
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[norm_title(r["filename"]) or f"doc-{r['id']}"].append(r)

    def rank(r):
        # 빈 양식다운 것 — 「양식·서식·○○」이 있고, 괄호 속 기관·학과·사람 이름(채운 사본의 표시)이 없고, 제목이 짧은 것.
        # 그다음 hwpx, 최근 순
        t = storage.title_of(r["filename"] or "")
        named = [x for x in re.findall(r"[\(\[（【]([^\)\]）】]*)[\)\]）】]", t) if not re.search(r"양식|서식|○|ㅇ|oo|OO|붙임|별지", x)]
        return (bool(re.search(r"양식|서식|○○|ㅇㅇ|oo|OO", t)), not named, storage.original_ext(r) == ".hwpx", -len(t),
                r.get("created_at") or "")

    picked = []
    for key, rs in groups.items():
        best = max(rs, key=rank)
        picked.append({"id": best["id"], "title": storage.title_of(best["filename"] or ""), "ext": storage.original_ext(best),
                       "key": key, "copies": len(rs), "stored_path": best["stored_path"]})
    picked.sort(key=lambda x: (-x["copies"], x["title"]))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "select.json").write_text(json.dumps({"forms": len(rows), "groups": len(picked), "picked": picked},
                                                ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"양식 한글 문서 {len(rows)}건 → 묶음 {len(picked)}개", flush=True)
    return picked


def _archive_row(db, sp: str) -> dict | None:
    if not sp.startswith("dgx://"):
        return None
    from zzaimy.app import archive
    archive.ensure(db)
    with db._conn() as c:
        row = c.execute("SELECT rel, size, mtime FROM archive_files WHERE rel = ? AND removed_at = ''", (sp[6:],)).fetchone()
    return dict(zip(("rel", "size", "mtime"), row)) if row else None


def to_hwpx(src: Path, ext: str, http) -> bytes:
    """hwpx 는 그대로, hwp 는 기안기 서버로 — open(파일→문서 데이터) → save(HWPX) → 받기."""
    data = src.read_bytes()
    if ext == ".hwpx":
        return data
    from zzaimy.app import webhwp
    base = webhwp.base_url()
    r = http.post(f"{base}/webhwpctrl/open", files={"openFile": (f"x{ext}", data)}, data={"format": "HWP", "args": ""}, timeout=180)
    r.raise_for_status()
    j = r.json()
    if not j.get("json"):
        raise RuntimeError(f"기안기 열기 실패 {j.get('resultCode')}")
    s = http.post(f"{base}/webhwpctrl/save", headers={"AJAX": "true"},
                  files={"hwpj": ("hwpj.data", j["json"].encode("utf-8"))},
                  data={"id": j["id"], "filename": "x.hwpx", "format": "HWPX", "args": ""}, timeout=180)
    s.raise_for_status()
    sj = s.json()
    if not sj.get("result"):
        raise RuntimeError(f"기안기 저장 실패 {sj.get('resultCode')}")
    g = http.get(f"{base}/filterserver/get/{j['id']}/{sj['fileName']}", timeout=120)
    g.raise_for_status()
    if g.content[:2] != b"PK":
        raise RuntimeError("변환 결과가 hwpx 가 아님")
    return g.content


def convert(db, picked: list[dict], limit: int | None) -> None:
    import httpx
    from zzaimy.app import archive_original
    done = _load(OUT / "convert.json")
    (OUT / "hwpx").mkdir(parents=True, exist_ok=True)
    n = 0
    with httpx.Client() as http:
        for p in picked:
            if limit and n >= limit:
                break
            key = str(p["id"])
            row = _archive_row(db, str(p["stored_path"] or ""))
            fp = f"{row['size']}|{row['mtime']}" if row else ""
            if done.get(key, {}).get("fp") == fp and (OUT / "hwpx" / f"{key}.hwpx").exists():
                continue
            n += 1
            t0 = time.time()
            try:
                if row is None:
                    raise FileNotFoundError("원본 보관소 대장에 없음")
                src = archive_original.fetch(row)
                try:
                    data = to_hwpx(Path(src), p["ext"], http)
                finally:
                    try:
                        Path(src).unlink()
                    except OSError:
                        pass
                (OUT / "hwpx" / f"{key}.hwpx").write_bytes(data)
                done[key] = {"fp": fp, "ok": True, "size": len(data), "sec": round(time.time() - t0, 1)}
            except Exception as e:
                done[key] = {"fp": fp, "ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
            print(f"[{n}] doc {key} {'변환' if done[key]['ok'] else '실패'} {p['title'][:40]} {done[key].get('error', '')}", flush=True)
            _save(OUT / "convert.json", done)
    _save(OUT / "convert.json", done)


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(p: Path, d: dict) -> None:
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


_PARA = re.compile(r"<hp:p\b.*?</hp:p>", re.S)


def _texts(xml: str) -> list[str]:
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", p)).strip() for p in _PARA.findall(xml)]


def check_one(path: Path, http) -> dict:
    """한 양식 점검 — 다시 열리는가, 구조, 채우기 시험(문단·표, 서식 표 칸·행 늘리기), 자가 점검."""
    from zzaimy.app import section_context as sc
    from zzaimy.app import webhwp
    from zzaimy.ingest import hwpx_fill as hf
    out: dict = {"ok": True, "problems": []}
    try:
        with zipfile.ZipFile(path) as z:
            secs = sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n))
            xml = "".join(z.read(n).decode("utf-8") for n in secs)
    except Exception as e:
        return {"ok": False, "problems": [f"hwpx 를 읽지 못함({type(e).__name__})"]}
    out["tables"] = xml.count("<hp:tbl")
    out["pictures"] = xml.count("<hp:pic")
    texts = [t for t in _texts(xml) if t]
    out["paragraphs"] = len(texts)
    heads = []
    for t in texts:
        num, title = sc.split_number(t)
        if num and 2 <= len(title) <= 40 and not re.search(r"\d{4}\.\s*\d", t):
            heads.append(t)
    out["headings"] = heads[:40]
    out["n_headings"] = len(heads)
    # 1) 기안기에서 다시 열리는가(웹 편집기가 그리는 꼴로 읽기)
    try:
        r = http.post(f"{webhwp.base_url()}/webhwpctrl/open", files={"openFile": ("x.hwpx", path.read_bytes())},
                      data={"format": "HWPX", "args": ""}, timeout=180)
        out["opens"] = bool(r.status_code == 200 and r.json().get("json"))
    except Exception as e:
        out["opens"] = False
        out["problems"].append(f"기안기에서 열기 실패({type(e).__name__})")
    if not out["opens"]:
        out["ok"] = False
    # 1-1) 쓸 자리 필드 박기(webhwp_fields) — 박은 파일이 기안기에서 열려야 등록한다. 등록은 이 파일로
    try:
        from zzaimy.app import webhwp_fields
        fz, fields = webhwp_fields.fieldize(path.read_bytes())
        out["fields"] = len(fields)
        out["field_kinds"] = dict(Counter(f["kind"] for f in fields))
        out["field_names"] = [f["name"] for f in fields][:30]
        r3 = http.post(f"{webhwp.base_url()}/webhwpctrl/open", files={"openFile": ("z.hwpx", fz)},
                       data={"format": "HWPX", "args": ""}, timeout=180)
        out["fields_open"] = bool(r3.status_code == 200 and r3.json().get("json"))
        if out["fields_open"] and fields:
            path.with_suffix(".fields.hwpx").write_bytes(fz)
        elif not fields:
            out["problems"].append("쓸 자리(절·칸·목록표)를 찾지 못해 필드를 박지 못함")
        else:
            out["problems"].append("필드를 박은 파일이 기안기에서 열리지 않음")
            out["ok"] = False
    except Exception as e:
        out["problems"].append(f"필드 박기 실패({type(e).__name__}: {str(e)[:60]})")
        out["ok"] = False
    # 2) 칸 양식 시험 — 라벨 칸 오른쪽 빈 칸(값 칸)을 채워 기안기에서 다시 열리는가(신청서·서명부·이력서 같은 칸만 있는 양식)
    labels = []
    for tr in re.findall(r"<hp:tr\b.*?</hp:tr>", xml, re.S):
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<hp:tc\b.*?</hp:tc>", tr, re.S)]
        for a, b in zip(cells, cells[1:]):
            if a and not b and 1 < len(a) <= 15 and a not in labels:
                labels.append(a)
    out["label_cells"] = len(labels)
    out["kind"] = "절 양식" if heads else "칸 양식"
    if labels:
        try:
            from hwpx import HwpxDocument
            doc = HwpxDocument.open(path.read_bytes())
            res = doc.fill_by_path({f"{lb} > right": "점검값" for lb in labels[:6]})
            filled = getattr(res, "filled", None) or getattr(res, "applied", None)
            out["label_fill"] = len(filled) if isinstance(filled, (list, tuple, dict)) else str(res)[:60]
            data = doc.to_bytes()
            r2 = http.post(f"{webhwp.base_url()}/webhwpctrl/open", files={"openFile": ("y.hwpx", data)},
                           data={"format": "HWPX", "args": ""}, timeout=180)
            out["label_fill_opens"] = bool(r2.status_code == 200 and r2.json().get("json"))
            if not out["label_fill_opens"]:
                out["problems"].append("칸을 채운 파일이 기안기에서 열리지 않음")
                out["ok"] = False
        except Exception as e:
            out["problems"].append(f"칸 채우기 실패({type(e).__name__}: {str(e)[:60]})")
    # 3) 절 양식 시험 — 절 제목 아래 문단과 새 표, 서식 표(머리행 + 빈 행) 칸 채우기·행 늘리기. 제목 후보를 차례로(목차 줄은 건너뜀)
    if heads:
        tbl_head = None
        for m in re.finditer(r"<hp:tbl\b.*?</hp:tbl>", xml, re.S):
            rows = [[re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<hp:tc\b.*?</hp:tc>", tr, re.S)]
                    for tr in re.findall(r"<hp:tr\b.*?</hp:tr>", m.group(0), re.S)]
            if len(rows) >= 2 and len(rows[0]) >= 2 and all(rows[0]) and not any(any(c for c in r) for r in rows[1:]):
                tbl_head = rows[0]
                break
        items = [("text", "1) 채우기 점검 문단\n가) 하위 항목")]
        items.append(("table", [["구분", "내용"], ["점검", "새 표"]]))
        if tbl_head:
            items.append(("table", [tbl_head] + [[f"값{r}-{c}" for c in range(len(tbl_head))] for r in range(1, 6)]))
        try:
            rep = {}
            for h in heads[:8]:
                rep = hf.fill(path, [{"heading": h, "items": items}], path.with_suffix(".filled.hwpx"))
                if rep.get("filled"):
                    break
            out["fill"] = {k: rep.get(k) for k in ("filled", "skipped", "paragraphs", "tables", "tables_updated", "tables_grown", "checks")}
            out["form_table"] = bool(tbl_head)
            if not rep.get("filled"):
                out["problems"].append("절 제목 후보 8개 모두 서식에서 찾지 못함")
            if rep.get("checks"):
                out["problems"].append("자가 점검: " + "; ".join(rep["checks"][:3]))
                out["ok"] = False
            if tbl_head and not (rep.get("tables_updated") or rep.get("tables_grown")):
                out["problems"].append("서식 표에 값을 채우지 못함")
            path.with_suffix(".filled.hwpx").unlink(missing_ok=True)
        except Exception as e:
            out["problems"].append(f"채우기 실패({type(e).__name__}: {str(e)[:80]})")
            out["ok"] = False
    elif not labels:
        out["problems"].append("절 제목도 라벨 칸도 찾지 못함")
    return out


def check(picked: list[dict]) -> dict:
    import httpx
    conv = _load(OUT / "convert.json")
    res = _load(OUT / "check.json")
    titles = {str(p["id"]): p for p in picked}
    with httpx.Client() as http:
        for key, c in conv.items():
            if not c.get("ok"):
                continue
            path = OUT / "hwpx" / f"{key}.hwpx"
            sha = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            if res.get(key, {}).get("sha") == sha:
                continue
            r = check_one(path, http)
            r["sha"] = sha
            r["title"] = titles.get(key, {}).get("title", "")
            res[key] = r
            print(f"점검 doc {key} {'통과' if r['ok'] and not r['problems'] else '확인'} 표 {r.get('tables')} 제목 {r.get('n_headings')} "
                  f"{'; '.join(r['problems'])[:120]}", flush=True)
            _save(OUT / "check.json", res)
    _save(OUT / "check.json", res)
    return res


def report(picked: list[dict]) -> None:
    conv, res = _load(OUT / "convert.json"), _load(OUT / "check.json")
    cnt = Counter()
    lines = ["# 기안기 양식 점검", ""]
    for p in picked:
        k = str(p["id"])
        c, r = conv.get(k), res.get(k)
        if not c:
            cnt["미처리"] += 1
            continue
        if not c.get("ok"):
            cnt["변환 실패"] += 1
            lines.append(f"- 변환 실패 · doc {k} {p['title']} — {c.get('error')}")
            continue
        if not r:
            cnt["점검 전"] += 1
            continue
        state = "통과" if r["ok"] and not r["problems"] else ("확인 필요" if r["ok"] else "문제")
        cnt[state] += 1
        cnt[r.get("kind", "?")] += 1
        cnt["필드 있음" if r.get("fields") else "필드 없음"] += 1
        if state != "통과":
            lines.append(f"- {state} · doc {k} {p['title']} (표 {r.get('tables')}, 제목 {r.get('n_headings')}) — {'; '.join(r['problems'])}")
    head = ["", f"묶음 {len(picked)}개 · " + " · ".join(f"{k} {v}" for k, v in cnt.most_common()), ""]
    (OUT / "report.md").write_text("\n".join(lines[:2] + head + lines[2:]) + "\n", encoding="utf-8")
    _save(OUT / "report.json", dict(cnt))
    print(" · ".join(f"{k} {v}" for k, v in cnt.most_common()), flush=True)


def register(db, picked: list[dict]) -> None:
    """점검 통과(문제 없음) 양식을 한글 문서함 「공통/양식/<묶음>」 에 잠긴 양식으로 — 이미 둔 것은 내용이 같으면 그대로."""
    from zzaimy.app import webhwp
    base = Path(db.path).parent if hasattr(db, "path") else ROOT / "data/platform"
    res = _load(OUT / "check.json")
    placed = _load(OUT / "registered.json")
    n = 0
    for p in picked:
        k = str(p["id"])
        r = res.get(k)
        if not r or not r.get("ok"):
            continue
        fz = OUT / "hwpx" / f"{k}.fields.hwpx"
        if not fz.exists():                              # 쓸 자리 필드가 없는 양식은 기안기 작업 양식으로 두지 않는다
            continue
        data = fz.read_bytes()
        sha = hashlib.sha256(data).hexdigest()[:16]
        if placed.get(k, {}).get("sha") == sha and webhwp.get(db, int(placed[k]["fid"])):
            continue
        group = (re.findall(r"[가-힣]{2,}", p["title"]) or ["기타"])[0][:12]
        if placed.get(k) and webhwp.get(db, int(placed[k]["fid"])):
            webhwp.save(db, base, int(placed[k]["fid"]), data, user="zzaimy")
            fid = int(placed[k]["fid"])
        else:
            fid = webhwp.add(db, base, webhwp.COMMON, f"{p['title'][:60]}.hwpx", data, owner="zzaimy",
                             source=f"양식:doc-{k}", folder=f"양식/{group}")
        placed[k] = {"fid": fid, "sha": sha}
        n += 1
    _save(OUT / "registered.json", placed)
    print(f"한글 문서함에 둔 양식 {n}건(전체 {len(placed)})", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["select", "convert", "check", "report", "register", "all"])
    ap.add_argument("--limit", type=int, default=None, help="이번에 변환할 최대 수(시험용)")
    a = ap.parse_args()
    db = _db()
    picked = select(db)
    if a.step in ("convert", "all"):
        convert(db, picked, a.limit)
    if a.step in ("check", "all"):
        check(picked)
    if a.step in ("report", "all", "check"):
        report(picked)
    if a.step == "register":
        register(db, picked)
    print("WEBHWP_FORMS_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
