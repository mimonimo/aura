#!/usr/bin/env python3
"""DGX 원본 보관소의 문서를 DGX 에서 가볍게 처리 — 문서함과 같은 처리기(DocumentProcessor)로 파싱·조각까지만(검토 의견 LLM·비전 판독 없이).

사용자 2026-10-02: "해당 부분 분석한 자료라던지 이런건 VM 에도 다 있어야 … RAG 나 온톨로지, 문서 분류 이런거, 문서함에도 떠야".
원본은 DGX 에 두고(ADR-0047), 결과(글·조각·분류)만 JSONL 로 낸다 → VM 에서 scripts/168 이 'DGX 보관 문서'로 문서함에 들인다.

입력: scripts/165 원본 목록(JSONL)과 이미 들인 원본 경로 목록(--skip, 한 줄에 하나).
대상: 글이 있는 형식(hwp·hwpx·pdf·docx·xlsx), 이름·크기 중복 아님, 이미 들이지 않음. 작업자마다 따로 SQLite 를 임시로 쓴다.
출력: --out 디렉터리에 작업자별 parsed-<n>.jsonl (한 줄 = 문서 하나: rel·분류·글·조각)

사용(DGX): ZZAIMY_LIGHT_PROCESS=1 PYTHONPATH=src .venv-parse/bin/python scripts/167_dgx_parse.py \\
           --inventory ~/archive_inventory.jsonl --skip ~/ingested.txt --out ~/parsed --workers 12 [--limit 500] [--retry-failed]
끝에 대상·기록·미처리·상태별 수를 내고, 작업자가 비정상 종료했거나 미처리가 있으면 PARSE_INCOMPLETE·종료 코드 1.
"""
from __future__ import annotations

import argparse
import json
import re
import multiprocessing as mp
import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

TEXT_EXT = {"hwp", "hwpx", "pdf", "docx", "xlsx", "pptx", "xls", "doc", "ppt"}   # 옛 오피스는 처리기가 LibreOffice 로 바꿔 읽는다
MAX_BYTES = 300 * 1024 * 1024
MAX_TEXT = 2_000_000       # 본문 상한(글자) — 넘으면 잘린 것을 기록에 남긴다(조각은 따로 전부 낸다)
# 처리 상태 — 가벼운 처리 완료는 OCR 품질 통과·학습 승인이 아니다(C-183). 후속 단계는 이 값으로 가른다
#   parsed: 글·조각 있음 · partial: 일부만 읽음(쪽 일부 판독·본문 잘림) · empty: 처리됐지만 글 없음 · failed: 오류·시간 초과
QUALITY = "unchecked"      # OCR 품질 관문은 아직 거치지 않았다(171 대결 뒤 정한다)


class DocTimeout(Exception):
    pass


def _alarm(_sig, _frm):
    raise DocTimeout()


_ENV_ERR = re.compile(r"ModuleNotFoundError|LocalEntryNotFoundError|ImportError")


def jsonl_lines(path) -> "list[str]":
    """JSONL 줄 — 「\\n」으로만 나눈다. str.splitlines 는 본문 속 폼피드(\\x0c, PDF 글에 흔하다)·\\u2028 에서도 잘라
    기록을 깨뜨렸다(10/5 실측: parsed-0 7,019건 중 51건이 안 보여 같은 원본을 매번 다시 처리, 집계는 「기록 0」)."""
    with open(path, encoding="utf-8", newline="") as fh:
        return [ln.rstrip("\r\n") for ln in fh]


def version(it: dict) -> str:
    """원본 판 — 크기·수정 시각. 같은 경로라도 판이 다르면 다시 처리한다."""
    return f"{int(it.get('size') or 0)}:{int(float(it.get('mtime') or 0))}"


MINERU_SLOTS = int(os.environ.get("ZZAIMY_MINERU_SLOTS", "2"))
MAX_RSS_GB = float(os.environ.get("ZZAIMY_WORKER_MAX_RSS_GB", "4"))
RECYCLE = 3                      # 작업자가 메모리 상한을 넘겨 스스로 끝냄 — main 이 그 자리부터 새 작업자로 잇는다


def _rss_gb() -> float:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return 0.0
MIN_FREE_GB = float(os.environ.get("ZZAIMY_MIN_FREE_GB", "24"))


def _mem_available_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return 1e9


def _wait_for_memory(n: int) -> None:
    """공용 장비(DGX, 통합 메모리) — 가용 메모리가 하한 밑이면 여유가 생길 때까지 기다린다.
    2026-10-02 실측: 작업자 14개가 MinerU 서버를 하나씩 띄워 121GB 를 다 쓰고 SSH 가 멎었다."""
    waited = 0
    while _mem_available_gb() < MIN_FREE_GB:
        if waited % 300 == 0:
            print(f"[w{n}] 가용 메모리 {_mem_available_gb():.0f}GB < {MIN_FREE_GB:.0f}GB — 기다림", flush=True)
        time.sleep(15)
        waited += 15


def _lock_dir() -> Path:
    """잠금 파일 자리 — 공용 장비(DGX)의 /tmp 는 다른 사용자도 쓴다. 본인만 쓰는 디렉터리(700)에 둔다."""
    d = Path(os.environ.get("ZZAIMY_LOCK_DIR") or Path.home() / ".cache" / "zzaimy-locks")
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _open_lock(path: Path):
    """링크를 따라가지 않고, 내용을 지우지 않고 연다(O_NOFOLLOW, 잘라 내기 없음)."""
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    return os.fdopen(fd, "r+")


def _limit_mineru() -> None:
    """MinerU(판독 모델을 프로세스마다 GPU 에 올린다)는 작업자 수와 상관없이 MINERU_SLOTS 개만 동시에."""
    import fcntl
    from zzaimy.ingest.parsers import mineru as _m

    orig = _m.MineruParser.parse

    def parse(self, *a, **kw):
        while True:
            for i in range(MINERU_SLOTS):
                fh = _open_lock(_lock_dir() / f"mineru_slot{i}.lock")
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    fh.close()
                    continue
                try:
                    return orig(self, *a, **kw)
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
                    fh.close()
            time.sleep(2)
    _m.MineruParser.parse = parse


def worker(n: int, items: list[dict], data_root: str, out_dir: str, timeout_s: int, start: int = 0) -> None:
    """문서마다 제한 시간(timeout_s, SIGALRM) — 넘으면 그 문서는 failed(시간 초과)로 적고 다음으로.
    파이썬으로 돌아오지 않는 네이티브 코드 안에서 멈추면 알람이 늦게 걸린다 — 그때는 작업자 종료 코드와
    미처리 건수로 드러난다(main 이 집계)."""
    os.environ["ZZAIMY_LIGHT_PROCESS"] = "1"
    os.environ.setdefault("ZZAIMY_NO_VISION", "1")
    work = Path(out_dir) / f"w{n}"
    work.mkdir(parents=True, exist_ok=True)
    # tmp.db 는 한 문서를 읽는 동안만 쓰는 임시 자리다. 지난 실행 기록이 남아 있으면 강제 재처리(--force) 때 같은 파일이
    # 자기 옛 기록과 「같은 내용의 문서가 이미 있습니다」로 걸린다(10/4 실측: kordoc 재처리 762건이 이렇게 빠짐).
    # 중복 판정은 운영 쪽(168·문서함)이 한다 — 여기서는 매번 비우고, 문서마다 읽은 뒤 지운다.
    for suffix in ("", "-wal", "-shm", "-journal"):
        (work / f"tmp.db{suffix}").unlink(missing_ok=True)
    os.environ["ZZAIMY_PLATFORM_SQLITE_PATH"] = str(work / "tmp.db")
    os.environ.pop("ZZAIMY_DATABASE_URL", None)
    from zzaimy.app.db import Database
    from zzaimy.app.pipeline import DocumentProcessor

    db = Database(work / "tmp.db")
    _limit_mineru()
    proc = DocumentProcessor()
    out = (Path(out_dir) / f"parsed-{n}.jsonl").open("a", encoding="utf-8")
    signal.signal(signal.SIGALRM, _alarm)
    progress = work / "progress"
    for idx in range(start, len(items)):
        it = items[idx]
        src = Path(data_root) / it["rel"]
        t0 = time.time()
        rec = {k: it.get(k) for k in ("rel", "program", "program_name", "status", "kind", "year", "round", "size", "mtime")}
        rec.update({"version": version(it), "quality": QUALITY})
        if it.get("force"):
            rec["force"] = True
        _wait_for_memory(n)
        signal.alarm(max(1, int(timeout_s)))
        did = None
        try:
            did = db.add_document(filename=src.name, stored_path=str(src), doc_type="grant", sector="grant")
            proc.process(db, did, src)
            signal.alarm(0)
            d = db.get_document(did) or {}
            chunks = [{k: c.get(k) for k in ("seq", "kind", "content", "page_no", "bbox")} for c in db.list_doc_chunks(did)]
            text = d.get("masked_text") or ""
            note = d.get("parse_note") or ""
            truncated = len(text) > MAX_TEXT
            if d.get("status") != "reviewed":
                state = "failed"
            elif not chunks:
                state = "empty"
            elif truncated or "일부" in note:
                state = "partial"
            else:
                state = "parsed"
            rec.update({"ok": state in ("parsed", "partial"), "state": state, "error": d.get("error"),
                        "filename": d.get("filename") or src.name, "parse_note": note,
                        "masked_text": text[:MAX_TEXT], "text_len": len(text), "truncated": truncated,
                        "doc_kind": d.get("kind"), "family": d.get("family"), "chunks": chunks})
        except DocTimeout:
            rec.update({"ok": False, "state": "failed", "error": f"시간 초과({timeout_s}초)", "chunks": []})
        except Exception as e:  # 한 문서 실패가 작업자를 멈추지 않게
            signal.alarm(0)
            rec.update({"ok": False, "state": "failed", "error": f"{type(e).__name__}: {e}"[:300], "chunks": []})
        if did is not None:
            try:
                db.delete_document(did)
            except Exception:
                pass
        rec["sec"] = round(time.time() - t0, 1)
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()
        progress.write_text(str(idx + 1))
        # 판독 도구가 메모리를 쌓는다(10/3 실측: 21시간 뒤 작업자 6개가 25GB, 가용 메모리 하한에 스스로 걸려 멈춤) — 상한을 넘으면 끝내고 새로 뜬다
        if idx + 1 < len(items) and _rss_gb() > MAX_RSS_GB:
            out.close()
            sys.exit(RECYCLE)
    out.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", required=True)
    ap.add_argument("--skip", default="")
    ap.add_argument("--root", default=str(Path.home() / "data"))
    ap.add_argument("--out", default=str(Path.home() / "parsed"))
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ext", default=",".join(sorted(TEXT_EXT)), help="처리할 형식(쉼표) — 엑셀은 docling 이 있어야 한다")
    ap.add_argument("--exclude", default="", help="경로가 이 정규식에 맞으면 뺀다 — 예: 지출·증빙 계열(절대 규칙 11 범위 밖)을 OCR 하지 않게")
    ap.add_argument("--only", default="", help="이 목록(한 줄에 원본 경로 하나)에 있는 원본만 — 스캔 PDF 핵심 문서처럼 범위를 좁혀 다시 돌릴 때")
    ap.add_argument("--force", action="store_true", help="이미 처리한 원본도 다시(읽기 도구가 나아졌을 때 — 예: kordoc 설치 뒤 한글). 168 이 같은 판이어도 같은 문서 번호로 갱신")
    ap.add_argument("--retry-failed", action="store_true", help="이전에 실패한 원본도 다시(판독 도구를 새로 깐 뒤). 성공하면 같은 rel 의 새 줄이 덧붙고 168 이 그것을 들인다")
    ap.add_argument("--dry-run", action="store_true", help="대상만 세어 보이고 끝낸다(처리하지 않음)")
    ap.add_argument("--doc-timeout", type=int, default=900, help="문서 한 건 제한 시간(초) — 넘으면 failed(시간 초과)로 적고 다음 문서로")
    args = ap.parse_args()
    if args.workers < 1:
        ap.error("--workers 는 1 이상")
    exts = {e.strip() for e in args.ext.split(",") if e.strip()}
    skip = set(Path(args.skip).read_text(encoding="utf-8").splitlines()) if args.skip and Path(args.skip).is_file() else set()
    done = set()
    for f in Path(args.out).glob("parsed-*.jsonl"):
        for line in jsonl_lines(f):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            # 같은 경로라도 판(크기·수정 시각)이 다르면 다시 처리한다 — 열쇠는 경로+판
            # 처리 환경 오류(모듈·판독 모델 파일 없음)는 문서 탓이 아니다 — 끝난 것으로 치지 않아 주기 처리가 저절로 다시 시도한다
            # (10/6: 환경이 깨져 있던 날의 실패 652건이 재처리 목록에서 빠져 남아 있었다)
            env_err = not rec.get("ok") and _ENV_ERR.search(str(rec.get("error") or ""))
            if rec.get("rel") and not args.force and (rec.get("ok") or not args.retry_failed) and not env_err:
                done.add((rec["rel"], rec.get("version") or version(rec)))
    only = set(Path(args.only).read_text(encoding="utf-8").splitlines()) if args.only else None
    import re as _re
    exclude = _re.compile(args.exclude) if args.exclude else None
    items = []
    for line in jsonl_lines(args.inventory):
        try:
            it = json.loads(line)
        except ValueError:
            continue
        if only is not None and it.get("rel") not in only:
            continue
        if exclude is not None and exclude.search(it.get("rel") or ""):
            continue
        if it.get("ext") not in exts or it.get("dup_of") or it["rel"] in skip or (it["rel"], version(it)) in done:
            continue
        if int(it.get("size") or 0) > MAX_BYTES or Path(it["rel"]).name.startswith("~$"):
            continue
        items.append(it)
    # 계획·보고·평가·기본계획을 먼저, 큰 파일은 뒤로
    pri = {"evaluation": 0, "plan": 1, "report": 2, "basic_plan": 3, "announcement": 4, "guideline": 5, "regulation": 6, "criteria": 7}
    items.sort(key=lambda it: (pri.get(it.get("kind") or "", 9), int(it.get("size") or 0)))
    if args.limit:
        items = items[: args.limit]
    print(f"대상 {len(items)}건(이미 처리 {len(done)}, 문서함에 있음 {len(skip)}) · 작업자 {args.workers}", flush=True)
    if args.dry_run:
        for it in items[:20]:
            print("  ", version(it), it["rel"], flush=True)
        return 0
    Path(args.out).mkdir(parents=True, exist_ok=True)
    shards = [items[i:: args.workers] for i in range(args.workers)]
    before = _counts(Path(args.out))
    if args.force:
        for it in items:
            it["force"] = True
    def spawn(i: int, start: int):
        p = mp.Process(target=worker, args=(i, shards[i], args.root, args.out, args.doc_timeout, start))
        p.start()
        return p

    running = {i: spawn(i, 0) for i, sh in enumerate(shards) if sh}
    bad: list[tuple[int, int | None]] = []
    recycled = 0
    while running:
        time.sleep(2)
        for i, p in list(running.items()):
            if p.is_alive():
                continue
            p.join()
            if p.exitcode == RECYCLE:                         # 메모리 상한 — 진행 위치부터 새 작업자
                prog = Path(args.out) / f"w{i}" / "progress"
                nxt = int(prog.read_text()) if prog.is_file() else 0
                running[i] = spawn(i, nxt)
                recycled += 1
                continue
            del running[i]
            if p.exitcode != 0:
                bad.append((i, p.exitcode))
    if recycled:
        print(f"메모리 상한({MAX_RSS_GB:g}GB)으로 작업자 새로 띄움 {recycled}회", flush=True)
    # 거짓 완료 막기(C-183) — 작업자 종료 코드와 이번 회차에 실제로 적힌 기록을 맞춰 본다
    after = _counts(Path(args.out))
    wrote = {k: after.get(k, 0) - before.get(k, 0) for k in set(after) | set(before)}
    n_written = sum(wrote.values())
    missing = len(items) - n_written
    print(f"대상 {len(items)} · 기록 {n_written} · 미처리 {missing} · 상태별 {dict(sorted(wrote.items()))}"
          + (f" · 비정상 종료 작업자 {bad}" if bad else ""), flush=True)
    if bad or missing > 0:
        print("PARSE_INCOMPLETE — 다시 실행하면 미처리분만 이어서 처리한다", flush=True)
        return 1
    print("PARSE_DONE", flush=True)
    return 0


def _counts(out_dir: Path) -> dict[str, int]:
    """결과 파일의 상태별 줄 수(이번 회차 전후를 견줘 실제로 처리된 수를 센다)."""
    c: dict[str, int] = {}
    for f in out_dir.glob("parsed-*.jsonl"):
        for line in jsonl_lines(f):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            st = r.get("state") or ("parsed" if r.get("ok") else "failed")
            c[st] = c.get(st, 0) + 1
    return c


if __name__ == "__main__":
    raise SystemExit(main())
