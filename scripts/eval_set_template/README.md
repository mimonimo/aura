# 문서 작성 시험 묶음

이 폴더를 `data/eval_sets/<이름>/` 으로 복사해 채운다(data/ 는 git 에 올라가지 않는다).

- `blank.hwpx` 빈 양식(정답 문서에서 값을 지운 것)
- `evidence/` 근거 파일(공고·기본계획 등). 프로젝트를 만들 때 빈 양식과 함께 올린다
- `prompts.txt` 시험 요청(한 줄에 하나, 차례대로 같은 대화)
- `answer.hwpx` 또는 `answer.pdf` 정답 문서
- `meta.json` (선택) 프로젝트 이름, 업무 영역

실행과 비교는 docs/eval-sets.md 를 본다.
