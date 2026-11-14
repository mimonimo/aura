"""기관 서식 프로필(11-15) — 그 기관이 직접 쓴 문서(지난 계획서·보고서)에서 캐낸 글자 모양을, 양식이 정하지 않은 자리에 쓴다.

왜: 양식(지원 기관이 낸 서식)은 장·절 제목과 작성방법만 두고 본문 모양은 정하지 않는 일이 많다. 그 자리를 플랫폼 기본값(명조 11pt)으로
채우면 학교가 실제로 내는 문서(특성화 04: 한컴 윤고딕 720 12pt 개조식, 표 10pt)와 모양이 갈렸다. 학교의 지난 계획서(LINC3.0 2023·2024)도
같은 글꼴 집안(윤고딕 720 본문, 윤고딕 250 표)이었다 — 기관마다 일정한 모양이 있고, 그것은 그 기관 문서에서만 알 수 있다.

고르는 차례: 양식에 그 자리의 견본 모양이 있으면 양식(서식 작성자의 뜻) → 없으면 기관 프로필 → 없으면 플랫폼 기본(export/house_style).
프로필은 파생 결과물이다 — scripts/229_house_profile.py 가 원본 문서에서 다시 만들고(입력 지문이 바뀌면), data/platform/house_profile.json 에 둔다.
값은 분포의 최빈값만 담는다(문서 글은 옮기지 않는다).
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path

ROLES = ("chapter", "h1", "h2", "bullet_□", "bullet_○", "bullet_ㆍ", "bullet_-", "bullet_▪", "bullet_※", "body",
         "table_head", "table_cell", "box")
_CHAPTER = re.compile(r"^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.\s")
_H1 = re.compile(r"^\s*\d{1,2}\.\s")
_H2 = re.compile(r"^\s*(\d{1,2}\.\d{1,2}\.?|[가-하]\.)\s")
_BULLET = {"□": "□", "■": "□", "❏": "□", "○": "○", "◦": "○", "ㅇ": "○", "●": "○", "◎": "○",
           "·": "ㆍ", "ㆍ": "ㆍ", "•": "ㆍ", "‧": "ㆍ", "-": "-", "–": "-", "▪": "▪", "▫": "▪", "※": "※"}


def role_of(text: str, in_table: bool = False, head_row: bool = False, box: bool = False) -> str:
    """문단 글의 역할 — 표 칸·상자·제목 단계·개조식 부호·본문."""
    t = (text or "").strip()
    if box:
        return "box"
    if in_table:
        return "table_head" if head_row else "table_cell"
    if _CHAPTER.match(t):
        return "chapter"
    if _H2.match(t):
        return "h2"
    if _H1.match(t):
        return "h1"
    if t[:1] in _BULLET:
        return "bullet_" + _BULLET[t[:1]]
    return "body" if len(t) > 40 else ""


def mine(paths: list[Path]) -> dict:
    """문서들 → 프로필 {roles: {역할: {font, pt, bold, chars}}, sources, at}. 글 양(글자 수)으로 무게를 둔 최빈값."""
    from zzaimy.eval import style_compare as sc
    count: dict[str, Counter] = {r: Counter() for r in ROLES}
    used = []
    for p in paths:
        try:
            paras, st = (sc.read_hwp if str(p).lower().endswith(".hwp") else sc.read_hwpx)(Path(p))
        except Exception:
            continue
        used.append(Path(p).name)

        def add(role: str, run, text: str) -> None:
            ch = st.chars.get(str(run.get("charPrIDRef") or ""))
            if role and ch and ch.color.upper().lstrip("#") in ("000000", "") and text.strip():
                count[role][(ch.font, round(ch.size_pt, 1), bool(ch.bold))] += len(text.strip())

        for para in paras:
            tbls = [x for x in para.iter() if sc._local(x.tag) == "tbl"]
            if not tbls:
                text = sc._para_text(para)
                role = role_of(text)
                for run in sc._kids(para, "run"):
                    add(role, run, sc._run_text(run))
                continue
            for tbl in tbls:
                rows = int(tbl.get("rowCnt") or 0)
                cols = int(tbl.get("colCnt") or 0)
                box = rows == 1 and cols == 1
                for tr in sc._kids(tbl, "tr"):
                    for tc in sc._kids(tr, "tc"):
                        addr = next(iter(sc._kids(tc, "cellAddr")), None)
                        head = addr is not None and (addr.get("rowAddr") or "0") == "0" and rows > 1
                        for q in tc.iter():
                            if sc._local(q.tag) != "p":
                                continue
                            text = sc._para_text(q)
                            # 상자(1×1 표) 안 글은 상자 역할로 따로 — 상자 본문(LINC: 윤고딕 250 11pt)과 상자 밖 본문(윤고딕 720 13pt)은 모양이 다르다.
                            # 에이전트가 절 필드 뒤에 쓰는 본문은 상자 밖 문단이라 본문·개조식 역할은 상자 밖 문단에서만 센다
                            role = role_of(text, True, head, box)
                            for run in sc._kids(q, "run"):
                                add(role, run, sc._run_text(run))
    roles = {}
    for r, cnt in count.items():
        if not cnt:
            continue
        # 글꼴·크기·굵기를 따로 센다 — 짧은 굵은 머리줄이 섞인 본문에서 한 묶음 최빈값을 고르면 본문 전체가 굵게 되었다
        fonts, sizes, total, bold_n = Counter(), Counter(), 0, 0
        for (font, pt, bold), n in cnt.items():
            fonts[font] += n
            sizes[pt] += n
            total += n
            bold_n += n if bold else 0
        font, nf = fonts.most_common(1)[0]
        pt, _ = sizes.most_common(1)[0]
        roles[r] = {"font": font, "pt": pt, "bold": bold_n / total >= 0.6, "chars": total,
                    "share": round(nf / total, 2), "bold_share": round(bold_n / total, 2)}
    return {"roles": roles, "sources": used, "at": time.strftime("%Y-%m-%d %H:%M")}


def path(data_dir: Path | str) -> Path:
    return Path(data_dir) / "house_profile.json"


def load(data_dir: Path | str | None) -> dict:
    """프로필 — 없거나 못 읽으면 빈 dict(플랫폼 기본으로 돈다)."""
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


MIN_CHARS = 300          # 이보다 적게 본 역할은 믿지 않는다(우연한 한두 문단)


def style(prof: dict, role: str) -> dict | None:
    """역할의 {font, pt, bold} — 근거가 적으면 None. 개조식 부호 역할이 없으면 본문 역할로."""
    roles = (prof or {}).get("roles") or {}
    got = roles.get(role)
    if (not got or got.get("chars", 0) < MIN_CHARS) and role.startswith("bullet_"):
        got = roles.get("body") if (roles.get("body") or {}).get("chars", 0) >= MIN_CHARS else None
    return got if got and got.get("chars", 0) >= MIN_CHARS else None


_DIR: Path | None = None


def configure(data_dir: Path | str) -> None:
    """앱이 뜰 때 프로필 자리(data/platform)를 정한다."""
    global _DIR
    _DIR = Path(data_dir)


def current() -> dict:
    return load(_DIR)


def apply_to(data: bytes, pids, prof: dict | None = None, text: bool = True, skip_marks: set[str] | None = None) -> bytes:
    """새로 쓴 문단(pids — 안에 든 표 포함)의 글자에 기관 프로필 모양을 입힌다. text=False 면 표 칸만(양식이 본문 모양을 정한 때).
    역할: 상자 밖 문단은 글 꼴(제목 단계·개조식 부호·본문), 표는 머리 행·자료 칸, 1×1 표는 상자. 프로필에 근거가 적은 역할은 그대로.
    skip_marks: 양식이 본문 견본(검정 글씨)을 둔 부호 — 그 줄은 양식 모양을 둔다. 굵기는 프로필이 뚜렷할 때만(6할 넘게 굵음·2할 밑) 바꾼다."""
    prof = prof if prof is not None else current()
    pids = {str(x) for x in (pids or [])}
    if not prof or not (prof.get("roles")) or not pids:
        return data
    import io
    import zipfile

    from zzaimy.app import webhwp_profile as wpf
    from zzaimy.app import webhwp_style as ws
    z = zipfile.ZipFile(io.BytesIO(data))
    hdr = ws._Header(z.read("Contents/header.xml").decode("utf-8"))
    run_cp = re.compile(r'(<hp:run\b[^>]*\bcharPrIDRef=")(\d+)')

    def restyle(frag: str, role: str) -> str:
        st = style(prof, role)
        if not st:
            return frag
        bs = st.get("bold_share")
        bold = True if (bs if bs is not None else (1 if st.get("bold") else 0)) >= 0.6 else False if (bs if bs is not None else 0) <= 0.2 else None
        return run_cp.sub(lambda m: m.group(1) + hdr.char_like(m.group(2), st["font"], st["pt"], bold), frag)

    def plain_text(frag: str) -> str:
        return re.sub(r"<[^>]+>", "", re.sub(r"<hp:parameters\b.*?</hp:parameters>", "", frag, flags=re.S))

    new: dict[str, str] = {}
    for n in [x for x in z.namelist() if re.match(r"Contents/section\d+\.xml$", x)]:
        xml = z.read(n).decode("utf-8")
        blocks = wpf._blocks(xml, pids)
        if not blocks:
            continue
        out, last = [], 0
        for a, b in blocks:
            frag = xml[a:b]
            if "<hp:tbl" in frag:
                rc = re.search(r'<hp:tbl\b[^>]*\browCnt="(\d+)"[^>]*\bcolCnt="(\d+)"', frag)
                rows, cols = (int(rc.group(1)), int(rc.group(2))) if rc else (0, 0)

                def cell(m):
                    tc = m.group(0)
                    head = rows > 1 and re.search(r'<hp:cellAddr\b[^>]*rowAddr="0"', tc) is not None
                    return restyle(tc, "box" if rows == 1 and cols == 1 else "table_head" if head else "table_cell")
                frag = re.sub(r"<hp:tc\b.*?</hp:tc>", cell, frag, flags=re.S)
            elif text:
                t_ = plain_text(frag).strip()
                from zzaimy.ingest import hwpx_fill as _hf
                role = role_of(t_)
                if role and _hf.marker_of(t_) not in (skip_marks or set()):    # 양식이 그 부호의 본문 견본(검정)을 둔 줄은 양식대로
                    frag = restyle(frag, role)
            out.append(xml[last:a] + frag)
            last = b
        new[n] = "".join(out) + xml[last:]
    if not new:
        return data
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zo:
        for info in z.infolist():
            body = (new[info.filename].encode("utf-8") if info.filename in new
                    else hdr.xml.encode("utf-8") if info.filename == "Contents/header.xml" else z.read(info.filename))
            zo.writestr(info, body, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
    return buf.getvalue()
