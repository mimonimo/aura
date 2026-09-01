# 0001. 학습 스택 선정과 venv 분리 (TASK-03)

- **상태**: 확정
- **날짜**: 2026-09-01
- **관련**: `PROJECT_BRIEF.md` §8.1-F, `research/01-F-llm-finetuning.md`, `research/00-summary.md` F항, W1-W2 TASK-03

## 맥락

aarch64 + Blackwell(sm_121)은 리서치가 위험 2번으로 지목한 조합이다. 파인튜닝
프레임워크가 이 환경에서 실제로 도는지 W1~W2에 확인해야 P5 모델 트랙 계획이 선다.
또한 설치 과정에서 파서 스택과 학습 스택의 의존성 충돌이 발생해 환경 구성
방침도 함께 정해야 했다.

## 선택지

| 안 | 장점 | 단점 |
|---|---|---|
| A. LLaMA-Factory (리서치 1안) | NVIDIA 플레이북 등재, YAML 재현성(모델 카드 재료), pip 설치로 즉시 동작 확인됨 | Unsloth 대비 반복 실험 속도 낮을 수 있음 |
| B. Unsloth (리서치 대안) | Spark 공식 지원 표방, 반복 실험 속도 | 이번 스모크에서 미검증 (필요 시 후속 확인) |
| 환경-A. 단일 venv | 관리 단순 | transformers 메이저 충돌 (파서 스택 4.57 ↔ 학습 스택 5.6), pandas·gradio 하향 충돌 실측됨 |
| 환경-B. venv 분리 (.venv / .venv-train) | 검증 완료된 파서 환경 보존, 스택별 버전 고정 명확 | 디스크 중복(각 venv에 torch 포함), 실행 시 venv 선택 필요 |

## 결정

- 파인튜닝 프레임워크: **LLaMA-Factory 0.9.5** (리서치 1안 그대로). Unsloth는
  반복 실험용 후보로 유지하되 필요 시점에 별도 스모크 후 채택.
- 임베딩 학습: sentence-transformers 6.0.1로 1-step 검증 완료. FlagEmbedding
  (리서치 1안, hn_mine)은 P2 하드 네거티브 마이닝 시점에 확인.
- 환경: **venv 분리** — `.venv`(파싱·서빙), `.venv-train`(학습). 버전 고정은
  `configs/training-env.txt` (pip freeze 전문).

## 근거

spark-1397 실측 (2026-09-01):

- LoRA 1-step 완주: Qwen/Qwen3-0.6B, `device: cuda:0`, bfloat16,
  train_loss 3.2446, train_runtime 3.23s. 설정: `configs/training/lora-smoke.yaml`
- 임베딩 대조학습 1-step 완주: paraphrase-multilingual-MiniLM-L12-v2 + MNR loss,
  loss 0.1588. 스크립트: `scripts/20_embed_smoke.py`
- **aarch64 소스 빌드가 필요했던 패키지: 없음.** torch 2.13.0, llamafactory 0.9.5,
  sentence-transformers 6.0.1, docling 2.124.0, mineru 3.4.5 전부 사전 빌드 휠로 설치됨
- 단일 venv 충돌 실측: llamafactory 설치가 transformers를 4.57.6→5.6.0으로 올리고
  pandas 3.0.5→2.3.3, gradio 6.8→5.50으로 내림. presidio-analyzer(pydantic 하한),
  gradio-pdf(gradio 6 요구)와 충돌 경고 발생 → 분리로 해소

## 35B MoE QLoRA 가능성 — **추정 (실측 아님)**

Qwen3.5-35B-A3B 기준: 4-bit 가중치 약 20GB + LoRA 어댑터·옵티마이저 수 GB
+ gradient checkpointing 전제 활성값 수 GB ≈ **30~40GB 수준으로 추정**.
가용 통합메모리 116GiB 대비 여유가 커 QLoRA는 가능성이 높다고 판단한다.
실측은 P5 진입 시 수행하며, 그 전까지 이 수치를 계획 근거로만 쓰고
확정값으로 인용하지 않는다.

## 결과와 되돌리기 비용

- LLaMA-Factory YAML이 모델 카드·논문 방법론의 재현 절차 재료가 된다.
  프레임워크 교체 시 학습 설정 재작성 + 재검증 필요 (스모크 수준이면 반나절).
- venv 분리를 되돌리면(단일화) transformers 메이저 충돌을 다시 풀어야 한다.
  유지 비용은 디스크 중복뿐이므로 되돌릴 이유가 생기기 어렵다.
