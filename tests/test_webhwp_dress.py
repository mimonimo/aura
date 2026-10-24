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
