"""지난 문서 양식 정리(scripts/208, app/past_forms) — 판정(개인 서류·채워진 제출본·중복·깨짐), 사업·목적 자리, 적용(옮김·휴지통·백업)·
다시 돌리기·지문이 다르면 멈춤·되돌리기. 이름·사업은 모두 지어낸 것."""
from __future__ import annotations

import importlib.util
import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from zzaimy.app import past_forms as pf
from zzaimy.app import webhwp
from zzaimy.app import webhwp_fields as wf
from zzaimy.app.db import Database

ROOT = Path(__file__).resolve().parent.parent


def _script():
    spec = importlib.util.spec_from_file_location("tidy208", ROOT / "scripts" / "208_past_forms_tidy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hwpx(section: str, fields: bool = True) -> bytes:
    from hwpx import HwpxDocument
    base = HwpxDocument.new().to_bytes()
    zin = zipfile.ZipFile(io.BytesIO(base))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            body = zin.read(info.filename)
            if info.filename == "Contents/section0.xml":
                body = re.sub(r"(<hs:sec\b[^>]*>)", r"\1" + section, body.decode(), count=1).encode()
            z.writestr(info, body)
    data = buf.getvalue()
    return wf.fieldize(data)[0] if fields else data


_ID = [100]


def _p(text: str) -> str:
    _ID[0] += 1
    run = f'<hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run>' if text else '<hp:run charPrIDRef="0"/>'
    return f'<hp:p id="{_ID[0]}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">{run}</hp:p>'


def _table(rows: list[list[str]]) -> str:
    _ID[0] += 1
    tid = _ID[0]
    trs = "".join("<hp:tr>" + "".join(f"<hp:tc><hp:subList>{_p(c)}</hp:subList></hp:tc>" for c in r) + "</hp:tr>" for r in rows)
    _ID[0] += 1
    return (f'<hp:p id="{_ID[0]}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0">'
            f'<hp:tbl id="{tid}">{trs}</hp:tbl></hp:run></hp:p>')


def blank_plan(extra: str = "") -> bytes:
    return _hwpx(_p(f"사업 계획서 {extra}") + _p("※ 작성 요령: ○○ 칸은 기관에 맞게 적는다")
                 + _table([["사업명", ""], ["기간", ""], ["예산", ""], ["담당 부서", ""]]))


def program_form() -> bytes:
    return _hwpx(_p("가나혁신 결과보고서") + _p("가나혁신 사업의 결과를 ○○ 에 맞게 적는다")
                 + _table([["과제명", ""], ["성과", ""], ["비고", ""]]))


def personal_resume() -> bytes:
    return _hwpx(_p("이력서") + _table([["성명", "김가나"], ["연락처", "010-2345-6789"], ["학력", "가나대학교"]]))


def filled_application() -> bytes:
    return _hwpx(_p("참가 신청서")
                 + _table([["신청자", "박다라"], ["소속", "가나학과"], ["신청일", "2024. 3. 15."], ["금액", "1,250,000"],
                           ["기간", "2024. 4. 1. ~ 2024. 5. 30."], ["담당자", "이마바"]]))


def _setup(tmp_path):
    db = Database(tmp_path / "platform.db")
    base = tmp_path
    rows = [{"key": "k1", "name": "가나다라 혁신사업", "terms": ["가나혁신"], "ids": {"k1", "program:가나다라혁신사업"},
             "merged_into": "", "period": "2020~2022", "years": {}}]
    progs = pf.Programs(db, base, rows=rows)
    return db, base, progs


def _add(db, base, name, data, folder, src):
    return webhwp.add(db, base, webhwp.COMMON, name, data, owner="zzaimy", source=src, folder=folder)


def test_measure_and_judge_thresholds():
    m = pf.measure(blank_plan())
    assert m["ok"] and m["fields"] >= 3 and m["blank_share"] > 0.3 and pf.template_signals(m) >= 2
    assert pf.judge(m, "사업 계획서", "계획서") is None
    m = pf.measure(personal_resume())
    assert m["mobiles"] == 1 and m["name_count"] == 1
    assert pf.judge(m, "이력서", "이력·소개서")[0] == "personal"
    m = pf.measure(filled_application())
    assert m["blank_share"] == 0 and pf.concrete(m) >= pf.FILLED_CONCRETE
    assert pf.judge(m, "참가 신청서", "신청서")[0] == "filled"
    assert pf.judge(pf.measure(b"not a zip"), "x", "기타")[0] == "broken"
    # 개인 정보 꼴이지만 보기 값(010-0000-0000, 홍길동)은 세지 않는다
    m = pf.measure(_hwpx(_p("신청서") + _table([["성명", "홍길동"], ["연락처", "010-0000-0000"], ["소속", ""]])))
    assert m["mobiles"] == 0 and m["name_count"] == 0


def test_classify_program_and_purpose(tmp_path):
    db, base, progs = _setup(tmp_path)
    c = pf.classify(program_form(), "가나혁신 결과보고서", progs)
    assert c["action"] == pf.KEEP and c["program"] == "가나다라 혁신사업" and c["purpose"] == "결과보고서"
    assert c["stage"] == "past" and c["folder"] == "지난 사업 문서/가나다라 혁신사업/결과보고서"
    c = pf.classify(blank_plan(), "사업 계획서", progs)
    assert c["program"] == "" and c["folder"] == "공통 양식/계획서"
    assert pf.mask_title("김가나 이력서", ["김가나"]) == "김○○ 이력서"
    assert pf.mask_title("이다라 재직증명서") == "이○○ 재직증명서"
    assert pf.mask_title("사업 계획서") == "사업 계획서"


def test_plan_apply_idempotent_and_restore(tmp_path):
    t = _script()
    db, base, progs = _setup(tmp_path)
    keep = _add(db, base, "사업 계획서.hwpx", blank_plan(), "양식/계획서", "양식:doc-1")
    dup = _add(db, base, "사업 계획서 사본.hwpx", blank_plan(), "양식/사업", "양식:doc-2")
    prog = _add(db, base, "가나혁신 결과보고서.hwpx", program_form(), "공통 양식/결과보고서", "양식:common-3")
    pers = _add(db, base, "김가나 이력서.hwpx", personal_resume(), "양식/이력서", "양식:doc-4")
    fill = _add(db, base, "참가 신청서.hwpx", filled_application(), "양식/신청서", "양식:doc-5")
    broken = _add(db, base, "깨진 양식.hwpx", b"PKnot really", "양식/기타", "양식:doc-6")
    platform = _add(db, base, "회의록.hwpx", blank_plan("회의"), "공통 양식/회의록", "양식:minutes")
    mine = webhwp.add(db, base, "기획처", "내 문서.hwpx", blank_plan(), owner="kim", folder="양식")
    db.set_setting(f"webhwp_lock:{keep}", json.dumps({"user": "", "at": 0}))
    db.set_setting("chat_note:1", json.dumps({"p": webhwp.get(db, keep)["path"]}, ensure_ascii=False))

    plan = t.build_plan(db, base, progs)
    by = {e["fid"]: e for e in plan["entries"]}
    assert set(by) == {keep, dup, prog, pers, fill, broken}                 # 플랫폼 양식·다른 부서 문서는 대상 아님
    assert by[keep]["action"] == "keep" and by[keep]["target"] == "공통 양식/계획서"
    assert by[dup]["reason_code"] == "duplicate" and by[dup]["dup_of"] == keep
    assert by[prog]["target"] == "지난 사업 문서/가나다라 혁신사업"         # 양식이 적은 사업은 목적 폴더 없이
    assert by[pers]["reason_code"] == "personal" and by[pers]["title_masked"] == "김○○ 이력서"
    assert by[fill]["reason_code"] == "filled" and by[broken]["reason_code"] == "broken"
    out = tmp_path / "out"
    out.mkdir()
    (out / "tidy_plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    t.write_md(plan, out / "tidy_plan.md")
    md = (out / "tidy_plan.md").read_text(encoding="utf-8")
    assert "김가나" not in md and "김○○ 이력서" in md and "개인 서류" in md and "가나다라 혁신사업" in md

    old_keep = webhwp.get(db, keep)["path"]
    bk = tmp_path / "bk"
    assert t.cmd_apply(db, base, out / "tidy_plan.json", bk, out) == 0
    k = webhwp.get(db, keep)
    assert webhwp.folder_of(base, k["path"]) == "공통 양식/계획서" and Path(k["path"]).exists() and not Path(old_keep).exists()
    assert webhwp.meta(db, keep)["source"] == "양식:doc-1" and webhwp.locked(db, keep)
    assert k["path"] in db.get_setting("chat_note:1", "")                  # 경로를 담은 설정도 따라간다
    assert webhwp.folder_of(base, webhwp.get(db, prog)["path"]) == "지난 사업 문서/가나다라 혁신사업"
    for fid in (dup, pers, fill, broken):
        assert webhwp.get(db, fid) is None
    trash = webhwp.root(base) / "공통" / ".휴지통"
    assert len([x for x in trash.iterdir() if x.is_file()]) == 4
    assert (bk / "files_rows.json").exists() and len(json.loads((bk / "files_rows.json").read_text())) == 6
    assert any(bk.joinpath("files").rglob("김가나 이력서.hwpx"))
    assert not (webhwp.root(base) / "공통" / "양식").exists()                # 빈 옛 폴더는 지운다
    assert webhwp.get(db, platform) and webhwp.get(db, mine)
    assert json.loads((out / "tidy_removed.json").read_text()) == {"2": "duplicate", "4": "personal", "5": "filled", "6": "broken"}
    # 다시 돌려도 바뀌는 것 없음
    assert t.cmd_apply(db, base, out / "tidy_plan.json", tmp_path / "bk2", out) == 0 and not (tmp_path / "bk2").exists()
    # 되돌리기
    assert t.cmd_restore(db, base, bk) == 0
    assert webhwp.get(db, pers)["path"].endswith("김가나 이력서.hwpx") and Path(webhwp.get(db, pers)["path"]).exists()
    assert webhwp.get(db, keep)["path"] == old_keep and Path(old_keep).exists()


def test_apply_refuses_when_fingerprint_changed(tmp_path):
    t = _script()
    db, base, progs = _setup(tmp_path)
    a = _add(db, base, "사업 계획서.hwpx", blank_plan(), "양식/계획서", "양식:doc-1")
    b = _add(db, base, "김가나 이력서.hwpx", personal_resume(), "양식/이력서", "양식:doc-4")
    plan = t.build_plan(db, base, progs)
    out = tmp_path / "out"
    out.mkdir()
    (out / "tidy_plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    Path(webhwp.get(db, a)["path"]).write_bytes(blank_plan("고침"))            # 계획 뒤 누가 고쳤다
    assert t.cmd_apply(db, base, out / "tidy_plan.json", tmp_path / "bk", out) == 2
    assert webhwp.get(db, b) and webhwp.folder_of(base, webhwp.get(db, a)["path"]) == "양식/계획서"
    assert not (tmp_path / "bk").exists()


def test_scope_and_folders():
    assert pf.in_scope("양식/계획서", "양식:doc-1") == "양식"
    assert pf.in_scope("공통 양식/공문", "양식:official") == ""             # 플랫폼이 만든 것은 대상 아님
    assert pf.in_scope("공통 양식/계획서", "양식:common-9") == "공통 양식"
    assert pf.in_scope("작업", "doc:3") == ""
    assert pf.platform_made("양식:plan") and not pf.platform_made("양식:doc-3")
    assert pf.folder_for("가나 사업", "예산", small=True) == "지난 사업 문서/가나 사업"
    assert pf.purpose_of("2024 운영 결과보고서") == "결과보고서" and pf.purpose_of("무제") == "기타"


@pytest.mark.parametrize("text,code", [("", "empty")])
def test_empty_file(text, code):
    m = pf.measure(_hwpx(_p(text), fields=False))
    assert pf.judge(m, "무제", "기타")[0] == code
