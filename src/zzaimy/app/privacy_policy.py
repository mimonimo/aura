"""Internal document masking is optional; outbound/training protection is independent."""
import json
from datetime import datetime, timezone

KEY = "internal_masking_policy_v1"
ENTITIES = {
    "KR_RRN": "주민등록번호", "KR_PHONE": "전화번호", "EMAIL": "이메일",
    "KR_NAME": "성명", "KR_BANK_ACCOUNT": "계좌번호", "KR_BRN": "사업자등록번호",
    "KR_STUDENT_ID": "학번·수험번호", "KR_BIRTHDATE": "생년월일",
}


def load(db):
    raw = db.get_setting(KEY, "")
    if not raw:
        return {"enabled": False, "entities": list(ENTITIES), "updated_at": "", "updated_by": ""}
    value = json.loads(raw)
    if (not isinstance(value, dict) or not isinstance(value.get("enabled"), bool)
            or not isinstance(value.get("entities"), list)
            or any(e not in ENTITIES for e in value["entities"])
            or (value["enabled"] and not value["entities"])):
        raise ValueError("개인정보 설정 형식 오류")
    return value


def save(db, enabled, entities, user):
    if any(e not in ENTITIES for e in entities):
        raise ValueError("지원하지 않는 마스킹 항목")
    if enabled and not entities:
        raise ValueError("마스킹을 켜려면 항목을 하나 이상 선택하세요.")
    value = {"enabled": bool(enabled), "entities": sorted(set(entities)),
             "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": user}
    db.set_setting(KEY, json.dumps(value, ensure_ascii=False))
    return value


def apply(db, document, masker_factory):
    from zzaimy.ingest.pii import MaskedDocument
    policy = load(db)
    if not policy["enabled"]:
        return MaskedDocument(doc_id=document.doc_id, text=document.text), []
    return masker_factory().mask(document, entities=policy["entities"])


def scrub_internal(db, text):
    from zzaimy.ingest.pii import PiiMasker, RawDocument
    return apply(db, RawDocument(doc_id="internal", text=text), PiiMasker)[0].text
