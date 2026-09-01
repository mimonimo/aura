# 0004. vLLM 서빙 이미지 확정 — nightly 다이제스트 고정 (TASK-02)

- 상태: 확정
- 날짜: 2026-09-01
- 관련: `PROJECT_BRIEF.md` §8.1-J, `research/01-J-llm-serving.md`, `configs/serving.yaml`, W1-W2 TASK-02

## 맥락

vLLM은 리서치 1안으로 확정된 상태였고, 남은 것은 "aarch64 + Blackwell(sm_121)에서
실제로 도는 이미지"를 태그 고정하는 일이었다. 리서치가 경고한 대로 릴리스
이미지에서 문제가 터졌다.

## 선택지와 실측 결과

| 안 | 결과 |
|---|---|
| A. `v0.11.0` 릴리스 (digest 014a95f2…) | 실패 — 동봉 triton ptxas가 sm_121a 미지원: `ptxas fatal: Value 'sm_121a' is not defined for option 'gpu-name'` |
| B. nightly (digest df6c5814…) | 성공 — torch.compile 정상, 서버 기동, 응답 확인 |

## 결정

`vllm/vllm-openai@sha256:df6c5814eadd5b5c9e388e7093dfd811f900539619fcf07c65bb637f607898ff`
로 고정한다. "nightly" 태그는 움직이므로 참조 기준은 다이제스트다.
차후 안정 릴리스가 sm_121a를 지원하면 같은 스모크 절차로 재검증 후 이관한다.

## 실측 기록 (spark-1397, 2026-09-01)

- 모델: Qwen/Qwen3-4B, `--max-model-len 8192 --gpu-memory-utilization 0.5`
- 처리량: TTFT 중앙값 47ms · 생성 23.0 tok/s (배치 1, 스트리밍, max_tokens=256,
  temperature=0, 단일 동시 요청, 웜업 1회 제외 3회 측정 — `scripts/21_serving_smoke.py`)
- 대역폭 제약(~273GB/s) 장비의 dense 4B 기대 범위 내. 브리프의 MoE 우선 지침 타당성 뒷받침.

## 운영 함정 2건 (재현 절차에 포함할 것)

1. 통합 메모리 페이지 캐시: 대용량 다운로드 후 캐시가 차면 vLLM이 보는 여유
   메모리가 급감해 기동 실패 (관측: free 6.56GiB). 기동 전 `sudo sysctl vm.drop_caches=3`
   + `--gpu-memory-utilization` 명시(기본 0.9는 공유 장비에서 불가)가 규칙.
2. 장비 공유: 이 장비에 타 사용자 Ollama 서버가 상주(~5GB). 메모리 계획에 반영.

## 추가 기록 — 주 서빙 모델 상향 (2026-09-01 밤, 실측)

sLLM 목표(로컬 최대 성능)에 따라 주 서빙을 Qwen3-4B → Qwen3-30B-A3B-Instruct-2507
(MoE, Apache 2.0)로 교체했다. 같은 조건(배치 1, 256토큰, 웜업 제외 3회)에서:

| 모델 | TTFT | 생성 속도 |
|---|---|---|
| Qwen3-4B (dense) | 47ms | 23.0 tok/s |
| Qwen3-30B-A3B (MoE, 활성 3B) | 73ms | 30.7 tok/s |

| Qwen3.5-35B-A3B (MoE, 활성 3B) | 87ms | 30.8 tok/s |

MoE가 dense 4B보다 빠르면서 품질은 상위 체급 — 대역폭 제약 장비에서 MoE를
우선하라는 브리프 4장의 판단이 실측으로 확인됐다. 최종 주 서빙은 리서치 1안
그대로 Qwen3.5-35B-A3B (2026-09-02 교체. 전날 "저장소 없음" 판단은 구 CLI
오류 로그를 오독한 것으로 정정한다). FP8 변형(Qwen3.5-35B-A3B-FP8)은 시도했으나 이 이미지에서 cutlass_scaled_mm 'Error Internal'(sm_121 FP8 커널 미성숙)로 기동 실패 — bf16 유지, vLLM 업데이트 시 재시도.
기동 파라미터는 serving.yaml.

## 결과와 되돌리기 비용

이 다이제스트가 모델 카드·평가 재현 절차의 서빙 기준이 된다. 이미지 교체 시
스모크(기동 + 응답 + 처리량) 재실행이 필수 — 절차는 이 문서와 serving.yaml에 있다.
