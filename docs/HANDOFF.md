# 인수인계 — 운영 구성과 남은 일

대화 맥락 없이 작업을 이어받게 하는 다리다. 순서대로 `CLAUDE.md`(작업 지침) → 이 문서(구성·접속·사람 손이 필요한 일) →
`PROJECT_BRIEF.md`(판단 기준). 지금 하는 일과 진행 중인 일은 `docs/dev-now.md` 가 정본이고, 구조는 `docs/architecture.md`,
결정의 근거는 `docs/decisions/`(ADR) 에 있다.

문서 점검: 2026-10-27. 옛 날짜별 기록(9~10월 초 후보 생성·반입 실험·옛 현황표)은 지웠다. 필요하면 깃 이력과
`docs/collaboration/` 의 작업 기록에서 찾는다.

---

## 1. 운영 구성

장비 목록과 장비 사이 연결의 정본은 `src/zzaimy/app/process_flow.py` 의 `HOSTS`·`LINKS` 이고, 개발 현황과 처리 흐름(`/dev/flow`)의
시스템 구성도가 이것으로 그려진다. 장비나 역할이 바뀌면 그 표 한 곳을 고친다.

| 장비 | 맡는 일 | 사양·서비스 |
|---|---|---|
| 운영 VM | 플랫폼 화면·API, DB, 검색 색인, 지식그래프·온톨로지, 웹 한글 기안기 서버, 공동 편집 서버 | ESXi VM, CPU 8 · 메모리 64GB, GPU 없음, PostgreSQL(pgvector) |
| 토르 02 | Writer 27B — 대화, 27B 문서 작업 에이전트 | 젯슨 AGX Thor, 오픈 모델 Qwen3.8-27B NVFP4(vLLM, 포트 8001) |
| 토르 03 | 검토·판독 27B, 질의 임베딩 Embed v2, 리랭커 Rerank v1 | 같은 27B NVFP4, 임베딩 8016 · 리랭커 8015 |
| DGX | 원본 보관, 일괄 문서 읽기(MinerU OCR), LoRA 학습 | GB10 · 통합 메모리 121GB · 디스크 3.7TB, 우리 전용 aura 계정 |
| 원본 보관 PC | 학교 실문서 보관(사업별 폴더) | DGX 로 rclone 복사 |

- Writer 는 오픈 모델 그대로다. Writer SFT v1 병합본은 같은 시험으로 비교한 결과 문서 작업 에이전트가 동작하지 않아 채택하지 않았고,
  토르 02 는 10-24 오픈 모델로 되돌렸다(ADR-0069 확정). 비교 결과는 `/dev/models/compare`, 비교 도구는 `scripts/210`~`213`·`202`.
- 검색 학습본 Embed v2·Rerank v1 은 운영 채택 상태다(ADR-0020·0021). 색인과 질의 임베딩 모델은 한 짝이라 한쪽만 바꾸지 않는다.
- 옛 DGX Spark 는 쓰지 않는다. 계획에 없는 모델은 올리지 않는다(절대 규칙 9).
- LLM 연결은 `/dev/tools` 에서 등록하고 `data/platform/llm_connections.json` 에 저장된다. 역할에 정한 서버가 연결을 받지 않으면
  같은 모델을 내는 다른 교내 연결로 넘긴다(외부 연결로는 넘기지 않음).
- vLLM 은 생각 모드가 기본으로 켜져 있어, 클라이언트가 기본으로 끈다. 끄지 않은 호출은 한도를 생각에 다 써서 조용히 실패한다.

### 데이터가 도는 길

원본 보관 PC → DGX(원본 보관·읽기) → 운영 VM(원본 장부·등록·색인·그래프)이다. VM 의 cron 이 `scripts/170_vm_sync.py` 를
`scripts/zz_run.sh`(메모리 상한) 로 돌리고, 원본이 바뀌면 1분 안에 따라잡는다. DGX 쪽은 VM 전용 읽기 전용 키로만 열려 있다.
단계별 문서 수와 주기는 처리 흐름 화면(`/dev/flow`)이 정본이다. VM 메모리를 많이 쓰는 일(그래프 전체 재구축·색인 전체)은 한 번에 하나만 돌린다.

### 문서 작성

- 주 경로는 웹 한글 기안기다. 한글 문서함(`/hwp`)에서 원본 hwpx 를 브라우저로 바로 고치고, 같은 문서를 여러 사람과 AURA 가
  칸(누름틀) 단위로 함께 고친다(ADR-0070). 칸 글은 운영 DB 표에 판 번호와 함께 남고 몇 초 뒤 hwpx 에 서식째 써 넣는다.
  자세한 층과 흐름은 architecture 10.2, 시연은 `/dev/coedit-demo`(ADR-0071).
- 27B 문서 작업 에이전트가 양식 고르기(없으면 실물을 참고해 설계) → 근거 찾기 → 관문(근거 없는 숫자·남은 예시 글자 거절) → 절 쓰기를
  거쳐 hwpx 의 절 자리에 바로 쓴다. 무엇을 쓸지는 27B 가 정하고 코드는 도구·관문·기록만 맡는다(architecture 9·10.3, ADR-0060·0065·0068).
- 구글 독스는 보조 경로다. 학교 계정을 이은 사용자만 쓰고, 구글 앱 등록은 `/dev/users#google-links`(ADR-0029).
- 한글 완성본은 원본 hwpx 에 서식 보존으로 채운다(ADR-0035). .hwp 원본은 hwpx 로 바꿔 쓴다.
- 캡스톤 논문도 기안기 문서(한글 문서함 공통/논문)로 쓴다(ADR-0072). 원고는 `docs/paper/논문/`.

### 접속과 운영 메모

주소·계정·비밀번호는 이 문서에 적지 않는다. 학과망 VPN 안에서만 닿고, 키 등록과 비밀번호는 담당자에게 따로 받는다.
서버가 한꺼번에 안 닿으면 대개 VPN 미연결이므로 기다렸다 다시 시도한다.

- 운영 VM: 서비스는 `systemctl --user status zzaimy.service`, 로그 `~/app.log`. 환경은 `~/zzaimy-capstone/.env.local`.
  VM 에서 배치·측정을 돌릴 때는 `.env.local` 을 읽고 띄운다(아니면 CPU 폴백으로 수치가 틀린다). pip 네트워크가 없어 패키지는 오프라인 휠로 넣는다.
  한글 읽기 kordoc 은 `~/opt/kordoc`, 판을 올리면 `scripts/147_kordoc_parity.py` 로 대조한다(ADR-0033).
- 토르: Writer 서빙은 `scripts/126_serve_writer.sh`, 학습본 올리기는 `scripts/116_ship_and_serve.sh`, 점검은 `scripts/110_serving_check.sh`.
  큰 파일을 받은 뒤에는 페이지 캐시를 비우고(root) 서빙을 올린다. 컨테이너는 `docker stop` 으로 내린다(`rm -f` 는 GPU 메모리가 안 돌아온다).
- DGX: 학습 환경은 `~/zzaimy-capstone/.venv-train`(고정 판 `configs/training-env.txt`), 읽기 환경은 `.venv-parse`. 공용 장비라 MinerU 동시 실행과
  가용 메모리 하한을 지킨다(`scripts/167`). 학습본 이관은 병합 → NVFP4 → `116` 으로 두 토르.
- 서버 이전: `scripts/209_migration_manifest.py`, 절차는 `docs/ops/migration.md`.

## 2. 정본과 배포

정본은 GitHub mimonimo/aura 하나다.

1. 맥에서 고친다 → 시험 → `git commit` → `git push origin main`. 처음 받으면 `git config core.hooksPath .githooks`.
2. `bash scripts/99_deploy.sh --restart` — VM 이 origin 의 그 커밋으로 맞추고 재시작한다. `--restart` 가 없으면 서비스는 옛 코드다.
   맥이나 VM 에 커밋하지 않은 변경이 있으면 멈춘다.
3. `data/`·`.env.local` 은 깃에 없다. VM 것이 그대로 남는다.

실행 중인 스크립트를 `scp` 로 덮어쓰지 않는다. 시험은 임시 DB 로 하고 운영 설정은 읽기만 한다.

## 3. 저장소·데이터

- 실문서·파싱 결과·비밀값은 `data/` 와 `.env.local` 에만 두고 커밋하지 않는다.
- 저장 구조(ADR-0030·0031): DB 가 원본이다. 문서는 `data/platform/documents/`(반입·첨부·생성·보고·기안기), 파생 지식은
  `knowledge/`(index·eval·exports), 모델은 `data/train/`(datasets·baselines·models·runs). 경로는 `src/zzaimy/app/paths.py` 한 곳, 배치 표는 `docs/data-layout.md`.
- 새 반입 지점은 `storage.adopt_original` 을 거친다.

## 4. 개발 화면

주소와 이름의 정본은 `src/zzaimy/app/dev_nav.py` 다.

| 묶음 | 화면 |
|---|---|
| 개요 | 개발 현황 `/dev`, 처리 흐름 `/dev/flow`, 논문·설계 문서 `/dev/docs`, 변경 이력 `/dev/history` |
| 문서 반입·읽기 | 반입 점검 `/dev/flow#intake`, 데이터 현황 `/dev/db` |
| 검색·지식 | RAG 공간 `/dev/rag`, 그래프 탐색 `/graph/explore`, 품질·성능 `/dev/quality` |
| 문서 작성 | 공동 편집 시연 `/dev/coedit-demo` |
| 모델 학습 | 데이터·학습 `/dev/train`, 수집 자료 `/dev/collect`, 도구·모델 연결 `/dev/tools`, 모델 비교 결과 `/dev/models/compare` |
| 보안·관리 | 구글 계정 연결 `/dev/users`(구글 앱 등록·학교 계정 연결 현황), 개인정보·반출 점검 `/dev/pii` |

계정·부서·구성원 관리는 개발 화면이 아니라 업무 화면 사이드바의 조직 관리 `/team` 한 곳이다(ADR-0073). `/dev/intake` 는 처리 흐름의 반입 점검 칸으로 넘긴다.
옛 주소 `/dev/data`·`/dev/nas`·`/dev/api/intake` 와 구독 인증 상태 확인은 지웠다(10-25).

## 5. 사람 손이 필요한 남은 일

진행 중인 개발 일은 `docs/dev-now.md` 「진행 중인 일」·「다음에 할 일」 에 있다. 여기에는 코드로 끝낼 수 없는 것만 둔다.

- 학교 확인: 개인정보 부서 검토와 문서 열람 등급 체계, 보관·삭제 범위, 주민번호 화면 표시의 가리기 기준.
- 전산실: 내부 DNS 에 플랫폼 이름 등록. 지금은 관리자 PC 의 hosts 로만 풀린다.
- 사용자 제공: 실사용 측정에 쓸 실제 교직원 질문 20~30개.
- 관리자 작업: 구글 클라우드 동의 화면을 내부용으로 바꾸기, 서버의 sudo 작업(토르 캐시 비우기·재부팅 등).
- 한글 프로그램 확인: 한글 완성본을 한글 프로그램에서 직접 열어 보기.

## 6. 절대 지킬 규칙

전체는 `CLAUDE.md` 절대 규칙(브리프 9장). 운영에서 특히 사고가 났던 것:

- 수치는 인출하고 생성하지 않는다. 베이스라인 측정 전에 파인튜닝을 시작하지 않는다.
- 가용 자원을 꽉 채우지 않는다. 무거운 작업은 코어 절반·낮은 우선순위, VM 에서는 한 번에 하나(9/8 VM 먹통, 10/4 메모리 부족 사고).
- 배포·재시작·시험을 한 줄에 묶지 않는다. 상태를 확인하고 다음으로 간다.
- 내부 정보를 밖으로 내지 않는다. 외부 전송 결정이 남은 것은 `docs/notes/2026-10-15-egress-audit.md`.
- 직접 만들지 않은 서버 폴더는 비어 있어도 묻고 지운다.

## 7. 이어받는 절차

1. GitHub mimonimo/aura 협업자 권한을 받아 `git clone`, `git config core.hooksPath .githooks`.
2. 서버를 만질 경우 학과망 VPN 접근과 VM SSH 키 등록을 받는다.
3. 새 세션에서 `CLAUDE.md` → 이 문서 → `PROJECT_BRIEF.md` → `docs/dev-now.md` 를 읽는다.
4. 여러 작업자가 나눠 맡을 때는 `docs/collaboration/README.md` 의 분담·요청 절차를 따른다.
