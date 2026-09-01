# K. 생성 모델 (오픈웨이트, 한국어) — ZZAIMY-Writer·ZZAIMY-Extract의 베이스

> 조사일: 2026-08-15
> 평가 축: 한국어 공문서 문체 잠재력, 긴 컨텍스트, **MoE 여부**(Spark 대역폭 제약), 파인튜닝 생태계, **라이선스·명명 자유도**.

---

## 결론 요약

| 용도 | 선정 | 핵심 근거 |
|---|---|---|
| **Writer 베이스 1안** | **Qwen3.5-35B-A3B** (또는 Qwen3.6-35B-A3B) | MoE 총 35B/활성 3B — Spark 대역폭 제약에 최적. Apache 2.0 → `ZZAIMY-Writer` 명명 자유. 파인튜닝 생태계(LLaMA-Factory·SWIFT 등) 최성숙. 한국어 유창성 상위권(단, 공문서 문체는 어차피 LoRA로 학습) |
| **Writer 대안 1안** | **Qwen3.5-122B-A10B** | 동일 라이선스 계열의 품질 상향판. 4bit 양자화 시 약 61GB+ — 128GB 통합 메모리에 구동 가능. 초안 품질이 35B로 부족할 때 승격 |
| **한국어 특화 비교군** | Kanana-2-30B-A3B (카카오) | 한국어 벤치마크 우수(KMMLU 67.3, HAE-RAE 75.6) + MoE-A3B로 하드웨어 정합 완벽. **그러나 파생 모델명에 "Kanana" 접두사 의무 → ZZAIMY 명명 불가.** 성능 격차가 크면 명명 포기 여부는 사용자 결정 사항(99번 문서) |
| **Extract 베이스 1안** | **Qwen3-4B** 계열 (3~7B급) | Apache 2.0, 소형 dense — 배치 추출 처리량 극대화. 대안: Mi:dm 2.0 Mini 2.3B/Base 11.5B(MIT, 한국 특화), A.X-4.0-Light 7B(Apache 2.0, 한국어 특화) |

**베이스 확정은 Phase 3 베이스라인 측정에서** 실제 공고·계획서 샘플로 후보 2~3개를 직접 비교한 후 한다(브리프 10장 순서 준수). 여기서는 후보군과 탈락군만 확정한다.

---

## Spark 대역폭 제약과 MoE — 정량 감각

LPDDR5X ≈ 273GB/s 기준 대략적 생성 속도(디코드, 4bit 양자화 가정):

| 모델 | 활성 파라미터 | 토큰당 읽기(≈4bit) | 이론 상한 속도 |
|---|---|---|---|
| Qwen3.5/3.6-35B-A3B | 3B | ≈1.5GB | ~180 tok/s급 |
| Kanana-2-30B-A3B | 3B | ≈1.5GB | ~180 tok/s급 |
| Qwen3.5-122B-A10B | 10B | ≈5GB | ~55 tok/s급 |
| Solar Open 102B-A12B | 12B | ≈6GB | ~45 tok/s급 |
| A.X-4.0 (72B dense) | 72B | ≈36GB | **~7 tok/s급 — 부적합** |
| gpt-oss-120b (A5.1B) | 5.1B | ≈2.6GB(MXFP4) | ~100 tok/s급 |

*실측치가 아니라 대역폭 나누기 읽기량의 이론 상한(캐시·오버헤드 미반영). 브리프 4장 1항의 "MoE 우선" 원칙을 수치로 확인한 것. dense 대형(70B급)은 계획서 섹션 생성(섹션당 수천 토큰)에 실용 불가 판정.*

---

## 후보별 상세

### Qwen3 계열 (Qwen3 → 3.5 → 3.6, Alibaba) — 추천 1안

- **저장소**: QwenLM/Qwen3.6 등 · **전 계열 Apache 2.0** (명명 자유 ✅)
- **2026 라인업**: Qwen3.5(2026-02): 397B-A17B / 122B-A10B / 35B-A3B / 27B(dense). Qwen3.6-35B-A3B(2026-04).
- **강점**:
  - A3B MoE가 Spark 제약과 정확히 정합. 122B-A10B 승격 경로도 동일 라이선스·동일 툴체인.
  - **파인튜닝 생태계 최성숙**: LLaMA-Factory, SWIFT, Unsloth, TRL 모두 Qwen 계열 1순위 지원. LoRA SFT 사례 최다.
  - vLLM/SGLang 서빙 1순위 지원 — DGX Spark vLLM 공식 지원 확인(J 영역).
  - 다국어(한국어 포함) 유창성 상위권. 32k+ 장문 컨텍스트.
- **약점/위험**: 한국어 공문서 문체·행정 용어의 자연스러움은 한국 특화 모델 대비 열세 가능 — **미확인**, Phase 3에서 실측. 중국 기업 모델이라는 점에 대한 기관 내 수용성은 확인 필요(온프레미스라 데이터 유출 우려는 없음).

### Kanana-2-30B-A3B (카카오, 2026-01) — 한국어 특화 비교군 (명명 제약)

- **HF**: kakaocorp/kanana-2-30b-a3b · 30B 총/3B 활성(전문가 128, MLA), 네이티브 32k(YaRN 128k)
- **한국어 성적**(자체 보고): KMMLU 67.32, HAE-RAE 75.57, MATH-Ko 86.26 — 동급 최상위권. 카카오 사용자 데이터 미사용 명시.
- **라이선스: Kanana License — 결정적 제약 두 가지**:
  1. 파생 모델명에 **"Kanana" 접두사 포함 의무** → `ZZAIMY-Writer` 명명 불가(예: `Kanana-ZZAIMY-Writer`로만 가능)
  2. "Powered by Kanana" 고지 의무. (월 1천만 사용자·원격 서비스 조항은 교내 사용에 무관)
- **판단**: 기술적으로는 본 프로젝트에 가장 잘 맞는 모델 중 하나. 명명 요건 때문에 1안이 될 수 없으나, Phase 3 비교군에 포함해 **성능 격차를 수치로 확인한 뒤** 격차가 크면 "명명 vs 성능" 트레이드오프를 사용자에게 상정.

### Mi:dm 2.0 (KT, K-intelligence) — Extract 유력 / Writer 보조

- **HF**: K-intelligence/Midm-2.0-Base-Instruct(11.5B dense), Midm-2.0-Mini-Instruct(2.3B dense) · **MIT** (명명 자유 ✅)
- "Korea-centric": 한국 사회·행정 맥락 이해를 명시적 목표로 학습. 공공 영역 활용을 표방.
- dense이지만 11.5B/2.3B 소형이라 대역폭 부담 낮음(11.5B 4bit ≈ 6GB → ~45 tok/s급).
- **판단**: Extract(7.4) 베이스 유력 후보. Writer 후보로도 Phase 3 비교군 포함 가치(한국어 문체 vs Qwen 유창성 비교). 32k 컨텍스트는 확인 필요.

### A.X 시리즈 (SKT) — Light만 Extract 후보

- **A.X-4.0**(72B dense, 한국어 특화)·**A.X-4.0-Light**(7B)·**A.X-K1**(519B-A33B, DeepSeek-V3 아키텍처, 소버린 모델) — 모두 **Apache 2.0** (명명 자유 ✅)
- 탈락/한정: A.X-4.0(72B dense)는 대역폭상 부적합. A.X-K1(519B)은 4bit로도 ≈260GB — 128GB 초과, 구동 불가. **A.X-4.0-Light(7B)만 Extract 후보로 유지.**

### gpt-oss (OpenAI) — 탈락 (Writer 기준)

- gpt-oss-120b(A5.1B)/20b(A3.6B), Apache 2.0, MXFP4 네이티브 — 하드웨어 정합성은 우수.
- **탈락 사유: 한국어 약세가 문서로 확인됨.** 독립 평가에서 한국어 벤치마크(KMMLU, HAE-RAE, KBL 등) 전반에서 한국어 최적화 모델들에 일관되게 뒤지고, 중국어 C-Eval 20~28%라는 극단적 비영어 약세 보고. OpenAI 쿡북에 "한국어 성능 개선을 위한 파인튜닝" 문서가 존재한다는 것 자체가 베이스 한국어의 한계 방증. 영어권 보조 작업용으로만 참고.

### EXAONE 4.0 / K-EXAONE (LG AI Research) — 탈락 (라이선스)

- 한국어 성능은 국내 최상위권으로 평가되나, **EXAONE 라이선스는 연구 목적 한정 — 상업적 사용 시 LG와 별도 계약 필요.** 대학의 행정 업무 시스템(계획서 작성)이 "연구/교육 목적"에 해당하는지 해석이 모호하고, 위반 시 라이선스 즉시 종료 조항 존재. 공공기관 사업 산출물의 기반으로 삼기에 법적 불확실성이 큼(브리프 4장 위험 보고 의무). 파생 모델 재배포·명명 자유도 없음.

### Solar Open / Solar Open 2 (Upstage) — 탈락 (명명)

- Solar Open(102B-A12B, 2026-01), Solar Open 2(250B-A15B, 2026-07) — 정부 소버린 AI 사업 주관, 한국어 중심 MoE로 기술적 매력 높음.
- **탈락 사유: "Upstage Solar License" — 파생 모델명에 'Solar' 접두사 의무 + "Built with Solar" 고지 의무** → ZZAIMY 명명 불가. Llama와 동일 유형의 제약. (250B는 어차피 128GB에서 4bit로도 한계선)

### Llama 계열 — 탈락 (명명, 브리프 명시)

- 파생 모델명 "Llama" 포함 + "Built with Llama" 고지 의무 — 브리프 4장이 이미 배제 근거 명시. 한국어도 상기 후보들 대비 우위 근거 없음.

---

## 명명 자유도 종합표

| 모델 | 라이선스 | ZZAIMY-* 명명 | 비고 |
|---|---|---|---|
| Qwen3/3.5/3.6 계열 | Apache 2.0 | ✅ | |
| Mi:dm 2.0 | MIT | ✅ | |
| A.X 시리즈 | Apache 2.0 | ✅ | |
| gpt-oss | Apache 2.0 | ✅ | 한국어 탈락 |
| Kanana-2 | Kanana License | ❌ "Kanana" 접두사 의무 | 성능 비교군 유지 |
| Solar Open | Upstage Solar License | ❌ "Solar" 접두사 의무 | |
| EXAONE | EXAONE License | ❌ (연구 한정) | 상업 별도 계약 |
| Llama | Llama License | ❌ "Llama" 포함 의무 | |

## 위험 목록

1. **Qwen 계열의 한국어 공문서 문체 적합성 미실측** — Writer LoRA가 문체를 학습하는 구조라 베이스 격차가 좁혀질 것으로 예상되나, 검증 전 단정 금지. Phase 3 베이스라인에서 Qwen3.5-35B-A3B vs Kanana-2-30B-A3B vs Mi:dm-2.0-Base 3파전 비교 권장.
2. **MoE LoRA 파인튜닝의 프레임워크 성숙도** — dense 대비 MoE의 LoRA SFT 지원이 프레임워크별로 편차. E/F 영역에서 aarch64 동작과 함께 검증 필요.
3. Qwen3.5/3.6의 세부(컨텍스트 길이, base 모델 공개 여부 등)는 Phase 1에서 모델 카드 직접 확인 필요 — 본 문서 수치는 2026-08 검색 기준.
