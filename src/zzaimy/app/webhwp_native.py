"""기안기 기본 동작(네이티브) 허용 목록 — 에이전트가 웹 한글 기안기(WebHwpCtrl 13.82.0.208)의 동작을 그대로 부르게 한다.

전수조사(docs/notes/2026-10-24-webhwp-features.md)에서 기안기에 실제 등록돼 있고(CreateAction(이름).pgi 가 있음) 빈 문서에서
실행해 결과를 확인한 동작만 담는다. 인자 이름은 기안기 매개변수 묶음(HParameterSet)의 항목 이름 그대로이고, 단위만 사람 기준
(길이 mm·글자 크기 pt·색 #RRGGBB 또는 이름)으로 받아 기안기 값(HWPUNIT·1/100pt·0xBBGGRR)으로 바꾼다.

이 모듈은 「허용 동작 + 인자 + 자리」 → 편집 화면이 실행할 단계 목록만 만든다(실행·명령 줄·권한은 webhwp_edit_ops·webhwp_live_routes).
단계 원소(편집 화면의 static/webhwp-agent-runner.js 가 실행):
  ["field", 이름, 고르기]  그 필드로 커서(고르기면 필드 글을 블록으로)
  ["find", 글, n]         문서 처음부터 n 번째로 찾은 글을 블록으로(ForwardFind)
  ["run", 동작]           Run — 아래 RUN_OK 안의 이름만
  ["exec", 동작, {항목: 값}] CreateAction·CreateSet·GetDefault·SetItem·Execute — EXEC_OK 안의 이름만, 항목은 그 동작의 허용 항목만
  ["text", 글]            InsertText
  ["assert", "table"]     커서가 표 칸 안이 아니면 멈춤
  ["read", [항목…]]       지금 자리의 정보 읽기(READ_KEYS)
  ["picture", 폭mm, 높이mm] 명령에 붙은 그림(서버가 받아 둔 문서함·첨부 그림)을 넣기
대화 상자를 여는 동작(…Dlg, 매개변수 없이 Run 한 PageSetup 등)·파일 열기·저장·인쇄·하이퍼링크·외부 주소·임의 스크립트는 없다.
"""
from __future__ import annotations

import re

MM = 283.465                       # HWPUNIT / mm


class NativeError(ValueError):
    """에이전트에게 돌려줄 까닭 — 명령을 넣지 않는다."""


# ── 값 꼴 ──
# ("int", lo, hi) · ("bool",) · ("mm", lo, hi) · ("pt", lo, hi) · ("color",) · ("enum", {이름: 값}) · ("str", 최대 길이)
TEXT_COLORS = {"검정": "#000000", "빨강": "#FF0000", "파랑": "#0000FF", "초록": "#008000", "노랑": "#FFFF00", "흰색": "#FFFFFF",
               "회색": "#808080", "자주": "#800080", "청록": "#008080", "주황": "#FFA500", "연회색": "#F2F2F2", "연노랑": "#FFF2CC",
               "연파랑": "#DFEAF5", "연두": "#E2EFDA"}
MAX_TEXT = 2000
MAX_STEPS = 200


def _I(lo, hi):
    return ("int", lo, hi)


_B = ("bool",)
_C = ("color",)


def _MM(lo, hi):
    return ("mm", lo, hi)


def _PT(lo, hi):
    return ("pt", lo, hi)


def _E(m: dict):
    return ("enum", m)


def _S(n):
    return ("str", n)


ALIGN = _E({"양쪽": 0, "왼쪽": 1, "오른쪽": 2, "가운데": 3, "배분": 4, "나눔": 5})
LINE_SPACING_TYPE = _E({"글자에 따라": 0, "고정 값": 1, "여백만": 2, "최소": 3})
UNDERLINE = _E({"없음": 0, "아래": 1, "위": 3})
PN_POS = _E({"없음": 0, "위 왼쪽": 1, "위 가운데": 2, "위 오른쪽": 3, "아래 왼쪽": 4, "아래 가운데": 5, "아래 오른쪽": 6,
             "바깥 위": 7, "바깥 아래": 8, "안쪽 위": 9, "안쪽 아래": 10})
NUM_FMT = _E({"1": 0, "①": 1, "I": 2, "i": 3, "A": 4, "a": 5})
GUTTER = _E({"왼쪽": 0, "맞쪽": 1, "위쪽": 2})
PAPERS = {"A4": (210, 297), "A3": (297, 420), "A5": (148, 210), "B4": (257, 364), "B5": (182, 257),
          "Letter": (215.9, 279.4), "Legal": (215.9, 355.6)}

_CHAR_ITEMS = {
    "Bold": _B, "Italic": _B, "UnderlineType": UNDERLINE, "StrikeOutType": _B, "SuperScript": _B, "SubScript": _B,
    "Emboss": _B, "Engrave": _B, "SmallCaps": _B, "OutlineType": _I(0, 6), "ShadowType": _I(0, 2),
    "TextColor": _C, "ShadeColor": _C, "UnderlineColor": _C, "Height": _PT(1, 4096),
    "FaceNameHangul": _S(40), "FaceNameLatin": _S(40), "FaceNameHanja": _S(40), "FaceNameSymbol": _S(40),
    "SpacingHangul": _I(-50, 50), "SpacingLatin": _I(-50, 50), "RatioHangul": _I(50, 200), "RatioLatin": _I(50, 200),
}
_PARA_ITEMS = {
    "AlignType": ALIGN, "LineSpacingType": LINE_SPACING_TYPE, "LineSpacing": _I(0, 500),
    "LeftMargin": _MM(-100, 200), "RightMargin": _MM(-100, 200), "Indentation": _MM(-100, 100),
    "PrevSpacing": _PT(0, 200), "NextSpacing": _PT(0, 200),
    "KeepWithNext": _B, "KeepLinesTogether": _B, "PagebreakBefore": _B, "WidowOrphan": _B, "SnapToGrid": _B,
}
_PAGE_ITEMS = {
    "PageDef.PaperWidth": _MM(50, 1000), "PageDef.PaperHeight": _MM(50, 1000), "PageDef.Landscape": _B,
    "PageDef.TopMargin": _MM(0, 100), "PageDef.BottomMargin": _MM(0, 100), "PageDef.LeftMargin": _MM(0, 100),
    "PageDef.RightMargin": _MM(0, 100), "PageDef.HeaderLen": _MM(0, 100), "PageDef.FooterLen": _MM(0, 100),
    "PageDef.GutterLen": _MM(0, 100), "PageDef.GutterType": GUTTER,
}
_BORDER = {f"BorderType{s}": _I(0, 16) for s in ("Left", "Right", "Top", "Bottom")}
_BORDER.update({f"BorderWidth{s}": _I(0, 15) for s in ("Left", "Right", "Top", "Bottom")})
# 기안기 항목 이름의 오타(BorderCorlorLeft)까지 기안기 그대로 — 사람은 BorderColorLeft 로 써도 받는다(_ALIAS)
_BORDER.update({"BorderCorlorLeft": _C, "BorderColorRight": _C, "BorderColorTop": _C, "BorderColorBottom": _C})
_ALIAS = {"BorderColorLeft": "BorderCorlorLeft"}

# 동작 정의. kind: exec(매개변수 묶음) | run(매개변수 없음) | text(동작 뒤 글을 넣고 닫기)
#   where: pos(자리 필요) · sel(블록 필요 — 필드면 필드 글, find 면 찾은 글) · cell(표 칸 필요) · doc(자리 없어도 됨 — 문서 처음 구역)
#   items: 허용 항목, need: 하나는 줘야 하는 항목, fixed: 늘 넣는 항목(사람이 못 바꿈), virtual: 편의 인자
NATIVE: dict[str, dict] = {
    # 글자
    "CharShape": {"label": "글자 모양", "kind": "exec", "where": "sel", "items": _CHAR_ITEMS},
    # 문단
    "ParagraphShape": {"label": "문단 모양", "kind": "exec", "where": "pos", "items": _PARA_ITEMS},
    # 쪽
    "PageSetup": {"label": "편집 용지", "kind": "exec", "where": "doc", "items": _PAGE_ITEMS, "virtual": ("paper",)},
    "PageNumPos": {"label": "쪽 번호 매기기", "kind": "exec", "where": "doc",
                   "items": {"DrawPos": PN_POS, "NumberFormat": NUM_FMT, "SideChar": _B}, "need": ("DrawPos",)},
    "HeaderFooter": {"label": "머리말·꼬리말", "kind": "text", "where": "doc",
                     "items": {"HeaderFooterCtrlType": _E({"머리말": 0, "꼬리말": 1}), "Type": _E({"양쪽": 0, "짝수 쪽": 1, "홀수 쪽": 2})},
                     "need": ("HeaderFooterCtrlType",)},
    "NewNumber": {"label": "새 번호로 시작", "kind": "exec", "where": "pos",
                  "items": {"NumType": _E({"쪽": 0, "각주": 1, "미주": 2, "그림": 3, "표": 4, "수식": 5}), "NewNumber": _I(1, 9999)},
                  "need": ("NumType", "NewNumber")},
    "PageHiding": {"label": "현재 쪽만 감추기", "kind": "exec", "where": "pos", "items": {"Fields": _I(0, 63)}, "need": ("Fields",)},
    "BreakPage": {"label": "쪽 나누기", "kind": "run", "where": "pos"},
    "BreakColumn": {"label": "단 나누기", "kind": "run", "where": "pos"},
    "BreakSection": {"label": "구역 나누기", "kind": "run", "where": "pos"},
    "BreakLine": {"label": "줄 나누기", "kind": "run", "where": "pos"},
    "BreakPara": {"label": "문단 나누기", "kind": "run", "where": "pos"},
    # 넣기
    "InsertText": {"label": "글 넣기", "kind": "exec", "where": "pos", "items": {"Text": _S(MAX_TEXT)}, "need": ("Text",)},
    "InsertFootnote": {"label": "각주", "kind": "text", "where": "pos", "items": {}},
    "InsertEndnote": {"label": "미주", "kind": "text", "where": "pos", "items": {}},
    "InsertPageNum": {"label": "쪽 번호 넣기(커서 자리)", "kind": "run", "where": "pos"},
    "InsertPicture": {"label": "그림 넣기", "kind": "picture", "where": "pos", "items": {}, "virtual": ("image", "width_mm", "height_mm")},
    # 찾아 바꾸기(문서 전체, 알림 창 없이)
    "AllReplace": {"label": "모두 바꾸기", "kind": "exec", "where": "none",
                   "items": {"FindString": _S(200), "ReplaceString": _S(MAX_TEXT), "MatchCase": _B, "WholeWordOnly": _B},
                   "need": ("FindString",), "fixed": {"IgnoreMessage": 1, "Direction": 2, "ReplaceMode": 1}},
    # 표
    "TableCreate": {"label": "표 만들기", "kind": "exec", "where": "pos", "items": {"Rows": _I(1, 300), "Cols": _I(1, 30)},
                    "need": ("Rows", "Cols")},
    "TableInsertRowColumn": {"label": "줄·칸 추가", "kind": "exec", "where": "cell",
                             "items": {"Side": _E({"왼쪽": 0, "오른쪽": 1, "위": 2, "아래": 3}), "Count": _I(1, 50)}, "need": ("Side",)},
    "TableDeleteRowColumn": {"label": "줄·칸 지우기", "kind": "exec", "where": "cell",
                             "items": {"Type": _E({"칸": 0, "열": 0, "줄": 1, "행": 1})}, "need": ("Type",)},
    "TableSplitCell": {"label": "셀 나누기", "kind": "exec", "where": "cell",
                       "items": {"Rows": _I(1, 20), "Cols": _I(1, 20), "DistributeHeight": _B, "Merge": _B}},
    "TableMergeCell": {"label": "셀 합치기", "kind": "run", "where": "cell"},
    "CellBorderFill": {"label": "셀 테두리·배경", "kind": "exec", "where": "cell", "items": _BORDER, "virtual": ("FillColor",)},
    "TableAppendRow": {"label": "표 끝에 줄 더하기", "kind": "run", "where": "cell"},
    "TableSubtractRow": {"label": "표 끝 줄 빼기", "kind": "run", "where": "cell"},
    "TableDistributeCellWidth": {"label": "셀 너비를 같게", "kind": "run", "where": "cell"},
    "TableDistributeCellHeight": {"label": "셀 높이를 같게", "kind": "run", "where": "cell"},
    "TableVAlignTop": {"label": "셀 세로 위", "kind": "run", "where": "cell"},
    "TableVAlignCenter": {"label": "셀 세로 가운데", "kind": "run", "where": "cell"},
    "TableVAlignBottom": {"label": "셀 세로 아래", "kind": "run", "where": "cell"},
    "TableFormulaSumAuto": {"label": "블록 합계", "kind": "run", "where": "cell"},
    "TableFormulaAvgAuto": {"label": "블록 평균", "kind": "run", "where": "cell"},
    "TableFormulaProAuto": {"label": "블록 곱", "kind": "run", "where": "cell"},
    "TableInsertComma": {"label": "자릿점 넣기", "kind": "run", "where": "cell"},
    "TableDeleteComma": {"label": "자릿점 빼기", "kind": "run", "where": "cell"},
    # 글머리표·문단 번호·개요
    "PutBullet": {"label": "글머리표 켜기·끄기", "kind": "run", "where": "pos"},
    "PutParaNumber": {"label": "문단 번호 켜기·끄기", "kind": "run", "where": "pos"},
    "PutOutlineNumber": {"label": "개요 번호 켜기·끄기", "kind": "run", "where": "pos"},
    "ParaNumberBulletLevelUp": {"label": "한 수준 위로", "kind": "run", "where": "pos"},
    "ParaNumberBulletLevelDown": {"label": "한 수준 아래로", "kind": "run", "where": "pos"},
}
# 매개변수 없이 바로 되는 서식 단축 동작(블록 또는 문단에)
for _n, _l in {"CharShapeBold": "진하게", "CharShapeItalic": "기울임", "CharShapeUnderline": "밑줄", "CharShapeStrikeout": "취소선",
               "CharShapeSuperscript": "위 첨자", "CharShapeSubscript": "아래 첨자", "CharShapeNormal": "보통 모양",
               "CharShapeHeightIncrease": "글씨 크게", "CharShapeHeightDecrease": "글씨 작게",
               "CharShapeSpacingIncrease": "자간 넓게", "CharShapeSpacingDecrease": "자간 좁게"}.items():
    NATIVE[_n] = {"label": _l, "kind": "run", "where": "sel"}
for _n, _l in {"ParagraphShapeAlignLeft": "왼쪽 정렬", "ParagraphShapeAlignRight": "오른쪽 정렬", "ParagraphShapeAlignCenter": "가운데 정렬",
               "ParagraphShapeAlignJustify": "양쪽 정렬", "ParagraphShapeAlignDistribute": "배분 정렬",
               "ParagraphShapeIncreaseLeftMargin": "왼쪽 여백 늘이기", "ParagraphShapeDecreaseLeftMargin": "왼쪽 여백 줄이기",
               "ParagraphShapeIncreaseLineSpacing": "줄 간격 넓게", "ParagraphShapeDecreaseLineSpacing": "줄 간격 좁게",
               "ParagraphShapeIndentPositive": "첫 줄 들여쓰기", "ParagraphShapeIndentNegative": "첫 줄 내어쓰기"}.items():
    NATIVE[_n] = {"label": _l, "kind": "run", "where": "pos"}
# 스타일 — 기안기는 스타일 목록의 n 번째(1~10)를 적용하는 단축 동작만 API 로 듣는다(Style 묶음은 이름 없는 항목이라 SetItem 이 듣지 않음).
# 목록 차례는 문서 header 의 스타일 차례(hwp_styles 로 확인) — 보통 1 바탕글·2 본문·3~9 개요 1~7
for _i in range(1, 11):
    NATIVE[f"StyleShortcut{_i}"] = {"label": f"스타일 {_i}번째", "kind": "run", "where": "pos"}

# 편집 화면 실행기가 받아 줄 동작 이름(단계 관문) — 자리 옮기기·블록 만들기용 보조 동작 포함
_HELPERS = {"MoveDocBegin", "MoveDocEnd", "MoveLineEnd", "MoveParaEnd", "MoveParaBegin", "TableCellBlock", "TableCellBlockExtend",
            "TableRightCell", "TableLowerCell", "Cancel", "CloseEx", "Select"}
RUN_OK = {k for k, v in NATIVE.items() if v["kind"] == "run"} | _HELPERS | {"InsertFootnote", "InsertEndnote"}
EXEC_OK = {k: set(v.get("items") or {}) | set(v.get("fixed") or {}) for k, v in NATIVE.items() if v["kind"] in ("exec", "text")}
EXEC_OK["CellBorderFill"] |= {"FillAttr.Type", "FillAttr.WinBrushFaceColor", "FillAttr.WinBrushHatchColor", "FillAttr.WinBrushFaceStyle"}
EXEC_OK["CharShape"] |= {"FontTypeHangul", "FontTypeLatin", "FontTypeHanja", "FontTypeSymbol"}
EXEC_OK["ForwardFind"] = {"FindString", "IgnoreMessage", "Direction"}
READ_KEYS = ("pos", "field", "selection", "char", "para", "heading", "pages", "fields", "table", "text")


def bgr(color) -> int:
    """#RRGGBB 또는 색 이름 → 기안기 색 값(0xBBGGRR). 모르는 색은 NativeError."""
    c = TEXT_COLORS.get(str(color or "").strip(), str(color or "").strip())
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", c):
        raise NativeError(f"색은 #RRGGBB 또는 {'·'.join(TEXT_COLORS)} 가운데 하나다.")
    r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
    return (b << 16) | (g << 8) | r


def _num(v, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise NativeError(f"{name} 는 숫자다.")
    try:
        f = float(v)
    except ValueError:
        raise NativeError(f"{name} 는 숫자다.")
    if f != f or f in (float("inf"), float("-inf")):
        raise NativeError(f"{name} 는 숫자다.")
    return f


def convert(name: str, spec: tuple, v):
    """사람 단위 값 → 기안기 값. 범위 밖·모르는 값은 NativeError(조용히 자르지 않는다)."""
    kind = spec[0]
    if kind == "bool":
        if isinstance(v, bool):
            return 1 if v else 0
        if v in (0, 1, "0", "1"):
            return int(v)
        if str(v) in ("켜기", "예", "true"):
            return 1
        if str(v) in ("끄기", "아니오", "false"):
            return 0
        raise NativeError(f"{name} 는 true|false 다.")
    if kind == "enum":
        m = spec[1]
        if isinstance(v, str) and v in m:
            return m[v]
        if not isinstance(v, bool) and isinstance(v, int) and v in m.values():
            return v
        raise NativeError(f"{name} 는 {'|'.join(m)} 가운데 하나다.")
    if kind == "color":
        return bgr(v)
    if kind == "str":
        if not isinstance(v, str):
            raise NativeError(f"{name} 는 글이다.")
        if len(v) > spec[1]:
            raise NativeError(f"{name} 는 {spec[1]}자까지다.")
        return v
    f = _num(v, name)
    lo, hi = spec[1], spec[2]
    if not lo <= f <= hi:
        unit = {"mm": "mm", "pt": "pt"}.get(kind, "")
        raise NativeError(f"{name} 는 {lo}~{hi}{unit} 다.")
    if kind == "mm":
        return int(round(f * MM))
    if kind == "pt":
        return int(round(f * 100))
    if f != int(f):
        raise NativeError(f"{name} 는 정수다.")
    return int(f)


def items_for(action: str, params: dict) -> dict:
    """허용 항목만 기안기 값으로. 모르는 항목은 NativeError(무엇을 쓸 수 있는지 알려 준다)."""
    spec = NATIVE[action]
    allowed = spec.get("items") or {}
    virtual = set(spec.get("virtual") or ())
    out: dict = {}
    for k, v in (params or {}).items():
        k = _ALIAS.get(k, k)
        if action == "PageSetup" and f"PageDef.{k}" in allowed:   # PageDef. 은 빼고 써도 된다
            k = f"PageDef.{k}"
        if k in virtual or k == "text":
            continue
        if k not in allowed:
            names = ", ".join(list(allowed) + list(virtual)) or "없음"
            raise NativeError(f"{action} 에 쓸 수 없는 항목 「{k}」 — 쓸 수 있는 것: {names}.")
        if v is None or v == "":
            continue
        out[k] = convert(k, allowed[k], v)
    if action == "CharShape":                         # 글꼴 이름을 주면 글꼴 종류(1 = TTF)도 — 기안기 글자 모양 창과 같게
        for lang in ("Hangul", "Latin", "Hanja", "Symbol"):
            if f"FaceName{lang}" in out:
                out[f"FontType{lang}"] = 1
    if action == "PageSetup" and params.get("paper"):
        key = next((p for p in PAPERS if p.lower() == str(params["paper"]).lower()), None)
        if not key:
            raise NativeError(f"paper 는 {', '.join(PAPERS)} 가운데 하나다.")
        out.setdefault("PageDef.PaperWidth", int(round(PAPERS[key][0] * MM)))
        out.setdefault("PageDef.PaperHeight", int(round(PAPERS[key][1] * MM)))
    if action == "CellBorderFill" and params.get("FillColor") not in (None, ""):
        fc = str(params["FillColor"])
        if fc in ("없음", "none"):
            out.update({"FillAttr.Type": 0})
        else:
            out.update({"FillAttr.Type": 1, "FillAttr.WinBrushFaceColor": bgr(fc), "FillAttr.WinBrushHatchColor": 0,
                        "FillAttr.WinBrushFaceStyle": -1})
    if action == "PageNumPos" and "SideChar" in out:  # 줄표(- 1 -)는 문자 코드로
        out["SideChar"] = 0x2D if out["SideChar"] else 0
    for k in spec.get("need") or ():
        if k not in out:
            raise NativeError(f"{action} 는 {', '.join(spec['need'])} 를 준다.")
    if spec["kind"] == "exec" and not out and not spec.get("need"):
        names = ", ".join(list(allowed) + list(virtual))
        raise NativeError(f"{action} 에 바꿀 항목을 하나 이상 준다 — {names}.")
    out.update(spec.get("fixed") or {})
    return out


def place(at, where: str) -> tuple[list, str]:
    """자리 인자 → (단계, 필드 이름 또는 ''). at:
    {field: 이름, select: true|false} · {find: 글, nth: n} · {cell: 표 칸 필드, right: n, down: n}(칸 블록) · {move: 문서 처음|문서 끝}."""
    if at in (None, "", {}):
        if where in ("doc", "none"):
            return ([["run", "MoveDocBegin"]] if where == "doc" else []), ""
        raise NativeError("자리(at)를 준다 — {field: 필드 이름} · {find: 찾을 글} · {cell: 표 칸 필드} · {move: 문서 처음|문서 끝}. "
                          "사람의 커서 자리에는 쓰지 않는다.")
    if not isinstance(at, dict):
        raise NativeError("at 은 {field|find|cell|move: …} 꼴이다.")
    if at.get("field"):
        f = str(at["field"])[:200]
        sel = bool(at.get("select", where == "sel"))
        steps = [["field", f, sel]]
        if where == "cell":
            steps.append(["assert", "table"])
        return steps, f
    if at.get("cell"):
        f = str(at["cell"])[:200]
        right, down = (int(convert(k, _I(0, 100 if k == "down" else 30), at.get(k) or 0)) for k in ("right", "down"))
        steps = [["field", f, False], ["assert", "table"]]
        if right or down:
            steps += [["run", "TableCellBlock"], ["run", "TableCellBlockExtend"]] + [["run", "TableRightCell"]] * right \
                + [["run", "TableLowerCell"]] * down
        return steps, f
    if at.get("find"):
        text = str(at["find"])
        if len(text) > 100:
            raise NativeError("find 는 100자까지다.")
        nth = convert("nth", _I(1, 50), at.get("nth") or 1)
        steps = [["find", text, nth]]
        if where == "cell":
            steps.append(["assert", "table"])
        return steps, ""
    mv = str(at.get("move") or "")
    if mv in ("문서 처음", "doc_begin"):
        return [["run", "MoveDocBegin"]], ""
    if mv in ("문서 끝", "doc_end"):
        return [["run", "MoveDocEnd"]], ""
    raise NativeError("at 은 field·find·cell·move(문서 처음|문서 끝) 가운데 하나를 준다.")


def steps_for(action: str, params: dict | None, at=None, picture: tuple[float, float] | None = None) -> tuple[list, str]:
    """허용 동작 → (단계, 대상 필드). 모르는 동작·항목·범위 밖 값은 NativeError."""
    if action not in NATIVE:
        raise NativeError(f"기안기 동작 허용 목록에 없다 — {action!s:.40}. 쓸 수 있는 것: {', '.join(NATIVE)}.")
    if params is not None and not isinstance(params, dict):
        raise NativeError("params 는 {항목: 값} 꼴이다.")
    params = params or {}
    spec = NATIVE[action]
    pre, field = place(at, spec["where"])
    kind = spec["kind"]
    if kind == "run":
        if params:
            raise NativeError(f"{action} 는 인자가 없다.")
        body = [["run", action]]
    elif kind == "exec":
        body = [["exec", action, items_for(action, params)]]
    elif kind == "text":
        text = params.get("text")
        if not isinstance(text, str) or not text.strip():
            raise NativeError(f"{action} 는 params.text(넣을 글)를 준다.")
        if len(text) > MAX_TEXT:
            raise NativeError(f"text 는 {MAX_TEXT}자까지다.")
        items = items_for(action, {k: v for k, v in params.items() if k != "text"}) if spec.get("items") else {}
        if action in ("InsertFootnote", "InsertEndnote"):
            body = [["run", "MoveLineEnd"] if (at or {}).get("field") else ["run", "Cancel"], ["run", action], ["text", text], ["run", "CloseEx"]]
        else:
            body = [["exec", action, items], ["text", text], ["run", "CloseEx"]]
    elif kind == "picture":
        if not picture:
            raise NativeError("그림(params.image)을 준다.")
        body = [["run", "Cancel"], ["picture", round(float(picture[0]), 1), round(float(picture[1]), 1)]]
    else:                                              # pragma: no cover — 정의 실수
        raise NativeError("알 수 없는 동작 꼴.")
    tail = [["run", "Cancel"]] if spec["where"] in ("sel", "cell") else []
    steps = pre + body + tail
    if len(steps) > MAX_STEPS:
        raise NativeError("단계가 너무 많다(칸 블록을 줄인다).")
    return steps, field


def read_steps(at, what) -> tuple[list, str]:
    """읽기 단계 — at 이 없으면 사람의 지금 커서 자리를 읽는다(읽기만이라 커서를 옮기지 않는다)."""
    keys = [str(k) for k in (what or ["pos", "field", "selection", "char", "para", "heading", "pages", "table"]) if str(k) in READ_KEYS]
    if not keys:
        raise NativeError(f"what 은 {', '.join(READ_KEYS)} 가운데에서 고른다.")
    pre, field = place(at, "pos") if at else ([], "")
    return pre + [["read", keys]], field


def steps_ok(steps) -> bool:
    """편집 화면에 넘기기 전 마지막 관문 — 모든 단계가 허용 원소·허용 동작·허용 항목인가(명령 줄에 직접 쓴 것도 여기서 막는다)."""
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        return False
    for st in steps:
        if not isinstance(st, list) or not st:
            return False
        k = st[0]
        if k == "run":
            if len(st) != 2 or st[1] not in RUN_OK:
                return False
        elif k == "exec":
            if len(st) != 3 or st[1] not in EXEC_OK or not isinstance(st[2], dict) or not set(st[2]) <= EXEC_OK[st[1]]:
                return False
            if any(not isinstance(v, (int, str)) or isinstance(v, bool) or (isinstance(v, str) and len(v) > MAX_TEXT) for v in st[2].values()):
                return False
        elif k == "text":
            if len(st) != 2 or not isinstance(st[1], str) or len(st[1]) > MAX_TEXT:
                return False
        elif k == "field":
            if len(st) != 3 or not isinstance(st[1], str) or len(st[1]) > 200:
                return False
        elif k == "find":
            if len(st) != 3 or not isinstance(st[1], str) or len(st[1]) > 100 or not isinstance(st[2], int) or not 1 <= st[2] <= 50:
                return False
        elif k == "assert":
            if st != ["assert", "table"]:
                return False
        elif k == "read":
            if len(st) != 2 or not isinstance(st[1], list) or not set(st[1]) <= set(READ_KEYS):
                return False
        elif k == "picture":
            if len(st) != 3 or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and 1 <= x <= 1000 for x in st[1:]):
                return False
        else:
            return False
    return True


def read_only(steps) -> bool:
    """읽기만 하는 명령인가(자리 옮기기·찾기·읽기뿐) — 편집 화면이 구조 잠금·저장 없이 실행해도 된다."""
    return bool(steps) and all(st[0] in ("read", "field", "find", "assert") or (st[0] == "run" and st[1] in ("MoveDocBegin", "MoveDocEnd", "Cancel"))
                               for st in steps) and any(st[0] == "read" for st in steps)


def describe() -> str:
    """도구 설명에 넣을 동작 목록 — 「이름(항목…)」."""
    out = []
    for k, v in NATIVE.items():
        if k.startswith("StyleShortcut") and k != "StyleShortcut1":
            continue
        if k == "StyleShortcut1":
            out.append("StyleShortcut1~10(스타일 목록 n번째)")
            continue
        args = list((v.get("items") or {}).keys()) + list(v.get("virtual") or ()) + (["text"] if v["kind"] == "text" else [])
        out.append(f"{k}({', '.join(a.replace('PageDef.', '') for a in args)})" if args else k)
    return "; ".join(out)
