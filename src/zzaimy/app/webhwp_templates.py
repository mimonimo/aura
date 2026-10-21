"""웹 한글 기안기용 공통 양식 — 구글 문서 공통 양식(ingest.gdocs_templates.SPECS)과 같은 절·표를 한글(hwpx)로 만든다.

왜. 공통 양식으로 쓴 작업본은 한글 원본 서식이 없어 지금까지 Word 로만 내보냈다. 같은 정의에서 hwpx 양식을 만들면
절 제목이 그대로 맞으므로 「한글로 내보내기」 가 서식 보존 채우기(hwpx_fill)로 작업본 내용을 이 양식에 채울 수 있고,
기안기에서 바로 열어 고칠 수 있다. 양식의 모양·구조는 구글 쪽 정의 하나만 고치면 두 편집기에 같이 따라온다(입력 하나 → 결과 둘).

- 절 제목(H) → 제목 문단, 작성 지침(G) → 【작성방법】 상자(한 칸 표 — 제출본 만들 때 remove_boxes 로 지운다),
  빈 문단(P) → 빈 문단, 표(T·TG·FORM·KV) → 같은 칸 수의 표(머리행·라벨 글자, 칸 합치기).
- 만든 파일은 한글 문서함 「공통/공통 양식」 폴더에 둔다(ensure_all). 정의가 바뀌면(지문이 달라지면) 다시 만든다.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

FOLDER = "공통 양식"


def _title(spec: dict) -> str:
    return spec["title"].replace("(구글 독스)", "").replace("공통 양식", "").strip() + " 공통 양식"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _table(doc, t: dict) -> None:
    head = t.get("head") or ([] if t.get("form") or t.get("columns") == ["항목", "내용"] else [t["columns"]])
    rows = [*head, *t.get("rows", [])]
    if not rows:
        return
    n = max(len(r) for r in rows)
    tbl = doc.add_table(len(rows), n)
    for ri, row in enumerate(rows):
        for ci in range(n):
            text = row[ci] if ci < len(row) else ""
            if text:
                try:
                    tbl.set_cell_text(ri, ci, str(text), logical=True)
                except TypeError:
                    tbl.set_cell_text(ri, ci, str(text))
    # TG 의 merges 는 머리행 포함 격자 좌표, FORM 도 같은 격자 — (행, 열, 행 수, 열 수)
    for (r, c, nr, nc) in t.get("merges") or []:
        if nr <= 1 and nc <= 1:
            continue
        try:
            tbl.merge_cells(r, c, r + nr - 1, c + nc - 1)
        except Exception:
            pass
    try:
        w = [float(x) for x in t.get("widths") or []]
        if len(w) == n and sum(w) > 0:
            total = 42520                      # A4 본문 폭(HWPUNIT, 약 150mm)
            tbl.set_column_widths([int(total * x / sum(w)) for x in w])
    except Exception:
        pass


def build(spec: dict) -> bytes:
    """공통 양식 정의 하나 → hwpx 바이트."""
    from hwpx import HwpxDocument

    doc = HwpxDocument.new()
    for line in spec.get("cover") or []:
        doc.add_paragraph(line, style="바탕글")
    doc.add_paragraph("", style="바탕글")
    for b in spec["blocks"]:
        if "h" in b:
            doc.add_paragraph("", style="바탕글")
            doc.add_paragraph(b["text"], style="바탕글")
        elif "guide" in b:
            box = doc.add_table(1, 1)
            box.set_cell_text(0, 0, f"【작성방법】 {b['guide']}")
        elif "p" in b:
            doc.add_paragraph(b["p"], style="바탕글")
        elif "table" in b:
            _table(doc, b["table"])
    return doc.to_bytes()


def ensure_all(db, base: Path, *, owner: str = "zzaimy") -> list[dict]:
    """공통 양식 hwpx 를 한글 문서함 「공통/공통 양식」 에 둔다 — 없거나 정의가 바뀐 것만 새로 만든다(앞 판은 .판/ 으로)."""
    from zzaimy.app import webhwp
    from zzaimy.ingest.gdocs_templates import SPECS

    out = []
    for sid, spec in SPECS.items():
        fp = fingerprint(spec)
        key = f"webhwp_template:{sid}"
        try:
            cur = json.loads(db.get_setting(key, "{}") or "{}")
        except ValueError:
            cur = {}
        f = webhwp.get(db, int(cur["id"])) if cur.get("id") else None
        # 정의가 같고 파일 내용도 만든 그대로일 때만 둔다 — 누가 덮어썼으면(지문이 다르면) 정의대로 다시 만든다
        same = f and Path(f["path"]).exists() and cur.get("fp") == fp and cur.get("sha") == _sha(Path(f["path"]).read_bytes())
        if same:
            out.append({"id": f["id"], "spec": sid, "name": f["name"], "made": False})
            continue
        data = build(spec)
        if f and Path(f["path"]).exists():
            webhwp.save(db, base, f["id"], data, user=owner)
            fid = f["id"]
        else:
            fid = webhwp.add(db, base, webhwp.COMMON, f"{_title(spec)}.hwpx", data, owner=owner, source=f"양식:{sid}",
                             folder=FOLDER)
        db.set_setting(key, json.dumps({"id": fid, "fp": fp, "sha": _sha(data)}))
        out.append({"id": fid, "spec": sid, "name": webhwp.get(db, fid)["name"], "made": True})
    return out


def path_for(db, spec_id: str) -> Path | None:
    """이 공통 양식의 hwpx 경로 — 한글 완성본을 만들 때 원본 서식으로 쓴다. 없으면 None."""
    from zzaimy.app import webhwp
    try:
        cur = json.loads(db.get_setting(f"webhwp_template:{spec_id}", "{}") or "{}")
    except ValueError:
        return None
    f = webhwp.get(db, int(cur["id"])) if cur.get("id") else None
    if not f or not Path(f["path"]).exists() or cur.get("sha") != _sha(Path(f["path"]).read_bytes()):
        return None                                   # 만든 그대로가 아니면 쓰지 않는다(호출부가 ensure_all 로 다시 만든 뒤 부른다)
    return Path(f["path"])
