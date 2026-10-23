"""양식 만들기(hwp_form_create) — 설계 관문, 학교 서식 번호 체계, 빌드 결과 XML(절·칸·표 머리·작성방법·쪽 나누기), 기존 양식 먼저 관문,
설계 재시도와 작업본 잇기. 그리고 시나리오 평가(scripts/202)의 채점 함수 — 디자인 반영을 실제 XML 값으로 보는지."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from tests.test_delegate import FakeClient
from tests.test_webhwp import _setup
from zzaimy.app import llm_budget, webhwp
from zzaimy.app import webhwp_agent as wa
from zzaimy.app import webhwp_edit_ops as eo
from zzaimy.app import webhwp_form_create as fc
from zzaimy.app import webhwp_style as ws
from zzaimy.app import webhwp_write as ww

ROOT = Path(__file__).resolve().parents[1]

DESIGN = {"title": "2027 AI 교육 사업계획서", "cover": True, "subtitle": "기획처",
          "summary": ["사업명", "사업 기간", "총 사업비", "대상"],
          "sections": [{"level": 1, "title": "Ⅰ. 사업 개요"},
                       {"level": 2, "title": "1. 추진 배경 및 필요성", "guide": "공고의 사업 목적과 우리 대학 현황을 잇는다", "length": "1쪽 이내"},
                       {"level": 2, "title": "사업 목표", "guide": "측정할 수 있는 목표"},
                       {"level": 1, "title": "추진 계획"},
                       {"level": 2, "title": "추진 체계", "guide": "조직과 역할", "diagram": "사업단 조직"},
                       {"level": 2, "title": "세부 추진 내용"},
                       {"level": 3, "title": "(1) 교육과정 운영", "guide": "과정별로", "table": ["과정", "대상", "시기"]},
                       {"level": 3, "title": "비교과 운영", "guide": "프로그램별로"},
                       {"level": 1, "title": "추진 일정", "guide": "월별 일정", "table": ["추진 과제", "일정", "담당"]},
                       {"level": 1, "title": "기대 효과", "guide": "성과지표와 잇는다", "length": "500자 내외"}],
          "why": "공고의 평가 항목 차례"}


def _fields(data: bytes) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in ww.fields(data):
        out.setdefault(f["kind"], []).append(f["name"])
    return out


def test_validate_rejects_broken_designs():
    assert fc.validate(DESIGN)[1] == []
    probs = " ".join(fc.validate({"title": " ", "sections": []})[1])
    assert "제목(title)이 비었다" in probs and "절(sections)이 하나도 없다" in probs
    dup = fc.validate({"title": "가", "sections": [{"level": 1, "title": "사업 개요"}, {"level": 1, "title": "1. 사업 개요"}]})[1]
    assert any("제목이 같다" in p for p in dup)                          # 번호만 다른 같은 절
    skip = fc.validate({"title": "가", "sections": [{"level": 1, "title": "개요"}, {"level": 3, "title": "세부"}]})[1]
    assert any("단계를 건너뛰었다" in p for p in skip)
    long_ = fc.validate({"title": "가", "sections": [{"level": 1, "title": "개요", "guide": "가" * (fc.GUIDE_MAX + 1)}]})[1]
    assert any("작성방법 안내" in p for p in long_)
    many = fc.validate({"title": "가", "sections": [{"level": 1, "title": f"절 이름 {chr(0xAC00 + i)}"} for i in range(fc.MAX_SECTIONS + 1)]})[1]
    assert any("너무 많다" in p for p in many)
    note = fc.validate({"title": "가", "sections": [{"level": 1, "title": "결과 보고서 제출"}]})[1]
    assert any("안내 글처럼" in p for p in note)                         # 「~제출」 로 끝나면 기안기가 절로 읽지 못한다
    lab = fc.validate({"title": "가", "summary": ["000", "사업명"], "sections": [{"level": 1, "title": "개요"}]})[1]
    assert any("요약 표 칸 라벨" in p for p in lab)
    loose, p2 = fc.validate({"title": "가", "sections": [{"level": 1, "title": "안건", "guide": "안건별로", "length": "안건마다 짧게"}]})
    assert p2 == [] and loose["sections"][0]["length"] == "" and "분량: 안건마다 짧게" in loose["sections"][0]["guide"]   # 분량 꼴이 아니면 안내로
    cols = fc.validate({"title": "가", "sections": [{"level": 1, "title": "개요", "table": ["하나"]}]})[1]
    assert any("표 열은" in p for p in cols)


def test_numbering_follows_school_form_and_strips_model_numbers():
    clean, _ = fc.validate(DESIGN)
    heads = [s["heading"] for s in clean["sections"]]
    assert heads[:4] == ["1. 사업 개요", "가. 추진 배경 및 필요성", "나. 사업 목표", "2. 추진 계획"]
    assert "1) 교육과정 운영" in heads and "2) 비교과 운영" in heads and "3. 추진 일정" in heads
    assert [s["parent"] for s in clean["sections"]][:2] == [True, False]


def test_build_makes_fields_guides_tables_and_cover_break():
    clean, _ = fc.validate(DESIGN)
    data = fc.build(clean)
    assert eo.check_bytes(data) == []
    f = _fields(data)
    assert f["칸"] == ["칸:사업명", "칸:사업 기간", "칸:총 사업비", "칸:대상"]
    # 쓸 자리는 아래 절이 없는 절만 — 위 절(1. 사업 개요·2. 추진 계획·나. 세부 추진 내용)은 필드가 없다
    assert "절:가. 추진 배경 및 필요성" in f["절"] and "절:1) 교육과정 운영" in f["절"] and "절:4. 기대 효과" in f["절"]
    assert not any(n in f["절"] for n in ("절:1. 사업 개요", "절:2. 추진 계획", "절:나. 세부 추진 내용"))
    assert len(f["절"]) == sum(1 for s in clean["sections"] if not s["parent"])
    assert any(n.endswith(":추진 과제") for n in f["표"]) and any(n.endswith(":과정") for n in f["표"])
    # 작성방법 상자 — 안내·분량·표·도식 자리가 「작성방법 요구 요소」 로 읽힌다(같은 낱말 규칙)
    ctx = ww.section_contexts(data)
    assert wa.length_hint(wa.guide_of(ctx["절:가. 추진 배경 및 필요성"])) == "1쪽 이내"
    assert "도식" in wa.needs_of(wa.guide_of(ctx["절:가. 추진 체계"]))
    assert "표" in wa.needs_of(wa.guide_of(ctx["절:3. 추진 일정"]))
    # 학교 표 기본 모양 — 절 안 표의 머리줄 음영(HOUSE head_fill), 표지 다음 첫 절 제목 앞 쪽 나누기
    xml = fc_xml(data)
    hdr = fc_xml(data, r"Contents/header\.xml$")
    tbl = next(m.group(0) for m in ws._TBL.finditer(xml) if (ws._cells(m.group(0)) or [{"text": ""}])[0]["text"] == "추진 과제")
    head = [c for c in ws._cells(tbl) if ws.roles(tbl).get((c["r"], c["c"])) == "head"]
    ref = re.search(r'borderFillIDRef="(\d+)"', head[0]["xml"]).group(1)
    assert re.search(rf'<hh:borderFill\b[^>]*\bid="{ref}".*?faceColor="{ws.HOUSE["head_fill"]}"', hdr, re.S)
    first = re.search(r'<hp:p\b[^>]*pageBreak="1"[^>]*>(?:(?!<hp:p\b).)*?1\. 사업 개요', xml, re.S)
    assert first, "표지면 첫 절 앞에서 쪽을 나눈다"


def fc_xml(data: bytes, pat: str = r"Contents/section\d+\.xml$") -> str:
    import io
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(data))
    return "".join(z.read(n).decode("utf-8") for n in sorted(z.namelist()) if re.match(pat, n))


def _hw(tmp_path, monkeypatch, sid=77):
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    sid = db.create_chat_session("시험", owner="kim")
    hw = wa.HwpTools(db, base, sid, "kim", "기획처", {"id": 1, "name": "AI 사업"}, lambda: [])
    return db, hw, sid


def test_form_create_requires_forms_first_then_retries_bad_design(tmp_path, monkeypatch):
    db, hw, sid = _hw(tmp_path, monkeypatch)
    db.add_chat(sid, "user", "내년 AI 교육 사업계획서 양식부터 잡아 줘. 대상은 1학년이야.")
    db.set_setting(f"chat_summary:{sid}", json.dumps({"text": "지금 목표:\n- 2027 AI 교육 사업계획서\n확정된 것:\n- 대상 1학년", "upto": 1}))
    bad = json.dumps({"title": "계획서", "sections": [{"level": 1, "title": "개요"}, {"level": 1, "title": "개요"}]}, ensure_ascii=False)
    client = FakeClient(["설계입니다 " + bad, json.dumps(DESIGN, ensure_ascii=False)])
    hw.client, hw.request = client, "양식부터 잡아 줘"
    hw.form_search = lambda q: [{"title": "공고", "text": "평가 항목: 추진 배경, 추진 체계, 추진 일정"}]
    # 기존 양식을 먼저 찾아보지 않았으면 만들지 않는다
    out = hw.form_create({"purpose": "AI 교육 사업 신청", "kind": "사업계획서"})
    assert "hwp_forms" in out and not client.sent and hw.fid() is None
    hw.forms({"query": "사업계획서"})
    labels = []
    monkeypatch.setattr(llm_budget, "label", _spy_label(labels, llm_budget.label))
    out = hw.form_create({"purpose": "AI 교육 사업 신청", "kind": "사업계획서", "must": ["추진 일정"], "cover": True})
    assert out.startswith("양식 「") and "쓸 자리" in out, out
    # 관문 까닭을 붙여 한 번 더 → 통과한 설계를 자기 검토 한 번(답이 JSON 이 아니면 통과한 설계 그대로)
    assert len(client.sent) == 3 and "제목이 같다" in client.sent[1][-1]["content"] and "점검표" in client.sent[2][0]["content"]
    user = client.sent[0][1]["content"]
    assert "[참고 양식" in user and "사업계획서 공통 양식" in user and "[이 갈래의 흔한 절" in user    # 실물 양식 구조를 참고로
    assert "2027 AI 교육 사업계획서" in user and "대상 1학년" in user and "평가 항목" in user and "추진 일정" in user
    assert labels[:2] == ["서브 · 양식 설계", "서브 · 양식 설계"] and labels[2] == "서브 · 양식 검토"
    fid = hw.fid()
    meta = webhwp.meta(db, fid)
    assert meta["source"] == "양식생성" and meta["form_design"]["shape"]["slots"] == 7
    assert wa.bound(db, sid) == fid and "작업/" in webhwp.get(db, fid)["path"]
    assert fc.load_log(db, sid)[-1]["ok"] is True
    # 만든 작업본에 바로 쓸 수 있다(같은 쓰기 길)
    assert hw.write({"field": "절:3. 추진 일정", "text": "1) 월별 일정은 아래 표와 같음\n| 추진 과제 | 일정 |\n|---|---|\n| 교육과정 개발 | 3~6월 |"}).startswith("「")


def test_form_create_gives_up_after_two_bad_designs(tmp_path, monkeypatch):
    db, hw, sid = _hw(tmp_path, monkeypatch)
    client = FakeClient(["JSON 아님", json.dumps({"title": "", "sections": []})])
    monkeypatch.setenv("ZZAIMY_FORM_REFS", "0")
    hw.client = client
    hw.forms({"query": "없는 낱말"})
    out = hw.form_create({"purpose": "시험"})
    assert out.startswith("양식을 만들지 않았다") and hw.fid() is None and len(client.sent) == 2


def _spy_label(sink, real):
    class _L(real):
        def __init__(self, name):
            sink.append(name)
            super().__init__(name)
    return _L


def _e202():
    spec = importlib.util.spec_from_file_location("e202", ROOT / "scripts" / "202_webhwp_scenario_eval.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_scenario_scoring_reads_design_from_xml():
    e = _e202()
    clean, _ = fc.validate(DESIGN)
    data = fc.build(clean)
    v0 = e.design_values(data)
    assert e.design_score(v0) == {"가로": False, "여백 좁게": False, "머리줄 연파랑": False, "쪽 번호": False}
    data, _ = eo.page_setup(data, {"orientation": "가로", "margin": "좁게"})
    data = ws.apply(data, None, {"head_fill": "연파랑"})
    data, _ = eo.page_number(data, {"position": "아래 가운데"})
    v = e.design_values(data)
    assert v["landscape"] and v["margin_mm"]["left"] == 15.0 and v["page_number"] == "BOTTOM_CENTER"
    assert v["head_tables"] >= 2 and v["head_fill_tables"] == v["head_tables"]
    assert all(e.design_score(v).values())
    b = e.broken(data)
    assert b["check_n"] == 0 and b["field_unpaired"] == 0
    st = e.structure_score(clean)
    assert st["checks"]["번호 체계(1. → 가. → 1))"] and st["checks"]["쓸 자리마다 작성방법"] and st["checks"]["표 자리 1개 이상"]
    g = e.goal_score("지금 목표:\n- 2027. 3.~2028. 2. 1학년 대상 AI 교육 사업계획서(예산 5억 원)\n확정된 것:\n- 대상 1학년")
    assert g["goal"] == {"대상": True, "예산": True, "기간": True}
    assert e.goal_score("지금 목표:\n- AI 교육 사업\n확정된 것:\n- 예산 5억")["goal"]["예산"] is False


def test_scenario_body_stats_and_judge_parsing():
    e = _e202()
    s = e.body_stats("1) 1학년 1,200명 대상\n| 과제 | 일정 |\n|---|---|\n| 개발 | 3월 |")
    assert s["tables"] == 1 and s["nums"] >= 2
    client = FakeClient(['{"구체성": 4, "논리": 3, "문체": 5, "근거": 2, "까닭": "일정이 구체적"}'])
    j = e.judge(client, "절:추진 일정", "1) 3월 개발")
    assert j["구체성"] == 4 and "기준표" in client.sent[0][0]["content"] and "구체성" in client.sent[0][0]["content"]
    assert e._avg([j, {"구체성": 2, "논리": 3, "문체": 5, "근거": 4}])["구체성"] == 3.0


def test_references_are_structure_only_and_kind_dict_follows_inputs(tmp_path, monkeypatch):
    from zzaimy.app import webhwp_templates as wt
    db, hw, sid = _hw(tmp_path, monkeypatch)
    wt.ensure_all(db, hw.base)
    refs = fc.find_references(db, hw.base, "다음 주 운영위원회 회의록 양식", "회의록")
    assert refs and "회의록" in refs[0]["name"] and len(refs[0]["heads"]) >= 2
    text = fc.render_refs(refs)
    assert "절:" in text and set(refs[0]) == {"name", "key", "heads", "tables", "guides", "labels"}   # 구조(절 제목·표 머리·안내 요지·칸 라벨)만
    assert fc.kind_noun("행사 운영계획서 양식") == "운영계획서" and fc.kind_noun("아무 말") == ""
    d1 = fc.kind_dict(db, hw.base)
    assert "회의록" in d1["kinds"] and d1["kinds"]["회의록"]["sections"]
    assert fc.kind_dict(db, hw.base)["built"] == d1["built"]                          # 입력이 같으면 다시 만들지 않는다
    fid = next(r["id"] for r in db.list_files(kind="webhwp", limit=500) if "회의록" in r["name"])
    clean, _ = fc.validate(DESIGN)
    webhwp.save(db, hw.base, fid, fc.build(clean), user="zzaimy")                  # 입력 하나가 바뀌면 지문이 달라져 다시 만든다
    d2 = fc.kind_dict(db, hw.base)
    assert d2["fp"] != d1["fp"]


def test_self_review_revises_design_or_keeps_original(tmp_path, monkeypatch):
    db, hw, sid = _hw(tmp_path, monkeypatch)
    monkeypatch.setenv("ZZAIMY_FORM_REFS", "0")
    first = {"title": "회의록", "sections": [{"level": 1, "title": "회의 개요", "guide": "일시·장소"}, {"level": 1, "title": "논의 내용", "guide": "안건별"}]}
    revised = {"review": ["의결 사항 절이 빠져 더함"], **first,
               "sections": first["sections"] + [{"level": 1, "title": "의결 사항", "guide": "안건별 의결 결과"}]}
    client = FakeClient([json.dumps(first, ensure_ascii=False), json.dumps(revised, ensure_ascii=False)])
    fcr = fc.FormCreator(client, db, sid, hw, "회의록 양식")
    clean, probs, tries = fcr.design({"purpose": "위원회 회의록", "kind": "회의록"})
    assert not probs and [s_["title"] for s_ in clean["sections"]][-1] == "의결 사항"
    assert tries[-1]["used"] and tries[-1]["notes"] == ["의결 사항 절이 빠져 더함"]
    bad = {"sections": [], "title": ""}
    client = FakeClient([json.dumps(first, ensure_ascii=False), json.dumps(bad)])
    clean, probs, tries = fc.FormCreator(client, db, sid, hw, "회의록 양식").design({"purpose": "위원회 회의록"})
    assert len(clean["sections"]) == 2 and tries[-1]["used"] is False                 # 고친 것이 관문을 못 지나면 처음 설계
    client = FakeClient([json.dumps(first, ensure_ascii=False)])
    clean, _, tries = fc.FormCreator(client, db, sid, hw, "x", review=False).design({"purpose": "x"})
    assert len(client.sent) == 1 and len(tries) == 1


def test_forms_set_scoring_counts_inclusion_not_exact_answers():
    e = _e202()
    case = next(c for c in e.FORM_CASES if c["name"] == "회의록")
    d, _ = fc.validate({"title": "운영위원회 회의록", "summary": ["회의명", "일시", "장소"],
                        "sections": [{"level": 1, "title": "회의 개요", "guide": "일시·장소·참석자를 적는다", "length": "5줄 이내"},
                                     {"level": 1, "title": "안건", "guide": "안건 이름과 제안 근거 자료"},
                                     {"level": 1, "title": "논의 내용", "guide": "안건별 발언 요지"}]})
    sc = e.form_scores(case, d)
    assert sc["include"] == "3/6" and sc["numbering"] and sc["guide_cover"] == 1.0 and sc["guide_source"] > 0
    rows = [{"name": "회의록", "mode": "참고", "path": "직접", "made_by": "직접", "scores": sc, "broken": {"check_n": 0, "field_unpaired": 0}, "sec": 3.0},
            {"name": "회의록", "mode": "참고", "path": "직접", "made_by": "실패", "sec": 5.0}]
    md = e.forms_summary(rows, "1차")
    assert "| 참고·직접 | 1차 | 1/2 | 0.5 |" in md
    assert e.forms_row(rows[1], "1차").startswith("| 회의록 | 1차 | 참고 | 직접·실패 |")


def test_model_trial_sets_and_hides_candidate(monkeypatch):
    import os
    from zzaimy.eval import model_trial
    monkeypatch.delenv("ZZAIMY_LLM_TRY_CONN", raising=False)
    monkeypatch.delenv("ZZAIMY_LLM_TRY_MODEL", raising=False)
    assert model_trial.apply("", "") == {} and "ZZAIMY_LLM_TRY_CONN" not in os.environ
    assert model_trial.apply("c1", "writer-lora") == {"ZZAIMY_LLM_TRY_CONN": "c1", "ZZAIMY_LLM_TRY_MODEL": "writer-lora"}
    with model_trial.without_trial():                                  # 채점자는 후보 설정 없이(운영 연결)
        assert "ZZAIMY_LLM_TRY_CONN" not in os.environ
    assert os.environ["ZZAIMY_LLM_TRY_MODEL"] == "writer-lora"
    assert model_trial._no_address({"name": "m", "base_url": "http://x", "api_key": "k", "base": "b"}) == {"name": "m", "base": "b"}
    model_trial.apply("", "")


def test_form_sources_scope(tmp_path):
    """보안: 공용 갈래 사전은 전체 공개 문서만, 참고 양식은 쓸 수 있는 부서 폴더(와 공통)만."""
    from zzaimy.app import webhwp_form_create as fc

    class DB:
        def list_files(self, kind=None, limit=0):
            return []

        def list_documents(self, q=None, light=False):
            return [{"id": 1, "filename": "가 서식.hwpx", "kind": "form", "access_level": "dept"},
                    {"id": 2, "filename": "나 서식.hwpx", "kind": "form", "access_level": "public"},
                    {"id": 3, "filename": "다 서식.hwpx", "kind": "form", "access_level": None}]
    keys = [s["key"] for s in fc.form_sources(DB(), tmp_path)]
    assert keys == ["doc:2"]
    keys = [s["key"] for s in fc.form_sources(DB(), tmp_path, visible=lambda d: d["id"] != 2)]
    assert keys == ["doc:1", "doc:3"]


def test_body_table_replaces_empty_form_table_only():
    """본문이 표를 들고 오면 같은 절의 빈 양식 표(머리줄만 있는 표)는 지운다 — 표 없는 본문이면 양식 표를 둔다. 작성방법 조사도 받침에 맞게."""
    clean, _ = fc.validate(DESIGN)
    data = fc.build(clean)
    assert "사업단 조직은 도식" in "".join(ww.section_contexts(data).values())
    table_names = lambda d: [n for n in _fields(d)["표"] if n.endswith((":추진 과제", ":일정", ":담당"))]
    assert len(table_names(data)) == 3
    plain, _ = ww.write_section(data, "절:3. 추진 일정", ww.parse_body("월별로 추진함."), {})
    assert len(table_names(plain)) == 3
    body = "월별 일정은 다음과 같음.\n| 추진 과제 | 일정 | 담당 |\n|---|---|---|\n| 준비 | 3월 | 기획처 |"
    out, _ = ww.write_section(data, "절:3. 추진 일정", ww.parse_body(body), {})
    assert table_names(out) == [] and eo.check_bytes(out) == []
    assert any(n.endswith(":과정") for n in _fields(out)["표"]), "다른 절의 양식 표는 그대로"
