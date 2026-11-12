"""작업본 서식 규격(11-12) — 양식에서 본문·표 모양을 한 번 읽어 두고, AURA 가 쓰는 모든 글이 그 규격을 따르게 한다.

사용자 판정(특성화 재현 3차): 「주먹구구식이다. 문서를 보고 기본 서식 규격을 만들든지, 제공된 문서가 있으면 거기에 맞춰라. 글자 색도」.
견본·작성방법 글(파랑·빨강·기울임) 자리에 값과 본문을 넣으면 그 글자 꼴을 물려받아 완성본이 파란 기울임으로 남았다.

- profile(data): 양식의 규격 — 본문 글자(글꼴·크기)·줄 간격, 표 머리줄(바탕색·굵게)·자료 칸 글자 크기, 쓰인 글자 색. 없으면 학교 기본 꼴
  (export/house_style.HOUSE). 작업본을 만들 때(hwp_start) 뽑아 webhwp meta 「profile」 에 두고 hwp_styles·상태 줄에 한 줄로 보인다.
- plain_runs(data, pids, fields): 이번에 쓴 문단(pids — 안에 든 표·도식 칸 포함)과 필드 안 글의 글자 모양을 본문 꼴(검정·기울임 없음)로.
  쓰는 길(절 쓰기·표 넣기·칸 값)은 이 함수나 같은 규칙(webhwp_style._Header.char_plain)을 거친다.
- off_profile(data, pids): 쓴 문단 안에 규격 밖 글자(검정 아닌 색·기울임)가 남았는지 — 품질 점검(webhwp_lint)용.
"""
from __future__ import annotations

import io
import re
import zipfile

_SEC = re.compile(r"Contents/section(\d+)\.xml$")
_RUN_CP = re.compile(r'(<hp:run\b[^>]*\bcharPrIDRef=")(\d+)')


def _open(data: bytes) -> tuple[zipfile.ZipFile, dict[str, str], str]:
    z = zipfile.ZipFile(io.BytesIO(data))
    secs = {n: z.read(n).decode("utf-8") for n in z.namelist() if _SEC.search(n)}
    return z, secs, z.read("Contents/header.xml").decode("utf-8")


def _pack(z: zipfile.ZipFile, new: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for info in z.infolist():
            body = new[info.filename].encode("utf-8") if info.filename in new else z.read(info.filename)
            out.writestr(info, body, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def _para_block(xml: str, start: int) -> int:
    """start 에서 열린 <hp:p> 의 짝 </hp:p> 끝 위치(안에 든 표 칸 문단까지 센다)."""
    depth, i = 0, start
    pat = re.compile(r"<hp:p\b|</hp:p>")
    for m in pat.finditer(xml, start):
        depth += 1 if m.group(0) == "<hp:p" else -1
        if depth == 0:
            return m.end()
        i = m.end()
    return len(xml)


def _blocks(xml: str, pids: set[str]) -> list[tuple[int, int]]:
    out = []
    for m in re.finditer(r'<hp:p\b[^>]*\bid="(\d+)"', xml):
        if m.group(1) in pids and not any(a <= m.start() < b for a, b in out):
            out.append((m.start(), _para_block(xml, m.start())))
    return out


def plain_runs(data: bytes, pids, fields=()) -> bytes:
    """pids 문단(안에 든 표·도식 포함)과 fields 필드 안 글의 글자 모양을 본문 꼴로(검정·기울임 없음). 바뀐 게 없으면 그대로."""
    from zzaimy.app import webhwp_style as ws
    from zzaimy.app import webhwp_write as ww
    pids = {str(p) for p in (pids or [])}
    names = {str(f) for f in (fields or [])}
    if not pids and not names:
        return data
    z, secs, hdr0 = _open(data)
    H = ws._Header(hdr0)
    new: dict[str, str] = {}
    for n, xml in secs.items():
        spans = _blocks(xml, pids) if pids else []
        if names:
            for m in ww._BEGIN.finditer(xml):
                if ww._unq(m.group(2)) in names:
                    sp = ww._span(xml, m.group(1), m.end())
                    run_open = xml.rfind("<hp:run", 0, m.start())
                    if sp:
                        spans.append((run_open if run_open >= 0 else m.start(), sp[1]))
        if not spans:
            continue
        spans.sort()
        buf, pos = [], 0
        for a, b in spans:
            if a < pos:
                a = pos
            if b <= a:
                continue
            buf.append(xml[pos:a])
            buf.append(_RUN_CP.sub(lambda q: q.group(1) + H.char_plain(q.group(2)), xml[a:b]))
            pos = b
        buf.append(xml[pos:])
        x2 = "".join(buf)
        if x2 != xml:
            new[n] = x2
    if not new:
        return data
    if H.xml != hdr0:
        new["Contents/header.xml"] = H.xml
    return _pack(z, new)


def _char_info(header: str, cp: str) -> dict:
    m = re.search(rf'<hh:charPr\b[^>]*\bid="{cp}"[^>]*>(?:(?!</hh:charPr>).)*</hh:charPr>', header, re.S)
    if not m:
        return {}
    b = m.group(0)
    h = re.search(r'\bheight="(\d+)"', b)
    col = re.search(r'\btextColor="#([0-9A-Fa-f]{6})"', b)
    font = re.search(r'<hh:fontRef\b[^>]*\bhangul="(\d+)"', b)
    face = ""
    if font:
        g = re.search(r'<hh:fontface\b[^>]*lang="HANGUL"[^>]*>.*?</hh:fontface>', header, re.S)
        f = re.search(rf'<hh:font\b[^>]*\bid="{font.group(1)}"[^>]*\bface="([^"]+)"', g.group(0)) if g else None
        face = f.group(1) if f else ""
    return {"pt": round(int(h.group(1)) / 100, 1) if h else None, "color": "#" + col.group(1).upper() if col else "#000000",
            "italic": "<hh:italic" in b, "bold": "<hh:bold" in b, "face": face}


def profile(data: bytes) -> dict:
    """양식의 서식 규격 요약 — {body: {face, pt}, table: {head_fill, head_bold, cell_pt}, colors: [본문에 쓴 색], source: form|house}.
    양식 본문 문단(긴 문장)·서식 표의 역할별 최빈 모양에서 읽고, 없으면 학교 기본 꼴(HOUSE)."""
    from zzaimy.app import webhwp_write as ww
    from zzaimy.export import house_style as hs
    from zzaimy.ingest import hwpx_fill as hf
    z, secs, header = _open(data)
    solid = hf._solid_border_ids(header)
    objs = [hf._Section(n, secs[n], solid) for n in sorted(secs, key=lambda n: int(_SEC.search(n).group(1)))]
    body_cp = ww._body_char(objs, header) or hf._default_refs(header)[1]
    roles = hf._role_styles(objs, header)
    body = _char_info(header, body_cp)
    out = {"body": {"face": body.get("face", ""), "pt": body.get("pt")}, "source": "form" if "head" in roles else "house"}
    if "head" in roles:
        bf = roles["head"][0]
        fill = re.search(rf'<hh:borderFill\b[^>]*\bid="{bf}"[^>]*>.*?</hh:borderFill>', header, re.S)
        face = re.search(r'faceColor="(#[0-9A-Fa-f]{6})"', fill.group(0)) if fill else None
        head = _char_info(header, roles["head"][2])
        out["table"] = {"head_fill": face.group(1).upper() if face else hs.HOUSE["head_fill"], "head_bold": bool(head.get("bold")),
                        "cell_pt": _char_info(header, roles.get("body", roles["head"])[2]).get("pt")}
    else:
        out["table"] = {"head_fill": hs.HOUSE["head_fill"], "head_bold": hs.HOUSE["head_bold"], "cell_pt": hs.HOUSE["font_pt"]}
    out["colors"] = ["#000000"]
    return out


def summary(p: dict | None) -> str:
    """규격 한 줄 — 상태 줄·hwp_styles 에 보인다."""
    if not p:
        return ""
    b, t = p.get("body") or {}, p.get("table") or {}
    src = "양식에서 읽음" if p.get("source") == "form" else "학교 기본 꼴"
    return (f"서식 규격({src}): 본문 {b.get('face') or '기본 글꼴'} {b.get('pt') or '?'}pt · 표 머리줄 바탕 {t.get('head_fill')}"
            f"{' 굵게' if t.get('head_bold') else ''} · 표 칸 {t.get('cell_pt') or '?'}pt(열 6개부터 9pt) · 글자 색 검정 — "
            "쓰는 글·값은 이 꼴로 들어간다(견본 글의 파랑·기울임은 물려받지 않는다).")


def off_profile(data: bytes, pids) -> list[str]:
    """쓴 문단 안에 남은 규격 밖 글자 — ["「글 앞부분」(파랑·기울임)"]. 품질 점검용(쓰기 없음)."""
    pids = {str(p) for p in (pids or [])}
    if not pids:
        return []
    _, secs, header = _open(data)
    bad = {}
    for m in re.finditer(r'<hh:charPr\b[^>]*\bid="(\d+)"[^>]*>(?:(?!</hh:charPr>).)*</hh:charPr>', header, re.S):
        col = re.search(r'\btextColor="#([0-9A-Fa-f]{6})"', m.group(0))
        why = []
        if col and col.group(1).upper() != "000000":
            why.append("색 글자")
        if "<hh:italic" in m.group(0):
            why.append("기울임")
        if why:
            bad[m.group(1)] = "·".join(why)
    out = []
    for xml in secs.values():
        for a, b in _blocks(xml, pids):
            for r in re.finditer(r'<hp:run\b[^>/]*\bcharPrIDRef="(\d+)"[^>/]*>(.*?)</hp:run>', xml[a:b], re.S):
                t = "".join(re.findall(r"<hp:t\b[^>]*>([^<]*)</hp:t>", r.group(2))).strip()
                if t and r.group(1) in bad:
                    out.append(f"「{t[:20]}」({bad[r.group(1)]})")
    return out[:20]


def ensure(db, fid: int, path) -> dict:
    """작업본의 규격 — 처음 부를 때(작업본을 만든 뒤 첫 걸음) 양식에서 뽑아 설정 webhwp_profile:<id> 에 두고, 그다음은 그것을 쓴다.
    (webhwp meta 와 따로 둔다 — 쓰기 기록(written)을 고치는 다른 길과 겹쳐 쓰지 않게.)"""
    import json
    from pathlib import Path
    key = f"webhwp_profile:{int(fid)}"
    try:
        got = json.loads(db.get_setting(key, "") or "{}")
        if got:
            return got
    except ValueError:
        pass
    try:
        p = profile(Path(path).read_bytes())
    except Exception:
        return {}
    db.set_setting(key, json.dumps(p, ensure_ascii=False))
    return p
