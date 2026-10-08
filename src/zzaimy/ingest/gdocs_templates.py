"""구글 독스 공통 양식 4종 — 여러 사업이 함께 쓰는 독스 전용 판(K-20261008-01).

DGX 실문서 서식 23개(LINC 3.0·LINC+·LINC·혁신지원·특성화·앵커 5극3특·초광역·AID·신산업·부트캠프·도심캠퍼스타운·지방전문대 활성화)를
읽고 공통 뼈대를 뽑았다. 원본을 옮기지 않고 독스에 맞춰 다시 짠다 — 한글 서식을 docx 로 바꿔 올리면 넓은 표(14~20열, 2단 머리행)·
세로 병합·글상자·쪽 단위 표지 때문에 쪽이 넘어가고 표가 깨졌다.

독스 규칙:
  - 제목은 독스 제목 스타일(HEADING_1~3) — 목차·이동·에이전트의 절 찾기가 이것을 쓴다. 번호는 글자로(Ⅰ. 1. 가.) 적는다.
  - 표는 1단 머리행, 세로 병합 없음, 8열 이하, 열 너비 고정, 머리행은 쪽마다 반복. 넓은 표는 나눈다(재원 구분·지표 정의서 따로).
  - 강제 쪽 나눔·글상자·표 속 표 없음. A4·여백 20mm.
  - 절마다 「작성 지침」(회색 작은 글씨) — 27B 가 무엇을, 어떤 근거로, 얼마나 쓸지. 사업마다 다른 항목은 빈칸으로 둔다.
수치 규칙: 성과지표 값·예산 금액은 근거 문서에서 그대로 옮긴다(절대 규칙 1·5) — 지침에 적어 둔다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PAGE_W, PAGE_H, MARGIN = 595.28, 841.89, 56.7          # A4, 20mm
KV_KEY_MAX = 0.30                                        # 항목형 표의 항목 칸 상한(쪽 폭 비율)
HEAD_ABOVE = {"HEADING_1": 24, "HEADING_2": 16, "HEADING_3": 12}
CONTENT_W = PAGE_W - 2 * MARGIN
GUIDE_COLOR = {"red": 0.42, "green": 0.45, "blue": 0.50}
HEAD_BG = {"red": 0.91, "green": 0.93, "blue": 0.96}

# 근거 찾기 지침에 되풀이되는 말
SRC_PREV = "문서함에서 같은 사업의 이전 연차 계획서·실적보고서의 같은 절을 먼저 찾아 흐름을 잇는다"
NUM_RULE = "수치는 근거 문서의 값을 그대로 옮기고 출처(문서·쪽)를 괄호로 단다. 근거가 없으면 빈칸으로 두고 「확인 필요」라고 적는다"
BUDGET_RULE = "금액은 계산하지 말고 예산 문서·담당자 입력 값을 그대로 적는다(합계·비율도 예산 문서 값). 단위는 표 머리에 맞춘다"

YEARS4 = ["1차년도", "2차년도", "3차년도", "4차년도"]


def H(level: int, text: str) -> dict:
    return {"h": level, "text": text}


def G(text: str) -> dict:
    return {"guide": text}


def P(text: str = "") -> dict:
    return {"p": text}


def T(columns: list[str], rows: list[list[str]] | int = 3, widths: list[float] | None = None) -> dict:
    if isinstance(rows, int):
        rows = [[""] * len(columns) for _ in range(rows)]
    return {"table": {"columns": columns, "rows": rows, "widths": widths or [1] * len(columns)}}


def KV(items: list[str], key_w: float = 1, val_w: float = 3) -> dict:
    """항목·내용 2열 표(머리행 없이 항목 열이 머리 구실)."""
    return {"table": {"columns": ["항목", "내용"], "rows": [[k, ""] for k in items], "widths": [key_w, val_w]}}


# ── ① 사업계획서 ──────────────────────────────────────────────────────────────
PLAN = {
    "id": "plan", "title": "사업계획서 공통 양식(구글 독스)",
    "cover": ["「○○○○ 사업」 사업계획서", "○○대학교 · 20○○. ○."],
    "intro": "여러 재정지원사업 서식의 공통 뼈대다. 사업마다 정해진 서식 항목이 있으면 그 이름으로 바꾸고, 없는 절은 지운다. "
             "회색 「작성 지침」은 다 쓴 뒤 지운다.",
    "blocks": [
        H(1, "사업 개요"),
        G("한 쪽 이내. 공고·기본계획의 사업명·기간·지원 규모와 우리 대학 계획의 핵심만 적는다. " + NUM_RULE),
        KV(["사업명", "사업 기간", "총 사업비 (국비·지방비·대응자금)", "주관·참여 기관", "사업단장(책임자)", "사업 목표(한 줄)",
            "추진 과제(수)", "대표 성과지표"]),

        H(1, "Ⅰ. 사업 추진 목표"),
        H(2, "1. 대학 여건 및 현황 분석"),
        G("우리 대학·지역·산업의 현황을 최근 3년 지표로 보이고 강점·약점·기회·위협을 정리한다. " + SRC_PREV + ". " + NUM_RULE),
        T(["구분", "지표", "최근 3년 값", "출처"], 4, [1, 2, 2, 1.2]),
        T(["구분", "내용", "사업에 주는 시사점"], [["강점", "", ""], ["약점", "", ""], ["기회", "", ""], ["위협", "", ""]], [0.8, 2.6, 2]),
        H(2, "2. 비전·목표·인재상"),
        G("대학 중장기 발전계획의 비전과 이 사업의 목표를 잇는다. 목표는 측정할 수 있게(성과지표와 연결) 쓴다."),
        KV(["비전", "사업 목표", "인재상", "핵심 가치"]),
        H(2, "3. 추진 전략 및 과제 체계"),
        G("전략 → 추진 과제 → 세부 프로그램 → 연관 성과지표가 한 줄로 이어지게 쓴다. 과제 이름은 Ⅲ장·Ⅴ장 표와 같게 맞춘다."),
        T(["추진 전략", "추진 과제", "세부 프로그램", "연관 성과지표"], 4, [1.2, 1.4, 2, 1.4]),

        H(1, "Ⅱ. 사업 추진 체계"),
        H(2, "1. 추진 조직 및 거버넌스"),
        G("사업단·위원회·협의체의 구성과 의사결정 흐름을 적는다. 조직도 그림을 넣을 때는 표 대신 그림 한 장으로(글상자 금지)."),
        T(["조직", "구성", "주요 역할", "회의 주기"], 4, [1.2, 1.6, 2.4, 1]),
        H(2, "2. 참여 주체별 역할"),
        G("대학 부서·지자체·기업·참여 대학이 무엇을 맡는지 겹치지 않게 적는다."),
        T(["기관·부서", "역할", "참여 인력"], 4, [1.4, 3, 1.4]),
        H(2, "3. 사업단장 및 핵심 인력의 역량"),
        G("이 사업과 관련된 경력·실적만 적는다. 개인정보(연락처·생년월일)는 넣지 않는다."),
        T(["성명", "직위", "담당", "관련 경력·실적"], 3, [1, 1, 1.2, 3]),
        H(2, "4. 운영 규정 및 제도화"),
        G("사업 운영 규정·지침·학칙 개정 계획을 적는다. 제정·개정 예정이면 시기를 적는다."),
        P(),

        H(1, "Ⅲ. 사업 추진 계획"),
        G("추진 과제마다 「1. (추진 과제 1)」 묶음(개요 표·세부 내용·일정·기대 효과)을 복사해 쓴다. 과제 이름은 Ⅰ-3 표와 같게."),
        H(2, "1. (추진 과제 1) ○○○○"),
        H(3, "가. 과제 개요"),
        KV(["과제명", "목적·필요성", "주요 내용", "대상·규모", "추진 기간", "담당 조직", "예산(1차년도)", "연관 성과지표"]),
        H(3, "나. 세부 추진 내용"),
        G("프로그램별로 무엇을, 누구와, 어떻게 하는지 적는다. 이전 연차 실적이 있으면 무엇을 바꾸는지 함께 적는다. " + SRC_PREV + "."),
        P(),
        H(3, "다. 추진 일정"),
        G("분기(Q1~Q4) 또는 월로 적는다. 넓은 간트표 대신 이 표로 쓴다."),
        T(["세부 내용", "추진 시기", "산출물"], 4, [3, 1.2, 1.6]),
        H(3, "라. 기대 효과"),
        G("이 과제가 끝났을 때 달라지는 것을 정성·정량으로 적는다. 정량 효과는 Ⅳ장 성과지표 목표값과 맞춘다."),
        P(),
        H(2, "2. 과제 간·타 사업 연계 계획"),
        G("같은 대학의 다른 재정지원사업과 겹치는 프로그램은 역할을 나누고 중복 투자를 막는 방법을 적는다."),
        T(["연계 대상 사업", "연계 내용", "역할 분담", "중복 방지 방안"], 3, [1.4, 2, 1.6, 1.6]),

        H(1, "Ⅳ. 성과관리 계획"),
        H(2, "1. 성과관리 체계"),
        G("목표 설정 → 점검 → 자체평가 → 환류의 흐름과 담당 조직·주기를 적는다."),
        P(),
        H(2, "2. 성과지표 총괄표"),
        G("핵심(공통) 지표와 자율 지표를 모두 적는다. 기준값·목표값은 공고·협약 문서나 이전 계획서의 값을 그대로 옮긴다. " + NUM_RULE),
        T(["구분", "지표명(단위)", "기준값", *YEARS4, "측정 방법·증빙"], 5, [0.8, 1.8, 0.8, 0.7, 0.7, 0.7, 0.7, 1.4]),
        H(2, "3. 성과지표 정의서"),
        G("자율 지표마다 이 표를 하나씩 둔다(지표 수만큼 복사)."),
        KV(["지표명", "정의", "산출식", "기준값 근거", "목표 설정 근거", "측정 시기", "증빙 자료"]),
        H(2, "4. 성과 확산·환류 및 지속가능성"),
        G("사업 종료 뒤에도 이어질 운영 방법(학칙·예산 자체 부담·협약)을 적는다."),
        P(),

        H(1, "Ⅴ. 재정투자 계획"),
        H(2, "1. 재정투자 방향"),
        G("투자 우선순위와 과제별 배분의 이유를 적는다. " + BUDGET_RULE),
        P(),
        H(2, "2. 연차별 총 사업비"),
        G(BUDGET_RULE + ". 4년을 넘는 사업은 이 표를 두 개로 나눈다(열을 늘리지 않는다)."),
        T(["구분(단위: 백만원)", *YEARS4, "합계"], [["국비", "", "", "", "", ""], ["지방비", "", "", "", "", ""],
                                              ["대응자금", "", "", "", "", ""], ["합계", "", "", "", "", ""]], [1.6, 1, 1, 1, 1, 1]),
        H(2, "3. 추진 과제별 사업비"),
        T(["추진 과제(단위: 백만원)", *YEARS4, "합계", "비율(%)"], 4, [1.8, 0.8, 0.8, 0.8, 0.8, 0.9, 0.8]),
        H(2, "4. 비목별 편성(1차년도)"),
        G("비목 이름은 해당 사업의 예산 편성·집행 기준을 따른다. 산출 근거는 「단가 × 수량」 꼴로 적는다. " + BUDGET_RULE),
        T(["비목", "세부 산출 근거", "금액(원)", "비율(%)"],
          [["인건비", "", "", ""], ["교육·연구 프로그램 개발·운영비", "", "", ""], ["교육·연구 환경 개선비", "", "", ""],
           ["실험실습 장비 구입비", "", "", ""], ["기업 지원·협력 활동비", "", "", ""], ["그 밖의 사업 운영 경비", "", "", ""], ["합계", "", "", ""]],
          [1.6, 2.8, 1, 0.7]),
        H(2, "5. 집행 관리 및 중복 투자 방지"),
        G("예산 집행 절차·점검 주기·담당 조직과, 다른 재정지원사업·교비와 같은 항목에 이중으로 쓰지 않도록 가르는 기준을 적는다."),
        P(),

        H(1, "붙임. 증빙 자료 목록"),
        G("공고에서 요구한 필수·보관 증빙을 빠짐없이 적는다."),
        T(["연번", "증빙 자료명", "관련 항목", "비고"], 4, [0.5, 2.6, 1.6, 1]),
    ],
}

# ── ② 연차·종합 실적보고서 ─────────────────────────────────────────────────────
REPORT = {
    "id": "report", "title": "실적보고서(연차·종합) 공통 양식(구글 독스)",
    "cover": ["「○○○○ 사업」 ○차년도 실적보고서", "○○대학교 · 20○○. ○."],
    "intro": "연차 실적보고서·종합평가 보고서의 공통 뼈대다. 계획서의 과제·지표 이름과 같게 맞추고, 계획 대비 실적으로 쓴다. "
             "회색 「작성 지침」은 다 쓴 뒤 지운다.",
    "blocks": [
        H(1, "실적 요약"),
        G("한 쪽 이내. 핵심 성과 세 가지는 성과지표·실적 문서에 있는 것만 적는다. " + NUM_RULE),
        KV(["사업명", "보고 기간(차년도)", "사업비 집행률", "주요 성과(3가지)", "성과지표 달성(달성 지표 수/전체)", "차년도 중점 과제"]),

        H(1, "Ⅰ. 사업 개요 및 목표 달성 노력"),
        H(2, "1. 비전·목표와 추진 개요"),
        G("계획서의 비전·목표를 다시 적지 말고, 이번 연차에 무엇을 이루려 했는지와 그 결과를 요약한다. " + SRC_PREV + "."),
        P(),
        H(2, "2. 연차 계획 대비 추진 실적"),
        G("추진 과제마다 계획(계획서 문구)과 실적을 나란히 적는다. 달성 여부는 「달성·부분 달성·미달성」 중 하나."),
        T(["추진 과제", "계획", "실적", "달성 여부", "비고"], 4, [1.3, 1.8, 2, 0.8, 0.8]),

        H(1, "Ⅱ. 추진 과제별 실적"),
        G("추진 과제마다 「1. (추진 과제 1)」 묶음을 복사해 쓴다. 숫자(참여 인원·건수)는 실적 증빙 문서의 값만."),
        H(2, "1. (추진 과제 1) ○○○○"),
        H(3, "가. 추진 실적"),
        T(["세부 프로그램", "운영 기간", "참여 규모", "주요 실적"], 4, [1.6, 1.2, 1, 2.4]),
        H(3, "나. 성과와 계획 대비 달성"),
        G("계획서의 같은 과제 목표와 나란히 놓고 무엇을 얼마나 이뤘는지 적는다. 계획 값·실적 값은 두 문서의 값을 그대로 옮긴다."),
        P(),
        H(3, "다. 미흡 사항과 개선 방안"),
        G("계획보다 못 미친 점과 그 원인, 다음 연차에 바꿀 것을 적는다. 원인이 문서에 없으면 「확인 필요」라고 적는다."),
        P(),
        H(3, "라. 지자체·기관 연계 실적"),
        G("지자체·기업·타 대학과 함께 한 일을 적는다. 없으면 이 항목을 지운다."),
        P(),

        H(1, "Ⅲ. 성과지표 실적"),
        H(2, "1. 성과지표 실적 총괄"),
        G("계획서 성과지표 총괄표와 같은 순서로 적는다. 기준값·목표값은 계획서 값, 실적값은 실적 증빙 값을 그대로. "
          "달성률은 직접 계산하지 말고 시스템 계산 또는 담당자 확인 값을 적는다."),
        T(["구분", "지표명(단위)", "기준값", "목표값", "실적값", "달성률(%)", "증빙"], 5, [0.8, 1.9, 0.8, 0.8, 0.8, 0.8, 1]),
        H(2, "2. 자체평가"),
        G("지표별로 잘된 점과 부족한 점을 근거와 함께 짧게 적는다."),
        P(),
        H(2, "3. 미달 지표의 원인과 대책"),
        T(["지표", "미달 원인", "개선 대책", "이행 시기"], 3, [1.4, 2, 2, 1]),

        H(1, "Ⅳ. 사업비 집행 실적"),
        H(2, "1. 연차별 집행 현황"),
        G(BUDGET_RULE),
        T(["구분(단위: 백만원)", "예산", "집행액", "집행률(%)", "이월·불용"], [["국비", "", "", "", ""], ["지방비", "", "", "", ""],
                                                                     ["대응자금", "", "", "", ""], ["합계", "", "", "", ""]], [1.6, 1, 1, 1, 1]),
        H(2, "2. 추진 과제별 집행 현황"),
        T(["추진 과제(단위: 백만원)", "예산", "집행액", "집행률(%)", "비고"], 4, [1.8, 1, 1, 1, 1.2]),
        H(2, "3. 비목별 집행 현황"),
        T(["비목", "예산(원)", "집행액(원)", "집행률(%)", "주요 집행 내역"], 6, [1.6, 1, 1, 0.8, 2]),
        H(2, "4. 집행 관리의 적절성"),
        G("집행 점검 결과·지적 사항과 조치, 다른 사업과의 중복 집행이 없었음을 확인한 방법을 적는다. 수치는 집행 문서 값을 그대로 옮긴다."),
        P(),

        H(1, "Ⅴ. 사업 관리·운영"),
        H(2, "1. 사업단 조직·운영 실적"),
        G("사업단 조직 변동, 위원회·협의체 개최 실적(회수·안건)을 적는다. 개최 횟수는 회의록 수 그대로."),
        P(),
        H(2, "2. 성과관리·환류 실적"),
        T(["평가·점검 결과", "개선 조치", "반영 시기"], 3, [2.2, 2.6, 1]),
        H(2, "3. 구성원·이해관계자 의견 수렴"),
        T(["대상", "방법", "주요 의견", "반영 결과"], 3, [1, 1, 2.2, 2]),

        H(1, "Ⅵ. 환경 변화 대응 및 평가·컨설팅 결과 반영"),
        G("지난 평가·컨설팅의 지적 사항을 빠짐없이 옮기고 조치 결과를 적는다."),
        T(["지적·권고 사항", "조치 내용", "조치 결과"], 3, [2, 2.2, 1.6]),

        H(1, "Ⅶ. 차년도 추진 계획 및 지속가능성"),
        G("이번 연차의 미흡 사항이 차년도 계획에 어떻게 반영되는지 이어서 적는다."),
        P(),

        H(1, "첨부. 우수사례"),
        G("우수사례마다 이 묶음을 복사한다. 표·그림·그래프는 넣어도 되지만 글상자는 쓰지 않는다."),
        T(["사례명", "관련 과제", "기간", "참여 규모", "핵심 성과"], 1, [1.6, 1.2, 1, 0.9, 1.8]),
        H(2, "1. 추진 배경 및 개요"),
        G("왜 시작했는지, 누가 어떤 규모로 참여했는지 두세 문장으로 적는다."),
        P(),
        H(2, "2. 추진 과정"),
        G("준비 → 운영 → 마무리 순으로 시기와 함께 적는다."),
        P(),
        H(2, "3. 추진 성과"),
        G("정량 성과는 증빙 문서 값 그대로, 정성 성과는 참여자 변화·후속 연계로 적는다."),
        P(),
        H(2, "4. 기대 효과 및 향후 과제"),
        G("다른 과제·학과로 넓힐 방법과 남은 과제를 적는다."),
        P(),
    ],
}

# ── ③ 단위 프로그램 실시계획서 ────────────────────────────────────────────────
PROGRAM_PLAN = {
    "id": "program_plan", "title": "단위 프로그램 실시계획서 공통 양식(구글 독스)",
    "cover": ["「○○○○ 프로그램」 실시계획서", "○○학과(부서) · 20○○. ○."],
    "intro": "사업단 안의 단위 프로그램(교육·산학·취창업·기술개발 등)을 열기 전에 쓰는 계획서다. 사업의 예산 과목·성과지표와 이어지게 쓴다.",
    "blocks": [
        H(1, "1. 프로그램 개요"),
        G("예산 과목(사업/추진 과제/비목)은 사업단 예산 문서의 이름을 그대로 쓴다."),
        KV(["프로그램명", "사업명·추진 과제(예산 과목)", "목적", "운영 기간", "운영 장소·방식", "대상·인원", "주관 부서·담당자",
            "참여 기관·기업"]),
        H(1, "2. 추진 배경 및 필요성"),
        G("학생·기업·지역의 수요와 이전 운영 결과(있으면)를 근거로 적는다. " + SRC_PREV + "."),
        P(),
        H(1, "3. 세부 운영 내용"),
        T(["단계·차시", "일정", "내용", "담당·강사", "비고"], 4, [0.9, 1, 2.6, 1.2, 0.8]),
        H(1, "4. 참여 인력 및 역할"),
        G("개인정보(연락처·생년월일)는 넣지 않는다."),
        T(["구분", "성명", "소속·직위", "역할"], 4, [0.9, 1, 1.6, 2.4]),
        H(1, "5. 성과 목표"),
        G("사업 성과지표 가운데 이 프로그램이 기여하는 지표를 고른다. 목표값은 사업단 성과지표 계획의 값과 맞춘다."),
        T(["성과지표(단위)", "목표값", "측정 방법", "연계 사업 지표"], 3, [2, 0.9, 1.6, 1.6]),
        H(1, "6. 예산 계획"),
        G("산출 근거는 「단가 × 수량」 꼴. " + BUDGET_RULE),
        T(["비목", "산출 근거", "금액(원)", "비고"], 5, [1.4, 2.8, 1, 0.8]),
        H(1, "7. 기대 효과 및 성과 활용"),
        G("프로그램이 끝난 뒤 결과를 어디에(교육과정·후속 프로그램·성과지표) 쓰는지 적는다."),
        P(),
        H(1, "8. 붙임"),
        G("자문 계획서·협약서·참여 명단 등 붙일 서류 이름만 적는다."),
        T(["연번", "서류명", "비고"], 3, [0.5, 3.6, 1.4]),
    ],
}

# ── ④ 단위 프로그램 결과보고서 ────────────────────────────────────────────────
PROGRAM_REPORT = {
    "id": "program_report", "title": "단위 프로그램 결과보고서 공통 양식(구글 독스)",
    "cover": ["「○○○○ 프로그램」 결과보고서", "○○학과(부서) · 20○○. ○."],
    "intro": "운영을 마친 단위 프로그램의 결과보고서다. 실시계획서의 목표·예산과 견주어 쓴다.",
    "blocks": [
        H(1, "1. 프로그램 개요"),
        KV(["프로그램명", "사업명·추진 과제(예산 과목)", "실제 운영 기간", "운영 장소·방식", "참여 인원(계획/실제)", "주관 부서·담당자",
            "참여 기관·기업"]),
        H(1, "2. 추진 경과"),
        G("날짜는 실제 운영 기록(출석부·회의록·공문)의 날짜를 쓴다."),
        T(["일자", "내용", "참여 인원", "비고"], 5, [1, 3, 0.9, 0.9]),
        H(1, "3. 운영 내용 및 결과"),
        G("무엇을 했고 무엇이 나왔는지(결과물) 적는다. 사진은 문단 사이에 넣고 글상자로 감싸지 않는다."),
        P(),
        T(["결과물명", "형태", "활용처"], 3, [2.4, 1.2, 2.2]),
        H(1, "4. 성과 목표 달성"),
        G("실시계획서 「5. 성과 목표」 표와 같은 지표를 같은 순서로 적는다. " + NUM_RULE),
        T(["성과지표(단위)", "목표값", "실적값", "달성 여부", "증빙"], 3, [2, 0.9, 0.9, 0.9, 1.2]),
        H(1, "5. 참여자 만족도 및 의견"),
        T(["구분", "응답 수", "만족도", "주요 의견"], 3, [1, 0.8, 0.8, 3.2]),
        H(1, "6. 성과 및 개선 사항(CQI)"),
        G("담당 교수·산업체 인사가 함께 쓴다. 개선 방안은 다음 운영 때 무엇을 바꿀지로."),
        T(["잘된 점", "아쉬운 점", "개선 방안", "다음 운영 반영"], 3, [1.5, 1.5, 1.8, 1.2]),
        H(1, "7. 예산 집행 결과"),
        G(BUDGET_RULE),
        T(["비목", "계획 금액(원)", "집행 금액(원)", "집행률(%)", "집행 내역"], 5, [1.3, 1, 1, 0.8, 2]),
        H(1, "8. 붙임"),
        G("참여자 명단·사진·결과물 목록·정산서 등 붙일 서류 이름만 적는다(개인정보가 든 명단은 별도 파일로)."),
        T(["연번", "서류명", "비고"], 3, [0.5, 3.6, 1.4]),
    ],
}

SPECS = {s["id"]: s for s in (PLAN, REPORT, PROGRAM_PLAN, PROGRAM_REPORT)}


def check_spec(spec: dict) -> list[str]:
    """독스 규칙 점검 — 표는 8열 이하·열 너비 수가 맞고·머리말 낱말이 꺾이지 않게 들어가며,
    제목 단계가 건너뛰지 않고, 맨 아래 절마다 작성 지침이나 표가 있다."""
    errs, last = [], 0
    blocks = spec["blocks"]
    for i, b in enumerate(blocks):
        if "h" in b:
            nxt = next((j for j in range(i + 1, len(blocks)) if "h" in blocks[j]), len(blocks))
            leaf = nxt == len(blocks) or blocks[nxt]["h"] <= b["h"]
            if leaf and not any("guide" in x or "table" in x for x in blocks[i + 1:nxt]):
                errs.append(f"작성 지침·표가 없는 절: {b['text']}")
            if b["h"] > last + 1:
                errs.append(f"제목 단계 건너뜀: {b['text']}")
            last = b["h"]
        if "table" in b:
            t = b["table"]
            n = len(t["columns"])
            if n > 8:
                errs.append(f"표가 8열을 넘음: {t['columns']}")
            if len(t["widths"]) != n or any(len(r) != n for r in t["rows"]):
                errs.append(f"표 모양이 맞지 않음: {t['columns']}")
            elif t["columns"] != ["항목", "내용"] and sum(_min_width(h) for h in t["columns"]) > CONTENT_W:
                errs.append(f"머리말이 쪽 폭에 다 들지 않음: {t['columns']}")
            elif t["columns"] == ["항목", "내용"]:
                errs += [f"항목 이름의 낱말이 칸보다 김(빈칸으로 끊을 것): {r[0]}" for r in t["rows"] if _min_width(r[0]) > KV_KEY_MAX * CONTENT_W]
    return errs


def outline_text(spec: dict) -> str:
    """사양을 글로 — 27B 프롬프트·검토용(절·지침·표 머리)."""
    out = [f"# {spec['title']}", spec.get("intro", "")]
    for b in spec["blocks"]:
        if "h" in b:
            out.append("#" * (b["h"] + 1) + " " + b["text"])
        elif "guide" in b:
            out.append(f"(작성 지침) {b['guide']}")
        elif "table" in b:
            t = b["table"]
            out.append("| " + " | ".join(t["columns"]) + " |" + (" 항목: " + ", ".join(r[0] for r in t["rows"] if r[0]) if t["columns"] == ["항목", "내용"] else ""))
    return "\n".join(x for x in out if x is not None)


# ── 독스 빌더 ─────────────────────────────────────────────────────────────────

def _u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _text_pt(text: str, size: float = 10.0) -> float:
    """글자 폭 어림 — 한글·전각·가운뎃점은 글자 크기, 그 밖은 절반 남짓."""
    return sum(size if ord(c) > 0x2E7F or c == "·" else size * 0.55 for c in text)


def _words(text: str) -> list[str]:
    return text.split()                                          # 독스는 한글을 빈칸에서만 꺾는다(실측 2026-10-08)


def _min_width(head: str) -> float:
    """머리말의 가장 긴 낱말이 한 줄에 드는 칸 폭 — 칸 안쪽 여백·굵은 글씨 여유 14pt 포함."""
    return max([_text_pt(w) for w in _words(head)] + [0.0]) + 14.0


def fit_widths(heads: list[str], weights: list[float], kv: bool = False, total: float = CONTENT_W) -> list[float]:
    """칸 폭(pt). 칸마다 머리말의 가장 긴 낱말(빈칸·여는 괄호에서만 꺾임)이 한 줄에 들도록 최소 폭을 먼저 주고,
    남는 폭을 비율(weights)대로 나눈다. 항목형(kv)은 항목 칸이 가장 긴 항목 낱말을 담되 KV_KEY_MAX 를 넘지 않는다."""
    if kv:
        key = min(max([_min_width(h) for h in heads] + [0.0]), total * KV_KEY_MAX)
        key = max(key, total * weights[0] / sum(weights))
        return [round(key, 1), round(total - key, 1)]
    need = [_min_width(h) for h in heads]
    if sum(need) >= total:                                       # 칸이 너무 많으면 최소 폭 비율로 줄인다
        return [round(total * x / sum(need), 1) for x in need]
    fixed: set[int] = set()
    while True:                                                  # 최소에 못 미치는 칸은 최소로 묶고 나머지를 다시 나눈다
        rest = total - sum(need[i] for i in fixed)
        free = sum(weights[i] for i in range(len(heads)) if i not in fixed)
        w = [need[i] if i in fixed else rest * weights[i] / free for i in range(len(heads))]
        short = {i for i in range(len(heads)) if i not in fixed and w[i] < need[i]}
        if not short:
            return [round(x, 1) for x in w]
        fixed |= short


class _Builder:
    def __init__(self, email: str, doc: str, http):
        from zzaimy.ingest import gdocs
        self.g, self.email, self.doc, self.http = gdocs, email, doc, http
        self.inserts: list[dict] = []
        self.styles: list[dict] = []
        self.cur = self._end()

    def _end(self) -> int:
        r = self.g._read(self.email, self.doc, self.http)
        self.g._raise(r)
        body = self.g.body_content(r.json())
        return int(body[-1]["endIndex"]) - 1

    def para(self, text: str, style: str = "NORMAL_TEXT", guide: bool = False, center: bool = False) -> None:
        s = self.cur
        self.inserts.append({"insertText": {"location": {"index": s}, "text": text + "\n"}})
        e = s + _u16(text) + 1
        ps = {"namedStyleType": style}
        fields = ["namedStyleType"]
        if center:
            ps["alignment"] = "CENTER"
            fields.append("alignment")
        if style == "NORMAL_TEXT":
            ps.update({"lineSpacing": 140, "spaceBelow": {"magnitude": 4, "unit": "PT"}})
            fields += ["lineSpacing", "spaceBelow"]
        elif style.startswith("HEADING_"):                      # 표 바로 뒤에서도 띄우고, 쪽 맨 아래에 제목만 남지 않게
            ps.update({"spaceAbove": {"magnitude": HEAD_ABOVE.get(style, 12), "unit": "PT"}, "keepWithNext": True})
            fields += ["spaceAbove", "keepWithNext"]
        self.styles.append({"updateParagraphStyle": {"range": {"startIndex": s, "endIndex": e}, "paragraphStyle": ps,
                                                     "fields": ",".join(fields)}})
        if guide and text:
            self.styles.append({"updateTextStyle": {"range": {"startIndex": s, "endIndex": e - 1},
                                                    "textStyle": {"italic": True, "fontSize": {"magnitude": 9.5, "unit": "PT"},
                                                                  "foregroundColor": {"color": {"rgbColor": GUIDE_COLOR}}},
                                                    "fields": "italic,fontSize,foregroundColor"}})
        self.cur = e

    def flush(self) -> None:
        if self.inserts or self.styles:
            self.g._batch(self.email, self.doc, self.inserts + self.styles, self.http)
        self.inserts, self.styles = [], []

    def table(self, columns: list[str], rows: list[list[str]], widths: list[float]) -> None:
        self.flush()
        kv = columns == ["항목", "내용"]
        grid = rows if kv else [columns] + rows
        n_rows, n_cols = len(grid), len(columns)
        self.g._batch(self.email, self.doc, [{"insertTable": {"location": {"index": self.cur}, "rows": n_rows, "columns": n_cols}}], self.http)
        r = self.g._read(self.email, self.doc, self.http)
        self.g._raise(r)
        body = self.g.body_content(r.json())
        el = next(e for e in body if e.get("table") and int(e["startIndex"]) >= self.cur - 1)
        ts = int(el["startIndex"])
        fills = []
        for ri, row in enumerate(el["table"]["tableRows"]):
            for ci, cell in enumerate(row["tableCells"]):
                txt = grid[ri][ci] if ci < len(grid[ri]) else ""
                if txt:
                    fills.append((int(cell["content"][0]["startIndex"]), txt, ri, ci))
        reqs = [{"insertText": {"location": {"index": i}, "text": t}} for i, t, _r, _c in sorted(fills, reverse=True)]
        if reqs:
            self.g._batch(self.email, self.doc, reqs, self.http)
        style: list[dict] = []
        for ci, w in enumerate(fit_widths([r[0] for r in rows] if kv else columns, widths, kv=kv)):
            style.append({"updateTableColumnProperties": {
                "tableStartLocation": {"index": ts}, "columnIndices": [ci],
                "tableColumnProperties": {"widthType": "FIXED_WIDTH", "width": {"magnitude": w, "unit": "PT"}},
                "fields": "widthType,width"}})
        head = {"tableRange": {"tableCellLocation": {"tableStartLocation": {"index": ts}, "rowIndex": 0, "columnIndex": 0},
                               "rowSpan": (n_rows if kv else 1), "columnSpan": (1 if kv else n_cols)},
                "tableCellStyle": {"backgroundColor": {"color": {"rgbColor": HEAD_BG}}}, "fields": "backgroundColor"}
        style.append({"updateTableCellStyle": head})
        if not kv:
            style.append({"pinTableHeaderRows": {"tableStartLocation": {"index": ts}, "pinnedHeaderRowsCount": 1}})
        self.g._batch(self.email, self.doc, style, self.http)
        # 글자 꼴 — 표 전체 10pt, 머리(행 또는 항목 열) 굵게
        r = self.g._read(self.email, self.doc, self.http)
        body = self.g.body_content(r.json())
        el = next(e for e in body if e.get("table") and int(e["startIndex"]) == ts)
        ts_end = int(el["endIndex"])
        fx = [{"updateTextStyle": {"range": {"startIndex": ts + 1, "endIndex": ts_end - 1}, "textStyle": {"fontSize": {"magnitude": 10, "unit": "PT"}},
                                   "fields": "fontSize"}}]
        for ri, row in enumerate(el["table"]["tableRows"]):
            for ci, cell in enumerate(row["tableCells"]):
                if (kv and ci == 0) or (not kv and ri == 0):
                    a, b = int(cell["content"][0]["startIndex"]), int(cell["content"][-1]["endIndex"]) - 1
                    if b > a:
                        fx.append({"updateTextStyle": {"range": {"startIndex": a, "endIndex": b}, "textStyle": {"bold": True}, "fields": "bold"}})
        self.g._batch(self.email, self.doc, fx, self.http)
        self.cur = self._end()


def build(email: str, spec: dict, folder_id: str | None = None, http=None, replace: bool = True) -> dict:
    """사양 → 구글 독스. 같은 폴더에 같은 이름의 문서가 있으면(replace) 휴지통으로 보내고 새로 만든다. {id, url, title}."""
    from zzaimy.ingest import gdrive, gdrive_files
    errs = check_spec(spec)
    if errs:
        raise ValueError("; ".join(errs))
    http = http or gdrive._http()
    title = spec["title"]
    if replace and folder_id:
        old = gdrive_files.find_in_folder(email, title, folder_id, http=http)
        if old:
            http.patch(f"{gdrive.API}/files/{old['id']}", headers=gdrive_files._headers(email, http),
                       params={"supportsAllDrives": "true"}, json={"trashed": True})
    doc = gdrive_files.create_document(email, title, folder_id, http=http)
    render(email, doc, spec, http)
    return {"id": doc, "url": f"https://docs.google.com/document/d/{doc}/edit", "title": title}


def render(email: str, doc: str, spec: dict, http=None) -> None:
    """사양을 이미 있는 (빈) 독스 문서 끝에 깐다 — 쪽 모양·표지 줄·절·지침·표. 초안 대화가 만든 문서에도 쓴다."""
    from zzaimy.ingest import gdocs, gdrive
    http = http or gdrive._http()
    gdocs._batch(email, doc, [{"updateDocumentStyle": {"documentStyle": {
        "pageSize": {"width": {"magnitude": PAGE_W, "unit": "PT"}, "height": {"magnitude": PAGE_H, "unit": "PT"}},
        "marginTop": {"magnitude": MARGIN, "unit": "PT"}, "marginBottom": {"magnitude": MARGIN, "unit": "PT"},
        "marginLeft": {"magnitude": MARGIN, "unit": "PT"}, "marginRight": {"magnitude": MARGIN, "unit": "PT"}},
        "fields": "pageSize,marginTop,marginBottom,marginLeft,marginRight"}}], http)
    b = _Builder(email, doc, http)
    for line in spec.get("cover", []):
        b.para(line, "SUBTITLE", center=True)
    if spec.get("intro"):
        b.para("작성 지침 — " + spec["intro"], guide=True)
    for blk in spec["blocks"]:
        if "h" in blk:
            b.para(blk["text"], f"HEADING_{blk['h']}")
        elif "guide" in blk:
            b.para("작성 지침 — " + blk["guide"], guide=True)
        elif "p" in blk:
            b.para(blk["p"])
        elif "table" in blk:
            t = blk["table"]
            b.table(t["columns"], t["rows"], t["widths"])
    b.flush()


# 지시문 → 양식. 앞에서부터 처음 맞는 것(모든 패턴이 맞아야). 단위 프로그램을 사업 문서보다 먼저 본다.
PICK = [
    ("program_report", (r"프로그램|특강|캠프|행사|교육과정|워크숍", r"결과\s*보고|운영\s*결과|결과서")),
    ("program_plan", (r"프로그램|특강|캠프|행사|워크숍", r"실시\s*계획|운영\s*계획|계획서|계획안")),
    ("report", (r"실적\s*보고|성과\s*보고|연차\s*보고|결과\s*보고서|자체\s*평가\s*보고",)),
    ("plan", (r"사업\s*계획|수정\s*계획서|사업\s*신청서|계획서",)),
]


def pick(text: str) -> dict | None:
    """초안 지시문이 어느 공통 양식에 해당하는가 — 서류 갈래 낱말로만(사업 이름 규칙 없음). 없으면 None."""
    t = text or ""
    for sid, pats in PICK:
        if all(re.search(p, t) for p in pats):
            return SPECS[sid]
    return None


def export_specs(out_dir: Path) -> list[Path]:
    """사양을 JSON·글로 내보낸다 — 27B 프롬프트·검토용."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for sid, spec in SPECS.items():
        p = out_dir / f"{sid}.json"
        p.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
        (out_dir / f"{sid}.md").write_text(outline_text(spec), encoding="utf-8")
        paths.append(p)
    return paths
