"""원본 보관소의 같은 파일 판정 — 이름·크기가 같아도 내용(sha256 지문)이 다르면 중복이 아니다.

예전에는 이름·크기만 보아 내용이 다른 문서(같은 이름의 양식에 다른 내용을 채운 것 등)가 「같은 파일 중복」으로 처리에서 빠졌다.
지문은 파일을 직접 읽을 수 있는 DGX 목록(scripts/165)이 내고, 「파일 사실 장부」(archive_facts.jsonl: 경로·크기·시각·지문·
실제 형식)로 남긴다. VM(scripts/170)은 원본 목록을 rsync --list-only 로만 보므로 그 장부를 받아 같은 판정을 한다.

- 지문은 이름·크기가 같은 묶음, 그중 처리 대상 문서 형식(doc_formats.DOC_EXT·확장자로 알 수 없는 파일)만 낸다.
  사진·동영상 묶음은 어차피 처리 대상이 아니라 읽지 않는다(이름·크기 기준 그대로).
- 지문을 아직 못 낸 파일(새로 들어온 직후·읽기 시간 예산 초과)은 예전처럼 이름·크기로 본다 — 다음 회차에 지문이 생기면 바로잡힌다.
- 실제 형식: 확장자 없음·「…다운로드」 파일은 파일 머리로 본 실제 문서 형식을 장부의 형식 값으로 쓴다(문서가 아니면 그대로).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from pathlib import Path

from zzaimy import doc_formats

FACTS_NAME = "archive_facts.jsonl"


def file_sha256(path: Path, block: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def load_facts(path: Path | str) -> dict[str, dict]:
    """파일 사실 장부 → {rel: {size, mtime, sha256?, real?}}. 없거나 깨진 줄은 건너뛴다."""
    out: dict[str, dict] = {}
    p = Path(path)
    if not p.is_file():
        return out
    with open(p, encoding="utf-8", newline="") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("rel"):
                out[r["rel"]] = r
    return out


def save_facts(path: Path | str, facts: dict[str, dict], keep: set[str] | None = None) -> int:
    """장부를 통째로 다시 쓴다(임시 파일 → 바꿔치기, 받는 쪽이 반쯤 쓴 파일을 보지 않게). keep 이 있으면 그 경로만 남긴다."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name("." + p.name + ".tmp")
    n = 0
    with open(tmp, "w", encoding="utf-8") as fh:
        for rel in sorted(facts):
            if keep is not None and rel not in keep:
                continue
            fh.write(json.dumps(facts[rel], ensure_ascii=False) + "\n")
            n += 1
    os.replace(tmp, p)
    return n


def _fresh(fact: dict | None, f: dict, use_mtime: bool) -> bool:
    """장부의 사실이 지금 파일과 같은 판인가 — 크기(DGX 는 수정 시각까지). VM 의 시각은 rsync 목록의 지역 시각이라 크기만 본다."""
    if not fact or int(fact.get("size") or -1) != int(f.get("size") or 0):
        return False
    return not use_mtime or int(float(fact.get("mtime") or 0)) == int(float(f.get("mtime") or 0))


def _key(f: dict) -> tuple[str, int]:
    return (f.get("filename") or f["rel"].rsplit("/", 1)[-1], int(f.get("size") or 0))


def apply_real_ext(files: list[dict], facts: dict[str, dict], root: Path | None = None, use_mtime: bool = False) -> int:
    """확장자로 알 수 없는 파일(확장자 없음·「…다운로드」)의 형식 값을 실제 문서 형식으로. root 가 있으면(DGX) 파일 머리를 읽어
    장부를 채우고, 없으면(VM) 장부만 본다. 바꾼 수를 돌려준다."""
    n = 0
    for f in files:
        name = f.get("filename") or f["rel"].rsplit("/", 1)[-1]
        if not doc_formats.needs_sniff(name, f.get("ext")):
            continue
        fact = facts.get(f["rel"])
        if root is not None and not (_fresh(fact, f, use_mtime) and "real" in fact):
            fact = dict(fact) if _fresh(fact, f, use_mtime) else {}      # 판이 바뀌었으면 옛 지문도 버린다
            fact.update(rel=f["rel"], size=f["size"], mtime=f.get("mtime"), real=doc_formats.sniff_doc_ext(root / f["rel"]))
            facts[f["rel"]] = fact
        if _fresh(fact, f, use_mtime) and fact.get("real"):
            f["ext"] = fact["real"]
            n += 1
    return n


def hash_candidates(files: list[dict]) -> list[dict]:
    """지문을 낼 파일 — 이름·크기가 같은 묶음(둘 이상) 가운데 처리 대상 문서 형식인 것. 작은 파일부터(예산 안에 묶음을 많이 풀게)."""
    count = Counter(_key(f) for f in files)
    doc = set(doc_formats.DOC_EXT)
    out = [f for f in files if count[_key(f)] > 1 and int(f.get("size") or 0) > 0 and (f.get("ext") or "") in doc]
    return sorted(out, key=lambda f: (int(f.get("size") or 0), f["rel"]))


def fill_digests(files: list[dict], facts: dict[str, dict], root: Path, budget_s: float = 1200.0) -> dict:
    """DGX: 지문이 없거나 판이 바뀐 후보만 읽어 장부에 적는다. 시간 예산을 넘으면 멈추고 나머지는 다음 회차에."""
    t0 = time.time()
    hashed = pending = failed = 0
    for f in hash_candidates(files):
        fact = facts.get(f["rel"])
        if _fresh(fact, f, True) and fact.get("sha256"):
            continue
        if time.time() - t0 > budget_s:
            pending += 1
            continue
        try:
            sha = file_sha256(root / f["rel"])
        except OSError:
            failed += 1
            continue
        keep = {k: v for k, v in (fact or {}).items() if k == "real"} if _fresh(fact, f, True) else {}
        facts[f["rel"]] = {"rel": f["rel"], "size": f["size"], "mtime": f.get("mtime"), "sha256": sha, **keep}
        hashed += 1
    return {"hashed": hashed, "pending": pending, "failed": failed}


def assign_dups(files: list[dict], facts: dict[str, dict], use_mtime: bool = False) -> dict:
    """files 순서대로 dup_of 를 정한다 — 이름·크기가 같고 지문도 같은 앞의 파일. 지문이 다르면 중복이 아니다.
    지문을 모르는 파일은 예전처럼 이름·크기 묶음의 첫 파일을 가리킨다. 집계(내용이 달라 중복에서 빠진 수·지문 모름)를 돌려준다."""
    count = Counter(_key(f) for f in files)
    first: dict = {}
    first_sha: dict = {}
    split = unknown = 0
    for f in files:
        k = _key(f)
        rel = f["rel"]
        if count[k] < 2:
            f["dup_of"] = ""
            continue
        head = first.setdefault(k, rel)
        fact = facts.get(rel)
        sha = fact.get("sha256") if _fresh(fact, f, use_mtime) else None
        if not sha:
            f["dup_of"] = "" if head == rel else head
            unknown += head != rel
            continue
        rep = first_sha.setdefault((k, sha), rel)
        f["dup_of"] = "" if rep == rel else rep
        split += rep == rel and head != rel
    return {"split_by_content": split, "unknown_digest": unknown}
