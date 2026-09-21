# 인수인계 — 다른 세션/계정이 이어받을 때 먼저 읽는 문서

이 문서는 **대화 맥락 없이도** 작업을 이어받게 하는 다리다. 순서대로:
`CLAUDE.md`(작업 지침) → 이 문서(현재 상태) → `PROJECT_BRIEF.md`(판단 기준).

최종 업데이트: 2026-09-20 (인프라·서빙 절 갱신)

---

## 1. 지금 어떤 구성으로 도는가

```
[Windows PC · 한글]  ──브라우저──▶  [Linux 운영 서버(VM)]  ──API──▶  [DGX · sLLM]      (기본 연결: 답변·비전 판독)
  실사용·한글 편집          웹·검색·OCR·문서관리(CPU)   ──API──▶  [젯슨 토르 02·03]  (보조 연결·학습본 서빙)
```

- **LLM 연결은 화면(개발자 > 연결)에서 등록한 값이 우선**이다(`data/platform/llm_connections.json`).
  2026-09-20 등록: 교내 DGX(기본), 토르 03, 토르 02. 스크립트에서 연결을 쓰려면 `llm_connections.configure(...)` 필요.
- **DGX `211.170.162.110`** — Ollama 0.33, HTTP 로만 사용(셸 없음). 모델 qwen3.6:35b(기본, vision 가능, 76~83 tok/s),
  qwen3.8:27b(Writer 베이스, 22~25 tok/s), gpt-oss:120b. Ollama 는 vLLM 식 생각 끄기 인자를 무시하므로
  `reasoning_effort="none"` 을 쓴다 — `generate/client.py` 가 서버를 판별해 자동으로 붙인다.
- **젯슨 토르** `thor-03@211.170.162.121`·`thor-02@211.170.162.120`, SSH 포트 8022(맥 키 등록됨, sudo 는 비밀번호 필요).
  Jetson AGX Thor, 통합 메모리 122GB. **큰 파일을 받은 뒤엔 root 로 `sync; sysctl vm.drop_caches=3` 하고 서빙을 올린다** —
  페이지 캐시를 CUDA 가 되찾지 못해 vLLM 이 cuBLAS 오류로 죽는다(K-47). 확인은 `torch.cuda.mem_get_info()`. Ollama 0.32.6 을 0.0.0.0:11434 로 열었다(인증 없음, ufw 꺼짐).
  모델: 03 qwen3:30b-a3b-instruct-2507(64 tok/s)·gemma4:e2b, 02 qwen3:4b-instruct-2507(53 tok/s).
  주의: `qwen3:30b-a3b`·`qwen3:4b` 태그는 2507 Thinking 판(항상 생각) — Instruct 태그를 쓸 것.
  **문맥 크기를 줄인 서빙용 모델을 따로 만들어 쓴다**(2026-09-21). 원본 태그는 문맥이 262K 라
  호출마다 메모리를 크게 잡고(30B 45GB) 모델이 계속 오르내려 호출이 시간 초과된다. 실측: 같은
  8천 자 요약이 113초 → **5.2초**. Modelfile 로 만든다(sudo 불필요):
  `zzaimy-answer`(30B·16K, 서빙 실험용) · `zzaimy-review`(4B·8K) — 둘 다 9/21부터 용도 지정에서 빠졌다.
  **9/21 확정 분담**: 토르 02 = 대화·초안(사람이 기다리는 일), 토르 03 = 반입 검토·이미지 판독(배치) + 임베딩·리랭커.
  같은 27B(`zzaimy-writer`) **bf16** 을 두 대에 각각 올린다(양자화 판 NVFP4·FP8 은 이 vLLM 빌드에서 적재 실패, ADR-0022 덧붙임).
  실측 bf16 동시 3요청 생성 합계 14 토큰/초. 서빙은 `scripts/126_serve_writer.sh` 하나로 두 대 동일하게.
  DGX 는 문서대로 학습 전용(SSH 불가, Ollama 만 닿음). K-45.
  **임시(9/21 14시~)**: NVFP4 판이 이 vLLM(0.19.0)에서 적재 실패(양자화된 lm_head 를 Qwen3.8 클래스가 못 받음) →
  FP8 판(27GB) 내려받는 동안 반입 검토·이미지 판독을 DGX Ollama `qwen3.8:27b`(4bit)로 돌린다. 실측 판독 1쪽 20~27초
  (토르 bf16 128초), 검토 26.7 토큰/초. 토르 03 에 FP8 이 오르면 다시 토르 03 으로(사용자 확정 구도).
  계획에 없던 gemma4 는 9/21 지웠다(사용자 지적, K-43). 공개 수집 문서만 외부 상용 모델로 읽는 용도(`vision_public`)가 있고 키는 화면에서 넣는다.
  토르 Ollama 0.32.6 은 qwen3.8 을 못 읽는다(갱신은 sudo). 학습본 서빙은 vLLM 컨테이너
  `ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor`(0.19)로 — `scripts/82`(서빙)·`94`(어댑터 전달)·`95`(자가 점검, 9/20 통과).
  VM→.120 은 첫 구간 장비(10.10.10.13)의 허용 목록 누락으로 막혀 있다가 9/20 사용자가 해소.
  **임베딩 재계산은 토르 GPU 로**: `bash scripts/96_embed_on_thor.sh --apply`(맥에서 실행) — 3,751조각 44초,
  VM CPU 계산 표본과 코사인 1.00000. VM CPU 경로(`66_reindex.sh`)는 한 시간 넘게 걸린다.

  **상시 서비스 두 개(9/20 올림, 토르 03, `--restart unless-stopped`·도커 부팅 시작 enabled)**:

  | 포트 | 무엇 | 올리는 법 | VM 쪽 설정(`.env.local`) |
  |---|---|---|---|
  | 8013 | 리랭커 베이스 (bge-reranker-v2-m3) — 비교용 | `bash scripts/102_serve_reranker_on_thor.sh 8013` | (평가에만 씀) |
  | 8014 | 질의 임베딩 베이스 (KURE-v1) — 비교용 | `bash scripts/103_serve_embed_on_thor.sh 8014` | (평가에만 씀) |
  | 8016 | **운영 질의 임베딩 = ZZAIMY-Embed v2 학습본** | `MODEL=/models/zzaimy-embed-v2 bash scripts/103_serve_embed_on_thor.sh 8016` | `ZZAIMY_EMBED_URL=http://211.170.162.121:8016/embed` |
  | 8015 | **운영 리랭커 = ZZAIMY-Rerank v1 학습본** | `MODEL=/models/zzaimy-rerank-v1 bash scripts/102_serve_reranker_on_thor.sh 8015` | `ZZAIMY_RERANK_URL=http://211.170.162.121:8015/score` + `ZZAIMY_RERANK_MIN=0.005` |

  **학습본을 서빙으로 올릴 때는 `bash scripts/116_ship_and_serve.sh <rerank|embed> <학습본이름>`** —
  옮기기·서비스 교체·(임베딩이면) 재색인·(리랭커면) 근거 하한 재측정·설정 갱신·점검까지 한 번에 한다.
  학습 장비에서 가져오려면 `--from <계정@주소>:<경로>` 를 붙인다.

  점검은 `bash scripts/110_serving_check.sh` 한 줄 — 서비스 생존·VM 설정·하한이 모델과 맞는지 함께 본다.

  **색인과 질의 임베딩 모델은 한 짝이다** — 한쪽만 바꾸면 벡터 공간이 어긋나 검색이 조용히 망가진다.
  색인은 `MODEL=… bash scripts/96_embed_on_thor.sh --apply`, 쓰인 모델은
  `data/platform/chunk_embeddings.meta.json` 에 적힌다. ADR-0021.

  **리랭커 모델을 바꾸면 근거 하한을 다시 잰다** — `scripts/105_rerank_floor.py` 로 재고
  `.env.local` 의 `ZZAIMY_RERANK_MIN` 을 갱신한다. 지금 값은 모델 학습 화면에 표시된다.
  눈금은 모델마다 다르다(베이스 0.271 · 학습본 0.005). 학습은 `scripts/104`,
  홀드아웃 검증은 `scripts/106`. 결정 근거는 ADR-0020.

  둘 다 OpenAI 규격이 아니라 단일 용도 규약이다(`/health`·`/score`·`/embed`). VM 은 서비스가 죽으면
  스스로 물러난다(리랭커→VM CPU, 임베딩→VM 모델을 그때 올림, 그것도 없으면 키위 검색만).
  효과(질의 150건 실측 9/20): 리랭킹 질의당 3.75초→0.07초, 정확한 질문 R@1 0.647→0.667,
  상황 질문 R@1 0.413→0.460. VM 상주 메모리 634MB→358MB(질의 임베딩 모델을 안 올린다).
  학습본까지 더하면 홀드아웃 문서에서 정확한 질문 R@1 0.649→0.711·MRR 0.751→0.790.
  후보 수는 10→20 으로 늘렸다(리랭커가 싸져서 가능해진 몫). 오늘 운영 수치:
  정확한 질문 R@1 0.753·MRR 0.786 / 상황 질문 R@1 0.587·MRR 0.630 (아침 0.633/0.704, 0.400/0.504).
  모델 파일은 토르 `~/zzaimy/models/`(KURE-v1·bge-reranker-v2-m3·zzaimy-embed-v1), 서비스 코드는 `~/zzaimy/serve/`.

- **운영 서버 = ESXi VM** (`aura@192.168.16.226`). 웹·검색·OCR·문서관리 담당. GPU 없음.
  - 접속: 학과망 VPN 안에서 `ssh aura@192.168.16.226` (키 등록 필요, 비번은 별도 전달).
  - 웹: `https://192.168.16.226` (self-signed, https·443). 로그인 `zzaimy`(담당자) / `zzdev`(개발자).
  - 서비스: `systemctl --user status zzaimy.service` (자동시작·linger 설정됨). 로그 `~/app.log`.
  - 환경: `~/zzaimy-capstone/.env.local` — 여기 `VLLM_BASE_URL`에 GPU 서버 주소 넣으면 생성 켜짐.
  - 스택: python3.12 venv(`.venv`), 검색(KURE·bge·kiwi), OCR(MinerU·docling·tesseract),
    산출물(python-hwpx·docx·pdf). 전부 오프라인 캐시(`HF_HUB_OFFLINE=1`). torch는 **2.4.1 고정**
    (2.14 최신은 torchvision::nms 오류로 탈락).
  - **패키지 추가는 오프라인 절차**(VM은 pip 네트워크 없음): 맥에서 `.venv/bin/pip wheel <pkg> --no-deps -w data/tmp/wheels`
    → `scp` → VM `.venv/bin/pip install --no-index --no-deps --find-links /tmp/wheels <pkg>`. 2026-09-15 pyhwp(0.1b15)를
    이 절차로 설치 — 그 전까지 VM에는 pyhwp가 없어 .hwp 접수 문서가 빈 채로 처리됐다(재처리 `scripts/79 --write`).
- **새 DGX `220.67.5.51:8022`** — 여전히 접속 불가(확인 대기). 지금 쓰는 DGX 는 위의 211.170.162.110.
- **⚠ 기존 Spark(211.170.162.109 = 211.xx)는 사용 금지** (사용자 지시). 데이터는 이미 VM으로 이관 완료.

## 1-1. 정본과 배포 (2026-09-20 정리)

**정본은 깃허브 하나다.** 예전에는 맥에서 고치고 rsync 로 VM 에 파일만 밀었고, 깃 이력은 VM 에만
쌓여 "무엇이 원본인지"를 매번 확인해야 했다. VM 이 외부로 나갈 수 있게 되면서 아래로 통일한다.

1. 맥에서 고친다 → 테스트 → `git commit` → `git push origin main`
2. `bash scripts/99_deploy.sh --restart` — VM 이 origin 에서 받아 그 커밋으로 맞추고 재시작한다.
   맥에 미커밋 변경이 있거나 푸시하지 않았으면 배포가 멈춘다. VM 에서 직접 고친 것이 있어도 멈춘다.
3. `data/`·`.env.local` 은 깃에 없다 — VM 것이 그대로 남는다.

주의: 실행 중인 스크립트를 `scp` 로 덮어쓰지 않는다(bash 가 바뀐 파일을 이어 읽어 사고가 났다, 9/20).

## 2. 저장소·데이터

- 코드: GitHub **mimonimo/aura** (사용자 소유). 업스트림(읽기전용, 교수님): mrgrit/zzaimy.
- **업스트림 파일 수정 금지**: `PROJECT_BRIEF.md`, `research/**`, `docs/business-plan.md`.
- **데이터 커밋 금지**: 실문서·파싱결과는 `data/` 아래에만(.gitignore). VM에 문서 44·청크 3767 있음.
- 결정은 ADR로: `docs/decisions/` (최신 0008 = 외부 참조 이그레스 게이트웨이).

## 3. 무엇이 되어 있나 (완료)

- 운영 서버 구축·데이터 이관·오프라인 자립·자동시작.
- 검색: 하이브리드(RRF w_a≈0.3) + 리랭커. 실측 R@1 0.49→0.62, MRR 0.62→0.72.
- OCR: MinerU 어절F1 ~0.81. 인식 영역 시각화(팝업·전 페이지). 문서 뷰어 2탭(원본/AI읽기).
- 산출물: docx·pdf·hwpx 내보내기.
- 개발자 대시보드(/dev): 진행률·검색지표·모델 트랙 4종 표·주간 보고서.
- 외부 AI 참조 관리(민감정보 제거·자동 분류·감사 기록·승인 큐·/dev/egress) — 이그레스 게이트웨이, ADR-0008.
  외부 전송은 ZZAIMY_EXTERNAL_ENABLED + 외부 기관 서버 연결(개발 키) + 아웃바운드 개방 전까지 비활성
  (판정·기록은 동작, 허용·승인 건은 전송 대기로 보관).
- 발표 자료: `docs/paper/제안-발표.html`(웹 슬라이드), `docs/paper/제안발표-내용.md`(텍스트).

## 4. 무엇이 남았나 (진행/예정)

| 항목 | 상태 | 막는 것 |
|---|---|---|
| 새 DGX(220.67.5.51) 확인 | 대기 | 제공자 확인 (접속 불가). 기존 DGX(.110)는 연결 완료 |
| sLLM 서빙 연결 | **완료(9/20)** | DGX(.110)·토르 02·03 연결 등록. 학습본 서빙 경로는 95 자가 점검 통과 |
| ②Rerank 파인튜닝 | **완료·운영 적용(9/20)** | 베이스라인(GPU 조건) 먼저 측정 → 학습(`scripts/104`) → 홀드아웃 검증(`106`) → 하한 재측정(`105`) → 8015 서빙. 홀드아웃 R@1 0.649→0.711. ADR-0020·모델 카드 |
| ①Embed 파인튜닝 | v1 보류 · v2 측정 중(9/20) | v1 은 조밀 단독만 올라 하이브리드 이득 없음. v2 는 오답을 융합 후 후보에서 뽑아 재학습(`scripts/108`), 판정은 후보 진입률(`109`) |
| Writer/Extract 파인튜닝 | 예정 | 교내 본문서·골드 세트 없음(★베이스라인 측정 먼저). DGX 는 Ollama 만 열려 있어 학습은 토르에서 |
| 검색 서빙 GPU 이관 | **완료(9/20)** | 리랭커·질의 임베딩을 토르 서비스로(§1 표). 리랭킹 3.75초→0.07초, 후보 10→20, 운영 지표 정확한 질문 R@1 0.753·상황 0.587. 점검 `scripts/110` |
| 이그레스 에이전트 연동 | 예정 | LLM 서빙(DGX) 연결 후 — 채팅·초안에서 관문 경유 외부 참조 |
| 이그레스 실전송 개방 | 통신 개방됨(9/17) | 서버존 나가는 웹은 Imperva WAF 에서 허용 완료. 남은 것: 허브 개발 키를 LLM 연결에 등록(화면 수정 창) + 외부 참조용 지정 + ZZAIMY_EXTERNAL_ENABLED |
| 외부 자료 수집(학습용) | 예정 | 대상 사이트 지정. **기준 문서와 분리**(학습에만) |
| 한글 실시간 편집 에이전트 | **완료(2026-09-08 실장비 검증)** | 앱 다운로드(/dev/hwp)→연결→서버 지시→화면 반영 왕복 확인(한컴 automation 12.0). 남은 것: 대화창 자연어 변환(LLM 연결 후) |
| OCR 도전자 대결(PaddleOCR-VL) | 유보 | GPU 필요 (CPU에선 1쪽 60분+ 불가 확인) |
| 문서함 전체 재반입(9/20~21) | 반입 끝·재처리 진행 | 문서 194건(규정 8 · 교내 113 · 국고 공개 68 · 외부 5) · 기준 조각 4,092개 · 개체 38. 이름은 반입 단계에서 본문 제목으로(`scripts/125` 가 실제 경로로 자가 점검). 남은 실패 1건(내려받기가 막힌 .pdf). 측정2(9/21 12:42): 어휘 0.428 · 임베딩 0.639 · 하이브리드 0.649 (R@1, 질의 1,029). 운영 구성 행(0.550)은 측정을 .env.local 없이 띄워 CPU 베이스 리랭커로 떨어진 값 — 운영 경로 실측은 같은 표본에서 0.683→0.770(K-48). 측정은 env 를 읽고 다시 돌린다(사슬 3 뒤 자동). 지금 공개 수집 73건(마스킹 대상)·깨진 본문 11건 재처리 → 색인 → 스모크 → 최종 측정(사슬 2) |
| 규정 조각 재분할(2026-09-14, 1,236→1,211) | **완료(VM 적용)** | 조 참조에서 끊지 않음·빈 조각 병합·중복 제거·1,400자 상한. 백업 `data/platform/backup/platform-<stamp>.db`. 재색인·개체 재추출 완료. 합성 질의 세트는 VM에 없음(Spark 유실) → 검색 정확도 재측정은 LLM 연결 후 51 재생성 필요 |
| 리랭커 쌍 길이 | **512로 되돌림(9/20)** | CPU 시절 256으로 줄였던 것이 정확한 질문의 순위를 떨어뜨리고 있었다(R@1 0.647→0.640). GPU 서비스에서 512·문서 이름 포함으로 되돌려 0.667. CPU 폴백은 앞쪽 10개만 재정렬 |
| PII 점검 화면(/dev/pii) | **완료(9/15 실측: 자가 점검 19/19, 잔여 0건)** | 마스킹 기록(유형·건수만) 저장, 알려진 정답 자가 점검, 색인 본문 잔여 스캔, 정책표(시점·이유·기록). 전화번호 탐지에 영숫자 경계(해시 파일명 오탐 해소). `scripts/78_post_deploy_smoke.py`가 배포마다 실행·기록 |
| Label Studio 무수동 연동 | **완료(9/15, whoami PASS)** | `scripts/68_labelstudio_token.sh`로 토큰 생성→유닛→재시작→검증, 아이디 저장. /dev/data 연결 상태·진행률. 도구 주소·로그인 아이디·LS 비밀번호 재설정은 `/dev/train` 도구 카드의 계정·연결 창 |
| 계정 로직 정리 (9/15) | 완료 | 비밀번호는 pbkdf2 해시로만 저장(평문 계정은 로그인 때 승격), 비활성 계정 로그인·세션 거부, 프로필 메뉴는 본인 비밀번호만 변경(`/account/password`). 플랫폼 계정 추가·역할 변경 UI는 두지 않음(사용자 지시: 학습 도구 계정만) — 계정 추가는 `data/platform/accounts.json` 직접 편집 |
| NAS 수집 (9/17) | 완료 | `ingest/nas_sync.py`(local·smb 백엔드, 읽기만), 원천 `data/platform/nas_sources.json`(0600)·상태 `nas_state.json`, 화면 `/dev/nas`, 라우트 `/dev/nas/*`, 자동 반입 스케줄러(켜진 원천만). SMB 는 `scripts/80_install_smb.sh`(오프라인 휠 data/tmp/wheels). ADR-0018 |
| 경북 Open AI Service Hub 연결 (9/17) | 키 등록 대기 | `https://open.hasa.re.kr/v1`(OpenAI 호환) 을 외부 기관 GPU 서버 연결로 등록. 개발 키는 사용자가 화면에서 입력, 일 2천만 토큰·RPM 10·동시 1. VM 아웃바운드(open.hasa.re.kr = 221.142.226.68, TCP 443) — 9/17 개방 완료. 막던 곳은 ASA·팔로알토가 아니라 서버존(192.168.16.128/25) 앞 인라인 **Imperva WAF**(관리 콘솔 https://211.170.162.9:8083)였다. 서버망에서 나가는 웹(80·443·22)만 포트로 떨구는 이그레스 정책. WAF 에서 서버존 나가는 웹을 허용하니 즉시 통함. 진단 근거: ASA packet-tracer 는 allow, 그러나 ASA Server_zone_2 인터페이스 캡처에 VM 443 이 0 개(853 은 300 개 왕복) → ASA 앞에서 드롭. 플랫폼 연결 확인 성공(실시간 35 모델·카탈로그 50). 모델 목록은 허브 공개 카탈로그(`/api/catalog`, 50개)를 개발 장비에서 받아 연결에 저장(`catalog`) — 수정 창 드롭다운(사용 가능 8·비전 1). 통신이 열리면 '모델 목록' 버튼으로 갱신. 허브 문서(dolzi-gitc.github.io/GITC-Open-AI-Service-Hub)에 맞춘 것은 OpenAI 호환 규약 범위(Bearer 키·/v1/models·chat/completions·429/503 재시도)까지만 — 허브 전용 헤더(X-Safety-Mode)·임베딩/리랭크 엔드포인트는 쓰지 않음(교내 KURE·bge 유지) |
| LLM 연결 관리 (9/17) | 완료 | `generate/llm_connections.py` + `data/platform/llm_connections.json`(0600). 종류는 교내 GPU 서버·외부 기관 GPU 서버(OpenAI 호환 규격)뿐, 상용 API 없음. 문서 작업 기본은 교내 바로·외부 기관은 ack 후(`activate`), 외부 AI 참조는 외부 기관 연결만(`set_external`, `egress._send_external`). 화면 `/dev/train` LLM 연결 카드, 라우트 `/dev/llm/*`. 호출 공통: 요청 시간 제한(`ZZAIMY_LLM_TIMEOUT`, 기본 180s)·429/5xx 백오프 재시도(`ZZAIMY_LLM_RETRIES`, 기본 3)·응답 usage 를 날짜·연결·모델별로 `data/platform/llm_usage.json` 에 누적(카드에 오늘 요청·토큰)·오류는 사람 말(키 거부/모델 없음/한도/일시 불가). ADR-0017 |
| 모델 서버·모델 선택 (9/16) | 완료 | `src/zzaimy/generate/model_config.py`: 설정(`llm_base_url`·`llm_model`) > 환경변수 > 기본값, `probe()` 가 `/v1/models` 목록. `VllmClient` 가 이를 따름(모델 미선택이면 서버 첫 모델). 화면은 `/dev/train` 모델 서버 카드, 저장은 `POST /dev/train/model`, 기동 시 `create_app` 에서 적용 |
| 데이터 열람 탐색기 (9/15) | 완료 | `/dev/db` 탭(문서·규정·국고 코퍼스·채팅 기록). 문서 상세 = 개요·마스킹 기록(유형·건수)·추출 조각·검색 단위(임베딩 유무, `chunk_embeddings.meta.json`+npz)·그래프 연관(`build_graph`). 모음 함수는 `src/zzaimy/app/data_explorer.py`. `/dev/corpus` 는 `/dev/db?tab=corpus` 로 301 |
| 한글 에이전트 설치파일 배포 (9/15) | 완료 | Windows 에서 빌드 키트로 만든 setup.exe 를 /dev/hwp 에서 올림(MZ 서명·크기 검사, sha256·버전 메타 `data/dist/zzaimy-agent-setup.json`), 담당자 내려받기는 `/hwp/setup.exe`(로그인 필요) |
| 개발 현황 허브화 (9/15) | 완료 | /dev 는 개요·구축 현황(기능 상태에서 파생한 영역별 완료 비율)·진행 현황·모델 트랙 + 바로가기 카드만. 상세는 /dev/quality(검색 품질·백로그), /dev/docs(논문·ADR·기술 검토·측정 기록), /dev/history(작업 기록 전체·변경 이력·주간 보고서). 진행 현황은 최신 날짜 작업만. 학습 도구 계정·연결(LS 아이디·비밀번호 재설정, GPU 도구 주소)은 /dev/train 도구 카드 설정 창 |
| 검색 품질 카드(기계 산출물) | 완료 | `data/platform/eval/retrieval-latest.json`만 렌더, 없으면 '아직 측정 없음'. `/dev/eval/run`은 질의 세트 없으면 409 |
| 개발자 영역 전수 감사 반영 | 완료 | 감사 40여 건 반영 + 9/15 중복 통합(규모 타일 공용, 근거 없는 % 제거, 변경 이력 통합 — `docs/notes/uiux-audit.md` 기록) + 한국어 표현 전수 손질(`docs/notes/ui-glossary.md`, 외부 참조 관문→외부 AI 참조 관리). 남은 것: B15, E6 |
| 지식 그래프 프로젝트 탐색 | 완료(9/15) | 프로젝트 목록·선택 상세 패널·검색 목록·프로젝트↔문서 추정 연관(임베딩, 배경 캐시). 프로젝트 이름·지침이 실제 내용이어야 연관이 잡힘 |
| OCR 표·그림·문맥 추출 고도화 (ADR-0016) | **완료(9/15 VM 실측)** | 표 평문(text)·캡션·주석, 그림 속 글자(tesseract), HWPX/HWP5 구조 파서, 이미지 문서 tesseract 폴백(docling 모델 없음), 글자 띄움 OCR 보정. 실측: 디지털 PDF 표 10개 평문 284s, 스캔 PDF 표 4개 466s(CPU MinerU), .hwp 표 2개 4s, PNG는 MinerU OCR 570s(품질 우선)·docling/MinerU 실패 시 tesseract 폴백 6s. 남은 것: 표 캡션 오부착 사례, MinerU 속도, 그림 설명(VLM, DGX 후). 검증 `scripts/79_ocr_structure_check.py <id> [--write]` |
| 접수번호 규칙 | 완료(9/15) | `연도-유형코드-일련번호`, 일련번호는 기존 최대값+1(삭제 후 재사용 없음) |
| 라벨 없는 성명 마스킹(NER) | **결정 대기** | 서식 표 라벨('성 명', '학 번', '생년월일') 기반 탐지는 9/15에 추가했으나, 상장처럼 라벨 없이 적힌 이름은 규칙으로 못 잡는다. 한국어 NER 모델(오프라인 반입, 수백 MB, CPU 추론)이 필요 — 사용자 결정 후 진행 |
| 배점 커버리지 의미 판정 재보정 (ADR-0015) | 미확인 | 돋보임 폭 0.10은 합성 문단 표본 기준. 실제 생성 초안·공고 쌍이 나와야(LLM 서빙 후) 재보정 가능 — 관련 항목 폭이 하한(+0.11~0.13)에 가까움 |

## 5. 절대 지킬 규칙 (어기면 사고)

- **수치는 인출하고 생성하지 않는다** (환각 = 허위기재). 검증기가 대조.
- **★베이스라인 측정 전에 파인튜닝 시작 금지** (안 그러면 개선폭 입증 불가).
- **PII는 인덱싱 전 마스킹**, 열람 등급은 검색 단계 필터.
- **하드코딩·억지 짜맞추기 금지** — 성능은 일반 메커니즘으로만 (사용자 강조).
- **가용 자원 꽉 채우지 말 것** — CPU/메모리 무거운 작업은 코어 절반·낮은 우선순위.
  (2026-09-08 OCR 배치에 전체 코어 할당 → VM 6시간 먹통 실사고.)
- **작업은 순차로** — 배포+재시작+테스트를 한 체인에 묶지 말 것. 상태 확인 후 다음.
- **외부로 내부정보 유출 절대 금지** — 외부 참조는 이그레스 게이트웨이(세척·분류) 통과 필수.

## 6. 이어받는 절차 (다른 계정/PC)

1. GitHub mimonimo/aura **협업자 권한** 받기 → `git clone`.
2. 학과망 **VPN 접근** + VM SSH 키 등록 (서버를 만질 경우).
3. 새 Claude Code 세션에서 이 문서 + `CLAUDE.md` + `PROJECT_BRIEF.md` 읽고 시작.
4. 서버 상태 확인: `ssh aura@192.168.16.226 'systemctl --user status zzaimy.service'`.
5. 코드 수정 → 맥에서 push → VM에서 `git pull`(프록시 물린 상태에서만 외부 통신).

> 인프라의 세션 로컬 메모리(SSH 예절·프록시 터널 구성 등)는 이 문서로 대체됨.
> 상세 세션 기록이 필요하면 이전 담당자에게 대화 로그를 요청.
