# 문서 작성 시험 묶음

빈 양식·근거·시험 요청·정답을 한 폴더에 넣어 두면 운영 플랫폼에서 문서 작성을 처음부터 끝까지 돌리고 정답과 견준다(11-10). 정답에서 빈 양식과 시험 요청을 만드는 일은 담당자가 하고, 이 도구는 돌리기와 견주기만 한다.

## 폴더

`data/eval_sets/<이름>/` (data/ 는 git 에 올라가지 않는다. 틀은 scripts/eval_set_template)

- `blank.hwpx` 빈 양식
- `evidence/` 근거 파일(공고·기본계획 등), 빈 양식과 함께 새 프로젝트에 올린다
- `prompts.txt` 한 줄에 한 요청, 차례대로 같은 대화에 보낸다. `#` 줄은 메모, 앞에 `[꼼꼼]` 이면 답변 깊이 꼼꼼하게
- 또는 `prompts/` 폴더: 파일 하나(.txt·.md, 이름 차례)가 요청 하나다. 표·여러 줄이 든 긴 요청에 쓴다(있으면 prompts.txt 보다 먼저)
- `answer.hwpx` 또는 `answer.pdf` 정답
- `meta.json` (선택) `{"project": "프로젝트 이름", "sector": "grant"}`

## 명령

```
python scripts/225_eval_set_run.py data/eval_sets/특성화2026 --init
AURA_EVAL_PW=… python scripts/225_eval_set_run.py data/eval_sets/특성화2026 --user <시험 계정> --compare --episodes-ssh <계정>@<운영 서버>
```

계정은 시연 계정 말고 시험 계정을 쓴다. 비밀번호는 환경 변수로만 준다(인자·기록·저장소에 남기지 않는다). 새 프로젝트를 만들어 쓰므로 기존 문서는 건드리지 않는다. 나눠 돌리기: `--steps 1-4`(그 차례만), `--project-id N --session M`(만든 프로젝트·대화에 이어서), `--snapshots`(요청마다 작업본 사본 turn_NN.hwpx). 비교만 다시 하려면 `python scripts/226_eval_set_compare.py data/eval_sets/특성화2026`.

## 결과 읽기

`runs/<시각>/` 에 result.hwpx(대화의 지금 작업본), run.json(요청마다 걸린 시간·시간 초과·답 머리), messages.json, episodes.jsonl(걸음 기록), report.md·score.json.

report.md 는 정답 절마다 결과 절을 제목 열쇠(번호·기호 뗀 글자)로 맞춰 이렇게 센다. 모두 기계 산출이고 모델 채점은 없다.

- 채움: 「확인 필요」를 뺀 본문이 30자 이상인 절
- 수치 맞음: 정답 절의 수치(금액·인원·비율·연도, 단위째) 가운데 결과 같은 절에 나온 것
- 근거 밖 수치: 결과에만 있고 정답·근거 어디에도 없는 수치(지어낸 수치 의심, 눈으로 확인)
- 확인 필요: 결과 절의 「확인 필요」 수
- 글 겹침: 정답 절 글의 3글자 묶음이 결과 절에 나온 비율
- 표 칸: 머리줄을 뺀 표 칸 가운데 글이 있고 「확인 필요」가 아닌 칸
