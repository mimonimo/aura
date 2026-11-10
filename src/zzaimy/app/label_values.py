"""라벨 칸 다시 채우기 — 요약 칸(행사명·기간·장소…)을 근거를 다 읽기 전에 채우면 「확인 필요」로 남는데, 뒤에 맡겨 쓴 절의
근거·본문에는 그 값이 「장소 | 경주드림센터」「다) 장소: 경주드림센터」 꼴로 나온다(11-10 대화 177). 근거 글에서 「라벨 → 값」 짝을
뽑아, 「확인 필요」로 남은 칸 가운데 같은 라벨의 값이 하나뿐인 칸만 그 값으로 채운다. 값이 둘 이상 다르면 고르지 않는다 —
판단이 필요한 것은 에이전트 몫이다. 양식으로 쓴 지난 문서(작년 결과보고서)의 값은 이 해의 값이 아니라 근거에서 뺀다."""
from __future__ import annotations

import re
import unicodedata

PENDING = re.compile(r"^\s*[\[\(【]?\s*확인\s*필요\s*[\]\)】]?\s*$")
_MARK = re.compile(r"^\s*(?:[-*•◦○□■▪·∙ㆍ]+|\d+[.)]|[가-힣][.)]|\(\d+\)|\([가-힣]\)|[①-⑳])\s*")
_PAREN = re.compile(r"^[(（]\s*([^()（）]{1,12}?)\s*[)）]\s*(.+)$")
_COLON = re.compile(r"^([^:：|]{1,12}?)\s*[:：]\s*(.+)$")
_SRC = re.compile(r"\s*[(（]\s*출처\s*[:：]\s*([^()（）]*)[)）]")
_HEAD = re.compile(r"^\s*(?:\[근거 \d+\]\s*)?《([^》]*)》")
MAX_LABEL, MAX_VALUE = 10, 80


def norm(s: str) -> str:
    """라벨 견주기 꼴 — 호환 글자를 풀고 띄어쓰기·괄호·꾸밈 글자를 뺀다(「장 소」「(장소)」「**장소**」 가 같은 라벨)."""
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"[\s*\[\]()（）【】「」『』<>:：]", "", s)


def label_of(field: str) -> str:
    return norm(field.split(":", 1)[1] if ":" in field else field)


def _value(v: str, skip_src) -> str:
    """짝의 값을 다듬는다 — 출처 꼬리는 떼고, 양식 문서에서 온 값(출처가 양식 문서)이면 빈 글."""
    v = v.replace("**", "")
    for m in _SRC.finditer(v):
        if skip_src(m.group(1)):
            return ""
    v = _SRC.sub("", v).strip().strip("|").strip()
    if not v or len(v) > MAX_VALUE or "확인" in v or re.search(r"○○|000|\{|\}", v):
        return ""
    return v


def _label_ok(lab: str) -> bool:
    return 1 <= len(lab) <= MAX_LABEL and not re.search(r"\d", lab)


def pairs(text: str, skip_src=lambda _t: False) -> list[tuple[str, str]]:
    """글에서 「라벨 → 값」 짝 — 표 줄(라벨 | 값 | 라벨 | 값), 괄호 라벨(「(장 소) 부산」), 쌍점 라벨(「다) 장소: 경주」).
    skip_src(출처 이름) 가 참인 출처 꼬리가 달린 값은 뺀다."""
    out: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        line = line.replace("**", "").strip()
        if not line or line.startswith("{"):
            continue
        if "|" in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            for i in range(0, len(cells) - 1, 2):
                lab, v = norm(cells[i]), _value(cells[i + 1], skip_src)
                if _label_ok(lab) and v:
                    out.append((lab, v))
            continue
        body = _MARK.sub("", line)
        m = _PAREN.match(body) or _COLON.match(body)
        if not m:
            continue
        lab, v = norm(m.group(1)), _value(m.group(2), skip_src)
        if _label_ok(lab) and v:
            out.append((lab, v))
    return out


def known(texts: list[str], skip_title=lambda _t: False) -> dict[str, set[str]]:
    """여러 근거 글의 짝 모음 — 라벨마다 서로 다른 값들. 머리 《제목》 이 양식 문서인 조각은 통째로 뺀다."""
    got: dict[str, set[str]] = {}
    for t in texts or []:
        m = _HEAD.match(t or "")
        if m and skip_title(m.group(1)):
            continue
        for lab, v in pairs(t, skip_title):
            got.setdefault(lab, set()).add(v)
    return got


def _key(v: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", v))


def refill(fields: list[dict], texts: list[str], skip_title=lambda _t: False) -> dict[str, str]:
    """「확인 필요」 로 남은 칸 필드 → 근거에서 값이 하나뿐인 같은 라벨의 값. 칸 안 여러 줄(「장소: 확인 필요」)은 그 줄만 바꾼다."""
    got = known(texts, skip_title)

    def one(lab: str) -> str:
        vals = got.get(lab) or set()
        uniq = {_key(v): v for v in vals}
        return next(iter(uniq.values())) if len(uniq) == 1 else ""

    out: dict[str, str] = {}
    for f in fields or []:
        if f.get("kind") != "칸":
            continue
        text = str(f.get("text") or "")
        if PENDING.match(text):
            v = one(label_of(f["name"]))
            if v:
                out[f["name"]] = v
            continue
        lines, changed = text.split("\n"), False
        for i, ln in enumerate(lines):
            m = _COLON.match(_MARK.sub("", ln.strip()))
            if m and PENDING.match(m.group(2)):
                v = one(norm(m.group(1)))
                if v:
                    lines[i] = ln[:ln.index(m.group(2), ln.index(m.group(1)))] + v
                    changed = True
        if changed:
            out[f["name"]] = "\n".join(lines)
    return out
