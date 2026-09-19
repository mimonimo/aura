"""글자 분해 복원 — OCR 이 한 글자씩 띄어 놓은 낱말을 원래대로 붙인다.

무엇을 고치는가. 스캔 문서에서 "영 남 이 공 대 학 교 총 장" 처럼 낱글자가
빈칸으로 갈라져 들어온다. 글자 자체는 모두 남아 있으므로, 잘못 끼어든 빈칸만
지우면 원문이 돌아온다. 없는 글자를 만들어 넣지 않으므로 절대규칙 1을 지킨다.

어디까지 붙이는가. 두 가지 근거만 쓴다.
  1. 세 글자 이상이 빈칸 하나로만 이어지면 붙인다.
     실측 근거(운영 자료 4,977조각, 2026-09-19): 길이 2는 869건으로 대부분
     "및·중" 같은 한 글자 낱말이 이웃한 정상이고, 길이 3 이상은 127건인데
     사업명·년월일·통영시장·영남이공대학교총장처럼 모두 분해된 낱말이었다.
  2. 두 글자는 붙인 꼴이 같은 자료 안에 멀쩡한 낱말로 이미 있을 때만 붙인다.
     사전을 박아 넣지 않고 들어온 문서에서 어휘를 모으므로, 새 문서가 들어오면
     그 문서의 어휘로 판단이 넓어진다.

구두점이 끼면 끊는다. "가, 나 중" 같은 항목 기호는 손상이 아니기 때문이다.
"""
from __future__ import annotations

import re
from collections import Counter

# 낱글자가 빈칸 하나로 이어진 구간 — 뒤에 붙은 낱말이 있으면 함께 본다
# (구두점·숫자·영문이 끼면 끊긴다)
_RUN = re.compile(r"(?:(?<=\s)|^)((?:[가-힣] )+[가-힣])(?:( )([가-힣]{2,}))?(?=\s|$|[^가-힣])")
_WORD = re.compile(r"[가-힣]{2,}")

JOIN_MIN = 3          # 이 길이부터는 어휘 확인 없이 붙인다 (위 실측 근거)
VOCAB_MIN_COUNT = 2   # 어휘로 인정할 최소 등장 횟수 — 한 번뿐인 것은 근거가 얕다

# 띄어쓰기가 빠진 것으로 보는 한글 덩어리 길이.
# 실측 근거(운영 자료 한글 덩어리 37,361개, 2026-09-19): 길이 9까지가 98.6%,
# 10 이상은 0.9%뿐이며 그 표본은 "성명생년월일성별학생", "가구원정보제공동의현황",
# "통영시대학생학자금이자지원사업신청서" 처럼 전부 어절이 붙은 것이었다.
# 문서마다 다르게 잡지 않고 코퍼스 전체에서 나온 하나의 기준을 쓴다.
RESPACE_RUN = 10


def has_damage(text: str) -> bool:
    """이 글에 분해된 낱말이 있는가 — 세 글자 이상 이어진 구간이 있으면 그렇다."""
    for m in _RUN.finditer(text or ""):
        if len(_join(m.group(1))) >= JOIN_MIN:
            return True
    return False


def build_vocab(texts) -> Counter:
    """어휘는 손상되지 않은 글에서만 모은다.

    손상된 글에서 모으면 "할수"·"둘수" 처럼 원래 띄어 써야 할 것이 어휘에 섞이고,
    그 어휘로 다시 고치면 멀쩡한 글을 망가뜨린다. 실제로 그런 일이 있었다.
    """
    vocab: Counter = Counter()
    for t in texts:
        if has_damage(t):
            continue
        for w in _WORD.findall(t or ""):
            vocab[w] += 1
    return vocab


def _join(run: str) -> str:
    return run.replace(" ", "")


def repair(text: str, vocab: Counter | None = None) -> str:
    """분해된 낱말을 붙여 돌려준다. 고칠 것이 없으면 원문 그대로.

    두 글자 복원은 이 글이 이미 손상됐다고 확인될 때만 한다. 손상은 한 조각
    안에서 몰려 나오므로, 멀쩡한 글의 "할 수"를 건드리지 않게 하는 안전장치다.
    """
    if not text:
        return text
    damaged = has_damage(text)

    def sub(m: re.Match) -> str:
        run, gap, tail = m.group(1), m.group(2) or "", m.group(3) or ""
        joined = _join(run)
        rest = gap + tail
        if len(joined) >= JOIN_MIN:
            return joined + rest
        if vocab and damaged:
            # "통 영 시에" 처럼 뒤 낱말까지 합쳐야 아는 낱말이 되는 경우
            if tail and vocab.get(joined + tail, 0) >= VOCAB_MIN_COUNT:
                return joined + tail
            if vocab.get(joined, 0) >= VOCAB_MIN_COUNT:
                return joined + rest
        return run + rest

    return _RUN.sub(sub, text)


def repair_all(texts: list[str]) -> tuple[list[str], dict]:
    """여러 조각을 한꺼번에 고친다 — 어휘를 자료 전체에서 모아 쓴다.

    돌려주는 통계는 화면과 기록에 쓸 실측값이다. 지어낸 수치가 아니다.
    """
    vocab = build_vocab(texts)
    fixed, changed, joins = [], 0, Counter()
    for t in texts:
        out = repair(t, vocab)
        if out != t:
            changed += 1
            for m in _RUN.finditer(t or ""):
                j = _join(m.group(1))
                if j in out:
                    joins[j] += 1
        fixed.append(out)
    return fixed, {"chunks": len(texts), "changed": changed,
                   "joined": sum(joins.values()), "words": joins.most_common(12)}


_GLUED = re.compile(r"[가-힣]{%d,}" % RESPACE_RUN)


def needs_respacing(line: str) -> bool:
    """이 줄에 어절이 붙은 자리가 있는가 — 덩어리 길이 하나로 판정한다."""
    return bool(_GLUED.search(line or ""))


def _respace(line: str) -> str:
    """띄어쓰기가 사라진 줄에 어절 경계를 되돌린다.

    정부 공고 PDF 에는 "평가를거쳐글로컬대학위원회에서" 처럼 어절이 붙어 들어온다.
    붙은 채로 색인하면 검색어와 맞지 않는다. 판정은 줄 단위로 한다 —
    문서 평균으로 보면 이런 줄이 묻히기 때문이다.
    """
    if not needs_respacing(line):
        return line
    try:
        from zzaimy.app.regulations import restore_spacing

        out = restore_spacing(line)
        return out if out and out.strip() else line
    except Exception:          # 형태소 분석기가 없는 장비 — 원문을 그대로 둔다
        return line


def repair_document(text: str) -> tuple[str, dict]:
    """문서 한 건을 통째로 고친다 — 두 방향 손상을 줄마다 본다.

    하나는 낱말이 글자 단위로 갈라진 것이고, 다른 하나는 어절이 붙어 버린 것이다.
    둘은 원인이 다르지만 모두 검색을 망가뜨린다. 어휘는 그 문서의 멀쩡한 줄에서
    모은다 — 문서마다 쓰는 말이 다르기 때문이다.
    """
    if not text:
        return text, {"chunks": 0, "changed": 0, "joined": 0, "respaced": 0, "words": []}
    lines = text.split("\n")
    joined_lines, stats = repair_all(lines)
    respaced = 0
    out_lines = []
    for line in joined_lines:
        fixed = _respace(line)
        if fixed != line:
            respaced += 1
        out_lines.append(fixed)
    stats["respaced"] = respaced
    return "\n".join(out_lines), stats
