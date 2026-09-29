"""Preserve explicit document relationships, never infer missing facts.

IDs for blocks are local to a document revision. Public IDs include that revision.
An authorized allow-list is mandatory even for cross-reference expansion.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json


@dataclass(frozen=True)
class Block:
    id: str
    order: int
    kind: str
    text: str
    parent: str | None = None
    requires: tuple[str, ...] = ()
    locator: str = ""
    structure_verified: bool = False


@dataclass(frozen=True)
class Document:
    program_id: str
    document_id: str
    revision: str
    role: str
    blocks: tuple[Block, ...]

    def ref(self, block_id: str) -> str:
        # JSON tuple is unambiguous even when external IDs contain separators.
        return json.dumps([self.program_id, self.document_id, self.revision, block_id],
                          ensure_ascii=False, separators=(",", ":"))

    def to_dict(self) -> dict:
        return {"schema_version": 1, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict) -> Document:
        if data.get("schema_version") != 1:
            raise ValueError("Unsupported context schema")
        blocks = tuple(Block(**{**b, "requires": tuple(b.get("requires", ()))})
                       for b in data["blocks"])
        result = cls(data["program_id"], data["document_id"], data["revision"],
                     data["role"], blocks)
        validate(result)
        return result


def validate(doc: Document) -> None:
    if not all(isinstance(v, str) and v.strip() for v in
               (doc.program_id, doc.document_id, doc.revision, doc.role)):
        raise ValueError("Document identity and role are required")
    ids = {b.id for b in doc.blocks}
    if len(ids) != len(doc.blocks) or not all(ids):
        raise ValueError("Block IDs must be unique and nonempty")
    if len({b.order for b in doc.blocks}) != len(doc.blocks):
        raise ValueError("Source order must be unique within a revision")
    by_id = {b.id: b for b in doc.blocks}
    for b in doc.blocks:
        if not isinstance(b.text, str) or not isinstance(b.order, int):
            raise ValueError("Invalid block text/order")
        if b.parent is not None and b.parent not in ids:
            raise ValueError("Unknown parent")
        if any(r not in ids for r in b.requires):
            raise ValueError("Unknown required evidence")
        seen = {b.id}
        parent = b.parent
        while parent is not None:
            if parent in seen:
                raise ValueError("Parent cycle")
            seen.add(parent)
            parent = by_id[parent].parent


@dataclass(frozen=True)
class Context:
    records: tuple[dict, ...]
    complete: bool
    needs_review: bool
    issues: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"schema_version": 1, **asdict(self)}


def build_context(documents: tuple[Document, ...], *, program_id: str,
                  seeds: tuple[str, ...], allowed_refs: frozenset[str],
                  max_chars: int = 24000) -> Context:
    """Expand explicit dependencies; reject partial evidence rather than truncate.

    max_chars counts serialized records (including identity/locator), NOT tokens.
    The model adapter must perform an additional tokenizer-specific limit check.
    Missing/denied refs produce generic diagnostics, not forbidden identifiers.
    """
    if max_chars < 1 or not seeds:
        raise ValueError("Positive budget and at least one seed required")
    index = {}
    for doc in documents:
        validate(doc)
        for block in doc.blocks:
            key = doc.ref(block.id)
            if key in index:
                raise ValueError("Duplicate document revision/block identity")
            index[key] = (doc, block)
    queue = list(seeds)
    visited, selected, issues = set(), {}, set()
    while queue:
        ref = queue.pop()
        if ref in visited:
            continue
        visited.add(ref)
        if ref not in allowed_refs or ref not in index:
            issues.add("evidence_unavailable")
            continue
        doc, block = index[ref]
        if doc.program_id != program_id:
            issues.add("program_mismatch")
            continue
        selected[ref] = (doc, block)
        deps = block.requires + ((block.parent,) if block.parent is not None else ())
        queue.extend(doc.ref(dep) for dep in deps)
    records = []
    needs_review = False
    for ref, (doc, block) in sorted(selected.items(), key=lambda item:
            (item[1][0].document_id, item[1][0].revision, item[1][1].order)):
        needs_review |= not block.structure_verified
        records.append({"ref": ref, "program_id": doc.program_id,
                        "document_id": doc.document_id, "revision": doc.revision,
                        "role": doc.role, **asdict(block)})
    if len(json.dumps(records, ensure_ascii=False)) > max_chars:
        issues.add("budget_exceeded")
        records = []  # Never return a severed table or silently drop an exception.
    return Context(tuple(records), not issues, needs_review, tuple(sorted(issues)))
