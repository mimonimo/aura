"""결과보고서 시험 묶음 B — 235(가상 AI 실무인재 양성사업)와 짜임이 다른 두 번째 가상 사업.

왜(11-23 사용자): 「성과지표 같은 건 사업·문서마다 다른데 고정값을 찾으려 해도 되나」 — 한 사업으로만 시험하면 고침이 그 사업에 맞춰진다.
그래서 이름·개수·표 꼴·문서 표현을 일부러 바꾼다: 분야 대신 「과제」 3개, 월별 대신 「분기별」 표, 성과지표 대신 「핵심지표」 3개(목표치·달성치),
금액은 「천원」 단위 표, 문서 문장은 「수강생 n명이 수료」「참석자 n명」「상담 n건」「기관 n곳과 업무협약」 처럼 다르게, 집행 엑셀은 열 차례도 다르게.
정답은 truth.json 의 표마다(첫 열 이름 → 그 행에 있어야 할 수) — 채점은 238(첫 열 이름으로 행을 찾아 수 대조, 사업 이름에 묶이지 않음).
실물 문서와 섞지 않는다 — 사업·기관·사람 이름은 모두 가상이다.

  python scripts/237_report_bundle_fixture_b.py data/eval_sets/결과보고서B_가상2026
작년 보고서는 docx 로 만든다 — 운영 서버 변환(webhwp_convert.convert DOCX→HWPX)으로 blank.hwpx 를 만든다(235 와 같은 길).
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

PROGRAM = "2026 가상 지역 평생교육 활성화사업"
DEPT = "가상 평생교육원"
TASKS = ["지역 연계 강좌", "성인 학습자 지원", "평생교육 네트워크"]
SEMOK = ["강사료", "운영비", "홍보비", "재료비"]
BUDGET = 120_000_000
KPI = {"강좌 수료자(명)": 250, "학습 상담(건)": 120, "지역 기관 연계(곳)": 8}

# (월, 일, 과제, 이름, 참석자, 핵심지표 기여) — 참석자가 0 이면 참석 인원을 적지 않는 문서
EVENTS = [
    (4, 7, "지역 연계 강좌", "생활 속 디지털 기초 강좌", 36, {"강좌 수료자(명)": 31}),
    (4, 21, "평생교육 네트워크", "가상시 도서관·복지관 업무협약", 0, {"지역 기관 연계(곳)": 2}),
    (5, 12, "성인 학습자 지원", "학습 설계 상담 주간", 0, {"학습 상담(건)": 44}),
    (5, 26, "지역 연계 강좌", "시니어 스마트폰 활용 강좌", 28, {"강좌 수료자(명)": 25}),
    (6, 16, "평생교육 네트워크", "지역 평생교육 협의회 정례 모임", 19, {}),
    (7, 14, "지역 연계 강좌", "여름 인문학 특강", 52, {"강좌 수료자(명)": 47}),
    (8, 25, "성인 학습자 지원", "재직자 야간 학습 상담", 0, {"학습 상담(건)": 38}),
    (9, 8, "평생교육 네트워크", "가상구 주민자치센터 외 2곳 업무협약", 0, {"지역 기관 연계(곳)": 3}),
    (9, 22, "지역 연계 강좌", "가을 공예 체험 강좌", 33, {"강좌 수료자(명)": 30}),
    (10, 20, "성인 학습자 지원", "성인 학습자 진로 탐색 워크숍", 27, {"학습 상담(건)": 27}),
    (11, 10, "지역 연계 강좌", "지역 역사 탐방 강좌", 41, {"강좌 수료자(명)": 38}),
    (12, 1, "평생교육 네트워크", "평생교육 성과 나눔 한마당", 64, {"지역 기관 연계(곳)": 1}),
]
MEETINGS = [(4, 2, "사업 운영위원회 1차 회의"), (8, 6, "사업 운영위원회 2차 회의"), (12, 8, "사업 운영위원회 3차 회의")]


def _q(m: int) -> str:
    return f"{(m - 1) // 3 + 1}분기"


def _doc(path: Path, title: str, rows: list[tuple[str, str]], body: list[str]) -> None:
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
    d.save(path)


def _kpi_line(k: str, v: int) -> str:
    name, unit = k.split("(")[0], k.split("(")[1].rstrip(")")
    return f"이 결과는 핵심지표 「{name}」 달성치에 {v}{unit}으로 반영한다."


def build(out: Path) -> dict:
    rnd = random.Random(726)
    ev = out / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    task = defaultdict(lambda: {"운영 횟수": 0, "참석자": 0})
    qtr = defaultdict(lambda: {"참석자": 0})
    kpi = {k: 0 for k in KPI}
    docs = []
    for (m, d_, tk, name, ppl, kp) in EVENTS:
        date = f"2026년 {m}월 {d_}일"
        body = [f"{date}에 「{name}」을(를) 진행하였다."]
        if ppl:
            body.append(f"참석자는 {ppl}명이었고, 사후 설문에서 다수가 계속 참여 의사를 밝혔다.")
        for k, v in kp.items():
            if k == "강좌 수료자(명)":
                body.append(f"출석 기준을 채운 수강생 {v}명이 강좌를 수료하였다.")
            elif k == "학습 상담(건)":
                body.append(f"기간 중 학습 상담 {v}건을 진행하였다.")
            else:
                body.append(f"지역 기관 {v}곳과 업무협약을 맺었다.")
            body.append(_kpi_line(k, v))
            kpi[k] += v
        body.append("운영 중 나온 의견은 다음 운영 계획에 반영한다.")
        fn = ev / f"{name} 운영 결과서.docx"
        _doc(fn, f"{name} 운영 결과서", [("사업", PROGRAM), ("추진 과제", tk), ("운영일", date), ("주관", DEPT)], body)
        task[tk]["운영 횟수"] += 1
        task[tk]["참석자"] += ppl
        qtr[_q(m)]["참석자"] += ppl
        docs.append(fn.name)
    for (m, d_, name) in MEETINGS:
        fn = ev / f"{name} 결과.docx"
        _doc(fn, f"{name} 결과", [("사업", PROGRAM), ("일시", f"2026년 {m}월 {d_}일"), ("참석", "운영위원 5명")],
             ["안건: 과제별 추진 상황 점검과 다음 분기 운영 계획", "의견: 저녁 시간대 강좌를 늘리고 홍보를 지역 소식지로 넓히자는 의견이 나왔다."])
        docs.append(fn.name)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "지출"
    ws.append(["지출일자", "추진 과제", "세목", "내용", "지출액(원)"])
    semok = defaultdict(int)
    total = 0
    for (m, d_, tk, name, ppl, kp) in EVENTS:
        for s in rnd.sample(SEMOK, 2):
            amt = rnd.randrange(12, 70) * 50_000
            ws.append([f"2026.{m:02d}.{d_:02d}", tk, s, name, amt])
            semok[s] += amt
            total += amt
    fn = ev / "사업비 지출 내역(2026).xlsx"
    wb.save(fn)
    docs.append(fn.name)

    # 작년(2025) 결과보고서 — 짜임: Ⅰ 개요 / Ⅱ 과제별 / Ⅲ 분기별 / Ⅳ 핵심지표 / Ⅴ 사업비(천원) / Ⅵ 향후 계획
    old_over = [("사업명", PROGRAM.replace("2026", "2025")), ("사업 기간", "2025. 4. 1. ~ 2025. 12. 31."), ("총 사업비", "9,500만 원"), ("주관 부서", DEPT)]
    old_task = [("지역 연계 강좌", "4", "137", "디지털·인문 강좌 운영"), ("성인 학습자 지원", "2", "18", "상담 주간 2회"), ("평생교육 네트워크", "3", "46", "기관 협약 4곳")]
    old_qtr = [("2분기", "디지털 기초 강좌 외 2건", "71"), ("3분기", "인문학 특강 외 2건", "84"), ("4분기", "성과 나눔 행사 외 1건", "46")]
    old_kpi = [("강좌 수료자(명)", "180", "122", "67.8"), ("학습 상담(건)", "90", "73", "81.1"), ("지역 기관 연계(곳)", "6", "4", "66.7")]
    old_bud = [("강사료", "23,450", "34.9"), ("운영비", "19,800", "29.5"), ("홍보비", "9,150", "13.6"), ("재료비", "14,750", "22.0")]
    d = docx.Document()
    d.styles["Normal"].font.size = Pt(10)
    d.add_heading(f"{old_over[0][1]} 결과보고서", level=0)

    def table(rows):
        t = d.add_table(rows=0, cols=len(rows[0]))
        t.style = "Table Grid"
        for r in rows:
            cells = t.add_row().cells
            for j, v in enumerate(r):
                cells[j].text = v

    d.add_heading("Ⅰ. 사업 개요", level=1)
    table([list(r) for r in old_over])
    d.add_heading("Ⅱ. 과제별 추진 실적", level=1)
    d.add_paragraph("2025년에는 세 과제를 중심으로 강좌·상담·기관 협력을 추진하였다.")
    table([["추진 과제", "운영 횟수", "참석자(명)", "주요 내용"]] + [list(r) for r in old_task])
    d.add_heading("Ⅲ. 분기별 추진 실적", level=1)
    table([["분기", "주요 활동", "참석자(명)"]] + [list(r) for r in old_qtr])
    d.add_heading("Ⅳ. 핵심지표 달성도", level=1)
    table([["핵심지표", "목표치", "달성치", "달성도(%)"]] + [list(r) for r in old_kpi])
    d.add_heading("Ⅴ. 사업비 집행 현황", level=1)
    table([["세목", "집행액(천원)", "구성비(%)"]] + [list(r) for r in old_bud])
    d.add_paragraph("사업비는 총 67,150천원을 집행하여 집행률은 70.7%이다.")
    d.add_heading("Ⅵ. 향후 계획", level=1)
    d.add_paragraph("수료율이 낮은 야간 강좌는 운영 시간을 조정하고, 협약 기관과 공동 강좌를 늘린다.")
    d.save(out / "작년 결과보고서(2025).docx")
    (out / "결과보고서 작성 안내.txt").write_text(
        f"작년(2025) 결과보고서와 같은 짜임으로 {PROGRAM}의 2026년 결과보고서를 만든다. 작년 수치와 내용은 쓰지 않는다.\n"
        "근거는 올린 2026년 운영 결과서, 운영위원회 결과, 사업비 지출 내역 엑셀이다. 과제별·분기별 표는 결과서마다 확인한 값을 모아 합계를 낸다.\n"
        "사업비 표는 천원 단위로 적는다. 자료에 없는 값은 지어내지 말고 「확인 필요」로 둔다. 총 사업비는 1억 2천만 원이다.\n"
        "2026년 핵심지표 목표치: " + ", ".join(f"{k.split('(')[0]} {v}{k.split('(')[1].rstrip(')')}" for k, v in KPI.items()) + ".\n", encoding="utf-8")
    rate = round(total / BUDGET * 100, 1)
    truth = {
        "사업": PROGRAM,
        # 표마다 첫 열 이름 → 그 행에 있어야 할 수(여럿이면 모두). 채점은 첫 열 글로 행을 찾는다(238)
        "tables": {
            "과제별 운영 횟수·참석자": {k: [v["운영 횟수"], v["참석자"]] for k, v in task.items()},
            "분기별 참석자": {k: [v["참석자"]] for k, v in sorted(qtr.items())},
            "핵심지표 달성치": {k.split("(")[0]: {"달성치": v} for k, v in kpi.items()},   # 열 이름을 준 값은 그 열에서만 본다(목표치와 같은 수가 있어서)
            "세목별 집행액(천원)": {k: [f"{v // 1000:,}"] for k, v in semok.items()},
        },
        "본문": {"총 집행액(천원)": f"{total // 1000:,}", "집행률(%)": f"{rate}"},
        "정답 수(잔여 판단에서 뺌)": sorted({str(x) for v in task.values() for x in v.values()} | {str(v["참석자"]) for v in qtr.values()}
                                   | {str(v) for v in kpi.values()} | {f"{v // 1000:,}" for v in semok.values()}
                                   | {f"{total // 1000:,}", f"{rate}"} | {f"{round(kpi[k] / KPI[k] * 100, 1)}" for k in KPI}),
        "작년 값": sorted({v for r in old_task + old_qtr + old_kpi + old_bud for v in r[1:] if any(ch.isdigit() for ch in v) and len(v) >= 2}
                        | {"9,500만 원", "67,150", "70.7"}),
        "문서": docs,
        "참고(목표·사업비)": {"핵심지표 목표": KPI, "총 사업비(원)": BUDGET, "총 집행액(원)": total},
    }
    (out / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "meta.json").write_text(json.dumps({"project": f"{PROGRAM} 결과보고서(시험)", "sector": "grant"}, ensure_ascii=False), encoding="utf-8")
    return truth


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/eval_sets/결과보고서B_가상2026")
    t = build(out)
    print(out, "문서", len(t["문서"]), json.dumps(t["tables"], ensure_ascii=False), t["본문"])
