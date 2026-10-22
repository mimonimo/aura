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


BUILD = 5          # 만드는 방식(build)이 바뀌면 올린다 — 정의가 같아도 운영의 양식이 새 방식으로 다시 만들어진다


def fingerprint(spec: dict) -> str:
    return hashlib.sha256((str(BUILD) + json.dumps(spec, ensure_ascii=False, sort_keys=True, default=str)).encode()).hexdigest()[:16]


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
    # 글자 모양 — 표지 제목·절 제목은 굵게 크기 차례로, 작성 지침은 작고 회색(구글 공통 양식과 같은 위계)
    try:
        cover_cp = doc.ensure_run_style(bold=True, size=18)
        head_cp = {1: doc.ensure_run_style(bold=True, size=14), 2: doc.ensure_run_style(bold=True, size=12),
                   3: doc.ensure_run_style(bold=True, size=11)}
    except Exception:                                  # 라이브러리 판이 달라 글자 모양을 못 만들면 바탕글 그대로
        cover_cp, head_cp = None, {}
    for i, line in enumerate(spec.get("cover") or []):
        doc.add_paragraph(line, style="바탕글", **({"char_pr_id_ref": cover_cp} if cover_cp and i == 0 else {}))
    doc.add_paragraph("", style="바탕글")
    for b in spec["blocks"]:
        if "h" in b:
            doc.add_paragraph("", style="바탕글")
            cp = head_cp.get(min(int(b["h"]), 3))
            doc.add_paragraph(b["text"], style="바탕글", **({"char_pr_id_ref": cp} if cp else {}))
        elif "guide" in b:
            box = doc.add_table(1, 1)
            box.set_cell_text(0, 0, f"【작성방법】 {b['guide']}")
        elif "p" in b:
            doc.add_paragraph(b["p"], style="바탕글")
        elif "table" in b:
            _table(doc, b["table"])
    # 쓸 자리(절 다음 빈 문단·라벨 칸·목록표)에 누름틀 — 기안기에서 사람·에이전트가 같은 이름으로 짚는다(webhwp_fields)
    # 표 모양은 학교 서식에서 캔 기본(머리줄·라벨 음영·굵게, 바깥 0.4mm·안쪽 0.12mm, 10pt — webhwp_style.HOUSE)
    from zzaimy.app import webhwp_fields, webhwp_style
    return webhwp_style.apply(webhwp_fields.fieldize(doc.to_bytes())[0])


# ── 기안기용 공통 공문 ── 행정업무규정 공문서 구성(두문·본문·결문)을 따른 학교 공문 서식. 쓸 자리는 build 가 누름틀로 박는다
# (표 라벨 칸 → 「칸:<라벨>」, 결재란 머리행 → 「표n:<직위>」, 번호 붙은 본문 항목 → 「절:<항목>」). 모르는 값(주소·전화)은 빈칸.
def _kv(rows):
    return {"table": {"columns": ["항목", "내용"], "rows": [list(r) for r in rows], "widths": [1, 4]}}


def _grid(rows, widths):
    return {"table": {"columns": list(rows[0]), "form": True, "rows": [list(r) for r in rows], "widths": widths}}


def _list(head, n=1, widths=None):
    return {"table": {"columns": head, "rows": [[""] * len(head) for _ in range(n)], "widths": widths or [1] * len(head)}}


_SIGN = _list(["기안자", "검토자", "결재권자"], 1)                       # 결재란 — 직위 머리행 + 서명 칸
_FOOT = _grid([["시행", "", "접수", ""], ["우편번호", "", "주소", ""], ["전화", "", "전송", ""],
               ["전자우편", "", "공개구분", ""]], [1, 2, 1, 2])

OFFICIAL = {
    "draft": {"id": "draft", "title": "기안문(내부결재) 공통 양식", "cover": ["영남이공대학교"], "blocks": [
        _kv([["수신", "내부결재"], ["(경유)", ""], ["제목", ""]]),
        {"p": ""},
        {"h": 1, "text": "1. 관련"}, {"h": 1, "text": "2. 추진 내용"},
        {"p": "붙임  1. (붙임 문서 이름) 1부.  끝."},
        {"p": ""}, _SIGN, {"p": ""}, _FOOT]},
    "outgoing": {"id": "outgoing", "title": "시행문(대외 발송) 공통 양식", "cover": ["영남이공대학교"], "blocks": [
        _kv([["수신", ""], ["(경유)", ""], ["제목", ""]]),
        {"p": ""},
        {"h": 1, "text": "1. 관련"}, {"h": 1, "text": "2. 요청 사항"},
        {"p": "붙임  1. (붙임 문서 이름) 1부.  끝."},
        {"p": ""}, {"p": "영남이공대학교총장"}, {"p": ""}, _SIGN, {"p": ""}, _FOOT]},
    "cooperation": {"id": "cooperation", "title": "협조문(부서 간) 공통 양식", "cover": ["업무 협조 요청"], "blocks": [
        _kv([["수신", ""], ["발신", ""], ["제목", ""], ["회신 기한", ""]]),
        {"p": ""},
        {"h": 1, "text": "1. 협조 배경"}, {"h": 1, "text": "2. 협조 요청 사항"},
        _list(["구분", "요청 내용", "기한", "담당"], 3, [1, 3, 1, 1]),
        {"h": 1, "text": "3. 문의"},
        {"p": "끝."}]},
    "brief": {"id": "brief", "title": "1쪽 요약 보고서 공통 양식", "cover": ["(보고서 제목)"], "blocks": [
        _kv([["보고 일자", ""], ["보고 부서", ""], ["보고자", ""]]),
        {"h": 1, "text": "1. 개요"}, {"h": 1, "text": "2. 현황"}, {"h": 1, "text": "3. 문제점 및 개선 방안"},
        {"h": 1, "text": "4. 향후 계획"},
        _list(["추진 과제", "일정", "담당"], 3, [3, 1, 1])]},
}


# ── 국고 사업 운영 문서 ── 사업을 하는 동안 늘 만드는 문서(예산 변경·장비 구입·성과지표 증빙·자체성과분석·협약 신청·출장 결과).
# 금액은 천원 단위, 사업명·과제·지표 같은 머리 값은 라벨 칸(「칸:…」), 본문 항목은 「절:…」, 내역 표는 「표n:…」 필드가 된다
GRANT = {
    "budget_change": {"id": "budget_change", "title": "예산 변경 신청서 공통 양식", "cover": ["예산 변경 신청서"], "blocks": [
        _kv([["사업명", ""], ["세부 과제", ""], ["신청 부서", ""], ["담당자", ""], ["신청 일자", ""]]),
        {"h": 1, "text": "1. 변경 사유"},
        {"h": 1, "text": "2. 변경 내역"},
        _list(["비목", "세목", "당초(천원)", "변경(천원)", "증감(천원)", "사유"], 3, [1, 1, 1, 1, 1, 2]),
        {"h": 1, "text": "3. 성과지표·사업 추진에 미치는 영향"},
        {"p": ""}, _SIGN]},
    "equipment": {"id": "equipment", "title": "기자재 구입 계획서 공통 양식", "cover": ["기자재 구입 계획서"], "blocks": [
        _kv([["사업명", ""], ["세부 과제", ""], ["사용 부서", ""], ["담당자", ""]]),
        {"h": 1, "text": "1. 구입 목적"},
        {"h": 1, "text": "2. 구입 내역"},
        _list(["품명", "규격", "수량", "단가(천원)", "금액(천원)", "활용 계획"], 3, [1.5, 2, 0.6, 1, 1, 2]),
        {"h": 1, "text": "3. 활용 및 관리 계획"},
        {"h": 1, "text": "4. 기대 효과"}]},
    "kpi_evidence": {"id": "kpi_evidence", "title": "성과지표 실적·증빙 공통 양식", "cover": ["성과지표 실적 및 증빙"], "blocks": [
        _kv([["성과지표", ""], ["지표 정의", ""], ["산출식", ""], ["목표값", ""], ["실적값", ""], ["달성률(%)", ""]]),
        {"h": 1, "text": "1. 실적 산출 근거"},
        _list(["연번", "증빙 자료", "내용", "수량", "비고"], 3, [0.6, 2, 3, 0.8, 1]),
        {"h": 1, "text": "2. 미달성 사유 및 개선 계획"}]},
    "self_review": {"id": "self_review", "title": "운영 결과 자체성과분석 공통 양식", "cover": ["운영 결과 자체성과분석"], "blocks": [
        _kv([["프로그램명", ""], ["운영 기간", ""], ["참여 인원", ""], ["집행 예산(천원)", ""], ["운영 부서", ""]]),
        {"h": 1, "text": "1. 운영 개요"},
        {"h": 1, "text": "2. 목표 대비 성과"},
        _list(["성과지표", "목표", "실적", "달성률(%)", "분석"], 3, [2, 1, 1, 1, 3]),
        {"h": 1, "text": "3. 만족도 조사 결과"},
        {"h": 1, "text": "4. 개선 사항 및 환류 계획"}]},
    "partner_apply": {"id": "partner_apply", "title": "산학협력 협약 체결 신청서 공통 양식", "cover": ["산학협력 협약 체결 신청서"], "blocks": [
        _grid([["기관명", "", "대표자", ""], ["사업자등록번호", "", "업종", ""], ["주소", "", "전화", ""],
               ["담당자", "", "전자우편", ""]], [1.2, 2, 1, 2]),
        {"h": 1, "text": "1. 협약 목적"},
        {"h": 1, "text": "2. 협력 내용"},
        _list(["협력 분야", "내용", "기간"], 3, [1.5, 4, 1.5]),
        {"p": "위와 같이 산학협력 협약 체결을 신청합니다."},
        {"p": "20    .    .    ."},
        _kv([["신청 기관", ""], ["대표자(서명)", ""]])]},
    "trip_report": {"id": "trip_report", "title": "출장·회의 결과 보고서 공통 양식", "cover": ["출장(회의) 결과 보고"], "blocks": [
        _kv([["관련 사업", ""], ["출장자", ""], ["기간", ""], ["장소", ""], ["목적", ""]]),
        {"h": 1, "text": "1. 주요 내용"},
        {"h": 1, "text": "2. 결과 및 후속 조치"},
        {"h": 1, "text": "3. 경비"},
        _list(["항목", "금액(원)", "비고"], 3, [2, 1, 2]),
        {"p": "붙임  1. 증빙 자료 1부.  끝."}]},
}


def ensure_all(db, base: Path, *, owner: str = "zzaimy") -> list[dict]:
    """공통 양식 hwpx 를 한글 문서함 「공통/공통 양식」 에 둔다 — 없거나 정의가 바뀐 것만 새로 만든다(앞 판은 .판/ 으로).
    사업 문서(구글 공통 양식과 같은 정의)는 「공통 양식」, 기안기용 공문은 「공통 양식/공문」."""
    from zzaimy.app import webhwp
    from zzaimy.ingest.gdocs_templates import SPECS

    out = []
    for sid, spec in [*SPECS.items(), *OFFICIAL.items(), *GRANT.items()]:
        folder = f"{FOLDER}/공문" if sid in OFFICIAL else (f"{FOLDER}/국고 사업" if sid in GRANT else FOLDER)
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
                             folder=folder)
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
