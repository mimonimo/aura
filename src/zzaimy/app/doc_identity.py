"""문서의 정체 파악 — 이 문서가 어떤 사업에 관한 것인지 본문에서 인출한다.

왜 필요한가. 문서를 잇는 근거가 "같은 낱말이 나온다"가 되면 의미가 없다.
"학생"이나 "지원"은 거의 모든 문서에 나오므로 연결 신호가 되지 못한다.
문서가 무엇에 관한 것인지를 먼저 정하고, 그 정체끼리 이어야 연관이 성립한다.

절대규칙 1을 코드로 지킨다 — 모든 값은 본문에 실제로 있어야 한다.
모델이 지어낸 값은 대조에서 걸러 버린다. 걸러 낸 이유도 함께 남긴다.
"""
from __future__ import annotations

import json
import re
import unicodedata

FIELDS: dict[str, str] = {
    "program": "사업 또는 공고의 이름",
    "organizer": "공고하거나 시행하는 기관·부서",
    "year": "연도 또는 회차",
    "target": "지원 대상 또는 신청 자격",
    "scale": "지원 규모나 금액",
    "period": "신청 기간",
    "law": "근거가 되는 법령·지침·규정",
}

PROMPT = (
    "다음은 문서 본문이다. 이 문서가 무엇에 관한 것인지 아래 항목을 본문에서 "
    "그대로 찾아 JSON 한 덩어리로만 답하라.\n"
    "본문에 없는 항목은 빈 문자열로 둔다. 추측하거나 바꿔 쓰지 않는다.\n"
    "찾은 값은 본문에 적힌 표현을 그대로 옮긴다.\n\n"
    + "\n".join(f"- {k}: {desc}" for k, desc in FIELDS.items())
    + "\n\n본문:\n{body}\n"
)

# 본문 대조에서 무시할 것 — 공백·괄호·가운뎃점 같은 표기 차이는 같은 값으로 본다
_LOOSE = re.compile(r"[\s·・,()\[\]{}「」『』\"'‘’“”]+")
_DIGITS = re.compile(r"\d")


def _normalize(text: str) -> str:
    return _LOOSE.sub("", unicodedata.normalize("NFKC", text)).lower()


def parse_json(raw: str) -> dict:
    """모델이 돌려준 글에서 JSON 한 덩어리만 꺼낸다. 못 꺼내면 빈 dict."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        got = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def verify(found: dict, source: str) -> tuple[dict, list[str]]:
    """본문에 실제로 있는 값만 남긴다.

    수치가 들어간 값은 특히 엄격하게 본다 — 근거 없는 수치는 허위 기재가 되기 때문이다.
    돌려주는 것은 (남은 값, 걸러 낸 이유 목록)이다.
    """
    body = _normalize(source)
    kept: dict[str, str] = {}
    dropped: list[str] = []
    for key in FIELDS:
        value = found.get(key)
        if not isinstance(value, str):
            continue
        value = value.strip()
        if not value:
            continue
        if _normalize(value) in body:
            kept[key] = value
            continue
        # 표현이 조금 달라도 수치가 모두 본문에 있으면 살린다. 하나라도 없으면 버린다.
        digits = _DIGITS.findall(value)
        if digits and all(d in source for d in set(digits)):
            core = _normalize(re.sub(r"\d+", "", value))
            if core and core in body:
                kept[key] = value
                continue
        dropped.append(f"{key}: 본문에서 확인되지 않아 버렸습니다 ({value[:40]})")
    return kept, dropped


def extract(text: str, call, max_chars: int = 6000) -> dict:
    """본문에서 문서의 정체를 인출한다.

    call 은 프롬프트 한 덩어리를 받아 모델의 답글을 돌려주는 함수다. 주입해서 쓰므로
    이 모듈은 어느 모델을 쓰는지 알지 못한다. 실패하면 빈 값과 사유를 돌려준다.
    """
    body = (text or "").strip()
    if not body:
        return {"ok": False, "identity": {}, "dropped": [], "error": "본문이 없습니다"}
    head = body[:max_chars]
    try:
        raw = call(PROMPT.format(body=head))
    except Exception as e:                      # 서버 오류·시간 초과 등
        return {"ok": False, "identity": {}, "dropped": [],
                "error": f"모델 호출에 실패했습니다 ({type(e).__name__})"}
    found = parse_json(raw)
    if not found:
        return {"ok": False, "identity": {}, "dropped": [],
                "error": "모델 답글에서 JSON 을 찾지 못했습니다"}
    kept, dropped = verify(found, head)
    return {"ok": bool(kept), "identity": kept, "dropped": dropped,
            "error": "" if kept else "본문에서 확인된 값이 없습니다"}


def signature(identity: dict) -> str:
    """문서를 잇는 열쇠 — 사업 이름을 표기 차이 없이 맞춘 것. 없으면 빈 문자열."""
    return _normalize(identity.get("program", ""))
