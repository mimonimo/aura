"""글·웹 문서·메일 본문 읽기 — csv·txt(글자), html·htm·mht(태그 뺀 본문), eml(제목·본문, 첨부는 넣지 않음).

학교 원본에는 한글 윈도의 옛 인코딩(cp949) 글 파일이 많다 — UTF-8 로 안 풀리면 cp949 로 읽는다.
"""
from __future__ import annotations

import csv
import email
import email.policy
import io
import re
from html.parser import HTMLParser
from pathlib import Path

_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}
_BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article",
               "header", "footer", "blockquote", "pre", "dt", "dd", "ul", "ol", "hr", "title"}
_CELL_TAGS = {"td", "th"}
_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.I)


def decode_bytes(raw: bytes, hint: str | None = None) -> str:
    """글자 풀기 — 알려진 인코딩(hint) → UTF-8 → cp949 → 깨진 글자는 대체 문자로."""
    for enc in (hint, "utf-8-sig", "cp949"):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def read_text(path: Path) -> str:
    return decode_bytes(Path(path).read_bytes())


def read_csv(path: Path) -> str:
    """CSV — 줄마다 칸을 「 | 」로 잇는다(빈 칸은 뺀다, 엑셀 읽기와 같은 꼴)."""
    text = read_text(path)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for r in csv.reader(io.StringIO(text), dialect):
        cells = [c.strip() for c in r if c and c.strip()]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


class _TextOnly(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.title: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self.skip += 1
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK_TAGS:
            self.out.append("\n")
        elif tag in _CELL_TAGS:
            self.out.append(" | ")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK_TAGS:
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title.append(data)
            return
        if not self.skip:
            self.out.append(data)


def html_text(html: str) -> str:
    """HTML 의 본문 글자만 — 스크립트·스타일은 버리고, 문단·표 칸 경계는 줄·「 | 」로 남긴다. 제목(title)은 맨 앞에."""
    p = _TextOnly()
    p.feed(html)
    p.close()
    lines = []
    for ln in "".join(p.out).split("\n"):
        ln = re.sub(r"[ \t ]+", " ", ln).strip(" |")
        if ln:
            lines.append(ln)
    title = re.sub(r"\s+", " ", "".join(p.title)).strip()
    if title and (not lines or lines[0] != title):
        lines.insert(0, title)
    return "\n".join(lines)


def read_html(path: Path) -> str:
    raw = Path(path).read_bytes()
    m = _CHARSET.search(raw[:4096])
    return html_text(decode_bytes(raw, m.group(1).decode("ascii", "ignore") if m else None))


def _part_text(part) -> str:
    raw = part.get_payload(decode=True) or b""
    text = decode_bytes(raw, part.get_content_charset())
    return html_text(text) if part.get_content_type() == "text/html" else text.strip()


def _body_parts(msg) -> list:
    """본문 부분만 — 첨부(Content-Disposition: attachment·파일 이름 있음)는 뺀다."""
    out = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        if part.get_content_maintype() != "text":
            continue
        if part.get_content_disposition() == "attachment" or part.get_filename():
            continue
        out.append(part)
    return out


def read_mht(path: Path) -> str:
    """웹 보관 파일(MHT, MIME 묶음) — HTML 부분의 본문 글자. HTML 이 없으면 글 부분."""
    msg = email.message_from_bytes(Path(path).read_bytes(), policy=email.policy.compat32)
    parts = [p for p in msg.walk() if not p.is_multipart() and p.get_content_maintype() == "text"]
    html = [p for p in parts if p.get_content_type() == "text/html"]
    texts = [_part_text(p) for p in (html or parts)]
    return "\n\n".join(t for t in texts if t)


def read_eml(path: Path) -> str:
    """메일(EML) — 제목과 본문. 같은 본문의 글·HTML 판이 함께 있으면 글 판만. 첨부 파일은 넣지 않는다."""
    msg = email.message_from_bytes(Path(path).read_bytes(), policy=email.policy.default)
    subject = str(msg.get("subject") or "").strip()
    parts = _body_parts(msg)
    plain = [p for p in parts if p.get_content_type() == "text/plain"]
    texts = [_part_text(p) for p in (plain or parts)]
    body = "\n\n".join(t for t in texts if t)
    return (f"제목: {subject}\n\n" if subject else "") + body


READERS = {".txt": read_text, ".md": read_text, ".csv": read_csv, ".html": read_html, ".htm": read_html,
           ".mht": read_mht, ".mhtml": read_mht, ".eml": read_eml}
