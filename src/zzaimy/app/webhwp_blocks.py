"""서식의 견본 블록 늘리기(11-11) — 「2.1. OOO(과제명)」 처럼 견본 하나만 든 과제·프로그램·지표 묶음을 담당자가 정한 수만큼 늘린다.

공모 서식은 과제·프로그램 견본을 하나씩만 두고 「필요한 만큼 늘려 쓴다」. 실제 계획서는 과제 여섯·프로그램 여럿으로 늘어나는데,
에이전트는 있는 절에만 쓸 수 있어 견본 하나에 과제 여섯을 몰아 쓰거나 나머지를 빠뜨렸다(전문대학 특성화 서식, 11-11 실측).

한 블록 = 제목 문단부터, 그 아래 같은 갈래이거나 더 높은 제목(번호 단계가 같거나 위) 앞까지 — 아래 단계 제목·본문 칸(필드)·표가 함께 든다.
복사본마다
- 맨 위 제목의 번호를 다음 차례로(2.1 → 2.2, 1) → 2)), 아래 제목의 앞 번호도 따라 바꾼다(2.1.1 → 2.2.1),
- 제목의 자리 표시(OOO(과제명)·○○○)를 담당자가 준 이름으로,
- 필드 이름을 새 제목 글로(겹치면 #n), 문단·필드·표 id 를 새로 매긴다.
원래 블록도 첫 이름으로 바꾼다. 본문 칸 안 글(이미 쓴 내용)은 복사본에서 비운다 — 같은 글이 되풀이되지 않게.
판단(몇 개·이름)은 담당자·에이전트, 이 모듈은 자르고 붙이기만 한다.
"""
from __future__ import annotations

import re

_P_OPEN = re.compile(r"<hp:p\b[^>]*>")
_HEAD_NUM = re.compile(r"^\s*(?:(?P<dots>\d+(?:\.\d+)*)\.?(?=\s)|(?P<paren>\d+)\)|\((?P<pp>\d+)\))")
PLACEHOLDER = re.compile(r"[OＯ○◯]{2,}\s*(?:\([^)]*\))?|0{3,}")
_FIELD_BEGIN = re.compile(r'(<hp:fieldBegin\b[^>]*?\bname=")([^"]*)(")')
_IDS = re.compile(r'\b(id|instid|beginIDRef|fieldid|zOrder)="(\d+)"')


class BlockError(ValueError):
    pass


def _text(p: str) -> str:
    return "".join(re.findall(r"<hp:t\b[^>]*>([^<]*)</hp:t>", p)).strip()


def level(head: str) -> tuple | None:
    """제목 번호의 단계 — (갈래, 깊이). 「2.」=(0,1) 「2.1.」=(0,2) 「2.1.1」=(0,3) 「1)」=(1,0) 「(1)」=(2,0). 번호가 없으면 None."""
    m = _HEAD_NUM.match(head or "")
    if not m:
        return None
    if m.group("dots"):
        return (0, m.group("dots").count(".") + 1)
    return (1, 0) if m.group("paren") else (2, 0)


def _rank(lv: tuple) -> int:
    """위아래 차례 — 작을수록 위. 점 번호 깊이 1~4, 그 아래 「1)」, 그 아래 「(1)」."""
    return lv[1] if lv[0] == 0 else 10 + lv[0]


def _top_paras(xml: str) -> list[tuple[int, int]]:
    """본문 문단(표 칸 안 문단은 빼고) 범위."""
    out, depth, start = [], 0, None
    for m in re.finditer(r"<hp:p\b[^>]*>|</hp:p>", xml):
        if m.group(0).startswith("<hp:p"):
            if depth == 0:
                start = m.start()
            depth += 1
        else:
            depth -= 1
            if depth == 0 and start is not None:
                out.append((start, m.end()))
                start = None
    return out


def _field_paras(xml: str, paras: list[tuple[int, int]]) -> set[int]:
    """필드(누름틀) 안에 든 문단 번호 — 본문 글은 제목으로 보지 않는다."""
    inside, open_ = set(), False
    for i, (a, b) in enumerate(paras):
        p = xml[a:b]
        nb, ne = p.count("<hp:fieldBegin"), p.count("<hp:fieldEnd")
        if open_ or nb:
            inside.add(i)
        if nb > ne:
            open_ = True
        elif ne > nb:
            open_ = False
    return inside


def _heading_paras(xml: str) -> list[tuple[int, int, int, str, tuple]]:
    """(문단 번호, 시작, 끝, 글, 단계) — 필드 밖 본문 문단 가운데 번호 제목만."""
    paras = _top_paras(xml)
    infield = _field_paras(xml, paras)
    out = []
    for i, (a, b) in enumerate(paras):
        if i in infield:
            continue
        p = xml[a:b]
        if "<hp:tbl" in p:
            continue
        t = _text(p)
        lv = level(t) if t and len(t) <= 120 else None
        if lv is None and t and len(t) <= 60 and _CHAPTER.match(t):
            # 장 제목(「Ⅲ. 성과관리 계획」 — 글상자에 든 것도)은 맨 위 경계다. 번호 제목만 보던 때는 Ⅱ장 끝 견본 블록이
            # 다음 장 제목 상자까지 품어, 늘린 블록마다 「Ⅲ.」 제목 상자가 복사돼 Ⅱ장 가운데 박혔다(11-12 특성화 재현 2차)
            lv = (0, 0)
        if lv:
            out.append((i, a, b, t, lv))
    return out


def find_block(xml: str, head: str) -> tuple[int, int, str, tuple] | None:
    """제목 글(앞뒤 공백·필드 이름 머리 「절:」·꼬리 「#n」 무시)로 블록 (시작, 끝, 제목 글, 단계)."""
    want = re.sub(r"#\d+$", "", re.sub(r"^절:", "", head or "")).strip()
    heads = _heading_paras(xml)
    pick = None
    if (head or "").startswith("절:"):                  # 필드 이름이면 그 필드 바로 앞 제목(같은 제목 글이 여럿이어도 한 곳을 짚는다)
        fm = next((m for m in _FIELD_BEGIN.finditer(xml) if m.group(2) == head), None)
        if fm:
            before = [k for k, h in enumerate(heads) if h[1] < fm.start()]
            if before and heads[before[-1]][3] == want:
                pick = before[-1]
    for k, (_, a, _, t, lv) in enumerate(heads):
        if (pick is not None and k != pick) or (pick is None and t != want):
            continue
        end = len(xml)
        for _, a2, _, _, lv2 in heads[k + 1:]:
            if _rank(lv2) <= _rank(lv):
                end = a2
                break
        else:
            m = xml.rfind("</hs:sec>")                     # 구역 끝까지 — 닫는 태그 앞에서 자른다
            end = m if m > a else len(xml)
        return a, end, t, lv
    return None


def _renum(t: str, old: str, new: str, lv: tuple) -> str:
    """제목 앞 번호를 바꾼다 — 블록 맨 위(old → new) 와 그 아래(old.x → new.x)."""
    if lv[0] == 0:
        return re.sub(rf"^(\s*){re.escape(old)}(?=[.\s])", lambda m: m.group(1) + new, t, count=1)
    if lv[0] == 1:
        return re.sub(rf"^(\s*){re.escape(old)}\)", lambda m: f"{m.group(1)}{new})", t, count=1)
    return re.sub(rf"^(\s*)\({re.escape(old)}\)", lambda m: f"{m.group(1)}({new})", t, count=1)


def _set_text(p: str, new: str) -> str:
    """문단 글을 바꾼다 — 첫 글 조각에 새 글, 나머지 글 조각은 비운다(글자 모양은 첫 조각 것)."""
    first = [True]

    def put(m):
        if first[0]:
            first[0] = False
            return m.group(1) + _esc(new) + "</hp:t>"
        return m.group(1) + "</hp:t>"
    out = re.sub(r"(<hp:t\b[^>]*>)[^<]*</hp:t>", put, p)
    return re.sub(r"<hp:linesegarray>.*?</hp:linesegarray>", "", out, flags=re.S)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _clear_fields(seg: str) -> str:
    """복사본 본문 칸 안 글을 비운다 — 필드 시작·끝 사이의 글 조각만(제목·표 머리 글은 둔다)."""
    out, pos = [], 0
    for m in re.finditer(r"<hp:fieldBegin\b.*?</hp:fieldBegin></hp:ctrl>", seg, re.S):
        e = seg.find("<hp:fieldEnd", m.end())
        if e < 0:
            break
        out.append(seg[pos:m.end()])
        inner = seg[m.end():e]
        if "<hp:tbl" not in inner:
            inner = re.sub(r"(<hp:t\b[^>]*>)[^<]*(</hp:t>)", r"\1\2", inner)
        out.append(inner)
        pos = e
    out.append(seg[pos:])
    return "".join(out)


def _reid(seg: str, ids) -> str:
    """복사본의 id 를 새로 — 같은 옛 id 는 같은 새 id 로(필드 시작·끝이 서로를 가리키는 값이 맞게)."""
    table: dict[str, str] = {}

    def sub(m):
        k, v = m.group(1), m.group(2)
        if k == "zOrder":
            return m.group(0)
        if v not in table:
            table[v] = str(ids.new())
        return f'{k}="{table[v]}"'
    return _IDS.sub(sub, seg)


def name_heading(head: str, name: str) -> str:
    """제목의 자리 표시를 이름으로 — 자리 표시가 없으면 번호 뒤 글을 이름으로 바꾼다."""
    if PLACEHOLDER.search(head):
        return PLACEHOLDER.sub(lambda m: name, head, count=1)
    m = _HEAD_NUM.match(head)
    return (head[:m.end()] + " " + name) if m else name


def repeat_xml(xml: str, head: str, names: list[str], ids, taken: set[str], outside: set[str] | None = None) -> tuple[str, list[str]]:
    """구역 XML 에서 블록을 이름 수만큼으로. (새 XML, 새 블록 맨 위 제목 글들)."""
    found = find_block(xml, head)
    if not found:
        raise BlockError(f"「{head}」 제목을 찾지 못했다 — hwp_fields 의 절 이름(번호 제목)으로 준다.")
    a, end, top, lv = found
    m = _HEAD_NUM.match(top)
    num = m.group("dots") or m.group("paren") or m.group("pp")
    block = xml[a:end]
    if lv[0] == 0:
        parts = num.split(".")
        mk = lambda i: ".".join(parts[:-1] + [str(int(parts[-1]) + i)])
    else:
        mk = lambda i: str(int(num) + i)
    # 이 블록의 옛 이름은 다시 써도 된다 — 단 블록 밖(outside: 부분 XML 로 부를 때 그 바깥)에도 있는 이름은 그대로 막는다
    taken = (set(taken) - set(m.group(2) for m in _FIELD_BEGIN.finditer(block))) | set(outside or ())
    segs, tops = [], []
    for i, name in enumerate(names):
        seg = block if i == 0 else _reid(_clear_fields(block), ids)
        new_num = mk(i)
        out, pos = [], 0
        for pa, pb in _top_paras(seg):
            p = seg[pa:pb]
            t = _text(p)
            lv2 = level(t) if t and "<hp:tbl" not in p and "<hp:fieldBegin" not in p else None
            if lv2 is not None and (pa == 0 or _rank(lv2) > _rank(lv)):
                t2 = t
                if pa == 0:
                    t2 = name_heading(_renum(t, num, new_num, lv), name)
                    tops.append(t2)
                elif lv[0] == 0 and lv2[0] == 0:
                    t2 = _renum(t, num, new_num, (0, 0))
                if t2 != t:
                    p = _set_text(p, t2)
            out.append(seg[pos:pa])
            out.append(p)
            pos = pb
        out.append(seg[pos:])
        seg = "".join(out)
        segs.append(_rename_fields(seg, taken))
    return xml[:a] + "".join(segs) + xml[end:], tops


def _rename_fields(xml: str, taken: set[str]) -> str:
    """블록 안 절 필드 이름을 그 필드 바로 앞 제목 글로 맞춘다 — 늘린 블록의 필드가 옛 제목 이름(「절:2.1. OOO(과제명)」)을 갖지 않게.
    문서 안에서 겹치면 #2, #3 …(서식 필드 이름 규칙과 같다). 칸 필드(「칸:」)·문단 필드는 그대로. 매긴 이름은 taken 에 더한다."""
    paras = _top_paras(xml)
    last_head = None
    renames: list[tuple[int, int, str]] = []
    for a, b in paras:
        p = xml[a:b]
        t = _text(p)
        if t and "<hp:fieldBegin" not in p and "<hp:tbl" not in p and level(t):
            last_head = t
            continue
        for m in _FIELD_BEGIN.finditer(p):
            nm = m.group(2)
            if not nm.startswith("절:") or not last_head:
                continue
            base, k = "절:" + last_head, 1
            want = base
            while want in taken:
                k += 1
                want = f"{base}#{k}"
            taken.add(want)
            if want != nm:
                renames.append((a + m.start(2), a + m.end(2), want))
            last_head = None
    for s, e, want in sorted(renames, reverse=True):
        xml = xml[:s] + _esc(want).replace('"', "&quot;") + xml[e:]
    return xml


def _first_child(xml: str, a: int, end: int, lv: tuple) -> str | None:
    """블록 안 첫 아래 견본 제목(자리 표시가 든 것, 없으면 「1)」 갈래 첫 제목) — 과제 블록 안 프로그램 견본."""
    sub = xml[a:end]
    heads = [h for h in _heading_paras(sub) if h[1] > 0 and _rank(h[4]) > _rank(lv)]
    for h in heads:
        if PLACEHOLDER.search(h[3]):
            return h[3]
    kids = [h for h in heads if h[4][0] == 1]
    return kids[0][3] if kids else None


def repeat_tree(xml: str, head: str, items: list, ids, taken: set[str]) -> tuple[str, list[str]]:
    """블록 늘리기를 한 번에 — items 는 이름 글 또는 {name, children: [아래 블록 이름…]}.
    먼저 위 블록(과제)을 늘리고, 블록마다 그 안의 첫 아래 견본(프로그램)을 children 수만큼 늘린다. (새 XML, 만든 제목 글들)."""
    names = [(it.get("name") if isinstance(it, dict) else it) for it in items]
    names = [str(n or "").strip() for n in names]
    if not all(names):
        raise BlockError("이름이 빈 블록이 있다.")
    xml, tops = repeat_xml(xml, head, names, ids, taken)
    made = list(tops)
    for it, top in zip(items, tops):
        kids = [str(x).strip() for x in ((it.get("children") or []) if isinstance(it, dict) else []) if str(x).strip()]
        if not kids:
            continue
        found = find_block(xml, top)
        if not found:
            continue
        a, end, _, lv = found
        child = _first_child(xml, a, end, lv)
        if not child:
            raise BlockError(f"「{top}」 블록 안에 늘릴 아래 견본(「1) OOO(프로그램명)」 같은 제목)이 없다.")
        outside = {m.group(2) for m in _FIELD_BEGIN.finditer(xml[:a] + xml[end:])}
        sub, kid_tops = repeat_xml(xml[a:end], child, kids, ids, taken, outside)
        xml = xml[:a] + sub + xml[end:]
        made += ["  " + t for t in kid_tops]
    return xml, made


_CHAPTER = re.compile(r"^\s*([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ])\s*\.\s*\S")
_SCAN = re.compile(r"<hp:tbl\b|</hp:tbl>|<hp:t\b[^>]*>([^<]*)</hp:t>|<hp:ctrl><hp:fieldBegin\b[^>]*\bname=\"([^\"]*)\"")


def field_paths(data: bytes) -> dict[str, list[str]]:
    """필드마다 문서 속 자리(위 제목들) — {이름: [장 제목, 위 절, …, 바로 위 절]}.

    반복 블록을 늘리면 「(1) 5개년 추진계획」 같은 같은 꼴 필드가 #2·#14 처럼 여럿 생기고, 번호는 문서 차례가 아니다.
    이름만 보고는 어느 과제·프로그램 것인지 알 수 없어 에이전트가 다른 과제의 칸에 썼다(11-12 특성화 재현: 3.1 의 내용을 2.3 칸 #14 에).
    장 제목(「Ⅲ. 성과관리 계획」 — 표 밖 문단)과 절 필드의 번호 단계(level)로 위 제목 사슬을 만든다."""
    from zzaimy.app import webhwp_write as ww
    _, secs = ww._open(data)
    order = sorted(secs, key=lambda n: int(m.group(1)) if (m := re.search(r"(\d+)\.xml$", n)) else 0)
    out: dict[str, list[str]] = {}
    chapter, stack = "", []                                  # stack: [(차례, 제목)]
    for n in order:
        depth = 0
        for m in _SCAN.finditer(secs[n]):
            g = m.group(0)
            if g.startswith("<hp:tbl"):
                depth += 1
                continue
            if g.startswith("</hp:tbl"):
                depth = max(0, depth - 1)
                continue
            if m.group(2) is not None:
                name = ww._unq(m.group(2))
                if name.startswith("문단:") or ww.is_cont(name) or name in out:
                    continue
                head = re.sub(r"#\d+$", "", name.split(":", 1)[-1]).strip()
                lv = level(head) if name.startswith("절:") else None
                if lv is not None:
                    r = _rank(lv)
                    while stack and stack[-1][0] >= r:
                        stack.pop()
                out[name] = ([chapter] if chapter else []) + [t for _, t in stack]
                if lv is not None:
                    stack.append((_rank(lv), head))
                continue
            t = (m.group(1) or "").strip()
            if depth == 0 and len(t) <= 60 and _CHAPTER.match(t):
                if t != chapter:
                    chapter, stack = t, []
    return out


def path_text(path: list[str], width: int = 28) -> str:
    """자리 사슬을 한 줄로 — 「Ⅱ. 사업추진 계획 › 3.1. YNC-CDMP… › 1) …」(제목은 앞 width 자)."""
    return " › ".join(p if len(p) <= width else p[:width] + "…" for p in path)
