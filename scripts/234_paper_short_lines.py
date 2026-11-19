"""논문 docx 에서 마지막 줄에 한두 낱말만 남는 문단 찾기 — 워드처럼 어절 단위로 줄을 나눠 맑은 고딕 글자 폭으로 흉내 낸다.

워드 PDF 가 없어도 쪽과 무관하게 문단 안 줄바꿈은 정해진다(폭·글꼴·크기·들여쓰기). 「…이루어진 / 다.」 같은 줄을 찾아 문장을 다듬는 데 쓴다(11-19 사용자).
쓰기: python scripts/234_paper_short_lines.py 논문.docx [--max 3]
"""
from __future__ import annotations
import argparse, re, sys
import docx
from docx.shared import Emu
from fontTools.ttLib import TTFont

FONT = "/Applications/Microsoft Word.app/Contents/Resources/DFonts/malgun.ttf"
BOLD = "/Applications/Microsoft Word.app/Contents/Resources/DFonts/malgunbd.ttf"


class Metric:
    def __init__(self, path):
        f = TTFont(path); self.cmap = f.getBestCmap(); self.hmtx = f["hmtx"]; self.upm = f["head"].unitsPerEm
    def w(self, s, pt):
        tot = 0
        for ch in s:
            g = self.cmap.get(ord(ch))
            tot += self.hmtx[g][0] if g else self.upm
        return tot / self.upm * pt


def lines(text, width, first, pt, m):
    words = text.split(" ")
    out, cur, avail = [], "", width - first
    for wd in words:
        cand = (cur + " " + wd) if cur else wd
        if m.w(cand.rstrip(), pt) <= avail or not cur:
            cur = cand
        else:
            out.append(cur); cur = wd; avail = width
    out.append(cur)
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("docx"); ap.add_argument("--max", type=int, default=3)
    a = ap.parse_args()
    d = docx.Document(a.docx); m = Metric(FONT)
    sec = d.sections[-1]; W = Emu(sec.page_width - sec.left_margin - sec.right_margin).pt
    hits = 0
    for p in d.paragraphs:
        t = p.text.strip()
        if len(t) < 40 or p.style.name.lower().startswith(("toc", "heading", "caption")):
            continue
        pf = p.paragraph_format
        left = (pf.left_indent.pt if pf.left_indent else 0) + (pf.right_indent.pt if pf.right_indent else 0)
        first = pf.first_line_indent.pt if pf.first_line_indent else 0
        sizes = [r.font.size.pt for r in p.runs if r.font.size]
        pt = max(set(sizes), key=sizes.count) if sizes else 10
        ls = lines(t, W - left, first, pt, m)
        if len(ls) >= 2 and len(ls[-1].replace(" ", "")) <= a.max:
            hits += 1
            print(f"{ls[-2][-25:]} / {ls[-1]}")
    print(f"합 {hits}", file=sys.stderr)


if __name__ == "__main__":
    main()
