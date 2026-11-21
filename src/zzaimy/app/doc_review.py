"""마치기 전 문서 다시 보기 — 27B 가 「완료」를 말하기 전에 작업본을 다시 읽게 하는 자료.

사람이 문서를 다 쓰고 처음부터 훑어보며 고치듯, 에이전트가 finish 를 부르면 코드가 작업본을 읽어
절마다 무엇이 들었는지(문단 수·표 머리줄)와 눈에 띄는 문제 후보를 글로 돌려준다. 고칠지·어떻게 고칠지는
27B 가 판단한다(코드는 보여 주기만 — 사례 규칙으로 고치지 않는다). 11-17 포스터 예시: 다른 절에 들어간 표,
빈 예시 표, 「확인 필요」, 한자 섞임, 같은 글이 되풀이된 열, 머리줄 없는 표, 1) 없이 시작한 2) 를 「완료」 뒤에야 찾았다.
"""
from __future__ import annotations

import re

from zzaimy.app import webhwp_write as ww

_PH = re.compile(r"확인\s*필요|○○|\(\s*\)|XX|본문\s*입력")
_HAN = re.compile(r"[一-鿿]+")
_TBL = re.compile(r"<hp:tbl\b.*?</hp:tbl>", re.S)
_TR = re.compile(r"<hp:tr\b.*?</hp:tr>", re.S)
_TC = re.compile(r"<hp:tc\b.*?</hp:tc>", re.S)
_T = re.compile(r"<hp:t\b[^>]*>((?:[^<]|<hp:(?:tab|lineBreak|fwSpace|nbSpace|hyphen)\b[^>]*/>)*)</hp:t>")   # 「가)⇥본문」 처럼 탭·줄바꿈이 낀 글도(11-18 — 8절 항목을 빈 문단으로 읽었다)
_INL = re.compile(r"<[^>]+>")
_PARA = re.compile(r"<hp:p\b.*?</hp:p>", re.S)
_ITEM = re.compile(r"^\s*(\d{1,2})\)\s")
MAX_ISSUES = 12


def _txt(xml: str) -> str:
    return re.sub(r"\s+", " ", _INL.sub(" ", "".join(_T.findall(re.sub(r"<hp:parameters\b.*?</hp:parameters>", "", xml, flags=re.S))))).strip()


def _rows(tbl: str) -> list[list[str]]:
    return [[_txt(c) for c in _TC.findall(r)] for r in _TR.findall(tbl)]


def _sections(data: bytes) -> list[tuple[str, str]]:
    """(절 이름, 그 절 XML) — 절 필드부터 다음 절 필드 앞까지. 첫 절 앞(표지·요약 표)은 「머리」."""
    _, secs = ww._open(data)
    out: list[tuple[str, str]] = []
    for n in ww._sec_order(secs):
        xml = secs[n]
        # 절 필드가 든 문단의 시작부터 — 필드 자리에서 자르면 첫 문단(「1) …」·첫 글)이 잘려 읽히지 않는다
        marks = [((ww._para_bounds(xml, m.start()) or (m.start(),))[0], ww._unq(m.group(2)))
                 for m in ww._BEGIN.finditer(xml) if ww._unq(m.group(2)).startswith("절:")]
        if not marks:
            out.append(("머리", xml))
            continue
        out.append(("머리", xml[:marks[0][0]]))
        for i, (at, name) in enumerate(marks):
            out.append((name, xml[at:marks[i + 1][0] if i + 1 < len(marks) else len(xml)]))
    return out


FULL_MAX = 16000                     # 다시 읽기에 싣는 문서 글 상한(자) — 넘으면 절마다 앞부분만


def full_text(data: bytes, only: set[int] | None = None) -> str:
    """작업본을 사람이 읽는 차례대로 — 절 제목, 문단, 표(마크다운). only: 이 번호의 절만 글까지(나머지는 제목만)."""
    parts = []
    tno = 0
    for name, xml in _sections(data):
        parts.append(f"## {name.split(':', 1)[-1]}" if name != "머리" else "## (표지·요약)")
        m0 = re.match(r"절:(\d{1,2})\.", name)
        if only and not (m0 and int(m0.group(1)) in only):
            tno += len(_TBL.findall(xml))
            continue
        pos = 0
        for m in _TBL.finditer(xml):
            parts += [t for t in (_txt(p) for p in _PARA.findall(xml[pos:m.start()])) if t]
            tno += 1
            rows = _rows(m.group(0))
            if rows:
                parts.append(f"[표{tno}]")
                parts += ["| " + " | ".join(r) + " |" for r in rows]
            pos = m.end()
        parts += [t for t in (_txt(p) for p in _PARA.findall(xml[pos:])) if t]
    out = "\n".join(parts)
    return out if len(out) <= FULL_MAX else out[:FULL_MAX] + "\n…(뒤는 줄임)"


_SEC_ASK = re.compile(r"(?:^|[\s「(])(\d{1,2})\.\s*[가-힣]|(\d{1,2})\s*절")


def asked_sections(request: str) -> set[int]:
    """요청이 짚은 절 번호(「3. 목표…」·「3절」). 없으면 빈 집합 — 문서 전체를 다시 읽는다."""
    return {int(x or y) for x, y in _SEC_ASK.findall(request or "")}


def review(data: bytes, request: str = "") -> str:
    """다시 읽기 자료 — 요청한 절(짚지 않았으면 전체)의 글 + 절별 모습 + 코드가 본 문제 후보. 27B 가 읽고 판단한다.
    절 하나를 쓰는 요청에서 문서 전체를 읽히면 걸음이 길어진다 — 그 절만 글까지, 나머지는 절별 모습(표 머리줄)으로."""
    only = asked_sections(request)
    outline, issues = [], []
    tno = 0
    for name, xml in _sections(data):
        label = name.split(":", 1)[-1]
        tables = _TBL.findall(xml)
        body = _TBL.sub("", xml)
        paras = [t for t in (_txt(p) for p in _PARA.findall(body)) if t]
        heads = []
        for tb in tables:
            tno += 1
            rows = _rows(tb)
            if not rows:
                continue
            heads.append(f"표{tno}[{' | '.join(c for c in rows[0] if c)[:60] or '머리줄 비었음'}]")
            data_rows = rows[1:]
            filled = [c for r in data_rows for c in r if c]
            if data_rows and not filled:
                issues.append(f"「{label}」 표{tno} 은 머리줄만 있고 칸이 모두 비었다 — 쓰지 않은 양식 표면 지우고, 쓸 표면 채운다.")
            elif data_rows:
                empty = sum(1 for r in data_rows for c in r[1:] if not c)
                if empty and empty >= max(2, len(data_rows)):
                    issues.append(f"「{label}」 표{tno} 에 빈 칸이 {empty}개 있다.")
            if len(rows) >= 2 and rows[0] and sum(1 for c in rows[0] if re.search(r"\d", c)) >= max(2, len(rows[0]) - 1):
                issues.append(f"「{label}」 표{tno} 은 첫 줄이 값으로 시작한다(머리줄 없음): 「{' | '.join(rows[0])[:50]}」.")
            ncol = max(len(r) for r in rows)
            if ncol >= 3 and len(data_rows) >= 2:
                for a in range(ncol):
                    for b in range(a + 1, ncol):
                        if all(len(r) > b and r[a] and r[a] == r[b] for r in data_rows):
                            issues.append(f"「{label}」 표{tno} 의 「{rows[0][a] if a < len(rows[0]) else a + 1}」 열과 "
                                          f"「{rows[0][b] if b < len(rows[0]) else b + 1}」 열이 모든 줄에서 같은 글이다.")
                            break
                    else:
                        continue
                    break
            raw_ = [c for r in data_rows for c in r[1:] if re.fullmatch(r"\d{5,}", c.strip())]
            if raw_ and not any(re.search(r"\d{1,3}(?:,\d{3})+", c) for r in rows for c in r):
                issues.append(f"「{label}」 표{tno} 에 쉼표 없는 큰 수가 있다(「{raw_[0]}」 등 {len(raw_)}칸) — 금액·인원 같은 값이면 「1,234,000」 처럼 자리 쉼표를 넣는다.")
            for r in data_rows:
                for c in r:
                    if _PH.search(c):
                        issues.append(f"「{label}」 표{tno} 칸에 「{_PH.search(c).group(0)}」 이 남았다: 「{c[:40]}」.")
                        break
        for p in paras:
            if _PH.search(p):
                issues.append(f"「{label}」 본문에 「{_PH.search(p).group(0)}」 이 남았다: 「{p[:50]}」.")
            h = _HAN.search(p)
            if h:
                issues.append(f"「{label}」 본문에 한자 「{h.group(0)}」 가 섞였다: 「{p[max(0, h.start() - 15):h.end() + 15]}」.")
        nums = [int(m.group(1)) for m in (_ITEM.match(p) for p in paras) if m]
        if nums and nums[0] != 1:
            issues.append(f"「{label}」 의 번호 항목이 {nums[0]}) 부터 시작한다(1) 이 없다).")
        m1 = re.match(r"절:(\d{1,2})\.", name)
        if only and not (m1 and int(m1.group(1)) in only):
            issues = [x for x in issues if not x.startswith(f"「{label}」")]
        if name != "머리":
            outline.append(f"- {label}: 문단 {len(paras)}개" + (", " + ", ".join(heads) if heads else ", 표 없음"))
        elif heads:
            outline.append("- (표지·요약): " + ", ".join(heads))
    issues = list(dict.fromkeys(issues))[:MAX_ISSUES]
    return (("마치기 전에 이번에 쓴 절을 다시 읽는다(나머지 절은 제목만):\n\n" if only else "마치기 전에 작업본을 처음부터 다시 읽는다. 아래가 지금 문서 전체다:\n\n") + full_text(data, only)
            + "\n\n절별 모습:\n" + "\n".join(outline)
            + ("\n\n코드가 본 문제 후보(참고 — 실제 문제인지는 직접 읽고 판단한다):\n" + "\n".join(f"{i}. {x}" for i, x in enumerate(issues, 1))
               if issues else "")
            + "\n\n담당자가 이 문서를 받았을 때 문제가 될 것을 찾는다 — 요청한 값이 빠지거나 다르게 들어간 곳, 다른 절에 들어간 글·표, "
              "남은 빈 표·빈칸·「확인 필요」, 어색하거나 틀린 글, 앞뒤가 맞지 않는 수치, 머리줄 없는 표. 고칠 것은 도구로 고친 뒤 finish 하고, "
              "담당자에게 값을 받아야 하는 것만 finish 글에 적는다. 문제가 없으면 그대로 finish 한다.")
