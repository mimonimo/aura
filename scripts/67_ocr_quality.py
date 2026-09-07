"""OCR 품질 점검 — 스캔 경로 문서의 의심 신호를 자동으로 훑는다.

무겁지 않게: DB의 조각 텍스트만 검사한다 (재파싱 없음, CPU 수 초).
신호: 한글·라틴 혼합 어절, 공백 빈약 조각 — 특정 단어 목록 없이 일반
패턴만 본다 (하드코딩 금지 원칙).
결과는 docs/ocr-quality-report.md — 개발 현황에서 열람한다.

실행: PYTHONPATH=src .venv/bin/python scripts/67_ocr_quality.py
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path

DB = Path("data/platform/platform.db")
REPORT = Path("docs/ocr-quality-report.md")

# 한글 음절 사이에 라틴 소문자가 낀 어절 — OCR 혼동의 전형
_MIXED = re.compile(r"[가-힣][a-z]{1,4}[가-힣]")


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    docs = {
        r["id"]: r["filename"]
        for r in conn.execute(
            "SELECT id, filename FROM documents WHERE status='reviewed'"
            " AND (parse_note LIKE '%OCR%' OR parse_note LIKE '%비전%')"
        )
    }
    rows = conn.execute(
        "SELECT doc_id, kind, content FROM doc_chunks WHERE doc_id IN"
        f" ({','.join('?' * len(docs))})", list(docs)
    ).fetchall() if docs else []

    mixed: Counter = Counter()
    sparse: Counter = Counter()
    samples: dict[str, str] = {}
    for r in rows:
        text = r["content"]
        if r["kind"] == "table":
            # 표 JSON은 셀 텍스트만 이어붙여 본다
            text = " ".join(re.findall(r'"([^"]{2,40})"', text))
        for m in _MIXED.finditer(text):
            token = text[max(0, m.start() - 4): m.end() + 4]
            mixed[r["doc_id"]] += 1
            samples.setdefault(f"m{r['doc_id']}", token)
        if r["kind"] == "text" and len(text) > 60:
            spaces = text.count(" ")
            if spaces * 20 < len(text):
                sparse[r["doc_id"]] += 1

    lines = [
        "# OCR 품질 점검",
        "",
        f"점검일 {date.today().isoformat()} · 대상: 스캔·비전 경로 문서"
        f" {len(docs)}건의 저장 조각",
        "",
        "| 문서 | 혼합문자 | 공백 빈약 | 예시 |",
        "|---|---|---|---|",
    ]
    flagged = sorted(docs, key=lambda d: -(mixed[d] * 3 + sparse[d]))
    any_issue = False
    for d in flagged:
        if mixed[d] + sparse[d] == 0:
            continue
        any_issue = True
        ex = samples.get(f"m{d}") or ""
        lines.append(
            f"| {docs[d][:30]} (#{d}) | {mixed[d]} | {sparse[d]} | {ex[:40]} |"
        )
    if not any_issue:
        lines.append("| (의심 신호 없음) | 0 | 0 | |")
    lines += [
        "",
        "혼합문자가 있는 문서는 재처리(신뢰도 재판독·교정 포함)로 대부분 해소된다.",
        "공백 빈약은 원문이 표·코드성 텍스트인 경우 오탐일 수 있다.",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[4:14]))
    print("QUALITY_DONE")


if __name__ == "__main__":
    main()
