# A. 문서 파싱 / OCR

> 조사일: 2026-08-15 · 조사자: CC
> 전체 품질의 70%가 결정되는 최우선 영역. 평가 축: **표 구조 보존, 한국어 스캔본 정확도, 문서 계층 추출, 배치 처리 성능, aarch64 동작 여부**(1차 필터).

---

## 결론 요약

| 구분 | 선정 | 핵심 근거 |
|---|---|---|
| **추천 1안** | **MinerU 3.x** (VLM 백엔드 + 파이프라인 백엔드 병용) | 문서 파싱 특화 도구 중 최대 생태계(77.7k★), 2026-04 AGPL→Apache 2.0 기반 라이선스 전환, DGX Spark 동작 사례 확인, 한국어 명시 지원(PP-OCRv6, 37개 언어) |
| **대안 1안** | **Docling** (IBM) | MIT 라이선스로 법적 위험 최소, arm64 공식 지원 명시, TableFormer 표 구조 인식(복잡 표 93.6% 주장), OCR 엔진 교체형 구조(RapidOCR 한국어) |
| **보조 후보** | PaddleOCR-VL 1.5/1.6 (0.9B VLM) | OmniDocBench 최상위권 + 109개 언어(한국어 명시) + Apache 2.0. 단, 레이아웃 단계의 PaddlePaddle 의존이 aarch64에서 위험 |
| **필수 후속 조치** | **한국어 실물 문서 bake-off** | 어떤 후보도 "한국어 공공 행정문서" 기준 공개 벤치마크가 없음. Phase 2 초입에 실제 결과보고서 표본 30~50페이지(표 위주)로 1안·대안·보조를 직접 비교 후 확정 |

**중요**: 아래 성능 수치는 전부 **각 프로젝트의 자체 측정치**이며 독립 검증이 아니다. 한국어 문서에 대한 수치는 어떤 후보도 공개 근거가 없다 → 자체 평가셋으로만 확정 가능.

---

## 1차 필터: aarch64 (DGX Spark = Grace ARM64 + Blackwell GB10, sm_121)

| 후보 | aarch64 상태 | 근거 |
|---|---|---|
| MinerU (VLM 백엔드) | ⭕ 동작 확인 | GitHub Discussion #4770 (2026-04): 초기엔 CPU만 사용됐으나 vLLM nightly aarch64 이미지 기반으로 GB10 GPU 동작 확인. vLLM 자체가 DGX Spark 공식 지원(vLLM 블로그 2026-06) |
| MinerU (파이프라인 백엔드) | 🔶 미확인(가능성 높음) | OCR 모델이 PP-OCR의 PyTorch 포트로 구동되어 PaddlePaddle 런타임 불필요. PyTorch는 aarch64+CUDA 공식 지원. **Spark 실측 필요** |
| Docling | ⭕ 공식 명시 | 공식 문서에 amd64/arm64 지원 명시. PyTorch 기반 |
| PaddleOCR-VL (VLM부) | 🔶 조건부 | VLM 인식부는 vLLM 서빙 가능. 그러나 레이아웃 검출(PP-DocLayout 계열)이 PaddlePaddle 의존 |
| PaddleOCR / PaddlePaddle GPU | ❌ 공식 휠 없음 | paddlepaddle-gpu는 aarch64 공식 바이너리 미제공(Issue #72578). DGX Spark에서 CUDA 13 수동 컴파일 성공 사례(2026-04)만 존재 — 운영 경로로 부적합 |
| dots.ocr | ⭕ 가능 | transformers/vLLM 서빙 — PyTorch 기반 |
| DeepSeek-OCR | ⭕ 가능 | vLLM 업스트림 공식 지원(2025-10) |
| olmOCR | ⭕ 가능(무의미) | vLLM 기반 7B — 동작은 가능하나 한국어에서 탈락(하단) |
| marker / Surya | ⭕ 가능(무의미) | PyTorch 기반 — 라이선스에서 탈락(하단) |
| unstructured | ⭕ 가능 | 순수 Python 조합 라이브러리 |

---

## 후보별 상세

### 1. MinerU — 추천 1안

- **저장소**: https://github.com/opendatalab/MinerU · **77,667★** · 최근 push 2026-08-14 (매우 활발)
- **버전**: v3.4.0 (2026-06-18) — PP-OCRv6 파이프라인 백엔드 도입, OCR 정확도 +11%(OmniDocBench v1.6, 자체 측정), OCR 속도 약 2배
- **라이선스**: 2026-04에 AGPLv3 → **"MinerU Open Source License" (Apache 2.0 기반 커스텀)** 전환.
  - 추가 조건: ①월활성사용자 1억 명 또는 월매출 2천만 달러 초과 시 별도 상용 라이선스(본교 해당 없음) ②MinerU 사용 사실을 서비스 문서에 고지(교내 시스템 문서에 1줄 표기로 충족).
  - → **교내 전용 사용에 실질 제약 없음.** 단, 표준 SPDX 라이선스가 아니므로 전문을 저장소에 보관하고 고지 의무를 이행할 것.
- **아키텍처(이중 백엔드)**:
  - **파이프라인 백엔드**: 레이아웃 검출 + 수식 + 표 인식 + OCR(PP-OCRv5/v6의 PyTorch 포트)의 단계식. `lang_list="korean"` 파라미터로 한국어 명시 지원(37개 언어).
  - **VLM 백엔드**: MinerU2.5 (1.2B VLM) — OmniDocBench 90.67(자체 측정). vLLM/sglang 서빙 → **Spark 20대 병렬 배치와 정합**. 단 VLM 백엔드의 한국어 성능은 **미확인**(학습 데이터가 중국어·영어 중심으로 추정).
- **표 구조**: 표→HTML 변환 지원. 병합 셀 복원 품질은 한국어 문서 기준 미확인.
- **위험**:
  - VLM 백엔드 한국어 성능 미확인 → 한국어는 파이프라인 백엔드가 주력이 될 수 있고, 이 경우 Spark에서 파이프라인 백엔드 GPU 가속 실측이 선행돼야 함.
  - 커스텀 라이선스(비표준)에 대한 기관 법무 검토 1회 필요.

### 2. Docling — 대안 1안

- **저장소**: https://github.com/docling-project/docling · **64,781★** · 최근 push 2026-08-14 (매우 활발) · **MIT**
- **운영 주체**: IBM Research 발, LF AI & Data 재단 프로젝트 — 지속성 신뢰도 높음.
- **강점**:
  - **라이선스가 가장 깨끗함**(MIT). 공공기관 관점에서 무위험.
  - **TableFormer** 표 구조 인식 — 병합 셀·테두리 없는 표 복원에 특화(복잡 표 93.6% 주장, 자체 측정). **결과보고서 실적 표 추출이라는 본 프로젝트 핵심 요건과 직결.**
  - OCR 엔진 플러그인 구조: EasyOCR·Tesseract·**RapidOCR**(PP-OCRv6 모델, 한국어 지원 명시, ONNX 런타임이라 paddle 불필요)·기타. born-digital PDF는 OCR 없이 텍스트 레이어 직독 — 빠르고 정확.
  - arm64 공식 지원 명시. DoclingDocument 포맷으로 계층 구조(제목·절·표·그림) 보존.
- **약점/위험**:
  - 한국어 스캔본 인식 품질이 OCR 엔진(RapidOCR/EasyOCR)에 종속 — VLM 계열 대비 저품질 스캔에서 열세 가능.
  - 2026-02 신형 통합 모델(레이아웃+표+OCR 동시 SOTA 주장) 발표 — 한국어 커버리지 미확인.

### 3. PaddleOCR-VL — 보조 후보 (VLM 단독 최강 후보이나 의존성 위험)

- **저장소**: https://github.com/PaddlePaddle/PaddleOCR · 87,671★ · push 2026-07-22 · **Apache 2.0**
- **모델**: PaddleOCR-VL 1.5/1.6 (0.9B) — OmniDocBench v1.6 96.33%(자체 측정) 전체 1위권, MinerU2.5·dots.ocr 대비 우위 주장. **109개 언어 지원에 한국어 명시** — CJK에 강한 Baidu 계열.
- **효율**: vLLM 백엔드에서 MinerU2.5 대비 처리량 +15.8%, dots.ocr 대비 메모리 -40% (자체 측정).
- **결정적 위험**: 문서 전체 파이프라인은 레이아웃 검출(PP-DocLayout 계열)이 **PaddlePaddle 런타임 의존**인데, paddlepaddle-gpu는 **aarch64 공식 휠이 없다.** CPU aarch64 휠로 레이아웃만 CPU 처리하는 우회는 가능하나 수만 장 배치에서 병목. DGX Spark용 수동 컴파일 사례(2026-04)는 존재하지만 운영 경로로 삼기엔 취약.
- **판단**: bake-off에서 인식 품질 비교 기준선으로 포함하되, 채택은 aarch64 경로(ONNX 포트 또는 수동 빌드 검증) 확보가 전제.

### 4. dots.ocr — 관찰 후보

- **저장소**: https://github.com/studio-dots-ai/dots.ocr (rednote-hilab에서 이관) · **9,068★** · **최근 push 2026-03-24 — 약 5개월 정체** · **MIT**
- 1.7B LLM 기반 단일 VLM으로 레이아웃+인식 통합. 100개 언어 자체 벤치마크에서 저자원 언어 우위 주장. 한국어 개별 수치 미공개.
- **위험**: 커밋 정체(2026-03 이후), 소규모 조직 이관 — 지속성 불투명. 탈락은 아니나 주력 채택 부적합. bake-off 비교군으로만.

### 5. DeepSeek-OCR / DeepSeek-OCR2 — 관찰 후보

- **저장소**: https://github.com/deepseek-ai/DeepSeek-OCR · 23,792★ · push 2026-01-27 · **MIT**
- 3B, vLLM 업스트림 공식 지원. "광학 컨텍스트 압축"이라는 연구 지향 — 문서 파싱 파이프라인 완성도(계층 구조·reading order 산출물 형식)는 MinerU·Docling 대비 미성숙. 한국어 근거 **미확인**.
- **판단**: 주력 부적합, 필요 시 bake-off 비교군.

---

## 탈락 후보와 탈락 이유

| 후보 | 탈락 이유 |
|---|---|
| **marker** (datalab-to, 38.8k★) | 코드가 v2.0(2026-07)부터 Apache 2.0이 됐지만 **모델 가중치가 수정 RAIL-M — 매출/펀딩 $5M 미만 조직에만 무료.** 대학(연 예산 수백억 원)이 면제 대상에 든다는 보장이 없고, 공공기관 사업 시스템에 해석 분쟁 여지가 있는 라이선스를 넣지 않는다(브리프 4장 필수 제약). 성능은 상위권이므로 라이선스가 해소되면 재검토 가치 있음 |
| **Surya** (datalab-to, 21.3k★) | marker와 동일 주체·동일 구조: 가중치 CC-BY-NC-SA 4.0 + $5M 면제 조항. 동일 사유로 탈락 |
| **olmOCR** (allenai, 19.3k★) | Apache 2.0으로 라이선스는 깨끗하나 **학습 데이터 구축 시 비영어 텍스트를 필터링** — 한국어 스캔본이라는 본 프로젝트 핵심 요건에 구조적으로 부적합. 영어 문서 대량 처리 용도로는 우수 |
| **unstructured** (15.3k★) | Apache 2.0이지만 자체 인식 모델이 없는 조합 라이브러리. 오픈소스판의 표 구조 추출·한국어 스캔 처리에 차별 강점 없음(고성능 기능은 유료 API 중심). MinerU/Docling이 상위 호환 |
| **PyMuPDF4LLM** | 래퍼는 Apache 2.0이나 핵심 의존성 PyMuPDF가 **AGPL-3.0** — 브리프 4장이 명시한 위험 라이선스. born-digital 텍스트 직독이 필요하면 pypdfium2(Apache/BSD) 또는 Docling 내장 파서로 대체 가능 |
| **GOT-OCR2.0, GLM-OCR, HunyuanOCR 등** | 2026년 신규 VLM OCR 다수 존재하나 한국어 근거·생태계 성숙도 미확인. 필요 시 bake-off 시점에 재스캔 |

---

## 배치 처리 관점 (Spark 최대 20대)

- MinerU VLM·PaddleOCR-VL·dots.ocr 모두 **vLLM 서빙 → 문서 단위 완전 병렬화**와 정합(브리프 4장 3항: 클러스터링 없이 워크로드 20등분).
- Docling·MinerU 파이프라인 백엔드는 프로세스 단위 병렬 — 마찬가지로 문서 단위 분할 배치에 적합.
- 프리필·배치 위주 작업이므로 Spark의 연산 강점 구간. 대역폭 병목은 파싱 단계에서는 상대적으로 덜 민감(생성 토큰 수가 페이지당 제한적).

## 종합 위험 목록

1. **한국어 공공 행정문서 기준 공개 벤치마크가 전무** — 모든 후보의 한국어 수치는 미확인. 자체 bake-off 없이 확정 금지.
2. **PaddlePaddle GPU aarch64 공식 미지원** — PaddleOCR 계열 풀파이프라인의 구조적 위험.
3. **MinerU VLM 백엔드 한국어 성능 미확인** — 한국어는 파이프라인 백엔드 의존 가능성, Spark GPU 가속 실측 필요.
4. **스캔본 비율·품질 미상** — 사용자 확인 필요(99-open-questions).
5. **HWP/HWPX 문서 존재 가능성** — 한국 공공기관 공고문·계획서 원본이 HWP일 수 있음. 이 경우 PDF 파싱과 별도의 경로(hwpx 직접 파싱이 PDF 변환보다 구조 보존에 유리)가 필요. 사용자 확인 필요.

## 권장 결정 절차

1. Phase 1에서 MinerU(1안)·Docling(대안) 이중 트랙으로 설계 고정 (인터페이스를 파서 교체 가능하게).
2. Phase 2 초입: 실물 문서 표본(스캔 결과보고서 표 중심 30~50페이지)으로 bake-off → 주 파서 확정.
3. bake-off 항목: 표 셀 정확도(병합 셀 포함), 한국어 문자 정확도(CER), 계층 구조 복원, 페이지당 처리 시간(Spark 1대 기준).
