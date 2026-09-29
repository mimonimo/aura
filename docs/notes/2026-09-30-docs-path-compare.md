# 한글 → 구글 독스 변환 경로 견주기

작성 2026-09-30(측정은 9/29 밤 VM). 스크립트 `scripts/154_docs_path_compare.py`, 수치 원본은 VM 의
`data/eval/docs_path_compare.json`. 올린 결과는 모두 드라이브 ZZAIMY/_변환실험 에 남겨 두었다(지우지 않음).

## 견준 경로

- A 지금 경로: 우리 변환기(hwpx_docx·hwp5_docx) → docx → 독스 변환 업로드. `gdrive_files.bytes_for_view` 그대로.
- B kordoc 4.15.7 마크다운(기본 출력, 병합 셀이 있는 표는 kordoc 이 HTML 표로 낸다) → text/markdown 으로 올려 독스 변환.
- B2 kordoc 마크다운(--html-tables, hwp 는 --inline-images) → markdown-it 으로 HTML 렌더, 표 테두리 CSS 한 줄만 덧붙임 → text/html 로 올려 독스 변환.

드라이브 API 는 text/markdown 업로드를 독스로 받아 준다. 거부되지 않았고, `#` 제목은 제목 문단으로, 파이프 표와 마크다운 안의 HTML 표(rowspan·colspan 포함)도 표로 들어왔다.

잣대는 독스 PDF 쪽수(원본은 dev-now ⑬⑭ 의 알려진 쪽수), 표 수(표 안의 표 포함), 병합 칸 수(rowSpan 또는 columnSpan 이 1 보다 큰 칸), 제목 문단 수, 그림 수(inlineObjects), 글자 덮기다. 글자 덮기는 kordoc 마크다운의 줄·표 칸을 정답 글 조각으로 보고(4자 이상, 목록 번호 제외) 정규화한 조각이 독스 본문에 들어 있는 비율이다. B·B2 는 같은 마크다운에서 나왔으니 덮기 100% 는 "독스가 버린 글이 없다"는 뜻이지 원본과 같다는 뜻은 아니다.

## 수치

| 문서 | 경로 | 독스 쪽(원본) | 표 | 병합 칸 | 제목 | 그림 | 글자 덮기 |
|---|---|---|---|---|---|---|---|
| 작성서식(hwpx) | A | 59 (62) | 125 | 1122 | 0 | 3 | 95.9% |
| 작성서식(hwpx) | B | 45 (62) | 65 | 397 | 0 | 3 | 100% |
| 작성서식(hwpx) | B2 | 36 (62) | 65 | 397 | 0 | 1 | 100% |
| 사업계획서(hwp) | A | 103 (73) | 165 | 1376 | 2 | 27 | 96.7% |
| 사업계획서(hwp) | B | 161 (73) | 161 | 634 | 103 | 29(깨짐) | 100% |
| 사업계획서(hwp) | B2 | 170 (73) | 162 | 634 | 103 | 29 | 100% |
| 평가편람(hwpx) | A | 24 (22) | 45 | 82 | 0 | 0 | 100% |
| 평가편람(hwpx) | B | 24 (22) | 45 | 40 | 0 | 0 | 100% |
| 평가편람(hwpx) | B2 | 20 (22) | 45 | 40 | 0 | 0 | 100% |

업로드 크기는 사업계획서 A 6.5MB, B 0.24MB, B2 51MB(그림을 data URI 로 넣어서). 변환 시간은 A 가 사업계획서 57초, 작성서식 20초로 가장 길고 kordoc 은 1초 안팎이다.

병합 칸 수는 A 쪽이 많은데, 이는 원본 병합을 더 살렸다기보다 우리 변환기가 한글의 격자(열 경계)를 그대로 옮겨 칸을 잘게 나누고 다시 합치기 때문이다(평가편람 A 는 1×2 병합이 43개인데 kordoc 쪽은 2개다. 대부분 이 경우로 보이며 칸별 대조는 미확인). kordoc 쪽 40개는 논리 병합만 센 값이다. 병합 칸 수만으로 우열을 가리지 않았다.

## 눈으로 본 것

쪽 그림은 1·3·8·15쪽을 60dpi 로 떠서 봤다(세션 scratchpad `paths/<문서>/<경로>-<쪽>.png`, 커밋 대상 아님).

A 는 셋 가운데 유일하게 "원본처럼" 보인다. A4 용지·원본 여백, 표 테두리 굵기, 머리 칸 음영, 병합 칸, 가운데 정렬, 표 제목 상자까지 살아 있다. 평가편람 3쪽은 원본 편람과 거의 같은 모양이다. 흠은 세 가지다.
- 글꼴에 없는 글자가 네모로 나온다. 한글 전용 사용자 영역 글자(원문자 ①)가 PDF 에서 깨진 글자로 남고, 반각 가운뎃점(･)이 일부 칸에서 ▣ 로 보인다. 덮기 4% 손실의 대부분이 이것이다.
- 사업계획서의 '26년 소계 같은 행에서 수치(327·601·510·1,044)가 빠진다. kordoc 과 B 에는 있다. hwp5_docx 의 칸 값 누락으로 보이며 원인은 미확인.
- 사업계획서 쪽수가 73 → 103 으로 부푼다(⑭ 에서 본 문제와 같은 줄기).

B 는 글은 다 들어오지만 모양이 원본과 멀다. 용지가 레터(612×792pt)·여백 72pt 로 떨어지고, 표 테두리가 전혀 없다(borderTop 폭 None). 열 폭 정보가 없어 넓은 표가 좁은 열로 눌려 글자가 세로로 한두 자씩 쌓인다(사업계획서 3·8쪽). 그림은 kordoc 이 따로 저장한 파일을 상대 경로로 가리키므로 독스가 가져오지 못해 크기 0 인 빈 그림 29개로 남는다(PDF 그림 0개).

B2 는 B 에 테두리·머리 칸 음영(th)만 더한 모양이다. 그림은 data URI 로 넣으니 독스가 제대로 받는다(PDF 그림 30개). 그래도 용지·열 폭 문제는 B 와 같아서 사업계획서는 170쪽, 신청서 표의 전화번호 칸이 한 글자 폭으로 눌린다. 작성서식처럼 빈 칸이 많은 양식은 줄 높이가 줄어 36쪽으로 원본보다 오히려 짧아지고, 빈 서식 칸(입력란)은 모양이 남지만 칸 크기는 원본과 다르다. 제목 상자처럼 한 줄짜리 레이아웃 표가 "2 | | 제목" 식의 3열 평표로 풀린다.

B·B2 가 A 보다 나은 점도 있다. 제목이 제목 문단으로 들어와(사업계획서 103개, A 는 2개) 독스 개요·목차가 바로 선다. 원문자·가운뎃점 같은 글자가 깨지지 않는다. 표의 칸 값이 빠지지 않는다.

## 판단

- 사람이 "원본처럼" 보고 고쳐 쓰는 작업본(양식·계획서·편람)은 A 를 유지한다. 모양 충실도에서 B·B2 와 격차가 크다.
- hwp → md → 독스(B·B2)는 열람용 작업본으로는 부족하다. 레터 용지, 열 폭 없음, 테두리 없음(B)이 구조적 한계이고, 이것을 채우려면 결국 docx 에서 하는 일을 HTML 로 다시 하게 된다. 글·표 구조·제목만 필요한 곳(에이전트가 읽는 사본, 검색용 요약, 초안 뼈대)에는 B2 가 충분하고 가볍다.
- 그림이 있는 문서를 md 경로로 보낼 때는 B 가 아니라 B2(--inline-images, data URI)여야 한다. B 는 그림이 모두 빈 자리로 남는다. 다만 그림이 많으면 HTML 이 커진다(사업계획서 51MB, 이번에는 통과).
- A 에서 고칠 것은 이번 측정으로 분명해졌다: 사용자 영역 원문자·반각 가운뎃점의 글꼴 대체(또는 표준 문자로 바꾸기), hwp5_docx 의 표 수치 누락, 사업계획서 쪽수 부풀림. kordoc 마크다운을 A 결과의 글자 대조 기준으로 쓰면(이 스크립트의 덮기 잣대) 회귀 점검에 바로 쓸 수 있다.
- A 의 제목 문단이 0~2개인 것은 독스 개요 탐색에 불리하다. kordoc 이 잡는 제목 위계를 A 의 docx 문단 스타일(제목 1~3)로 옮기는 것을 다음 후보로 둔다(모양은 그대로, 스타일만).

## 결과 문서(드라이브 ZZAIMY/_변환실험)

- 작성서식 A https://docs.google.com/document/d/1Bhkwk_MMRV18cXEwiTy7EPrHTAPf6qcPqUjiFtzSB2g/edit
- 작성서식 B https://docs.google.com/document/d/1V1Qu1FcJ-B7KIxfOrcMKmARgM4itsm3mZpwnWSPth7c/edit
- 작성서식 B2 https://docs.google.com/document/d/1rujtfcPphQJAk_tPKcUtJC7GZKZsNIS9UZwy-NFkTME/edit
- 사업계획서 A https://docs.google.com/document/d/1vnzmlyptwfq00R8FJ5YwdvdOogmiMrYQQ__4CcT5dBM/edit
- 사업계획서 B https://docs.google.com/document/d/1v2NPLm6jOnSgH5b1x_Py6dboGlCJA3BSmFYI1Y00Lhg/edit
- 사업계획서 B2 https://docs.google.com/document/d/1zsH1aZlCJsK2xhgXKlxtobGoBT6n7iMntRWroqFP_p0/edit
- 평가편람 A https://docs.google.com/document/d/1sd0n-BI6-ia05qj_xdPmMD6stljBh0UY58gk3F-ExNY/edit
- 평가편람 B https://docs.google.com/document/d/16oyrogt4Fnj59qSCkrcv_POqcClZTAD00JY0gqwUVVY/edit
- 평가편람 B2 https://docs.google.com/document/d/1BoS-DzITzMrE24bUSlC2vz25PliBGUpJPC6NEXhJtAM/edit

첫 시험 실행(평가편람만)에서 올린 세 벌(1FN9hc…, 13JxF1…, 1k4z3w…)도 같은 폴더에 남아 있다. 수치는 위 평가편람 행과 같다.

## 다시 돌리기

VM 저장소에서 `set -a; . ./.env.local; set +a; env PYTHONPATH=src .venv/bin/python scripts/154_docs_path_compare.py`.
잣대만 고쳤을 때는 `--remeasure` 로 JSON 에 적힌 독스를 올리지 않고 다시 잰다. 쪽 그림은 `--pages` 로 고르고 /tmp/pc/<문서>/ 에 남는다.
