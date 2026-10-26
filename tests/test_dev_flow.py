"""처리 흐름 화면 — 단계·낱말·장비가 모두 그려지고, 산출물이 없으면 수치는 「—」 로 둔다(지어낸 수치 없음)."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import metrics, process_flow
from zzaimy.app.main import create_app


def _client(tmp_path):
    metrics._cache.update(at=0.0, path=None, data=None)
    app = create_app(db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter())
    return app, TestClient(app)


def test_flow_page_lists_every_stage_term_and_host(tmp_path):
    _, c = _client(tmp_path)
    page = c.get("/dev/flow").text
    for s in process_flow.STAGES:
        assert s["name"] in page and f'id="stage-{s["n"]}"' in page
    for t in process_flow.TERMS:
        assert t["name"] in page
    assert 'href="/dev#system"' in page                                    # 장비는 개발 현황 시스템 구성 한 곳에
    hub = c.get("/dev").text
    for h in process_flow.HOSTS:
        assert h["name"] in hub
    assert "구글 드라이브·독스" in hub and "웹 한글 기안기" in hub and "SFT 후보" not in hub
    assert [s["n"] for s in process_flow.STAGES] == list(range(1, len(process_flow.STAGES) + 1))
    assert all(len(s["how"]) <= 4 for s in process_flow.STAGES)          # 단계마다 4줄까지
    assert "scripts/" not in " ".join(h for s in process_flow.STAGES for h in s["how"])   # 스크립트 번호는 「관련 코드」 줄에만
    assert "수치 기준" in page


def test_flow_numbers_come_from_outputs(tmp_path):
    app, c = _client(tmp_path)
    data_dir = tmp_path
    snap = process_flow.snapshot(app.state.db)
    assert snap["vals"]["shacl_violations"] == "—" and snap["vals"]["reg_r1"] == "—"
    (data_dir / "ontology").mkdir()
    (data_dir / "ontology" / "shacl_report.json").write_text(json.dumps({"n_violations": 0, "n_warnings": 108}))
    snap = process_flow.snapshot(app.state.db)
    assert snap["vals"]["shacl_violations"] == "0" and snap["vals"]["shacl_warnings"] == "108"


def test_overview_links_to_flow(tmp_path):
    _, c = _client(tmp_path)
    assert 'href="/dev/flow"' in c.get("/dev").text


def test_compare_keeps_latest_complete_run(tmp_path):
    """관계 질문만 다시 잰 더 최근 측정이 있어도, 비교 표는 세 갈래를 다 잰 가장 최근 측정을 쓴다."""
    import os
    import time
    from zzaimy.app import answer_flow
    ev = tmp_path / "eval"
    ev.mkdir()
    arm = lambda k, rate: {"search": {"section": {"n": 73, "counts": {"doc_hit": {"k": k, "n": 73, "rate": rate}}},  # noqa: E731
                                      "relation": {"n": 75}}, "answer": {"n": 36, "wall_sec_median": 30 if rate < 0.5 else 17}}
    full = {"summary": {"fulltext": arm(13, 0.18), "graph_read": arm(51, 0.70)}}
    single = {"made": "2026-10-17", "summary": {"graph_read": arm(60, 0.82)}}
    part = {"summary": {"fulltext": {"search": {"section": {"n": 0}, "relation": {"n": 75}}}}}
    (ev / "graphrag_vs_fulltext_full.json").write_text(json.dumps(full))
    (ev / "graphrag_vs_fulltext_part.json").write_text(json.dumps(part))
    later = time.time() + 10
    os.utime(ev / "graphrag_vs_fulltext_part.json", (later, later))
    got = answer_flow.compare_rows(ev)
    assert got["file"] == "graphrag_vs_fulltext_full.json" and got["n_sec"] == 73
    row = next(r for r in got["rows"] if r["label"].startswith("특정 문서"))
    assert row["diff"]["text"] == "+52%p" and row["diff"]["good"]          # 풀텍스트 → 운영 차이
    t = next(r for r in got["rows"] if r["label"].startswith("답이 나오기까지"))
    assert t["diff"]["text"] == "-13.0초" and t["diff"]["good"]            # 시간은 줄면 좋음
    # 운영 방식 하나만 잰 더 최근 측정은 표에 섞지 않고 한 줄로
    (ev / "graphrag_vs_fulltext_single.json").write_text(json.dumps(single))
    later2 = time.time() + 20
    os.utime(ev / "graphrag_vs_fulltext_single.json", (later2, later2))
    got = answer_flow.compare_rows(ev)
    assert got["file"] == "graphrag_vs_fulltext_full.json" and got["latest"]["made"] == "2026-10-17"


def test_flow_page_title_is_plain(tmp_path):
    """블록 끝 표시를 잘못 바꿔 카드가 제목에 들어가는 일이 없게 — 탭 제목은 화면 이름뿐."""
    _, c = _client(tmp_path)
    page = c.get("/dev/flow").text
    title = page.split("<title>", 1)[1].split("</title>", 1)[0]
    assert "<div" not in title and "처리 흐름" in title and "왜 기성 도구 대신 직접 만들었나" not in page   # 품질·성능의 검색 설명 옆으로(10-26)
    assert c.get("/dev/quality").text.count("왜 기성 도구 대신 직접 만들었나</h3>") == 1


def test_flow_explains_coediting_with_live_settings(tmp_path):
    """공동 편집 처리 방식 표 — 방식마다 보는 것·뒤의 일·시간·동시 규칙, 아래 줄은 운영 설정값(바꾸면 화면도 바뀜)."""
    app, c = _client(tmp_path)
    page = c.get("/dev/flow").text
    assert 'id="coedit"' in page and "공동 편집 처리 방식" in page
    for r in process_flow.COEDIT:
        assert r["what"] in page and all(r[k] for k in ("see", "back", "lag", "together"))
    assert "보내기 간격 300ms" in page
    app.state.db.set_setting("coedit:throttle_ms", "500")
    assert "보내기 간격 500ms" in c.get("/dev/flow").text
    assert "SFT 후보" not in " ".join(h["role"] for h in process_flow.HOSTS)       # 끝난 비교는 장비 설명에서 뺐다


def test_system_diagram_file_follows_host_list():
    """구성도 그림 파일은 장비·연결 목록에서 그린 결과와 같아야 한다 — 목록을 바꾸고 그림을 안 다시 쓰면 여기서 막힌다."""
    from pathlib import Path
    from zzaimy.app import system_diagram
    svg = system_diagram.svg()
    for h in process_flow.HOSTS:
        assert h["name"] in svg
    assert "구글 드라이브·독스" in svg and "준비 중" not in svg and "작업본(협업)" not in svg
    f = Path(__file__).resolve().parents[1] / "src/zzaimy/app/static/topology.svg"
    assert f.read_text(encoding="utf-8").strip() == svg.strip(), "static/topology.svg 를 system_diagram.svg() 로 다시 쓴다(모듈 머리 주석의 명령)"


def test_coedit_sequence_figures_use_live_settings(tmp_path):
    """공동 편집 표 위의 순서 그림 두 장 — 사람이 칸에 칠 때와 AURA 가 절을 쓸 때, 보내기 간격은 운영 설정값."""
    app, c = _client(tmp_path)
    page = c.get("/dev/flow").text
    assert page.count('<figure class="pf-fig"><svg') == 2
    assert "사람이 칸에 글을 칠 때" in page and "AURA(27B)가 양식을 불러오고 절을 쓸 때" in page and "0.3초마다" in page
    app.state.db.set_setting("coedit:throttle_ms", "500")
    assert "0.5초마다" in c.get("/dev/flow").text
