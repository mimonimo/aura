# zzaimy 패키지

플랫폼 구현 코드다. `app`은 화면·API·계정·검색·문서 작업 에이전트, `ingest`는 문서 추출·분류와 한글·오피스 변환,
`graph`는 지식 그래프와 온톨로지, `document_context`는 문서 안팎의 명시적 참조, `verify`는 수치 대조·배점 반영·예산 검산,
`generate`는 모델 연결 설정, `eval`은 검색 평가, `dataset`은 학습 후보 생성·검수, `export`는 학습 산출물 반출 묶음을 맡는다.
`index`·`retrieve`·`extract`는 자리만 잡힌 패키지이며, 지금 색인과 검색 구현은 `app`에 있다.
