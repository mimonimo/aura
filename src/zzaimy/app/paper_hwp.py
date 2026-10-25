"""캡스톤 논문을 웹 한글 기안기에서 쓰기(ADR-0072) — 논문 양식(hwpx) 만들기, 논문 문서 하나 관리, 마크다운 원고 → 절 필드 글.

- 양식: 작년 캡스톤 논문집 한 편의 차례(docs/paper/논문-양식-가이드.md)를 따른다. 표지(머리 줄 + PROJECT·제목·팀원·팀명 상자) → 목차
  (1./1.1/1.1.1 세 단계, 점선) → 그림 목차 → 표 목차 → 1~6장 → 참고 문헌 → 초록 → 팀원 소개. A4, 머리말 오른쪽 위 줄인 제목,
  꼬리말 「영남이공대학교 사이버보안과」 와 쪽 번호. 목차 줄은 오른쪽 탭(점선 채움) 뒤에 쪽 번호 — 쪽은 편집 화면이 재서 넣는다(toc_set). 장·절·소절 제목은 글자 체계(house_style.TYPE)의 chapter·h1·h2 로 서로 다르게.
- 쓸 자리: 절마다 제목 바로 아래 문단 하나가 누름틀 「절:<번호> <제목>」(참고 문헌처럼 번호 없는 장은 「절:<제목>」). 제목은 필드 밖이라
  공동 편집에서 칸 단위로 나눠 쓴다(ADR-0070). 장 제목(1. 서론)에는 쓸 자리를 두지 않는다 — 장은 절로 시작한다.
- 차례 정의는 OUTLINE 한 곳. docs/paper/논문/ 에 장 원고(*.md)가 있으면 그 번호 제목에서 차례를 읽는다(outline_for).
- 꾸밈 문단 모양: 단계 목록·팁 상자·그림 자리·그림 캡션·표 캡션·코드를 header 의 이름 붙은 문단 스타일로 둔다(사람은 기안기 스타일 목록에서
  고르고, 서식째 쓰기는 이 스타일을 입힌다). 칸 글 공동 편집은 글만 오가므로 필드 안에서는 글 꼴(「※ 참고:」·「[그림 n-m]」 줄)로 대신한다.
- 문서: 설정 paper:hwp_fid 가 논문 문서 id. 한글 문서함 「공통/논문」 에 두어 교직원·관리자 모두 함께 고친다(공통 양식처럼 잠그지 않는다).
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from zzaimy.export import house_style as hs

SETTING = "paper:hwp_fid"
FOLDER = "논문"
WRITER = "claude"                 # 논문 원고를 쓰는 참여자 아이디(공동 편집 표의 user_id) — 화면 이름은 webhwp.display_name 이 「Claude」
WRITER_NAME = "Claude"
BUILD = 2                         # 만드는 방식이 바뀌면 올린다(문서 메타에 남겨 어느 판 양식으로 만든 문서인지 안다) — 2: 학과 기본값·목차 탭 줄

# 표지·머리말·꼬리말 값 — 표지 상자의 팀명 같은 칸은 기안기에서 고친다(칸 필드). 학과는 표지 머리 줄 칸(칸:학과)의 기본값이자
# 꼬리말·표지 아래 줄의 글(필드 밖) — 이미 만든 문서의 필드 밖 글은 restamp 로 바꾼다
PLACEHOLDER_DEPT = "○○과"
META = {
    "year": "2026",
    "dept": "사이버보안과",
    "title": "AURA: 교내 행정 업무 자동화를 위한 sLLM 모델 개발 및 행정 자동화 에이전트",
    "short": "AURA 교내 행정 업무 자동화 sLLM·에이전트",
    "file": "AURA 캡스톤 논문",
}

# 차례 — (단계, 번호, 제목). 단계 1 = 장, 2 = 절, 3 = 소절. 번호가 빈 1단계는 번호 없는 장(참고 문헌·초록·팀원 소개)
OUTLINE: list[tuple[int, str, str]] = [
    (1, "1", "서론"),
    (2, "1.1", "프로젝트 배경 및 목적"),
    (2, "1.2", "핵심 내용"),
    (2, "1.3", "기대 효과"),
    (2, "1.4", "프로젝트 구성"),
    (1, "2", "관련 연구"),
    (2, "2.1", "모델 서빙과 생성 모델"),
    (3, "2.1.1", "vLLM"),
    (3, "2.1.2", "젯슨 토르와 NVFP4 양자화 서빙"),
    (3, "2.1.3", "Qwen3.8-27B와 Qwen3.5-35B-A3B"),
    (2, "2.2", "문서 판독과 한글 변환"),
    (3, "2.2.1", "MinerU와 OCR"),
    (3, "2.2.2", "kordoc과 자체 hwpx 변환기"),
    (3, "2.2.3", "서식 보존 채우기"),
    (2, "2.3", "검색"),
    (3, "2.3.1", "KURE 임베딩"),
    (3, "2.3.2", "bge 리랭커"),
    (3, "2.3.3", "하이브리드 검색과 RRF"),
    (2, "2.4", "개인정보 보호와 지식 구조"),
    (3, "2.4.1", "Presidio 개인정보 마스킹"),
    (3, "2.4.2", "지식 그래프와 온톨로지(OWL·SHACL)"),
    (2, "2.5", "문서 작성 환경"),
    (3, "2.5.1", "웹 한글 기안기와 칸 단위 공동 편집"),
    (3, "2.5.2", "구글 독스·드라이브 연동"),
    (3, "2.5.3", "SSE"),
    (1, "3", "환경 구축 및 시스템 설계"),
    (2, "3.1", "환경 구축"),
    (3, "3.1.1", "장비 구성과 역할 분담"),
    (3, "3.1.2", "서빙 환경"),
    (2, "3.2", "시스템 아키텍처 설계"),
    (3, "3.2.1", "전체 시스템 구성도 및 데이터 흐름"),
    (3, "3.2.2", "문서 계열과 색인 분리"),
    (3, "3.2.3", "열람 등급과 개인정보 보호"),
    (1, "4", "구현"),
    (2, "4.1", "문서 반입과 판독"),
    (2, "4.2", "검색 학습본 채택"),
    (2, "4.3", "검증기 세 종"),
    (2, "4.4", "학습된 연관 판정"),
    (2, "4.5", "지식 그래프와 외부 참조 관문"),
    (2, "4.6", "한글 완성본 산출"),
    (2, "4.7", "데이터 공방과 학습 데이터"),
    (2, "4.8", "플랫폼 기능"),
    (1, "5", "평가 및 결과"),
    (2, "5.1", "평가 방법"),
    (2, "5.2", "검색 정확도"),
    (2, "5.3", "학습본 모델 비교"),
    (2, "5.4", "서빙 정밀도 비교"),
    (2, "5.5", "실물 검증 사례"),
    (2, "5.6", "대시보드·시연 결과"),
    (1, "6", "결론"),
    (2, "6.1", "연구 요약 및 기여점"),
    (2, "6.2", "결과 평가 및 한계점"),
    (2, "6.3", "향후 연구 방향"),
    (1, "", "참고 문헌"),
    (1, "", "초록"),
    (1, "", "팀원 소개"),
]
BACK = ("참고 문헌", "초록", "팀원 소개")
BACK_OLD = {"초록": ("Abstract",)}          # 예전 이름 — 10-25 국문만으로 바꾸기 전에 만든 논문 문서의 쓸 자리 이름
LISTS = ("그림 목차", "표 목차")       # 쓸 자리가 있는 차례 장 — 원고의 캡션에서 쓰는 쪽(scripts/214)이 채운다
TOC_LEADER = " " + "·" * 20

# 꾸밈 문단 스타일 — 이름 → 모양. pt·bold·color·shade(글자 바탕)·align·indent(왼쪽, 글자 수)·hang(내어쓰기, 글자 수)·before·after(pt)
STYLES = {
    "단계 목록": {"indent": 1, "hang": 1.6},
    "팁 상자": {"pt": 10, "color": "#1F3864", "indent": 0.5, "shade": "#EEF3FA"},
    "그림 자리": {"pt": 10, "color": "#7F7F7F", "align": "CENTER"},
    "그림 캡션": {"pt": 10, "bold": True, "align": "CENTER", "after": 6},
    "표 캡션": {"pt": 10, "bold": True, "align": "CENTER", "after": 6},
    "코드": {"pt": 9.5, "color": "#333333", "indent": 1, "shade": "#F2F2F2", "line": 130},
    "참고 문헌": {"pt": 10, "hang": 2.6, "after": 3},
}
_REF = re.compile(r"^\[\d+\]\s")                         # 참고 문헌 줄 「[12] 저자. 제목. 주소」 — 내어쓰기 모양
# 서식째 쓰기에서 상자로 그리는 갈래의 칸 바탕(1칸 표) — 팁 상자·코드·그림 자리
BOX_FILL = {"팁 상자": "#EEF3FA", "코드": "#F2F2F2", "그림 자리": "none"}


def key_of(num: str, title: str) -> str:
    return f"절:{num} {title}" if num else f"절:{title}"


APPX = "부록"


def level_of(num: str) -> int:
    """번호 → 단계. 1 → 장, 1.1 → 절, 1.1.1 → 소절. 부록은 「부록」 이 장, 「부록 1」 이 절, 「부록 1.1」 이 소절."""
    if num.startswith(APPX):
        core = num[len(APPX):].strip()
        return 1 if not core else core.count(".") + 2
    return num.count(".") + 1


def heading_text(num: str, title: str, level: int) -> str:
    """문서에 보일 제목 — 「1. 서론」(장)·「1.1 배경」(절)·「부록. 사용 설명서」·「부록 1. 한눈에 보기」·부록 소절은 원고대로 「1.1 …」."""
    if not num:
        return title
    if num.startswith(APPX):
        core = num[len(APPX):].strip()
        if not core or "." not in core:
            return f"{num}. {title}"
        return f"{core} {title}"
    return f"{num}. {title}" if level == 1 else f"{num} {title}"


# ── 차례 ──
_HEAD = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_NUM = re.compile(r"^(\d+(?:\.\d+){0,3})\.?\s+(.+)$")
_APPX = re.compile(rf"^({APPX}(?:\s*\d+(?:\.\d+)*)?)\s*\.?\s+(.+)$")


def parse_heading(text: str) -> tuple[str, str]:
    """「1.1 프로젝트 배경」·「2. 관련 연구」·「부록 1. 한눈에 보기」 → (번호, 제목). 번호가 없으면 ("", 글)."""
    t = re.sub(r"[*_`]", "", text).strip()
    m = _APPX.match(t) or _NUM.match(t)
    if not m:
        return "", t
    num = re.sub(r"\s+", " ", m.group(1)).strip()
    if num.startswith(APPX) and num != APPX and not num[len(APPX):].startswith(" "):
        num = APPX + " " + num[len(APPX):]
    return num, m.group(2).strip()


def headings(text: str):
    """원고의 제목 줄을 차례로 — (줄 번호, 갈래, 번호, 제목, 단계). 갈래:
    sec(번호 절, 3단계까지 — 쓸 자리), back(참고 문헌·초록·팀원 소개), sub(그보다 깊거나 번호 없는 소제목 — 본문 안 줄),
    note(번호 없는 장·절 — 「이 장의 그림·표 자리」 같은 원고 메모). 부록 장(「# 부록. …」) 안의 숫자 번호는 「부록 」 을 붙여 본문 장과 겹치지 않게."""
    fence = False
    appx = False
    in_back = False
    for i, ln in enumerate(text.splitlines()):
        if ln.strip().startswith("```"):
            fence = not fence
            continue
        m = None if fence else _HEAD.match(ln)
        if not m:
            continue
        depth = len(m.group(1))
        num, title = parse_heading(m.group(2))
        if num.startswith(APPX):
            appx = True
        elif num and depth == 1:
            appx = False
        elif num and appx:
            num = f"{APPX} {num}" if depth >= 3 else num
        if not num:
            if depth == 1 and title in BACK:
                appx, in_back = False, True
                yield i, "back", "", title, 1
            elif depth <= 2 and not in_back:
                if depth == 1:
                    appx = False
                yield i, "note", "", title, 0
            else:
                yield i, "sub", "", title, 0
            continue
        in_back = False
        lv = level_of(num)
        yield i, ("sec" if lv <= 3 else "sub"), num, title, lv


def outline_from_markdown(texts: list[str]) -> list[tuple[int, str, str]]:
    """장 원고들의 번호 제목 → 차례(sec·back 만). 같은 이름은 한 번만."""
    out: list[tuple[int, str, str]] = []
    seen = set()
    for text in texts:
        for _, kind, num, title, lv in headings(text):
            if kind not in ("sec", "back"):
                continue
            k = key_of(num, title)
            if k not in seen:
                seen.add(k)
                out.append((lv, num, title))
    return out


def chapter_files(src: Path) -> list[Path]:
    """원고 폴더의 장 파일 — README 는 목록이라 뺀다. 이름 차례(01-서론.md …, 부록은 맨 뒤)."""
    if not src or not src.is_dir():
        return []
    return sorted((p for p in src.glob("*.md") if p.name.lower() != "readme.md"), key=lambda p: (p.name.startswith(APPX), p.name))


def outline_for(src: Path | None) -> list[tuple[int, str, str]]:
    """원고가 있고 장이 셋 이상 잡히면 원고 차례, 아니면 OUTLINE. 원고가 아직 없는 장은 OUTLINE 의 그 장으로 채우고(장 번호 차례),
    뒤 장(참고 문헌·초록·팀원 소개)은 양식 가이드 차례(BACK)로, 부록은 그 뒤에 둔다."""
    files = chapter_files(src) if src else []
    got = outline_from_markdown([p.read_text(encoding="utf-8") for p in files]) if files else []
    if sum(1 for lv, n, _ in got if lv == 1 and n and n[0].isdigit()) < 3:
        return list(OUTLINE)

    def groups(items):
        out: dict[str, list] = {}
        for lv, n, t in items:
            if n:
                out.setdefault(APPX if n.startswith(APPX) else n.split(".")[0], []).append((lv, n, t))
        return out
    mine, dflt = groups(got), groups(OUTLINE)
    merged: list[tuple[int, str, str]] = []
    for ch in sorted((c for c in set(mine) | set(dflt) if c != APPX), key=int):
        g = mine.get(ch) or dflt[ch]
        if not any(lv == 1 for lv, _, _ in g):            # 장 제목 줄 없이 절만 쓴 원고 — 장 제목은 OUTLINE 에서
            head = next((x for x in dflt.get(ch, []) if x[0] == 1), (1, ch, f"{ch}장"))
            g = [head, *g]
        merged += g
    merged += [(1, "", t) for t in BACK]
    if mine.get(APPX):
        g = mine[APPX]
        if not any(lv == 1 for lv, _, _ in g):
            g = [(1, APPX, "사용 설명서"), *g]
        merged += g
    return merged


def _own_text(texts: list[str]) -> dict[str, bool]:
    """원고의 절마다 제 글(아래 절 말고, 원고 메모 주석 빼고)이 있나 — {필드 이름: 있음}."""
    out: dict[str, bool] = {}
    for text in texts:
        for s_ in split_sections(text):
            if s_.get("note"):
                continue
            body = re.sub(r"<!--.*?-->", "", s_["body"], flags=re.S).strip()
            k = key_of(s_["num"], s_["title"])
            out[k] = out.get(k, False) or bool(body)
    return out


def headless(outline: list[tuple[int, str, str]], texts: list[str]) -> set[str]:
    """쓸 자리를 두지 않을 절 — 차례에 아래 절이 있고, 원고에 그 절 제 글이 없는 절(장 제목처럼 제목만 보인다).
    원고가 아직 없는 장의 절은 쓸 자리를 남긴다(나중에 원고가 생기면 쓴다)."""
    own = _own_text(texts)
    nums = [n for lv, n, _ in outline if n]
    out = set()
    for lv, num, title in outline:
        if lv < 2 or not num:
            continue
        k = key_of(num, title)
        if k in own and not own[k] and any(n.startswith(num + ".") for n in nums):
            out.add(k)
    return out


# ── 양식 만들기 ──
def _marker(name: str) -> str:
    return f"⟦{name}⟧"


def build(outline: list[tuple[int, str, str]] | None = None, meta: dict | None = None, no_body: set[str] | None = None) -> bytes:
    """차례 → 논문 양식 hwpx 바이트(쓸 자리 누름틀·꾸밈 스타일·머리말·꼬리말·쪽 번호까지).
    no_body(필드 이름)는 제목만 두고 쓸 자리를 두지 않는다 — 아래 절만 있고 제 글이 없는 절(1.4 처럼, headless)."""
    no_body = no_body or set()
    from hwpx import HwpxDocument

    from zzaimy.app import webhwp_style
    outline = list(outline or OUTLINE)
    meta = {**META, **(meta or {})}
    doc = HwpxDocument.new()
    plan: list[tuple[str, str]] = []            # 표 밖 문단의 (글, TYPE 갈래) — 문서 차례대로
    cell_kinds: dict[str, str] = {}             # 표지 상자 칸 문단의 표식 → TYPE 갈래
    state = {"page": False}

    def para(text: str, kind: str = "", page: bool = False):
        p = doc.add_paragraph(text, style="바탕글")
        if page or state["page"]:
            p.element.set("pageBreak", "1")
            state["page"] = False
        if kind and text.strip():
            plan.append((text, kind))
        return p

    # 표지 — 머리 줄, 상자(PROJECT _ n · 제목 · 팀원 [팀명]), 아래쪽 학교·학과
    for _ in range(6):
        para("")
    para(f"{meta['year']} {_marker('칸:학과')} 캡스톤디자인 졸업프로젝트 논문", "cover_sub")
    para("")
    para("")
    box = doc.add_table(3, 1, width=42520)
    rows = [f"PROJECT _ {_marker('칸:프로젝트 번호')}", _marker("칸:논문 제목"), f"{_marker('칸:팀원')} [{_marker('칸:팀명')}]"]
    for i, t in enumerate(rows):
        box.set_cell_text(i, 0, t)
    cell_kinds.update({rows[0]: "cover_sub", rows[1]: "cover_title", rows[2]: "cover_date"})
    for _ in range(14):
        para("")
    para(f"영남이공대학교 {meta['dept']}", "cover_org")

    # 목차 — 세 단계, 단계마다 한 글자 들여쓰기, 줄 끝 점선(쪽 번호는 기안기에서 쪽이 정해진 뒤)
    para("목 차", "toc_title", page=True)
    para("")
    for lv, num, title in outline:
        line = ("    " * (lv - 1)) + heading_text(num, title, lv) + TOC_LEADER
        para(line, "toc")
    for name in LISTS:                          # 그림 목차·표 목차 — 쪽마다 제목 + 쓸 자리(원고 캡션에서 채운다)
        para(name, "toc_title", page=True)
        para("")
        para(_marker(f"절:{name}"))
    for lv, num, title in outline:
        text = heading_text(num, title, lv)
        if lv == 1:
            para(text, "chapter", page=True)
            para("")
            if not num:                         # 번호 없는 장(참고 문헌·초록·팀원 소개)은 장 자체가 쓸 자리
                para(_marker(key_of(num, title)))
            continue
        para("")
        para(text, "h1" if lv == 2 else "h2")
        if key_of(num, title) not in no_body:
            para(_marker(key_of(num, title)))
    data = webhwp_style.body_base(doc.to_bytes())
    data = webhwp_style.apply(data, per_table={1: {"head_rows": 0, "labels_only": True, "row_height": 2400}})
    data = webhwp_style.dress(data, plan)
    data = _dress_cells(data, cell_kinds)
    data, _ = add_styles(data)
    data = _fieldize(data, meta)
    data = _page_frame(data, meta)
    return page_numbers(toc_set(data, {}))[0]


def _dress_cells(data: bytes, kinds: dict[str, str]) -> bytes:
    """표 칸 안 문단(표지 상자)에 TYPE 갈래 모양 — dress 는 표 밖 문단만 짚는다."""
    from zzaimy.app import webhwp_style

    def fn(xml: str, hdr, _n: str) -> str:
        def sub(m: re.Match) -> str:
            p = m.group(0)
            kind = kinds.get(webhwp_style._text(p))
            if not kind:
                return p
            cp = re.search(r'charPrIDRef="(\d+)"', p).group(1)
            pp = re.search(r'paraPrIDRef="(\d+)"', p).group(1)
            c, q = webhwp_style.type_ids(hdr, kind, cp, pp)
            p = re.sub(r'(<hp:run\b[^>]*\bcharPrIDRef=")\d+', rf"\g<1>{c}", p)
            return re.sub(r'(<hp:p\b[^>]*\bparaPrIDRef=")\d+', rf"\g<1>{q}", p, count=1)
        xml = re.sub(r"<hp:p\b[^>]*>(?:(?!<hp:p\b).)*?</hp:p>", sub, xml, flags=re.S)

        def box(m: re.Match) -> str:                  # 표지 상자 — 바깥 선만(굵게), 칸 사이 선 없음
            t = m.group(0)
            if _marker("칸:논문 제목") not in t:
                return t
            tcs = re.findall(r"<hp:tc\b.*?</hp:tc>", t, re.S)
            for i, c in enumerate(tcs):
                bf = hdr.border("0.4 mm", "0.4 mm", "0.4 mm" if i == 0 else "", "0.4 mm" if i == len(tcs) - 1 else "", "none")
                t = t.replace(c, re.sub(r'(<hp:tc\b[^>]*\bborderFillIDRef=")\d+', rf"\g<1>{bf}", c, count=1), 1)
            return t
        return re.sub(r"<hp:tbl\b.*?</hp:tbl>", box, xml, flags=re.S)
    return webhwp_style._rewrite(data, fn)


_HINTS = {"칸:학과": "학과 이름", "칸:프로젝트 번호": "번호", "칸:논문 제목": "논문 제목", "칸:팀원": "팀원 이름", "칸:팀명": "팀명"}


def _fieldize(data: bytes, meta: dict | None = None) -> bytes:
    """⟦이름⟧ 표식을 누름틀로 — 표식만 든 문단은 그 문단이 쓸 자리, 글 사이 표식은 그 자리에 칸. 논문 제목·학과는 기본 글을 넣어 둔다."""
    from zzaimy.app import webhwp_fields as wf
    meta = {**META, **(meta or {})}
    defaults = {"칸:논문 제목": meta["title"], "칸:학과": meta["dept"]}
    zin = zipfile.ZipFile(io.BytesIO(data))
    secs = {n: zin.read(n).decode("utf-8") for n in zin.namelist() if re.match(r"Contents/section\d+\.xml$", n)}
    ids = wf._Ids(list(secs.values()))
    out = {}
    for n, xml in secs.items():
        def sub(m: re.Match) -> str:
            name = m.group(1)
            hint = _HINTS.get(name, "본문 입력")
            a, b = wf.field_parts(name, hint, ids)
            value = escape(defaults.get(name, ""))
            return f"{a}<hp:t>{value}</hp:t>{b}" if value else f"{a}<hp:t/>{b}"
        y = re.sub(r"⟦([^⟧]+)⟧", lambda m: "</hp:t>" + sub(m) + "<hp:t>", xml)
        y = re.sub(r"<hp:t></hp:t>", "", y)
        out[n] = wf.strip_lineseg_in(y, lambda p: "fieldBegin" in p)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            body = out[info.filename].encode("utf-8") if info.filename in out else zin.read(info.filename)
            z.writestr(info, body, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def _page_frame(data: bytes, meta: dict) -> bytes:
    """A4·머리말(오른쪽 위 줄인 제목, 아래 선)·꼬리말(학교·학과)·쪽 번호(아래 오른쪽). 표지에는 머리말·꼬리말·쪽 번호를 숨긴다."""
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.app import webhwp_style
    data = eo.page_setup(data, {"paper": "A4"})[0] if _paper_not_a4(data) else data
    data = eo.header_footer(data, {"header": meta["short"], "align": "오른쪽"})[0]
    data = eo.header_footer(data, {"footer": f"영남이공대학교 {meta['dept']}", "align": "왼쪽"})[0]
    data = eo.page_number(data, {"position": "아래 오른쪽"})[0]

    def fn(xml: str, hdr, _n: str) -> str:
        xml = re.sub(r'(<hp:visibility\b[^>]*?)hideFirstHeader="0"', r'\1hideFirstHeader="1"', xml, count=1)
        xml = re.sub(r'(<hp:visibility\b[^>]*?)hideFirstFooter="0"', r'\1hideFirstFooter="1"', xml, count=1)
        xml = re.sub(r'(<hp:visibility\b[^>]*?)hideFirstPageNum="0"', r'\1hideFirstPageNum="1"', xml, count=1)
        m = re.search(r"<hp:header\b.*?</hp:header>", xml, re.S)
        if m:                                    # 머리말 글은 작게(9pt, 진회색), 아래 선 하나
            h = m.group(0)
            pm = re.search(r"<hp:p\b[^>]*>", h)
            cp = re.search(r'charPrIDRef="(\d+)"', h).group(1)
            pp = re.search(r'paraPrIDRef="(\d+)"', pm.group(0)).group(1)
            c = hdr.char(cp, pt=9, color="#555555")
            bf = hdr.border("", "", "", "0.12 mm", "none", color="#7F7F7F")
            q = hdr.para_style(pp, border=bf)
            h2 = re.sub(r'(<hp:run\b[^>]*\bcharPrIDRef=")\d+', rf"\g<1>{c}", h)
            h2 = re.sub(r'(<hp:p\b[^>]*\bparaPrIDRef=")\d+', rf"\g<1>{q}", h2, count=1)
            xml = xml.replace(h, h2, 1)
        m = re.search(r"<hp:footer\b.*?</hp:footer>", xml, re.S)
        if m:
            f = m.group(0)
            cp = re.search(r'charPrIDRef="(\d+)"', f).group(1)
            c = hdr.char(cp, pt=9, color="#555555")
            xml = xml.replace(f, re.sub(r'(<hp:run\b[^>]*\bcharPrIDRef=")\d+', rf"\g<1>{c}", f), 1)
        return xml
    return webhwp_style._rewrite(data, fn)


def _paper_not_a4(data: bytes) -> bool:
    x = zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode("utf-8")
    m = re.search(r'<hp:pagePr\b[^>]*\bwidth="(\d+)"[^>]*\bheight="(\d+)"', x)
    return not m or (int(m.group(1)), int(m.group(2))) != (59528, 84186)


# ── 목차 쪽 번호 ── 쪽 나눔은 기안기만 안다(서버는 글자 배치를 하지 않는다). 편집 화면이 칸마다 쪽을 재서 보내면(static/paper-toc.js)
# 목차 줄을 「제목 <오른쪽 탭, 점선 채움> 쪽」 으로 다시 쓴다. 목차 줄은 필드 밖이고, 장 줄(1. 서론)은 그 장 첫 절의 쪽을 쓴다.
TOC_TITLE = "목 차"
_TOC_TAB = '<hp:tab width="0" leader="3" type="2"/>'      # 한글 차례 실물 꼴 — 오른쪽 탭, 채움은 header 의 tabItem(leader="DASH")


def _ptext(p: str) -> str:
    """문단 글 — 탭은 \t, 필드 안내 글은 뺀다."""
    p = re.sub(r"<hp:parameters\b.*?</hp:parameters>", "", p, flags=re.S)
    p = re.sub(r"<hp:tab\b[^>]*/>", "\t", p)
    return re.sub(r"<[^>]+>", "", p).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _toc_lines(xml: str) -> list[tuple[int, int, str, str]]:
    """목차 줄 문단 — (시작, 끝, 들여쓰기, 제목). 「목 차」 제목 문단 다음부터 쪽을 나누는 문단 앞까지, 빈 줄은 뺀다."""
    out: list[tuple[int, int, str, str]] = []
    started = False
    for m in re.finditer(r"<hp:p\b[^>]*>(?:(?!<hp:p\b).)*?</hp:p>", xml, re.S):
        p = m.group(0)
        t = _ptext(p)
        if not started:
            started = t.strip() == TOC_TITLE
            continue
        if 'pageBreak="1"' in p[:p.find(">")]:
            break
        if not t.strip():
            continue
        head = re.sub(r"\t.*$", "", t, flags=re.S)
        head = re.sub(r"\s*·{3,}\s*\d*\s*$", "", head)
        indent = head[:len(head) - len(head.lstrip(" "))]
        out.append((m.start(), m.end(), indent, head.strip()))
    return out


def _field_heads(data: bytes) -> list[tuple[str, str]]:
    """절 필드를 문서 차례로 — (목차에 보일 제목, 필드 이름)."""
    from zzaimy.app import webhwp_write as ww
    out = []
    for f in ww.fields(data):
        name = f["name"]
        if not name.startswith("절:") or name[2:] in LISTS:
            continue
        num, title = parse_heading(name[2:])
        out.append((heading_text(num, title, level_of(num) if num else 1), name))
    return out


def toc_entries(data: bytes) -> list[dict]:
    """목차 줄마다 쪽을 잴 필드 — [{text, field}]. 줄과 절 필드를 차례대로 짝짓고(같은 제목이 부록에 또 있어도 차례로 갈린다),
    필드가 없는 장 줄은 다음 줄의 필드(그 장의 첫 절)로 잰다. 맞는 필드가 끝내 없으면 field 가 빈 글."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    xml = zin.read("Contents/section0.xml").decode("utf-8")
    heads = _field_heads(data)
    k = 0
    out: list[dict] = []
    for _s, _e, _i, text in _toc_lines(xml):
        hit = next((j for j in range(k, len(heads)) if _norm(heads[j][0]) == _norm(text)), None)
        if hit is not None:
            k = hit + 1
        out.append({"text": text, "field": heads[hit][1] if hit is not None else ""})
    for i in range(len(out) - 1, -1, -1):
        if not out[i]["field"] and i + 1 < len(out):
            out[i]["field"] = out[i + 1]["field"]
            out[i]["from_next"] = True
    return out


def toc_set(data: bytes, pages: dict[str, int]) -> bytes:
    """목차 줄을 「들여쓰기 + 제목 + 오른쪽 탭(점선 채움) + 쪽」 으로 — pages 는 {필드 이름: 쪽}(모르는 줄은 쪽 없이 점선만).
    예전 꼴(제목 뒤 「····」)도 이 꼴로 바꾼다. 필드 밖 목차 줄만 고친다."""
    from zzaimy.ingest import hwpx_fill as hf
    entries = toc_entries(data)

    def fn(xml: str, hdr, name: str) -> str:
        if name != "Contents/section0.xml":
            return xml
        lines = _toc_lines(xml)
        if not lines:
            return xml
        width = hf._text_width_hu(xml)
        tab_id = _tab_pr(hdr, width)
        pp_cache: dict[str, str] = {}
        out, pos = [], 0
        for (s_, e_, indent, text), ent in zip(lines, entries):
            p = xml[s_:e_]
            pp = re.search(r'paraPrIDRef="(\d+)"', p).group(1)
            if pp not in pp_cache:
                pp_cache[pp] = _para_with_tab(hdr, pp, tab_id)
            run = re.search(r'<hp:run\b[^>]*\bcharPrIDRef="(\d+)"', p)
            cp = run.group(1) if run else "0"
            n = pages.get(ent.get("field") or "")
            page = str(int(n)) if isinstance(n, (int, float)) or str(n or "").isdigit() else ""
            opener = re.sub(r'paraPrIDRef="\d+"', f'paraPrIDRef="{pp_cache[pp]}"', re.match(r"<hp:p\b[^>]*>", p).group(0), count=1)
            body = f'<hp:run charPrIDRef="{cp}"><hp:t>{escape(indent + text)}{_TOC_TAB}{page}</hp:t></hp:run>'
            out += [xml[pos:s_], opener, body, "</hp:p>"]
            pos = e_
        out.append(xml[pos:])
        return "".join(out)
    from zzaimy.app import webhwp_style
    return webhwp_style._rewrite(data, fn)


_NEW_NUM = '<hp:ctrl><hp:newNum num="1" numType="PAGE"/></hp:ctrl>'


def page_numbers(data: bytes) -> tuple[bytes, int]:
    """작년 논문집처럼 본문 쪽 번호를 「1. 서론」 에서 1 로 — 쪽 번호 매기기(pageNum)를 첫 번호 장 제목 문단으로 옮기고 그 자리에
    쪽 번호 새로(newNum 1)를 둔다. 쪽 번호는 그 컨트롤이 있는 쪽부터 찍히므로 표지·목차·그림 목차·표 목차(쪽 수와 상관없이)에는 번호가 없다.
    필드 안은 건드리지 않는다. (새 바이트, 옮긴 컨트롤 수). 이미 그 꼴이면 그대로. 예전 판의 앞부분 감추기(pageHiding)는 걷는다."""
    from zzaimy.app import webhwp_style
    count = [0]

    def fn(xml: str, _hdr, name: str) -> str:
        if name != "Contents/section0.xml":
            return xml
        paras = list(re.finditer(r"<hp:p\b[^>]*>(?:(?!<hp:p\b).)*?</hp:p>", xml, re.S))
        start = next((i for i, m in enumerate(paras) if _ptext(m.group(0)).strip() == TOC_TITLE), None)
        if start is None:
            return xml
        first = None
        for i in range(start + 1, len(paras)):
            p = paras[i].group(0)
            num, _t = parse_heading(_ptext(p).strip())
            if 'pageBreak="1"' in p[:p.find(">")] and num and num[0].isdigit() and "." not in num and "fieldBegin" not in p:
                first = i
                break
        if first is None:
            return xml
        target = paras[first]
        if "<hp:newNum" in target.group(0) and "<hp:pageNum" in target.group(0):
            return xml
        pn = re.search(r"<hp:ctrl><hp:pageNum\b[^>]*/></hp:ctrl>", xml)
        pn_xml = pn.group(0) if pn else '<hp:ctrl><hp:pageNum pos="BOTTOM_RIGHT" formatType="DIGIT" sideChar=""/></hp:ctrl>'
        p = target.group(0)
        run = re.search(r'<hp:run\b[^>]*\bcharPrIDRef="\d+"[^>]*>', p)
        if not run:
            return xml
        add = ("" if "<hp:newNum" in p else _NEW_NUM) + ("" if "<hp:pageNum" in p else pn_xml)
        q = p[:run.end()] + add + p[run.end():]
        xml = xml[:target.start()] + q + xml[target.end():]
        if pn and pn.start() < target.start():          # 앞쪽(구역 첫 문단)의 쪽 번호 컨트롤은 지운다
            xml = xml[:pn.start()] + xml[pn.end():]
        xml = re.sub(r'<hp:run charPrIDRef="\d+"><hp:ctrl><hp:pageHiding [^>]*/></hp:ctrl></hp:run>', "", xml)
        xml = re.sub(r"<hp:ctrl><hp:pageHiding [^>]*/></hp:ctrl>", "", xml)
        count[0] += 1
        return xml
    out = webhwp_style._rewrite(data, fn)
    return out, count[0]


def renumber(db, base: Path, fid: int, *, user: str = WRITER) -> dict:
    """이미 만든 논문 문서에 page_numbers 를 — 필드 밖만, 앞 판은 .판/ (restamp 와 같은 저장 길)."""
    def change(data: bytes):
        new, n = page_numbers(data)
        return new, f"{n}곳"
    return _save_outside(db, base, fid, change, user=user)


def _tab_pr(hdr, width: int) -> str:
    """오른쪽 탭 하나(글 폭 끝, 점선 채움)인 탭 모양 — 이미 있으면 그 id. 한글 실물 꼴(글자 단위 판은 HWPUNIT, 기본 판은 두 배 값)."""
    for m in re.finditer(r'<hh:tabPr id="(\d+)"[^>/]*>(.*?)</hh:tabPr>', hdr.xml, re.S):
        if f'pos="{width}" type="RIGHT" leader="DASH" unit="HWPUNIT"' in m.group(2):
            return m.group(1)
    ids = [int(x) for x in re.findall(r'<hh:tabPr id="(\d+)"', hdr.xml)]
    tid = str(max(ids or [0]) + 1)
    body = (f'<hh:tabPr id="{tid}" autoTabLeft="0" autoTabRight="0"><hp:switch>'
            f'<hp:case hp:required-namespace="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar">'
            f'<hh:tabItem pos="{width}" type="RIGHT" leader="DASH" unit="HWPUNIT"/></hp:case>'
            f'<hp:default><hh:tabItem pos="{width * 2}" type="RIGHT" leader="DASH"/></hp:default></hp:switch></hh:tabPr>')
    close = hdr.xml.find("</hh:tabProperties>")
    hdr.xml = hdr.xml[:close] + body + hdr.xml[close:]
    n = len(re.findall(r"<hh:tabPr\b", hdr.xml))
    hdr.xml = re.sub(r'(<hh:tabProperties\b[^>]*\bitemCnt=")\d+', rf"\g<1>{n}", hdr.xml, count=1)
    return tid


def _para_with_tab(hdr, pp: str, tab_id: str) -> str:
    """문단 모양 pp 를 그 탭 모양으로 — 같은 것이 있으면 그 id."""
    m = re.search(rf'<hh:paraPr\b[^>]*?\bid="{pp}"[^>]*>.*?</hh:paraPr>', hdr.xml, re.S)
    if not m:
        return pp
    body = m.group(0)
    if re.search(rf'\btabPrIDRef="{tab_id}"', body):
        return pp
    body = re.sub(r'\btabPrIDRef="\d+"', f'tabPrIDRef="{tab_id}"', body, count=1)
    body = re.sub(r'\bid="\d+"', f'id="{hdr._next_id("paraPr")}"', body, count=1)
    return hdr._append("paraProperties", body)


# ── 꾸밈 문단 스타일 ──
def add_styles(data: bytes) -> tuple[bytes, dict[str, dict]]:
    """header 에 STYLES 의 이름 붙은 문단 스타일을 둔다(이미 있으면 그대로) — (새 바이트, {이름: {style, para, char}})."""
    from zzaimy.app import webhwp_style
    from zzaimy.app import webhwp_write as ww
    zin = zipfile.ZipFile(io.BytesIO(data))
    header = zin.read("Contents/header.xml").decode("utf-8")
    base = re.search(r'<hh:style\b[^>]*\bid="0"[^>]*>', header).group(0)
    pp0 = re.search(r'paraPrIDRef="(\d+)"', base).group(1)
    cp0 = re.search(r'charPrIDRef="(\d+)"', base).group(1)
    have = dict(re.findall(r'<hh:style\b[^>]*\bid="(\d+)"[^>]*\bname="([^"]+)"', header))
    have = {v: k for k, v in have.items()}
    ch = int(hs.TYPE["body"]["pt"] * 100)
    out: dict[str, dict] = {}
    for name, st in STYLES.items():
        if name in have:
            tag = re.search(rf'<hh:style\b[^>]*\bid="{have[name]}"[^>]*>', header).group(0)
            out[name] = {"style": have[name], "para": re.search(r'paraPrIDRef="(\d+)"', tag).group(1),
                         "char": re.search(r'charPrIDRef="(\d+)"', tag).group(1)}
            continue
        pp = pp0
        if st.get("indent") or st.get("hang"):
            lv = ww._LevelStyles(header, pp0, ch)
            pp = lv.pp(int(ch * float(st.get("indent") or 0)), int(ch * float(st.get("hang") or 0)))
            header = lv.header
        hdr = webhwp_style._Header(header)
        cp = hdr.char(cp0, bold=st.get("bold"), pt=st.get("pt"), color=st.get("color", ""))
        if st.get("shade"):
            body = re.search(rf'<hh:charPr\b[^>]*?\bid="{cp}"[^>]*>.*?</hh:charPr>', hdr.xml, re.S).group(0)
            nid = str(hdr._next_id("charPr"))
            body2 = re.sub(r'\bshadeColor="[^"]*"', f'shadeColor="{st["shade"]}"', re.sub(r'\bid="\d+"', f'id="{nid}"', body, count=1), count=1)
            cp = hdr._append("charProperties", body2)
        pp = hdr.para_style(pp, align=st.get("align", ""), line=st.get("line"), before=st.get("before"), after=st.get("after"))
        header = hdr.xml
        ids = [int(x) for x in re.findall(r'<hh:style\b[^>]*\bid="(\d+)"', header)]
        sid = str(max(ids) + 1)
        tag = (f'<hh:style id="{sid}" type="PARA" name="{name}" engName="" paraPrIDRef="{pp}" charPrIDRef="{cp}" '
               f'nextStyleIDRef="0" langID="1042" lockForm="0"/>')
        close = header.find("</hh:styles>")
        header = header[:close] + tag + header[close:]
        n = len(re.findall(r"<hh:style\b", header))
        header = re.sub(r'(<hh:styles\b[^>]*\bitemCnt=")\d+', rf"\g<1>{n}", header, count=1)
        out[name] = {"style": sid, "para": pp, "char": cp}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            body = header.encode("utf-8") if info.filename == "Contents/header.xml" else zin.read(info.filename)
            z.writestr(info, body, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
    return buf.getvalue(), out


def styles_in(data: bytes) -> dict[str, dict]:
    """문서 header 에 있는 꾸밈 스타일 — {이름: {style, para, char}}(없는 것은 빠진다)."""
    h = zipfile.ZipFile(io.BytesIO(data)).read("Contents/header.xml").decode("utf-8")
    out = {}
    for tag in re.findall(r"<hh:style\b[^>]*>", h):
        name = re.search(r'\bname="([^"]*)"', tag).group(1)
        if name in STYLES:
            out[name] = {"style": re.search(r'\bid="(\d+)"', tag).group(1), "para": re.search(r'paraPrIDRef="(\d+)"', tag).group(1),
                         "char": re.search(r'charPrIDRef="(\d+)"', tag).group(1)}
    return out


# ── 논문 문서 ──
def current(db) -> dict | None:
    """지금 논문 문서(장부 줄) — 없거나 파일이 사라졌으면 None."""
    from zzaimy.app import webhwp
    try:
        fid = int(db.get_setting(SETTING, "") or 0)
    except (TypeError, ValueError):
        fid = 0
    f = webhwp.get(db, fid) if fid else None
    return f if f and Path(f["path"]).exists() else None


def create(db, base: Path, owner: str, src: Path | None = None) -> tuple[int, bool]:
    """논문 문서를 만든다(이미 있으면 그것) — (문서 id, 새로 만들었나). 자리는 한글 문서함 「공통/논문」."""
    from zzaimy.app import webhwp
    f = current(db)
    if f:
        return int(f["id"]), False
    outline = outline_for(src)
    data = build(outline, no_body=headless(outline, [p.read_text(encoding="utf-8") for p in chapter_files(src)] if src else []))
    fid = webhwp.add(db, base, webhwp.COMMON, f"{META['file']}.hwpx", data, owner=owner, source="논문", folder=FOLDER)
    m = webhwp.meta(db, fid)
    m.update(paper={"build": BUILD, "sections": len(outline)})
    db.set_setting(f"webhwp_meta:{fid}", json.dumps(m, ensure_ascii=False))
    db.set_setting(SETTING, str(fid))
    return fid, True


# ── 이미 만든 문서 고치기(필드 밖만) ── 표지 아래 줄·꼬리말의 학과, 목차 쪽 번호. 칸 글(사람이 쓴 것)은 건드리지 않는다
def _outside_fields(xml: str) -> list[tuple[int, int]]:
    """필드 밖 구간 — 누름틀 시작부터 그 짝 끝까지를 뺀 나머지 (시작, 끝) 목록."""
    inside: list[tuple[int, int]] = []
    for m in re.finditer(r'<hp:fieldBegin\b[^>]*\bid="(\d+)"', xml):
        e = re.search(rf'<hp:fieldEnd\b[^>]*\bbeginIDRef="{m.group(1)}"[^>]*/>', xml[m.end():])
        if e:
            inside.append((m.start(), m.end() + e.end()))
    out, pos = [], 0
    for a, b in sorted(inside):
        if a > pos:
            out.append((pos, a))
        pos = max(pos, b)
    out.append((pos, len(xml)))
    return out


def stamp_dept(data: bytes, old: list[str], new: str) -> tuple[bytes, int]:
    """필드 밖 글(표지 아래 줄·꼬리말·머리말 등)의 학과 이름 old → new — (새 바이트, 바꾼 곳 수). 글 조각(<hp:t>) 안만 본다."""
    from zzaimy.app import webhwp_style
    olds = [escape(o) for o in old if o and o != new]
    count = [0]

    def fn(xml: str, _hdr, _n: str) -> str:
        if not olds:
            return xml
        parts, pos = [], 0
        for a, b in _outside_fields(xml):
            parts.append(xml[pos:a])
            seg = xml[a:b]

            def sub(m: re.Match) -> str:
                t = m.group(2)
                for o in olds:
                    if o in t:
                        count[0] += t.count(o)
                        t = t.replace(o, escape(new))
                return m.group(1) + t + m.group(3)
            parts.append(re.sub(r"(<hp:t>)([^<]*)(</hp:t>)", sub, seg))
            pos = b
        parts.append(xml[pos:])
        return "".join(parts)
    out = webhwp_style._rewrite(data, fn)
    return out, count[0]


def _save_outside(db, base: Path, fid: int, change, *, user: str, removable: set[str] | None = None) -> dict:
    """필드 밖 고치기 공용 — 칸 글을 먼저 파일에 써 넣고(commit_now) → 고치고 → 점검(파일 구조·필드 값이 그대로인지) →
    앞 판을 .판/ 에 남기고 저장. 저장 알림(「문서 저장」)으로 열린 편집 화면이 새 판을 불러온다. 구조 잠금을 쥔 사람이 있으면 쓰지 않는다.
    change(data) → (새 바이트, 알릴 말)."""
    from zzaimy.app import webhwp, webhwp_coedit as ce
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.app import webhwp_write as ww
    who = webhwp.lock_holder(db, fid).get("user", "")
    if who:
        return {"ok": False, "why": "struct", "by": who}
    ce.commit_now(db, base, fid)
    with ce.file_lock(fid):
        f = webhwp.get(db, fid)
        data = Path(f["path"]).read_bytes()
        new, note = change(data)
        if new == data:
            return {"ok": True, "changed": False, "note": note}
        probs = eo.check_bytes(new)
        if probs:
            return {"ok": False, "why": "check", "by": "; ".join(probs[:2])}
        before = ww.field_values(data)
        gone = set(before) - set(ww.field_values(new))
        guide = _guides(data)
        if gone - (removable or set()) or any(before[k].strip() not in ("", guide.get(k, "")) for k in gone) \
                or ww.field_values(new) != {k: v for k, v in before.items() if k not in gone}:
            return {"ok": False, "why": "fields", "by": ""}         # 지워도 되는 빈 칸 말고는 칸 글이 그대로여야 한다
        eo._snapshot(db, base, fid)                     # 고치기 전 판은 꼭 남긴다(save 의 10분 간격 규칙과 상관없이)
        webhwp.save(db, base, fid, new, user=user)
    return {"ok": True, "changed": True, "note": note}


def restamp(db, base: Path, fid: int, dept: str | None = None, *, user: str = WRITER) -> dict:
    """이미 만든 논문 문서의 학과 글(필드 밖 — 표지 아래 「영남이공대학교 …」·꼬리말)을 dept(기본 META) 로 바꾼다.
    예전 값은 문서 메타 paper.dept(없으면 자리표시 「○○과」). 표지 머리 줄의 학과는 칸(칸:학과)이라 여기서 바꾸지 않는다 — 칸 글은 공동 편집 길로."""
    from zzaimy.app import webhwp
    dept = (dept or META["dept"]).strip()
    m = webhwp.meta(db, fid)
    paper = m.get("paper") if isinstance(m.get("paper"), dict) else {}
    old = [x for x in {paper.get("dept") or "", PLACEHOLDER_DEPT} if x]
    def change(data: bytes):
        new, n = stamp_dept(data, old, dept)
        return new, f"{n}곳"
    r = _save_outside(db, base, fid, change, user=user)
    if r.get("ok"):
        m = webhwp.meta(db, fid)
        m["paper"] = {**(m.get("paper") if isinstance(m.get("paper"), dict) else {}), "dept": dept}
        db.set_setting(f"webhwp_meta:{int(fid)}", json.dumps(m, ensure_ascii=False))
    return r


def rename_back(db, base: Path, fid: int, *, user: str = WRITER) -> dict:
    """예전 이름으로 만든 논문 문서의 뒤 장 이름을 지금 이름으로 — 필드 밖 글(장 제목 줄·목차 줄)만 바꾼다(「Abstract」 → 「초록」).
    쓸 자리(필드) 이름은 그대로 두고 match_field 가 예전 이름도 알아본다(칸 이름을 바꾸면 공동 편집 칸 기록이 끊긴다)."""
    def change(data: bytes):
        total = 0
        for new_name, olds in BACK_OLD.items():
            data, n = stamp_dept(data, list(olds), new_name)
            total += n
        return data, f"{total}곳"
    return _save_outside(db, base, fid, change, user=user)


def _guides(data: bytes) -> dict[str, str]:
    """필드마다 안내 글(누름틀의 Direction, 「본문 입력」 같은 것) — 기안기가 저장한 파일은 빈 칸에 안내 글을 실제 글로 넣어 둔다."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    out: dict[str, str] = {}
    for n in sorted(x for x in zin.namelist() if re.match(r"Contents/section\d+\.xml$", x)):
        xml = zin.read(n).decode("utf-8")
        for m in re.finditer(r'<hp:fieldBegin\b[^>]*\bname="([^"]*)"[^>]*>(.*?)</hp:fieldBegin>', xml, re.S):
            g = re.search(r'<hp:stringParam name="Direction"[^>]*>([^<]*)</hp:stringParam>', m.group(2))
            name = m.group(1).replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
            out.setdefault(name, (g.group(1) if g else "").strip())
    return out


def headless_report(data: bytes, texts: list[str], *, written: dict | None = None, state: dict | None = None) -> tuple[list[str], dict[str, str]]:
    """이미 만든 문서에서 지울 빈 쓸 자리와, 아래 절이 있는데 지우지 않는 절의 까닭 — (지울 필드, {남긴 필드: 까닭}).
    지우는 것: 아래 절 필드가 있고, 원고에 그 절 제목은 있으나 제 글이 없고, 칸 글 표에 글이 없고, 파일 값이 비었거나 안내 글뿐인 절 필드.
    서식째 쓴 절(written)은 지우지 않는다(서식째 쓰면 본문이 필드 뒤에 붙어 필드 값만으로는 비어 보일 수 있다)."""
    from zzaimy.app import webhwp_write as ww
    own = _own_text(texts)
    vals = ww.field_values(data)
    guide = _guides(data)
    names = [n for n in vals if n.startswith("절:") and n[2:] not in LISTS]
    out, kept = [], {}
    for n in names:
        num, _t = parse_heading(n[2:])
        if not num or not any(m.startswith(f"절:{num}.") for m in names):
            continue
        if n not in own:
            kept[n] = "원고에 이 절 제목이 없다"
        elif own[n]:
            kept[n] = "원고에 제 글이 있다"
        elif (written or {}).get(n):
            kept[n] = "서식째 쓴 절이다"
        elif ((state or {}).get(n) or {}).get("text", "").strip():
            kept[n] = "칸 글 표에 글이 있다"
        elif vals[n].strip() not in ("", guide.get(n, "")):
            kept[n] = f"파일 값이 있다: {vals[n].strip()[:30]}"
        else:
            out.append(n)
    return out, kept


def headless_fields(data: bytes, texts: list[str], **kw) -> list[str]:
    return headless_report(data, texts, **kw)[0]


def _drop_field_paras(data: bytes, names: set[str]) -> tuple[bytes, int]:
    """그 필드만 든 문단(필드 밖 글이 없는 문단)을 통째로 지운다 — (새 바이트, 지운 수)."""
    from zzaimy.app import webhwp_style
    count = [0]

    def fn(xml: str, _hdr, _n: str) -> str:
        for name in names:
            m = re.search(rf'<hp:fieldBegin\b[^>]*\bname="{re.escape(escape(name, {chr(34): "&quot;"}))}"', xml)
            if not m:
                continue
            a = xml.rfind("<hp:p ", 0, m.start())
            b = xml.find("</hp:p>", m.end())
            if a < 0 or b < 0:
                continue
            para = xml[a:b + 7]
            beg = para.rfind("<hp:ctrl>", 0, m.start() - a)
            end = re.search(r'<hp:fieldEnd\b[^>]*/></hp:ctrl>', para[m.end() - a:])
            if beg < 0 or not end:
                continue
            fspan = (beg, m.end() - a + end.end())
            rest = para[:fspan[0]] + para[fspan[1]:]
            if "<hp:tbl" in rest or _ptext(rest).strip():
                xml = xml[:a] + rest + xml[b + 7:]           # 필드 밖 글이 있으면 필드(안내 글 포함)만 뗀다
            else:
                xml = xml[:a] + xml[b + 7:]                  # 필드만 든 문단은 통째로
            count[0] += 1
        return xml
    out = webhwp_style._rewrite(data, fn)
    return out, count[0]


def drop_headless(db, base: Path, fid: int, texts: list[str], *, user: str = WRITER) -> dict:
    """이미 만든 문서의 빈 쓸 자리(아래 절만 있고 제 글이 없는 절, 화면에 붉은 「본문 입력」 이 보이던 칸)를 지운다.
    지금 누가 쥐었거나 칸 글 표에 글이 있는 칸은 두고, 앞 판은 .판/ 에 남긴다(restamp 와 같은 저장 길)."""
    from zzaimy.app import webhwp, webhwp_coedit as ce
    ce.commit_now(db, base, fid)
    data = Path(webhwp.get(db, fid)["path"]).read_bytes()
    m = webhwp.meta(db, fid)
    names = {n for n in headless_fields(data, texts, written=m.get("written") if isinstance(m.get("written"), dict) else {},
                                        state=ce.state(db, fid)) if not ce.holder(db, fid, n)}

    def change(d: bytes):
        new, n = _drop_field_paras(d, names)
        return new, f"{n}곳"
    r = _save_outside(db, base, fid, change, user=user, removable=names)
    r["fields"] = sorted(names)
    return r


def toc_refresh(db, base: Path, fid: int, pages: dict, *, user: str) -> dict:
    """편집 화면이 잰 쪽({필드: 쪽})으로 목차 줄의 쪽 번호를 다시 쓴다. 바뀐 줄 수를 note 로."""
    clean = {str(k)[:200]: int(v) for k, v in (pages or {}).items() if str(v).isdigit() and 0 < int(v) < 10000}

    def change(data: bytes):
        new = toc_set(data, clean)
        n = sum(1 for e in toc_entries(new) if e["field"] in clean)
        return new, f"{n}줄"
    return _save_outside(db, base, fid, change, user=user)


def is_paper(db, fid: int) -> bool:
    from zzaimy.app import webhwp
    return isinstance(webhwp.meta(db, fid).get("paper"), dict)


# ── 원고(마크다운) → 절 ──
def split_sections(text: str) -> list[dict]:
    """원고 하나 → [{num, title, level, body, note}] — sec·back 제목 하나가 한 덩이, note 제목(원고 메모)도 덩이로 두되 note=True.
    sub 제목은 덩이 안의 소제목 줄(「\\0sub 」 표식, blocks 가 sub 덩이로)."""
    lines = text.splitlines()
    heads = {i: (kind, num, title, lv) for i, kind, num, title, lv in headings(text)}
    out: list[dict] = []
    cur = None
    for i, ln in enumerate(lines):
        h = heads.get(i)
        if h and h[0] in ("sec", "back", "note"):
            cur = {"num": h[1], "title": h[2], "level": h[3], "body": [], "note": h[0] == "note"}
            out.append(cur)
            continue
        if h:
            if cur is not None:
                cur["body"].append("\u0000sub " + (heading_text(h[1], h[2], 3) if h[1] else h[2]))
            continue
        if cur is not None:
            cur["body"].append(ln)
    for s_ in out:
        s_["body"] = "\n".join(s_["body"]).strip("\n")
    return out


_IMG = re.compile(r"^!\[([^\]]*)\]\(([^)]*)\)\s*$")
_CAP = re.compile(r"^\s*\[(그림|표)\s*(\d+-\d+|부록-\d+)?\]\s*(.*)$")
_OL = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_UL = re.compile(r"^\s*[-*+]\s+(.*)$")


def _inline(t: str) -> str:
    """줄 안 마크다운 꾸밈을 걷는다 — 굵게·기울임·코드 따옴표, 링크는 「글(주소)」."""
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1(\2)", t)
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    return t.strip()


_SENT = re.compile(r"^(은|는|이|가|을|를|의|과|와|에서|에|로|으로|처럼|까지|도)(\s|$)")
_PAREN_SRC = re.compile(r"\s*\((캡처[^:：)]*|작성 필요|그림 자리|그림(?=\s+docs/))\s*[:：]?\s*([^)]*)\)\s*$")


def _caption_line(s: str):
    """캡션 줄 「[그림 1-2] …」·「[표 5-1] …」 — (갈래, 번호, 나머지). 「[표 3-1] 은 …」 처럼 문장 속 언급이면 None."""
    m = _CAP.match(s)
    if not m or _SENT.match(m.group(3) or ""):
        return None
    return m.group(1), m.group(2) or "", (m.group(3) or "").strip()


def blocks(body: str) -> list[dict]:
    """절 본문 마크다운 → 덩이 목록. 갈래: p(문단)·steps(번호 목록)·bullets(글머리)·tip(> 인용, 「참고:」)·
    figure(![캡션](자리) 또는 「[그림 n-m] 자리: …」 + 「[그림 n-m] 캡션」 줄)·table(| 표 |, 앞이나 뒤의 「[표 n-m] 캡션」 줄)·
    code(``` 블록)·diagram(```도식)·sub(소제목)."""
    out: list[dict] = []
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)     # 원고 메모(HTML 주석)는 문서에 넣지 않는다
    lines = body.splitlines()
    i = 0
    para: list[str] = []
    pending_cap = None                         # 표 바로 앞 캡션 줄 — 다음 표에 붙인다

    def flush():
        if para:
            text = " ".join(_inline(x) for x in para if x.strip())
            tip = re.match(r"^(참고|팁)\s*[:：]\s*", text)              # 「참고: …」 문단은 팁 상자
            out.append({"kind": "tip", "text": text[tip.end():]} if tip else {"kind": "p", "text": text})
            para.clear()

    def next_line(j: int) -> str:
        while j < len(lines) and not lines[j].strip():
            j += 1
        return lines[j].strip() if j < len(lines) else ""
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        cap = _caption_line(s)
        if s.startswith("\u0000sub "):
            flush()
            out.append({"kind": "sub", "text": _inline(s[5:])})
        elif s.startswith("```"):
            flush()
            lang = s[3:].strip()
            j = i + 1
            code = []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                code.append(lines[j])
                j += 1
            kind = "diagram" if re.match(r"(도식|diagram|flow)", lang) else "code"
            out.append({"kind": kind, "text": "\n".join(code).rstrip(), "lang": lang})
            i = j
        elif not s:
            flush()
        elif _IMG.match(s):
            flush()
            m = _IMG.match(s)
            out.append({"kind": "figure", "caption": _inline(m.group(1)), "src": m.group(2).strip(), "no": ""})
        elif cap and cap[0] == "그림":
            flush()
            _, no, rest = cap
            if re.match(r"^자리", rest):                       # 「[그림 5-1] 자리: 경로」 — 그림 자리, 캡션은 다음 줄
                out.append({"kind": "figure", "caption": "", "src": re.sub(r"^자리\s*[:：]?\s*", "", rest).strip(" ()"), "no": no})
            else:
                src = ""
                pm = _PAREN_SRC.search(rest)
                if pm:
                    src, rest = (pm.group(1) + ": " + pm.group(2)).strip(": "), rest[:pm.start()].strip()
                last = out[-1] if out else None
                if last and last["kind"] == "figure" and not last["caption"] and last.get("no", "") in ("", no):
                    last["caption"], last["no"] = _inline(rest), no or last.get("no", "")
                else:
                    out.append({"kind": "figure", "caption": _inline(rest), "src": src, "no": no})
        elif cap and cap[0] == "표" and next_line(i + 1).startswith("|"):
            flush()
            pending_cap = (cap[1], _inline(cap[2]))
        elif cap and cap[0] == "표" and out and out[-1]["kind"] == "table" and not out[-1]["caption"] and not para:
            out[-1]["caption"], out[-1]["no"] = _inline(cap[2]), cap[1]
        elif s.startswith("|") and s.count("|") >= 2:
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                r = lines[i].strip()
                if not re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", r):
                    rows.append([_inline(c) for c in r.strip("|").split("|")])
                i += 1
            no, caption = pending_cap or ("", "")
            pending_cap = None
            out.append({"kind": "table", "rows": rows, "caption": caption, "no": no})
            continue
        elif s.startswith(">"):
            flush()
            tip = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                tip.append(lines[i].strip()[1:].strip())
                i += 1
            t = _inline(" ".join(x for x in tip if x))
            t = re.sub(r"^\[!(TIP|NOTE|INFO|IMPORTANT|WARNING)\]\s*", "", t, flags=re.I)
            t = re.sub(r"^(참고|팁|tip|note)\s*[:：]\s*", "", t, flags=re.I)
            out.append({"kind": "tip", "text": t})
            continue
        elif _OL.match(ln):
            flush()
            items = []
            start = int(_OL.match(ln).group(1))
            while i < len(lines) and (_OL.match(lines[i]) or (items and lines[i].startswith("   ") and lines[i].strip()
                                                              and not _caption_line(lines[i].strip()))):
                m = _OL.match(lines[i])
                if m:
                    items.append(_inline(m.group(2)))
                else:
                    items[-1] += " " + _inline(lines[i])
                i += 1
            out.append({"kind": "steps", "items": items, "start": start})      # 그림을 사이에 둔 목록은 번호를 잇는다
            continue
        elif _UL.match(ln):
            flush()
            items = []
            while i < len(lines) and (_UL.match(lines[i]) or (items and lines[i].startswith("  ") and lines[i].strip()
                                                              and not _caption_line(lines[i].strip()))):
                m = _UL.match(lines[i])
                if m:
                    items.append(_inline(m.group(1)))
                else:
                    items[-1] += " " + _inline(lines[i])
                i += 1
            out.append({"kind": "bullets", "items": items})
            continue
        else:
            para.append(ln)
        i += 1
    flush()
    return out


class Numbering:
    """그림·표 번호 — 장마다 1부터(「[그림 3-2]」). 원고에 번호를 적었으면 그 번호를 쓴다. 쓴 캡션은 목차용으로 모은다."""

    def __init__(self):
        self.next: dict[tuple[str, str], int] = {}
        self.listed: dict[str, list[str]] = {"그림": [], "표": []}

    def take(self, what: str, chapter: str, given: str = "") -> str:
        if given:
            return given
        k = (what, chapter or "0")
        n = self.next.get(k, 0) + 1
        self.next[k] = n
        return f"{chapter or '0'}-{n}"

    def peek(self, what: str, chapter: str, given: str = "") -> str:
        """다음에 take 가 줄 번호(세지 않고 보기만)."""
        return given or f"{chapter or '0'}-{self.next.get((what, chapter or '0'), 0) + 1}"

    def caption(self, what: str, chapter: str, caption: str, given: str = "") -> str:
        line = f"[{what} {self.take(what, chapter, given)}] {caption}".rstrip()
        self.listed[what].append(line)
        return line


def to_text(bl: list[dict], chapter: str, nums: Numbering) -> str:
    """덩이 → 칸 글(공동 편집으로 오가는 글만). 꾸밈은 글 꼴로 — 번호 목록 「1) 」, 글머리 「- 」, 팁 「※ 참고: 」, 그림 자리 「〔그림 자리: …〕」
    + 캡션 「[그림 n-m] …」, 표는 「칸 | 칸」 줄 + 캡션 「[표 n-m] …」, 코드는 네 칸 들여쓰기, 소제목 「■ 」."""
    out: list[str] = []
    for b in bl:
        k = b["kind"]
        if k == "p":
            out.append(b["text"])
        elif k == "sub":
            out.append("■ " + b["text"])
        elif k == "steps":
            out += [f"{i}) {t}" for i, t in enumerate(b["items"], b.get("start", 1))]
        elif k == "bullets":
            out += [f"- {t}" for t in b["items"]]
        elif k == "tip":
            out.append("※ 참고: " + b["text"])
        elif k == "figure":
            out.append(f"〔그림 자리: {b.get('src') or '그림 넣을 곳'}〕")
            if b.get("caption") or b.get("no"):
                out.append(nums.caption("그림", chapter, b.get("caption") or "", b.get("no", "")))
        elif k == "table":
            out += [" | ".join(r) for r in b["rows"]]
            if b.get("caption") or b.get("no"):
                out.append(nums.caption("표", chapter, b.get("caption") or "", b.get("no", "")))
        elif k == "code":
            out += ["    " + x for x in (b["text"].splitlines() or [""])]
        elif k == "diagram":
            out += [x.strip() for x in b["text"].splitlines() if x.strip()]
    return "\n".join(out)


def to_items(bl: list[dict], chapter: str, nums: Numbering, figures: dict[str, list[Path]] | None = None,
             pictures: dict[str, str] | None = None) -> tuple[list[tuple[str, object]], dict[str, str]]:
    """덩이 → 서식째 쓰기 항목(webhwp_write.write_section 의 items)과 꾸밈 짝({글: 스타일 이름}) — 글 문단은 그 글로, 상자(1칸 표)는 칸 글로 짚는다.
    figures({그림 번호: [그림 파일]}, figure_files)에 그 번호의 파일이 있으면 그림 자리 상자 대신 그림 표식 문단(⟦그림 파일 n⟧)을 두고
    pictures 에 {표식: 파일 경로} 를 모은다 — 쓴 뒤 place_pictures 가 표식 문단을 그림으로 바꾼다."""
    items: list[tuple[str, object]] = []
    style: dict[str, str] = {}
    texts: list[str] = []
    figures = figures or {}
    pictures = pictures if pictures is not None else {}

    def flush():
        if texts:
            items.append(("text", "\n".join(texts)))
            texts.clear()
    for b in bl:
        k = b["kind"]
        if k == "p":
            texts.append(b["text"])
            if _REF.match(b["text"]):
                style[b["text"]] = "참고 문헌"
        elif k == "sub":
            texts.append("■ " + b["text"])
        elif k == "steps":
            for i, t in enumerate(b["items"], b.get("start", 1)):
                line = f"{i}) {t}"
                texts.append(line)
                style[line] = "단계 목록"
        elif k == "bullets":
            texts += [f"- {t}" for t in b["items"]]
        elif k == "figure" and figures.get(nums.peek("그림", chapter, b.get("no", ""))):
            flush()
            if not items:                              # 절이 그림으로 시작하면 절 필드는 비워 둔다(표식이 필드 안에 들어가지 않게)
                items.append(("text", ""))
            for path in figures[nums.peek("그림", chapter, b.get("no", ""))]:
                mark = _marker(f"그림 파일 {len(pictures) + 1}")
                pictures[mark] = str(path)
                items.append(("text", mark))
            if b.get("caption") or b.get("no"):
                cap = nums.caption("그림", chapter, b.get("caption") or "", b.get("no", ""))
                texts.append(cap)
                style[cap] = "그림 캡션"
        elif k in ("tip", "code", "figure"):
            flush()
            if k == "tip":
                cell = "참고  " + b["text"]
                name = "팁 상자"
            elif k == "code":
                cell = b["text"]
                name = "코드"
            else:
                cell = f"그림 자리: {b.get('src') or '그림 넣을 곳'}"
                name = "그림 자리"
            items.append(("table", [[cell]]))
            style[_norm(cell)] = name
            if k == "figure" and (b.get("caption") or b.get("no")):
                cap = nums.caption("그림", chapter, b.get("caption") or "", b.get("no", ""))
                texts.append(cap)
                style[cap] = "그림 캡션"
        elif k == "table":
            flush()
            items.append(("table", b["rows"]))
            if b.get("caption") or b.get("no"):
                cap = nums.caption("표", chapter, b.get("caption") or "", b.get("no", ""))
                texts.append(cap)
                style[cap] = "표 캡션"
        elif k == "diagram":
            flush()
            items.append(("diagram", b["text"]))
    flush()
    return items, style


_FIG_ROW = re.compile(r"^\|\s*\[그림\s*([^\]]+?)\]\s*\|[^|]*\|\s*(.+?)\s*\|\s*$")
_IMG_PATH = re.compile(r"(docs/images/[\w./-]+?\.(?:png|jpe?g|gif|bmp))", re.I)


def figure_files(list_md: Path, root: Path) -> dict[str, list[Path]]:
    """그림·표 목록(docs/paper/논문/그림-표-목록.md)의 「자료」 칸이 저장소 그림 파일로 시작하는 그림 — {번호: [파일]}.
    「캡처 필요」·「작성 필요」·「본문 글 상자」 로 시작하면 아직 넣을 그림이 없는 것으로 본다. 「a.jpg + b.jpg」 는 둘 다(뒤 것은 앞 것과 같은 폴더)."""
    out: dict[str, list[Path]] = {}
    if not list_md or not Path(list_md).is_file():
        return out
    for ln in Path(list_md).read_text(encoding="utf-8").splitlines():
        m = _FIG_ROW.match(ln.strip())
        if not m:
            continue
        no, src = m.group(1).strip(), m.group(2).strip()
        first = _IMG_PATH.match(src)
        if not first:
            continue
        paths = [root / first.group(1)]
        for extra in re.findall(r"\+\s*([\w.-]+\.(?:png|jpe?g|gif|bmp))", src[first.end():], re.I):
            paths.append(paths[0].parent / extra)
        paths = [x for x in paths if x.is_file()]
        if paths:
            out[no] = paths
    return out


PIC_MAX_H = 52000          # 그림 높이 상한(HWPUNIT, 약 18cm) — 세로로 긴 화면 캡처가 한 쪽을 넘지 않게


def place_pictures(data: bytes, pids: list[str], pictures: dict[str, bytes]) -> bytes:
    """서식째 쓴 문단(pids) 가운데 그림 표식(⟦그림 파일 n⟧)만 든 문단을 그 그림 문단으로 바꾼다 — 글자처럼 취급하는 그림, 가운데, 글 폭에 맞춤.
    문단 id 는 그대로 두어 다시 쓰면 지워진다. 같은 그림 바이트가 이미 BinData 에 있으면 그것을 다시 쓴다(다시 써도 파일이 불지 않게)."""
    from zzaimy.ingest import hwpx_fill as hf
    if not pictures or not pids:
        return data
    zin = zipfile.ZipFile(io.BytesIO(data))
    names = zin.namelist()
    sec_names = sorted(n for n in names if re.match(r"Contents/section\d+\.xml$", n))
    secs = {n: zin.read(n).decode("utf-8") for n in sec_names}
    hpf_name = next((n for n in names if n.endswith("content.hpf")), None)
    if not hpf_name:
        return data
    hpf = zin.read(hpf_name).decode("utf-8")
    have = {n: zin.read(n) for n in names if n.startswith("BinData/")}
    added: dict[str, bytes] = {}
    from zzaimy.app import webhwp_style
    st = styles_in(data)
    hdr = webhwp_style._Header(zin.read("Contents/header.xml").decode("utf-8"))
    # 그림 문단은 가운데·다음 문단(캡션)과 함께 — 그림과 캡션이 쪽을 사이에 두고 갈리지 않게
    para = hdr.para_style(st["그림 자리"]["para"], keep_next=True) if "그림 자리" in st else None
    ids = [int(x) for x in re.findall(r'<hp:pic id="(\d+)"', "".join(secs.values()))]
    next_pic = [max(ids or [1_000_000]) + 1]

    def bin_for(img: bytes) -> str | None:
        for n, b in {**have, **added}.items():
            if b == img:
                return Path(n).stem
        kind = hf.image_kind(img)
        if not kind:
            return None
        used = set(hf._MANIFEST_ID.findall(hpf)) | {Path(x).stem for x in [*have, *added]}
        k = 1
        while f"image{k}" in used:
            k += 1
        added[f"BinData/image{k}.{kind[0]}"] = img
        return f"image{k}"
    changed = False
    for n, xml in secs.items():
        width = hf._text_width_hu(secs[sec_names[0]])
        for pid in pids:
            m = re.search(rf'<hp:p id="{pid}"[^>]*>(?:(?!<hp:p\b).)*?</hp:p>', xml, re.S)
            if not m:
                continue
            mark = re.sub(r"<[^>]+>", "", m.group(0)).strip()
            img = pictures.get(mark)
            if img is None:
                continue
            bid = bin_for(img)
            if not bid:
                continue
            px = hf._image_px(img)
            org_w, org_h = (px[0] * 75, px[1] * 75) if px else (width, width * 3 // 4)
            w = min(org_w, width)
            h = int(w * org_h / org_w)
            if h > PIC_MAX_H:
                w, h = int(w * PIC_MAX_H / h), PIC_MAX_H
            pp = para or re.search(r'paraPrIDRef="(\d+)"', m.group(0)).group(1)
            run = re.search(r'<hp:run\b[^>]*\bcharPrIDRef="(\d+)"', m.group(0))
            cp = run.group(1) if run else "0"
            px_xml = hf.picture_xml(bid, next_pic[0], w, h, org_w, org_h, pp, cp).replace('<hp:p id="0"', f'<hp:p id="{pid}"', 1)
            next_pic[0] += 1
            xml = xml[:m.start()] + px_xml + xml[m.end():]
            changed = True
        secs[n] = xml
    if not changed:
        return data
    for name, b in added.items():
        mt = hf.image_kind(b)[1]
        hpf = hpf.replace("</opf:manifest>", f'<opf:item id="{Path(name).stem}" href="{name}" media-type="{mt}" isEmbeded="1"/></opf:manifest>', 1)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            if info.filename in secs:
                body = secs[info.filename].encode("utf-8")
            elif info.filename == hpf_name:
                body = hpf.encode("utf-8")
            elif info.filename == "Contents/header.xml":
                body = hdr.xml.encode("utf-8")
            else:
                body = zin.read(info.filename)
            z.writestr(info, body, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
        for name, b in added.items():
            z.writestr(name, b, compress_type=zipfile.ZIP_STORED)
    return buf.getvalue()


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


SHORT_CELL = 8             # 이 글자 폭 이하(한글 한 글자 = 1)만 든 열은 가운데


def dress_tables(data: bytes, pids: list[str]) -> bytes:
    """서식째 쓴 표(두 칸 이상)에 학교 표 모양 — 머리줄(첫 행)은 가운데·굵게·음영, 본문 칸은 왼쪽 정렬(양쪽 정렬이면 좁은 칸 글이 벌어진다).
    한 열의 본문 칸이 모두 짧은 값(SHORT_CELL 이하·수)이면 그 열은 가운데, 모두 금액이면 오른쪽 — 열마다 한 가지로."""
    from zzaimy.app import webhwp_style as ws
    if not pids:
        return data

    def fn(xml: str, hdr, _n: str) -> str:
        for pid in pids:
            m = re.search(rf'<hp:p id="{pid}"[^>]*>', xml)
            if not m:
                continue
            t = re.compile(r"<hp:tbl\b.*?</hp:tbl>", re.S).search(xml, m.end())
            if not t or t.start() - m.end() > 400:
                continue
            tbl = t.group(0)
            if len(ws._cells(tbl)) < 2:
                continue                                  # 1칸 표는 팁·코드·그림 자리 상자(dress_written 이 맡는다)
            tbl2 = ws.style_table(tbl, hdr, {"head_rows": 1, "labels_only": True, "body_align": "LEFT"})
            cells = ws._cells(tbl2)
            heads = ws._head_texts(cells, {(c["r"], c["c"]): ("head" if c["r"] == 0 else "body") for c in cells})
            cols: dict[int, list[str]] = {}
            for c in cells:
                if c["r"] > 0 and c["cs"] == 1 and c["text"].strip():
                    cols.setdefault(c["c"], []).append(c["text"])
            want = {}
            for col, vals in cols.items():
                al = [hs.value_align(v, heads.get(col, "")) for v in vals]
                if all(a == hs.TABLE["num_align"] for a in al) and hs.TABLE["num_align"] != "CENTER":
                    want[col] = hs.TABLE["num_align"]
                elif all(a or hs.text_units(v) <= SHORT_CELL for a, v in zip(al, vals)):
                    want[col] = "CENTER"
            out, pos = [], 0
            for c in cells:
                if c["r"] == 0 or c["cs"] != 1 or c["c"] not in want:
                    continue
                x2 = re.sub(r'(<hp:p\b[^>]*\bparaPrIDRef=")(\d+)', lambda q: q.group(1) + hdr.para_style(q.group(2), align=want[c["c"]]), c["xml"])
                out += [tbl2[pos:c["s"]], x2]
                pos = c["e"]
            out.append(tbl2[pos:])
            xml = xml[:t.start()] + "".join(out) + xml[t.end():]
        return xml
    return ws._rewrite(data, fn)


def dress_written(data: bytes, pids: list[str], style: dict[str, str]) -> bytes:
    """서식째 쓴 문단(pids)에 꾸밈 스타일 — 글 문단은 글로, 1칸 표(팁·코드·그림 자리)는 칸 글로 짚고 칸 바탕도 칠한다.
    표 칸 글의 줄바꿈(코드 여러 줄)은 줄 나눔(lineBreak)으로."""
    from zzaimy.app import webhwp_style
    st = styles_in(data)
    if not st or not pids:
        return data
    want = {_norm(k): v for k, v in style.items()}

    def restyle(p: str, name: str) -> str:
        s = st[name]
        p = re.sub(r'(<hp:p\b[^>]*\bparaPrIDRef=")\d+', rf"\g<1>{s['para']}", p, count=1)
        p = re.sub(r'(<hp:p\b[^>]*\bstyleIDRef=")\d+', rf"\g<1>{s['style']}", p, count=1)
        p = re.sub(r'(<hp:run\b[^>]*\bcharPrIDRef=")\d+', rf"\g<1>{s['char']}", p)
        return re.sub(r"<hp:linesegarray>.*?</hp:linesegarray>", "", p, flags=re.S)

    def fn(xml: str, hdr, _n: str) -> str:
        for pid in pids:
            m = re.search(rf'<hp:p id="{pid}"[^>]*>', xml)
            if not m:
                continue
            depth, end = 0, None
            for t in re.finditer(r"<hp:p\b|</hp:p>", xml[m.start():]):
                depth += 1 if t.group(0) != "</hp:p>" else -1
                if depth == 0:
                    end = m.start() + t.end()
                    break
            if end is None:
                continue
            frag = xml[m.start():end]
            if "<hp:tbl" in frag:
                cells = re.findall(r"<hp:tc\b.*?</hp:tc>", frag, re.S)
                if len(cells) != 1:
                    continue
                raw = "".join(re.findall(r"<hp:t>([^<]*)</hp:t>", cells[0])).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
                name = want.get(_norm(raw))
                if name not in st:
                    continue
                c = cells[0]
                c2 = re.sub(r"<hp:t>([^<]*)</hp:t>", lambda t: "<hp:t>" + t.group(1).replace("\n", "</hp:t><hp:lineBreak/><hp:t>") + "</hp:t>", c)
                c2 = re.sub(r"<hp:p\b[^>]*>(?:(?!<hp:p\b).)*?</hp:p>", lambda q: restyle(q.group(0), name), c2, flags=re.S)
                fill = BOX_FILL.get(name, "none")
                line = "0.12 mm"
                bf = hdr.border(line, line, line, line, fill, "DASH" if name == "그림 자리" else "SOLID",
                                color="#9FB3D1" if name == "팁 상자" else "#BFBFBF")
                c2 = re.sub(r'(<hp:tc\b[^>]*\bborderFillIDRef=")\d+', rf"\g<1>{bf}", c2, count=1)
                frag2 = frag.replace(c, c2, 1)
            else:
                name = want.get(_norm(webhwp_style._text(frag)))
                if name not in st:
                    continue
                frag2 = restyle(frag, name)
            xml = xml[:m.start()] + frag2 + xml[end:]
        return xml
    return webhwp_style._rewrite(data, fn)


# ── 원고 쓰기(Claude 참여자) ── 사람·AURA 와 같은 공동 편집 길(임대 → 칸 글 → 놓기 → 한글 파일 써 넣기)
CLIENT = "claude-writer"


def _similar(a: str, b: str) -> bool:
    import difflib
    a, b = re.sub(r"\s+", "", a).lower(), re.sub(r"\s+", "", b).lower()
    return bool(a and b) and (a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() >= 0.6)


def match_field(fields: list[str], num: str, title: str) -> str:
    """원고 절 → 문서 필드 이름. 이름이 같으면 그것, 아니면 번호가 같고 제목이 비슷한 절 필드(「핵심 내용」·「핵심내용」),
    번호 없는 장은 제목이 같은 것. 없으면 빈 글 — 번호만 같은 다른 절에 쓰지 않는다."""
    k = key_of(num, title)
    if k in fields:
        return k
    if num:
        hit = [f for f in fields if f.startswith(f"절:{num} ") and _similar(f[len(f"절:{num} "):], title)]
        return hit[0] if len(hit) == 1 else ""
    names = [_norm(x).lower() for x in (title, *BACK_OLD.get(title, ()))]      # 예전 이름으로 만든 문서도(「초록」 ← 「Abstract」)
    hit = [f for f in fields if f.startswith("절:") and _norm(f[2:]).lower() in names]
    return hit[0] if hit else ""


def plan(data: bytes, texts: list[str], *, rich: bool = False, figures: dict[str, list[Path]] | None = None) -> dict:
    """원고들 → 쓸 목록. {"writes": [{field, kind(text|rich), text, items, style, pictures, chapter}], "missing": [원고 절], "lists": {그림·표 목차}}.
    그림·표 번호는 원고 차례대로 장마다 매긴다(Numbering). 서식째(rich)면 figures(figure_files)에 파일이 있는 그림은 그림으로 넣는다."""
    from zzaimy.app import webhwp_write as ww
    names = [x["name"] for x in ww.fields(data)]
    nums = Numbering()
    writes, missing, heads, notes = [], [], [], []
    for text in texts:
        for s in split_sections(text):
            if s.get("note"):
                notes.append(s["title"])
                continue
            body = s["body"].strip()
            if not body:
                continue
            field = match_field(names, s["num"], s["title"])
            if not field:
                (heads if s["level"] == 1 and s["num"] else missing).append(heading_text(s["num"], s["title"], s["level"]))
                continue
            chapter = (APPX if s["num"].startswith(APPX) else s["num"].split(".")[0]) if s["num"] else ""
            bl = blocks(body)
            w = {"field": field, "chapter": chapter, "blocks": [b["kind"] for b in bl]}
            if rich:
                w["kind"] = "rich"
                w["pictures"] = {}
                w["items"], w["style"] = to_items(bl, chapter, nums, figures, w["pictures"])
                w["text"] = to_text(bl, chapter, Numbering())          # 미리 보기용(번호는 따로 셈)
            else:
                w["kind"] = "text"
                w["text"] = to_text(bl, chapter, nums)
            writes.append(w)
    lists = {}
    for what, name in (("그림", "절:그림 목차"), ("표", "절:표 목차")):
        if nums.listed[what] and name in names:
            lists[name] = "\n".join(nums.listed[what])
    return {"writes": writes, "missing": missing, "chapter_heads": heads, "notes": notes, "lists": lists}


def _parts(text: str, n: int = 4) -> list[str]:
    """보는 사람에게 글이 차오르는 것처럼 — 줄 묶음 끝에서 끊은 앞부분들(마지막은 전체)."""
    lines = text.split("\n")
    if len(lines) <= 1 or n <= 1:
        return [text]
    step = max(1, -(-len(lines) // n))
    return ["\n".join(lines[:i]) for i in range(step, len(lines), step)] + [text]


def _who_wrote(db, fid: int, field: str, file_text: str) -> str:
    """지금 칸 글의 임자 — 칸 글 표의 마지막 작성자, 표에 없고 파일에 글이 있으면 「파일」, 비었으면 빈 글."""
    from zzaimy.app import webhwp_coedit as ce
    cur = ce.state(db, fid, [field]).get(field)
    if cur:
        return cur["by"] if cur.get("text", "").strip() else ""
    return "파일" if file_text.strip() else ""


def _field_para_id(data: bytes, field: str) -> str:
    """그 필드가 든 문단의 id — 절 첫 줄(필드 안 글)도 꾸밈 스타일을 입히려고."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    for n in sorted(x for x in zin.namelist() if re.match(r"Contents/section\d+\.xml$", x)):
        xml = zin.read(n).decode("utf-8")
        m = re.search(rf'<hp:fieldBegin\b[^>]*\bname="{re.escape(escape(field, {chr(34): "&quot;"}))}"', xml)
        if m:
            opens = list(re.finditer(r'<hp:p id="(\d+)"', xml[:m.start()]))
            return opens[-1].group(1) if opens else ""
    return ""


def _steps(text: str, pause: float) -> list[str]:
    """보는 사람에게 글이 차오르는 걸음 — 글이 길수록 걸음이 많다(줄 묶음 끝에서 끊는다, 4~12걸음). pause 가 0 이면 한 번에."""
    if pause <= 0:
        return [text]
    return _parts(text, max(4, min(12, len(text) // 150 + 1)))


def _gate(db, fid: int, data: bytes, field: str, overwrite: bool) -> dict | None:
    """쓰기 전 관문 — 사람이 쥔 칸·사람이 쓴 칸(overwrite 아니면)이면 건너뛸 까닭, 아니면 None."""
    from zzaimy.app import webhwp_coedit as ce
    from zzaimy.app import webhwp_write as ww
    holder = ce.holder(db, fid, field)
    if holder and holder != WRITER:
        return {"field": field, "ok": False, "why": "held", "by": holder}
    owner = _who_wrote(db, fid, field, ww.field_values(data).get(field, ""))
    if owner and owner != WRITER and not overwrite:
        return {"field": field, "ok": False, "why": "human", "by": owner}
    return None


def _rich_section(data: bytes, w: dict, written: dict) -> tuple[bytes, list[str]]:
    """서식째 절 하나를 파일 바이트에 — 문단·표·도식 넣기 → 표 모양 → 꾸밈 스타일 → 그림. (새 바이트, 넣은 문단 id)."""
    from zzaimy.app import webhwp_write as ww
    field = w["field"]
    new, put = ww.write_section(data, field, w["items"], written)
    new = dress_tables(new, put)
    head = _field_para_id(new, field)
    new = dress_written(new, put + ([head] if head else []), w.get("style") or {})
    pics = {k: Path(v).read_bytes() for k, v in (w.get("pictures") or {}).items() if Path(v).is_file()}
    return place_pictures(new, put, pics), put


def write(db, base: Path, fid: int, w: dict, *, overwrite: bool = False, pause: float = 0.4, sleep=None) -> dict:
    """절 하나를 쓴다 — {"field", "ok", "why"}. 서식째(rich)는 write_batch 로 한 절짜리 묶음."""
    if w["kind"] == "rich":
        return write_batch(db, base, fid, [w], overwrite=overwrite)[0]
    import time as _t

    from zzaimy.app import webhwp, webhwp_coedit as ce
    sleep = sleep or _t.sleep
    field = w["field"]
    skip = _gate(db, fid, Path(webhwp.get(db, fid)["path"]).read_bytes(), field, overwrite)
    if skip:
        return skip
    # 절 하나를 흘려 쓰는 동안 임대를 쥐는 공동 편집 도구(SectionStream — 그 절만의 창 번호) — 사람 화면에는 「Claude · 쓰는 중」
    stream = ce.SectionStream(db, fid, field, user=WRITER, base=base)
    if not stream.ok:
        return {"field": field, "ok": False, "why": "held", "by": stream.held}
    try:
        for part in _steps(w["text"], pause)[:-1]:
            stream.write(part)
            ce.touch(db, fid, WRITER, [field], client=stream.client)       # 걸음마다 임대·표시를 이어 둔다(「보는 중」 으로 바뀌지 않게)
            ce.presence(db, fid, WRITER, CLIENT, field)
            sleep(pause)
        ok = stream.write(w["text"])
    finally:
        stream.close()
    if ok:
        ce.commit_now(db, base, fid)                  # 스크립트는 곧 끝나므로 기다리지 않고 바로 파일에
    ce.presence(db, fid, WRITER, CLIENT, "")
    return {"field": field, "ok": ok, "why": "" if ok else "none"}


def write_batch(db, base: Path, fid: int, ws: list[dict], *, overwrite: bool = False) -> list[dict]:
    """서식째 절 여럿(보통 한 장)을 한 번에 — 절마다 임대를 잡고(사람 화면엔 「Claude · 쓰는 중」), 파일에 차례로 넣은 뒤 한 번만 저장한다.
    열린 편집 화면은 저장 알림으로 새 판을 한 번 불러온다(절마다 다시 부르면 화면이 매번 처음 쪽으로 돌아갔다). 구조 잠금을 쥔 사람이 있으면 쓰지 않는다.
    hwpx 조각을 칸에 넣는 길(webhwp_coedit.put_rich)은 아직 파일에 글만 써 넣어, 화면과 파일의 모양이 갈라지지 않게 이 길을 쓴다."""
    from zzaimy.app import webhwp, webhwp_coedit as ce
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.app import webhwp_write as ww
    results: dict[str, dict] = {}
    data = Path(webhwp.get(db, fid)["path"]).read_bytes()
    streams: dict[str, object] = {}
    who = webhwp.lock_holder(db, fid).get("user", "")
    for w in ws:
        f = w["field"]
        skip = {"field": f, "ok": False, "why": "struct", "by": who} if who and who != WRITER else _gate(db, fid, data, f, overwrite)
        if skip:
            results[f] = skip
            continue
        st = ce.SectionStream(db, fid, f, user=WRITER, base=base)
        if not st.ok:
            results[f] = {"field": f, "ok": False, "why": "held", "by": st.held}
            continue
        streams[f] = st
    try:
        if streams:
            ce.presence(db, fid, WRITER, CLIENT, next(iter(streams)))
            ce.commit_now(db, base, fid)             # 칸 글 표의 남의 글을 먼저 파일에 — 서식째 쓰기가 파일을 새로 쓰므로
            data = Path(webhwp.get(db, fid)["path"]).read_bytes()
            data, _ = add_styles(data)               # 나중에 더한 꾸밈 스타일(참고 문헌 등)이 예전 문서에 없으면 둔다
            m = webhwp.meta(db, fid)
            written = m.get("written") if isinstance(m.get("written"), dict) else {}
            done: list[str] = []
            for w in ws:
                f = w["field"]
                if f not in streams:
                    continue
                ce.presence(db, fid, WRITER, CLIENT, f)
                for g in streams:                     # 쥔 임대를 이어 둔다(파일 쓰기가 길어도 풀리지 않게)
                    ce.touch(db, fid, WRITER, [g], client=streams[g].client)
                new, put = _rich_section(data, w, written)
                probs = eo.check_bytes(new)
                if probs:
                    results[f] = {"field": f, "ok": False, "why": "check", "by": "; ".join(probs[:2])}
                    continue
                data, written[f] = new, put
                done.append(f)
            if done:
                webhwp.save(db, base, fid, data, user=WRITER)     # 한 번만 — 열린 편집 화면은 「문서 저장」 알림으로 새 판을 한 번 불러온다
                m = webhwp.meta(db, fid)
                m["written"] = written
                db.set_setting(f"webhwp_meta:{int(fid)}", json.dumps(m, ensure_ascii=False))
                vals = ww.field_values(data)
                for f in done:
                    ok = streams[f].write(vals.get(f, ""))
                    results[f] = {"field": f, "ok": ok, "why": "" if ok else "none"}
    finally:
        for st in streams.values():
            st.close()
        if streams:
            ce.commit_now(db, base, fid)
            ce.presence(db, fid, WRITER, CLIENT, "")
    return [results.get(w["field"]) or {"field": w["field"], "ok": False, "why": "none"} for w in ws]


def leave(db, fid: int) -> None:
    from zzaimy.app import webhwp_coedit as ce
    ce.leave(db, fid, CLIENT, WRITER)


def fragment(bl: list[dict], chapter: str, nums: Numbering) -> bytes:
    """덩이 → 서식째 조각 hwpx(꾸밈 스타일·표·팁 상자 포함). 기안기는 이 조각을 필드에 SetTextFile(base64, 'HWPX', 'insertfile') 로
    서식째 넣을 수 있다(시험으로 확인) — 칸 글 공동 편집의 서식 동기화(설정 coedit:rich)를 hwpx 조각으로 넓힐 때의 보낼 거리(ADR-0072)."""
    from hwpx import HwpxDocument

    from zzaimy.app import webhwp_style
    from zzaimy.app import webhwp_write as ww
    doc = HwpxDocument.new()
    doc.add_paragraph(_marker("절:조각"), style="바탕글")
    data, _ = add_styles(webhwp_style.body_base(doc.to_bytes()))
    data = _fieldize(data)
    items, style = to_items(bl, chapter, nums)
    data, put = ww.write_section(data, "절:조각", items, {})
    return dress_written(data, put, style)
