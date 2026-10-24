"""캡스톤 논문을 웹 한글 기안기에서 쓰기(ADR-0072) — 논문 양식(hwpx) 만들기, 논문 문서 하나 관리, 마크다운 원고 → 절 필드 글.

- 양식: 작년 캡스톤 논문집 한 편의 차례(docs/paper/논문-양식-가이드.md)를 따른다. 표지(머리 줄 + PROJECT·제목·팀원·팀명 상자) → 목차
  (1./1.1/1.1.1 세 단계, 점선) → 그림 목차 → 표 목차 → 1~6장 → 참고 문헌 → Abstract → 팀원 소개. A4, 머리말 오른쪽 위 줄인 제목,
  꼬리말 「영남이공대학교 ○○과」 와 쪽 번호. 장·절·소절 제목은 글자 체계(house_style.TYPE)의 chapter·h1·h2 로 서로 다르게.
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

from zzaimy.export import house_style as hs

SETTING = "paper:hwp_fid"
FOLDER = "논문"
WRITER = "claude"                 # 논문 원고를 쓰는 참여자 아이디(공동 편집 표의 user_id) — 화면 이름은 webhwp.display_name 이 「Claude」
WRITER_NAME = "Claude"
BUILD = 1                         # 만드는 방식이 바뀌면 올린다(문서 메타에 남겨 어느 판 양식으로 만든 문서인지 안다)

# 표지·머리말·꼬리말 값 — 학과·팀명은 정해지면 기안기에서 고친다(표지 상자는 칸 필드)
META = {
    "year": "2026",
    "dept": "○○과",
    "title": "AURA: 교내 행정 업무 자동화를 위한 sLLM 모델 개발 및 행정 자동화 에이전트",
    "short": "AURA 교내 행정 업무 자동화 sLLM·에이전트",
    "file": "AURA 캡스톤 논문",
}

# 차례 — (단계, 번호, 제목). 단계 1 = 장, 2 = 절, 3 = 소절. 번호가 빈 1단계는 번호 없는 장(참고 문헌·Abstract·팀원 소개)
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
    (1, "", "Abstract"),
    (1, "", "팀원 소개"),
]
BACK = ("참고 문헌", "Abstract", "팀원 소개")
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
}
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
    sec(번호 절, 3단계까지 — 쓸 자리), back(참고 문헌·Abstract·팀원 소개), sub(그보다 깊거나 번호 없는 소제목 — 본문 안 줄),
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
    뒤 장(참고 문헌·Abstract·팀원 소개)은 양식 가이드 차례(BACK)로, 부록은 그 뒤에 둔다."""
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


# ── 양식 만들기 ──
def _marker(name: str) -> str:
    return f"⟦{name}⟧"


def build(outline: list[tuple[int, str, str]] | None = None, meta: dict | None = None) -> bytes:
    """차례 → 논문 양식 hwpx 바이트(쓸 자리 누름틀·꾸밈 스타일·머리말·꼬리말·쪽 번호까지)."""
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
            if not num:                         # 번호 없는 장(참고 문헌·Abstract·팀원 소개)은 장 자체가 쓸 자리
                para(_marker(key_of(num, title)))
            continue
        para("")
        para(text, "h1" if lv == 2 else "h2")
        para(_marker(key_of(num, title)))
    data = webhwp_style.body_base(doc.to_bytes())
    data = webhwp_style.apply(data, per_table={1: {"head_rows": 0, "labels_only": True, "row_height": 2400}})
    data = webhwp_style.dress(data, plan)
    data = _dress_cells(data, cell_kinds)
    data, _ = add_styles(data)
    data = _fieldize(data)
    data = _page_frame(data, meta)
    return data


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


def _fieldize(data: bytes) -> bytes:
    """⟦이름⟧ 표식을 누름틀로 — 표식만 든 문단은 그 문단이 쓸 자리, 글 사이 표식은 그 자리에 칸. 논문 제목은 기본 글을 넣어 둔다."""
    from zzaimy.app import webhwp_fields as wf
    zin = zipfile.ZipFile(io.BytesIO(data))
    secs = {n: zin.read(n).decode("utf-8") for n in zin.namelist() if re.match(r"Contents/section\d+\.xml$", n)}
    ids = wf._Ids(list(secs.values()))
    out = {}
    for n, xml in secs.items():
        def sub(m: re.Match) -> str:
            name = m.group(1)
            hint = _HINTS.get(name, "본문 입력")
            a, b = wf.field_parts(name, hint, ids)
            value = META["title"] if name == "칸:논문 제목" else ""
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
    data = build(outline)
    fid = webhwp.add(db, base, webhwp.COMMON, f"{META['file']}.hwpx", data, owner=owner, source="논문", folder=FOLDER)
    m = webhwp.meta(db, fid)
    m.update(paper={"build": BUILD, "sections": len(outline)})
    db.set_setting(f"webhwp_meta:{fid}", json.dumps(m, ensure_ascii=False))
    db.set_setting(SETTING, str(fid))
    return fid, True


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
_CAP = re.compile(r"^\s*\[(그림|표)\s*(\d+-\d+)?\]\s*(.*)$")
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
_PAREN_SRC = re.compile(r"\s*\((캡처[^:：)]*|작성 필요|그림 자리)\s*[:：]?\s*([^)]*)\)\s*$")


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
    lines = body.splitlines()
    i = 0
    para: list[str] = []
    pending_cap = None                         # 표 바로 앞 캡션 줄 — 다음 표에 붙인다

    def flush():
        if para:
            out.append({"kind": "p", "text": " ".join(_inline(x) for x in para if x.strip())})
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
            while i < len(lines) and (_OL.match(lines[i]) or (items and lines[i].startswith("   ") and lines[i].strip())):
                m = _OL.match(lines[i])
                if m:
                    items.append(_inline(m.group(2)))
                else:
                    items[-1] += " " + _inline(lines[i])
                i += 1
            out.append({"kind": "steps", "items": items})
            continue
        elif _UL.match(ln):
            flush()
            items = []
            while i < len(lines) and (_UL.match(lines[i]) or (items and lines[i].startswith("  ") and lines[i].strip())):
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
            out += [f"{i}) {t}" for i, t in enumerate(b["items"], 1)]
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


def to_items(bl: list[dict], chapter: str, nums: Numbering) -> tuple[list[tuple[str, object]], dict[str, str]]:
    """덩이 → 서식째 쓰기 항목(webhwp_write.write_section 의 items)과 꾸밈 짝({글: 스타일 이름}) — 글 문단은 그 글로, 상자(1칸 표)는 칸 글로 짚는다."""
    items: list[tuple[str, object]] = []
    style: dict[str, str] = {}
    texts: list[str] = []

    def flush():
        if texts:
            items.append(("text", "\n".join(texts)))
            texts.clear()
    for b in bl:
        k = b["kind"]
        if k == "p":
            texts.append(b["text"])
        elif k == "sub":
            texts.append("■ " + b["text"])
        elif k == "steps":
            for i, t in enumerate(b["items"], 1):
                line = f"{i}) {t}"
                texts.append(line)
                style[line] = "단계 목록"
        elif k == "bullets":
            texts += [f"- {t}" for t in b["items"]]
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


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


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
    t = _norm(title).lower()
    hit = [f for f in fields if f.startswith("절:") and _norm(f[2:]).lower() == t]
    return hit[0] if hit else ""


def plan(data: bytes, texts: list[str], *, rich: bool = False) -> dict:
    """원고들 → 쓸 목록. {"writes": [{field, kind(text|rich), text, items, style, chapter}], "missing": [원고 절], "lists": {그림·표 목차}}.
    그림·표 번호는 원고 차례대로 장마다 매긴다(Numbering)."""
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
                w["items"], w["style"] = to_items(bl, chapter, nums)
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


def write(db, base: Path, fid: int, w: dict, *, overwrite: bool = False, pause: float = 0.4, sleep=None) -> dict:
    """절 하나를 쓴다 — {"field", "ok", "why"}. 사람이 임대를 쥔 칸·사람이 쓴 글이 있는 칸(overwrite 아니면)은 건너뛴다."""
    import time as _t

    from zzaimy.app import webhwp, webhwp_coedit as ce
    from zzaimy.app import webhwp_write as ww
    sleep = sleep or _t.sleep
    field = w["field"]
    f = webhwp.get(db, fid)
    data = Path(f["path"]).read_bytes()
    holder = ce.holder(db, fid, field)
    if holder and holder != WRITER:
        return {"field": field, "ok": False, "why": "held", "by": holder}
    owner = _who_wrote(db, fid, field, ww.field_values(data).get(field, ""))
    if owner and owner != WRITER and not overwrite:
        return {"field": field, "ok": False, "why": "human", "by": owner}
    got = ce.acquire(db, fid, field, WRITER, CLIENT)
    if not got["ok"]:
        return {"field": field, "ok": False, "why": "held", "by": got["by"]}
    ce.presence(db, fid, WRITER, CLIENT, field)
    if w["kind"] == "rich":
        who = webhwp.lock_holder(db, fid).get("user", "")
        if who and who != WRITER:
            ce.release(db, fid, field, WRITER)
            return {"field": field, "ok": False, "why": "struct", "by": who}
        ce.commit_now(db, base, fid)                 # 칸 글 표의 남의 글을 먼저 파일에 — 서식째 쓰기가 파일을 새로 쓰므로
        data = Path(webhwp.get(db, fid)["path"]).read_bytes()
        m = webhwp.meta(db, fid)
        written = m.get("written") if isinstance(m.get("written"), dict) else {}
        new, put = ww.write_section(data, field, w["items"], written)
        new = dress_written(new, put, w.get("style") or {})
        from zzaimy.app import webhwp_edit_ops as eo
        probs = eo.check_bytes(new)
        if probs:
            ce.release(db, fid, field, WRITER)
            return {"field": field, "ok": False, "why": "check", "by": "; ".join(probs[:2])}
        webhwp.save(db, base, fid, new, user=WRITER)          # 열린 편집 화면은 「문서 저장」 알림으로 새 판을 불러온다
        m = webhwp.meta(db, fid)
        written[field] = put
        m["written"] = written
        db.set_setting(f"webhwp_meta:{int(fid)}", json.dumps(m, ensure_ascii=False))
        inner = ww.field_values(new).get(field, "")
        done, held = ce.put(db, fid, WRITER, {field: inner}, client=CLIENT)
    else:
        for part in _parts(w["text"])[:-1]:
            ce.apply_ops(db, fid, WRITER, CLIENT, [{"seq": int(_t.time() * 1e6), "field": field, "text": part}], force=True)
            sleep(pause)
        done, held = ce.put(db, fid, WRITER, {field: w["text"]}, base=base, client=CLIENT)
    if held:
        return {"field": field, "ok": False, "why": "held", "by": held.get(field, "")}
    return {"field": field, "ok": bool(done), "why": "" if done else "none"}


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
