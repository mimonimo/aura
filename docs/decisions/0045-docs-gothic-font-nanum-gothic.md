# ADR-0045: 독스 변환본의 고딕 글꼴은 Nanum Gothic

상태: 채택 · 날짜: 2026-09-30

## 배경

한글 → 독스 변환(hwpx_docx·hwp5_docx)과 md → 독스 변환(md_docx)은 한글의 고딕 계열 글꼴을 'Nanum Barun Gothic' 으로 바꿔
넣었다. md 변환본을 독스에서 PDF 로 내보내 보니 박힌 글꼴이 굴림이었고 한글 굵게가 사라져 있었다. 대화 18 작업본(9/29)도 같았다
(pdffonts: Gulim, NanumMyeongjo). 독스 글꼴 목록에 나눔바른고딕이 없어 독스가 굴림으로 대신 그린 것이다. 굴림에는 굵은 모양이 없다.

ADR-0036 이 잰 '나눔바른고딕 본문 줄 pitch 1.20em' 도 실제로는 굴림으로 그려진 것을 잰 값일 가능성이 크다(미확인).

## 측정 (scripts/154 경로 A, ZZAIMY_DOCS_SANS 로 글꼴만 바꿈, 2026-09-30)

독스 PDF 쪽수(원본 쪽수):

| 문서 | Nanum Barun Gothic(→굴림) | Nanum Gothic | Noto Sans KR |
|---|---|---|---|
| 작성서식 | 59 (62) | 58 | 58 |
| 사업계획서 | 103 (73) | 96 | 103 |
| 평가편람 | 24 (22) | 22 | 22 |

Nanum Gothic 은 NanumGothic·NanumGothicBold 로 박힌다(굵게가 살아난다). 글자 덮기는 셋 다 같다(95.9·96.7·100%).
LibreOffice 열람 PDF(VM 에 두 글꼴 모두 있음): 작성서식 63→63, 사업계획서 92→94, 평가편람 24→24.

## 결정

FONT_MAP 의 고딕을 'Nanum Gothic' 으로 한다. 명조는 그대로 'Nanum Myeongjo'(독스에 있고 박힌다). ZZAIMY_DOCS_SANS 로 계속
실험할 수 있다. 줄 간격 계수는 DOCS_LINE_EM_BY_FONT 의 Nanum Gothic 값(1.625)을 쓴다 — 위 측정이 이 값으로 잰 것이다.

## 남은 것

- 사업계획서는 여전히 원본보다 23쪽 많다. 원인 후보는 장평·자간(독스에 조절 수단 없음)과 표 안 줄 넘김이다.
- 원문자(①)·반각 가운뎃점(･)은 Nanum Gothic 에 글리프가 없어 다른 글꼴로 대체돼 그려진다(PDF 에 MS-PMincho·ShanHeiSun 이
  섞인다). 글자를 바꾸는 것은 원문 변경이라 하지 않았다.
- 이미 만든 작업본은 예전 글꼴 그대로다. 새로 뜨는 작업본부터 적용된다.
