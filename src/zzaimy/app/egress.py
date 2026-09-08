"""외부 참조 이그레스 게이트웨이 — 내부 정보 유출 차단 (ADR-0008).

외부 Claude API로 나가는 모든 질의는 이 모듈을 통과한다. 세척(scrub)으로
개인정보·기관 식별자를 제거하고, 분류(classify)로 안전/승인대기/차단을 정한다.
확신하지 못하는 질의는 절대 safe가 아니다(fail-closed).

세척은 100%를 보장하지 못한다. 그래서 잔여 위험이 있으면 사람 승인(review)
또는 차단(blocked)으로 넘겨 잔여 판단을 사람이 한다.
"""

from __future__ import annotations

import json as _json
import os as _os
import re
from dataclasses import dataclass, field
from datetime import datetime as _dt
from datetime import timezone as _tz
from enum import Enum


class Verdict(str, Enum):
    SAFE = "safe"        # 자동 전송 가능
    REVIEW = "review"    # 사람 승인 대기
    BLOCKED = "blocked"  # 자동 차단


# 기관 식별자 데니리스트 — 이 문자열들은 절대 외부로 나가지 않는다.
# (보호 대상 목록. 인식 성능 하드코딩과 무관 — 보안 데니리스트는 정공법.)
_INSTITUTION_TERMS: list[tuple[str, str]] = [
    ("영남이공대학교", "한 전문대학"),
    ("영남이공대", "한 전문대학"),
    ("Yeungnam University College", "a college"),
    ("YNC", "the college"),
    ("ync", "the college"),
    ("짜이미", "이 시스템"),
    ("ZZAIMY", "this system"),
    ("zzaimy", "this system"),
]

# 고위험 PII 정규식 — Presidio가 놓칠 수 있는 것을 한 겹 더 확실히 막는다.
# (주민번호·이메일·전화·계좌/카드 유형. 예산 수치와 구분되는 특정 형태만.)
_HIGH_RISK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("RRN", re.compile(r"\b\d{6}[-–]\d{7}\b")),                    # 주민등록번호
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("PHONE", re.compile(r"\b01[016-9][-\s]?\d{3,4}[-\s]?\d{4}\b")),
    ("CARD", re.compile(r"\b\d{4}[-\s]\d{4}[-\s]\d{4}[-\s]\d{4}\b")),
]

# 인명 — 이름표 문맥에서만 보수적으로(과잉 마스킹 방지). Presidio 한국어
# 인명 인식이 약해 보강한다.
_NAME_LABEL = re.compile(
    r"(담당자|성명|이름|신청인|작성자|대표자|책임자|연구자|문의처?)"
    r"(\s*[:：]?\s*)([가-힣]{2,4})"
)
_NAME_HONORIFIC = re.compile(r"([가-힣]{2,4})(\s*)(씨|님|과장|팀장|부장|교수|선생님?)\b")

# 세척 후에도 이런 잔여가 남으면 자동 전송하지 않는다.
# - 한글 고유명사류: OO대학교/고등학교/중학교/재단/센터/공사/재활원 등 기관 접미사
_RESIDUAL_ORG = re.compile(
    r"[가-힣A-Za-z0-9]{2,}(대학교|대학|고등학교|중학교|초등학교|재단|공사|"
    r"센터|연구원|병원|재활원|복지관|주식회사)"
)
# - 마스킹 토큰이 남아있으면(=PII를 지웠다는 뜻) 최소 review
_MASK_TOKEN = re.compile(r"\[[A-Z_]+\]")


@dataclass(frozen=True)
class ScrubResult:
    """세척 결과 — 실제로 외부에 나갈 문구와 무엇을 지웠는지."""

    text: str                       # 세척된(외부로 나갈) 텍스트
    original: str                   # 원문 (감사 기록용, 외부로 나가지 않음)
    removed: list[str] = field(default_factory=list)  # 제거·치환된 항목 유형


_masker = None


def _get_masker():
    global _masker
    if _masker is None:
        from zzaimy.ingest.pii import PiiMasker, RawDocument

        _masker = (PiiMasker(), RawDocument)
    return _masker


def scrub(text: str) -> ScrubResult:
    """텍스트에서 개인정보·기관 식별자를 제거한다.

    1) PII 마스킹(Presidio 한국어) → 인명·전화·주민번호 등을 [토큰]으로.
    2) 기관 식별자 데니리스트 → 일반화 치환.
    """
    removed: list[str] = []
    working = text

    # 1) PII 마스킹
    try:
        masker, RawDocument = _get_masker()
        masked, events = masker.mask(RawDocument(doc_id="egress", text=working))
        working = masked.text
        removed.extend(sorted({e.entity_type for e in events}))
    except Exception:
        # 마스커 로드 실패 시에도 기관 치환은 수행하고, 분류에서 fail-closed.
        removed.append("PII_MASKER_UNAVAILABLE")

    # 1b) 고위험 정규식 스윕 — 마스커가 놓친 것을 확실히 제거
    for label, pat in _HIGH_RISK_PATTERNS:
        if pat.search(working):
            working = pat.sub(f"[{label}]", working)
            removed.append(label)

    # 1c) 이름표·경칭 문맥의 인명 (라벨·경칭은 보존, 이름만 치환)
    if _NAME_LABEL.search(working):
        working = _NAME_LABEL.sub(r"\1\2[PERSON]", working)
        removed.append("PERSON")
    if _NAME_HONORIFIC.search(working):
        working = _NAME_HONORIFIC.sub(r"[PERSON]\2\3", working)
        if "PERSON" not in removed:
            removed.append("PERSON")

    # 2) 기관 식별자 데니리스트 (대소문자 구분 — 약어 오탐 방지)
    for term, repl in _INSTITUTION_TERMS:
        if term in working:
            working = working.replace(term, repl)
            removed.append(f"ORG:{term}")

    return ScrubResult(text=working, original=text, removed=removed)


def classify(result: ScrubResult) -> Verdict:
    """세척 결과의 잔여 위험을 판정한다 (fail-closed).

    - 원문에 기관 데니리스트 용어가 있었으면(제거했더라도) 자동 전송 금지.
    - 세척 후에도 기관 접미사 고유명사가 남으면 자동 전송 금지.
    - 마스커를 못 써서 세척을 보증 못 하면 차단.
    - 그 외(깨끗) → safe.
    """
    if "PII_MASKER_UNAVAILABLE" in result.removed:
        return Verdict.BLOCKED

    had_org = any(r.startswith("ORG:") for r in result.removed)
    residual_org = bool(_RESIDUAL_ORG.search(result.text))
    had_pii = any(not r.startswith("ORG:") for r in result.removed)

    if residual_org:
        # 세척이 못 잡은 미상 기관 고유명사 — 사람이 봐야 한다.
        return Verdict.REVIEW
    if had_org:
        # 내부 기관을 일반화했지만, 문맥으로 식별 가능할 수 있어 사람 확인.
        return Verdict.REVIEW
    if had_pii:
        # 개인정보를 지웠다 — 잔여 문맥 확인 위해 최소 review.
        return Verdict.REVIEW
    return Verdict.SAFE


# ---------------------------------------------------------------------------
# 게이트웨이 오케스트레이션 — 제출·기록·승인·전송 (ADR-0008 3·4단계)
#
# 상태 흐름:
#   blocked                          차단 (종결)
#   queued  → denied                 승인 대기 → 거부 (종결)
#   queued  → approved → answered    승인 → 전송 성공
#   safe    → held     → answered    자동 허용 → (전송 가능해지면) 전송 성공
#   …      → failed                  전송 시도 중 오류 (재전송 가능)
#
# held/approved는 "나가도 된다고 판정됐지만 아직 안 나간" 상태다. 외부 전송이
# 비활성(아웃바운드 차단·키 없음)이어도 판정·감사 흐름은 그대로 동작한다.
# ---------------------------------------------------------------------------

# 전송 가능 상태 — 이 상태의 건만 실제 외부 호출을 시도한다.
_SENDABLE = {"held", "approved", "failed"}

_EXTERNAL_SYSTEM = (
    "너는 한국 정부 국고보조사업·대학 행정의 일반 지식을 답하는 참고 조수다. "
    "일반적인 절차·규정 상식·문서 작성 관행만 답하고, 질문자의 소속 기관이나 "
    "개인을 특정하는 정보를 되묻지 않는다. 확실하지 않은 내용은 모른다고 답한다."
)


def _now_iso() -> str:
    return _dt.now(_tz.utc).astimezone().isoformat(timespec="seconds")


def external_status() -> tuple[bool, str]:
    """외부 전송 가능 여부와 사유. 플래그·키·SDK 세 가지가 모두 필요하다."""
    if _os.environ.get("ZZAIMY_EXTERNAL_ENABLED") != "1":
        return False, "ZZAIMY_EXTERNAL_ENABLED 미설정"
    if not (_os.environ.get("ZZAIMY_ANTHROPIC_KEY") or _os.environ.get("ANTHROPIC_API_KEY")):
        return False, "API 키 미설정"
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "anthropic 패키지 미설치"
    return True, ""


def _send_external(text: str) -> str:
    """세척 완료 텍스트를 Claude API로 전송한다 — 게이트웨이 밖에서 호출 금지."""
    import anthropic

    client = anthropic.Anthropic(
        api_key=_os.environ.get("ZZAIMY_ANTHROPIC_KEY")
        or _os.environ.get("ANTHROPIC_API_KEY")
    )
    response = client.messages.create(
        model=_os.environ.get("ZZAIMY_EXTERNAL_MODEL", "claude-opus-5"),
        max_tokens=int(_os.environ.get("ZZAIMY_EXTERNAL_MAX_TOKENS", "4096")),
        system=_EXTERNAL_SYSTEM,
        messages=[{"role": "user", "content": text}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("외부 모델이 안전상 이유로 응답을 거부함")
    return "".join(b.text for b in response.content if b.type == "text")


def submit(db, text: str, requester: str, source: str = "manual") -> dict:
    """외부 참조 질의 제출 — 세척·분류·기록까지 무조건, 전송은 safe일 때만 시도."""
    result = scrub(text)
    verdict = classify(result)
    status = {
        Verdict.BLOCKED: "blocked",
        Verdict.REVIEW: "queued",
        Verdict.SAFE: "held",
    }[verdict]
    req_id = db.add_egress_request(
        requester=requester,
        source=source,
        original=result.original,
        scrubbed=result.text,
        removed=_json.dumps(result.removed, ensure_ascii=False),
        verdict=verdict.value,
        status=status,
    )
    if status == "held":
        _try_send(db, req_id)
    return db.get_egress_request(req_id)


def decide(db, req_id: int, approve: bool, decided_by: str) -> dict:
    """승인 대기 건의 사람 판정. 승인이면 전송을 시도한다."""
    row = db.get_egress_request(req_id)
    if row is None:
        raise ValueError(f"없는 요청: {req_id}")
    if row["status"] != "queued":
        raise ValueError(f"승인 대기 상태가 아님: {row['status']}")
    db.update_egress_request(
        req_id,
        status="approved" if approve else "denied",
        decided_by=decided_by,
        decided_at=_now_iso(),
    )
    if approve:
        _try_send(db, req_id)
    return db.get_egress_request(req_id)


def retry_send(db, req_id: int) -> dict:
    """전송 대기·실패 건 재전송 — 아웃바운드가 열린 뒤 수동으로 민다."""
    row = db.get_egress_request(req_id)
    if row is None:
        raise ValueError(f"없는 요청: {req_id}")
    if row["status"] not in _SENDABLE:
        raise ValueError(f"전송 가능한 상태가 아님: {row['status']}")
    _try_send(db, req_id)
    return db.get_egress_request(req_id)


def _try_send(db, req_id: int) -> None:
    """전송 시도 — 비활성이면 상태를 그대로 두고, 오류는 기록한다."""
    enabled, reason = external_status()
    if not enabled:
        db.update_egress_request(req_id, error=f"전송 보류: {reason}")
        return
    row = db.get_egress_request(req_id)
    try:
        answer = _send_external(row["scrubbed"])
    except Exception as exc:  # 네트워크·API 오류 — 감사 기록에 남기고 재시도 가능
        db.update_egress_request(req_id, status="failed", error=str(exc)[:500])
        return
    db.update_egress_request(
        req_id, status="answered", response=answer, sent_at=_now_iso(), error=None
    )
