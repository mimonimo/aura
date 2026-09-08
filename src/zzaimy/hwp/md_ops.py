"""Markdown 초안 → 한글 에이전트 op 시퀀스.

ADR-0014: 문서 생성의 중간표현은 Markdown이고, 한글 COM 에이전트가 이 op
시퀀스를 받아 실제 한글에서 문서를 만든다. 이 모듈은 순수 변환(부작용 없음)
이라 한글 없이 단위 테스트한다.

지원 블록:
- 제목/소제목  `# 제목`, `## 소제목`  → 텍스트 삽입 + 굵게·크기(best-effort)
- 본문 문단    일반 줄                → 텍스트 삽입
- 표          `| a | b |` + `|---|`  → insert_table + fill_table

캐럿 안전: 블록마다 먼저 `goto end`(MoveDocEnd)를 보낸다. 표 뒤에 캐럿이
표 안에 남아도 다음 블록이 문서 끝에 붙는다. (한컴 캐럿 동작은 실장비 1회 확인.)
"""

from __future__ import annotations

import re

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")

# 소제목 레벨별 글자 크기(pt). 실제 조판은 한글이 하며 값은 초안 기준.
_HEADING_SIZE = {1: 16, 2: 14, 3: 13}


def _split_row(line: str) -> list[str]:
    """`| a | b |` 한 줄을 셀 값 리스트로. 양끝 파이프·공백 제거."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_table_line(line: str) -> bool:
    return line.strip().startswith("|") and line.count("|") >= 2


def md_to_ops(md: str) -> list[dict]:
    """Markdown 문자열을 [{op, args}, ...] 시퀀스로 변환한다.

    반환 op는 에이전트 화이트리스트(goto/set_title/insert_text/set_format/
    insert_table/fill_table)만 사용한다. 앞에 new_doc/open, 뒤에
    export_artifact는 호출측이 붙인다.
    """
    lines = (md or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ops: list[dict] = []
    i = 0
    n = len(lines)

    def goto_end() -> None:
        ops.append({"op": "goto", "args": {"where": "end"}})

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 빈 줄 — 문단 사이 간격(빈 문단 하나)
        if not stripped:
            i += 1
            continue

        # 표: 헤더줄 + 구분줄(---) + 본문줄들
        if _is_table_line(line) and i + 1 < n and _TABLE_SEP.match(lines[i + 1]):
            rows = [_split_row(line)]
            j = i + 2
            while j < n and _is_table_line(lines[j]):
                rows.append(_split_row(lines[j]))
                j += 1
            cols = max(len(r) for r in rows)
            rows = [r + [""] * (cols - len(r)) for r in rows]  # 열 수 맞춤
            goto_end()
            ops.append({"op": "insert_table",
                        "args": {"rows": len(rows), "cols": cols}})
            ops.append({"op": "fill_table", "args": {"cells": rows}})
            i = j
            continue

        # 제목/소제목
        m = _HEADING.match(line)
        if m:
            level = len(m.group(1))
            text = m.group(2)
            goto_end()
            ops.append({"op": "insert_text", "args": {"text": text + "\n"}})
            ops.append({"op": "set_format", "args": {
                "find": text, "bold": True,
                "size": _HEADING_SIZE.get(level, 12)}})
            i += 1
            continue

        # 본문 문단 — 연속된 비어있지 않은 줄을 한 문단으로 모으지 않고
        # 줄 단위로 넣는다(원문 줄바꿈 보존). 필드형 "키: 값"도 그대로.
        goto_end()
        ops.append({"op": "insert_text", "args": {"text": stripped + "\n"}})
        i += 1

    return ops


def author_sequence(md: str, *, template_path: str | None = None,
                    export: tuple[str, ...] = ("pdf", "hwpx")) -> list[dict]:
    """완전한 저작 시퀀스: (양식 열기 | 새 문서) → 내용 op → 산출물 내보내기.

    template_path 가 있으면 그 양식을 열어 채우고, 없으면 새 문서를 만든다.
    export 로 지정한 포맷마다 export_artifact 를 붙여 미리보기·다운로드용
    산출물을 서버로 회수하게 한다.
    """
    seq: list[dict] = []
    if template_path:
        seq.append({"op": "open", "args": {"path": template_path}})
    else:
        seq.append({"op": "new_doc", "args": {}})
    seq.extend(md_to_ops(md))
    for fmt in export:
        seq.append({"op": "export_artifact", "args": {"format": fmt}})
    return seq
