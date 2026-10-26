"""화면 용어 한 곳 — 처리 상태·문서 종류·파일 형식의 우리말 이름. 라이브러리·기준 문서·학교 원본가 같은 말을 쓴다.

새 화면은 여기 이름을 가져다 쓰고, 화면마다 따로 이름표를 두지 않는다.
"""

from __future__ import annotations

# 등록 문서의 처리 상태(documents.status)
STATUS_LABELS = {
    "received": "처리 중",                  # 대기열에 든 것도 화면에는 처리 중 하나로(등록 완료 / 처리 중 / 실패)
    "processing": "처리 중",
    "reviewed": "처리 완료",                # 라이브러리 필터·문서 상태 모두 「처리 완료」 하나
    "failed": "실패",
}

# 원본 파일의 처리 상태(app/archive_state) — 등록 문서와 겹치는 상태는 같은 이름
ARCHIVE_STATE_LABELS = {
    "registered": "문서 연결됨",           # 원본 파일에 라이브러리 문서가 이어짐 — 문서의 처리 상태와 다른 말(한 문서에 원본 여럿)
    "excluded": "처리 제외",
    "failed": STATUS_LABELS["failed"],
    "waiting": "대기",
}


def kind_labels() -> dict[str, str]:
    """문서 종류(서류 갈래) 이름 — 갈래 판정 규칙과 같은 자리(doc_routing.KINDS)에 있는 목록 하나."""
    from zzaimy.app.doc_routing import KINDS
    return dict(KINDS)


UNSORTED_KIND = "분류 전"
OTHER_KIND = "기타"


class TypeLabels(dict):
    """문서 유형 이름표 — 모르는 값도 영어 코드(notice·report …)를 그대로 보이지 않는다.

    `labels.get(code, code)` 처럼 코드를 기본값으로 넘겨도, 이 표에 없으면 문서 종류 이름(kind_labels, 라이브러리와
    같은 목록)으로, 거기에도 없으면 「기타」(빈 값은 「분류 전」)로 보인다. 화면마다 따로 고치지 않게 표 자체가 지킨다.
    """

    def get(self, key, default=None):                      # noqa: D401 — dict.get 과 같은 꼴
        if key in self:
            return self[key]
        if default is not None and default != key:        # 따로 정한 기본값(빈 값 등)은 그대로
            return default
        return doc_kind_label(key)


def doc_kind_label(code: str | None, fallback_labels: dict | None = None) -> str:
    """문서 유형·종류 코드의 우리말 이름 — 유형표(fallback_labels) → 종류 이름 → 「기타」/「분류 전」."""
    code = (code or "").strip()
    if not code:
        return UNSORTED_KIND
    if fallback_labels and code in fallback_labels:
        return fallback_labels[code]
    return kind_labels().get(code) or OTHER_KIND

# 파일 형식 갈래 — 원본 관리의 형식 선택지는 아래 갈래(학교 문서 원본이 되는 형식)만 보인다.
# 프로그램·시스템·웹 자료·백업 파일(exe·dll·css·rdf 같은 회계 프로그램 서식 등)은 원본 문서가 아니라서 「그 밖의 형식」 하나로 둔다.
# 확장자는 형식 갈래의 표준 확장자다. 오늘 장부에 있는 값을 옮겨 적은 것이 아니다.
# 처리 대상 문서 형식(zzaimy/doc_formats.DOC_EXT)은 모두 이 갈래 어딘가에 든다(tests/test_doc_formats 가 지킨다).
FORMAT_GROUPS = {
    "문서": ("hwp", "hwpx", "hwt", "pdf", "doc", "docx", "odt", "rtf", "txt", "md"),
    "표": ("xls", "xlsx", "xlsm", "xlt", "csv", "ods", "cell"),
    "발표": ("ppt", "pptx", "pptm", "ppsx", "odp", "show"),
    "웹 문서": ("html", "htm", "mht"),
    "메일": ("eml",),
    "그림": ("jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff", "webp", "heic", "svg", "ai", "psd"),
    "도면": ("dwg", "dxf"),
}
FORMAT_NOTE = {"mht": "웹 보관", "eml": "메일", "xlt": "엑셀 서식", "ai": "일러스트", "psd": "포토샵", "dwg": "도면", "dxf": "도면", "hwt": "한글 서식", "show": "한쇼", "cell": "한셀"}
OTHER_FORMAT = "other"
OTHER_FORMAT_LABEL = "그 밖의 형식"


def format_group(ext: str) -> str:
    e = (ext or "").lower().lstrip(".")
    return next((g for g, exts in FORMAT_GROUPS.items() if e in exts), "")


def format_label(ext: str) -> str:
    e = (ext or "").lower().lstrip(".")
    if e == OTHER_FORMAT:
        return OTHER_FORMAT_LABEL
    return f"{e.upper()}({FORMAT_NOTE[e]})" if e in FORMAT_NOTE else e.upper()


def original_formats() -> tuple[str, ...]:
    return tuple(e for exts in FORMAT_GROUPS.values() for e in exts)
