"""Training protection is independent of internal document preferences.

Automatic detection is limited; reviewer privacy approval remains mandatory.
"""
from functools import lru_cache
import json


@lru_cache(maxsize=1)
def _masker():
    from zzaimy.ingest.pii import PiiMasker
    return PiiMasker()


def protect_candidate(value):
    """Scrub every textual field, including provenance. Never change the source document."""
    from zzaimy.ingest.pii import RawDocument
    if isinstance(value, str):
        return _masker().mask(RawDocument(doc_id="training-copy", text=value))[0].text
    if isinstance(value, list):
        return [protect_candidate(v) for v in value]
    if isinstance(value, dict):
        return {k: protect_candidate(v) for k, v in value.items()}
    return value


def approved_bytes(path):
    """Read once, audit the exact bytes returned. Fail closed on malformed/unreviewed data."""
    from zzaimy.dataset.quality_gate import audit_dataset
    raw = path.read_bytes()
    try:
        pairs = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        if not pairs or any(not isinstance(p, dict) for p in pairs):
            raise ValueError()
        report = audit_dataset(pairs)
        if report["held"] or protect_candidate(pairs) != pairs:
            raise ValueError()
    except (ValueError, TypeError, AttributeError, KeyError, UnicodeError) as exc:
        raise ValueError("근거·구조·맥락·개인정보 검수 승인이 완료되지 않은 학습 데이터입니다.") from exc
    return raw
