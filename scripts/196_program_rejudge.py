#!/usr/bin/env python3
"""사업 분류 다시 보기 — 폴더 판정·이름 뽑기가 잘못 묶은 원본을 27B 가 파일 하나씩 보고 사업을 정한다.

왜: 폴더 단위 「사업 아님」 판정(class_review.jsonl)은 그 폴더에 섞인 사업 문서(「2020 P-TECH사업 결과보고서」)까지
기관 일반 업무로 보내고, 파일 이름 뽑기는 「3차년도 지방보조사업」「수익 비수익 사업」 같은 일반어를 사업 묶음으로 세운다.
코드가 낱말로 사업을 정하지 않는다 — 코드는 다시 볼 후보를 고르고(장부 이름이 제목·본문에 나오는 「사업 아님」 원본,
사업 이름이 아닌 묶음에 든 원본, 폴더째 한 사업으로 잡혔는데 파일 제목에는 다른 장부 사업 이름만 나오는 원본, 같은 본문이 서로 다른 사업 폴더에 든 원본), 27B 가 제목·폴더·본문 첫머리와 외부 확인 장부(kg_external.json)의 사업 목록을 보고 정한다.

판정은 같은 검토 장부에 파일 단위 줄(「file」)로 덧붙인다 — 원본 장부 맞추기(170)와 그래프(157)가 폴더 판정보다 먼저 쓴다
(graph.programs.apply_reviews). 답이 장부에 없는 이름이거나 「모름」·확신 낮음이면 적지 않는다. 줄마다 판정 모델을 남긴다.
사용(VM, .env.local 과 함께): PYTHONPATH=src .venv/bin/python scripts/196_program_rejudge.py --role review [--dry] [--limit N] [--redo]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import program_tree as pt  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402
from zzaimy.app.program_names import group_key, is_program_name, load_ledger  # noqa: E402

_spec = importlib.util.spec_from_file_location("s190", ROOT / "scripts" / "190_kind_read_sample.py")
s190 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(s190)

REVIEW = ROOT / "data" / "platform" / "class_review.jsonl"
NOT_PROGRAM = "사업 아님"

SYSTEM = """당신은 대학 행정 문서를 사업별로 정리하는 검토자다. 문서 하나가 어느 재정지원사업의 문서인지 정한다.
- 그 사업을 신청·수행·운영·보고·정산·평가하려고 만든 자료(계획서·결과보고서·공문·서식·명단·회계 자료 등)면 그 사업이다.
- 사업 이름이 지나가듯 나오기만 하는 기관 일반 업무(인사 일반·학교 전체 현황·여러 사업을 모은 표 등)는 「사업 아님」이다.
- 사업은 아래 「사업 목록」에 적힌 이름 그대로 고른다. 목록에 맞는 사업이 없거나 판단할 근거가 모자라면 「모름」.
- 제목·폴더·본문에 적힌 것만 근거로 삼는다. 추측으로 사업을 고르지 않는다.
답은 JSON 하나만: {"label": "사업 목록의 이름 그대로" 또는 "사업 아님" 또는 "모름", "confidence": "high"|"medium"|"low", "reason": "근거 한 문장"}"""


def _flat(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


def ledger_names(ledger: dict) -> list[dict]:
    return [e for e in ledger.get("programs") or [] if e.get("name") and e.get("category") != "사업 아님"]


def mentioned(text: str, entries: list[dict]) -> list[str]:
    """글에 이름·별칭이 그대로 나오는 장부 사업(긴 표기부터) — 27B 에게 먼저 보일 후보일 뿐, 고르는 것은 27B."""
    flat = _flat(text)
    out = []
    for e in entries:
        terms = sorted({_flat(t) for t in [e["name"], *(e.get("terms") or [])] if len(_flat(t)) >= 2}, key=len, reverse=True)
        if any(t in flat for t in terms):
            out.append(e["name"])
    return out


def not_name(name: str, lg: pt.Ledger) -> bool:
    """사업 목록 나무가 「사업 이름 아닌 묶음」으로 빼는 이름 — program_tree.build 와 같은 판정."""
    return bool(name) and lg.find(name) is None and (
        group_key(name) in lg.not_program or ("사업" in name and not is_program_name(name)))


def candidates(db, lg: pt.Ledger, entries: list[dict], done: set[str]) -> list[dict]:
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT a.rel, a.program, a.program_name, a.status, a.doc_id, d.filename, d.masked_text FROM archive_files a"
            " JOIN documents d ON d.id = a.doc_id WHERE a.removed_at = '' AND a.doc_id IS NOT NULL").fetchall()]
    # 같은 본문이 서로 다른 사업 폴더에 든 원본 — 어느 사업 것인지 파일마다 다시 본다(10-27 문서함 정리 뒤 208건, 같은 사업 안 여분은 187 dup 이 뺀다)
    import hashlib
    progs_of: dict[str, set] = {}
    sig = {}
    for r in rows:
        t = r.get("masked_text") or ""
        if len(t) >= 200 and r.get("program"):
            h = hashlib.sha1(t.encode("utf-8")).hexdigest()
            sig[r["rel"]] = h
            progs_of.setdefault(h, set()).add(r.get("program_name") or r["program"])
    out = []
    for r in rows:
        if r["rel"] in done:
            continue
        h = sig.get(r["rel"])
        if h and len(progs_of.get(h, ())) > 1:
            head = (r.get("masked_text") or "")[:1500]
            title = Path(r["rel"]).stem
            others = sorted(progs_of[h] - {r.get("program_name") or r["program"]})
            hint = (mentioned(title, entries) or mentioned(head, entries))[:6]
            out.append({**r, "why": "dup_cross", "title": title, "head": head, "hint": hint,
                        "cur": r.get("program_name") or "", "others": others[:4]})
            continue
        head = (r.get("masked_text") or "")[:1500]
        title = Path(r["rel"]).stem
        if not r.get("program") and r.get("status") == "agent":
            why = "nonprogram"
            hint = mentioned(title, entries) or mentioned(head[:600], entries)
            if not hint:                         # 장부 사업 이름이 나오지 않는 일반 업무 자료는 다시 보지 않는다
                continue
        elif r.get("program") and not_name(r.get("program_name") or "", lg):
            why = "not_name"
            hint = mentioned(title, entries) or mentioned(head, entries)
        elif r.get("program"):
            # 폴더째 한 사업으로 잡혔는데 파일 제목에는 다른 장부 사업 이름만 나오는 원본 — 폴더 판정이 섞인 폴더를 덮은 경우(10-19:
            # 「대학통합컨설팅 실무지원자료」 폴더의 맞춤형인력양성사업 계획서들이 LINC 로). 제목만 본다 — 본문에 지나가듯 나온 이름은 후보로 삼지 않는다
            cur = lg.find(r.get("program_name") or "")
            hint = mentioned(title, entries)
            if not hint or cur is None or cur.get("name") in hint:
                continue
            why = "mismatch"
            r = {**r, "cur": cur.get("name")}
        else:
            continue
        out.append({**r, "why": why, "title": title, "head": head, "hint": hint[:6]})
    return out


def ask(complete, c: dict, entries: list[dict]) -> dict | None:
    names = [e["name"] for e in entries]
    user = (f"제목: {c['title']}\n폴더: {str(Path(c['rel']).parent)}\n"
            + (f"제목·본문에 이름이 나오는 사업: {', '.join(c['hint'])}\n" if c["hint"] else "")
            + (f"지금 묶인 사업: {c['cur']} — 같은 본문이 다른 사업 폴더에도 있다: {', '.join(c['others'])}. 두 사업 모두에 쓰인 공용 자료면 "
               "본문·제목이 더 직접 가리키는 사업을, 가릴 수 없으면 지금 사업을 고른다.\n" if c.get("why") == "dup_cross" else "")
            + f"본문 첫머리:\n<<<\n{c['head'] or '(본문 없음)'}\n>>>\n\n사업 목록:\n" + "\n".join(f"- {n}" for n in names))
    raw = complete([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}])
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        got = json.loads(m.group(0))
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", default="review", help="판정 연결 용도(VllmClient) — VM 에서 .env.local 과 함께")
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--model", default="qwen3.8:27b")
    ap.add_argument("--db", default=str(ROOT / "data" / "platform" / "platform.db"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry", action="store_true", help="후보와 판정만 보이고 장부에 적지 않는다")
    ap.add_argument("--redo", action="store_true", help="이미 파일 단위로 판정한 원본도 다시 본다")
    ap.add_argument("--workers", type=int, default=4, help="동시에 묻는 수(서빙이 묶어 처리한다)")
    args = ap.parse_args()

    ledger = load_ledger()
    lg, entries = pt.Ledger(ledger), ledger_names(ledger)
    done: set[str] = set()
    if REVIEW.is_file() and not args.redo:
        for line in REVIEW.read_text(encoding="utf-8").splitlines():
            try:
                f = json.loads(line).get("file")
            except ValueError:
                continue
            if f:
                done.add(f)
    db = Database(Path(args.db))
    todo = candidates(db, lg, entries, done)
    print(f"후보 전체 {len(todo)}건" + (f" — 이번에 {args.limit}건" if args.limit else ""), flush=True)
    if args.limit:
        todo = todo[:args.limit]
    print(f"다시 볼 원본 {len(todo)}건(사업 아님 판정인데 장부 사업 이름이 나옴 {sum(c['why'] == 'nonprogram' for c in todo)}"
          f" · 사업 이름 아닌 묶음 {sum(c['why'] == 'not_name' for c in todo)}"
          f" · 같은 본문이 다른 사업에 {sum(c['why'] == 'dup_cross' for c in todo)})", flush=True)
    if not todo:
        return 0
    complete, llm = s190.make_complete(args.url, args.model, args.role)
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from zzaimy.generate import model_info
    stamp = time.strftime("%Y-%m-%d")
    tally = {"바뀜": 0, "그대로": 0, "버림": 0, "실패": 0}
    lock = threading.Lock()
    if not args.dry and REVIEW.is_file():
        shutil.copy2(REVIEW, REVIEW.with_name(f"class_review.jsonl.bak-{time.strftime('%Y%m%d%H%M%S')}"))

    def one(ic):
        i, c = ic
        try:
            got = ask(complete, c, entries)
        except Exception as e:
            with lock:
                tally["실패"] += 1
                print(f"[{i}] 판정 실패 {type(e).__name__} {c['title'][:60]}", flush=True)
            return
        label = str((got or {}).get("label") or "").strip()
        conf = str((got or {}).get("confidence") or "").strip()
        if label != NOT_PROGRAM:
            e = lg.find(label) if label and label != "모름" else None
            label = (e or {}).get("name") or ""
        with lock:
            if not label or conf not in ("high", "medium"):
                tally["버림"] += 1
                print(f"[{i}] 버림({(got or {}).get('label')}·{conf}) {c['title'][:60]}", flush=True)
                return
            same = (label == NOT_PROGRAM and c["why"] == "nonprogram") or (c["why"] == "mismatch" and label == c.get("cur"))
            tally["그대로" if same else "바뀜"] += 1
            print(f"[{i}] {'그대로' if same else '→ ' + label} ({conf}) {c['title'][:60]} — {str(got.get('reason') or '')[:60]}", flush=True)
            if args.dry:
                return
            row = {"file": c["rel"], "label": label, "raw": str(got.get("label") or ""), "confidence": conf,
                   "reason": str(got.get("reason") or "")[:200], "reviewer": model_info.label(llm),
                   model_info.FIELD: llm, "at": stamp, "was": c.get("program_name") or NOT_PROGRAM}
            with REVIEW.open("a", encoding="utf-8") as fh:         # 하나씩 바로 적는다 — 멈춰도 한 만큼은 남는다
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        list(pool.map(one, enumerate(todo, 1)))
    print(f"판정 {tally} — 바뀐 판정은 다음 원본 장부 맞추기(170)에서 사업 묶음에 반영된다", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
