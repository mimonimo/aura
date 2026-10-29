"""수집 자료 — 베타·실사용에서 쌓이는 대화·평가·실행 기록을 한 목록으로 보고, 기간·항목을 골라 한 번에 내려받는다.

플랫폼 개선(검색·답변 품질 측정, 학습 데이터 후보, 오류 분석)에 쓸 자료가 DB 표와 기록 파일(jsonl) 여러 곳에 흩어져 있다.
여기 SOURCES 한 곳에만 적는다(열쇠·이름·설명 명사구·쓰임·있는 곳·시각 칸). 화면(데이터·학습의 「수집 자료」 탭, /dev/train?tab=collect)은 건수·기간·크기를 보인다.

내려받기 묶음(zip)은 늘 가린 사본이다 — 원문은 내보내지 않는다.
- 글 칸: 접수 문서와 같은 개인정보 검사기(PiiMasker)로 가리고 고위험 꼴(주민번호·전화·메일 등)을 한 번 더 쓸어 낸다.
  검사기가 실패한 글은 「[가림 실패]」로 바꾼다(가리지 못한 원문을 내보내지 않는다).
- 사람 칸(user·owner·by): 서버에만 있는 소금으로 만든 가명(u_…)으로 — 묶음마다 같은 사람은 같은 가명이라 이어 볼 수 있다.
- 나눔(split): 대화(세션) 단위로 train/eval 을 정한다(같은 대화가 두 쪽에 갈리지 않게). 평가 세트(data/eval)의 질문과 같은 질문이
  든 줄은 heldout 으로 — 학습에 섞이지 않게.
만들기는 뒤에서 돌고(오래 걸릴 수 있다) 끝난 묶음은 data/platform/exports/collect/ 에 쌓인다. 관리자(dev)만.

묶음 구조(뒤처리가 기댈 수 있게 고정, SCHEMA_VERSION):
- 항목마다 <열쇠>.jsonl 하나 — 한 줄 한 기록, 모든 줄이 같은 키를 같은 차례로 가진다(없는 값은 null). 맨 끝 키는 split.
- manifest.json — 항목마다 이름·건수·기간·필드(이름·형식·뜻·처리)와 가림·가명·나눔 규칙, 스키마 판.
화면의 「구조·표본 보기」는 같은 변환(가림·가명·나눔)을 거친 앞 3건과 필드 목록을 보인다.
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
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

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
    epoch: bool = False       # 표의 시각이 숫자(초)인 경우 — 기안기 공동 편집 표(at)
    past: str = ""            # 더 쌓이지 않는 옛 경로 기록이면 그 까닭(화면에 「지난 기록」 으로)


SOURCES: tuple[Source, ...] = (
    Source("chat_messages", "대화 메시지", "에이전트 채팅·업무 도우미의 질문과 답(대화 단위)", "답변 품질 검토·학습 후보",
           table="chat_messages"),
    Source("answer_runs", "답 실행 기록", "답마다 모델·걸린 시간·근거 문서·리즈닝 단계·근거 밖 숫자 표시", "속도·근거 분석",
           table="answer_runs"),
    Source("answer_feedback", "답 평가", "도움됨/아쉬움과 사유·메모", "실패 사례 모으기·평가 세트",
           table="answer_feedback"),
    Source("chat_sources", "대화 근거", "답에 붙은 근거 문서·대목", "검색 정답 후보", table="chat_sources"),
    Source("webhwp_edits", "한글 기안기 칸 고친 기록", "공동 편집 문서의 칸마다 고친 판(사람·AURA, 시각, 글). 30일 보관", "공동 편집 분석·작성 학습 후보",
           table="webhwp_field_edits", time_key="at", epoch=True),
    Source("doc_agent", "문서 작업 에이전트 기록", "요청·도구 고르기·관찰·마침(문서 작업 에이전트)", "리즈닝 학습 후보",
           file="doc_agent_episodes.jsonl", time_key="at"),
    Source("helper", "업무 도우미 기록", "도우미의 요청·도구·제안", "도우미 개선", file="platform_helper_episodes.jsonl", time_key="at"),
    Source("drafting", "절 작성 기록", "절 작성 재료·지시·초안·점수", "작성 품질·학습 후보", file="drafting_episodes.jsonl", time_key="at", past="옛 독스 작성 경로. 기안기 경로는 문서 작업 에이전트 기록에 남음"),
    Source("context_plan", "맥락 계획 기록", "절마다 찾을 맥락 계획과 찾은 것", "맥락 구성 리즈닝", file="context_plan_episodes.jsonl",
           time_key="at", past="옛 독스 작성 경로. 기안기 경로는 문서 작업 에이전트 기록에 남음"),
    Source("form_choice", "양식 고르기 기록", "요청에 맞는 양식 후보와 고른 까닭", "양식 고르기 개선", file="form_choice_episodes.jsonl",
           time_key="at", past="옛 독스 작성 경로. 기안기 경로는 문서 작업 에이전트 기록에 남음"),
    Source("fill_gaps", "못 채운 칸 기록", "채우지 못한 칸과 까닭·필요 자료", "자료 보강 목록", file="fill_gaps.jsonl", time_key="at", past="옛 독스 작성 경로. 기안기 경로는 문서 작업 에이전트 기록에 남음"),
    Source("platform_actions", "도우미 실행 기록", "담당자가 [실행]을 누른 동작과 결과", "동작 오류 분석", file="platform_actions.jsonl",
           time_key="at"),
    Source("gdocs_audit", "구글 문서 작업 기록", "작업 문서 편집 동작·글자 수", "문서 편집 오류 분석", file="gdocs_audit.jsonl",
           time_key="at", past="독스는 보조 경로라 거의 쌓이지 않음"),
)
BY_KEY = {s.key: s for s in SOURCES}

PERSON_KEYS = ("user", "owner", "by", "by_user")
SESSION_KEYS = ("session", "session_id")
QUESTION_KEYS = ("request", "question", "command")
SKIP_MASK = {"at", "created_at", "role", "kind", "verdict", "mode", "split", "op", "stop", "action", *PERSON_KEYS, *SESSION_KEYS}
MASK_FAILED = "[가림 실패]"
SCHEMA_VERSION = "collect/2"          # 묶음 구조를 바꾸면 올린다(1 = 줄마다 키가 달랐던 옛 묶음)
SAMPLE_N = 3                          # 화면 표본 줄 수
SCAN_N = 500                          # 화면 구조 보기 — 앞에서 이만큼 읽어 필드를 모은다(묶음은 전부)

# 필드 뜻 — 여러 기록에 두루 쓰이는 키 이름 하나에 한 줄. 여기 없는 키는 manifest 에 뜻을 비워 둔다
FIELD_NOTES: dict[str, str] = {
    "id": "기록 번호(표 안에서 하나)", "session_id": "대화 가명(s_…), 같은 대화는 같은 값", "session": "대화 가명(s_…), 같은 대화는 같은 값",
    "message_id": "답 메시지 번호(대화 메시지의 id)", "role": "말한 쪽(user 질문 · assistant 답)", "content": "메시지 글(가림)",
    "mode": "답변 방식(RAG·일반·인터넷 검색)", "created_at": "기록 시각", "at": "기록 시각",
    "owner": "대화 주인 가명(u_…)", "user": "사용한 사람 가명(u_…)", "by": "누른 사람 가명(u_…) 또는 고른 주체", "by_user": "사람 가명(u_…)",
    "channel": "대화 창 갈래(chat 채팅 · helper 도우미 등)", "project_id": "프로젝트 번호", "kind": "기록 갈래",
    "model": "답에 쓴 모델 정보", "llm": "부른 모델 정보(이름·주소 갈래)", "timing": "단계별 걸린 시간(초)",
    "answer_timing": "답 단계별 걸린 시간(초)", "sources": "근거 문서 목록", "steps": "에이전트 걸음(생각·도구·관찰)",
    "flagged": "근거 밖 숫자 표시(1 이면 있음)", "verdict": "평가(good 도움됨 · bad 아쉬움)", "reasons": "아쉬움 사유 목록",
    "note": "메모(가림)", "batch": "같은 답의 근거 묶음 번호", "doc_id": "문서 번호", "title": "제목(가림)",
    "heading": "절 제목(가림)", "snippet": "근거 대목(가림)", "origin": "근거 출처 갈래", "weak": "약한 근거(1 이면 약함)",
    "request": "요청 글(가림)", "question": "질문 글(가림)", "command": "지시 글(가림)", "page": "요청한 화면 주소",
    "finished": "끝까지 마쳤는지", "stop": "멈춘 까닭", "writes": "쓰기 동작 수", "run": "실행 번호",
    "graph_read": "그래프 질문 읽기를 켰는지", "doc": "작업 문서 식별자", "section": "절 제목(가림)",
    "materials": "작성 재료(가림)", "reply": "모델 답 글(가림)", "draft": "초안 동작 목록(가림)", "references": "참고 정답(가림)",
    "score": "자동 채점", "candidates": "양식 후보 목록", "choice": "고른 후보", "why": "고른 까닭(가림)", "rule": "규칙이 고른 후보",
    "plan": "찾을 맥락 계획", "found": "찾은 맥락과 근거", "reason": "못 채운 까닭 갈래", "needs": "더 필요한 자료",
    "chars": "글자 수", "cells": "채운 칸 수", "op": "동작 이름", "args": "동작 인자", "ok": "성공했는지", "error": "오류 글",
    "result": "결과 글(가림)", "changed": "바뀐 것 목록", "action": "편집 동작", "count": "바뀐 개수", "style": "글자 모양",
    "table": "표 번호", "caption": "그림 설명(가림)", "split": "나눔(train 학습 · eval 평가 · heldout 평가 세트 질문과 같음)",
}


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


def _session_alias(salt: str, sess) -> str:
    return "s_" + hashlib.sha256(f"{salt}:sess:{sess}".encode()).hexdigest()[:10]


def transform(r: dict, salt: str, mask: Masker, held: set[str]) -> dict:
    """기록 한 줄 → 내보내는 꼴: 글은 가리고, 사람·대화는 가명, 끝에 split. 묶음과 화면 표본이 같이 쓴다."""
    sess = next((r.get(k) for k in SESSION_KEYS if r.get(k) not in (None, "")), None)
    sp = split_of(salt, sess)
    q = next((r.get(k) for k in QUESTION_KEYS if isinstance(r.get(k), str)), None)
    if q is None and r.get("role") == "user":
        q = r.get("content")
    if q and _norm(q) in held:
        sp = "heldout"
    out = _walk(r, "", mask, salt)
    out.pop("split", None)
    if sess not in (None, ""):
        for k in SESSION_KEYS:
            if k in out:
                out[k] = _session_alias(salt, sess)
    out["split"] = sp
    return out


def type_of(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "list"
    return "object" if isinstance(v, dict) else "str"


def treatment(key: str, types: set[str]) -> str:
    """필드가 내보낼 때 어떻게 바뀌는지 — 가명·가림·그대로."""
    if key in PERSON_KEYS:
        return "가명(u_…)"
    if key in SESSION_KEYS:
        return "대화 가명(s_…)"
    if key == "split":
        return "묶을 때 붙임"
    if key not in SKIP_MASK and types & {"str", "list", "object"}:
        return "개인정보 가림" if "str" in types else "안의 글 가림"
    return "그대로"


class SchemaScan:
    """내보내는 줄들의 키·형식을 모아 고정 스키마(처음 나온 차례, split 은 맨 끝)를 만든다."""

    def __init__(self):
        self.types: dict[str, set[str]] = {}
        self.seen: dict[str, int] = {}
        self.n = 0

    def add(self, row: dict) -> None:
        self.n += 1
        for k, v in row.items():
            self.types.setdefault(k, set()).add(type_of(v))
            self.seen[k] = self.seen.get(k, 0) + 1

    def keys(self) -> list[str]:
        ks = [k for k in self.types if k != "split"]
        return ks + ["split"] if "split" in self.types else ks

    def fields(self) -> list[dict]:
        out = []
        for k in self.keys():
            ts = self.types[k] | ({"null"} if self.seen[k] < self.n else set())    # 어떤 줄엔 없는 키 → 묶음에서 null
            name = "|".join(sorted(ts - {"null"})) or "null"
            out.append({"name": k, "type": name + ("|null" if "null" in ts and name != "null" else ""),
                        "meaning": FIELD_NOTES.get(k, ""), "treatment": treatment(k, ts)})
        return out


def _epoch(stamp: str, default: float) -> float:
    """화면이 넘기는 날짜 글(YYYY-MM-DD…) → 초. 빈 글·「9999」 같은 끝 표시는 default."""
    from datetime import datetime
    try:
        d = datetime.fromisoformat(stamp)
        return (d if d.tzinfo else d.astimezone()).timestamp()
    except (TypeError, ValueError):
        return default


def _iso(v) -> str:
    from datetime import datetime
    try:
        return datetime.fromtimestamp(float(v)).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return str(v or "")


def _db_rows(db, s: Source, since: str, until: str, limit: int = 0) -> Iterator[dict]:
    from zzaimy.app.database_backend import table_names
    with db._conn() as conn:
        if s.table not in table_names(conn):
            return
        if s.table == "chat_messages":
            sql = ("SELECT m.*, c.owner AS owner, c.channel AS channel, c.project_id AS project_id FROM chat_messages m"
                   " LEFT JOIN chat_sessions c ON c.id = m.session_id WHERE m.created_at >= ? AND m.created_at < ? ORDER BY m.id")
        elif s.epoch:
            sql = f"SELECT * FROM {s.table} WHERE {s.time_key} >= ? AND {s.time_key} < ? ORDER BY id"
        else:
            sql = f"SELECT * FROM {s.table} WHERE created_at >= ? AND created_at < ? ORDER BY id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        args = (_epoch(since, 0.0), _epoch(until, 9e12)) if s.epoch else (since, until)
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    if s.epoch:
        for r in rows:
            r[s.time_key] = _iso(r.get(s.time_key))
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


def rows(db, data_dir: Path, s: Source, since: str = "", until: str = "9999", limit: int = 0) -> Iterator[dict]:
    """기간 안의 기록 — limit 을 주면 앞에서 그만큼만."""
    got = _db_rows(db, s, since, until, limit) if s.table else _file_rows(data_dir, s, since, until)
    for i, r in enumerate(got):
        if limit and i >= limit:
            break
        yield r


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
                    if s.table in table_names(conn) and s.epoch:
                        stamps = [_iso(r[0]) for r in conn.execute(f"SELECT {s.time_key} FROM {s.table}").fetchall()]
                        stamps = sorted(x for x in stamps if x)
                        n, first, last = len(stamps), (stamps[0] if stamps else ""), (stamps[-1] if stamps else "")
                        for x in stamps:
                            days[x[:10]] = days.get(x[:10], 0) + 1
                    elif s.table in table_names(conn):
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
                    "span": _short_span(first, last, this_year), "days": days, "past": s.past})
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


def _manifest_head(salt: str, by: str, since: str, until: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "made_at": time.strftime("%Y-%m-%d %H:%M:%S"), "made_by": pseudonym(salt, by),
        "period": [since[:10] or "처음", until[:10] or "끝"],
        "format": "항목마다 <열쇠>.jsonl — UTF-8, 한 줄 한 기록. 모든 줄이 fields 의 키를 같은 차례로 가진다(없는 값은 null), 맨 끝은 split",
        "masking": {"text": "글 칸은 접수 문서와 같은 개인정보 검사기(PiiMasker)로 가리고 고위험 꼴(주민번호·전화·메일 등)을 한 번 더 쓸어 냄",
                    "failed": f"검사기가 실패한 글은 「{MASK_FAILED}」로 바꿈(원문을 내보내지 않음)",
                    "kept": sorted(SKIP_MASK - set(PERSON_KEYS) - set(SESSION_KEYS))},
        "pseudonymization": {"people": "사람 칸(" + "·".join(PERSON_KEYS) + ")은 서버 소금 가명 u_… — 묶음 사이에 같은 사람은 같은 가명",
                             "sessions": "대화 칸(" + "·".join(SESSION_KEYS) + ")은 서버 소금 가명 s_… — 같은 대화는 같은 값",
                             "salt": "서버에만 둠(묶음에 없음)"},
        "split_rule": {"unit": "대화(세션) — 같은 대화가 두 쪽에 갈리지 않게", "ratio": "train 9 : eval 1",
                       "method": "sha256(소금:s:대화) 를 10 으로 나눈 나머지가 0 이면 eval, 대화가 없는 줄은 train",
                       "heldout": "평가 세트(data/eval) 질문과 같은 질문이 든 줄은 heldout — 학습에 섞지 않음"},
        "sources": {},
    }


def build(db, data_dir: Path, keys: list[str], since: str, until: str, by: str = "") -> Path:
    """가린 묶음(zip) — 항목마다 <열쇠>.jsonl(고정 스키마)과 manifest.json. 끝난 파일 경로를 돌려준다.

    항목마다 두 번 지난다: 가린 줄을 임시 파일에 쓰며 키·형식을 모으고, 스키마를 정한 뒤 같은 차례로 다시 써 넣는다.
    """
    salt, mask, held = _salt(data_dir), Masker(), eval_questions(data_dir)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = out_dir(data_dir) / f"collect-{stamp}.zip"
    part = dest.with_suffix(".part")
    tmp = dest.with_suffix(".rows")
    manifest = _manifest_head(salt, by, since, until)
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as zf:
            for key in keys:
                s = BY_KEY.get(key)
                if s is None:
                    continue
                n, splits, scan, first, last = 0, {"train": 0, "eval": 0, "heldout": 0}, SchemaScan(), "", ""
                with tmp.open("w", encoding="utf-8") as fh:
                    for r in rows(db, data_dir, s, since, until or "9999"):
                        t = str(r.get(s.time_key) or "")
                        if t:
                            first, last = min(first or t, t), max(last, t)
                        out = transform(r, salt, mask, held)
                        scan.add(out)
                        fh.write(json.dumps(out, ensure_ascii=False) + "\n")
                        n += 1
                        splits[out["split"]] += 1
                ks = scan.keys()
                with zf.open(f"{s.key}.jsonl", "w") as zfh, tmp.open(encoding="utf-8") as fh:
                    for line in fh:
                        row = json.loads(line)
                        zfh.write((json.dumps({k: row.get(k) for k in ks}, ensure_ascii=False) + "\n").encode("utf-8"))
                manifest["sources"][s.key] = {
                    "file": f"{s.key}.jsonl", "name": s.name, "desc": s.desc, "use": s.use, "where": s.table or s.file,
                    "rows": n, "time_field": s.time_key,
                    "period": {"first": first[:19].replace("T", " "), "last": last[:19].replace("T", " ")},
                    "fields": scan.fields(), "split": splits}
            manifest["mask_failed"] = mask.failed
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        part.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
        part.unlink(missing_ok=True)
    return dest


def peek(db, data_dir: Path, key: str) -> dict:
    """화면 「구조·표본 보기」 — 앞 SCAN_N 줄로 모은 필드 목록과 앞 SAMPLE_N 줄(묶음과 같은 가림·가명·나눔).

    구조는 원래 줄에서 읽되 내보낼 때 바뀌는 형식(가명은 글)으로 적는다 — 500줄을 다 가리지 않으려고.
    """
    s = BY_KEY[key]
    salt, mask, held = _salt(data_dir), Masker(), eval_questions(data_dir)
    scan, raw, n = SchemaScan(), [], 0
    for r in rows(db, data_dir, s, limit=SCAN_N):
        n += 1
        if len(raw) < SAMPLE_N:
            raw.append(r)
        scan.add({k: ("" if k in PERSON_KEYS or (k in SESSION_KEYS and v not in (None, "")) else v) for k, v in r.items()}
                 | {"split": "train"})
    sample = [transform(r, salt, mask, held) for r in raw]
    ks = scan.keys()
    return {"key": s.key, "name": s.name, "fields": scan.fields(), "sample": [{k: r.get(k) for k in ks} for r in sample],
            "scanned": n, "scan_limit": SCAN_N, "mask_failed": mask.failed}


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


PAGE = "/dev/train?tab=collect"       # 화면은 데이터·학습의 한 탭(10-27) — 만들기·내려받기·구조 보기는 /dev/train/collect/…(옛 /dev/collect/… 는 10-27 에 지움)


def _back(err: str = "") -> RedirectResponse:
    return RedirectResponse(PAGE + (("&err=" + quote(err)) if err else ""), status_code=303)


def page_context(request: Request) -> dict:
    """데이터·학습 「수집 자료」 탭에 넣을 값 — 항목 목록(건수·기간), 만든 묶음, 만드는 중인 일."""
    st = _st(request)
    d = _data_dir(request)
    with _lock:
        running = [j for j in _jobs.values() if j["state"] == "running"]
        failed = [j for j in _jobs.values() if j["state"] == "failed"][-1:]
    sources = catalog(st.db, d)
    sources.sort(key=lambda c: not c["n"])                          # 빈 항목은 아래로(접어 둔다)
    firsts = [c["first"] for c in sources if c["first"]]
    return {"sources": sources, "made": made(d), "running": running, "failed": failed,
            "periods": PERIODS, "today": time.strftime("%Y-%m-%d"), "earliest": min(firsts) if firsts else time.strftime("%Y-%m-%d"),
            "days": {c["key"]: c["days"] for c in sources if c["n"]}}


@router.get("/dev/train/collect/peek/{key}")
def collect_peek(request: Request, key: str):
    st = _st(request)
    if key not in BY_KEY:
        raise HTTPException(404)
    return peek(st.db, _data_dir(request), key)


@router.post("/dev/train/collect/make")
def collect_make(request: Request, keys: list[str] = Form([]), since: str = Form(""), until: str = Form(""),
                 period: str = Form("")):
    st = _st(request)
    keys = [k for k in keys if k in BY_KEY]
    if not keys:
        return _back("항목을 하나 이상 고르세요")
    since, until = resolve_period(period, since, until, time.strftime("%Y-%m-%d"))
    until = (until + "T99") if until else ""       # 끝 날짜 그날까지 넣는다
    d, db, by = _data_dir(request), st.db, getattr(request.state, "user", "") or ""
    jid = secrets.token_hex(4)
    with _lock:
        if any(j["state"] == "running" for j in _jobs.values()):
            return _back("이미 만드는 중입니다")
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
    return _back()


@router.get("/dev/train/collect/file/{name}")
def collect_file(request: Request, name: str):
    _st(request)
    if not re.fullmatch(r"collect-\d{8}-\d{6}\.zip", name):
        raise HTTPException(404)
    p = out_dir(_data_dir(request)) / name
    if not p.is_file():
        raise HTTPException(404)
    log.info("수집 자료 묶음 내려받기 user=%s file=%s", getattr(request.state, "user", ""), name)
    return FileResponse(p, media_type="application/zip", filename=name)
