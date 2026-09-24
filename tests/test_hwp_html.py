"""옛 한글 → 한 파일 HTML — 스타일 내장, 취소선 제거, 머리말 제거, 그림 축소 data URI."""

from pathlib import Path

from zzaimy.ingest import hwp_html


def test_convert_inlines_css_drops_strike_and_embeds_images(tmp_path, monkeypatch):
    from PIL import Image

    out_root = {}

    def fake_run(cmd, check, capture_output, timeout):
        out = Path(cmd[cmd.index("--output") + 1])
        out.mkdir(parents=True)
        (out / "bindata").mkdir()
        Image.new("RGB", (3000, 2000), "white").save(out / "bindata" / "BIN0001.bmp")
        (out / "styles.css").write_text("body {\n  background-color: #eee;\n  padding: 4px;\n}\n.Paper {\n  background-color: #fff;\n  border: 1px solid black;\n}\n.a > span {\n  color: #000000;\n  text-decoration: line-through;\n  font-weight: bold;\n}\n")
        (out / "index.xhtml").write_text('<html><head><link rel="stylesheet" href="styles.css" type="text/css" /></head><body>'
                                          '<div class="HeaderArea"><p>머리말</p></div><p class="a"><span>제목</span>&#13;</p>'
                                          '<img src="bindata/BIN0001.bmp" style="  width: 250mm;&#10;   height: 100mm;&#10;" /></body></html>')
        out_root["out"] = out

    monkeypatch.setattr(hwp_html.subprocess, "run", fake_run)
    monkeypatch.setattr(hwp_html, "_hwp5html", lambda: Path("/bin/true"))
    html, stats = hwp_html.convert(tmp_path / "x.hwp")
    text = html.decode("utf-8")
    assert "<style>" in text and "line-through" not in text and "font-weight: bold" in text
    assert "#eee" not in text and "border: 1px solid black" not in text
    assert "머리말" not in text and "&#13;" not in text
    assert 'src="data:image/jpeg;base64,' in text and "width: 170.00mm" in text and "height:" not in text.split("<img")[1]
    assert stats == {"images": 1, "images_dropped": 0, "tables": 0}
