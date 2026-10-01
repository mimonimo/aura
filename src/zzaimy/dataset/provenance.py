"""학습쌍의 근거 기록 — 입력(사람 턴)에 실제로 보이는 줄마다 어느 문서·조각에서 왔는지 단다(아스트라 C-135 품질 관문 연결).

품질 관문(`dataset/quality_gate.py`)은 쌍마다 program_id·node_path·evidence_records 를 요구하고, 근거 글이 그 턴에 글자 그대로
보여야 하며, 답의 수치는 그 근거 글들 안에 있어야 한다. 여기서는 생성기가 만든 입력을 줄 단위로 읽어, 줄의 글이 실제로 들어 있는
조각을 찾아 기록한다. 못 찾은 줄은 기록하지 않는다 — 추측으로 채우지 않으며, 그 줄의 수치는 관문에서 '근거 없음'으로 보류된다.
검수 판정(review)은 사람이 남기는 것이라 여기서 만들지 않는다.
"""

from __future__ import annotations

import json
import re

_GROUP_OF_HEADER = (
    (("양식 안내", "작성방법", "절 구조", "문서 본문", "양식 표", "양식의", "목차", "절이 다룰 항목", "문서 제목"), "form"),
    (("평가지표", "평가편람", "평가 착안점", "평가영역"), "criteria"),
    (("지난 사업 자료", "사실 목록", "넣을 값"), "done"),
    (("공고·기본계획", "사업명", "개요"), "notice"),
)
_HEADER = re.compile(r"^\[([^\]]{2,60})\]")
_LEAD = re.compile(r"^(?:\s*(?:-\s+|\d{1,2}[.)]\s+|r\d+:\s*|\[c\d+\]\s*|표\s*\d+\s*\([^)]*\)))+")


def _norm(s: str) -> str:
    return re.sub(r"[^\w%]", "", (s or "").lower())


def chunk_text(content: str) -> str:
    """조각 글 — 표 조각(JSON)은 칸 글을 이어서."""
    c = content or ""
    if c.startswith("{") and '"cells"' in c[:400]:
        try:
            data = json.loads(c)
            return " ".join(str(x[5] if len(x) > 5 else x[-1]) for x in data.get("cells", []))
        except Exception:
            return c
    return c


class Sources:
    """문서 묶음의 조각 색인 — 묶음(form·done·criteria·notice)마다 (doc_id, chunk_id, 정규화한 글)."""

    def __init__(self) -> None:
        self.groups: dict[str, list[tuple[int, str, str]]] = {}

    def add(self, group: str, chunks: list[dict], prefix: str = "") -> "Sources":
        rows = self.groups.setdefault(group, [])
        for ch in chunks:
            text = _norm(chunk_text(ch.get("content") or ""))
            if text and ch.get("doc_id") and ch.get("id"):
                rows.append((int(ch["doc_id"]), f"{prefix}{ch['id']}", text))
        return self

    def find(self, piece: str, prefer: str | None = None) -> tuple[int, str] | None:
        key = _norm(piece)
        if len(key) < 6:
            return None
        probes = [key] if len(key) <= 40 else [key[:40], key[-40:]]
        order = ([prefer] if prefer in self.groups else []) + [g for g in self.groups if g != prefer]
        for g in order:
            for probe in probes:
                for doc_id, chunk_id, text in self.groups[g]:
                    if probe in text:
                        return doc_id, chunk_id
        # 여러 칸·여러 조각을 이어 만든 줄(행 이름을 이은 표 사실 등)은 통째로는 없다 — 14자 창으로, 기대한 묶음 안에서만 찾는다
        if prefer in self.groups and len(key) >= 14:
            for off in range(0, len(key) - 13, max(1, (len(key) - 14) // 4 or 1)):
                probe = key[off:off + 14]
                if probe.isdigit():
                    continue
                for doc_id, chunk_id, text in self.groups[prefer]:
                    if probe in text:
                        return doc_id, chunk_id
        return None


def _pieces(line: str) -> list[str]:
    """줄에서 조각을 찾을 글 — 줄 통째, 안 되면 ' | '·' · '·': ' 로 나눈 긴 토막(양식 격자 줄·'행 · 열: 값' 사실)."""
    body = _LEAD.sub("", line).strip()
    body = re.sub(r"^\([^)]{2,60}\)\s*", "", body)                       # 앞의 출처 표시 '(공고 · 사업 목적)'
    out = [body]
    parts = [p.strip() for p in re.split(r"\s+\|\s+|\s+·\s+|:\s+|\[c\d+\]", body) if len(_norm(p)) >= 6]
    out += sorted(parts, key=len, reverse=True)[:4]
    return out


def records_for(human: str, sources: Sources, program_id: str, turn: int = 0, known: dict | None = None) -> list[dict]:
    """사람 턴의 글 → 근거 기록 [{doc_id, chunk_id, text, turn, program_id}]. text 는 그 턴에 있는 줄 그대로.
    known = {줄: (doc_id, chunk_id)} — 생성기가 만든 자리에서 아는 출처(칸을 이어 만든 표 사실). 찾기보다 먼저 쓴다."""
    records: list[dict] = []
    seen: set[str] = set()
    prefer: str | None = None
    started = False
    for raw in human.split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = _HEADER.match(line)
        if m:
            name = m.group(1)
            if name in ('질문', '담당자 지시'):
                started = False
                continue
            started = True
            prefer = next((g for keys, g in _GROUP_OF_HEADER if any(k in name for k in keys)), prefer)
            line = line[m.end():].strip()
            if not line:
                continue
        if not started or line in seen or line.startswith("[질문]") or line.startswith("[담당자 지시]"):
            continue
        hit = (known or {}).get(line)
        for piece in ([] if hit else _pieces(line)):
            hit = sources.find(piece, prefer)
            if hit:
                break
        if hit:
            seen.add(line)
            records.append({"doc_id": hit[0], "chunk_id": hit[1], "text": line, "turn": turn, "program_id": program_id})
    return records


def attach(pair: dict, sources: Sources, program_id: str, node_path: list[str], known: dict | None = None) -> dict:
    """쌍의 meta 에 program_id·node_path·evidence_records 를 단다(사람 턴마다). review 는 비워 둔다 — 검수 전."""
    records: list[dict] = []
    for i, t in enumerate(pair.get("conversations") or []):
        if t.get("from") == "human":
            records += records_for(t.get("value") or "", sources, program_id, turn=i, known=known)
    meta = dict(pair.get("meta") or {})
    meta.update(program_id=program_id, node_path=[p for p in node_path if p], evidence_records=records)
    meta.setdefault("review", {"decision": None, "reviewer": None, "checks": {}})
    return dict(pair, meta=meta)
