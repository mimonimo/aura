"""문서·프로젝트 의미 벡터 — 그래프의 '추정 연관'(프로젝트↔문서) 재료.

규칙·키워드가 아니라 학습된 임베딩(KURE, embed_search.embed_texts)으로 문서와
프로젝트를 같은 공간에 놓고 코사인으로 잇는다(ADR-0015). 문서 벡터는 본문
앞부분(제목 포함)을 한 번 임베딩해 파일에 캐시하고, 본문이 바뀌면(서명 불일치)
그 문서만 다시 계산한다. 모델이 없으면(embed_fn → None) 아무것도 하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

_TEXT_CHARS = 1500      # 임베딩 입력 상한(모델 512토큰 안팎) — 제목과 앞부분이 주제를 대표한다


def doc_text(db, doc: dict) -> str:
    """문서를 대표하는 텍스트 — 파일명(제목) + 본문 앞부분."""
    title = (doc.get("filename") or "").rsplit(".", 1)[0].replace("_", " ")
    body = ""
    if doc.get("doc_type") == "regulation":
        parts = [c.get("content") or "" for c in db.chunks_for_docs([doc["id"]])[:6]]
        body = "\n".join(parts)
    else:
        body = doc.get("masked_text") or ""
    return f"{title}\n{body}"[:_TEXT_CHARS].strip()


def project_text(db, project: dict, criteria_docs: list[dict]) -> str:
    """프로젝트를 대표하는 텍스트 — 이름 + 지침·메모 + 연결한 기준 문서 제목."""
    parts = [project.get("name") or ""]
    try:
        parts += [n.get("content") or "" for n in db.list_project_notes(project["id"])[:5]]
    except Exception:
        pass
    parts += [(d.get("filename") or "").rsplit(".", 1)[0].replace("_", " ") for d in criteria_docs]
    return "\n".join(p for p in parts if p)[:_TEXT_CHARS].strip()


def _sig(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


class DocVectorCache:
    """문서 id → 벡터 캐시(파일). 서명이 다르면 재계산, 없으면 계산해 저장."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._ids: list[int] = []
        self._sigs: list[str] = []
        self._vecs = None
        self._load()

    def _load(self) -> None:
        try:
            import numpy as np

            if self.path.exists():
                data = np.load(self.path, allow_pickle=False)
                self._ids = [int(i) for i in data["ids"]]
                self._sigs = [str(s) for s in data["sigs"]]
                self._vecs = data["vectors"]
        except Exception:
            self._ids, self._sigs, self._vecs = [], [], None

    def _save(self) -> None:
        try:
            import numpy as np

            self.path.parent.mkdir(parents=True, exist_ok=True)
            np.savez(self.path, ids=np.array(self._ids), sigs=np.array(self._sigs),
                     vectors=self._vecs)
        except Exception:
            pass                                   # 캐시 실패는 기능을 막지 않는다

    def missing(self, texts: dict[int, str]) -> int:
        """캐시에 없거나 본문이 바뀐 문서 수 — 요청 경로에서 동기 계산할지 정하는 기준."""
        have = dict(zip(self._ids, self._sigs))
        return sum(1 for i, t in texts.items() if have.get(i) != _sig(t))

    def vectors(self, texts: dict[int, str], embed_fn):
        """{doc_id: text} → (ids, matrix). 캐시에 없거나 바뀐 것만 embed_fn 으로 계산."""
        import numpy as np

        have = {i: (s, k) for k, (i, s) in enumerate(zip(self._ids, self._sigs))}
        need = [i for i, t in texts.items() if have.get(i, ("", 0))[0] != _sig(t)]
        if need:
            new = embed_fn([texts[i] for i in need])
            if new is None:
                return [], None
            new = np.asarray(new, dtype="float32")
            keep = [k for k, i in enumerate(self._ids) if i not in set(need)]
            base_ids = [self._ids[k] for k in keep]
            base_sigs = [self._sigs[k] for k in keep]
            base_vecs = self._vecs[keep] if self._vecs is not None and keep else None
            self._ids = base_ids + need
            self._sigs = base_sigs + [_sig(texts[i]) for i in need]
            self._vecs = np.vstack([base_vecs, new]) if base_vecs is not None else new
            self._save()
        if self._vecs is None:
            return [], None
        pos = {i: k for k, i in enumerate(self._ids)}
        ids = [i for i in texts if i in pos]
        return ids, self._vecs[[pos[i] for i in ids]]


def summary_json(path: Path) -> dict:
    """캐시 상태(개발 화면·점검용)."""
    try:
        import numpy as np

        d = np.load(path, allow_pickle=False)
        return {"docs": int(len(d["ids"])), "dim": int(d["vectors"].shape[1])}
    except Exception:
        return {"docs": 0, "dim": 0}


__all__ = ["DocVectorCache", "doc_text", "project_text", "summary_json", "json"]
