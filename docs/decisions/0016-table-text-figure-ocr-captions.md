# 0016. 표·그림 문맥 추출 계약 — 표 평문, 그림 속 글자(CPU OCR), 캡션 부착

- 상태: 확정 (사용자 요청 2026-09-15: "표, 표 안 내용, 그림, 그림 속 내용, 문맥을 잘 가져와야 함")
- 날짜: 2026-09-15
- 관련: ADR-0007(OCR 표준 파이프라인), `src/zzaimy/app/pipeline.py`, `render.py`,
  `drafter.py`, `ingest/parsers/{mineru,hwpx,hwp5}.py`, `scripts/79_ocr_structure_check.py`

## 맥락 — 지금까지의 실제 동작 (코드 감사)

- 표 셀은 JSON(`{n_rows,n_cols,cells}`)으로만 저장됐다. 초안 재료(drafter)는 그 JSON을
  600자로 잘라 모델과 수치 검증기에 넘겼으므로, 표 속 수치 대부분은 근거 허용목록에
  들어가지 못했고 행·열 인덱스(0,1,2…)가 허용 수치로 새어 들어갔다. 맥락 분석(analyze)도
  `[표] {JSON 800자}`를 모델에 줬다.
- 본문 텍스트(`masked_text`, 규정 조각 분할, 검토 입력)는 모든 표를 문서 끝에 몰아 붙였다.
  표가 앞 문단·캡션과 떨어져 규정 조각에서 따로 놀았다.
- 그림은 파일로만 저장됐다(자산). 그림 속 글자(차트 축·도식 라벨·삽입 스캔)는 어디에도
  없었다. 운영 VM에 tesseract(kor)가 설치돼 있었지만 쓰이지 않았다.
- MinerU가 주는 `table_caption`/`table_footnote`는 읽지 않았고, `img_caption`은 페이지
  본문에만 남겨 구조 조각(entries 경로)에서는 사라졌다. 캡션 탐지는 어디에도 없었다.
- HWP는 `hwp5txt`가 표를 `<표>` 자리표시로 접어 신청서·계획서의 내용이 통째로 사라졌고,
  HWPX는 태그를 전부 걷어내 셀이 줄글로 흩어졌다.

## 결정

1. 표 JSON에 검색·인용용 평문 `text`를 함께 저장한다, 캡션 줄, 행마다 ` | `로 이은 셀
   (병합 셀 값은 병합 범위 전체에 채워 열·행이 머리글 맥락을 잃지 않게), 각주 줄.
   `caption`·`note`·`col_w`도 같은 JSON에 둔다. 셀이 바뀌면(오타 교정·캡션 부착) `text`를
   다시 만든다(`_finish_table_payload`). 소비자는 `render.table_text()`로 읽는다 —
   저장본이 없으면 셀에서 만들고, JSON이 아니면 원문 그대로(구버전 호환).
2. 초안 재료·맥락 분석은 표를 이 평문으로 받는다. 그러면 표 속 수치가 수치 검증기의
   허용목록에 그대로 들어간다(절대 규칙 1). 재료 종류에 `image_text`를 더한다.
3. 그림 속 글자는 새 조각 종류 `image_text`로 저장한다, 그림(`image`, content=파일명)
   바로 뒤에 같은 page_no·bbox로, 내용은 "캡션 줄 + OCR 줄들"의 평문. 기존 `image`
   계약(파일명)은 그대로라 뷰어·docx·검색 PDF가 깨지지 않고, 모르는 소비자는 문단으로
   보인다. 인풋 문서에서는 다른 글자와 똑같이 PII 마스킹·오타 교정을 거친다.
4. 그림 OCR은 tesseract CLI(CPU)로: `kor+eng` 중 설치된 언어, `--psm 3`, TSV 출력에서
   단어 신뢰도 55 미만과 글자 비율이 낮은 줄을 버린다(MinerU 저신뢰 줄 처리와 같은 원리).
   그림 1장 60초, 문서당 150초 예산을 넘기면 나머지는 생략하고, 도구가 없으면 조용히
   건너뛴다(`ZZAIMY_NO_IMAGE_OCR=1`로 끌 수 있다). 스레드는 `OMP_THREAD_LIMIT` 2로 제한.
   비전 모델(VLM)로 그림을 설명하는 일은 GPU(DGX) 이후다, CPU VLM은 실측상 비실용.
5. 본문 텍스트는 읽기 순서로 만든다(`_compose_text`), 문단·제목 사이 제자리에
   표(캡션+행)와 그림(`[그림] 캡션` + 글자). 구조 항목이 없는 파서는 페이지 본문 뒤에
   그 페이지의 표. 규정 조각 분할·검토 입력·masked_text가 이 텍스트를 쓴다.
6. 캡션은 두 경로로 붙는다. (a) 파서가 주면 그대로: MinerU `table_caption/footnote`,
   `img_caption/footnote`, HWPX `hp:caption`. (b) 없으면 이웃 조각에서 일반 패턴으로 —
   표 앞의 짧은 '표 N…' 줄과 단위 줄(최대 2), 표 뒤의 `※·주)·출처·자료` 줄, 그림 앞뒤의
   '그림 N…' 줄. 붙인 줄은 본문 조각에서 빼 중복을 없앤다. 문서별 규칙·단어 목록은 없다.
7. 한글 문서는 구조 파서로 읽는다. HWPX는 OWPML XML을 직접 걷어 문단(스타일명이
   개요·제목이면 heading)·표(`cellAddr/cellSpan/header`, 셀 폭→열 폭 비율)·그림(BinData
   추출)·캡션을 낸다. HWP 5.0은 `hwp5html` 변환본(표·병합·셀 폭 mm·그림)을 걷는다.
   둘 다 실패하면 이전 텍스트 경로(태그 제거 / hwp5txt)로 폴백한다.

## 결과·한계

- 회귀 잠금: `tests/test_ocr_structure.py` (표 평문·수치 검증 통과·캡션 부착·읽기 순서·
  TSV 해석·도구 부재 시 생략·HWPX/HWP 구조·뷰어/내보내기/분석 입력).
- 실문서 검증은 VM에서 `scripts/79_ocr_structure_check.py <doc_id>`로 한다(기본은 DB에
  쓰지 않음, `--write`로 반영). 다음 항목은 VM 실측 전까지 미확인: tesseract kor 인식
  품질과 소요 시간, MinerU 2.x content_list의 캡션 필드 실제 형태, hwp5html의 VM 동작
  (lxml 의존), 그림 OCR이 규정 조각 검색 지표에 미치는 영향.
- 스캔 표의 열 폭·비전 기반 그림 설명은 여전히 GPU 이후(품질 체계 표의 기존 항목).
- 기존 문서는 재처리(`scripts/61_reprocess_all.sh` 또는 79 `--write`)해야 새 계약(표
  `text`·`image_text`)이 채워진다. 재처리 전 문서는 `table_text()`가 셀에서 즉석 생성하므로
  초안·분석은 그대로 동작한다.
