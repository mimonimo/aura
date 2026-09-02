# 한국어 OCR 엔진 후보 조사 (2026-09-02)

스캔 행정문서 OCR 인식률·구조 품질을 올릴 엔진 후보 조사와 판정.
현 스택: MinerU 3.4.5 pipeline 백엔드(PP-OCR 계열, 줄 좌표 활용) + Qwen3.5 비전 판독.

전제: 한국어 공개 수치가 있는 후보가 드물다. 어떤 판정이든 실물 30~50p
표본 대조(진행 중인 bake-off 틀) 후 ADR로 확정한다.

## 판정

| 후보 | 무엇 | 판정 | 근거 |
|---|---|---|---|
| MinerU hybrid-engine | VLM 레이아웃·표 + PP-OCRv5 한국어 인식 결합 | **보류** (스모크 패배) | 실물 1건(문서 173, 스캔형 매뉴얼) 대조 결과: 표 구조는 pipeline과 동일(표 6·행 21), 글자는 "Ⅱ유형"→"표유형" 오독, 본문 추출량 절반(2,423자 vs 4,804자). 이기지 못함 — pipeline 유지. 실제 교내 스캔 표본 확보 후 재대결 |
| dots.ocr (dots.mocr) | 3B급 단일 VLM 문서 파서, MIT | 2순위 — 표본 대조 후 | vLLM 0.11+ 공식 통합이라 현 스택에 모델 추가만. 다국어 문서 파싱 동급 최강. 단 좌표가 블록 단위(줄보다 굵음), 한국어 단독 수치 미공개 |
| PaddleOCR-VL 0.9B/1.5 | 0.9B 문서 파싱 VLM, Apache 2.0 | 3순위 | 한국어 명시 지원(109개 언어), OmniDocBench 94.5% SOTA. vLLM 서빙으로 Paddle의 aarch64 문제 우회. 레이아웃 조합 단계가 하나 더 필요 |
| KLOCR / KolmOCR | 한국어 특화 인식기 / 7B VLM | 보조 트랙 | 한국어 인식률 자체가 병목으로 실측되면 재검토. KLOCR 라이선스 미확인 |
| Surya v2 | 650M 통합 모델, 한국어 86.7% 공개 | 보류 | 줄 bbox 품질·한국어 공개 수치는 매력적이나 가중치 라이선스(수정 OpenRail-M)가 학교 실사용과 충돌하는지 미확인 |
| DeepSeek-OCR-2 | 컨텍스트 압축 OCR VLM, MIT | 관찰 | grounding 좌표는 유용하나 한국어 수치 미공개. dots.ocr 실측 후 필요 시 |
| olmOCR-2 | 영어 SOTA | 기각 | 학습에서 비영어 문서를 명시적으로 필터링 — 한국어 부적합 |
| GOT-OCR 2.0 | 580M OCR | 기각 | 영어·중국어 전용, 한국어 미지원 |
| PP-OCRv5 직접 구동 | 전통 파이프라인 최신 | 기각(직접 구동) | PaddlePaddle이 aarch64 GPU 바이너리 미제공 — Spark에서 수동 빌드 부담. MinerU 내부 경유로 이미 사용 중 |
| donut | OCR-free 문서 이해 | 기각 | 필드 추출용·bbox 없음·2022년 모델 |

## 절대 규칙 관련 주의

end-to-end VLM 계열(dots.ocr, DeepSeek-OCR)은 저품질 스캔에서 텍스트를
그럴듯하게 지어내는 환각 위험이 전통 파이프라인보다 높다. VLM 백엔드를
도입하면 원문 대조 검증을 OCR 출력에도 적용한다 (수치는 인출하고 생성하지
않는다 — 브리프 절대 규칙 1과 동일한 정신).

## 출처

- MinerU 백엔드·모델: https://opendatalab.github.io/MinerU/reference/changelog/ ·
  https://arxiv.org/html/2509.22186v1
- dots.ocr: https://github.com/rednote-hilab/dots.ocr · dots.mocr
- PaddleOCR-VL: https://huggingface.co/PaddlePaddle/PaddleOCR-VL ·
  https://arxiv.org/pdf/2510.14528
- 한국어 PP-OCRv5 인식 모델: https://huggingface.co/PaddlePaddle/korean_PP-OCRv5_mobile_rec
- Paddle aarch64 이슈: https://github.com/PaddlePaddle/Paddle/issues/76215
- Surya: https://github.com/datalab-to/surya
- KLOCR/KOCRBench: https://arxiv.org/abs/2510.02543 · KolmOCR: https://github.com/posicube-services/KolmOCR
- olmOCR 비영어 필터링: https://huggingface.co/allenai/olmOCR-7B-0225-preview/discussions/7
