"""시나리오 A(작년 결과보고서를 양식으로 올해 보고서) 마무리 — 「확인 필요」 칸 다시 채우기, 올해 자료에 없는 절 한 줄,
되풀이 대신 finish 로 끝내기, 흘려 쓰기 글 판이 파일에 들어가지 않기(11-10 대화 177)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from tests.test_delegate import FakeClient, _work
from zzaimy.app import delegate, doc_agent, label_values as lv, webhwp
from zzaimy.app import webhwp_coedit as ce
from zzaimy.app import webhwp_write as ww


def test_refill_takes_single_value_and_skips_form_source():
    fields = [{"name": "칸:장소", "kind": "칸", "text": "확인 필요"},
              {"name": "칸:기간", "kind": "칸", "text": "2018. 6. 25. ~ 26."},
              {"name": "칸:주관", "kind": "칸", "text": "[확인 필요]"},
              {"name": "칸:개요", "kind": "칸", "text": "행사명: 축제\n목적: 확인 필요"},
              {"name": "절:1. 개요", "kind": "절", "text": "확인 필요"}]
    texts = ["《2017 LINC+ 페스티벌 결과보고서》\n◦ (장 소) 부산 벡스코\n◦ (목 적) 성과 확산",          # 양식 문서 — 빼야 한다
             "[근거 1] 《2018 YNC Festival》 12쪽\n| 장소 | 경주드림센터 | 주관 | LINC+ 사업단 |",
             "1) 개요\n다) 장소: 경주드림센터 (출처: 2018 YNC Festival 12쪽)\n라) 주관: 산학협력단 (출처: 2018 YNC Festival 3쪽)",
             "가) 목적: 산학협력 성과 공유 (출처: 2017 LINC+ 페스티벌 결과보고서 2쪽)"]
    skip = lambda t: "2017LINC+페스티벌결과보고서" in lv._key(lv.norm(t))
    got = lv.refill(fields, texts, skip)
    assert got == {"칸:장소": "경주드림센터"}                     # 주관은 값이 둘(고르지 않음), 목적은 양식 문서 값뿐, 절은 다루지 않음
    texts.append("마) 목적: 지역 산업 연계 (출처: 2018 YNC Festival 1쪽)")
    got = lv.refill(fields, texts, skip)
    assert got["칸:개요"] == "행사명: 축제\n목적: 지역 산업 연계"   # 칸 안 「라벨: 확인 필요」 줄만 바꾼다


def test_delegation_refills_pending_cell_and_marks_absent_section(tmp_path, monkeypatch):
    app, db, hw, secs = _work(tmp_path, monkeypatch, sid=191)
    cell = next(x["name"] for x in hw._fields() if x["kind"] == "칸" and lv._label_ok(lv.label_of(x["name"])))
    label = cell.split(":", 1)[1]
    assert hw.fill({"values": {cell: "확인 필요"}}).startswith("채웠다")
    fid = hw.fid()
    m = webhwp.meta(db, fid)
    m["source"] = "doc:999:지난문서"                          # 작년 결과보고서를 양식으로 연 작업본(값을 비운 지난 문서)
    db.set_setting(f"webhwp_meta:{fid}", json.dumps(m, ensure_ascii=False))
    hw.project_docs = lambda: [{"id": 999, "filename": "2017 페스티벌 결과보고서.pdf"}]

    def search(q):
        return [{"title": "2017 페스티벌 결과보고서", "text": f"| {label} | 작년 값 |", "doc_id": 999},
                {"title": "2018 행사 자료", "text": f"| {label} | 올해 값 |", "doc_id": 5}]
    client = FakeClient(["[본문]\n1) 행사 운영함\n[확인 필요]\n- 없음"])
    other = FakeClient([f"[본문]\n{delegate.ABSENT}\n[확인 필요]\n- 없음"])
    hw.client, hw.evidence, hw.delegating = client, (lambda: ["자료"]), True
    dlg = delegate.Delegator(client, db, hw.sid, hw, "작년 양식대로 올해 보고서 써 줘", search=search, quality=False,
                             pool=[(client, 1), (other, 1)])
    out = dlg.run({"sections": [{"section": secs[0], "gist": "운영"}, {"section": secs[1], "gist": "토크콘서트"}]})
    vals = {x["name"]: x["text"] for x in hw._fields()}
    assert vals[cell] == "올해 값" and "다시 채웠다" in out       # 작년 값(양식 문서)은 근거에서 빠져 값이 하나
    assert "한 줄만 남겼다" in out and delegate.is_absent(vals[secs[1]])
    assert "redo_note" not in out.split("한 줄만 남겼다")[1].split("\n\n")[0]


def test_repeat_after_writes_ends_with_finish_instead_of_repeat_stop():
    tools = [doc_agent.Tool("write_section", "쓰기", lambda a: "썼다", writes=True),
             doc_agent.Tool("search", "찾기", lambda a: "결과"),
             doc_agent.Tool("finish", "끝", lambda a: a.get("message", ""), final=True)]
    steps = [json.dumps({"thought": "t", "action": "write_section", "args": {"section": "1."}}),
             *[json.dumps({"thought": "t", "action": "search", "args": {"query": "표 칸"}})] * 3,
             json.dumps({"thought": "t", "action": "search", "args": {"query": "표 칸"}}),      # finish 만 받는 걸음에 또 찾기 → 다시 청함
             json.dumps({"thought": "t", "action": "finish", "args": {"message": "1절을 썼고 표 칸은 확인 필요로 두었습니다"}})]
    it = iter(steps)
    sent = []

    def create(**kw):
        sent.append(kw["messages"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(it)))])
    client = SimpleNamespace(model="m", client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    ep = doc_agent.run(client, "써 줘", lambda: "s", tools)
    assert ep.finished and ep.stop == "finished" and "확인 필요" in ep.final_message
    assert "이번 요청은 여기서 마친다" in sent[4][-1]["content"]


def test_repeat_without_writes_still_stops():
    tools = [doc_agent.Tool("search", "찾기", lambda a: "결과"), doc_agent.Tool("finish", "끝", lambda a: "", final=True)]
    same = json.dumps({"thought": "t", "action": "search", "args": {"query": "a"}})
    client = SimpleNamespace(model="m", client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=same))])))))
    ep = doc_agent.run(client, "q", lambda: "s", tools)
    assert ep.stop == "repeat" and not ep.finished


def test_table_section_write_leaves_no_pending_commit(tmp_path, monkeypatch):
    """표 든 절 쓰기 — 글 판 흘려 쓰기가 써 넣기 예약을 남기지 않는다. 예약 타이머가 표째 저장과 엇갈리면 들여쓰기 든 글 판이
    파일의 절 글을 덮었다(병렬 시험에서 가끔 실패하던 원인)."""
    from zzaimy.app import webhwp_agent as wa
    monkeypatch.setattr(wa.HwpTools, "STREAM_PACE", 0.001)
    app, db, hw, secs = _work(tmp_path, monkeypatch, sid=192)
    fid = hw.fid()
    hw.evidence = lambda: ["120"]
    assert "썼다" in hw.write({"field": secs[0], "text": "1) 단가 상승\n가) 장비 단가\n| 비목 | 금액 |\n|---|---|\n| 기자재 | 120 |"})
    assert (str(hw.base), int(fid)) not in ce._TIMERS
    vals = ww.field_values(Path(webhwp.get(db, fid)["path"]).read_bytes())
    assert "　" not in vals[secs[0]]
