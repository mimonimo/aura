"""마치기 전 문서 다시 보기 — 27B 가 「완료」를 말하기 전에 작업본을 다시 읽게 하는 자료.

사람이 문서를 다 쓰고 처음부터 훑어보며 고치듯, 에이전트가 finish 를 부르면 코드가 작업본을 읽어
절마다 무엇이 들었는지(문단 수·표 머리줄)와 눈에 띄는 문제 후보를 글로 돌려준다. 고칠지·어떻게 고칠지는
27B 가 판단한다(코드는 보여 주기만 — 사례 규칙으로 고치지 않는다). 11-17 포스터 예시: 다른 절에 들어간 표,
빈 예시 표, 「확인 필요」, 한자 섞임, 같은 글이 되풀이된 열, 머리줄 없는 표, 1) 없이 시작한 2) 를 「완료」 뒤에야 찾았다.
"""
from __future__ import annotations

import json
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


def gap_cols(rows: list[list[str]]) -> list[int]:
    """빈·「확인 필요」 칸이 남은 수 열(첫 열 빼고) — 글 열(주요 활동·대표 성과)은 장부 합계로 채울 열이 아니라 뺀다."""
    data_rows = rows[1:]
    if not data_rows or not rows[0] or len(rows[0]) < 2 or not all(c.strip() for c in rows[0]):
        return []                                            # 머리줄에 빈 칸이 있으면 라벨 표(사업명 | 값) — 묶어 채울 표가 아니다

    def _wordy(j):
        xs = [r[j] for r in data_rows if len(r) > j and r[j] and "확인 필요" not in r[j]]
        return bool(xs) and sum(1 for x in xs if re.search(r"\d", x)) * 2 < len(xs)
    return [j for j in range(1, len(rows[0])) if not _wordy(j)
            and any(len(r) > j and (not r[j] or "확인 필요" in r[j]) for r in data_rows)]


def _nk(s: str) -> str:
    return re.sub(r"[\s《》「」()（）·,]", "", s or "")


def unknown_names(rows: list[list[str]], known: set[str]) -> list[str]:
    """이름 열(칸 절반 넘게가 모은 기록 이름·문서 제목과 맞는 글 열)에서 홀로 모르는 이름 — 지어낸 행 후보.
    11-23 18차 단계별: 빈 달을 채우려 「교과목 개발 중간 점검」「AI 실무 역량 진단」 같은 없는 활동을 넣고 근거 문서는 「확인 필요」."""
    ks = {_nk(k) for k in known if len(_nk(k)) >= 4}
    if not ks or len(rows) < 3:
        return []

    def hit(c: str) -> bool:
        n = _nk(c)
        return any(k in n or n in k for k in ks) if len(n) >= 4 else True
    out = []
    for j in range(len(rows[0])):
        cells = [r[j] for r in rows[1:] if len(r) > j and r[j] and "확인 필요" not in r[j] and not re.fullmatch(r"[\d,.\s%명건개곳원-]+", r[j])]
        if len(cells) < 3:
            continue
        good = [c for c in cells if hit(c)]
        if len(good) * 2 > len(cells):
            out += [c for c in cells if not hit(c)]
    return list(dict.fromkeys(out))


def gap_tables(data: bytes) -> list[tuple[int, list[str], list[str], str]]:
    """작업본에서 빈 수 칸이 남은 표 — (표 번호, 행 이름들, 빈 열 머리들, 첫 열 머리)."""
    out, tno = [], 0
    for _, xml in _sections(data):
        for tb in _TBL.findall(xml):
            tno += 1
            rows = _rows(tb)
            gap = gap_cols(rows) if rows else []
            if gap:
                out.append((tno, [r[0] for r in rows[1:] if r], [rows[0][j] for j in gap], rows[0][0]))
    return out


def mixed_unit_cells(rows: list[list[str]]) -> tuple[str, list[str]] | None:
    """열 머리가 셈 단위를 밝힌 열(「참여 인원(명)」)에 「학생 33」 처럼 글이 섞인 칸 — (머리, 칸들). 24차 A 짧게에서 15칸."""
    if len(rows) < 2:
        return None
    for j, h in enumerate(rows[0]):
        if j == 0 or not re.search(r"[(（]\s*(명|건|개|곳|회|원|천원|%)\s*[)）]", h):
            continue
        mixed = [r[j] for r in rows[1:] if j < len(r) and re.fullmatch(r"\s*[가-힣]{1,8}(?:\s[가-힣]{1,6})?\s+[\d,]+(?:\.\d+)?\s*[가-힣%]{0,2}\s*", r[j])]
        if mixed:
            return h, mixed
    return None


def nameless_rows(rows: list[list[str]]) -> list[str]:
    """첫 칸(행 이름)이 비었는데 다른 칸에 값이 든 행 — 27차 B 짧게: 작년 작업본을 비울 때 행 이름 칸(「강사료」「운영비」)까지 비우고
    값만 채워 세목별 표가 「|  | 11,400 | 22.1 |」 로 끝났다. 표 머리가 둘 이상 열이고 다른 행에는 이름이 있을 때만."""
    if len(rows) < 3 or len(rows[0]) < 2:
        return []
    body = rows[1:]
    named = [r for r in body if r and r[0].strip()]
    bad = [" | ".join(c.strip() for c in r[1:] if c.strip())[:30] for r in body
           if r and not r[0].strip() and any(c.strip() and c.strip() != "확인 필요" for c in r[1:])]
    return bad if bad and (named or len(bad) >= 2) else []


def twin_columns(rows: list[list[str]]) -> tuple[str, str, int] | None:
    """두 수 열에 같은 수가 나란히 든 행이 여럿 — 한 값을 두 열에 넣은 꼴. 28차 A 짧게: 실적을 hwp_fill 로 넣자 「목표」 열에 들어가고,
    set_cells 로 실적 열을 채운 뒤에도 목표 칸에 실적이 남아 「264 | 264 | 확인 필요」 로 끝났다. 목표=실적인 행이 하나뿐이면 짚지 않는다."""
    if len(rows) < 3 or len(rows[0]) < 3:
        return None
    head = rows[0]
    best = None
    for i in range(1, len(head) - 1):
        for j in range(i + 1, len(head)):
            n = sum(1 for r in rows[1:] if len(r) > j and re.fullmatch(r"[\d,.]+", r[i].strip() or "x") and r[i].strip() == r[j].strip())
            if n >= 2 and (not best or n > best[2]):
                best = (head[i].strip(), head[j].strip(), n)
    return best


def scattered_counts(rows: list[list[str]], texts: list[tuple[str, str]], where: list | None = None) -> list[str]:
    """행 이름(지표 이름) 바로 뒤에 셈 단위 수가 적힌 근거 문서가 둘 이상인데, 행에 든 수가 그 가운데 문서 하나의 수와 같고 합과는 다르면 짚는다.
    29차 A 짧게: 성과지표를 모아 묶지 않고 서브가 문서 하나의 수(취업 연계 17명·협약 6건)를 실적으로 씀(정답 31·9 는 문서 여러 개의 합).
    texts: (문서 제목, 글). 합인지 한 문서 값인지는 27B 가 판단한다.
    where: 주면 짚은 칸마다 (행 번호, 열 번호, 문서 합) 을 1부터 센 표 자리로 붙인다 — 바로 부를 set_cells 인자를 만들 때 쓴다."""
    out = []
    head = rows[0] if rows else []
    # 목표·계획·비율 열은 문서의 실적 합과 견주지 않는다(목표 300 은 실적 합이 아니다)
    skip_cols = {j for j, h in enumerate(head) if re.search(r"목표|계획|기준|비율|률|%", str(h))}
    for ri, r in enumerate(rows[1:], 2):
        if not r or not r[0].strip():
            continue
        core = re.sub(r"\s*[(（][^)）]*[)）]\s*$", "", r[0]).strip()
        if len(core) < 2:
            continue
        pat = re.compile(re.escape(core) + r"[^\d\n]{0,12}?(\d[\d,]*)\s*(명|건|곳|회|개|개사|과목|점)")
        per = []
        for t, x in texts:
            x = x or ""
            for m in pat.finditer(x):
                ln = x[x.rfind("\n", 0, m.start()) + 1: (x.find("\n", m.end()) + 1 or len(x) + 1) - 1]
                if re.search(r"목표|계획\s*치", ln):         # 작성 안내의 「목표치: 기관 연계 8곳」 은 실적이 아니다
                    continue
                per.append((t, int(m.group(1).replace(",", "")), m.group(2)))
                break
        if len(per) < 2:
            continue
        total = sum(v for _, v, _ in per)
        for j, c in enumerate(r[1:], 1):
            c = c.strip()
            if j in skip_cols or not re.fullmatch(r"[\d,]+\s*[가-힣]{0,2}", c):
                continue
            v = int(re.sub(r"\D", "", c) or 0)
            # 한 문서의 수만 옮겼거나(29차 A 짧게: 취업 연계 17), 어느 문서와도 합과도 맞지 않는 수(31차 A 한번에: 참여 인원을 더한 52)
            if v != total:
                ex = ", ".join(f"《{t[:16]}》 {pv}{u}" for t, pv, u in per[:4])
                one = " — 그 가운데 문서 하나의 수다" if any(v == pv for _, pv, _ in per) else " — 어느 문서의 수와도, 합과도 다르다"
                out.append(f"「{r[0][:16]}」 행의 {c}{one}. 이 이름 뒤에 수가 적힌 문서가 {len(per)}개다({ex}{' …' if len(per) > 4 else ''}, 모두 더하면 {total})")
                if where is not None:
                    where.append((ri, j + 1, total))
                break
    return out


def review(data: bytes, request: str = "", ledger=None, known: set[str] | None = None, texts: list[tuple[str, str]] | None = None) -> str:
    """다시 읽기 자료 — 요청한 절(짚지 않았으면 전체)의 글 + 절별 모습 + 코드가 본 문제 후보. 27B 가 읽고 판단한다.
    절 하나를 쓰는 요청에서 문서 전체를 읽히면 걸음이 길어진다 — 그 절만 글까지, 나머지는 절별 모습(표 머리줄)으로.
    known: 모은 기록의 이름 값·근거 문서 제목(띄어쓰기 뺀 글) — 대부분 칸이 그 이름인 열에서 홀로 모르는 이름(지어낸 활동)을 짚는다.
    ledger(행 이름들, 열 머리들, 첫 열 머리) → 글: 빈·「확인 필요」 칸이 남은 표를 실적 장부로 묶어 본 결과(fact_ledger.for_table) — 후보에 붙인다."""
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
            # 첫 열 이름이 여러 행에 되풀이 — 담당자가 이름마다 한 행(월별 합계 등)을 바랐는지는 27B 가 요청을 보고 판단한다(11-23 16차:
            # 앞 요청에서 활동 행으로 채운 표를 「월마다 한 행으로 합계」 요청에서 요약 글만 붙이고 그대로 두었다)
            firsts = [r[0].strip() for r in data_rows if r and r[0].strip()]
            dup = {k: firsts.count(k) for k in dict.fromkeys(firsts) if firsts.count(k) >= 2}
            if len(dup) >= 2:
                issues.append(f"「{label}」 표{tno} 첫 열에 같은 이름이 여러 행이다(" + ", ".join(f"{k} {n}행" for k, n in list(dup.items())[:5])
                              + ") — 담당자 요청이 이름마다 한 행(합계)이면 aggregate_facts 묶음대로 한 행씩 합쳐 고치고, 활동마다 한 행이 맞으면 그대로 둔다.")
            # 빈·「확인 필요」 칸이 남은 열 — 장부로 묶어 채울 수 있는지 보인다(11-23 17차: 성과지표 실적 칸을 빈 채로 두고
            # finish, 다음 요청에서는 「문서에 없다」 며 물었다 — 결과 보고마다 지표 실적이 적혀 있었다)
            if ledger and data_rows and len(rows[0]) >= 2:
                gap = gap_cols(rows)
                if gap:
                    try:
                        more = ledger([r[0] for r in data_rows if r], [rows[0][j] for j in gap], rows[0][0])
                    except Exception:
                        more = ""
                    if more:
                        issues.append(f"「{label}」 표{tno} 의 {', '.join('「' + rows[0][j] + '」' for j in gap[:3])} 열에 빈·「확인 필요」 칸이 있다 —" + more)
            # 한 행의 서로 다른 수 열에 같은 수(목표 10 · 실적 10) — 한쪽 값을 다른 열에 옮겨 적었을 수 있다(11-23 18차 단계별: 협약 목표 10건을
            # 실적에도 적어 달성률 100%, 문서의 실적은 9건). 우연히 같을 수도 있어 후보로만
            same = []
            for r in data_rows:
                nums_ = [(j, c.strip()) for j, c in enumerate(r) if j > 0 and re.fullmatch(r"[\d,]+(?:\.\d+)?\s*[가-힣]{0,2}", c.strip() or "x")]
                for a_ in range(len(nums_)):
                    for b_ in range(a_ + 1, len(nums_)):
                        if re.sub(r"\D", "", nums_[a_][1]) == re.sub(r"\D", "", nums_[b_][1]) and rows[0][nums_[a_][0]:nums_[a_][0] + 1] != rows[0][nums_[b_][0]:nums_[b_][0] + 1]:
                            same.append(f"{r[0][:14]}: {rows[0][nums_[a_][0]] if nums_[a_][0] < len(rows[0]) else ''}={nums_[a_][1]}·{rows[0][nums_[b_][0]] if nums_[b_][0] < len(rows[0]) else ''}={nums_[b_][1]}")
            if same and len(same) < len(data_rows):
                issues.append(f"「{label}」 표{tno} 한 행의 다른 열에 같은 수가 있다({'; '.join(same[:3])}) — 한쪽 값(목표 등)을 다른 열에 옮겨 적었는지 근거와 대 본다.")
            if known and data_rows:
                odd = unknown_names(rows, known)
                if odd:
                    issues.append(f"「{label}」 표{tno} 에 모은 기록·근거 문서에 없는 이름이 있다: " + ", ".join(f"「{x[:24]}」" for x in odd[:5])
                                  + " — 문서에 있는 활동이면 그 이름으로 고치고, 지어낸 행이면 지운다(빈 달을 채우려 만든 행은 두지 않는다).")
            # 열 머리가 셈 단위를 밝힌 열(「참여 인원(명)」)인데 칸에 사람 갈래 글이 섞였다 — 24차 A 짧게: 「학생 33」「담당 교원 2」
            mixed_ = mixed_unit_cells(rows)
            if mixed_:
                h_, cells_ = mixed_
                issues.append(f"「{label}」 표{tno} 의 「{h_[:16]}」 열은 수만 적는 열인데 글이 섞인 칸이 있다(「{cells_[0]}」 등 {len(cells_)}칸) — "
                              "수만 남기고, 「학생」「교원」 같은 구분이 필요하면 내용 열이나 본문에 적는다.")
            nl_ = nameless_rows(rows)
            if nl_:
                issues.append(f"「{label}」 표{tno} 에 행 이름(첫 칸)이 빈 행이 {len(nl_)}개다(「{nl_[0]}」 등) — 무엇의 값인지 알 수 없다. "
                              "근거의 이름(세목·과제·지표 이름)을 첫 칸에 넣는다(hwp_table_edit set_cells start: [행, 1]).")
            tw_ = twin_columns(rows)
            if tw_:
                issues.append(f"「{label}」 표{tno} 의 「{tw_[0][:12]}」 열과 「{tw_[1][:12]}」 열에 같은 수가 든 행이 {tw_[2]}개다 — "
                              "한 값을 두 열에 넣었는지 본다. 그 열 값이 근거에 없으면 「확인 필요」 로 바꾼다(hwp_table_edit set_cells).")
            at_: list = []
            sc_ = scattered_counts(rows, texts, at_) if texts else []
            if sc_:
                # 문서 합으로 바꿀 바로 부를 인자를 붙인다 — 34차 B 한번에: 후보를 두 번 보고도 목표 8 을 달성치에 둔 채 마쳤다(문서 합 6)
                fix_ = "; ".join(json.dumps({"table": tno, "op": "set_cells", "start": [r_, c_], "values": [[str(t_)]]}, ensure_ascii=False)
                                 for r_, c_, t_ in at_[:3])
                issues.append(f"「{label}」 표{tno}: " + "; ".join(sc_[:3]) + " — 문서 하나의 수만 옮긴 것인지, 목표 같은 다른 값을 옮긴 것인지 본다. "
                              "여러 문서의 합이 실적이면 그 칸을 문서 합으로 바꾼다: hwp_table_edit " + fix_
                              + " (비율 열이 있으면 함께 다시 셈). 합이 아니라고 보면 collect_facts {items: [지표 이름 항목, 실적 항목]} 로 모은 뒤 "
                              "aggregate_facts 로 확인한다.")
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
            + "\n\n담당자가 이 문서를 받았을 때 문제가 될 것을 찾는다 — 요청한 값이 빠지거나 다르게 들어간 곳, 요청한 꼴(한 행에 무엇을 적을지)과 다른 표, 다른 절에 들어간 글·표, "
              "남은 빈 표·빈칸·「확인 필요」, 어색하거나 틀린 글, 앞뒤가 맞지 않는 수치, 머리줄 없는 표. 고칠 것은 도구로 고친 뒤 finish 하고, "
              "담당자에게 값을 받아야 하는 것만 finish 글에 적는다. 문제가 없으면 그대로 finish 한다.")
