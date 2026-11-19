"""결과보고서 시험 묶음 만들기 — 가상 사업의 1년치 진행 문서 더미(운영 결과·회의록·출장·협약·집행 내역 엑셀·만족도)와
빈 결과보고서 서식(docx), 작성 지침, 정답 집계(truth.json)를 만든다.

왜(11-20 사용자): TF 는 사업 진행 중 쌓인 여러 문서를 묶음으로 주고 「월별·분야별로 정리한 결과보고서」를 시킬 것이다.
문서가 많아 한 번에 문맥에 들어가지 않고, 합계는 어느 한 문서에도 없다. 정답을 아는 가상 자료로 모으기·집계·근거 표시를 잰다.
실물 문서와 섞지 않는다 — 사업·기관·사람 이름은 모두 가상이다(「가상」 접두).

  python scripts/235_report_bundle_fixture.py data/eval_sets/결과보고서_가상2026
빈 서식은 docx 로 만든다. hwpx 가 필요하면 운영 서버에서 기안기 필터 서버로 바꾼다(webhwp_convert.convert).
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import docx
import openpyxl
from docx.shared import Pt

PROGRAM = "2026 가상 AI 실무인재 양성사업"
DEPT = "가상 산학협력단"
FIELDS = ["교육과정 개발", "산학협력", "학생 역량강화", "교육 인프라"]
BIMOK = ["인건비", "장학금", "교육·연구 프로그램 개발 운영비", "기자재 구입비", "기타 사업운영경비"]
KPI = {"교육 이수 인원(명)": 300, "산업체 협약(건)": 10, "취업 연계(명)": 40, "교과목 개발(개)": 6}

EVENTS = [  # (월, 일, 분야, 이름, 참여 인원, 만족도 5점, 특기)
    (3, 18, "교육과정 개발", "AI 기초 교과목 개발 착수 워크숍", 24, 4.3, {"교과목 개발(개)": 0}),
    (4, 9, "학생 역량강화", "파이썬 기초 집중 캠프(1기)", 42, 4.5, {"교육 이수 인원(명)": 42}),
    (4, 23, "산학협력", "가상테크 외 3개사 산학협력 협약 체결", 0, None, {"산업체 협약(건)": 4}),
    (5, 14, "교육과정 개발", "데이터 분석 실무 교과목 2과목 개발 완료", 0, None, {"교과목 개발(개)": 2}),
    (5, 28, "학생 역량강화", "AI 자격증 대비 특강", 55, 4.1, {"교육 이수 인원(명)": 55}),
    (6, 11, "교육 인프라", "AI 실습실 GPU 서버 구축", 0, None, {}),
    (6, 25, "산학협력", "산업체 현장 전문가 초청 세미나", 61, 4.4, {}),
    (7, 8, "학생 역량강화", "하계 AI 프로젝트 부트캠프", 38, 4.7, {"교육 이수 인원(명)": 38, "취업 연계(명)": 6}),
    (7, 22, "산학협력", "가상로보틱스 외 2개사 협약 체결", 0, None, {"산업체 협약(건)": 3}),
    (8, 19, "교육과정 개발", "머신러닝 응용·AI 윤리 교과목 개발 완료", 0, None, {"교과목 개발(개)": 2}),
    (9, 10, "학생 역량강화", "파이썬 기초 집중 캠프(2기)", 47, 4.4, {"교육 이수 인원(명)": 47}),
    (9, 24, "교육 인프라", "실습실 협업 학습 공간 개선", 0, None, {}),
    (10, 15, "산학협력", "기업 연계 현장실습 매칭 데이", 52, 4.2, {"취업 연계(명)": 14}),
    (10, 29, "학생 역량강화", "AI 경진대회", 33, 4.6, {"교육 이수 인원(명)": 33}),
    (11, 12, "교육과정 개발", "생성형 AI 활용 교과목 개발 완료", 0, None, {"교과목 개발(개)": 1}),
    (11, 26, "산학협력", "가상데이터 외 1개사 협약 체결", 0, None, {"산업체 협약(건)": 2}),
    (12, 3, "학생 역량강화", "동계 취업 연계 직무 특강", 49, 4.3, {"교육 이수 인원(명)": 49, "취업 연계(명)": 11}),
    (12, 17, "산학협력", "사업 성과 공유회", 70, 4.5, {}),
]
MEETINGS = [(3, 5, "사업 추진위원회 1차 회의"), (6, 4, "사업 추진위원회 2차 회의"), (9, 3, "사업 추진위원회 3차 회의"), (12, 9, "자체평가위원회 회의")]
TRIPS = [(5, 21, "가상광역시", "가상테크 현장 방문(현장실습 협의)"), (10, 7, "가상특별시", "AI 교육 박람회 참관")]


def _doc(path: Path, title: str, rows: list[tuple[str, str]], body: list[str], table: list[list[str]] | None = None) -> None:
    d = docx.Document()
    d.styles["Normal"].font.size = Pt(10)
    d.add_heading(title, level=1)
    t = d.add_table(rows=0, cols=2)
    t.style = "Table Grid"
    for k, v in rows:
        c = t.add_row().cells
        c[0].text, c[1].text = k, v
    for para in body:
        d.add_paragraph(para)
    if table:
        tt = d.add_table(rows=0, cols=len(table[0]))
        tt.style = "Table Grid"
        for r in table:
            cells = tt.add_row().cells
            for j, v in enumerate(r):
                cells[j].text = str(v)
    d.save(path)


def build(out: Path) -> dict:
    rnd = random.Random(2026)
    ev = out / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    truth = {"사업": PROGRAM, "월별": defaultdict(lambda: {"행사": 0, "참여 인원": 0}), "분야별": defaultdict(lambda: {"행사": 0, "참여 인원": 0, "집행액": 0}),
             "성과지표": {k: {"목표": v, "실적": 0} for k, v in KPI.items()}, "비목별 집행액": defaultdict(int), "총 집행액": 0, "문서": []}
    n = 0
    for (m, dday, field, name, people, sat, kpi) in EVENTS:
        n += 1
        date = f"2026. {m}. {dday}."
        fn = ev / f"{name} 결과 보고.docx"            # 파일 이름에 월·분야를 넣지 않는다 — 실제 문서처럼 본문에서 읽어야 한다
        rows = [("사업명", PROGRAM), ("분야", field), ("일시", date), ("장소", "가상대학교 AI 실습동"), ("담당", f"{DEPT} 담당자")]
        body = [f"{date} {name}을(를) 운영하였다."]
        if people:
            body.append(f"참여 인원은 학생 {people}명이며, 만족도 조사 결과 5점 만점에 {sat}점이었다.")
        for k, v in kpi.items():
            if v:
                body.append(f"이번 활동으로 성과지표 「{k.split('(')[0]}」 실적 {v}{k.split('(')[1].rstrip(')')}을(를) 거두었다.")
                truth["성과지표"][k]["실적"] += v
        body.append("향후 참여 학생 의견을 반영해 다음 차수 운영 방식을 보완할 계획이다.")
        _doc(fn, f"{name} 결과 보고", rows, body)
        truth["월별"][m]["행사"] += 1
        truth["월별"][m]["참여 인원"] += people
        truth["분야별"][field]["행사"] += 1
        truth["분야별"][field]["참여 인원"] += people
        truth["문서"].append(fn.name)
    for (m, dday, name) in MEETINGS:
        n += 1
        fn = ev / f"{name} 회의록.docx"
        _doc(fn, f"{name} 회의록", [("사업명", PROGRAM), ("일시", f"2026. {m}. {dday}."), ("참석", "위원 7명")],
             ["안건 1: 분기 추진 실적 점검", "안건 2: 다음 분기 프로그램 일정 확정", "결정: 원안대로 추진하고 예산 집행률을 다음 회의에서 다시 점검한다."])
        truth["문서"].append(fn.name)
    for (m, dday, place, name) in TRIPS:
        n += 1
        fn = ev / f"출장 결과 보고({name}).docx"
        _doc(fn, f"출장 결과 보고({name})", [("사업명", PROGRAM), ("기간", f"2026. {m}. {dday}. (1일)"), ("출장지", place), ("출장자", "담당 교원 2명")],
             [f"{name} 목적으로 출장하였다.", "현장실습 수요와 교육과정 개선 의견을 수렴하였다."])
        truth["문서"].append(fn.name)
    # 집행 내역(엑셀) — 분야·비목·월별
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "집행내역"
    ws.append(["집행일", "분야", "비목", "적요", "금액(원)"])
    for (m, dday, field, name, people, sat, kpi) in EVENTS:
        for b in rnd.sample(BIMOK, 2):
            amt = rnd.randrange(8, 60) * 100_000
            ws.append([f"2026-{m:02d}-{dday:02d}", field, b, name, amt])
            truth["분야별"][field]["집행액"] += amt
            truth["비목별 집행액"][b] += amt
            truth["총 집행액"] += amt
    n += 1
    fn = ev / "예산 집행 내역(2026.3.~12.).xlsx"
    wb.save(fn)
    truth["문서"].append(fn.name)
    truth["예산"] = {"총 사업비(원)": 300_000_000}
    truth["집행률(%)"] = round(truth["총 집행액"] / 300_000_000 * 100, 1)

    # 서식 = 지난해(2025) 완료 결과보고서 — TF 는 빈 서식 대신 작년 완성본을 주고 「뼈대만 남기고 올해 내용으로」를 시킨다(11-20 사용자).
    # 옛 값(2025 실적)은 새 보고서에 남으면 안 된다 — truth.json 의 「작년 값」 으로 잔여를 잰다
    old = {"사업명": PROGRAM.replace("2026", "2025"), "사업 기간": "2025. 3. 1. ~ 2026. 2. 28.", "총 사업비": "250,000,000원", "주관 부서": DEPT}
    old_month = [("4월", "AI 입문 특강", "31", "04_AI 입문 특강 결과 보고"), ("7월", "하계 코딩 캠프", "44", "09_하계 코딩 캠프 결과 보고"),
                 ("10월", "산학 협약 체결(2개사)", "0", "13_협약 체결 보고"), ("12월", "성과 공유회", "58", "17_성과 공유회 결과 보고")]
    old_field = [("교육과정 개발", "3", "0", "교과목 3개 개발"), ("산학협력", "4", "58", "협약 5건"), ("학생 역량강화", "5", "163", "이수 163명"), ("교육 인프라", "1", "0", "실습실 1곳 개선")]
    old_kpi = [("교육 이수 인원(명)", "200", "163", "81.5"), ("산업체 협약(건)", "6", "5", "83.3"), ("취업 연계(명)", "30", "21", "70.0"), ("교과목 개발(개)", "4", "3", "75.0")]
    old_bud = [("인건비", "61,200,000", "29.1"), ("장학금", "38,000,000", "18.1"), ("교육·연구 프로그램 개발 운영비", "70,500,000", "33.5"),
               ("기자재 구입비", "30,100,000", "14.3"), ("기타 사업운영경비", "10,400,000", "5.0")]
    truth["작년 값"] = sorted({v for r in old_month + old_field + old_kpi + old_bud for v in r[1:] if any(ch.isdigit() for ch in v) and len(v) >= 2}
                             | {"250,000,000원", "2025. 3. 1. ~ 2026. 2. 28."})
    d = docx.Document()
    d.styles["Normal"].font.size = Pt(10)
    d.add_heading(f"「{old['사업명']}」 결과보고서", level=0)

    def table(rows):
        t = d.add_table(rows=0, cols=len(rows[0]))
        t.style = "Table Grid"
        for r in rows:
            cells = t.add_row().cells
            for j, v in enumerate(r):
                cells[j].text = v

    d.add_heading("1. 사업 개요", level=1)
    table([[k, v] for k, v in old.items()])
    d.add_heading("2. 추진 실적", level=1)
    d.add_heading("2.1 월별 추진 실적", level=2)
    d.add_paragraph("2025년에는 4월 AI 입문 특강을 시작으로 하계 코딩 캠프, 산학 협약 체결, 성과 공유회를 차례로 운영하였다.")
    table([["월", "주요 활동", "참여 인원(명)", "근거 문서"]] + [list(r) for r in old_month])
    d.add_heading("2.2 분야별 추진 실적", level=2)
    table([["분야", "활동 수", "참여 인원(명)", "대표 성과"]] + [list(r) for r in old_field])
    d.add_heading("3. 성과지표 달성 실적", level=1)
    table([["성과지표", "목표", "실적", "달성률(%)"]] + [list(r) for r in old_kpi])
    d.add_paragraph("취업 연계 지표는 목표에 못 미쳤으나 교육 이수 인원은 목표의 81.5%를 달성하였다.")
    d.add_heading("4. 예산 집행 실적", level=1)
    table([["비목", "집행액(원)", "비율(%)"]] + [list(r) for r in old_bud])
    d.add_paragraph("총 집행액은 210,200,000원으로 집행률은 84.1%이다.")
    d.add_heading("5. 성과 및 개선 사항", level=1)
    d.add_paragraph("산학 협약 기업과 현장실습을 연계한 점이 주요 성과이며, 2학기 프로그램 참여율이 낮아 홍보 방식을 개선할 필요가 있다.")
    d.save(out / "작년 결과보고서(2025).docx")
    (out / "지침.txt").write_text(
        f"올린 「작년 결과보고서(2025)」의 틀(절·표)만 살리고 내용은 비운 뒤, {PROGRAM} 2026년 결과보고서로 채운다. "
        "근거는 올린 2026년 진행 문서(운영 결과 보고, 회의록, 출장 보고, 집행 내역 엑셀)뿐이며 작년 값은 쓰지 않는다.\n"
        "월별·분야별 표는 문서마다 확인한 값을 모아 합계를 낸다. 문서에 없는 값은 만들지 말고 「확인 필요」로 남긴다.\n"
        "표의 각 행에는 근거 문서 이름을 적는다. 총 사업비는 3억 원이다.\n", encoding="utf-8")
    truth["월별"] = {str(k): v for k, v in sorted(truth["월별"].items())}
    truth["분야별"] = dict(truth["분야별"])
    truth["비목별 집행액"] = dict(truth["비목별 집행액"])
    (out / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "meta.json").write_text(json.dumps({"project": f"{PROGRAM} 결과보고서(시험)", "sector": "grant"}, ensure_ascii=False), encoding="utf-8")
    return truth


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/eval_sets/결과보고서_가상2026")
    t = build(out)
    print(out, "문서", len(t["문서"]), "총 집행액", t["총 집행액"], "집행률", t["집행률(%)"])
