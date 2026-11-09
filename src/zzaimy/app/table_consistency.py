"""표 사이 같은 값 맞추기 — 절을 나눠(동시에) 쓰면 한 표에는 값이 들어가고 다른 표의 같은 칸은 「확인 필요」로 남는다
(11-09 결과보고서: 일정 표의 「고객만족 서비스 관리사 특강 · 장소」는 [확인 필요], 다른 표의 같은 행·열에는 값). 같은 문서 안에서
행 이름(첫 칸)과 열 머리가 같은 칸에 값이 하나뿐이면 그 값으로 채운다. 값이 둘 이상 다르면 고르지 않고 남은 칸 목록만 알린다 —
판단이 필요한 것은 에이전트 몫이다."""
from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape

_TBL = re.compile(r"<hp:tbl\b.*?</hp:tbl>", re.S)
_TR = re.compile(r"<hp:tr\b.*?</hp:tr>", re.S)
_TC = re.compile(r"<hp:tc\b.*?</hp:tc>", re.S)
_T = re.compile(r"<hp:t>([^<]*)</hp:t>")
_PLACE = re.compile(r"^\s*[\[\(【]?\s*확인\s*필요\s*[\]\)】]?\s*$")


def _text(tc: str) -> str:
    return "".join(_T.findall(tc)).strip()


def _norm(s: str) -> str:
    return re.sub(r"[\s\[\]()（）·・,./:：「」『』<>\-~]", "", s or "")


def _same(a: str, b: str, least: int = 2) -> bool:
    """두 이름이 같은 것을 가리키는가 — 정규화한 글이 같거나, 짧은 쪽이 least 글자 이상이고 긴 쪽에 들어 있다."""
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= least and short in long_


def _cells(xml: str) -> list[dict]:
    """행·열 이름이 있는 칸 — 머리 행(첫 줄)과 첫 칸(행 이름)이 있는 목록형 표만(라벨 | 값 두 칸 표는 행 이름이 곧 항목이라 뺀다)."""
    out = []
    for ti, tm in enumerate(_TBL.finditer(xml)):
        rows = [_TC.findall(r) for r in _TR.findall(tm.group(0))]
        if len(rows) < 2 or max((len(r) for r in rows), default=0) < 3:
            continue
        heads = [_norm(_text(tc)) for tc in rows[0]]
        for ri, row in enumerate(rows[1:], start=1):
            if not row:
                continue
            key = _norm(_text(row[0]))
            if not key or _PLACE.match(_text(row[0])):
                continue
            for ci, tc in enumerate(row[1:], start=1):
                if ci < len(heads) and heads[ci]:
                    out.append({"t": ti, "r": ri, "c": ci, "key": key, "head": heads[ci], "text": _text(tc),
                                "row": _text(row[0]), "col": _text(rows[0][ci])})
    return out


def cross_fill(data: bytes) -> tuple[bytes, list[str], list[str]]:
    """(새 문서, 채운 칸 설명, 남은 「확인 필요」 칸 설명). 바꿀 것이 없으면 data 를 그대로 돌려준다."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    names = [n for n in zin.namelist() if re.match(r"Contents/section\d+\.xml$", n)]
    secs = {n: zin.read(n).decode("utf-8") for n in names}
    every = [(n, c) for n in names for c in _cells(secs[n])]
    filled, left = [], []
    plan: dict[str, dict[tuple[int, int, int], str]] = {}
    for n, c in every:
        if not _PLACE.match(c["text"]):
            continue
        vals = {o["text"] for _, o in every if not _PLACE.match(o["text"]) and o["text"]
                and _same(o["key"], c["key"], 4) and _same(o["head"], c["head"]) and (o is not c)}
        if len(vals) == 1:
            v = vals.pop()
            plan.setdefault(n, {})[(c["t"], c["r"], c["c"])] = v
            filled.append(f"「{c['row']}」 · 「{c['col']}」 → {v}")
        else:
            left.append(f"「{c['row']}」 · 「{c['col']}」" + (f"(다른 표에 값이 여럿: {', '.join(sorted(vals))[:80]})" if vals else ""))
    if not plan:
        return data, [], left
    for n, cells in plan.items():
        xml = secs[n]
        out, last = [], 0
        for ti, tm in enumerate(_TBL.finditer(xml)):
            if not any(k[0] == ti for k in cells):
                continue
            tbl = tm.group(0)
            trs = list(_TR.finditer(tbl))
            new_tbl, tl = [], 0
            for ri, trm in enumerate(trs):
                tcs = list(_TC.finditer(trm.group(0)))
                tr = trm.group(0)
                new_tr, cl = [], 0
                for ci, tcm in enumerate(tcs):
                    v = cells.get((ti, ri, ci))
                    if v is None:
                        continue
                    tc = tcm.group(0)
                    first = [True]

                    def put(m: re.Match) -> str:                  # 첫 글 조각에 값, 나머지 조각은 비운다
                        if first[0]:
                            first[0] = False
                            return f"<hp:t>{escape(v)}</hp:t>"
                        return "<hp:t></hp:t>"
                    tc2 = _T.sub(put, tc)
                    tc2 = re.sub(r"<hp:linesegarray>.*?</hp:linesegarray>", "", tc2, flags=re.S)   # 줄 배치는 기안기가 다시
                    new_tr.append(tr[cl:tcm.start()] + tc2)
                    cl = tcm.end()
                new_tr.append(tr[cl:])
                new_tbl.append(tbl[tl:trm.start()] + "".join(new_tr))
                tl = trm.end()
            new_tbl.append(tbl[tl:])
            out.append(xml[last:tm.start()] + "".join(new_tbl))
            last = tm.end()
        out.append(xml[last:])
        secs[n] = "".join(out)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            body = secs[info.filename].encode("utf-8") if info.filename in secs else zin.read(info.filename)
            zout.writestr(info, body, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
    return buf.getvalue(), filled, left
