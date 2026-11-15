"""표 갈래별 디자인 값 — 학교 실문서의 표를 갈래(export/table_kinds)로 나눠 갈래마다 최빈 모양을 캐고, 새 표에 입힐 값을 고른다.

캐기(mine): 반입 원본(hwpx·hwp)의 표마다 칸 글로 갈래를 가리고, 머리줄 음영·굵기·정렬·글자 크기, 열 값 꼴(수·금액·날짜·기호·라벨·글)별 정렬,
첫 열 라벨 칸 음영, 합계 행 모양, 바깥·안쪽·머리줄 아래 선 굵기, 한 줄 행 높이, 열 값 꼴별 열 폭을 센다. 문서 글은 옮기지 않고 분포만 남긴다.
결과는 파생 결과물이다 — scripts/230_table_styles.py 가 원본에서 다시 만들고 data/platform/table_styles.json 에 둔다(입력 지문이 같으면 그대로).

고르기(design): 갈래 기본값(table_kinds.KIND_DEFAULT) 위에, 그 갈래 표본이 MIN_TABLES 이상일 때만 캔 값을 얹는다. 한 값의 몫이 MIN_SHARE 아래면
(학교 표가 한 모양으로 모이지 않으면) 그 값은 기본값을 둔다. 양식에 견본 표 모양이 있으면 쓰는 쪽(webhwp_write)이 양식을 먼저 따른다.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from zzaimy.export import table_kinds as tk

MIN_TABLES = 15          # 갈래 표본이 이보다 적으면 캔 값을 쓰지 않는다
MIN_DOCS = 3             # 갈래 표본이 이보다 적은 문서에서 왔으면(한 문서의 버릇) 쓰지 않는다
SHORT_TYPES = ("symbol", "empty", "date", "percent", "money", "number")   # 갈래마다 폭을 따로 캐는 열 꼴 — 글 열 폭은 글 길이 몫이라 전체 값
PT_SHARE = 0.6           # 글자 크기는 문서마다 달라 갈래 차이로 보려면 더 모여야 한다
MIN_SHARE = 0.35         # 최빈값의 몫이 이보다 작으면(흩어진 분포) 기본값
PER_DOC = 40             # 한 문서가 한 갈래에 던지는 표 상한 — 표 수백 개짜리 계획서 한두 건이 분포를 덮지 않게
HU_PER_MM = 7200 / 25.4


# ── 캐기 ──────────────────────────────────────────────────────────────────────────────────────────

def _cell_info(tc, st, sc) -> dict:
    addr = next(iter(sc._kids(tc, "cellAddr")), None)
    span = next(iter(sc._kids(tc, "cellSpan")), None)
    sz = next(iter(sc._kids(tc, "cellSz")), None)
    chars: Counter = Counter()
    bold_n = 0
    aligns: Counter = Counter()
    texts = []
    for p in tc.iter():
        if sc._local(p.tag) != "p":
            continue
        t_p = ""
        for run in sc._kids(p, "run"):
            t = sc._run_text(run)
            if not t.strip():
                continue
            ch = st.chars.get(str(run.get("charPrIDRef") or ""))
            chars[round(ch.size_pt, 1) if ch else 10.0] += len(t.strip())
            bold_n += len(t.strip()) if ch and ch.bold else 0
            t_p += t
        if t_p.strip():
            ps = st.paras.get(str(p.get("paraPrIDRef") or ""))
            aligns[(ps.align if ps else "JUSTIFY")] += len(t_p.strip())
            texts.append(t_p.strip())
    n = sum(chars.values())
    bf = st.borders.get(str(tc.get("borderFillIDRef") or ""))
    return {"r": int(addr.get("rowAddr") or 0) if addr is not None else 0,
            "c": int(addr.get("colAddr") or 0) if addr is not None else 0,
            "rs": int(span.get("rowSpan") or 1) if span is not None else 1,
            "cs": int(span.get("colSpan") or 1) if span is not None else 1,
            "w": int(sz.get("width") or 0) if sz is not None else 0,
            "h": int(sz.get("height") or 0) if sz is not None else 0,
            "text": " ".join(" ".join(texts).split()), "field": False,
            "pt": chars.most_common(1)[0][0] if chars else None, "bold": (bold_n / n >= 0.5) if n else None,
            "align": aligns.most_common(1)[0][0] if aligns else None,
            "fill": ("#" + bf.fill.upper()) if bf and bf.fill else "none", "sides": dict(bf.sides) if bf else {}}


def _width_key(mm: float) -> str:
    return f"{round(float(mm), 2):g} mm"


def table_record(tbl, st, sc) -> dict | None:
    """표 하나 → 갈래와 모양 기록. 한 열·한 행 표(상자), 안에 표가 든 겉 표(배치 틀)는 None."""
    if any(sc._local(x.tag) == "tbl" for x in list(tbl.iter())[1:]):
        return None
    cells = [_cell_info(tc, st, sc) for tr in sc._kids(tbl, "tr") for tc in sc._kids(tr, "tc")]
    if not cells:
        return None
    nrows = max(c["r"] + c["rs"] for c in cells)
    ncols = max(c["c"] + c["cs"] for c in cells)
    if nrows < 2 or ncols < 2 or nrows > 400:
        return None
    from zzaimy.export import house_style as hs
    rl = hs.cell_roles(cells)
    hr = max([c["r"] + c["rs"] for c in cells if rl.get((c["r"], c["c"])) == "head"] or [0])
    grid = [[""] * ncols for _ in range(nrows)]
    for c in cells:
        grid[c["r"]][c["c"]] = c["text"]
    if not any(any(r) for r in grid):
        return None
    if ncols == 2 and tk.head_rows_of(grid) == 0:          # 2열 「라벨 | 값」 표의 첫 행은 머리줄이 아니다(새 표 판정과 같은 기준)
        hr = 0
    kind, f = tk.classify(grid, hr)
    types = f["types"]
    rec: dict = {"kind": kind, "rows": nrows, "cols": ncols, "head_rows": hr, "head": [], "body": [], "label": [], "total": [],
                 "outer": [], "outer_lr": [], "outer_tb": [], "inner": [], "rule": [], "row_h": [], "col_mm": [], "body_pt": [], "head_pt": []}
    totals = set(f["total_rows"])
    widths: dict[int, int] = {}
    for c in cells:
        if c["cs"] == 1 and c["w"] and c["c"] not in widths:
            widths[c["c"]] = c["w"]
    for ci, t in enumerate(types):
        if ci in widths:
            rec["col_mm"].append((t, round(widths[ci] / HU_PER_MM, 1)))
    rows_h: dict[int, list] = defaultdict(list)
    for c in cells:
        sides = c["sides"]
        for side, edge in (("left", c["c"] == 0), ("right", c["c"] + c["cs"] >= ncols), ("top", c["r"] == 0),
                           ("bottom", c["r"] + c["rs"] >= nrows)):
            kind_w = sides.get(side)
            if not kind_w:
                continue
            typ, mm = kind_w
            val = "none" if typ == "NONE" else _width_key(mm)
            if edge:
                rec["outer"].append(val)
                rec["outer_lr" if side in ("left", "right") else "outer_tb"].append(val)
            elif not (side == "bottom" and hr and c["r"] + c["rs"] == hr) and not (side == "top" and hr and c["r"] == hr):
                rec["inner"].append(val)
        if not c["text"]:
            continue
        if c["r"] < hr:
            rec["head"].append({"fill": c["fill"], "bold": c["bold"], "align": c["align"]})
            if c["pt"]:
                rec["head_pt"].append(c["pt"])
            if c["r"] + c["rs"] == hr and hr < nrows:
                b = sides.get("bottom")
                if b:
                    rec["rule"].append("none" if b[0] == "NONE" else ("DOUBLE" if "DOUBLE" in b[0] else _width_key(b[1])))
            continue
        if c["r"] in totals:
            rec["total"].append({"fill": c["fill"], "bold": c["bold"]})
            continue
        t = types[c["c"]] if c["c"] < len(types) else "text"
        if c["c"] == 0 and t in ("label", "text"):
            rec["label"].append({"fill": c["fill"], "bold": c["bold"], "align": c["align"]})
        rec["body"].append({"type": tk.value_type(c["text"]) if c["cs"] == 1 else t, "align": c["align"], "fill": c["fill"]})
        if c["pt"]:
            rec["body_pt"].append(c["pt"])
        if c["rs"] == 1 and c["h"]:
            rows_h[c["r"]].append((c["h"], hs.text_units(c["text"])))
    for r, hs_ in rows_h.items():
        if all(u <= 12 for _, u in hs_):                    # 한 줄 행만 — 긴 글 칸이 행 높이를 키운 행은 뺀다
            rec["row_h"].append(max(h for h, _ in hs_))
    return rec


class _Tally:
    """갈래마다 표 단위 표 세기 — 한 표는 값마다 한 표(그 표 안의 다수 값)를 던진다(큰 표 하나가 분포를 덮지 않게)."""

    def __init__(self):
        self.n = 0
        self.docs: set[str] = set()
        self.c: dict[str, Counter] = defaultdict(Counter)

    def vote(self, key: str, values: list) -> None:
        vals = [v for v in values if v not in (None, "")]
        if vals:
            self.c[key][Counter(vals).most_common(1)[0][0]] += 1

    def add(self, rec: dict, doc: str) -> None:
        self.n += 1
        self.docs.add(doc)
        h = rec["head"]
        if rec["head_rows"]:
            self.vote("head_fill", [x["fill"] for x in h])
            self.vote("head_bold", [x["bold"] for x in h])
            self.vote("head_align", [x["align"] for x in h])
            self.vote("head_pt", rec["head_pt"])
            self.vote("head_rule", rec["rule"])
        self.vote("body_pt", rec["body_pt"])
        self.vote("label_fill", [x["fill"] for x in rec["label"]])
        self.vote("label_align", [x["align"] for x in rec["label"]])
        self.vote("label_bold", [x["bold"] for x in rec["label"]])
        if rec["total"]:
            self.vote("total_fill", [x["fill"] for x in rec["total"]])
            self.vote("total_bold", [x["bold"] for x in rec["total"]])
        self.vote("outer", rec["outer"])
        self.vote("outer_lr", rec["outer_lr"])
        self.vote("outer_tb", rec["outer_tb"])
        self.vote("inner", rec["inner"])
        by_type: dict[str, list] = defaultdict(list)
        for b in rec["body"]:
            by_type[b["type"]].append(b["align"])
        for t, al in by_type.items():
            self.vote("align:" + t, al)
        if rec["row_h"]:
            self.c["row_h"][int(round(sorted(rec["row_h"])[len(rec["row_h"]) // 2] / 100.0) * 100)] += 1
        for t, mm in rec["col_mm"]:
            self.c["col_mm:" + t][int(round(mm))] += 1

    def summary(self) -> dict:
        out: dict = {"n": self.n, "docs": len(self.docs), "values": {}}
        for k, cnt in sorted(self.c.items()):
            if k.endswith("_fill"):
                # 음영은 색 갈래(회색·파랑·노랑·초록·없음…)로 먼저 모은다 — 옅은 회색 여러 값(#F2F2F2·#E5E5E5·#D8D8D8)이 표를 나눠 가져
                # 어느 하나도 다수가 되지 못하는 일. 갈래 안에서는 가장 많이 쓴 색
                fam: Counter = Counter()
                for v, n in cnt.items():
                    fam[fill_family(str(v))] += n
                fv, fn = fam.most_common(1)[0]
                best = max((v for v in cnt if fill_family(str(v)) == fv), key=lambda v: cnt[v])
                out["values"][k + ":family"] = {"mode": best, "family": fv, "share": round(fn / sum(cnt.values()), 2),
                                                "n": sum(cnt.values()), "top": [[a, b] for a, b in fam.most_common(4)]}
            tot = sum(cnt.values())
            if k.startswith("col_mm:") or k == "row_h":
                vals = sorted(v for v, n in cnt.items() for _ in range(n))
                out["values"][k] = {"median": vals[len(vals) // 2], "p10": vals[len(vals) // 10], "n": tot}
                continue
            v, n = cnt.most_common(1)[0]
            out["values"][k] = {"mode": v, "share": round(n / tot, 2), "n": tot,
                                "top": [[str(a), b] for a, b in cnt.most_common(4)]}
        return out


def fill_family(v: str) -> str:
    """음영 색 → 갈래(none·gray·blue·yellow·green·red·other). 옅고 짙음은 묻지 않는다."""
    m = re.fullmatch(r"#?([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})", v or "")
    if not m:
        return "none"
    r, g, b = (int(x, 16) for x in m.groups())
    if max(r, g, b) - min(r, g, b) <= 16:
        return "gray"
    if b >= r and b >= g:
        return "blue"
    if r >= b and g >= b and abs(r - g) <= 40:
        return "yellow"
    if g >= r and g >= b:
        return "green"
    return "red" if r >= g and r >= b else "other"


HWP_TIMEOUT_S = 120      # .hwp 한 건 구조 풀기 상한 — 넘으면 건너뛴다
HWP_MAX_MB = 20


def read_doc(p: Path):
    """(문단, 모양) — hwpx 는 그대로, .hwp 는 pyhwp 구조 XML 을 표준 출력으로 받아 읽는다(원본 서버에 캐시·임시 파일을 쓰지 않는다).
    앱이 이미 만든 구조 XML 캐시(hwp5_docx.dump_xml)가 있으면 그것을 읽기만 한다."""
    from zzaimy.eval import style_compare as sc
    p = Path(p)
    if p.suffix.lower() != ".hwp":
        return sc.read_hwpx(p)
    import hashlib
    import os
    import subprocess

    from zzaimy.ingest import hwp5_docx as h
    hh = hashlib.sha256()
    hh.update(str(p.stat().st_size).encode())
    with p.open("rb") as f:
        hh.update(f.read(1 << 20))
    cached = Path(os.environ.get("ZZAIMY_DATA_DIR", "data/platform")).parent / "cache" / "hwp5xml" / f"{hh.hexdigest()[:24]}.xml"
    if cached.exists() and cached.stat().st_size > 100:
        root = sc._parse(str(cached)).getroot()
    else:
        if p.stat().st_size > HWP_MAX_MB * 1e6:
            raise RuntimeError("큰 hwp")
        exe = h._hwp5proc()
        if exe is None:
            raise RuntimeError("hwp5proc 없음")
        out = subprocess.run([str(exe), "xml", str(p)], check=True, capture_output=True, timeout=HWP_TIMEOUT_S).stdout
        root = sc._fromstring(out)
    st, _ = h._load_styles(root.find("DocInfo"))
    body = root.find("BodyText")
    paras = []
    for sec in (body.findall("SectionDef") + body.findall("Section")) if body is not None else []:
        for p_el in sec.findall("Paragraph") + [q for cs in sec.findall("ColumnSet") for q in cs.findall("Paragraph")]:
            paras.append(h._translate_paragraph(p_el))
    return paras, st


def mine(paths: list[Path], log=None) -> dict:
    """문서들 → {kinds: {갈래: {n, docs, values}}, all: {...}, sources, tables, at}."""
    from zzaimy.eval import style_compare as sc
    tallies: dict[str, _Tally] = defaultdict(_Tally)
    used = 0
    for i, p in enumerate(paths):
        try:
            paras, st = read_doc(Path(p))
        except Exception as e:  # noqa: BLE001 — 못 읽는 문서는 건너뛴다
            if log:
                log(f"  건너뜀 {Path(p).parent.name[:40]}: {type(e).__name__}")
            continue
        used += 1
        seen = set()
        per_doc: Counter = Counter()
        for para in paras:
            for tbl in (x for x in para.iter() if sc._local(x.tag) == "tbl"):
                if id(tbl) in seen:
                    continue
                seen.add(id(tbl))
                try:
                    rec = table_record(tbl, st, sc)
                except Exception:  # noqa: BLE001
                    rec = None
                if rec and per_doc[rec["kind"]] < PER_DOC:
                    per_doc[rec["kind"]] += 1
                    tallies[rec["kind"]].add(rec, str(p))
                    tallies["_all"].add(rec, str(p))
        if log and (i + 1) % 20 == 0:
            log(f"  {i + 1}/{len(paths)} 문서, 표 {tallies['_all'].n}")
    kinds = {k: t.summary() for k, t in tallies.items() if k != "_all"}
    return {"kinds": kinds, "all": tallies["_all"].summary() if "_all" in tallies else {"n": 0},
            "sources": used, "tables": tallies["_all"].n if "_all" in tallies else 0, "at": time.strftime("%Y-%m-%d %H:%M")}


# ── 저장·읽기 ──────────────────────────────────────────────────────────────────────────────────────

def path(data_dir: Path | str) -> Path:
    return Path(data_dir) / "table_styles.json"


def load(data_dir: Path | str | None) -> dict:
    if not data_dir:
        return {}
    try:
        return json.loads(path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(data_dir: Path | str, prof: dict) -> Path:
    p = path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return p


_DIR: Path | None = None
_CACHE: dict = {"key": None, "val": {}}


def configure(data_dir: Path | str) -> None:
    """앱이 뜰 때 자리(data/platform)를 정한다."""
    global _DIR
    _DIR = Path(data_dir)


def current() -> dict:
    """캔 값 — 파일 시각이 바뀌면 다시 읽는다(230 을 다시 돌리면 재시작 없이 따라온다)."""
    if not _DIR:
        return {}
    p = path(_DIR)
    try:
        key = (str(p), p.stat().st_mtime)
    except OSError:
        return {}
    if _CACHE["key"] != key:
        _CACHE["key"], _CACHE["val"] = key, load(_DIR)
    return _CACHE["val"]


# ── 고르기 ─────────────────────────────────────────────────────────────────────────────────────────

_ALIGN_OK = {"CENTER", "LEFT", "RIGHT"}


def _align(v) -> str | None:
    """캔 정렬 → 새 표 정렬. 양쪽·배분은 왼쪽으로 — 좁은 칸에서 글자 사이가 벌어진다(webhwp_style.style_table 과 같은 까닭)."""
    v = str(v or "").upper()
    if v in ("JUSTIFY", "DISTRIBUTE", "DISTRIBUTE_SPACE"):
        return "LEFT"
    return v if v in _ALIGN_OK else None


def _fill(v) -> str | None:
    v = str(v or "")
    if v == "none":
        return "none"
    return v.upper() if re.fullmatch(r"#[0-9A-Fa-f]{6}", v) else None


def _width_or_none(v) -> str | None:
    """바깥 선 — 굵기 또는 none(선 없음: 학교 표에 흔한 좌우 열린 표)."""
    return "none" if str(v or "") == "none" else _width(v)


def _width(v) -> str | None:
    m = re.fullmatch(r"([\d.]+) mm", str(v or ""))
    return f"{float(m.group(1)):g} mm" if m and 0.05 <= float(m.group(1)) <= 1.0 else None


def mined_design(kind: str, prof: dict | None = None) -> dict:
    """갈래의 캔 값 가운데 믿을 만한 것만 디자인 키로(표본 MIN_TABLES·몫 MIN_SHARE). 없으면 빈 dict."""
    prof = prof if prof is not None else current()
    got = (prof or {}).get("all") if kind == "_all" else ((prof or {}).get("kinds") or {}).get(kind)
    got = got or {}
    if int(got.get("n") or 0) < MIN_TABLES or int(got.get("docs") or 0) < MIN_DOCS:
        return {}
    vals = got.get("values") or {}

    def mode(k, conv):
        v = vals.get(k) or {}
        if not v or float(v.get("share") or 0) < MIN_SHARE or int(v.get("n") or 0) < MIN_TABLES // 3:
            return None
        return conv(v.get("mode"))

    out: dict = {}
    for k in ("head_fill", "label_fill", "total_fill"):
        x = mode(k + ":family", _fill)                    # 색 갈래 다수 → 그 갈래에서 가장 많이 쓴 색
        if x:
            out[k] = x
    for k, conv in (("head_align", _align), ("label_align", _align), ("outer", _width), ("inner", _width), ("outer_tb", _width_or_none),
                    ("outer_lr", _width_or_none)):
        x = mode(k, conv)
        if x:
            out[k] = x
    for k in ("head_bold", "label_bold", "total_bold"):
        x = mode(k, lambda v: v if isinstance(v, bool) else str(v) == "True")
        if x is not None:
            out[k] = x
    for k in ("head_pt", "body_pt"):
        v = vals.get(k) or {}
        if float(v.get("share") or 0) >= PT_SHARE and v.get("mode") not in (None, "") and 7 <= float(v["mode"]) <= 14:
            out[k] = float(v["mode"])
    rule = mode("head_rule", lambda v: "DOUBLE" if v == "DOUBLE" else "THIN" if v == out.get("inner") else "SOLID" if _width(v) else None)
    if rule:
        out["head_rule"] = rule
    rh = vals.get("row_h") or {}
    if int(rh.get("n") or 0) >= MIN_TABLES // 3 and 1000 <= int(rh.get("median") or 0) <= 4000:
        out["row_h"] = int(rh["median"])
    al, mm = {}, {}
    for t in tk.COL_TYPES:
        x = mode("align:" + t, _align)
        if x:
            al[t] = x
        cm = vals.get("col_mm:" + t) or {}
        if (kind == "_all" or t in SHORT_TYPES) and int(cm.get("n") or 0) >= MIN_TABLES // 3 and 4 <= int(cm.get("median") or 0) <= 170:
            mm[t] = float(cm["median"])
    if al:
        out["align"] = al
    if mm:
        out["col_mm"] = mm
    return out


def design(kind: str, prof: dict | None = None) -> dict:
    """새 표에 입힐 디자인 — 갈래 기본값 위에 전체 표에서 캔 값, 그 위에 그 갈래에서 캔 값(갈래가 뚜렷하지 않은 값은 전체 값으로)."""
    prof = prof if prof is not None else current()
    allv = mined_design("_all", prof)
    for k in ("head_pt", "body_pt", "label_fill", "label_align", "label_bold"):   # 갈래마다 다른 것 — 전체 값으로 채우지 않는다
        allv.pop(k, None)
    return tk.merge(tk.merge(tk.default_design(kind), allv), mined_design(kind, prof))


def pt_delta(kind: str, role: str, prof: dict | None = None) -> float:
    """갈래의 글자 크기가 전체 표 최빈 크기와 얼마나 다른가(pt) — 기관 서식 프로필이 칸 크기를 정할 때 갈래 차이만 얹는다."""
    prof = prof if prof is not None else current()
    key = "head_pt" if role == "head" else "body_pt"
    k = mined_design(kind, prof).get(key)
    base = (((prof or {}).get("all") or {}).get("values") or {}).get(key) or {}
    try:
        return round(float(k) - float(base.get("mode")), 1) if k and base.get("mode") else 0.0
    except (TypeError, ValueError):
        return 0.0
