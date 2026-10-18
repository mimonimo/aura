"""수집 자료 — 베타·실사용에서 쌓이는 대화·평가·실행 기록을 한 목록으로 보고, 기간·항목을 골라 한 번에 내려받는다.

플랫폼 개선(검색·답변 품질 측정, 학습 데이터 후보, 오류 분석)에 쓸 자료가 DB 표와 기록 파일(jsonl) 여러 곳에 흩어져 있다.
여기 SOURCES 한 곳에만 적는다(열쇠·이름·설명 명사구·쓰임·있는 곳·시각 칸). 화면(/dev/collect)은 건수·기간·크기를 보인다.

내려받기 묶음(zip)은 늘 가린 사본이다 — 원문은 내보내지 않는다.
- 글 칸: 접수 문서와 같은 개인정보 검사기(PiiMasker)로 가리고 고위험 꼴(주민번호·전화·메일 등)을 한 번 더 쓸어 낸다.
  검사기가 실패한 글은 「[가림 실패]」로 바꾼다(가리지 못한 원문을 내보내지 않는다).
- 사람 칸(user·owner·by): 서버에만 있는 소금으로 만든 가명(u_…)으로 — 묶음마다 같은 사람은 같은 가명이라 이어 볼 수 있다.
- 나눔(split): 대화(세션) 단위로 train/eval 을 정한다(같은 대화가 두 쪽에 갈리지 않게). 평가 세트(data/eval)의 질문과 같은 질문이
  든 줄은 heldout 으로 — 학습에 섞이지 않게.
만들기는 뒤에서 돌고(오래 걸릴 수 있다) 끝난 묶음은 data/platform/exports/collect/ 에 쌓인다. 관리자(dev)만.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

log = logging.getLogger(__name__)
router = APIRouter()


@dataclass(frozen=True)
class Source:
    key: str
    name: str
    desc: str                 # 명사구 한 줄
    use: str                  # 쓰임 — 개선에 어떻게 쓰는가
    table: str = ""           # DB 표(질의) 또는
    file: str = ""            # data/platform 아래 기록 파일
    time_key: str = "created_at"


SOURCES: tuple[Source, ...] = (
    Source("chat_messages", "대화 메시지", "에이전트 채팅·업무 도우미의 질문과 답(대화 단위)", "답변 품질 검토·학습 후보",
           table="chat_messages"),
    Source("answer_runs", "답 실행 기록", "답마다 모델·걸린 시간·근거 문서·리즈닝 단계·근거 밖 숫자 표시", "속도·근거 분석",
           table="answer_runs"),
    Source("answer_feedback", "답 평가", "도움됨/아쉬움과 사유·메모", "실패 사례 모으기·평가 세트",
           table="answer_feedback"),
    Source("chat_sources", "대화 근거", "답에 붙은 근거 문서·대목", "검색 정답 후보", table="chat_sources"),
    Source("doc_agent", "문서 작업 에이전트 걸음", "요청·도구 고르기·관찰·마침(문서 작업 에이전트)", "리즈닝 학습 후보",
           file="doc_agent_episodes.jsonl", time_key="at"),
    Source("helper", "업무 도우미 걸음", "도우미의 요청·도구·제안", "도우미 개선", file="platform_helper_episodes.jsonl", time_key="at"),
    Source("drafting", "절 작성 기록", "절 작성 재료·지시·초안·점수", "작성 품질·학습 후보", file="drafting_episodes.jsonl", time_key="at"),
    Source("context_plan", "맥락 계획 기록", "절마다 찾을 맥락 계획과 찾은 것", "맥락 구성 리즈닝", file="context_plan_episodes.jsonl",
           time_key="at"),
    Source("form_choice", "양식 고르기 기록", "요청에 맞는 양식 후보와 고른 까닭", "양식 고르기 개선", file="form_choice_episodes.jsonl",
           time_key="at"),
    Source("fill_gaps", "못 채운 칸 기록", "채우지 못한 칸과 까닭·필요 자료", "자료 보강 목록", file="fill_gaps.jsonl", time_key="at"),
    Source("platform_actions", "도우미 실행 기록", "담당자가 [실행]을 누른 동작과 결과", "동작 오류 분석", file="platform_actions.jsonl",
           time_key="at"),
    Source("gdocs_audit", "구글 문서 작업 기록", "작업 문서 편집 동작·글자 수", "문서 편집 오류 분석", file="gdocs_audit.jsonl",
           time_key="at"),
)
BY_KEY = {s.key: s for s in SOURCES}

PERSON_KEYS = ("user", "owner", "by", "by_user")
SESSION_KEYS = ("session", "session_id")
QUESTION_KEYS = ("request", "question", "command")
SKIP_MASK = {"at", "created_at", "role", "kind", "verdict", "mode", "split", "op", "stop", "action", *PERSON_KEYS, *SESSION_KEYS}
MASK_FAILED = "[가림 실패]"


def out_dir(data_dir: Path) -> Path:
    d = Path(data_dir) / "exports" / "collect"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _salt(data_dir: Path) -> str:
    """가명 소금 — 서버에만 둔다(묶음에 넣지 않는다). 없으면 한 번 만든다."""
    p = Path(data_dir) / ".collect_salt"
    if not p.exists():
        p.write_text(secrets.token_hex(16), encoding="utf-8")
        p.chmod(0o600)
    return p.read_text(encoding="utf-8").strip()


def pseudonym(salt: str, who) -> str:
    if who in (None, ""):
        return ""
    return "u_" + hashlib.sha256(f"{salt}:{who}".encode("utf-8")).hexdigest()[:10]


def split_of(salt: str, session) -> str:
    if session in (None, ""):
        return "train"
    return "eval" if int(hashlib.sha256(f"{salt}:s:{session}".encode()).hexdigest(), 16) % 10 == 0 else "train"


def _norm(q: str) -> str:
    return re.sub(r"[\s\W_]+", "", q or "")


def eval_questions(data_dir: Path) -> set[str]:
    """평가 세트(data/eval/*.json)의 질문 — 학습 쪽에 섞이지 않게 heldout 으로 뺀다."""
    out: set[str] = set()
    root = Path(data_dir).parent / "eval"
    for p in sorted(root.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items = data if isinstance(data, list) else (data.get("items") or data.get("questions") or []) if isinstance(data, dict) else []
        for it in items:
            if isinstance(it, dict):
                q = it.get("question") or it.get("q") or it.get("query")
                if isinstance(q, str) and len(_norm(q)) >= 6:
                    out.add(_norm(q))
    return out


class Masker:
    """글 가리기 — 같은 글은 한 번만 검사한다. 검사기를 못 쓰면 가리지 못한 글을 내보내지 않는다."""

    def __init__(self):
        self._cache: dict[str, str] = {}
        self._m = None
        self.failed = 0
        try:
            from zzaimy.ingest.pii import PiiMasker, RawDocument
            self._m, self._raw = PiiMasker(), RawDocument
        except Exception:
            log.warning("개인정보 검사기를 불러오지 못했다 — 글 칸은 모두 「%s」", MASK_FAILED)
        try:
            from zzaimy.app.egress import _HIGH_RISK_PATTERNS
            self._sweep = list(_HIGH_RISK_PATTERNS)
        except Exception:
            self._sweep = []

    def __call__(self, text: str) -> str:
        if not text or len(text) < 2:
            return text
        if text in self._cache:
            return self._cache[text]
        if self._m is None:
            self.failed += 1
            return MASK_FAILED
        try:
            out = self._m.mask(self._raw(doc_id="collect", text=text))[0].text
            for label, pat in self._sweep:
                out = pat.sub(f"[{label}]", out)
        except Exception:
            self.failed += 1
            out = MASK_FAILED
        self._cache[text] = out
        return out


def _walk(v, key: str, mask: Masker, salt: str):
    if isinstance(v, dict):
        return {k: _walk(x, k, mask, salt) for k, x in v.items()}
    if isinstance(v, list):
        return [_walk(x, key, mask, salt) for x in v]
    if key in PERSON_KEYS:
        return pseudonym(salt, v)
    if isinstance(v, str) and key not in SKIP_MASK:
        return mask(v)
    return v


def _db_rows(db, s: Source, since: str, until: str) -> Iterator[dict]:
    from zzaimy.app.database_backend import table_names
    with db._conn() as conn:
        if s.table not in table_names(conn):
            return
        if s.table == "chat_messages":
            sql = ("SELECT m.*, c.owner AS owner, c.channel AS channel, c.project_id AS project_id FROM chat_messages m"
                   " LEFT JOIN chat_sessions c ON c.id = m.session_id WHERE m.created_at >= ? AND m.created_at < ? ORDER BY m.id")
        else:
            sql = f"SELECT * FROM {s.table} WHERE created_at >= ? AND created_at < ? ORDER BY id"
        rows = [dict(r) for r in conn.execute(sql, (since, until)).fetchall()]
    for r in rows:
        for k in ("model", "timing", "sources", "steps", "reasons"):
            if isinstance(r.get(k), str) and r[k][:1] in "[{":
                try:
                    r[k] = json.loads(r[k])
                except ValueError:
                    pass
        yield r


def _file_rows(data_dir: Path, s: Source, since: str, until: str) -> Iterator[dict]:
    p = Path(data_dir) / s.file
    if not p.is_file():
        return
    with p.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and since <= str(r.get(s.time_key) or "") < until:
                yield r


def rows(db, data_dir: Path, s: Source, since: str = "", until: str = "9999") -> Iterator[dict]:
    yield from (_db_rows(db, s, since, until) if s.table else _file_rows(data_dir, s, since, until))


def _short_span(first: str, last: str, this_year: str) -> str:
    """기간을 짧게 — 올해면 월-일만(「09-24 ~ 10-18」), 해가 다르면 연도까지."""
    a, b = first[:10], last[:10]
    if not a:
        return ""
    if a[:4] == b[:4] == this_year:
        a, b = a[5:], b[5:]
    return a if a == b else f"{a} ~ {b}"


def catalog(db, data_dir: Path) -> list[dict]:
    """화면 목록 — 항목마다 건수·처음·마지막 시각·크기(기록 파일)·날짜별 건수(기간을 고르면 화면이 바로 센다)."""
    from zzaimy.app.database_backend import table_names
    this_year = time.strftime("%Y")
    out = []
    for s in SOURCES:
        n, first, last, size, days = 0, "", "", None, {}
        try:
            if s.table:
                with db._conn() as conn:
                    if s.table in table_names(conn):
                        n, first, last = conn.execute(
                            f"SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM {s.table}").fetchone()
                        days = {str(d): int(c) for d, c in conn.execute(
                            f"SELECT substr(created_at, 1, 10), COUNT(*) FROM {s.table} GROUP BY 1").fetchall() if d}
            else:
                p = Path(data_dir) / s.file
                if p.is_file():
                    size = p.stat().st_size
                    times = [str(r.get(s.time_key) or "") for r in _file_rows(data_dir, s, "", "9999")]
                    times = [t for t in times if t]
                    n = len(times)
                    first, last = (min(times), max(times)) if times else ("", "")
                    for t in times:
                        days[t[:10]] = days.get(t[:10], 0) + 1
        except Exception:
            log.exception("수집 자료 세기 실패: %s", s.key)
        first, last = str(first or ""), str(last or "")
        out.append({"key": s.key, "name": s.name, "desc": s.desc, "use": s.use, "where": s.table or s.file,
                    "n": int(n or 0), "first": first[:10], "last": last[:16].replace("T", " "), "bytes": size,
                    "span": _short_span(first, last, this_year), "days": days})
    return out


PERIODS = (("all", "전체"), ("today", "오늘"), ("7d", "최근 7일"), ("30d", "최근 30일"), ("month", "이번 달"), ("custom", "직접 지정"))


def resolve_period(period: str, since: str, until: str, today: str) -> tuple[str, str]:
    """기간 고르기 → (시작일, 끝일). 빈 값은 처음·끝까지(전체). period 가 없으면 since·until 을 그대로 쓴다."""
    from datetime import date, timedelta
    t = date.fromisoformat(today)
    if period == "all":
        return "", ""
    if period == "today":
        return today, today
    if period in ("7d", "30d"):
        return (t - timedelta(days=int(period[:-1]) - 1)).isoformat(), today
    if period == "month":
        return t.replace(day=1).isoformat(), today
    since, until = since.strip()[:10], until.strip()[:10]
    if since and until and since > until:
        since, until = until, since
    return since, until


def build(db, data_dir: Path, keys: list[str], since: str, until: str, by: str = "") -> Path:
    """가린 묶음(zip) — 항목마다 <열쇠>.jsonl 과 manifest.json. 끝난 파일 경로를 돌려준다."""
    salt, mask, held = _salt(data_dir), Masker(), eval_questions(data_dir)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = out_dir(data_dir) / f"collect-{stamp}.zip"
    part = dest.with_suffix(".part")
    manifest = {"made_at": time.strftime("%Y-%m-%d %H:%M:%S"), "made_by": pseudonym(salt, by), "period": [since or "처음", until or "끝"],
                "masking": "글 칸 PiiMasker + 고위험 꼴 쓸기, 실패 시 「[가림 실패]」", "people": "서버 소금 가명(u_…) — 묶음 사이에 같은 사람은 같은 가명",
                "split": "대화(세션) 단위 train 9 : eval 1, 평가 세트(data/eval) 질문과 같으면 heldout", "sources": {}}
    with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as zf:
        for key in keys:
            s = BY_KEY.get(key)
            if s is None:
                continue
            n, splits = 0, {"train": 0, "eval": 0, "heldout": 0}
            with zf.open(f"{s.key}.jsonl", "w") as fh:
                for r in rows(db, data_dir, s, since, until or "9999"):
                    sess = next((r.get(k) for k in SESSION_KEYS if r.get(k) not in (None, "")), None)
                    sp = split_of(salt, sess)
                    q = next((r.get(k) for k in QUESTION_KEYS if isinstance(r.get(k), str)), None)
                    if q is None and r.get("role") == "user":
                        q = r.get("content")
                    if q and _norm(q) in held:
                        sp = "heldout"
                    out = _walk(r, "", mask, salt)
                    out["split"] = sp
                    if sess not in (None, ""):
                        for k in SESSION_KEYS:
                            if k in out:
                                out[k] = "s_" + hashlib.sha256(f"{salt}:sess:{sess}".encode()).hexdigest()[:10]
                    fh.write((json.dumps(out, ensure_ascii=False) + "\n").encode("utf-8"))
                    n += 1
                    splits[sp] += 1
            manifest["sources"][s.key] = {"name": s.name, "desc": s.desc, "use": s.use, "rows": n, "split": splits}
        manifest["mask_failed"] = mask.failed
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    part.replace(dest)
    return dest


_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _st(request: Request):
    if getattr(request.state, "role", "") != "dev":
        raise HTTPException(403, "개발자 계정 전용입니다")
    return request.app.state


def _data_dir(request: Request) -> Path:
    return Path(request.app.state.db.path).parent


def made(data_dir: Path) -> list[dict]:
    out = []
    for p in sorted(out_dir(data_dir).glob("collect-*.zip"), reverse=True)[:20]:
        info = {}
        try:
            with zipfile.ZipFile(p) as zf:
                info = json.loads(zf.read("manifest.json"))
        except Exception:
            pass
        out.append({"name": p.name, "bytes": p.stat().st_size, "made_at": info.get("made_at", ""),
                    "period": " ~ ".join(info.get("period") or []), "rows": sum((v or {}).get("rows", 0) for v in (info.get("sources") or {}).values()),
                    "items": ", ".join((v or {}).get("name", k) for k, v in (info.get("sources") or {}).items()),
                    "mask_failed": info.get("mask_failed", 0)})
    return out


@router.get("/dev/collect", response_class=HTMLResponse)
def collect_page(request: Request):
    st = _st(request)
    d = _data_dir(request)
    with _lock:
        running = [j for j in _jobs.values() if j["state"] == "running"]
        failed = [j for j in _jobs.values() if j["state"] == "failed"][-1:]
    sources = catalog(st.db, d)
    sources.sort(key=lambda c: not c["n"])                          # 빈 항목은 아래로(접어 둔다)
    firsts = [c["first"] for c in sources if c["first"]]
    return st.templates.TemplateResponse(request, "dev_collect.html", st.page_ctx(request, {
        "request": request, "sources": sources, "made": made(d), "running": running, "failed": failed,
        "periods": PERIODS, "today": time.strftime("%Y-%m-%d"), "earliest": min(firsts) if firsts else time.strftime("%Y-%m-%d"),
        "days": {c["key"]: c["days"] for c in sources if c["n"]}}))


@router.post("/dev/collect/make")
def collect_make(request: Request, keys: list[str] = Form([]), since: str = Form(""), until: str = Form(""),
                 period: str = Form("")):
    st = _st(request)
    keys = [k for k in keys if k in BY_KEY]
    if not keys:
        return RedirectResponse("/dev/collect?err=" + "항목을 하나 이상 고르세요", status_code=303)
    since, until = resolve_period(period, since, until, time.strftime("%Y-%m-%d"))
    until = (until + "T99") if until else ""       # 끝 날짜 그날까지 넣는다
    d, db, by = _data_dir(request), st.db, getattr(request.state, "user", "") or ""
    jid = secrets.token_hex(4)
    with _lock:
        if any(j["state"] == "running" for j in _jobs.values()):
            return RedirectResponse("/dev/collect?err=" + "이미 만드는 중입니다", status_code=303)
        _jobs[jid] = {"state": "running", "keys": keys, "at": time.strftime("%H:%M")}

    def run():
        try:
            build(db, d, keys, since, until, by)
            state, err = "done", ""
        except Exception as e:
            log.exception("수집 자료 묶음 만들기 실패")
            state, err = "failed", type(e).__name__
        with _lock:
            _jobs[jid].update(state=state, err=err)
    threading.Thread(target=run, name="collect-export", daemon=True).start()
    log.info("수집 자료 묶음 만들기 user=%s keys=%s %s~%s", by, ",".join(keys), since, until)
    return RedirectResponse("/dev/collect", status_code=303)


@router.get("/dev/collect/file/{name}")
def collect_file(request: Request, name: str):
    _st(request)
    if not re.fullmatch(r"collect-\d{8}-\d{6}\.zip", name):
        raise HTTPException(404)
    p = out_dir(_data_dir(request)) / name
    if not p.is_file():
        raise HTTPException(404)
    log.info("수집 자료 묶음 내려받기 user=%s file=%s", getattr(request.state, "user", ""), name)
    return FileResponse(p, media_type="application/zip", filename=name)
