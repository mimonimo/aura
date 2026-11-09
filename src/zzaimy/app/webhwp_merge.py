"""칸 밖 고침 세 판 합치기 — 여럿이 같이 쓰는 한글 문서에서 내가 칸(필드) 밖에 쓴 글이 다른 사람의 문서 틀 저장에 묻혀 사라지지 않게(11-10).

세 판: base(내 화면이 연 판), mine(내 화면의 지금 문서), theirs(지금 서버 파일). base→mine 에서 내가 칸 밖에 새로 쓰거나 고친 맨 바깥 문단만
골라 theirs 의 같은 자리에 옮긴다. 칸 안 글은 공동 편집 칸 길(webhwp_coedit)이 따로 보내므로 여기서는 건드리지 않는다.
옮길 자리를 못 찾거나 theirs 에서도 같은 문단을 고쳤으면 그 글은 「옮기지 못함」 으로 돌려준다 — 부르는 쪽이 충돌 사본으로 남긴다(조용히 버리지 않음).
문단을 지운 고침은 옮기지 않는다(남의 글을 지우기보다 남기는 쪽이 안전하다) — 수만 알린다.
"""
from __future__ import annotations

import difflib
import io
import re
import zipfile
from html import unescape

_SEC = re.compile(r"Contents/section\d+\.xml$")
_HEADER = "Contents/header.xml"
_LINESEG = re.compile(r"<hp:linesegarray>.*?</hp:linesegarray>", re.S)


def _top_paras(xml: str) -> list[tuple[int, int]]:
    """맨 바깥 문단(<hp:p>…</hp:p>)의 (시작, 끝) — 표 칸 안 문단은 그 문단 안에 든다."""
    out, depth, start = [], 0, None
    for t in re.finditer(r"<hp:p\b[^>]*?(/?)>|</hp:p>", xml):
        g = t.group(0)
        if g == "</hp:p>":
            depth -= 1
            if depth == 0 and start is not None:
                out.append((start, t.end()))
                start = None
            if depth < 0:
                return out
        elif t.group(1) == "/":                       # <hp:p …/> 빈 문단
            if depth == 0:
                out.append((t.start(), t.end()))
        else:
            if depth == 0:
                start = t.start()
            depth += 1
    return out


def para_text(p: str) -> str:
    """문단 글 — 서식·칸 표시를 뺀 글(공백 하나로)."""
    p = re.sub(r"<hp:parameters\b.*?</hp:parameters>", "", p, flags=re.S)
    p = re.sub(r"<hp:lineBreak\s*/>", "\n", p)
    p = re.sub(r"<hp:tab\b[^>]*/>", "\t", p)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", p))).strip()


def _has_field(p: str) -> bool:
    return "<hp:fieldBegin" in p or "<hp:fieldEnd" in p


# 풀기 한도 — 합치기는 칸 밖 문단만 보므로 문서 열기(webhwp_fields.MAX_UNZIPPED 300MB)보다 작게. 세 판 각각에 건다(보안 검토 11-10).
MERGE_MAX_UNZIPPED = 64 * 1024 * 1024     # 한 판에서 실제로 풀어 읽는 본문·머리 XML 합계
MERGE_MAX_ENTRIES = 2000                  # 한 판의 zip 항목 수
_CHUNK = 1024 * 1024


class TooBig(ValueError):
    """한도를 넘는 판 — 합치지 않는다(부르는 쪽은 충돌 사본·안내로)."""


def _open(data: bytes) -> zipfile.ZipFile:
    """풀기 전에 잰다 — 항목 수, 선언 크기·압축률(공용 webhwp_fields.check_zip), 합치기 한도."""
    from zzaimy.app import webhwp_fields as wf
    zin = zipfile.ZipFile(io.BytesIO(data))
    infos = zin.infolist()
    if len(infos) > MERGE_MAX_ENTRIES:
        raise TooBig(f"한글 파일 항목이 너무 많습니다({len(infos)}개)")
    try:
        wf.check_zip(zin)
    except ValueError as e:
        raise TooBig(str(e)) from e
    want = sum(i.file_size for i in infos if _SEC.search(i.filename) or i.filename == _HEADER)
    if want > MERGE_MAX_UNZIPPED:
        raise TooBig("한글 파일 본문이 너무 큽니다(합치기 한도 초과)")
    return zin


def _read(zin: zipfile.ZipFile, name: str, budget: list[int]) -> str:
    """흘려 읽기 — 선언 크기를 믿지 않고 실제로 풀린 양을 세어 한도를 넘는 순간 멈춘다."""
    parts = []
    with zin.open(name) as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            budget[0] -= len(chunk)
            if budget[0] < 0:
                raise TooBig("한글 파일 본문이 너무 큽니다(합치기 한도 초과)")
            parts.append(chunk)
    return b"".join(parts).decode("utf-8")


def _sections(data: bytes, with_header: bool = False) -> tuple[zipfile.ZipFile, dict[str, str], str]:
    zin = _open(data)
    budget = [MERGE_MAX_UNZIPPED]
    secs = {n: _read(zin, n, budget) for n in zin.namelist() if _SEC.search(n)}
    header = _read(zin, _HEADER, budget) if with_header and _HEADER in zin.namelist() else ""
    return zin, secs, header


def _ids_in(header: str, kind: str) -> set[str]:
    return set(re.findall(rf'<hh:{kind}\b[^>]*\bid="(\d+)"', header))


def _fresh_para(src: str, anchor: str, theirs_header: str, used_ids: set[str]) -> str:
    """mine 의 문단을 theirs 에 넣을 꼴로 — 글자·문단 모양 번호가 theirs 머리에 없으면 이웃 문단(anchor) 모양을 쓴다, 줄 배치 캐시는 지운다,
    문단 id 는 theirs 에 없는 새 번호로."""
    p = _LINESEG.sub("", src)
    char_ok, para_ok = _ids_in(theirs_header, "charPr"), _ids_in(theirs_header, "paraPr")
    a_char = re.search(r'charPrIDRef="(\d+)"', anchor)
    a_para = re.search(r'paraPrIDRef="(\d+)"', anchor)

    def fix(attr: str, ok: set[str], fallback):
        def sub(m):
            v = m.group(1)
            if v in ok or not fallback:
                return m.group(0)
            return f'{attr}="{fallback.group(1)}"'
        return sub
    p = re.sub(r'charPrIDRef="(\d+)"', fix("charPrIDRef", char_ok, a_char), p)
    p = re.sub(r'paraPrIDRef="(\d+)"', fix("paraPrIDRef", para_ok, a_para), p)

    def new_id(m):
        import random
        while True:
            v = str(random.randint(1_000_000_000, 2_147_000_000))
            if v not in used_ids:
                used_ids.add(v)
                return f'{m.group(1)}id="{v}"'
    return re.sub(r'(<hp:p\b[^>]*?\s)id="\d+"', new_id, p, count=1)


def merge_outside(base: bytes, mine: bytes, theirs: bytes) -> tuple[bytes | None, dict]:
    """(합친 theirs 바이트 또는 None(옮길 것 없음), 보고 {moved, lost: [글], skipped_deletes}).
    lost 는 옮기지 못한 내 문단 글 — 부르는 쪽이 충돌 사본으로 남긴다.
    판이 한도(항목 수·압축률·풀린 크기)를 넘거나 풀 수 없으면 합치지 않고 error 와 too_big/unreadable 을 돌려준다 — 부르는 쪽이 사본·안내로."""
    rep = {"moved": 0, "lost": [], "skipped_deletes": 0}
    try:
        _, bs, _h = _sections(base)
        _, ms, _h = _sections(mine)
        tz, ts, header = _sections(theirs, with_header=True)
    except TooBig as e:
        return None, {**rep, "error": str(e), "too_big": True}
    except Exception:
        return None, {**rep, "error": "한글 파일을 풀지 못했습니다", "unreadable": True}
    used_ids = set(re.findall(r'\bid="(\d+)"', "".join(ts.values())))
    new_secs: dict[str, str] = {}
    for name in sorted(ms):
        mx, bx, tx = ms[name], bs.get(name), ts.get(name)
        m_sp = _top_paras(mx)
        m_ps = [mx[a:b] for a, b in m_sp]
        m_tx = [para_text(p) for p in m_ps]
        if bx is None or tx is None:                       # 내가 새 구역을 만들었다 — 자리를 맞출 수 없다
            rep["lost"] += [t for p, t in zip(m_ps, m_tx) if t and not _has_field(p)]
            continue
        b_ps = [bx[a:b] for a, b in _top_paras(bx)]
        t_sp = _top_paras(tx)
        t_ps = [tx[a:b] for a, b in t_sp]
        b_tx = [para_text(p) for p in b_ps]
        t_tx = [para_text(p) for p in t_ps]
        b2t: dict[int, int] = {}
        for blk in difflib.SequenceMatcher(None, b_tx, t_tx, autojunk=False).get_matching_blocks():
            for k in range(blk.size):
                b2t[blk.a + k] = blk.b + k
        edits: list[tuple[int, str, str, int]] = []      # (theirs 문단 번호, "before"/"replace", 넣을 xml, 차례)

        def place_insert(i1: int, paras: list[str]) -> None:
            anchor_i = None
            if i1 > 0 and (i1 - 1) in b2t:
                anchor_i, where = b2t[i1 - 1], "after"
            elif i1 in b2t:
                anchor_i, where = b2t[i1], "before"
            elif i1 == 0 and t_ps:
                anchor_i, where = 0, "before"
            if anchor_i is None:
                rep["lost"] += [para_text(p) for p in paras]
                return
            anchor = t_ps[anchor_i]
            xml = "".join(_fresh_para(p, anchor, header, used_ids) for p in paras)
            edits.append((anchor_i + 1 if where == "after" else anchor_i, "before", xml, len(edits)))
            rep["moved"] += len(paras)

        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, b_tx, m_tx, autojunk=False).get_opcodes():
            if tag == "equal":
                continue
            if tag == "delete":
                rep["skipped_deletes"] += i2 - i1
                continue
            mine_new = list(range(j1, j2))
            if tag == "insert":
                paras = [m_ps[j] for j in mine_new if m_tx[j] and not _has_field(m_ps[j])]
                if paras:
                    place_insert(i1, paras)
                continue
            # replace — 짝지어 본다: 칸 든 문단은 칸 길이 맡는다. 칸 밖 문단을 고쳤으면 theirs 가 그대로일 때만 바꿔 쓴다
            pairs = list(zip(range(i1, i2), mine_new))
            extra = mine_new[len(pairs):]
            for bi, mj in pairs:
                mp, bp = m_ps[mj], b_ps[bi]
                if _has_field(mp) or _has_field(bp) or not m_tx[mj]:
                    continue
                ti = b2t.get(bi)
                if ti is not None and t_tx[ti] == b_tx[bi]:
                    edits.append((ti, "replace", _fresh_para(mp, t_ps[ti], header, used_ids), len(edits)))
                    rep["moved"] += 1
                elif ti is None and b_tx[bi] not in t_tx:
                    # theirs 에서 그 문단이 바뀌었거나 없어졌다 — 겹친 고침. 내 글을 버리지 않고 옮기지 못함으로 돌린다
                    rep["lost"].append(m_tx[mj])
                else:
                    rep["lost"].append(m_tx[mj])
            if extra:
                paras = [m_ps[j] for j in extra if m_tx[j] and not _has_field(m_ps[j])]
                if paras:
                    place_insert(i2, paras)
            rep["skipped_deletes"] += max(0, (i2 - i1) - len(pairs))
        if not edits:
            continue
        out = tx
        # 뒤에서부터 — 같은 문단이면 바꿔 쓰기 먼저(자리 값이 그대로일 때), 그 앞에 넣을 것은 나중 것부터 넣어 차례가 mine 과 같게
        for ti, kind, xml, _seq in sorted(edits, key=lambda e: (e[0], 1 if e[1] == "replace" else 0, e[3]), reverse=True):
            if kind == "replace":
                a, b = t_sp[ti]
                out = out[:a] + xml + out[b:]
            else:
                at = t_sp[ti][0] if ti < len(t_sp) else (t_sp[-1][1] if t_sp else len(out))
                out = out[:at] + xml + out[at:]
        new_secs[name] = out
    if not new_secs:
        return None, rep
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for info in tz.infolist():
            body = new_secs[info.filename].encode("utf-8") if info.filename in new_secs else tz.read(info.filename)
            zout.writestr(info, body, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
    return buf.getvalue(), rep
