# F. LLM 파인튜닝 프레임워크 (ZZAIMY-Writer LoRA SFT · ZZAIMY-Extract distillation용)

> 조사일: 2026-08-15
> 1차 필터: **aarch64 + Blackwell(GB10, sm_121)에서의 실제 동작.** 예상대로 여기서 걸러지는 후보가 많음.

---

## 결론 요약

| 구분 | 선정 | 근거 |
|---|---|---|
| **추천 1안** | **LLaMA-Factory** | **NVIDIA 공식 dgx-spark-playbooks 등재**(5.1 "Choosing a Fine-tuning Framework"). SFT·LoRA·QLoRA(INT4)·WebUI, Qwen 계열(MoE 포함) 광범위 지원. CLI+YAML 재현성 — 모델 카드 기록 요구와 정합 |
| **대안 1안** | **Unsloth** | **DGX Spark 공식 지원**(Unsloth 공식 블로그 + NVIDIA build.nvidia.com/spark/unsloth 플레이북). 단일 GPU LoRA 특화 2배 속도·저메모리 — Spark 1대 구조와 정확히 일치. 128GB에서 gpt-oss-120b급까지 학습 보고 |
| **보조(직접 구현)** | TRL + PEFT (NVIDIA PyTorch 컨테이너) | 프레임워크 제약에 부딪히면 최후 경로. NVIDIA 컨테이너(nvcr.io/nvidia/pytorch)가 Blackwell 최적화 PyTorch·Triton·FlashAttention 포함 — 가장 확실한 aarch64 기반 |
| **관찰** | NeMo AutoModel | NVIDIA 공식, FP8·멀티노드까지 — Phase 5에서 Spark 다수 대 학습이 필요해지면 검토 |

---

## aarch64 + Blackwell 1차 필터 결과

| 후보 | 판정 | 근거 |
|---|---|---|
| LLaMA-Factory | ⭕ | NVIDIA 공식 플레이북 수록(venv 설치, BF16/FP16/INT4). 커뮤니티 GB10 LoRA 사례 다수 |
| Unsloth | ⭕ | Unsloth 공식 DGX Spark 가이드 존재. 단 sm_121 대응을 위해 xformers 등 일부 패키지에 `TORCH_CUDA_ARCH_LIST=12.1` 재빌드/핫픽스가 필요했다는 커뮤니티 보고(2025-말~2026-초) — 최신 버전에서 해소 여부 스모크 테스트로 확인 |
| TRL/PEFT | ⭕ | NVIDIA PyTorch 컨테이너 기반 순수 PyTorch — 공식 플레이북 수록 |
| NeMo AutoModel | ⭕ | NVIDIA 공식, ARM64 명시, FP8 지원 |
| **Axolotl** | 🔶 미확인 | flash-attn·deepspeed 등 컴파일 의존성이 많아 sm_121/aarch64 검증 사례 미발견. 이득(다양한 학습 기법) 대비 검증 비용 큼 → 후순위 |
| **SWIFT** | 🔶 미확인 | Qwen 생태계 밀착·기능 폭은 최대이나 aarch64 공식 지원 명시·사례 미발견 → 후순위 |

*공통 주의: 이 플랫폼에서는 pip 범용 PyTorch가 아니라 **NVIDIA 제공 컨테이너/빌드**를 기반으로 할 것(공식 플레이북 권고). QLoRA(4bit)용 bitsandbytes는 aarch64 동작 보고가 있으나 버전 민감 — 스모크 테스트 항목에 포함.*

---

## 프로젝트 요구와의 대응

| 요구 (브리프) | 대응 |
|---|---|
| 7.3 Writer: Qwen3.5-35B-A3B급 MoE LoRA SFT | LLaMA-Factory가 Qwen MoE 계열 LoRA 지원. 35B(총) 4bit QLoRA 시 128GB 내 여유. **MoE LoRA는 라우터 동결/타깃 모듈 선택이 dense보다 민감** — Phase 5에서 dense 소형(비교군)과 병행 검증 |
| 7.4 Extract: 3~7B distillation SFT | 어느 후보로도 무난. 소형 dense라 Unsloth 속도 이점 큼 |
| 12장: 학습 결정 기록(모델 카드 재료) | LLaMA-Factory는 YAML 설정 파일 = 재현 가능한 학습 기록. Unsloth는 노트북/스크립트 중심 — 기록 규율 필요 |
| 다중 Spark 학습(선택) | 단일 Spark로 LoRA는 충분하다고 판단(35B-A3B QLoRA 기준). 필요 시 NeMo AutoModel 멀티노드 경로 |

## 권장 결정 절차

1. Phase 2에서 Spark 1대에 LLaMA-Factory·Unsloth 스모크 테스트(각 1시간짜리 소형 LoRA): 설치·학습·저장·vLLM 어댑터 로드까지 왕복 확인.
2. 둘 다 통과 시: **실험 반복은 Unsloth(속도), 최종 산출 학습은 LLaMA-Factory(YAML 재현성)** 이원 운용도 가능 — Phase 5에서 결정.
3. 실패 시: NVIDIA PyTorch 컨테이너 + TRL/PEFT 직접 구현으로 후퇴(기능은 충분, 편의만 감소).

## 위험
1. sm_121 타깃 미포함 휠(xformers, flash-attn, bitsandbytes 등)로 인한 재빌드 수요 — 컨테이너 기반으로 최소화하되 잔존 가능.
2. MoE LoRA 품질·안정성은 dense 대비 사례 적음 — Writer 학습 시 dense 비교군(Mi:dm 11.5B 등) 유지 권장.
3. Unsloth 무료판은 단일 GPU 중심(Spark 1대 학습에는 무관, 확장 시 제약).
