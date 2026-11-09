"""공문 양식(기안문·시행문)이 기안문 서식 차례(두문 → 본문 → 결문)이고, 두문·결문이 선 없는 줄이며, 기관명은 기관 정보에서 오는지."""
import io
import re
import zipfile

from zzaimy.app import webhwp_templates as wt


def _text(data: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    xml = "".join(z.read(n).decode("utf-8") for n in sorted(z.namelist()) if re.match(r"Contents/section\d+\.xml", n))
    return "".join(re.findall(r"<hp:t>([^<]*)</hp:t>", xml))


def test_draft_order_head_body_foot():
    t = _text(wt.build(wt.OFFICIAL["draft"]))
    order = ["수신", "내부결재", "(경유)", "제목", "1. 관련", "2. 추진 내용", "붙임", "끝.", "기안자", "검토자", "결재권자", "협조자",
             "시행(번호·일자)", "우편번호", "홈페이지", "전송(팩스)", "공개구분"]
    t = re.sub(r"【작성방법】[^】]*?(?=1\. 관련|2\. 추진|붙임|$)", "", t)     # 안내 상자 글은 빼고 차례만 본다
    at = 0
    for x in order:                                          # 결재란은 본문 뒤(결문), 수신·제목은 맨 위(두문)
        nxt = t.find(x, at)
        assert nxt >= 0, (x, t[at:at + 200])
        at = nxt + len(x)


def test_head_and_foot_are_plain_lines():
    draft = wt.OFFICIAL["draft"]["blocks"]
    tables = [b["table"] for b in draft if "table" in b]
    assert tables[0]["rows"][0] == ["수신", "내부결재"] and tables[0].get("plain")
    assert all(t.get("plain") for t in tables)               # 두문·결재 줄·협조자·시행 줄 모두 선 없는 줄
    assert not any(b.get("table", {}).get("plain") for b in wt.GRANT["budget_change"]["blocks"])   # 국고 서식 결재란은 칸 그대로


def test_institution_name_not_hardcoded():
    assert "영남이공대학교" not in repr(wt.OFFICIAL)
    loc = wt.localize(wt.OFFICIAL["outgoing"], {"대학명": "가나대학교", "주소": "대구광역시 남구 현충로 1"})
    assert loc["cover"] == ["가나대학교"]
    assert any(b.get("sign") == ["", "", "가나대학교총장"] for b in loc["blocks"])
    assert "대구광역시 남구 현충로 1" in repr(loc)
    bare = wt.localize(wt.OFFICIAL["draft"])
    assert bare["cover"] == ["(행정기관명)"] and "{" not in repr(bare["cover"])
    assert "(행정기관명)" in _text(wt.build(wt.OFFICIAL["draft"]))
