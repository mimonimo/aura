"""공문 결문 연락처는 기관 정보에서만 · 설계 양식의 표지(제목·부제·작성 연월·기관명) — 11-09 시연에서 다른 대학 홈페이지·전자우편이 결문에 들어갔고, 계획서 첫 장이 밋밋했다."""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from tests.test_webhwp import _setup
from zzaimy.app import institution
from zzaimy.app import webhwp, webhwp_agent as wa
from zzaimy.app import webhwp_form_create as fc
from zzaimy.app import webhwp_templates as wt
from zzaimy.app import webhwp_write as ww


def test_contact_key_and_block():
    assert institution.contact_key("칸:전송(팩스)") == "팩스" and institution.contact_key("홈페이지") == "홈페이지"
    assert institution.contact_key("칸:사업명") is None
    assert institution.has_contact_block(["칸:우편번호", "칸:주소", "칸:홈페이지"])
    assert not institution.has_contact_block(["칸:주소", "칸:전화"])           # 다른 서식의 주소·전화 칸은 건드리지 않는다


def test_official_foot_takes_institution_values():
    loc = wt.localize(wt.OFFICIAL["draft"], {"대학명": "가나대학교", "홈페이지": "www.gana.ac.kr", "우편번호": "12345"})
    assert "www.gana.ac.kr" in repr(loc) and "12345" in repr(loc) and "{홈페이지}" not in repr(loc)
    bare = wt.localize(wt.OFFICIAL["draft"], {})
    assert "{전자우편}" not in repr(bare) and "{팩스}" not in repr(bare)


def test_fill_keeps_contact_fields_to_institution(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    wt.ensure_all(db, base)
    hw = wa.HwpTools(db, base, 77, "kim", "기획처", {"id": 1, "name": "AI 사업"}, lambda: [])
    tid = next(line.split()[0] for line in hw.forms({"query": "기안문"}).splitlines() if "기안문" in line)
    hw.start({"form": tid, "title": "내부 보고"})
    fid = wa.bound(db, 77)
    names = {x["name"] for x in ww.fields(Path(webhwp.get(db, fid)["path"]).read_bytes())}
    home = next(n for n in names if institution.contact_key(n) == "홈페이지")
    # 자료에서 찾은 남의 기관 값 — 기관 정보가 비었으면 넣지 않고 까닭을 알린다
    out = hw.fill({"values": {home: "www.other.ac.kr", "칸:제목": "AI 사업 보고"}})
    assert "비워 둠" in out and home not in out.split("비워 둠")[0].split("채웠다:")[-1]
    vals = {x["name"]: x["text"] for x in ww.fields(Path(webhwp.get(db, fid)["path"]).read_bytes())}
    assert vals[home] == "" and vals["칸:제목"] == "AI 사업 보고"
    # 기관 정보가 있으면 에이전트가 무엇을 주든 기관 정보 값으로
    institution.set_fact(db, "홈페이지", "www.ours.ac.kr")
    hw.fill({"values": {home: "www.other.ac.kr"}})
    vals = {x["name"]: x["text"] for x in ww.fields(Path(webhwp.get(db, fid)["path"]).read_bytes())}
    assert vals[home] == "www.ours.ac.kr"
    # 「담당자가 알려 줬다」(from_user)도 대화에 그 글이 있을 때만
    hw.fill({"values": {home: "www.fake.ac.kr"}, "from_user": True})
    vals = {x["name"]: x["text"] for x in ww.fields(Path(webhwp.get(db, fid)["path"]).read_bytes())}
    assert vals[home] == "www.ours.ac.kr"
    db.add_chat(77, "user", "홈페이지는 www.said.ac.kr 로 적어 줘")
    hw.fill({"values": {home: "www.said.ac.kr"}, "from_user": True})
    vals = {x["name"]: x["text"] for x in ww.fields(Path(webhwp.get(db, fid)["path"]).read_bytes())}
    assert vals[home] == "www.said.ac.kr"


def _texts(data: bytes) -> list[str]:
    x = zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode()
    return [t for t in re.findall(r"<hp:t>([^<]*)</hp:t>", x) if t.strip()]


def test_designed_form_cover_page_and_header():
    d = {"title": "AI 교육 사업 계획서", "cover": True, "subtitle": "특성화 사업 연계", "summary": ["사업명", "사업기간"],
         "sections": [{"level": 1, "title": "사업 개요", "guide": "개요", "length": "1쪽 이내", "table": [], "table_kind": "", "diagram": ""}]}
    clean, probs = fc.validate(d)
    assert not probs
    t = _texts(fc.build(clean, {"대학명": "가나대학교"}))
    assert t[:2] == ["AI 교육 사업 계획서", "특성화 사업 연계"] and "가나대학교" in t[:5]
    assert any(re.fullmatch(r"20\d\d\. \d{1,2}\.", x) for x in t[:5])            # 작성 연월
    d["cover"] = False
    clean, _ = fc.validate(d)
    t = _texts(fc.build(clean, {"대학명": "가나대학교"}))
    assert re.fullmatch(r"가나대학교 · 20\d\d\. \d{1,2}\.", t[2])               # 표지 없는 문서도 머리에 기관·작성 연월
    assert "(행정기관명)" in _texts(fc.build(clean))[2]                          # 기관 정보가 없으면 안내 글


def test_template_school_placeholder_takes_institution_name():
    spec = {"id": "x", "title": "회의록", "cover": ["「○○○○ 위원회」 제○차 회의록", "○○대학교 ○○사업단 · 20○○. ○. ○."], "blocks": []}
    assert wt.localize(spec, {"대학명": "가나대학교"})["cover"][1].startswith("가나대학교 ○○사업단")   # 모르는 값(사업단·날짜)은 남긴다
    assert wt.localize(spec, {})["cover"][1].startswith("○○대학교")
