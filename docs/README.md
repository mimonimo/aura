# docs/

무엇이 어디 있는지만 적는다. 내용은 각 문서에 있다.

## 먼저 볼 것

| 문서 | 용도 |
|---|---|
| [HANDOFF.md](HANDOFF.md) | 인프라 현황·남은 일·이어받는 절차 |
| [dev-now.md](dev-now.md) | 지금 하는 일·다음 일 (화면에도 나온다) |
| [model-plan.md](model-plan.md) | 모델 4종 계획·학습 순서 |
| [llm-rerank-eval.md](llm-rerank-eval.md) | 검색·리랭커 측정 기록 |
| [quality-system.md](quality-system.md) | 품질 5계층 체계 |

## 폴더

| 폴더 | 무엇 |
|---|---|
| `decisions/` | ADR — 결정과 근거. 새 결정은 여기에 번호로 남긴다 |
| `model-cards/` | 학습한 모델의 카드(자료·방법·측정·한계) |
| `collaboration/` | Codex·Claude 공동 작업 기록과 규약 |
| `notes/` | 조사 메모·감사 결과·용어 정리 |
| `paper/`, `proposal/`, `weekly/` | 논문·제안서·주간 보고 원고 |
| `context/` | 누적된 작업 규율·선호 |
| `instructions/` | 지난 지시서(이력) |
| `archive/` | 기계가 뽑았던 지난 측정 보고서 |

## 규칙

- 측정 수치는 기계 산출물이 정본이다. 평가 스크립트는 `data/platform/eval/` 에 쓰고,
  저장소 사본은 `--report` 를 붙일 때만 갱신한다.
- 결정은 ADR로 남긴다. 문서 본문에서 결론만 바꾸고 근거를 남기지 않는 방식은 쓰지 않는다.
- 운영 접속 절차·자격 증명은 저장소에 두지 않는다.
