"""플랫폼 저장 계층 — SQLite.

문서 접수 이력, 처리 상태, 모델 검토 의견, 담당자 의견을 담는다.
인풋 문서의 본문은 마스킹본만 저장한다. 기준(regulation) 문서는 판단 근거라
개인 문서가 아니므로 원문 그대로 저장한다 (2026-09-02 결정).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  filename     TEXT NOT NULL,
  stored_path  TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'received',
  -- received → processing → reviewed | failed
  series       TEXT,
  masked_text  TEXT,
  ai_review    TEXT,
  error        TEXT,
  created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id     INTEGER NOT NULL REFERENCES documents(id),
  opinion    TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_sessions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  title      TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER,
  role       TEXT NOT NULL,      -- user | assistant
  content    TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  sector     TEXT NOT NULL,
  name       TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_notes (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  content    TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_criteria (
  project_id      INTEGER NOT NULL REFERENCES projects(id),
  criteria_doc_id INTEGER NOT NULL REFERENCES documents(id),
  PRIMARY KEY (project_id, criteria_doc_id)
);
-- 인풋 문서의 파싱 결과 구조화 저장 (마스킹본 기준) — 문서 간 연관성 분석과
-- 초안 작성 시 재료 인출의 원천. kind: text | table
CREATE TABLE IF NOT EXISTS doc_chunks (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id   INTEGER NOT NULL REFERENCES documents(id),
  seq      INTEGER NOT NULL,
  kind     TEXT NOT NULL DEFAULT 'text',
  page_no  INTEGER,
  content  TEXT NOT NULL
);
-- 문서에서 추출된 그림 — 파일은 assets 디렉터리에, 여기엔 경로만.
-- 원본과 같은 장비 안에만 머문다 (마스킹 대상 아님, 열람은 인증 뒤에서만)
CREATE TABLE IF NOT EXISTS doc_assets (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id   INTEGER NOT NULL REFERENCES documents(id),
  kind     TEXT NOT NULL DEFAULT 'image',
  page_no  INTEGER,
  path     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS regulation_chunks (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id     INTEGER NOT NULL REFERENCES documents(id),
  reg_title  TEXT NOT NULL,
  heading    TEXT NOT NULL,
  content    TEXT NOT NULL
);
-- 개체 계층 (지식 그래프 2단계, ADR-0010) — 문서에서 추출된 사업·기관·연도
-- 개체와 문서-개체 언급 관계. 결정론 추출기가 채우고 재실행 시 교체된다
CREATE TABLE IF NOT EXISTS entities (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT NOT NULL,
  kind       TEXT NOT NULL,        -- program | org | year
  created_at TEXT NOT NULL,
  UNIQUE (name, kind)
);
CREATE TABLE IF NOT EXISTS doc_entities (
  doc_id     INTEGER NOT NULL REFERENCES documents(id),
  entity_id  INTEGER NOT NULL REFERENCES entities(id),
  n_mentions INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (doc_id, entity_id)
);
-- 학습 데이터셋 대장 (데이터 공방) — 언제 어떤 소스로 몇 쌍을 만들었고
-- 정제(수치 검증)에서 몇 쌍이 탈락했는지. 논문 방법론의 원재료
CREATE TABLE IF NOT EXISTS datasets (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  name              TEXT NOT NULL,
  sources           TEXT NOT NULL,   -- review,draft,chat
  path              TEXT NOT NULL,   -- data/interim/sft/*.jsonl
  n_pairs           INTEGER NOT NULL,
  n_dropped_numbers INTEGER NOT NULL DEFAULT 0,
  n_dropped_short   INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL
);
-- 추출 품질 신고 (품질 체계 5계층, docs/quality-system.md) — 담당자가 화면에서
-- 발견한 추출 문제를 남기고, /dev 백로그로 집계한다
CREATE TABLE IF NOT EXISTS quality_reports (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id      INTEGER NOT NULL REFERENCES documents(id),
  kind        TEXT NOT NULL,        -- table | typo | layout | other
  note        TEXT NOT NULL DEFAULT '',
  reporter    TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'open',  -- open | done
  fix_note    TEXT,                 -- 어느 계층에서 어떻게 막았는지
  resolved_by TEXT,
  resolved_at TEXT,
  created_at  TEXT NOT NULL
);
-- 외부 참조 감사 기록 (ADR-0008) — 외부로 나가는 모든 질의는 이 테이블을
-- 거친다. original은 감사용으로만 보관하고 절대 외부로 나가지 않는다.
CREATE TABLE IF NOT EXISTS egress_requests (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at  TEXT NOT NULL,
  requester   TEXT NOT NULL,
  source      TEXT NOT NULL DEFAULT 'manual',  -- manual | chat | draft
  original    TEXT NOT NULL,
  scrubbed    TEXT NOT NULL,
  removed     TEXT NOT NULL DEFAULT '[]',      -- 제거·치환 내역 (JSON 배열)
  verdict     TEXT NOT NULL,                   -- safe | review | blocked
  status      TEXT NOT NULL,
  -- blocked(차단) | queued(승인 대기) | denied(거부) | held(전송 대기)
  -- | approved(승인·전송 대기) | answered(응답 수신) | failed(전송 실패)
  decided_by  TEXT,
  decided_at  TEXT,
  response    TEXT,
  sent_at     TEXT,
  error       TEXT
);
CREATE TABLE IF NOT EXISTS draft_history (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id     INTEGER NOT NULL,   -- 어느 문서의 초안인지
  draft      TEXT NOT NULL,      -- 재작성으로 대체되기 전의 초안(=DPO rejected 후보)
  created_at TEXT NOT NULL
);
-- 개인정보 마스킹 기록 (절대 규칙 3 감사) — 마스킹이 돌 때마다 문서별·유형별
-- 건수를 남긴다. 원문 값은 절대 담지 않는다. context는 마스킹이 끝난 본문에서
-- 뜬 짧은 문맥(치환 토큰 주변). entity_type 'NONE'·n 0은 '돌았지만 0건'.
CREATE TABLE IF NOT EXISTS mask_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id      INTEGER NOT NULL REFERENCES documents(id),
  entity_type TEXT NOT NULL,
  n           INTEGER NOT NULL DEFAULT 0,
  context     TEXT,
  created_at  TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Database:
    # 스키마 추가분 — 기존 DB에 없으면 붙인다 (개발 단계 간이 마이그레이션)
    _MIGRATIONS = [
        "ALTER TABLE documents ADD COLUMN doc_type TEXT NOT NULL DEFAULT 'auto'",
        "ALTER TABLE documents ADD COLUMN draft TEXT",
        "ALTER TABLE documents ADD COLUMN draft_spec TEXT",
        "ALTER TABLE documents ADD COLUMN owner TEXT NOT NULL DEFAULT 'zzaimy'",
        "ALTER TABLE projects ADD COLUMN owner TEXT NOT NULL DEFAULT 'zzaimy'",
        "ALTER TABLE chat_sessions ADD COLUMN owner TEXT NOT NULL DEFAULT 'zzaimy'",
        "ALTER TABLE documents ADD COLUMN coverage TEXT",
        "ALTER TABLE documents ADD COLUMN decision TEXT NOT NULL DEFAULT 'pending'",
        "ALTER TABLE documents ADD COLUMN content_sha256 TEXT",
        "ALTER TABLE regulation_chunks ADD COLUMN sector TEXT NOT NULL DEFAULT 'common'",
        "ALTER TABLE documents ADD COLUMN sector TEXT NOT NULL DEFAULT 'common'",
        # 부서 축(기획처·복지처 등) — RAG·그래프를 부서별로 스코프한다.
        # sector(업무영역)와 별개 축. 기본 '공통'은 전 부서 공용 기준.
        "ALTER TABLE documents ADD COLUMN dept TEXT NOT NULL DEFAULT '공통'",
        "ALTER TABLE regulation_chunks ADD COLUMN dept TEXT NOT NULL DEFAULT '공통'",
        # 열람 등급 — 반입 때 정한다(access_policy). 조각은 문서의 값을 물려받아 검색 SQL 이 바로 거른다.
        "ALTER TABLE documents ADD COLUMN access_level TEXT NOT NULL DEFAULT 'public'",
        "ALTER TABLE regulation_chunks ADD COLUMN access_level TEXT NOT NULL DEFAULT 'public'",
        "ALTER TABLE documents ADD COLUMN related_criteria_id INTEGER",
        "ALTER TABLE documents ADD COLUMN receipt_no TEXT",
        "ALTER TABLE chat_messages ADD COLUMN session_id INTEGER",
        "ALTER TABLE documents ADD COLUMN project_id INTEGER",
        "ALTER TABLE projects ADD COLUMN instructions TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE projects ADD COLUMN memo TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE chat_sessions ADD COLUMN project_id INTEGER",
        "ALTER TABLE projects ADD COLUMN due_date TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE documents ADD COLUMN parse_note TEXT",
        "ALTER TABLE doc_chunks ADD COLUMN bbox TEXT",
        "ALTER TABLE doc_assets ADD COLUMN bbox TEXT",
        "ALTER TABLE documents ADD COLUMN suggested_criteria TEXT",
        "ALTER TABLE documents ADD COLUMN identity TEXT",
    ]

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            for stmt in self._MIGRATIONS:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # 이미 있는 컬럼

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    _TYPE_CODES = {
        "grant": "국고", "recruit": "채용", "admission": "입학",
        "auto": "행정", "regulation": "기준",
    }

    def add_document(
        self,
        filename: str,
        stored_path: str,
        doc_type: str = "auto",
        sector: str = "common",
        related_criteria_id: int | None = None,
        project_id: int | None = None,
        owner: str = "zzaimy",
        dept: str | None = None,
        access_level: str | None = None,
    ) -> int:
        from zzaimy.app.access_policy import classify

        dept, access_level = classify(doc_type, owner=owner, dept=dept, access_level=access_level)
        now = _now()
        year = now[:4]
        code = self._TYPE_CODES.get(doc_type, "문서")
        with self._conn() as conn:
            # 접수번호 = 연도-유형코드-일련번호(4자리). 일련번호는 그해·그 유형의 기존 최대값+1.
            # (COUNT+1 방식은 문서를 지우면 번호가 겹쳤다 — 번호는 한 번 쓰면 다시 쓰지 않는다)
            prefix = f"{year}-{code}-"
            row = conn.execute(
                "SELECT receipt_no FROM documents WHERE receipt_no LIKE ?"
                " ORDER BY receipt_no DESC LIMIT 1",
                (prefix + "%",),
            ).fetchone()
            try:
                seq = int((row[0] or "").rsplit("-", 1)[1]) + 1 if row else 1
            except (ValueError, IndexError):
                seq = 1
            receipt_no = f"{prefix}{seq:04d}"
            cur = conn.execute(
                "INSERT INTO documents (filename, stored_path, doc_type, sector,"
                " related_criteria_id, project_id, receipt_no, created_at, owner, dept, access_level)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (filename, stored_path, doc_type, sector, related_criteria_id,
                 project_id, receipt_no, now, owner, dept, access_level),
            )
            return int(cur.lastrowid or 0)

    def update_document(self, doc_id: int, **fields: str | None) -> None:
        allowed = {
            "status", "series", "masked_text", "ai_review",
            "error", "draft", "draft_spec", "coverage", "decision", "parse_note",
            "suggested_criteria",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"허용되지 않은 필드: {unknown}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE documents SET {sets} WHERE id = ?", (*fields.values(), doc_id)
            )

    def rename_from_text(self, doc_id: int, text: str = "", overwrite: bool = False,
                         ask=None) -> str:
        """반입 단계에서 문서 이름을 본문의 제목으로 바꾼다 — 뒷단계는 그 이름으로 흐른다.

        파싱이 끝나 본문이 손에 들어온 시점에 부른다. 돌려주는 값은 바뀐 문서 이름이다.
        """
        ident = self.fill_identity_from_text(doc_id, overwrite=overwrite, text=text, ask=ask)
        doc = self.get_document(doc_id)
        return (doc or {}).get("filename", "") if ident else (doc or {}).get("filename", "")

    def fill_identity_from_text(self, doc_id: int, overwrite: bool = False,
                                text: str = "", ask=None) -> dict:
        """문서 본문에서 이름·날짜를 찾아 문서 이름으로 삼는다.

        파일 이름은 'test.pdf' 처럼 아무것이나 될 수 있고 같은 이름이 여럿 들어올 수도 있다.
        그래서 본문에서 찾은 이름을 문서의 이름으로 바꾸고, 올라온 파일 이름은 정체에
        `original_filename` 으로 남긴다. 파일 자체는 건드리지 않는다(stored_path 그대로).
        """
        from zzaimy.app.doc_title import (display_name, meaningless_filename, resolved_title,
                                          tidy_name)

        doc = self.get_document(doc_id)
        if doc is None:
            return {}
        have = self.get_doc_identity(doc_id)
        if not overwrite and have.get("title"):
            return have
        # 규정식 제목(제N조·규정·지침)뿐 아니라 '2022학년도 입학자 연계교육과정 편성표' 처럼
        # 첫 쪽 제목 줄만 있는 문서도 이름으로 삼는다. 파일 이름이 더 나으면 그대로 둔다.
        probe = dict(doc)
        if overwrite:
            # 이름 규칙이 바뀌어 다시 매길 때다 — 저장된 이름을 무시하고 본문에서 다시 읽는다
            probe["identity"] = None
            have = {k: v for k, v in have.items() if k not in ("title", "date")}
        if text:
            probe["masked_text"] = text
        title, date = resolved_title(probe)
        if not title and ask is not None and meaningless_filename(probe.get("filename") or ""):
            # 규칙이 못 찾았고 파일 이름도 뜻이 없다 — 모델에게 제목 줄을 짚게 한다(본문 대조 통과분만)
            from zzaimy.app.doc_identity import find_title_by_model

            got = find_title_by_model(probe.get("masked_text") or "", ask)
            title = tidy_name(got) if got else None
        found = {k: v for k, v in (("title", title), ("date", date)) if v}
        if not found:
            # 제목이 없으면 파일 이름이 이름이다 — 그래도 첨부 표시·기호는 뗀다
            back = doc.get("filename") or ""
            if overwrite:
                # 다시 읽어도 이름이 없으면 옛 이름을 버리고 올라온 파일 이름으로 돌아간다
                back = have.get("original_filename") or back
            name = tidy_name(back) or back
            if meaningless_filename(name):
                # 규칙도 모델도 제목을 못 찾았고 파일 이름은 URL 이다 — 그 사실을 이름으로 말한다.
                # 올라온 이름은 identity.original_filename 에 남는다.
                have.setdefault("original_filename", back)
                name = f"제목 없음 · {doc.get('receipt_no') or doc_id}"
            if name != doc.get("filename") or overwrite:
                if name != back:
                    have.setdefault("original_filename", back)
                with self._conn() as conn:
                    conn.execute("UPDATE documents SET identity = ?, filename = ? WHERE id = ?",
                                 (json.dumps(have, ensure_ascii=False), name, doc_id))
                    conn.execute("UPDATE regulation_chunks SET reg_title = ? WHERE doc_id = ?",
                                 (name, doc_id))
            return have
        found.setdefault("original_filename", doc.get("filename") or "")
        self.set_doc_identity(doc_id, found)
        have.update(found)
        name = display_name({**doc, "identity": json.dumps(have, ensure_ascii=False)})
        if name:
            name = self._unique_name(name, doc_id, text or doc.get("masked_text") or "")
        if name and name != doc.get("filename"):
            with self._conn() as conn:
                conn.execute("UPDATE documents SET filename = ? WHERE id = ?", (name, doc_id))
                # 인용에 쓰는 기준명도 같은 이름을 본다 — 이름이 바뀌었는데 인용만 옛 이름이면 안 된다
                conn.execute("UPDATE regulation_chunks SET reg_title = ? WHERE doc_id = ?",
                             (name, doc_id))
        return have

    def _unique_name(self, name: str, doc_id: int, text: str) -> str:
        """같은 제목의 문서가 이미 있으면 구분되는 말을 덧붙인다.

        학과별 편성표처럼 제목이 같은 문서가 여럿 들어온다. 본문에서 그다음으로 구분이 되는 줄
        (학과·과정·대상 같은 것)을 찾아 붙이고, 그래도 겹치면 접수번호를 붙인다.
        """
        with self._conn() as conn:
            taken = {r[0] for r in conn.execute(
                "SELECT filename FROM documents WHERE filename LIKE ? AND id <> ?",
                (f"{name}%", doc_id))}
        if name not in taken:
            return name
        # 제목 바로 다음 줄이 부제다 — '( 홈페이지 , 모바일앱 )' 과 '( 웰로 앱 사용 매뉴얼 )' 처럼
        # 같은 제목의 문서를 가르는 말은 대개 거기 있다.
        import re as _re

        from zzaimy.app.doc_title import _ORG_ONLY, _plain, _title_like

        def _clean(sub: str) -> str:
            sub = _re.sub(r"\s*[,，]\s*", ", ", _plain(sub).strip(" ()（）[]【】:：-·"))
            return _re.sub(r"\s{2,}", " ", sub).strip()

        def _squash(t: str) -> str:
            return _re.sub(r"[\s()（）*'\"·]", "", t)

        def _usable(sub: str) -> bool:
            # 띄어쓰기만 다른 같은 제목('가구원 정보 제공 동의 절차')은 구분이 아니다
            if not (2 <= len(sub) <= 60) or "|" in sub or _squash(sub) in _squash(name) or _squash(name.split(" · ")[0]) in _squash(sub):
                return False
            if _ORG_ONLY.match(sub.replace(" ", "")) or _re.search(r"(팀|실|과|부|처|국|원)$", sub):
                return False                      # 부서·기관 줄은 문서를 가르는 말이 아니다
            return _title_like(_re.sub(r"[:：]", " ", sub))   # '법인사업자:지점' 같은 항목:값도 구분이 된다

        lines = [_plain(ln) for ln in text.splitlines() if _plain(ln)]
        base_raw = name.split(" · ")[0]
        base = _re.sub(r"[\s()（）]", "", base_raw)
        # 제목이 나오는 모든 줄을 본다 — 판독 결과는 제목을 두 번 적기도 한다('## 제목' 뒤에 '**제목** (부제)').
        # 줄마다 제목 뒤에 남은 말('(웰로 앱 사용 매뉴얼)')이 먼저, 그다음이 그 다음 줄이다.
        cands: list[str] = []
        for i, ln in enumerate(lines[:12]):
            flat = _re.sub(r"[\s()（）]", "", ln)
            if not base or base not in flat:
                continue
            # 제목 줄에 남은 말 — 띄어쓰기가 달라도 뒤에 붙은 괄호 부제('(웰로 앱 사용 매뉴얼)')를 잡는다
            m = _re.search(r"[(（]([^()（）]{2,60})[)）]\s*\**\s*$", ln)
            rest = _clean(m.group(1)) if m else (_clean(ln.replace(base_raw, "", 1)) if base_raw in ln else "")
            if rest:
                cands.append(rest)
            if i + 1 < len(lines):
                cands.append(_clean(lines[i + 1]))
        for sub in cands:
            if _usable(sub):
                merged = f"{name} · {sub}"
                if merged not in taken:
                    return merged
        for line in (ln.strip() for ln in text.splitlines()):
            if not (4 <= len(line) <= 40) or line in name:
                continue
            if any(k in line for k in ("학과", "계열", "전공", "과정", "대상", "유형", "차수")):
                # 표에서 온 줄이면 이름 칸이 아니라 값 칸을 쓴다('학과(계열) | 건축학과' → '건축학과')
                cells = [c.strip() for c in line.split("|") if c.strip()]
                qualifier = cells[-1] if len(cells) > 1 else line
                if len(qualifier) < 2 or qualifier in name:
                    continue
                merged = f"{name} · {qualifier}"
                if merged not in taken:
                    return merged
                break
        with self._conn() as conn:
            row = conn.execute("SELECT receipt_no FROM documents WHERE id = ?", (doc_id,)).fetchone()
        receipt = (row[0] if row else "") or str(doc_id)
        return f"{name} · {receipt}"

    def get_document(self, doc_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            return dict(row) if row else None

    def list_documents(
        self,
        doc_type: str | None = None,
        q: str | None = None,
        project_id: int | None = None,
        owner: str | None = None,
    ) -> list[dict]:
        sql = (
            "SELECT d.*, p.name AS project_name FROM documents d"
            " LEFT JOIN projects p ON p.id = d.project_id"
        )
        cond: list[str] = []
        params: list[str | int] = []
        if doc_type:
            cond.append("d.doc_type = ?")
            params.append(doc_type)
        if q:
            cond.append("d.filename LIKE ?")
            params.append(f"%{q}%")
        if project_id:
            cond.append("d.project_id = ?")
            params.append(project_id)
        if owner:
            cond.append("d.owner = ?")
            params.append(owner)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY d.id DESC"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def pending_documents(self, limit: int = 8, owner: str | None = None) -> list[dict]:
        """판정 대기 — 검토는 끝났는데 담당자 판정이 없는 문서."""
        sql = (
            "SELECT * FROM documents WHERE status = 'reviewed' AND decision = 'pending'"
            " AND doc_type NOT IN ('regulation', 'ocr')"
        )
        params: list = []
        if owner:
            sql += " AND owner = ?"
            params.append(owner)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def chunks_for_docs(self, doc_ids: list[int]) -> list[dict]:
        if not doc_ids:
            return []
        marks = ",".join("?" for _ in doc_ids)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM regulation_chunks WHERE doc_id IN ({marks}) ORDER BY id",
                doc_ids,
            ).fetchall()
            return [dict(r) for r in rows]

    def failed_documents(self, limit: int = 5, owner: str | None = None) -> list[dict]:
        sql = "SELECT * FROM documents WHERE status = 'failed'"
        params: list = []
        if owner:
            sql += " AND owner = ?"
            params.append(owner)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def create_chat_session(
        self, title: str, project_id: int | None = None, owner: str = "zzaimy"
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO chat_sessions (title, created_at, project_id, owner)"
                " VALUES (?, ?, ?, ?)",
                (title[:60], _now(), project_id, owner),
            )
            return int(cur.lastrowid or 0)

    def get_chat_session(self, session_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_project_chat_sessions(self, project_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_sessions WHERE project_id = ? ORDER BY id DESC LIMIT 8",
                (project_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_chat_sessions(self, limit: int = 12, owner: str | None = None) -> list[dict]:
        sql = "SELECT * FROM chat_sessions"
        params: list = []
        if owner:
            sql += " WHERE owner = ?"
            params.append(owner)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def add_chat(self, session_id: int, role: str, content: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO chat_messages (session_id, role, content, created_at)"
                " VALUES (?, ?, ?, ?)",
                (session_id, role, content, _now()),
            )

    def delete_chat_message(self, message_id: int) -> None:
        """대화 한 줄 삭제 — 답변 다시 받기에서 직전 답변을 지울 때 쓴다."""
        with self._conn() as conn:
            conn.execute("DELETE FROM chat_messages WHERE id = ?", (message_id,))

    def list_chats(self, session_id: int, limit: int = 100) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    def create_project(
        self, sector: str, name: str, due_date: str = "", owner: str = "zzaimy"
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO projects (sector, name, created_at, due_date, owner)"
                " VALUES (?, ?, ?, ?, ?)",
                (sector, name[:80], _now(), due_date[:10], owner),
            )
            return int(cur.lastrowid or 0)

    def record_content_hash(self, doc_id: int, sha256: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE documents SET content_sha256 = ? WHERE id = ?", (sha256, doc_id))

    def find_same_content(self, sha256: str, exclude_id: int) -> dict | None:
        """같은 내용의 문서가 이미 있으면 그것 — 실패한 것은 빼고, 먼저 들어온 것을 준다."""
        if not sha256:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, filename, status FROM documents WHERE content_sha256 = ? AND id <> ? "
                "AND status <> 'failed' ORDER BY id LIMIT 1", (sha256, exclude_id)).fetchone()
            return dict(row) if row else None

    def set_document_type(self, doc_id: int, doc_type: str) -> None:
        """문서 갈래를 바꾼다 — 반입 때 스스로 정하거나 담당자가 고칠 때 쓴다."""
        with self._conn() as conn:
            conn.execute("UPDATE documents SET doc_type = ? WHERE id = ?",
                         (doc_type, doc_id))

    def set_document_sector(self, doc_id: int, sector: str) -> None:
        """문서가 놓일 업무 영역을 바꾼다."""
        with self._conn() as conn:
            conn.execute("UPDATE documents SET sector = ? WHERE id = ?",
                         (sector, doc_id))

    def set_document_project(self, doc_id: int, project_id: int | None) -> None:
        """문서를 프로젝트에 붙이거나 뗀다."""
        with self._conn() as conn:
            conn.execute("UPDATE documents SET project_id = ? WHERE id = ?",
                         (project_id, doc_id))

    def list_projects(self, sector: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT p.*, (SELECT COUNT(*) FROM documents d WHERE d.project_id = p.id)"
                " AS n_docs FROM projects p WHERE p.sector = ? ORDER BY p.id DESC",
                (sector,),
            ).fetchall()
            return [dict(r) for r in rows]

    def replace_doc_chunks(self, doc_id: int, chunks: list[dict]) -> None:
        """파싱 산출 조각 교체 저장 — {kind, page_no, content} 목록."""
        with self._conn() as conn:
            conn.execute("DELETE FROM doc_chunks WHERE doc_id = ?", (doc_id,))
            conn.executemany(
                "INSERT INTO doc_chunks (doc_id, seq, kind, page_no, content, bbox)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (doc_id, i, c.get("kind", "text"), c.get("page_no"),
                     c["content"], c.get("bbox"))
                    for i, c in enumerate(chunks)
                ],
            )

    def list_doc_chunks(self, doc_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM doc_chunks WHERE doc_id = ? ORDER BY seq", (doc_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def replace_doc_assets(self, doc_id: int, assets: list[dict]) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM doc_assets WHERE doc_id = ?", (doc_id,))
            conn.executemany(
                "INSERT INTO doc_assets (doc_id, kind, page_no, path) VALUES (?, ?, ?, ?)",
                [
                    (doc_id, a.get("kind", "image"), a.get("page_no"), a["path"])
                    for a in assets
                ],
            )

    def list_doc_assets(self, doc_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM doc_assets WHERE doc_id = ? ORDER BY id", (doc_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def referencing_documents(self, criteria_id: int) -> list[dict]:
        """이 기준 문서를 참조하는 접수 문서들 — 지정·자동 연결·프로젝트 경유.

        문서-규정 연관 그래프의 1단계 간선이다.
        """
        like_a = f'%"id": {criteria_id},%'
        like_b = f'%"id": {criteria_id}}}%'
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT d.* FROM documents d
                WHERE d.doc_type NOT IN ('regulation', 'ocr') AND (
                    d.related_criteria_id = ?
                    OR d.suggested_criteria LIKE ?
                    OR d.suggested_criteria LIKE ?
                    OR d.project_id IN (
                        SELECT project_id FROM project_criteria
                        WHERE criteria_doc_id = ?
                    )
                )
                ORDER BY d.id DESC LIMIT 30
                """,
                (criteria_id, like_a, like_b, criteria_id),
            ).fetchall()
            return [dict(r) for r in rows]

    def recent_activity(self, limit: int = 12) -> list[dict]:
        """대시보드 최근 활동 — 접수·기준 등록·추출·채팅을 시간 역순으로 합친다."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM (
                  SELECT created_at, filename AS title, id AS ref_id,
                         CASE doc_type WHEN 'regulation' THEN 'criteria'
                              WHEN 'ocr' THEN 'ocr' ELSE 'intake' END AS kind
                  FROM documents
                  UNION ALL
                  SELECT created_at, title, id AS ref_id, 'chat' AS kind
                  FROM chat_sessions
                ) ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_setting(self, key: str, default: str = "") -> str:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def all_settings(self) -> dict[str, str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            return {r["key"]: r["value"] for r in rows}

    def list_all_projects(self, owner: str | None = None) -> list[dict]:
        """사이드바용 — 섹터 구분 없이 전체 프로젝트 (문서 수 포함)."""
        sql = (
            "SELECT p.*, (SELECT COUNT(*) FROM documents d WHERE d.project_id = p.id)"
            " AS n_docs FROM projects p"
        )
        params: list = []
        if owner:
            sql += " WHERE p.owner = ?"
            params.append(owner)
        sql += " ORDER BY p.id DESC LIMIT 20"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def update_project_meta(
        self,
        project_id: int,
        instructions: str | None = None,
        memo: str | None = None,
        due_date: str | None = None,
    ) -> None:
        with self._conn() as conn:
            if due_date is not None:
                conn.execute(
                    "UPDATE projects SET due_date = ? WHERE id = ?",
                    (due_date[:10], project_id),
                )
            if instructions is not None:
                conn.execute(
                    "UPDATE projects SET instructions = ? WHERE id = ?",
                    (instructions[:4000], project_id),
                )
            if memo is not None:
                conn.execute(
                    "UPDATE projects SET memo = ? WHERE id = ?", (memo[:4000], project_id)
                )

    def set_project_criteria(self, project_id: int, criteria_doc_ids: list[int]) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM project_criteria WHERE project_id = ?", (project_id,)
            )
            conn.executemany(
                "INSERT OR IGNORE INTO project_criteria (project_id, criteria_doc_id)"
                " VALUES (?, ?)",
                [(project_id, cid) for cid in criteria_doc_ids],
            )

    def add_project_note(self, project_id: int, content: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO project_notes (project_id, content, created_at)"
                " VALUES (?, ?, ?)",
                (project_id, content[:2000], _now()),
            )
            return int(cur.lastrowid or 0)

    def list_project_notes(self, project_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM project_notes WHERE project_id = ? ORDER BY id DESC",
                (project_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_project_note(self, project_id: int, note_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM project_notes WHERE project_id = ? AND id = ?",
                (project_id, note_id),
            )

    def add_project_criteria(self, project_id: int, criteria_doc_ids: list[int]) -> None:
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO project_criteria (project_id, criteria_doc_id)"
                " VALUES (?, ?)",
                [(project_id, cid) for cid in criteria_doc_ids],
            )

    def remove_project_criterion(self, project_id: int, criteria_doc_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM project_criteria WHERE project_id = ? AND criteria_doc_id = ?",
                (project_id, criteria_doc_id),
            )

    def get_project_criteria_ids(self, project_id: int) -> list[int]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT criteria_doc_id FROM project_criteria WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            return [r["criteria_doc_id"] for r in rows]

    def rename_project(self, project_id: int, name: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE projects SET name = ? WHERE id = ?", (name[:80], project_id)
            )
            return cur.rowcount > 0

    def delete_project(self, project_id: int) -> bool:
        """프로젝트만 지운다 — 소속 문서는 연결 해제 후 그대로 남는다."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE documents SET project_id = NULL WHERE project_id = ?",
                (project_id,),
            )
            conn.execute(
                "DELETE FROM project_criteria WHERE project_id = ?", (project_id,)
            )
            conn.execute(
                "DELETE FROM project_notes WHERE project_id = ?", (project_id,)
            )
            cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            return cur.rowcount > 0

    def get_project(self, project_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_document(self, doc_id: int) -> None:
        """문서와 파생물(검토 의견·규정 조각)을 함께 지운다. 저장 파일은 호출부에서."""
        with self._conn() as conn:
            conn.execute("DELETE FROM reviews WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM regulation_chunks WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM doc_chunks WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM doc_assets WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    def add_regulation_chunks(
        self, doc_id: int, reg_title: str, chunks,
        sector: str = "common", dept: str | None = None, access_level: str | None = None,
    ) -> None:
        """조각의 부서·등급은 문서의 값을 물려받는다 — 따로 주지 않으면 documents 에서 읽는다."""
        with self._conn() as conn:
            if dept is None or access_level is None:
                row = conn.execute("SELECT dept, access_level FROM documents WHERE id = ?", (doc_id,)).fetchone()
                dept = dept if dept is not None else ((row[0] if row else None) or "공통")
                access_level = access_level if access_level is not None else ((row[1] if row else None) or "public")
            conn.execute("DELETE FROM regulation_chunks WHERE doc_id = ?", (doc_id,))
            conn.executemany(
                "INSERT INTO regulation_chunks"
                " (doc_id, reg_title, heading, content, sector, dept, access_level)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(doc_id, reg_title, c.heading, c.content, sector, dept, access_level)
                 for c in chunks],
            )

    def set_document_scope(self, doc_id: int, dept: str | None = None, access_level: str | None = None) -> None:
        """문서의 부서·등급을 바꾸고 조각에도 그대로 옮긴다 — 재색인 없이 검색 범위가 따라간다."""
        from zzaimy.app.access_policy import LEVELS

        with self._conn() as conn:
            if dept is not None:
                conn.execute("UPDATE documents SET dept = ? WHERE id = ?", (dept, doc_id))
                conn.execute("UPDATE regulation_chunks SET dept = ? WHERE doc_id = ?", (dept, doc_id))
            if access_level is not None and access_level in LEVELS:
                conn.execute("UPDATE documents SET access_level = ? WHERE id = ?", (access_level, doc_id))
                conn.execute("UPDATE regulation_chunks SET access_level = ? WHERE doc_id = ?", (access_level, doc_id))

    def set_doc_identity(self, doc_id: int, identity: dict) -> None:
        """문서가 어떤 사업에 관한 것인지 — 본문에서 확인된 값만 들어온다."""
        import json as _json

        with self._conn() as conn:
            # 첫 쪽에서 결정론적으로 찾은 이름·날짜(doc_title.py)는 모델 재읽기로 지우지 않는다
            row = conn.execute("SELECT identity FROM documents WHERE id = ?", (doc_id,)).fetchone()
            try:
                old = _json.loads((row[0] if row else None) or "{}")
            except ValueError:
                old = {}
            merged = {k: old[k] for k in ("title", "date") if old.get(k)}
            merged.update(identity)
            conn.execute("UPDATE documents SET identity = ? WHERE id = ?",
                         (_json.dumps(merged, ensure_ascii=False), doc_id))

    def get_doc_identity(self, doc_id: int) -> dict:
        import json as _json

        with self._conn() as conn:
            row = conn.execute("SELECT identity FROM documents WHERE id = ?",
                               (doc_id,)).fetchone()
        if not row or not row["identity"]:
            return {}
        try:
            got = _json.loads(row["identity"])
        except ValueError:
            return {}
        return got if isinstance(got, dict) else {}

    def docs_sharing_program(self, doc_id: int) -> list[dict]:
        """같은 사업을 다루는 다른 문서 — 낱말이 겹쳐서가 아니라 정체가 같아서 잇는다."""
        from zzaimy.app.doc_identity import signature

        key = signature(self.get_doc_identity(doc_id))
        if not key:
            return []
        out = []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, filename, identity FROM documents"
                " WHERE identity IS NOT NULL AND id != ?", (doc_id,)
            ).fetchall()
        import json as _json

        for r in rows:
            try:
                other = _json.loads(r["identity"])
            except ValueError:
                continue
            if isinstance(other, dict) and signature(other) == key:
                out.append({"id": r["id"], "filename": r["filename"],
                            "program": other.get("program", ""),
                            "year": other.get("year", "")})
        return out

    def department_counts(self) -> list[dict]:
        """실제 자료에 있는 부서 목록과 분량 — 담당자 소속 부서를 고를 때 근거가 된다."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT dept,"
                " (SELECT COUNT(*) FROM documents d WHERE d.dept = x.dept) AS n_docs,"
                " COUNT(*) AS n_chunks"
                " FROM regulation_chunks x GROUP BY dept ORDER BY n_chunks DESC"
            ).fetchall()
            out = [dict(r) for r in rows]
            known = {r["dept"] for r in out}
            extra = conn.execute(
                "SELECT dept, COUNT(*) AS n_docs FROM documents GROUP BY dept"
            ).fetchall()
            for r in extra:
                if r["dept"] not in known:
                    out.append({"dept": r["dept"], "n_docs": r["n_docs"], "n_chunks": 0})
        return out

    def regulation_chunk_counts(self) -> dict[int, int]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT doc_id, COUNT(*) FROM regulation_chunks GROUP BY doc_id"
            ).fetchall()
            return {r[0]: r[1] for r in rows}

    def list_regulation_chunks(
        self, sector: str | None = None, dept: str | None = None,
        user: str | None = None, levels: tuple[str, ...] | None = None,
    ) -> list[dict]:
        """규정 조각 후보. sector·dept가 주어지면 각 전용 + 공통만 남긴다.

        부서별 RAG(사용자 요구): dept를 주면 그 부서 문서 + 공통 규정만 검색
        후보가 된다 — 컨텍스트 예산 안에 관련 근거만 담고 타 부서를 배제.
        열람 등급(access_policy)은 dept·user·levels 가 하나라도 있을 때 건다 —
        public 은 누구나, dept 는 그 부서(와 공통), owner 는 올린 사람(user)만. levels 를 주면 그 등급만
        (학생 = ('public',)). 아무 범위도 없으면 전체(관리자·측정·재색인).
        """
        cond, params = [], []
        if sector:
            cond.append("sector IN (?, 'common')")
            params.append(sector)
        if dept:
            cond.append("dept IN (?, '공통')")
            params.append(dept)
        if levels:
            cond.append("access_level IN (%s)" % ",".join("?" for _ in levels))
            params.extend(levels)
        elif dept or user:
            cond.append("(access_level IN ('public', 'dept')"
                        " OR (access_level = 'owner' AND doc_id IN (SELECT id FROM documents WHERE owner = ?)))")
            params.append(user or "")
        sql = "SELECT * FROM regulation_chunks"
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY id"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def replace_doc_entities(
        self, doc_id: int, mentions: list[tuple[str, str, int]]
    ) -> None:
        """문서의 개체 언급을 교체 저장 — (이름, 유형, 횟수) 목록."""
        with self._conn() as conn:
            conn.execute("DELETE FROM doc_entities WHERE doc_id = ?", (doc_id,))
            for name, kind, count in mentions:
                cur = conn.execute(
                    "INSERT INTO entities (name, kind, created_at) VALUES (?, ?, ?)"
                    " ON CONFLICT(name, kind) DO UPDATE SET name = excluded.name"
                    " RETURNING id",
                    (name[:80], kind, _now()),
                )
                entity_id = cur.fetchone()[0]
                conn.execute(
                    "INSERT OR REPLACE INTO doc_entities"
                    " (doc_id, entity_id, n_mentions) VALUES (?, ?, ?)",
                    (doc_id, entity_id, count),
                )
            # 어느 문서에서도 언급되지 않는 고아 개체 정리
            conn.execute(
                "DELETE FROM entities WHERE id NOT IN"
                " (SELECT DISTINCT entity_id FROM doc_entities)"
            )

    def graph_entities(self, min_docs: int = 2) -> list[dict]:
        """그래프용 개체 — 문서 min_docs건 이상을 잇는 개체와 언급 간선."""
        with self._conn() as conn:
            ents = [dict(r) for r in conn.execute(
                """
                SELECT e.id, e.name, e.kind, COUNT(DISTINCT de.doc_id) AS n_docs
                FROM entities e JOIN doc_entities de ON de.entity_id = e.id
                GROUP BY e.id HAVING n_docs >= ?
                """,
                (min_docs,),
            ).fetchall()]
            ids = [e["id"] for e in ents]
            links: list[dict] = []
            if ids:
                marks = ",".join("?" for _ in ids)
                links = [dict(r) for r in conn.execute(
                    f"SELECT doc_id, entity_id, n_mentions FROM doc_entities"
                    f" WHERE entity_id IN ({marks})",
                    ids,
                ).fetchall()]
            return {"entities": ents, "links": links}

    def add_dataset(
        self, name: str, sources: str, path: str,
        n_pairs: int, n_dropped_numbers: int = 0, n_dropped_short: int = 0,
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO datasets (name, sources, path, n_pairs,"
                " n_dropped_numbers, n_dropped_short, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name[:80], sources, path, n_pairs,
                 n_dropped_numbers, n_dropped_short, _now()),
            )
            return int(cur.lastrowid or 0)

    def get_dataset(self, dataset_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM datasets WHERE id = ?", (dataset_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_datasets(self, limit: int = 30, offset: int = 0) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM datasets ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]

    def count_datasets(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0])

    # ---- 초안 재작성 이력 (DPO 선호쌍 원천 — 덮어쓰기 전 이전본 보존) ----
    def add_draft_history(self, doc_id: int, draft: str) -> None:
        if not (draft or "").strip():
            return
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO draft_history (doc_id, draft, created_at) VALUES (?, ?, ?)",
                (doc_id, draft, _now()))

    def list_draft_history(self, doc_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM draft_history WHERE doc_id = ? ORDER BY id", (doc_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def count_draft_history(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM draft_history").fetchone()[0])

    def add_quality_report(
        self, doc_id: int, kind: str, note: str, reporter: str
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO quality_reports (doc_id, kind, note, reporter, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (doc_id, kind, note[:1000], reporter, _now()),
            )
            return int(cur.lastrowid or 0)

    def list_quality_reports(
        self, status: str = "open", limit: int = 30
    ) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT q.*, d.filename FROM quality_reports q"
                " JOIN documents d ON d.id = q.doc_id"
                " WHERE q.status = ? ORDER BY q.id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def resolve_quality_report(
        self, report_id: int, resolved_by: str, fix_note: str = ""
    ) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE quality_reports SET status = 'done', resolved_by = ?,"
                " fix_note = ?, resolved_at = ? WHERE id = ? AND status = 'open'",
                (resolved_by, fix_note[:1000], _now(), report_id),
            )
            return cur.rowcount > 0

    def quality_report_stats(self) -> dict[str, int]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT kind, COUNT(*) FROM quality_reports WHERE status = 'open'"
                " GROUP BY kind"
            ).fetchall()
            open_by_kind = {r[0]: int(r[1]) for r in rows}
            done = conn.execute(
                "SELECT COUNT(*) FROM quality_reports WHERE status = 'done'"
            ).fetchone()[0]
        return {"open": sum(open_by_kind.values()), "done": int(done), **open_by_kind}

    def add_egress_request(
        self,
        requester: str,
        source: str,
        original: str,
        scrubbed: str,
        removed: str,
        verdict: str,
        status: str,
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO egress_requests (created_at, requester, source,"
                " original, scrubbed, removed, verdict, status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (_now(), requester, source, original, scrubbed, removed,
                 verdict, status),
            )
            return int(cur.lastrowid or 0)

    def update_egress_request(self, req_id: int, **fields: str | None) -> None:
        allowed = {"status", "decided_by", "decided_at", "response", "sent_at", "error"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"허용되지 않은 필드: {unknown}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE egress_requests SET {sets} WHERE id = ?",
                (*fields.values(), req_id),
            )

    def get_egress_request(self, req_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM egress_requests WHERE id = ?", (req_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_egress_requests(
        self, status: str | None = None, limit: int = 50
    ) -> list[dict]:
        sql = "SELECT * FROM egress_requests"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def egress_stats(self) -> dict[str, int]:
        """판정·상태별 건수 — /dev 이그레스 모니터링의 집계 원천."""
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM egress_requests").fetchone()[0]
            by_status = {
                r[0]: r[1]
                for r in conn.execute(
                    "SELECT status, COUNT(*) FROM egress_requests GROUP BY status"
                ).fetchall()
            }
        return {"total": int(total), **{k: int(v) for k, v in by_status.items()}}

    # ---- 개인정보 마스킹 기록 (절대 규칙 3 감사, pii_audit) ----

    def replace_mask_events(self, doc_id: int, rows: list[dict]) -> None:
        """마스킹 기록 교체 저장 — {entity_type, n, context} 목록.

        원문 값은 받지 않는다(호출자 pii_audit.record_mask_events가 마스킹본
        문맥만 만든다). 빈 목록은 '돌았지만 0건'을 뜻하는 NONE 행 하나로 남겨
        기록 자체가 없는 문서(기능 도입 전 처리분)와 구별한다.
        """
        now = _now()
        if not rows:
            rows = [{"entity_type": "NONE", "n": 0, "context": None}]
        with self._conn() as conn:
            conn.execute("DELETE FROM mask_events WHERE doc_id = ?", (doc_id,))
            conn.executemany(
                "INSERT INTO mask_events (doc_id, entity_type, n, context, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (doc_id, r["entity_type"], int(r.get("n", 0)),
                     (r.get("context") or None), now)
                    for r in rows
                ],
            )

    def list_mask_events(self, doc_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM mask_events WHERE doc_id = ? ORDER BY n DESC, entity_type",
                (doc_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def mask_event_stats(self) -> dict:
        """기록 있는 문서 수·총 치환 건수·유형별 건수·마지막 기록 시각."""
        with self._conn() as conn:
            docs = conn.execute(
                "SELECT COUNT(DISTINCT doc_id) FROM mask_events"
            ).fetchone()[0]
            total = conn.execute(
                "SELECT COALESCE(SUM(n), 0) FROM mask_events"
            ).fetchone()[0]
            by_type = {
                r[0]: int(r[1])
                for r in conn.execute(
                    "SELECT entity_type, SUM(n) FROM mask_events WHERE n > 0"
                    " GROUP BY entity_type ORDER BY 2 DESC"
                ).fetchall()
            }
            last = conn.execute("SELECT MAX(created_at) FROM mask_events").fetchone()[0]
        return {"docs": int(docs), "total": int(total), "by_type": by_type, "last_at": last}

    def mask_events_by_doc(self, limit: int = 300) -> list[dict]:
        """문서별 마스킹 기록 — 유형별 건수·문맥 표본을 문서 하나로 접는다."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT m.doc_id, d.filename, d.doc_type, d.owner, m.entity_type, m.n,"
                " m.context, m.created_at FROM mask_events m"
                " JOIN documents d ON d.id = m.doc_id"
                " ORDER BY m.doc_id DESC, m.n DESC, m.entity_type"
            ).fetchall()
        out: dict[int, dict] = {}
        for r in rows:
            d = out.get(r["doc_id"])
            if d is None:
                if len(out) >= limit:
                    continue
                d = out[r["doc_id"]] = {
                    "doc_id": r["doc_id"], "filename": r["filename"],
                    "doc_type": r["doc_type"], "owner": r["owner"],
                    "by_type": {}, "total": 0, "contexts": [],
                    "created_at": r["created_at"],
                }
            if r["n"] > 0:
                d["by_type"][r["entity_type"]] = int(r["n"])
                d["total"] += int(r["n"])
                if r["context"]:
                    d["contexts"].append(
                        {"entity_type": r["entity_type"], "context": r["context"]}
                    )
        return list(out.values())

    def add_review(self, doc_id: int, opinion: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO reviews (doc_id, opinion, created_at) VALUES (?, ?, ?)",
                (doc_id, opinion, _now()),
            )

    def get_reviews(self, doc_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM reviews WHERE doc_id = ? ORDER BY id", (doc_id,)
            ).fetchall()
            return [dict(r) for r in rows]
