"""산출물 내려받기 — 온톨로지·지식그래프·RAG 목록·학습 데이터를 한 곳에서 정의하고 GET /dev/export/{key} 로 내보낸다.

항목은 ITEMS 하나에만 적는다(key·이름·설명 명사구·형식·묶음·만드는 함수·있음 확인). 화면(_exports.html)은 묶음 이름으로
그 항목만 「이름 · 형식 · 크기/건수 · 받기」 줄로 보이고, 산출물이 없으면 「아직 없음」으로 링크를 끈다.

- 관리자(role dev)만 — /dev 경로는 main.check_auth 가 막고, 여기서도 한 번 더 본다(인증 없는 로컬 모드는 늘 dev).
- 지식그래프(노드 42만·관계 59만)는 메모리에 다 올리지 않는다 — 기본 키 순서로 끊어 읽고(keyset) 쪽마다 gzip 으로 흘려 보낸다.
  PostgreSQL 의 보통 커서는 결과 전체를 받아 오므로 커서 하나로 오래 읽지 않고 쪽마다 짧게 읽는다.
- RAG 목록은 문서 단위 수치만 — 조각 본문(개인정보가 섞일 수 있는 글)은 내보내지 않는다.
- 학습 데이터는 새로 만들지 않는다 — /dev/train 의 반출 묶음(검수 승인본)을 가리킨다.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse

log = logging.getLogger(__name__)
router = APIRouter()

PAGE = 5000                     # 한 번에 읽는 행 수 — 쪽마다 연결을 짧게 쓴다
GROUPS = {"ontology": "온톨로지", "graph": "지식그래프", "rag": "RAG", "train": "학습 데이터"}
NONE = "아직 없음"


@dataclass
class Item:
    key: str
    name: str
    desc: str                   # 명사구 한 줄
    fmt: str
    group: str
    make: Callable              # (request) → Response
    info: Callable              # (request) → 크기/건수 글자, 없으면 None
    href: str = ""              # 다른 화면의 내려받기를 가리킬 때(학습 데이터)
    params: dict = field(default_factory=dict)   # 거르기 인자 설명(?type= / ?kind=)


# ---- 경로·공용 ----

def data_dir(request: Request) -> Path:
    """플랫폼 DB 파일이 있는 폴더 — 운영 PostgreSQL 이어도 경로 값은 data/platform/platform.db."""
    return Path(getattr(request.app.state.db, "path", "data/platform/platform.db")).parent


def ontology_dir(request: Request) -> Path:
    return data_dir(request) / "ontology"           # scripts/177 이 쓰는 곳


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return ""


def _file_info(p: Path) -> str | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return f"{_size(st.st_size)} · {datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M')}"


def _count(request: Request, sql: str, args: tuple = ()) -> int | None:
    try:
        with request.app.state.db._conn() as conn:
            return int(conn.execute(sql, args).fetchone()[0])
    except Exception:          # 표가 아직 없음(그래프를 짓기 전)
        return None


def _rows_text(n: int | None, unit: str = "행") -> str | None:
    return f"{n:,}{unit}" if n else None


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d")


def _csv_lines(header: list[str], pages: Iterable[list]) -> Iterator[str]:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    yield buf.getvalue()
    for page in pages:
        buf.seek(0)
        buf.truncate()
        w.writerows(page)
        yield buf.getvalue()


def _gzip(texts: Iterable[str]) -> Iterator[bytes]:
    z = zlib.compressobj(6, zlib.DEFLATED, 31)       # wbits 31 = gzip 머리·꼬리
    for t in texts:
        out = z.compress(t.encode("utf-8"))
        if out:
            yield out
    yield z.flush()


def _utf8_bom(texts: Iterable[str]) -> Iterator[bytes]:
    yield "﻿".encode("utf-8")                    # 엑셀이 한글을 깨뜨리지 않게
    for t in texts:
        yield t.encode("utf-8")


def _csv_response(name: str, header: list[str], pages: Iterable[list], gz: bool) -> StreamingResponse:
    lines = _csv_lines(header, pages)
    if gz:
        return StreamingResponse(_gzip(lines), media_type="application/gzip",
                                 headers={"Content-Disposition": f'attachment; filename="{name}.csv.gz"'})
    return StreamingResponse(_utf8_bom(lines), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


def _keyset(db, select: str, keys: tuple[str, ...], where: str = "", args: tuple = ()) -> Iterator[list]:
    """기본 키 순서로 PAGE 행씩 — 쪽마다 연결을 열고 닫는다(긴 읽기가 그래프 갱신의 쓰기를 막지 않게)."""
    last: tuple | None = None
    while True:
        conds, params = ([where], list(args)) if where else ([], [])
        if last is not None:
            conds.append(f"({', '.join(keys)}) > ({', '.join('?' * len(keys))})")
            params += list(last)
        sql = select + (" WHERE " + " AND ".join(conds) if conds else "") + f" ORDER BY {', '.join(keys)} LIMIT {PAGE}"
        with db._conn() as conn:
            rows = [tuple(r) for r in conn.execute(sql, params).fetchall()]
        if not rows:
            return
        yield rows
        if len(rows) < PAGE:
            return
        last = rows[-1][:len(keys)]


# ---- 온톨로지 ----

def _onto_file(name: str, media: str, fallback: Path | None = None):
    def pick(request: Request) -> Path | None:
        p = ontology_dir(request) / name
        if p.is_file():
            return p
        return fallback if fallback is not None and fallback.is_file() else None

    def make(request: Request):
        p = pick(request)
        if p is None:
            raise HTTPException(404, f"{name}: {NONE}")
        return FileResponse(p, media_type=media, filename=f"aura-{name}")

    def info(request: Request) -> str | None:
        p = pick(request)
        return _file_info(p) if p else None

    return make, info


def _shapes_src() -> Path:
    from zzaimy.graph import ontology_rdf
    return ontology_rdf.SHAPES


# ---- 지식그래프 ----

def _kg_nodes(request: Request):
    from zzaimy.graph import kg_store
    db = request.app.state.db
    kg_store.ensure(db)
    t = (request.query_params.get("type") or "").strip()
    pages = _keyset(db, "SELECT id, type, label, props, doc_id FROM kg_nodes", ("id",),
                    "type = ?" if t else "", (t,) if t else ())
    return _csv_response(f"aura-kg-nodes{'-' + t if t else ''}-{_stamp()}", ["id", "type", "label", "props", "doc_id"], pages, gz=True)


def _kg_edges(request: Request):
    from zzaimy.graph import kg_store
    db = request.app.state.db
    kg_store.ensure(db)
    k = (request.query_params.get("kind") or "").strip()
    pages = _keyset(db, "SELECT src, dst, kind, basis, evidence, weight FROM kg_edges", ("src", "dst", "kind"),
                    "kind = ?" if k else "", (k,) if k else ())
    return _csv_response(f"aura-kg-edges{'-' + k if k else ''}-{_stamp()}",
                         ["src", "dst", "kind", "basis", "evidence", "weight"], pages, gz=True)


def program_rows(db) -> list[list]:
    """사업 목록 — 그래프 탐색 왼쪽 목록과 같은 사업(kg_explore.programs, 관리자 범위)과 같은 대·중·소 분류(program_tree.build)."""
    from zzaimy.app import kg_explore
    from zzaimy.app import program_tree as pt
    from zzaimy.app.program_names import load_ledger

    nodes = kg_explore.programs(db, None)
    tree = pt.build([{**n, "key": n["id"], "n": n["n_docs"]} for n in nodes if n["type"] == "program"], load_ledger())
    out = [["사업 묶음", "", n["label"], n["label"], n["type"], ";".join(n["ids"]), n["n_docs"], ""]
           for n in nodes if n["type"] == "program_group"]
    for t in tree:
        for g in t["groups"]:
            for p in g["progs"]:
                out.append([t["label"], g["label"], p["label"], p.get("raw_label") or p["label"], p["type"],
                            ";".join(p.get("ids") or [p["id"]]), p["n_docs"], "예" if p.get("in_ledger") else "아니오"])
    return out


_PROG_HEAD = ["대분류", "중분류", "사업", "그래프 이름", "노드 종류", "노드 id", "문서 수", "장부 등재"]


def _programs(request: Request):
    rows = program_rows(request.app.state.db)
    return _csv_response(f"aura-programs-{_stamp()}", _PROG_HEAD, [rows], gz=False)


def _programs_info(request: Request) -> str | None:
    return _rows_text(_count(request, "SELECT COUNT(*) FROM kg_nodes WHERE type IN ('program', 'program_group')"), "개 노드")


# ---- RAG ----

_DOC_HEAD = ["id", "접수번호", "제목", "문서 유형", "갈래", "부서", "공개 등급", "대상", "본문 조각 수", "규정 조각 수", "임베딩 색인 조각 수"]
_REG_HEAD = ["doc_id", "제목", "부서", "공개 등급", "학생 공개", "조각 수", "임베딩 색인 조각 수"]


def _index_ids() -> frozenset | None:
    """임베딩 색인(규정 조각, scripts/52)의 조각 id — 색인이 없거나 읽지 못하면 None(칸을 비운다)."""
    try:
        from zzaimy.app.data_explorer import embedding_index
        return embedding_index().get("ids")
    except Exception:
        return None


def _reg_counts(db, ids: frozenset | None) -> tuple[dict, dict]:
    """문서별 (규정 조각 수, 그 가운데 색인에 든 수) — 조각 id 만 읽는다(본문 없이)."""
    total: dict[int, int] = {}
    indexed: dict[int, int] = {}
    with db._conn() as conn:
        for d, i in conn.execute("SELECT doc_id, id FROM regulation_chunks"):
            d = int(d)
            total[d] = total.get(d, 0) + 1
            if ids is not None and int(i) in ids:
                indexed[d] = indexed.get(d, 0) + 1
    return total, indexed


def _title(r: dict) -> str:
    from zzaimy.app.doc_title import display_title
    try:
        return display_title({"filename": r.get("filename") or "", "identity": r.get("identity")})   # 본문 없이 — 이름·식별 정보만
    except Exception:
        return r.get("filename") or ""


def _rag_docs(request: Request):
    db = request.app.state.db
    ids = _index_ids()
    reg_total, reg_idx = _reg_counts(db, ids)
    with db._conn() as conn:
        chunks = {int(d): int(n) for d, n in conn.execute("SELECT doc_id, COUNT(*) FROM doc_chunks GROUP BY doc_id")}
    cols = ("id", "receipt_no", "filename", "identity", "doc_type", "kind", "dept", "access_level", "audience")

    def pages():
        for page in _keyset(db, f"SELECT {', '.join(cols)} FROM documents", ("id",), "status = 'reviewed'"):
            out = []
            for row in page:
                r = dict(zip(cols, row))
                d = int(r["id"])
                out.append([d, r["receipt_no"] or "", _title(r), r["doc_type"] or "", r["kind"] or "", r["dept"] or "",
                            r["access_level"] or "", r["audience"] or "", chunks.get(d, 0), reg_total.get(d, 0),
                            "" if ids is None else reg_idx.get(d, 0)])
            yield out

    return _csv_response(f"aura-rag-documents-{_stamp()}", _DOC_HEAD, pages(), gz=False)


def _reg_docs(request: Request):
    db = request.app.state.db
    ids = _index_ids()
    _total, reg_idx = _reg_counts(db, ids)
    with db._conn() as conn:
        rows = [tuple(r) for r in conn.execute(
            "SELECT r.doc_id, MIN(r.reg_title), MIN(COALESCE(r.dept, '')), MIN(COALESCE(r.access_level, '')),"
            " MIN(COALESCE(d.audience, 'staff')), COUNT(*) FROM regulation_chunks r LEFT JOIN documents d ON d.id = r.doc_id"
            " GROUP BY r.doc_id ORDER BY r.doc_id")]
    out = [[int(d), t, dept, lvl, "예" if aud == "student" else "아니오", int(n), "" if ids is None else reg_idx.get(int(d), 0)]
           for d, t, dept, lvl, aud, n in rows]
    return _csv_response(f"aura-regulations-{_stamp()}", _REG_HEAD, [out], gz=False)


# ---- 학습 데이터 ----

TRAIN_HREF = "/dev/train/export.zip?rag=0&datasets=1&model=0"


def _train_info(request: Request) -> str | None:
    sft = Path("data/interim/sft")
    n = len(list(sft.glob("*.jsonl"))) if sft.is_dir() else 0
    return f"JSONL {n}개" if n else None


def _train(request: Request):
    return RedirectResponse(TRAIN_HREF, status_code=303)


# ---- 항목 정의(한 곳) ----

def _items() -> list[Item]:
    onto_m, onto_i = _onto_file("ontology.ttl", "text/turtle; charset=utf-8")
    inst_m, inst_i = _onto_file("instances.ttl", "text/turtle; charset=utf-8")
    shp_m, shp_i = _onto_file("shapes.ttl", "text/turtle; charset=utf-8", fallback=_shapes_src())
    rep_m, rep_i = _onto_file("shacl_report.json", "application/json")
    return [
        Item("ontology", "온톨로지 설계도", "클래스·하위 클래스·관계 정의역과 치역(OWL)", "Turtle (.ttl)", "ontology", onto_m, onto_i),
        Item("instances", "온톨로지 인스턴스", "사업 묶음·사업·기관·연차·문서·성과지표와 관계", "Turtle (.ttl)", "ontology", inst_m, inst_i),
        Item("shapes", "검사 규칙", "노드·관계 꼴 검사 규칙(SHACL)", "Turtle (.ttl)", "ontology", shp_m, shp_i),
        Item("shacl_report", "검사 결과", "규칙별 위반 수와 예시 노드", "JSON", "ontology", rep_m, rep_i),
        Item("kg_nodes", "지식그래프 노드", "노드 id·종류·이름·속성·문서 번호", "CSV (gzip)", "graph", _kg_nodes,
             lambda r: _rows_text(_count(r, "SELECT COUNT(*) FROM kg_nodes"), "개"), params={"type": "노드 종류"}),
        Item("kg_edges", "지식그래프 관계", "관계 양 끝·종류·기준·근거·가중치", "CSV (gzip)", "graph", _kg_edges,
             lambda r: _rows_text(_count(r, "SELECT COUNT(*) FROM kg_edges"), "개"), params={"kind": "관계 종류"}),
        Item("programs", "사업 목록", "사업과 장부 대·중·소 분류·문서 수", "CSV", "graph", _programs, _programs_info),
        Item("rag_docs", "검색 범위 문서 목록", "문서별 종류·부서·공개 등급·조각 수·색인 조각 수(본문 제외)", "CSV", "rag", _rag_docs,
             lambda r: _rows_text(_count(r, "SELECT COUNT(*) FROM documents WHERE status = 'reviewed'"), "건")),
        Item("regulations", "규정 기준 문서 목록", "규정별 제목·조각 수·학생 공개 여부(본문 제외)", "CSV", "rag", _reg_docs,
             lambda r: _rows_text(_count(r, "SELECT COUNT(DISTINCT doc_id) FROM regulation_chunks"), "건")),
        Item("train", "학습 데이터(승인본)", "검수 승인 학습 데이터 반출 묶음(/dev/train)", "ZIP", "train", _train, _train_info,
             href=TRAIN_HREF),
    ]


ITEMS: dict[str, Item] = {i.key: i for i in _items()}


def _require_dev(request: Request) -> None:
    if getattr(request.state, "role", "") != "dev":
        raise HTTPException(403, "개발자 계정 전용입니다")


def rows(request: Request, groups: Iterable[str]) -> list[dict]:
    """화면 줄 — 묶음 순서대로 {group, group_label, items:[{key, name, desc, fmt, info, href}]}. info 가 None 이면 「아직 없음」."""
    out = []
    for g in groups:
        g = g.strip()
        if g not in GROUPS:
            continue
        items = []
        for it in ITEMS.values():
            if it.group != g:
                continue
            try:
                info = it.info(request)
            except Exception:
                log.exception("내려받기 항목 확인 실패: %s", it.key)
                info = None
            items.append({"key": it.key, "name": it.name, "desc": it.desc, "fmt": it.fmt, "info": info,
                          "href": it.href or f"/dev/export/{it.key}", "params": it.params})
        out.append({"group": g, "group_label": GROUPS[g], "items": items})
    return out


@router.get("/dev/export", response_class=HTMLResponse)
def export_list(request: Request, groups: str = "ontology,graph,rag,train"):
    """묶음별 내려받기 줄(부분 화면) — _exports.html 이 불러 끼운다."""
    _require_dev(request)
    st = request.app.state
    return st.templates.TemplateResponse(request, "_exports.html", {"request": request,
                                                                    "export_rows": rows(request, groups.split(","))})


@router.get("/dev/export/{key}")
def export_one(request: Request, key: str):
    _require_dev(request)
    it = ITEMS.get(key)
    if it is None:
        raise HTTPException(404, "없는 내려받기 항목입니다")
    extra = {k: request.query_params.get(k) for k in it.params if request.query_params.get(k)}
    log.info("산출물 내려받기 key=%s user=%s%s", key, getattr(request.state, "user", ""),
             (" " + json.dumps(extra, ensure_ascii=False)) if extra else "")
    return it.make(request)
