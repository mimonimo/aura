"""도식(인포그래픽) 그리기 — 에이전트가 내용(제목·상자·요점)을 정하고, 그림은 코드가 그린다(2026-09-29).

완성된 사업계획서의 인포그래픽 대부분은 '제목 상자 몇 개에 요점 몇 줄', '단계 흐름' 같은 구조 그림이다. 이미지 생성 모델은 계획(모델 4종)에
없으므로 그런 그림은 여기서 카드형·흐름형 도식으로 그린다. 디자이너가 그린 일러스트·배경 사진은 만들지 않는다.

spec = {"title": 큰 제목, "layout": "cards"|"flow", "blocks": [{"title": 상자 제목, "items": [요점, …]}, …], "footer": 아래 띠(선택)}
"""

from __future__ import annotations

import io
import os
from pathlib import Path

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    str(Path.home() / "Library/Fonts/NanumBarunGothic.ttf"),
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
]
_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf",
    str(Path.home() / "Library/Fonts/NanumBarunGothicBold.ttf"),
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
]
PALETTE = [(31, 78, 121), (46, 117, 182), (84, 130, 53), (191, 144, 0), (112, 48, 160), (198, 89, 17)]   # 상자 머리 색(순환)
W = 1400


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    env = os.environ.get("ZZAIMY_INFOGRAPHIC_FONT")
    for path in ([env] if env else []) + (_BOLD_CANDIDATES if bold else _FONT_CANDIDATES):
        if path and Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    """글자 폭으로 줄바꿈(한글은 낱말 경계가 약해 글자 단위로 자른다)."""
    lines: list[str] = []
    for para in str(text).split("\n"):
        cur = ""
        for ch in para:
            if draw.textlength(cur + ch, font=font) <= max_w:
                cur += ch
            else:
                lines.append(cur.rstrip())
                cur = ch.lstrip()
        lines.append(cur.rstrip())
    return lines or [""]


def render(spec: dict) -> bytes:
    """spec → PNG 바이트. 상자 수가 4개까지면 2열, 그 밖은 3열. layout=flow 면 가로 단계 흐름(→)."""
    from PIL import Image, ImageDraw

    blocks = [b for b in (spec.get("blocks") or []) if isinstance(b, dict) and (b.get("title") or b.get("items"))][:9]
    layout = (spec.get("layout") or "cards").lower()
    title = str(spec.get("title") or "").strip()
    footer = str(spec.get("footer") or "").strip()
    f_title, f_head, f_body, f_foot = _font(40, True), _font(28, True), _font(22), _font(24, True)
    margin, gap = 40, 24
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    cols = 1 if not blocks else (len(blocks) if layout == "flow" else (2 if len(blocks) <= 4 else 3))
    arrow_w = 60 if layout == "flow" else 0
    card_w = (W - 2 * margin - gap * (cols - 1) - arrow_w * (cols - 1)) // max(cols, 1)
    # 상자마다 본문 줄 계산 → 행마다 높이 통일
    cards = []
    for b in blocks:
        lines: list[tuple[str, bool]] = []
        for it in (b.get("items") or [])[:8]:
            wrapped = _wrap(probe, str(it), f_body, card_w - 56)
            for i, ln in enumerate(wrapped):
                lines.append((("• " if i == 0 else "  ") + ln, i == 0))
        cards.append({"title": str(b.get("title") or ""), "lines": lines})
    head_h, line_h = 52, 32
    rows_of = [cards[i:i + cols] for i in range(0, len(cards), cols)]
    row_h = [max(head_h + 20 + line_h * max(len(c["lines"]), 1) for c in row) for row in rows_of]
    y_title = margin
    title_h = (60 if title else 0)
    total_h = margin + title_h + sum(row_h) + gap * max(len(row_h) - 1, 0) + (70 if footer else 0) + margin
    img = Image.new("RGB", (W, max(total_h, 200)), "white")
    d = ImageDraw.Draw(img)
    if title:
        d.text((margin, y_title), title, font=f_title, fill=(30, 30, 30))
        d.line([(margin, y_title + 52), (W - margin, y_title + 52)], fill=(31, 78, 121), width=4)
    y = margin + title_h + (16 if title else 0)
    for r_i, row in enumerate(rows_of):
        x = margin
        for c_i, c in enumerate(row):
            k = (r_i * cols + c_i) % len(PALETTE)
            h = row_h[r_i]
            d.rounded_rectangle([x, y, x + card_w, y + h], radius=14, fill=(247, 249, 252), outline=(200, 208, 220), width=2)
            d.rounded_rectangle([x, y, x + card_w, y + head_h], radius=14, fill=PALETTE[k])
            d.rectangle([x, y + head_h - 14, x + card_w, y + head_h], fill=PALETTE[k])
            d.text((x + 18, y + 11), c["title"][:40], font=f_head, fill="white")
            ty = y + head_h + 12
            for ln, first in c["lines"]:
                d.text((x + 18, ty), ln, font=f_body, fill=(40, 40, 40))
                ty += line_h
            if layout == "flow" and c_i < len(row) - 1:
                ax = x + card_w + 8
                ay = y + h // 2
                d.polygon([(ax, ay - 14), (ax + arrow_w - 16, ay - 14), (ax + arrow_w - 16, ay - 26), (ax + arrow_w, ay),
                           (ax + arrow_w - 16, ay + 26), (ax + arrow_w - 16, ay + 14), (ax, ay + 14)], fill=(120, 130, 150))
            x += card_w + gap + arrow_w
        y += row_h[r_i] + gap
    if footer:
        d.rounded_rectangle([margin, y, W - margin, y + 54], radius=12, fill=(31, 78, 121))
        d.text((margin + 20, y + 13), footer[:70], font=f_foot, fill="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def parse_spec(text: str) -> dict | None:
    """모델이 낸 도식 글(JSON)을 spec 으로. JSON 이 아니면 None."""
    import json

    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("blocks"), list):
        return None
    return data
