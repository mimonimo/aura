"""문서를 올릴 때 사업 고르기·새 사업 등록 — 올린 문서를 그 사업의 연도 묶음(보관 프로젝트)에 넣는다.

- 기존 사업: 그래프의 사업 id(program:…) — 라이브러리 「사업별로 보기」와 같은 목록
- 새 사업: 외부 확인 장부(kg_external.json)에 항목을 더하고(출처 확인 전 표시) 사업 id 를 만든다. 지원기관을 적으면
  사업 목록 나무(program_tree)의 대분류·중분류에 바로 자리한다. 그래프에는 다음 재구축 때 사업 노드로 선다
문서는 archive.program_project(사업 × 연도 묶음)에 들어가 라이브러리 사업 목록·사업 그래프에서 함께 보인다.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from zzaimy.app.program_tree import Ledger

_NAME = re.compile(r"^.{3,80}$")


def slug(name: str) -> str:
    return "program:" + re.sub(r"[^0-9a-z가-힣]+", "", (name or "").lower())[:60]


def register(ledger_path: Path, name: str, ministry: str = "", by: str = "") -> tuple[str, str, str]:
    """새 사업 — (사업 id, 보일 이름, 오류). 장부에 이미 같은 사업(이름·별칭)이 있으면 새로 만들지 않고 그 항목을 쓴다."""
    name = " ".join((name or "").split())
    ministry = " ".join((ministry or "").split())[:40]
    if not _NAME.match(name):
        return "", "", "사업 이름은 3~80자로 적습니다"
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {"programs": []}
    hit = Ledger(ledger).find(name)
    if hit is not None:
        return slug(hit.get("name") or name), hit.get("name") or name, ""
    entry = {"name": name, "terms": [name], "category": "", "ministry": ministry, "sources": [],
             "checked": time.strftime("%Y-%m-%d"), "registered_by": by,
             "note": "라이브러리에서 문서를 올리며 담당자가 등록한 사업 — 외부 출처 확인 전"}
    ledger.setdefault("programs", []).append(entry)
    if ledger_path.is_file():
        hist = ledger_path.parent / "history"
        hist.mkdir(exist_ok=True)
        shutil.copy2(ledger_path, hist / f"kg_external.{time.strftime('%Y%m%d%H%M%S')}.json")
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    return slug(name), name, ""
