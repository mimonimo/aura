# 스캔 전처리·복원 후보 조사 (2026-09-02)

OCR 인식률을 올리기 위한 전처리 오픈소스 조사 결과와 판정.
현재 체인은 OpenCV 원근 보정 + CLAHE + 언샤프뿐이다. 조사 상세 출처는 말미.

공통 전제: 효과 수치는 전부 영어·중국어 벤치마크 기준이고 한글 행정문서
실측은 없다. 도입 판정이 나더라도 실물 표본 A/B 후 ADR로 확정한다.
sm_121 실동작도 전 후보 미확인 — 스모크 테스트가 선행돼야 한다.

## 판정

| 후보 | 역할 | 판정 | 근거 |
|---|---|---|---|
| DocRes (CVPR 2024, MIT) | 그림자·조명·디블러·dewarping·이진화 통합 복원 | 도입 후보 1순위 | 단일 모델·CLI 추론으로 폰카 그림자/조명 불균일을 해결. 지금 체인이 못 하는 영역 |
| UVDoc (MIT) | 곡면·접힌 문서 펴기 | 도입 후보 2순위 | 경량, 선 직진성 1위 — 표 구조 보존과 직결. DocRes의 dewarping과 실물 맞대결 후 승자만 채택 |
| Real-ESRGAN x2 (BSD-3) | 저해상도 표본 한정 초해상도 | 조건부 도입 | 2025 정부 문서 시스템 실전 채택 사례. 해상도 게이트(300dpi 미만)로 분기 투입. x4는 텍스트 뭉갬 위험 |
| 도장 적색 감쇠 (OpenCV 휴리스틱) | OCR 입력에서 관인 간섭 제거 | 실물 표본 확보 후 도입 | 비용 거의 0이지만 전면 적색 제거는 빨간 강조 글자까지 지운다 — 기존 도장 탐지(_extract_stamps) 영역 안에서만 감쇠. 제거본은 OCR 입력 전용, 원본 보관 |
| NAF-DPM | 심한 디블러 표본 한정 | 관찰 | DocDiff 대비 CER 절반이나 무겁다. 하드케이스 발생 시 재검토 |
| textbsr / MARCONet | 텍스트 특화 SR | 보류 | CC BY-NC 라이선스(실사용 저촉 여지) + 중국어 학습이라 한글 글자 환각 위험 |
| SauvolaNet 등 이진화 | 이진화 | 비도입 | 최신 OCR은 그레이스케일 선호 — 강제 이진화는 해로울 수 있음. 만능 이진화는 없다는 비교 연구 결론 |
| unpaper | 테두리 노이즈 제거 | 보류 | 도장·표 테두리를 노이즈로 오인할 위험이 행정문서에서 큼 |
| stamp_processing | 도장 딥러닝 제거 | 보류 | 베트남 문서 학습 — 한국 관인은 재학습 필요. 휴리스틱으로 부족할 때 재검토 |

## 안전 원칙

생성형 복원(SR·diffusion)은 글자·숫자를 바꿔칠 수 있다. 절대 규칙 1(수치는
인출)과 같은 정신으로, 전처리 전/후 OCR 결과의 수치 diff 검사를 파이프라인에
넣은 뒤에만 생성형 복원을 통과시킨다.

## 실측 근거 (참고)

- PreP-OCR(ACL 2025): 이미지 복원만으로 Tesseract CER 5.87%→1.99%,
  파이프라인 전체 CER 63.9~70.3% 감소 (영어권 실물 13,831쪽)
- UVDoc: MS-SSIM 0.785 (DocTr 0.697, DocGeoNet 0.706)

## 출처

- DocRes: https://github.com/ZZZHANG-jx/DocRes
- UVDoc: https://github.com/tanguymagne/UVDoc
- DocDiff(도장 1,597개 데이터셋 동봉): https://github.com/Royalvice/DocDiff
- NAF-DPM: https://github.com/ispamm/NAF-DPM
- PreP-OCR: https://aclanthology.org/2025.acl-long.749/
- textbsr: https://github.com/csxmli2016/textbsr
- Real-ESRGAN 정부 문서 적용 사례: https://arxiv.org/pdf/2510.13303
- 이진화 공정 비교: https://arxiv.org/abs/2401.11831
- stamp_processing: https://github.com/sun-asterisk-research/stamp_processing
- ocrmypdf cookbook: https://ocrmypdf.readthedocs.io/en/latest/cookbook.html
