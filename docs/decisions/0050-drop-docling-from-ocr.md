# ADR-0050: docling 을 판독(OCR) 경로에서 뺀다

- 상태: 채택(3항은 ADR-0051 로 대체)
- 날짜: 2026-10-02
- 관련: ADR-0007(문서 추출 표준 — 스캔 PDF 「실패 시 docling 폴백」 줄을 대체), ADR-0033(kordoc 읽기 어댑터), `docs/ocr-duel.md`

## 맥락

DGX 원본 보관소의 PDF 25,997건을 가볍게 처리했더니 15,543건이 글자층 없는 스캔본이었다. 스캔 판독 경로는 MinerU 구조 추출 →
(실패 시) docling → 글이 빈약하면 MinerU OCR 이었는데, docling 이 한국어를 깨진 글로라도 많이 내면 「글이 충분하다」로 통과했다.
사업 문서 24건(사업마다 하나, 본문 3쪽)을 같은 쪽·같은 잣대로 견줬다(scripts/171, 어절 F1, 세 엔진 모두 성공한 23건).

| 엔진 | 어절 F1 평균 | 쪽당 |
|---|---|---|
| Writer 27B 비전 | 0.839 | 165초(토르, 다른 일과 겹침) |
| MinerU(korean, ocr) | 0.690 | 7.6초(DGX GPU) |
| docling(기본 OCR) | 0.334 | 5.6초 |

사용자: "docling 품질 안 좋으면 제외하고", "제외된 거는 정리 잘 하고, 지저분하게 놔두지 말고".

## 결정

1. 스캔 PDF 는 MinerU 로만 읽는다. 구조 추출이 실패하면 MinerU OCR, 그래도 없으면 실패로 남긴다(잡음을 넣지 않는다).
2. 사진·스크린샷은 Writer 비전 → MinerU OCR → tesseract. docling 은 쓰지 않는다.
3. docling 은 오피스 문서(docx·xlsx·pptx)의 파일 구조 읽기에만 남긴다 — OCR 이 아니다. kordoc(한글·오피스 자체 파서, ADR-0033)과
   같은 문서로 대조한 뒤(아스트라 담당) 대체 여부를 정한다. 대체되면 이 ADR 을 이어 쓰는 새 ADR 로 docling 의존성을 지운다.
4. 핵심 문서(그래프·성과지표에 쓰이는 계획서·보고서·평가)는 Writer 비전으로 다시 읽는 구도 — 대량 판독용 27B 는 DGX(사용자 결정, 기동은 지시 뒤).

## 결과

- 코드: `app/pipeline.py` 의 스캔 PDF·사진 경로에서 docling 제거(커밋 2bfaaf3, 이 ADR 커밋). 측정 도구 171 의 기본 엔진은 MinerU.
- 의존성: `pyproject.toml` 의 docling 은 오피스 읽기 때문에 남는다. DGX `.venv-parse` 의 docling 도 같은 이유로 남긴다.
- ADR-0007 표의 「실패 시 docling 폴백」은 이 ADR 로 대체됐다.
