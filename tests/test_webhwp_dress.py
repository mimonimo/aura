"""27B 절 쓰기의 꾸밈(webhwp_dress) — 참고 상자(> 줄)·표 제목(<표> 줄)이 꾸밈 스타일로 들어가고, 문장 속 언급은 글로 남는다."""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from tests.test_delegate import _work
from zzaimy.app import webhwp
from zzaimy.app import webhwp_dress as wd
from zzaimy.app import webhwp_write as ww


def test_items_split_box_and_caption():
    items = ww.parse_body("1) 개요임\n> 예산은 확정 전임\n> 3월에 확정함\n<표> 추진 일정\n| 단계 | 시기 |\n|---|---|\n| 1 | 3월 |\n<표 3> 에서 보듯 일정은 짧음")
    out, style = wd.dress_items(items, ["<표 1> 예산"])
    kinds = [k for k, _ in out]
    assert ("table", [["예산은 확정 전임\n3월에 확정함"]]) in out
    assert "<표 2> 추진 일정" in style and style["<표 2> 추진 일정"] == "표 캡션"
    assert style["예산은 확정 전임 3월에 확정함"] == "팁 상자"
    assert kinds.count("table") == 2
    assert not any(k.startswith("<표 3>") for k in style)                # 표에 붙지 않은 문장 속 언급은 글


def test_agent_write_applies_styles(tmp_path, monkeypatch):
    app, db, hw, secs = _work(tmp_path, monkeypatch)
    msg = hw.write({"field": secs[0], "text": "1) 개요임\n> 예산은 확정 전임\n<표> 추진 일정\n| 단계 | 시기 |\n|---|---|\n| 1단계 | 3월 |"})
    assert msg.startswith("「"), msg
    data = Path(webhwp.get(db, hw.fid())["path"]).read_bytes()
    z = zipfile.ZipFile(io.BytesIO(data))
    hdr = z.read("Contents/header.xml").decode()
    sec = "".join(z.read(n).decode() for n in z.namelist() if n.startswith("Contents/section"))
    ids = dict((n, i) for i, n in re.findall(r'<hh:style\b[^>]*\bid="(\d+)"[^>]*\bname="([^"]+)"', hdr))
    assert "표 캡션" in ids and "팁 상자" in ids
    assert re.search(rf'<hp:p\b[^>]*styleIDRef="{ids["표 캡션"]}"', sec)
    assert re.search(rf'<hp:p\b[^>]*styleIDRef="{ids["팁 상자"]}"', sec)
    assert "<표 1> 추진 일정" in ww.field_values(data)[secs[0]] or "&lt;표 1&gt; 추진 일정" in sec


def test_caption_first_and_renumber(tmp_path, monkeypatch):
    app, db, hw, secs = _work(tmp_path, monkeypatch)
    hw.write({"field": secs[1], "text": "<표 2> 예산 편성\n| 항목 | 금액 |\n|---|---|\n| 인건비 | 100 |"})      # 절이 표 제목으로 시작
    hw.write({"field": secs[0], "text": "1) 개요임\n<표 2> 추진 일정\n| 단계 | 시기 |\n|---|---|\n| 1단계 | 3월 |"})   # 앞 절을 나중에, 같은 번호
    data = Path(webhwp.get(db, hw.fid())["path"]).read_bytes()
    z = zipfile.ZipFile(io.BytesIO(data))
    hdr = z.read("Contents/header.xml").decode()
    sid = re.search(r'<hh:style\b[^>]*\bid="(\d+)"[^>]*\bname="표 캡션"', hdr).group(1)
    sec = "".join(z.read(n).decode() for n in sorted(z.namelist()) if n.startswith("Contents/section"))
    caps = re.findall(rf'<hp:p\b[^>]*styleIDRef="{sid}"[^>]*>(?:(?!</hp:p>).)*?&lt;표 (\d+)&gt; ([^<]*)', sec, re.S)
    assert [(n, t.strip()) for n, t in caps] == [("1", "추진 일정"), ("2", "예산 편성")]


def test_aura_activity_keeps_requester(tmp_path, monkeypatch):
    """AURA 작성 기록에는 시킨 사람이 남는다 — 편집 화면이 다시 맞출 때도 「AURA · 이름」(예전엔 「AURA · AURA」)."""
    from zzaimy.app import webhwp_agent as wa
    app, db, hw, secs = _work(tmp_path, monkeypatch)
    hw.write({"field": secs[0], "text": "1) 개요임"})
    assert wa.activity(db, hw.fid()).get("by") == "kim"


def test_drop_heading_echo_only_exact_repeat():
    from zzaimy.app import webhwp_write as ww
    body = "2. 성과지표 총괄표\n\n<표> 성과지표 총괄표\n| 구분 | 지표 |\n|---|---|\n| 1 | 수료율 |"
    assert ww.drop_heading_echo(body, "절:2. 성과지표 총괄표").startswith("<표> 성과지표 총괄표")
    assert ww.drop_heading_echo("## 2. 성과지표  총괄표\n본문", "절:2. 성과지표 총괄표") == "본문"
    renamed = "1. (추진 과제 1) AI 교육 운영\n가. 과제 개요"                      # 제목을 바꿔 쓴 줄은 둔다
    assert ww.drop_heading_echo(renamed, "절:1. (추진 과제 1) ○○○○") == renamed
    assert ww.drop_heading_echo("2. 성과지표 총괄표", "절:2. 성과지표 총괄표") == "2. 성과지표 총괄표"   # 그 줄뿐이면 그대로
    assert ww.drop_heading_echo("1) 추진 배경\n내용", "절:1. 추진 배경 및 필요성").startswith("1) 추진 배경")
