"""절을 못 채운 까닭 — 절 작성마다 비거나 덜 찬 절을 원인 갈래로 나눠 남기고, 다음 작성의 맥락·담당자 안내에 쓴다.

왜 필요한가. 절 작성 에이전트가 절을 비워 두면 그 까닭은 답글 속 긴 문장에만 있었다(10/9 시험: 회의록의 「상정 안건」은 이번 회의의
안건지가 있어야 쓸 수 있는데 「근거 조각이 비어 있어」로만 끝남). 까닭을 모아 두면
  - 담당자에게 무엇을 올리면 이어 쓰는지 한 줄로 알리고,
  - 같은 절을 다시 쓸 때 「지난번에 무엇 때문에 비었다」를 재료 맨 앞에 두며,
  - 절·갈래별로 모아 검색(재료를 못 찾음)·양식(근거 위치)·자료(담당자만 가진 문서) 가운데 어디가 약한지 본다(scripts/181).

원인 갈래(모델이 아니라 규칙으로 — 답글 문장은 증거로만 남긴다):
  needs_user_document  절의 근거가 담당자만 가진 자료(회의 녹취·안건지·출석부·설문·집행 내역 …)인데 재료에 없다
  no_material          검색이 재료를 하나도 못 찾았다
  material_mismatch    재료는 왔는데 이 절에 쓸 갈래가 아니었다(모델이 쓰지 않음)
  partial              썼지만 비워 둔 값·「확인 필요」가 남았다
기록: data/platform/fill_gaps.jsonl (한 줄 = 절 작성 한 번)
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

LOG = "fill_gaps.jsonl"
# 담당자만 가진 자료 — 문서함 검색으로는 대신할 수 없다(이번 회의·이번 회차·이번 집행의 기록)
# (지난 연차 회의록·실적보고서처럼 문서함에 쌓이는 자료는 넣지 않는다 — 그것은 검색 문제다)
USER_DOC = re.compile(r"녹취|메모|안건지|서명부|출석부|수료 기록|설문|정산|집행 내역|운영 기록|소집 공문|변경 요청서|증빙|담당자 입력")
_SRC = re.compile(r"근거\s*—\s*([^.]+)")
_CHECK = re.compile(r"확인\s*필요")


def needs_of(materials: str) -> str:
    """절 재료(작성 지침)의 「근거 — …」 — 이 절이 무엇을 근거로 쓰는가. 없으면 빈 문자열."""
    m = _SRC.search(materials or "")
    return m.group(1).strip() if m else ""


def _written(ops: list[dict]) -> tuple[int, int]:
    """(넣은 글자 수, 채운 칸 수)."""
    chars = sum(len(str(o.get("text") or "").strip()) for o in ops if o.get("op") in ("insert", "replace", "table"))
    cells = sum(1 for o in ops if o.get("op") == "fill" for c in (o.get("cells") or [])
                if isinstance(c, dict) and str(c.get("text") or "").strip())
    return chars, cells


def analyze(heading: str, ops: list[dict], materials: str, reply: str = "", asks: list | None = None) -> dict | None:
    """절 작성 한 번의 결과 → 못 채운 까닭 기록(다 채웠으면 None)."""
    chars, cells = _written(ops or [])
    n_mat = (materials or "").count("《")                     # 재료 블록 수(render_materials 의 《문서 제목》)
    needs = needs_of(materials)
    text = " ".join(str(o.get("text") or "") for o in ops or []) + " " + " ".join(
        str(c.get("text") or "") for o in ops or [] if o.get("op") == "fill" for c in (o.get("cells") or []) if isinstance(c, dict))
    checks = len(_CHECK.findall(text))
    if chars == 0 and cells == 0:
        if needs and USER_DOC.search(needs):
            reason = "needs_user_document"
        elif n_mat == 0:
            reason = "no_material"
        else:
            reason = "material_mismatch"
    elif asks or checks:
        reason = "partial"
    else:
        return None
    return {"heading": heading, "reason": reason, "needs": needs, "materials": n_mat, "chars": chars, "cells": cells,
            "checks": checks, "asks": [a.get("name") if isinstance(a, dict) else str(a) for a in (asks or [])][:8],
            "reply": (reply or "")[:400]}


def record(data_dir: Path | str, rec: dict) -> None:
    import datetime as _dt
    try:
        rec = dict(rec, at=_dt.datetime.now().astimezone().isoformat(timespec="seconds"))
        with (Path(data_dir) / LOG).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass                                                    # 기록이 실패해도 작성은 계속


def user_message(rec: dict) -> str:
    """담당자에게 한 줄 — 무엇이 있으면 이어 쓰는가."""
    need = rec.get("needs") or "이 절의 근거 자료"
    return {
        "needs_user_document": f"이 절은 「{need}」가 있어야 쓸 수 있어 비워 두었습니다 — 그 자료를 프로젝트에 올리거나 대화에 붙이면 이어 씁니다.",
        "no_material": f"문서함에서 이 절의 재료를 찾지 못해 비워 두었습니다 — 「{need}」를 올려 주시면 그것으로 씁니다.",
        "material_mismatch": f"찾은 자료가 이 절에 맞는 갈래가 아니어서 비워 두었습니다 — 「{need}」가 필요합니다.",
        "partial": "일부 값은 근거가 없어 비워 두었습니다(「확인 필요」 표시) — 값을 알려 주시면 채웁니다.",
    }.get(rec.get("reason", ""), "")


def load(data_dir: Path | str, limit: int = 5000) -> list[dict]:
    p = Path(data_dir) / LOG
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def context_hint(data_dir: Path | str, heading: str) -> str:
    """같은 절을 다시 쓸 때 재료 맨 앞에 둘 말 — 지난 작성에서 이 절이 비었던 까닭과 필요한 자료."""
    recs = [r for r in load(data_dir) if r.get("heading") == heading and r.get("reason") != "partial"]
    if not recs:
        return ""
    why = Counter(r["reason"] for r in recs).most_common(1)[0][0]
    need = next((r.get("needs") for r in reversed(recs) if r.get("needs")), "")
    label = {"needs_user_document": "담당자만 가진 자료가 없어서", "no_material": "문서함에서 재료를 못 찾아서",
             "material_mismatch": "찾은 자료가 이 절에 맞지 않아서"}.get(why, why)
    return (f"지난 작성에서 이 절은 {len(recs)}번 비었다({label}). 필요한 자료: {need or '작성 지침의 근거'}. "
            "이번 재료에 그 자료가 있는지 먼저 보고, 없으면 지어내지 말고 무엇이 필요한지 asks 로 남긴다.")


def summary(data_dir: Path | str) -> list[dict]:
    """절 제목별 원인 분포 — 어디가 약한지(검색·양식·자료)."""
    by: dict[str, Counter] = defaultdict(Counter)
    need: dict[str, str] = {}
    for r in load(data_dir):
        by[r.get("heading", "")][r.get("reason", "")] += 1
        if r.get("needs"):
            need[r.get("heading", "")] = r["needs"]
    return sorted(({"heading": h, "total": sum(c.values()), **dict(c), "needs": need.get(h, "")} for h, c in by.items()),
                  key=lambda x: -x["total"])
