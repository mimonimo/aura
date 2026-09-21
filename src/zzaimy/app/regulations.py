"""규정 저장소 — 외부/내부 관리 규정을 근거로 검토하기 위한 계층.

규정 문서(학칙·훈령·사업 운영 매뉴얼 등)를 조각 단위로 저장하고, 검토 대상
문서와 관련 있는 조각을 찾아 검토 프롬프트에 근거로 넣는다.
검색은 키워드 겹침 점수(v1) — P3에서 벡터 검색으로 교체한다.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from zzaimy.app.chunk_quality import (
    MERGE_MIN_SUBSTANTIVE,
    MIN_SUBSTANTIVE_DROP,
    substantive_len,
)
from zzaimy.app.db import Database

# 조문형 규정의 분할 지점: 줄 머리의 제N조 / 제N장 / 제N절.
# 문장 속 참조("제3조에 따른", "제11조제1항)을 비롯한")는 경계가 아니다 — 거기서 끊으면
# 조문이 문장 중간에서 잘리고 표제가 엉킨다(실측 2026-09-14: 검색 상위에 그런 조각이
# 올라왔다). 조 뒤에 한글이 바로 이어지면(…조에/…조의 사업) 참조로 본다.
# 두 형태만 경계로 본다: ① 줄 머리의 제N조/장/절 ② 어디서든 '제N조(제목)' — 제목 괄호가
# 붙은 조 표기는 조문 머리에만 쓰이고 참조("제3조에", "제3조제1항")엔 안 붙는다. ②가 있어
# 줄바꿈이 사라진 텍스트(OCR·한 줄 붙임)에서도 조문을 나눈다.
_ARTICLE = re.compile(
    r"(?m)(?=^[ \t]*제\s*\d+\s*(?:조(?:\s*의\s*\d+)?|장|절)(?![가-힣])"
    r"|제\s*\d+\s*조(?:\s*의\s*\d+)?\s*[(（])"
)
_TOKEN = re.compile(r"[가-힣A-Za-z0-9]{2,}")
_CHUNK_SIZE = 700
# 이보다 짧으면서 본문이 없는 조각(목차 줄·표제뿐)은 이웃과 합친다
_MIN_CHUNK = 60
# 이보다 길면 임베딩(512토큰)·리랭커(256토큰)가 뒷부분을 못 본다 — 실측: 사업계획 본문
# 35,831자가 조각 하나였고 앞 700자만 검색됐다. 문장·줄 단위로 나눠 상한을 지킨다.
_MAX_CHUNK = 1400
_HARD_MAX = 1100

_kiwi = None
# 조각 본문 → 명사 집합 캐시. 키를 조각 id로 두면 안 된다 — 저장소가 여럿이고(플랫폼·
# 코퍼스) 재분할하면 같은 id에 다른 본문이 들어가 남의 명사로 검색하게 된다
# (실측: 테스트가 서로의 캐시를 물려받아 후보가 0건이 됐다). 본문 해시로 잡는다.
_noun_cache: dict[str, frozenset[str]] = {}


def _noun_key(text: str) -> str:
    import hashlib

    return hashlib.blake2b((text or "").encode("utf-8"), digest_size=16).hexdigest()


def chunk_nouns(chunk: dict) -> frozenset[str]:
    """조각의 명사 집합(캐시) — 본문이 같으면 다시 계산하지 않는다."""
    key = _noun_key(chunk.get("content") or "")
    got = _noun_cache.get(key)
    if got is None:
        got = _noun_cache[key] = extract_nouns(chunk.get("content") or "")
    return got


# 교내·행정 도메인 용어 사전 — 형태소 분석기가 쪼개지 않게 통단어로 등록.
# 실물 문서가 들어오면 자주 등장하는 용어를 여기에 계속 추가한다 (리서치 G영역 과제).
_USER_WORDS = [
    "산학협력단", "전공심화과정", "일학습병행", "공동훈련센터", "지역밀착형",
    "재정지원사업", "국고사업", "평가위원", "열람등급", "휴학원", "복학원",
    "학사경고", "계절학기", "편입학", "산업체위탁교육", "혁신지원사업",
    "취업규칙", "임용내규", "사무분장", "지식재산권", "메이커스페이스",
    "영남이공대학교", "결과보고서", "사업계획서", "공고문", "모집요강",
]


def _get_kiwi():
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi

        _kiwi = Kiwi()
        for w in _USER_WORDS:
            _kiwi.add_user_word(w, "NNP")
    return _kiwi


def extract_nouns(text: str) -> frozenset[str]:
    """형태소 분석으로 명사만 추출 — 조사·어미에 흔들리지 않는 검색 키."""
    try:
        kiwi = _get_kiwi()
        return frozenset(
            t.form
            for t in kiwi.tokenize(text[:4000])
            if t.tag.startswith("NN") and len(t.form) >= 2
        )
    except Exception:
        return frozenset(_TOKEN.findall(text[:4000]))


@dataclass(frozen=True)
class RegulationChunk:
    heading: str
    content: str


_ART_NUM = re.compile(r"제\s*\d+\s*조(?:\s*의\s*\d+)?")
_ART_TITLE = re.compile(
    r"제\s*\d+\s*조(?:\s*의\s*\d+)?\s*[(（]\s*([^)）\n]{2,40})\s*[)）]")


# 표제로 인정하는 구조 표기 — 조문·장절·번호·가나다·로마숫자·불릿·대괄호 표제.
# 이 형태가 아니면 본문 첫 줄을 표제로 승격하지 않는다.
_HEADING_MARK = re.compile(
    r"^(?:제\s*\d+\s*(?:조(?:\s*의\s*\d+)?|장|절|관|편)"
    r"|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*[.、)]"
    r"|\d+(?:[.-]\d+)*\s*[.)]"
    r"|[가나다라마바사아자차카타파하]\s*[.)]"
    r"|[□○◦▪▶◆■●◇△▲]\s*\S"
    r"|[\[(［（<《【]\s*\S)"
)
# 표제가 될 수 없는 것 — 주소·링크·표 괘선. 실측(운영 화면): 이메일 주소가 표제로
# 올라왔다. 첫 줄을 그대로 쓰던 규칙의 결과다.
_NOT_HEADING = re.compile(r"[@]|https?://|www\.|^[\s|+\-–—─_.]+$")
# 문장 종결 — 종결된 문장은 표제가 아니라 본문이다.
_SENTENCE_TAIL = re.compile(r"(?:다|함|음|임|됨|니다|한다|된다)\s*[.。]?\s*$")
# 낱말 중간에서 잘린 꼬리 — 마지막 어절이 한 글자이거나 조사·접속어면 잘린 본문이다.
# 실측: '6지원방법: 이자 지', '본 기관은 학자금 대출이자 지' 같은 표제가 올라왔다.
# 낱말 안의 글자로 판정하면 안 된다 — '개인정보 수집·이용 동의'의 '의'까지 잘림으로
# 보게 된다. 어절 단위로 본다.
_DANGLING_WORDS = {
    "은", "는", "이", "가", "을", "를", "의", "에", "에서", "으로", "로", "와", "과",
    "및", "또는", "에게", "부터", "까지", "한", "하는", "되는", "관한", "따른",
}
_HEADING_MAX = 60
# 번호·불릿이 붙었다고 다 표제는 아니다. 실측(통영 공고): "나. 신청액이 예산을 초과할
# 시 예산 범위 내에서 ①「국민기초생활 보장법」"처럼 본문 항목이 표제로 올라왔다.
# 표제는 짧은 이름표다 — 같은 문서의 진짜 표제는 "1. 지원대상"·"□ 신청 방법"처럼
# 30자 안쪽이다. 조문 표제(제N조(제목))만 _HEADING_MAX까지 허용한다.
_MARK_HEADING_MAX = 30


def _is_cut_off(line: str) -> bool:
    """줄이 낱말·절 중간에서 끊겼는가.

    형태소 분석으로 마지막 토큰이 조사(J*)나 어미(E*)면 뒤에 서술어가 이어질
    자리라 표제가 아니다. 글자로만 보면 '개인정보 수집·이용 동의'의 '의'까지
    조사로 오인한다(실측). 분석기를 못 쓰면 어절 단위 규칙으로 내려간다.
    """
    text = re.sub(r"[)\]）］>》】:：,，·]+$", "", line).strip()
    words = text.split()
    if not words:
        return True
    if words[-1] in _DANGLING_WORDS or re.fullmatch(r"[가-힣]", words[-1]):
        return True
    try:
        toks = _get_kiwi().tokenize(text)
    except Exception:
        return False
    if not toks:
        return True
    tag = toks[-1].tag
    return tag.startswith("J") or tag.startswith("E")


def looks_like_heading(line: str) -> bool:
    """이 줄을 표제로 써도 되는가 — 구조 표기가 있고, 문장도 파편도 아닐 것."""
    s = (line or "").strip().strip("|").strip()
    if not s or _NOT_HEADING.search(s):
        return False
    # 제목 괄호가 붙은 조문 표제만 길게 허용한다. "제2조에 따른 …"은 조 참조이지
    # 표제가 아니다(실측: 본문 한 줄이 그대로 표제가 됐다).
    limit = _HEADING_MAX if _ART_TITLE.match(s) else _MARK_HEADING_MAX
    if len(s) > limit:
        return False
    if _SENTENCE_TAIL.search(s):
        return False
    if _is_cut_off(s):
        return False          # 조사·한 글자 어절로 끝남 = 낱말 중간에서 잘린 줄
    return bool(_HEADING_MARK.match(s))


_LABEL_MAX = 20            # 표의 이름 칸으로 볼 수 있는 길이
_VALUE_MAX = 40            # 값이 이보다 길면 이름 칸을 표제로 쓴다
_PLAIN = re.compile(r"[^가-힣A-Za-z0-9]")          # 길이를 셀 때 뺄 것(공백·기호)
_PRIVATE_GLYPH = re.compile(r"[\ue000-\uf8ff\U000f0000-\U000ffffd]")   # 한글 문서의 사설 불릿 글자


def _table_heading(text: str) -> str:
    """표가 본문인 조각의 표제를 표 자신의 머리에서 만든다 — 못 만들면 빈 문자열.

    왜: 편성표·신청서 서식처럼 문서 전체가 표인 경우 서술형 표제가 아예 없어
    조각 제목이 문서 이름뿐이었다(실측 2026-09-20: 108건 중 6건이 '구조 표제를
    붙이지 못했습니다'). 인용도 "《연계교육과정 편성표 · 》"으로 나가고, 조각마다
    어느 학과 이야기인지 화면에서 구분되지 않았다.

    규칙(문서별 예외 없이):
      · 빈 줄이 아닌 줄의 절반 이상이 셀 구분자를 가진 조각(표 행 2줄 이상)만 대상으로 한다.
      · 표 행만 위에서부터 최대 3줄 보며, 셀을 정리한 뒤(병합 셀의 반복은 하나로) 판단한다.
        표가 아닌 줄은 쳐다보지 않는다 — 서술문 첫 줄이 표제로 올라가면 안 된다.
        - '이름 | 값' 두 칸이면 값을 쓴다. 이름은 어느 문서에나 있는 일반어라
          '소프트웨어융합과'가 '학과(계열)'보다 그 조각을 잘 가리킨다.
          값이 길면(서술에 가까움) 이름 칸을 쓴다.
        - 값 칸이 여럿인 서식 행('컨소시엄 주관대학 | 대학명 | …')은 이름 칸을 쓴다.
        - 한 칸으로 줄면 그 칸을 쓴다(제목 행).
      · 이렇게 고른 것 두 개까지 ' · '로 잇는다 — '소프트웨어융합과 · 컴퓨터공학과'.
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    rows = [ln for ln in lines if "|" in ln]
    if len(rows) < 2 or len(rows) / len(lines) < 0.5:
        return ""
    picks: list[str] = []
    for ln in rows[:3]:
        cells: list[str] = []
        for cell in (c.strip() for c in ln.split("|")):
            if cell and (not cells or cells[-1] != cell):
                cells.append(cell)             # 병합 셀이 만든 반복은 하나로
        if not cells:
            continue
        if len(cells) == 1:
            pick = cells[0]
        elif len(cells[0]) > _LABEL_MAX:
            continue                           # 첫 칸이 이름 같지 않다 — 자료 행이다
        elif len(cells) == 2:
            pick = cells[1] if len(cells[1]) <= _VALUE_MAX else cells[0]
        else:
            pick = cells[0]                    # 값 칸이 여럿인 서식 행 — 이름 칸이 표제
        pick = _PRIVATE_GLYPH.sub("", re.sub(r"\s+", " ", pick)).strip(" .·:*")
        # 두 글자짜리는 표 살림 낱말(구분·학년·합계·계·개발·운영)이라 무엇도 가리지 못한다.
        # 실측(2026-09-20): 이 하한이 없으면 431개 중 '합 계'·'구분 · 초급' 같은 표제가 섞여
        # 들어오고, 표제가 붙은 조각은 잡음 필터에서 보호되므로 잡음까지 살아남는다.
        plain = _PLAIN.sub("", pick)
        if len(plain) >= 3 and re.search(r"[가-힣A-Za-z]", pick) and pick not in picks:
            picks.append(pick)
        if len(picks) == 2:
            break
    return " · ".join(picks)[:_HEADING_MAX].strip(" ·") if picks else ""


def _heading_of(text: str) -> str:
    """조각을 대표하는 표제 — 구할 수 없으면 빈 문자열.

    ① '제N조(제목)'이면 그 제목 ② 첫 줄이 구조 표제 형태면 그 줄
    ③ 표가 본문인 조각이면 표의 머리에서 만든다(_table_heading). 그 밖에는
    표제를 만들지 않는다. 본문 첫 줄을 그대로 쓰면 이메일 주소·낱말 중간에서
    잘린 파편이 표제가 된다(운영 화면 실측). 표제가 없는 편이 거짓 표제보다 낫다.
    """
    t = re.sub(r"[ \t]+", " ", (text or "").strip())
    t = re.sub(r"\s*\|\s*", " ", t)          # 표 셀 구분자 → 공백
    if not t:
        return ""
    # ① 제N조(제목)
    m = _ART_TITLE.search(t[:160])
    if m:
        am = _ART_NUM.match(t)
        num = re.sub(r"\s+", "", am.group(0)) if am else ""
        title = m.group(1).strip()
        return (f"{num}({title})" if num else title)[:_HEADING_MAX]
    head = t.splitlines()[0].strip()
    if looks_like_heading(head):
        return head[:_HEADING_MAX].strip()
    return _table_heading(text)


def _split_size(text: str, size: int = _CHUNK_SIZE) -> list[str]:
    """줄 단위로 size자 안팎 조각으로 묶는다(줄 안에서 자르지 않는다)."""
    out: list[str] = []
    buf = ""
    for ln in text.splitlines():
        if buf and len(buf) + len(ln) + 1 > size:
            out.append(buf)
            buf = ln
        else:
            buf = f"{buf}\n{ln}" if buf else ln
    if buf:
        out.append(buf)
    return out


def _finalize(chunks: list[RegulationChunk]) -> list[RegulationChunk]:
    """조각 정리(일반 규칙): 실질이 모자란 조각은 이웃과 합치고, 중복은 버린다.

    실측(2026-09-19, corpus_pilot 1,846조각): 48.2%가 실질 글자수 40자 미만,
    36.9%가 20자 미만이었다. 20자 미만 조각은 코퍼스 중심 벡터와의 코사인이 0.762로
    가장 높아(긴 조각 0.68~0.70) 어떤 질의에도 딸려 온다 — "억지 연관"의 실체다.
    이전 규칙은 '본문 줄이 하나라도 있으면 통과'여서, 줄바꿈이 섞인 PDF 파편이
    전부 독립 조각으로 남았다. 이제는 줄 수가 아니라 실질 글자수로 판정한다.

    - 혼자 설 수 없는 조각(_self_contained 참조)은 다음 조각 앞에 붙인다
    - 문서 끝에서 모자란 조각은 직전 조각 뒤에 붙인다
    - 합쳐진 조각의 표제는 본문에서 다시 뽑고, 못 뽑으면 이웃의 표제를 쓴다
    """
    out = _merge_thin(chunks)

    seen: set[str] = set()
    uniq: list[RegulationChunk] = []
    for c in out:
        key = re.sub(r"\s+", " ", c.content).strip()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)

    # 상한 — 긴 조각은 구조에 맞춰 나눈다(줄이 많으면 줄 단위, 아니면 문장 단위). 표제는 유지.
    bounded: list[RegulationChunk] = []
    for c in uniq:
        if len(c.content) <= _MAX_CHUNK:
            bounded.append(c)
            continue
        many_lines = c.content.count("\n") >= 8
        pieces = (
            _split_size(c.content, _CHUNK_SIZE) if many_lines
            else _pack_sentences(c.content, _CHUNK_SIZE, _HARD_MAX)
        )
        for piece in pieces:
            if piece.strip():
                bounded.append(RegulationChunk(heading=c.heading, content=piece))
    # 크기 분할이 만든 조각도 같은 기준으로 다시 본다 — 줄 단위로 자르면 "3-2." 같은 번호 줄 하나가
    # 조각으로 떨어져 나온다(실측 2026-09-22: 운영 4,284조각 중 2~4자 조각 12건, 80자 미만 473건).
    return _merge_thin(bounded)


def _merge_thin(chunks: list[RegulationChunk]) -> list[RegulationChunk]:
    """혼자 설 수 없는 조각(_self_contained 참조)은 다음 조각 앞에, 문서 끝이면 직전 조각 뒤에 붙인다."""
    merged: list[RegulationChunk] = []
    for c in chunks:
        ct = (c.content or "").strip()
        if not ct:
            continue
        if merged and not _self_contained(merged[-1].content):
            prev = merged.pop()
            body = f"{prev.content}\n{ct}"
            merged.append(RegulationChunk(
                heading=_heading_of(body) or prev.heading or c.heading, content=body))
            continue
        merged.append(RegulationChunk(heading=c.heading or _heading_of(ct), content=ct))
    while len(merged) >= 2 and not _self_contained(merged[-1].content):
        last = merged.pop()
        prev = merged.pop()
        body = f"{prev.content}\n{last.content}"
        merged.append(RegulationChunk(
            heading=prev.heading or _heading_of(body), content=body))
    return merged


def index_ready(doc_id: int, chunks: list[RegulationChunk]) -> tuple[list[RegulationChunk], int]:
    """적재 직전의 잡음 관문 — (남길 조각, 뺀 수). 반입 경로(pipeline)와 승격(123)·재분할(75)이 같이 쓴다.

    강도는 SEARCH 다(INDEX 는 '제1조(목적)' 같은 짧은 조문까지 버린다). 표제가 붙은 조각은 이 플랫폼의
    뼈대라 지키되, 본문이 없는 표제만의 조각("3-2.", "Ⅴ.")은 지키지 않는다 — 이전 규칙은 표제만 있으면
    무조건 살려 번호 줄이 검색 단위로 남았다(2026-09-22 실측). 전부 걸러지면 원본을 그대로 둔다.
    """
    from zzaimy.app.chunk_quality import MIN_SUBSTANTIVE_DROP, Strictness, filter_chunks, substantive_len

    if not chunks:
        return [], 0
    kept, _removed = filter_chunks(
        [{"doc_id": doc_id, "content": c.content, "heading": c.heading} for c in chunks],
        Strictness.SEARCH,
    )
    keep_keys = {(k["heading"], k["content"]) for k in kept}
    survivors = [
        c for c in chunks
        if (c.heading, c.content) in keep_keys
        or ((c.heading or "").strip() and substantive_len(c.content) >= MIN_SUBSTANTIVE_DROP)
    ]
    if not survivors:
        return list(chunks), 0
    return survivors, len(chunks) - len(survivors)


# 문장이 끝났는가 — 한국어 행정문서의 평서 종결. 여기서 끝나지 않는 조각은
# 낱말·문장 중간에서 잘린 파편이다(실측: '…개발\n하고,' · '…유효\n하며,').
_COMPLETE_TAIL = re.compile(
    r"(?:다|함|음|임|됨|니다|한다|된다|요|것|오|기|함께|말한다)\s*[.。]\s*$"
    r"|(?:함|음|임|됨)\s*$"
    r"|[.!?)\]】」』]\s*$")


def _self_contained(text: str) -> bool:
    """이 조각이 혼자 설 수 있는가 — 실질이 충분하고 문장이 끝났는가.

    조각을 합칠지 정하는 유일한 기준이다. 길이만 보면 정상 조문("제1조(목적)
    …목적으로 한다." 실질 28자)까지 합쳐 버리고, 줄 수만 보면 줄바꿈이 섞인 PDF
    파편이 전부 통과한다(이전 규칙의 실패). 둘을 함께 본다:
      · 실질 글자수 ≥ MIN_SUBSTANTIVE_DROP(20) — 측정상 그 아래는 검색 잡음
      · 문장 종결로 끝남 — 잘린 파편은 조사·어미로 끝난다
    """
    t = (text or "").strip()
    n = substantive_len(t)
    if n < MIN_SUBSTANTIVE_DROP:
        return False                      # 20자 미만은 무조건 이웃과 합친다
    if n >= MERGE_MIN_SUBSTANTIVE:
        return True                       # 실질이 충분하면 그대로 검색 단위
    return bool(_COMPLETE_TAIL.search(t))  # 20~39자는 문장이 끝났을 때만


def _has_body(text: str) -> bool:
    """표제 말고 본문이 있는가 — '제N조(제목) 본문…' 또는 '표제 줄 + 본문 줄'.

    목차 줄("제4조)-이해관계 직무의 회피(")·장 제목("제1장 총칙")·불릿 표제("정보제공
    동의현황")는 한 줄뿐이고 제목 괄호 뒤에 이어지는 말이 없다 → 본문 없음.
    """
    m = _ART_TITLE.match(text)
    if m:
        body = text[m.end():]
    else:
        lines = text.splitlines()
        body = "\n".join(lines[1:]) if len(lines) >= 2 else ""
    return len(re.sub(r"[\W_]+", "", body)) >= 6


def split_regulation(text: str) -> list[RegulationChunk]:
    """조문형이면 제N조 단위로, 아니면 문단 묶음(약 700자)으로 나눈다."""
    text = text.strip()
    if not text:
        return []

    parts = [p.strip() for p in _ARTICLE.split(text) if p.strip()]
    if len(parts) >= 3:  # 조문 구조가 실제로 있다고 판단
        return _finalize(
            [RegulationChunk(heading=_heading_of(p), content=p) for p in parts]
        )

    def starts_section(para: str) -> bool:
        """이 문단이 새 절을 여는가.

        ① 구조 표제(제N장·번호·불릿)로 시작하거나 ② '짧은 표제 줄 + 본문 줄'
        형태일 때. ②는 빈 줄로 문단이 이미 나뉘어 있을 때만 쓴다 — 빈 줄이 없는
        PDF 추출문에서는 줄마다 짧아 이 조건이 무의미해진다.
        """
        lines = para.splitlines()
        first = lines[0].strip()
        if looks_like_heading(first):
            return True
        return (len(lines) >= 2 and len(first) <= 25
                and not first.endswith(("다.", "함.", "음.", ".")))

    chunks: list[RegulationChunk] = []
    buf = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        # 제목형 문단에서 새 조각 시작 — 단, 앞 조각이 혼자 설 수 있을 때만.
        # (실측: PDF 추출문은 줄마다 짧아 조건 없이 끊으면 한 줄짜리 조각이 쏟아진다)
        enough = _self_contained(buf)
        if buf and ((enough and starts_section(para)) or len(buf) + len(para) > _CHUNK_SIZE):
            chunks.append(RegulationChunk(heading=_heading_of(buf), content=buf))
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(RegulationChunk(heading=_heading_of(buf), content=buf))
    return _finalize(chunks)


# 서술형(공고·계획서) 절 경계 — 로마숫자·장/절/조·번호·가나다·불릿·대괄호
_PROSE_HEADING = re.compile(
    r"^(?:제\s*\d+\s*[장절관조]"
    r"|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*[.、\s]"
    r"|\d+(?:-\d+)?\s*[.)]\s"
    r"|[가나다라마바사아자차카타파하]\s*[.)]\s"
    r"|[□○◦▪▶◆■●·※])"
)
_SENT_END = re.compile(r"(?<=[다음함임])\.\s|(?<=\.)\s|(?<=니다)\.\s|(?<=[.!?])\s")
# 질문 줄 — 물음표로 끝나거나 한국어 의문 종결로 끝나는 줄(FAQ의 Q)
_QUESTION_LINE = re.compile(r"(?:\?|？|(?:나요|가요|까요|습니까|ㅂ니까|인가요|는지요))\s*$")


def _pack_sentences(text: str, target: int, hard_max: int) -> list[str]:
    """긴 본문을 문장 경계로 target자 안팎 창으로 묶는다(문장 안 자름)."""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) <= hard_max:
        return [text] if text else []
    sents = [s for s in _SENT_END.split(text) if s and s.strip()]
    out, buf = [], ""
    for s in sents:
        if buf and len(buf) + len(s) > target:
            out.append(buf.strip())
            buf = s
        else:
            buf = f"{buf} {s}" if buf else s
        if len(buf) >= hard_max:            # 문장 없이도 너무 길면 강제 컷
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def _is_table_block(lines: list[str]) -> bool:
    """줄의 절반 이상이 표 행(' | ' 구분자)이면 표 블록으로 본다 — _table_heading 과 같은 기준."""
    body = [ln for ln in lines if ln.strip()]
    return len(body) >= 2 and sum(1 for ln in body if " | " in ln) / len(body) >= 0.5


def split_prose(text: str, target: int = 700, hard_max: int = 1100) -> list[RegulationChunk]:
    """서술형 문서(공고·사업계획서)를 절 경계·문장 단위로 촘촘히 나눈다.

    PDF 추출 텍스트처럼 문단 사이 빈 줄이 없어도 동작한다. 절 표제(로마숫자·
    번호·불릿 등)에서 조각을 끊고, 표제 없는 긴 덩어리는 문장으로 묶는다.
    각 조각에는 직전 표제를 heading으로 붙인다.

    경계 규칙 두 가지를 둔다(실측에서 드러난 문제를 일반 규칙으로 막는다):
      · 앞 블록이 실질을 갖추기 전에는 표제를 만나도 끊지 않는다 — PDF 추출문은
        줄마다 불릿이 붙어 있어 무조건 끊으면 한 줄짜리 조각이 쏟아진다.
      · 물음표로 끝나는 줄은 새 블록을 연다 — 질문과 답이 한 조각에 함께 있도록.
        (실측: FAQ의 질문 한 줄만 독립 검색 단위가 됐다)
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    lines = [ln.rstrip() for ln in text.split("\n")]

    # 표제 라인에서 블록 분할
    blocks: list[tuple[str, list[str]]] = []
    heading, body = "", []

    def substance() -> int:
        return substantive_len(heading + " " + " ".join(body))

    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        is_head = _PROSE_HEADING.match(s) and len(s) <= 60
        is_question = bool(_QUESTION_LINE.search(s)) and len(s) <= 120
        if (is_head or is_question) and (heading or body):
            if substance() < MERGE_MIN_SUBSTANTIVE:
                body.append(s)                 # 아직 실질이 없다 — 끊지 않고 이어 붙인다
                continue
            blocks.append((heading, body))
            # 질문 줄은 표제가 아니라 본문의 시작 — 표제는 상위 절 제목을 유지한다
            heading, body = (s, []) if is_head else (heading, [s])
        elif not heading and not body and is_head:
            heading = s
        else:
            body.append(s)
    if heading or body:
        blocks.append((heading, body))

    chunks: list[RegulationChunk] = []
    for head, body_lines in blocks:
        if _is_table_block(body_lines):
            # 표는 행이 단위다 — 문장으로 묶으면 행 중간에서 잘려 머리글과 값이 떨어진다
            # (실측 2026-09-22: '제출기한 / 접수방법 | …' 행이 조각 경계에서 끊김). 표제는 첫 조각에만.
            rows_text = "\n".join(([head] if head else []) + [ln.strip() for ln in body_lines if ln.strip()])
            pieces = _split_size(rows_text, target)
            for pc in pieces:
                chunks.append(RegulationChunk(
                    heading=(head if looks_like_heading(head) else "")[:60], content=pc))
            continue
        body = " ".join(body_lines).strip()
        full = (head + " " + body).strip() if head else body
        if not full:
            continue
        pieces = _pack_sentences(full, target, hard_max)
        for pc in pieces:
            chunks.append(RegulationChunk(
                heading=(head if looks_like_heading(head) else "")[:60], content=pc))
    return _finalize(chunks)


def chunk_document(text: str) -> list[RegulationChunk]:
    """문서 성격을 판별해 청킹한다: 조문형(규정)이면 split_regulation, 그 외
    서술형(공고·계획서)이면 split_prose. 인제스트가 문서마다 이걸 부른다."""
    text = (text or "").strip()
    if not text:
        return []
    reg = split_regulation(text)
    # 조문형이면(조각이 여럿·평균 길이 적정) 그대로. 아니면 서술형 청커로 재분할.
    if len(reg) >= 3 and max((len(c.content) for c in reg), default=0) <= 1600:
        return reg
    return split_prose(text)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text))


_SINGLE_SYL = re.compile(r"(?<=[가-힣]) (?=[가-힣](?![가-힣]))|(?<=(?<![가-힣])[가-힣]) (?=[가-힣])")


_ONE_SYL = re.compile(r"^[가-힣]$")


def _syl_run(line: str) -> int:
    """한 글자 한글 토큰이 연달아 나온 최대 길이 — 글자마다 띄운 출력의 지문.

    비율로 보면 '그 외 사항은 따른다'(1글자 2/4)처럼 정상 표현이 걸린다. 반면
    '영 남 이 공 대 학교'는 한 글자가 연달아 5개다. 이어짐이 더 확실한 신호다.

    한글 아닌 토큰(숫자·기호·영문)은 이어짐을 끊는다. 건너뛰면 '2023년 5 월 26 일'이
    '월·일' 연속으로 잡혀 '일산학협력단장'처럼 붙는다(실측 2026-09-20에 잡힌 오작동).
    """
    best = run = 0
    for w in line.split():
        run = run + 1 if _ONE_SYL.match(w) else 0
        best = max(best, run)
    return best


def _spread_line(line: str, min_run: int, max_tokens: int = 0) -> bool:
    if max_tokens and len(line.split()) > max_tokens:
        return False
    return _syl_run(line) >= min_run


def over_spacing_evidence(text: str) -> bool:
    """이 문서가 글자마다 띄운 출력인가 — 확실한 줄(한 글자 4연속)이 둘 이상인지로 본다.

    짧은 줄('ㅇ 계 좌 정보')만 보고는 판정할 수 없어, 확실한 줄에서 증거를 모아
    증거가 있는 문서에서만 짧은 줄까지 손댄다(실측 2026-09-20: 규칙에 걸린 벌어진 줄
    548줄 옆에 같은 문서들의 짧은 줄 564줄이 그대로 남아 있었다).
    """
    return sum(1 for ln in (text or "").splitlines() if _spread_line(ln, 4)) >= 2


def _collapse_over_spacing(t: str, short_too: bool = False) -> str:
    """'영 남 이 공 학교'처럼 글자마다 띄운 OCR 출력의 공백을 걷어 낸다(줄 단위).

    한 글자 한글 토큰이 3개 이상 연달아 나오면 그 줄은 글자 단위로 쪼개진 것으로 보고,
    한 글자 토큰에 붙은 공백을 지운다. 정상 문장은 건드리지 않는다.

    short_too=True 면 토큰 5개 이하의 짧은 줄에서 2연속까지 손댄다('ㅇ 계 좌 정보').
    문서에 증거가 있을 때만 켜는 용도다 — 과하게 붙은 것은 뒤의 Kiwi 띄어쓰기가 되돌린다.
    """
    out = []
    for line in t.splitlines():
        # 짧은 줄의 완화는 짧은 줄에만. 긴 줄에 2연속 기준을 적용하면 '표 등 하단에'가
        # 걸려 문장 전체가 붙는다(실측 2026-09-20: 사업계획서 작성 요령 단락이 망가졌다).
        if _spread_line(line, 3) or (short_too and _spread_line(line, 2, max_tokens=5)):
            prev = None
            while prev != line:
                prev, line = line, _SINGLE_SYL.sub("", line)
        out.append(line)
    return "\n".join(out)


def restore_spacing(text: str, *, spread_doc: bool | None = None) -> str:
    """OCR이 흐트러뜨린 어절 공백을 Kiwi로 복원한다 — 두 방향.

    · 공백이 거의 없는 텍스트(비율 8% 미만): 띄어쓰기를 넣는다.
    · 글자마다 띄운 텍스트(tesseract 사진 OCR 실측 '영 남 이 공 학교'): 글자 사이 공백을
      걷어 낸 뒤 다시 띄어쓴다 — Kiwi의 space()는 공백을 넣기만 하고 지우지는 않는다.
      문서에 그 증거가 있으면 짧은 줄('ㅇ 계 좌 정보')까지 함께 고친다.
    이미 정상인 텍스트는 건드리지 않는다 — 원본 양식의 디지털 재구성이 목적이지 재작성이 아니다.
    """
    t = text.strip()
    if len(t) < 20:
        return text
    # spread_doc: 이 조각이 속한 문서가 글자마다 띄운 출력인지(호출부가 문서 전체로 판정해 넘긴다).
    # 넘어오지 않으면 이 글만 보고 판정한다.
    short_too = over_spacing_evidence(t) if spread_doc is None else spread_doc
    collapsed = _collapse_over_spacing(t, short_too=short_too)
    ratio = t.count(" ") / len(t)
    if collapsed == t and ratio >= 0.08:
        return text
    try:
        kiwi = _get_kiwi()
        fixed = kiwi.space(collapsed)
        return fixed if fixed else text
    except Exception:
        return collapsed if collapsed != t else text


def sparse_search(
    db: Database, query_text: str, top_k: int = 15, min_overlap: int = 1,
    sector: str | None = None, dept: str | None = None,
) -> list[dict]:
    """임베딩·리랭커 없이 Kiwi 명사 겹침(+IDF)만으로 조각을 랭킹한다.

    모델이 없는 환경(맥 dev·임베딩 미계산)에서 검색 동작을 확인·노출하는 용도.
    반환: 각 조각 dict에 float `score`를 붙인 top-k 리스트.
    """
    import math

    query = extract_nouns(query_text)
    if not query:
        return []
    chunks = db.list_regulation_chunks(sector=sector, dept=dept)
    nouns = {c["id"]: chunk_nouns(c) for c in chunks}
    n = max(len(chunks), 1)
    df = {t: sum(1 for c in chunks if t in nouns[c["id"]]) for t in query}
    idf = {t: math.log(1 + n / (1 + df[t])) for t in query}
    rare_cut = max(3, int(n * 0.1))
    scored: list[tuple[int, float, dict]] = []
    for chunk in chunks:
        matched = query & nouns[chunk["id"]]
        if len(matched) >= min_overlap:
            rare_hits = sum(1 for t in matched if df[t] <= rare_cut)
            score = sum(min(len(t), 4) * idf[t] for t in matched)
            scored.append((rare_hits, score, chunk))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    out = []
    for _, score, chunk in scored[:top_k]:
        c = dict(chunk)
        c["score"] = round(score, 2)
        out.append(c)
    return out


# 하이브리드 검색 상수 — 운영 경로(find_relevant)와 평가(zzaimy.eval.retrieval_eval)가 같은 값을
# 쓴다. 가중 근거: 2026-09-04 스윕(docs/retrieval-weight-sweep.md) — w_a 0.2~0.5 고원
# (MRR .692~.693), 동가중(1.0)은 .647로 손해. 고원 중앙값 0.4 채택
HYBRID_W_A = 0.4  # RRF 어휘 가중 (임베딩 1.0)
# 질의의 '초점어' 수 — 판별력(IDF) 상위 이만큼 중 하나는 겹쳐야 근거로 본다.
# 2로 둔 근거: 한국어 행정 질의는 대개 주제어 1~2개 + 범용어(기준·절차·방법)로
# 이뤄진다. 1이면 동의어·표기 차이에 너무 약하고, 3 이상이면 범용어가 초점에
# 섞여 걸러 내는 힘이 사라진다(명사 3개 이하 질의에는 적용하지 않는다).
FOCUS_TERMS = 2
# 초점어 규칙을 적용할 최소 조각 수 — 문서 빈도(IDF)가 뜻을 가지려면 모집단이
# 있어야 한다. 조각이 몇 개뿐인 저장소에서는 '검토·의무' 같은 범용어가 가장 희귀한
# 말이 되어 버린다(실측: 조각 3개짜리 테스트 저장소에서 정답이 걸러졌다).
FOCUS_MIN_CHUNKS = 200
# RRF 에 넣는 어휘·임베딩 순위 길이 / 리랭커에 넘기는 후보 수.
# 12·10 이었던 것을 20 으로 올렸다(2026-09-20). 리랭커가 토르 GPU 로 가면서 후보 20개도
# 질의당 0.2초대라 늘릴 수 있게 됐다 — VM CPU 시절에는 10개에 3.75초였다.
# 실측(질의 150건, 학습본 리랭커): 후보 10 → 20 에서
#   정확한 질문 R@5 0.820→0.860 · R@10 0.827→0.887 · MRR 0.777→0.806
#   상황 질문   R@5 0.680→0.713 · R@10 0.700→0.747 · MRR 0.622→0.647
# 30개는 더 낫지 않았다(상황 질문 R@1 0.600→0.580). 리랭커는 후보 안의 순서만 바꾸므로
# 풀에 정답이 들어오는 비율(0.827→0.893)이 천장이다.
HYBRID_TOP_K = 20
CANDIDATE_LIMIT = 20
# 조각별 질문 축(doc2query)의 융합 가중 — 0 이면 쓰지 않는다. 환경변수 ZZAIMY_QUESTION_W 로 잰다.
# 색인은 scripts/111(질문 생성)·112(임베딩)로 만든다. 켜는 값은 측정 뒤에 정한다.
HYBRID_W_Q = 0.0


def _lexical_ids(query: frozenset[str], chunks: list[dict], min_overlap: int) -> list[int]:
    """명사 집합으로 조각을 점수순 정렬 — lexical_rank·hybrid_candidates의 공통 본체.

    점수 = 겹친 명사의 길이 합 × IDF (긴·드문 명사가 더 정보량이 크다). 조각 명사는
    프로세스 내 캐시로 재계산을 피한다.
    """
    import math

    nouns = {c["id"]: chunk_nouns(c) for c in chunks}
    # 희소성 가중치 — 어디에나 나오는 명사(기준·처리 등)는 정보량이 낮다
    n = max(len(chunks), 1)
    df = {t: sum(1 for c in chunks if t in nouns[c["id"]]) for t in query}
    idf = {t: math.log(1 + n / (1 + df[t])) for t in query}

    # 희귀 명사(전체 조각의 10% 이하에서만 등장)가 질의의 실질 주제다 —
    # "휴학"이 "기준·처리" 같은 범용 명사에 밀리지 않게 1순위 정렬키로 쓴다
    rare_cut = max(3, int(n * 0.1))
    # 흔한 명사만 겹친 조각은 근거가 아니다 — "학생·지원·사업"처럼 거의 모든 문서에
    # 나오는 말로 이어지는 것이 "연관성이 억지스럽다"의 정체다. 두 단계로 막는다.
    #   ① 판별력 있는 명사(전체 조각의 10% 이하에서만 등장)가 하나도 없으면 제외
    #   ② 질의에서 가장 판별력 높은 명사(초점어) 중 하나는 반드시 겹쳐야 한다
    # ②가 필요한 이유: "소방 점검 주기와 과태료"처럼 코퍼스에 없는 주제를 물어도
    # '점검'만 겹쳐 근거가 올라왔다(실측). 초점어가 코퍼스에 아예 없으면 결과는
    # 0건이 맞다. 코퍼스가 작으면 rare_cut이 커져 ①은 자동으로 무력해진다.
    require_rare = os.environ.get("ZZAIMY_LEXICAL_REQUIRE_RARE", "1") != "0"
    focus: set[str] = set()
    if require_rare and len(query) > FOCUS_TERMS and n >= FOCUS_MIN_CHUNKS:
        focus = set(sorted(query, key=lambda t: (-idf[t], -len(t)))[:FOCUS_TERMS])
        if all(df[t] == 0 for t in focus):
            return []            # 질의의 핵심어가 저장소에 아예 없다 = 근거 없음
        focus = {t for t in focus if df[t] > 0}
    scored: list[tuple[int, float, int, int]] = []
    for chunk in chunks:
        matched = query & nouns[chunk["id"]]
        if len(matched) < min_overlap:
            continue
        rare_hits = sum(1 for t in matched if df[t] <= rare_cut)
        if require_rare and rare_hits == 0:
            continue
        if focus and not (matched & focus):
            continue
        score = sum(min(len(t), 4) * idf[t] for t in matched)
        scored.append((rare_hits, score, len(matched), chunk["id"]))
    scored.sort(key=lambda x: (-x[0], -x[1], -x[2]))
    return [cid for _, _, _, cid in scored]


def lexical_rank(
    db: Database, query_text: str, min_overlap: int = 2,
    sector: str | None = None, dept: str | None = None,
    chunks: list[dict] | None = None,
) -> list[int]:
    """어휘(Kiwi 명사+IDF) 순위 — 운영 검색의 어휘 축. 조각 id를 점수순으로 돌려준다.

    chunks를 주면 DB 조회를 생략한다(평가 배치·스윕 스크립트).
    """
    query = extract_nouns(query_text)
    if not query:
        return []
    if chunks is None:
        chunks = db.list_regulation_chunks(sector=sector, dept=dept)
    return _lexical_ids(query, chunks, min_overlap)


def select_candidates(
    merged_ids: list[int], by_id: dict[int, dict], limit: int = CANDIDATE_LIMIT,
) -> list[dict]:
    """융합 순위에서 근거가 될 수 없는 조각을 빼고 상위 limit개.

    품질 판정(chunk_quality, SEARCH 강도)을 그대로 쓴다 — 실질 내용이 없는 파편,
    목차·페이지번호, 정형 문구, 같은 본문의 중복은 근거가 될 수 없다.
    (실측: 같은 조각이 상위 3건을 전부 차지하거나 한 단어짜리 조각이 1위에 올랐다)
    """
    from zzaimy.app.chunk_quality import Strictness, assess, corpus_reasons

    ordered = [by_id[cid] for cid in merged_ids if cid in by_id]
    if not ordered:
        return []
    # 후보 집합 안에서만 보는 코퍼스 규칙 — 중복·머리말 반복은 여기서도 유효하다
    extra = corpus_reasons(ordered)
    seen_body: set[str] = set()
    candidates: list[dict] = []
    for i, c in enumerate(ordered):
        body = re.sub(r"\s+", " ", c.get("content") or "").strip()
        if body in seen_body:
            continue
        v = assess(c.get("content") or "",
                   {"extra_reasons": [r for r in extra.get(i, []) if r != "boilerplate"]})
        if not v.keep(Strictness.SEARCH):
            continue
        seen_body.add(body)
        item = dict(c)
        item["quality_score"] = v.score
        candidates.append(item)
        if len(candidates) >= limit:
            break
    return candidates


def hybrid_candidates(
    db: Database, query_text: str, min_overlap: int = 2,
    sector: str | None = None, dept: str | None = None,
    chunks: list[dict] | None = None,
    lexical_ids: list[int] | None = None, dense_ids: list[int] | None = None,
    limit: int = CANDIDATE_LIMIT,
) -> list[dict]:
    """리랭크 직전까지의 운영 후보 — 어휘·임베딩 순위를 RRF(w_a=0.4)로 융합해 상위 limit개.

    임베딩이 비활성이면 어휘 단독. 명사가 하나도 없는 질의는 빈 목록.
    lexical_ids·dense_ids를 주면 그 순위를 그대로 쓴다(평가에서 축별 순위를 한 번만 계산).
    """
    query = extract_nouns(query_text)
    if not query:
        return []
    if chunks is None:
        chunks = db.list_regulation_chunks(sector=sector, dept=dept)
    if lexical_ids is None:
        lexical_ids = _lexical_ids(query, chunks, min_overlap)

    # 임베딩 순위와 RRF 융합
    from zzaimy.app.embed_search import embed_search, question_search, rrf_merge

    allowed = {c["id"] for c in chunks}
    if dense_ids is None:
        dense_ids = [cid for cid, _ in embed_search(query_text, top_k=HYBRID_TOP_K)]
    dense_ids = [cid for cid in dense_ids if cid in allowed]
    merged = rrf_merge(lexical_ids[:HYBRID_TOP_K], dense_ids, w_a=HYBRID_W_A, w_b=1.0)
    # 보조 축 — 조각별 질문(doc2query). 가중이 0 이면 쓰지 않는다(측정하고 켠다).
    w_q = float(os.environ.get("ZZAIMY_QUESTION_W", HYBRID_W_Q))
    if w_q > 0:
        q_ids = [cid for cid, _ in question_search(query_text, top_k=HYBRID_TOP_K)
                 if cid in allowed]
        if q_ids:
            merged = rrf_merge(merged, q_ids, w_a=1.0, w_b=w_q)
    return select_candidates(merged, {c["id"]: c for c in chunks}, limit)


def drop_near_duplicates(chunks: list[dict]) -> list[dict]:
    """순서를 지키며 거의 같은 내용의 조각을 한 번만 남긴다.

    같은 규정이 두 번 적재되면(예: 규정집 파일과 개별 파일) 근거 세 자리를 같은 조항이 나눠
    가진다. 앞선(점수가 높은) 쪽을 남긴다. 기준은 적재 품질 점검의 근사 중복 기준과 같다.
    """
    from zzaimy.app.chunk_quality import NEAR_DUP_JACCARD, _fingerprint, _jaccard, _shingles

    kept: list[dict] = []
    seen: list[tuple[str, set]] = []
    for c in chunks:
        fp = _fingerprint(c.get("content") or "")
        sh = _shingles(c.get("content") or "")
        if any(fp == kfp or (fp and fp in kfp) or _jaccard(sh, ksh) >= NEAR_DUP_JACCARD
               for kfp, ksh in seen):
            continue
        seen.append((fp, sh))
        kept.append(c)
    return kept


def find_relevant(
    db: Database, query_text: str, top_k: int = 3, min_overlap: int = 2,
    sector: str | None = None, dept: str | None = None,
) -> list[dict]:
    """검토 대상 텍스트와 관련된 규정 조각 top-k — 운영 검색 경로.

    어휘(Kiwi)·임베딩(KURE) 하이브리드 후보(hybrid_candidates) → 크로스인코더 재정렬.
    zzaimy.eval.retrieval_eval이 같은 구성요소로 품질을 잰다(운영 구성 행).
    """
    candidates = hybrid_candidates(db, query_text, min_overlap, sector, dept)
    if not candidates:
        return []                 # 후보 자체가 없다 = 근거 없음. 억지로 채우지 않는다
    # 크로스인코더 재정렬 + 꼬리 자르기 — 표본 실측 R@1 +0.133 (docs/rerank-baseline.md).
    # 하한을 넘은 것이 하나도 없어도 1위는 남기고 weak_evidence를 붙인다 — 조용히
    # 지우는 대신 "근거가 약하다"고 밝히는 편이 담당자에게 낫다.
    from zzaimy.app.rerank import prune_scored, rerank_scored

    scored = rerank_scored(query_text, candidates)
    if scored is None:            # 리랭커가 없는 환경 — 융합 순위를 그대로 쓴다
        return candidates[:top_k]
    kept, weak = prune_scored(scored)
    # 근거가 약할 때만 질의 확장 — 규정 용어를 모르고 상황으로 물은 질문을 규정 용어 검색어로 풀어
    # 다시 찾는다. 평가(2026-09-20, 바꿔 말한 질의 150): R@5 0.593→0.647, MRR 0.462→0.498,
    # 정확한 질의 150 은 5건만 확장되고 지표 변화 없음. 확장본의 1위가 더 나을 때만 바꾼다.
    if weak:
        from zzaimy.app import query_expand

        if query_expand.configured():
            expanded = query_expand.expand(query_text)
            if expanded != query_text:
                c2 = hybrid_candidates(db, expanded, min_overlap, sector, dept)
                s2 = rerank_scored(query_text, c2) if c2 else None   # 원 질문 기준으로 다시 잰다
                if s2 and s2[0][1] > scored[0][1]:
                    scored = s2
                    kept, weak = prune_scored(scored)
    by_score = dict(zip((id(c) for c, _ in scored), (s for _, s in scored)))
    out = []
    for c in drop_near_duplicates(kept)[:top_k]:
        item = dict(c)
        item["rerank_score"] = round(by_score.get(id(c), 0.0), 4)
        if weak:
            item["weak_evidence"] = True
        out.append(item)
    return out


def suggest_criteria_docs(
    db: Database, masked_text: str, sector: str | None = None,
    dept: str | None = None, top_k: int = 3
) -> list[dict]:
    """검토 대상과 연관성 높은 기준 '문서' 추천 — 조각 점수를 문서로 집계한다."""
    hits = find_relevant(db, masked_text, top_k=12, sector=sector, dept=dept)
    agg: dict[int, dict] = {}
    for rank, h in enumerate(hits):
        d = agg.setdefault(
            h["doc_id"], {"doc_id": h["doc_id"], "title": h["reg_title"], "score": 0.0}
        )
        d["score"] += 1.0 / (rank + 1)  # 상위 조각일수록 가중
    ranked = sorted(agg.values(), key=lambda d: -d["score"])
    return ranked[:top_k]


def compose_review_context(
    db: Database, masked_text: str, sector: str | None = None,
    dept: str | None = None,
) -> str:
    """검토 프롬프트에 붙일 '참고 규정' 블록. 섹터 전용 + 공통 기준만 후보."""
    hits = find_relevant(db, masked_text, sector=sector, dept=dept)
    if not hits:
        return ""
    lines = ["[참고 규정 — 검토 의견에서 관련 조항을 근거로 인용하라]"]
    for h in hits:
        lines.append(f"《{h['reg_title']} · {h['heading']}》\n{h['content'][:600]}")
    return "\n\n".join(lines)
