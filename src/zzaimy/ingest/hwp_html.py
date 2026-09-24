"""옛 한글(.hwp 5.0) → 한 파일 HTML — 구글 드라이브가 독스로 바꾸는 입력.

pyhwp 의 hwp5html 은 문단·표·글자 모양을 CSS 클래스(charshape-N·parashape-N·borderfill-N)로 내놓는다. ODT 변환은
큰 실물 문서(30MB 사업계획서, 2026-09-24)에서 RelaxNG 검증·변환 모두 실패했고 HTML 은 됐다. 여기서는 그 HTML 을
스타일 시트를 안에 넣고, 그림은 JPEG 로 줄여(긴 변 1400px) data URI 로 박은 한 파일로 만든다 — 원본 BMP 는 장당
8MB 가 넘어 그대로는 올릴 수 없다. 머리말·꼬리말 영역은 뺀다(독스에서 편집 대상이 아니다).
"""

from __future__ import annotations

import base64
import io
import re
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_SIDE = 1400
_IMG = re.compile(r'<img\b[^>]*\bsrc="([^"]+)"[^>]*>', re.I)
_HEAD_FOOT = re.compile(r'<div class="(?:HeaderArea|FooterArea)">.*?</div>', re.S)
_LINK = re.compile(r'<link\b[^>]*href="styles\.css"[^>]*/?>', re.I)
_BR_CR = re.compile(r"&#13;")


def _hwp5html() -> Path | None:
    exe = Path(sys.executable).parent / "hwp5html"
    return exe if exe.exists() else None


def _image_data_uri(path: Path) -> str | None:
    try:
        from PIL import Image

        im = Image.open(path)
        im.load()
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        w, h = im.size
        scale = min(1.0, MAX_SIDE / max(w, h))
        if scale < 1.0:
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def convert(src: Path | str, timeout_s: int = 900) -> tuple[bytes, dict]:
    """.hwp → (html 바이트, 통계). hwp5html 이 없거나 실패하면 RuntimeError."""
    src = Path(src)
    exe = _hwp5html()
    if exe is None:
        raise RuntimeError("hwp5html 이 없습니다(pyhwp)")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "html"
        try:
            subprocess.run([str(exe), "--output", str(out), str(src)], check=True, capture_output=True, timeout=timeout_s)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"hwp5html 실패: {type(e).__name__}") from e
        index = out / "index.xhtml"
        if not index.exists():
            raise RuntimeError("hwp5html 산출이 없습니다")
        html = index.read_text(encoding="utf-8", errors="replace")
        css = (out / "styles.css").read_text(encoding="utf-8", errors="replace") if (out / "styles.css").exists() else ""
        stats = {"images": 0, "images_dropped": 0, "tables": html.count("<table")}
        html = _HEAD_FOOT.sub("", html)
        html = _BR_CR.sub("", html)
        html = _LINK.sub(f"<style>{css}</style>", html, count=1)

        def _img(m: re.Match) -> str:
            rel = m.group(1)
            p = out / rel
            uri = _image_data_uri(p) if p.exists() else None
            if not uri:
                stats["images_dropped"] += 1
                return ""
            stats["images"] += 1
            tag = m.group(0)
            tag = re.sub(r'src="[^"]+"', f'src="{uri}"', tag, count=1)
            # 쪽 너비를 넘는 그림은 본문 폭(170mm)으로
            tag = re.sub(r"width:\s*([\d.]+)mm", lambda w: f"width: {min(float(w.group(1)), 170.0):.2f}mm", tag)
            tag = re.sub(r"height:\s*[\d.]+mm;?", "", tag)
            return tag

        html = _IMG.sub(_img, html)
        return html.encode("utf-8"), stats
