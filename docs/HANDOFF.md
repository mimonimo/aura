# 인수인계 — 다른 세션/계정이 이어받을 때 먼저 읽는 문서

이 문서는 대화 맥락 없이도 작업을 이어받게 하는 다리다. 순서대로:
`CLAUDE.md`(작업 지침) → 이 문서(현재 상태) → `PROJECT_BRIEF.md`(판단 기준).

최종 업데이트: 2026-09-22 (§1 두 토르 NVFP4 구성 확정)

---

## 1. 지금 어떤 구성으로 도는가

```
[Windows PC · 한글]  ──브라우저──▶  [운영 서버 VM · CPU]  ──API──▶  [토르 02 · Writer 27B NVFP4 :8001]   대화·초안 (answer)
  실사용·한글 편집                웹·검색·OCR·문서관리      ──API──▶  [토르 03 · Writer 27B NVFP4 :8001]   반입 검토·이미지 판독·질의 확장 (review·vision)
                                                          ──API──▶  [토르 03 · 임베딩 :8016 · 리랭커 :8015]  검색 모델 서비스(학습본)
                                                                    [DGX .110]  학습 전용 — SSH dgx-01@211.170.162.110 -p 8022
```

### 역할 분담

토르 02는 대화와 초안 작성처럼 사람이 기다리는 일을 맡고, 토르 03은 반입 검토와 이미지 판독 같은
배치 작업과 임베딩·리랭커 서비스를 맡는다. 같은 27B 모델을 두 대에 각각 올려 배치가 대화를 막지
않게 했다. DGX는 학습만 한다. 계획에 없는 모델은 쓰지 않는다. 9월 21일에 계획 밖 소형 판독
모델을 지웠다.

LLM 연결은 화면(개발자 > 연결)에서 등록한 값이 우선이며 `data/platform/llm_connections.json`에
저장된다. 지금 연결은 토르 02의 Writer(answer, 기본 연결)와 토르 03의 Writer(review, vision) 둘뿐이다.
스크립트에서 연결을 쓰려면 `llm_connections.configure(...)`를 부른다. 공개 수집 문서만 외부 상용
모델로 읽는 용도(`vision_public`)가 따로 있고 키는 화면에서 넣는다. 지정하지 않으면 판독 모델을
쓴다(ADR-0024).

### 젯슨 토르

`thor-02@211.170.162.120`과 `thor-03@211.170.162.121`, SSH 포트 8022다. 맥의 키가 등록돼 있고 sudo는
비밀번호가 필요하다. Jetson AGX Thor로 통합 메모리 122GB. 모델 파일은 `~/zzaimy/models/`, 서비스
코드는 `~/zzaimy/serve/`에 있다.

Writer 서빙은 `scripts/126_serve_writer.sh` 하나로 한다(HOST, MODEL, IMAGE, PORT 인자). 두 대 모두
`MODEL=/models/Qwen3.8-27B-NVFP4`, `IMAGE=ghcr.io/nvidia-ai-iot/vllm:qwen3.8-next-jetson-thor-latest`
다. 9월 22일 토르 02까지 교체를 마쳤고 요청 하나에 초당 10.9토큰이 나온다(원본 bf16은 2.4~4.5).
양자화 판은 기본 젯슨 빌드(gemma4 태그, vLLM 0.19)에서 뜨지 않으므로 반드시 Qwen3.8 전용 태그를
쓴다. 학습본은 `116_ship_and_serve.sh writer`로 두 대에 올린다.

젯슨의 GPU 메모리 함정이 둘 있다. 큰 파일을 받은 뒤에는 페이지 캐시가 GPU 메모리를 막아 vLLM이
cuBLAS 오류로 죽으므로 root로 `sync; sysctl vm.drop_caches=3`을 한 뒤 서빙을 올린다. 컨테이너를
`docker rm -f`로 죽이면 GPU 메모리 88GB가 돌아오지 않아 재부팅이 필요했으므로 126은 `docker stop
-t 60`으로 정상 종료를 기다린다. 확인은 `torch.cuda.mem_get_info()`로 한다.

토르의 Ollama(0.32.6, 포트 11434)는 9월 21일부터 용도에서 빠졌다. 그 위의 `zzaimy-answer`와
`zzaimy-review` 모델은 서빙 실험의 흔적이다. 운영 서버에서 토르 02로 가는 길은 첫 구간 장비의
허용 목록 누락으로 막혀 있다가 9월 20일 사용자가 풀었다.

임베딩 재계산은 토르 GPU로 한다. 맥에서 `bash scripts/96_embed_on_thor.sh --apply`를 실행하면
조각 4천 개가 1분 안에 끝나고, 운영 서버 CPU 표본과 코사인 1.00000으로 맞았다. 운영 서버 CPU
경로(`66_reindex.sh`)는 한 시간 넘게 걸린다.

### DGX

`ssh -p 8022 dgx-01@211.170.162.110`으로 접속한다(맥 키 등록, 9월 22일 확인, sudo는 비밀번호 필요).
호스트 이름 spark-b30a, GB10, aarch64, 통합 메모리 121GB, Ubuntu 24.04, 디스크 3.0TB 여유, docker 29와
python 3.12가 있다. 같은 장비에 Ollama가 상주하며 27b, 35b, 120b 모델로 메모리 93GB를 잡고
있으므로 학습 전에는 그 모델들을 내려야 한다(`keep_alive` 0 또는 서비스 중지, sudo). 사용 금지인
옛 Spark(.109)와는 다른 장비다. 9월 21일 오후에 잠시 서빙을 넘겼다가 토르 03으로 복귀하며 연결을
지웠다. 학습본 이관 경로는 ADR-0023 3절(bf16 병합, NVFP4 양자화, 116으로 두 토르)이다.

학습 환경(2026-09-22 설치): `~/zzaimy-capstone`(깃 체크아웃) 안의 `.venv-train` — `configs/training-env.txt` 고정
스택(torch 2.13.0+cu130, transformers 5.6, peft 0.18, trl 0.24, sentence-transformers 6.0, llamafactory 0.9.5)을
그대로 설치했고 GB10 에서 bf16 행렬곱까지 확인했다. bitsandbytes 는 없다(aarch64 CUDA 13 휠 없음) — Writer 학습은
bf16 LoRA 가 기본이고 `scripts/83 --4bit` 는 휠이 생기면 쓴다. 베이스 가중치는 `~/zzaimy/models/Qwen3.8-27B`(토르 02 에서
rsync). 학습 전에는 Ollama 모델을 내린다: `curl -s localhost:11434/api/generate -d '{"model":"qwen3.8:27b","keep_alive":0}'`
(35b·120b 도 같이) — 내리면 CUDA 가용 91GB. 장비 검증은 `env PYTHONPATH=src .venv-train/bin/python scripts/83_sft_writer_qlora.py
--base ~/zzaimy/models/Qwen3.8-27B --smoke`(합성 4쌍 2스텝, 9월 22일 통과: 최고 메모리 52.7GB, 모델은 전부 GPU 에
올린다 — device_map auto 는 층을 meta 로 내려 역전파가 실패했다). 도커는 dgx-01 계정이 docker 그룹이 아니라 못 쓴다(sudo).

### 토르 03의 상시 서비스

9월 20일에 올렸고 `--restart unless-stopped`와 도커 부팅 시작이 걸려 있다.

| 포트 | 무엇 | 올리는 법 | 운영 서버 설정(`.env.local`) |
|---|---|---|---|
| 8013 | 리랭커 베이스(bge-reranker-v2-m3), 비교용 | `bash scripts/102_serve_reranker_on_thor.sh 8013` | 평가에만 |
| 8014 | 질의 임베딩 베이스(KURE-v1), 비교용 | `bash scripts/103_serve_embed_on_thor.sh 8014` | 평가에만 |
| 8016 | 운영 질의 임베딩, ZZAIMY-Embed v2 | `MODEL=/models/zzaimy-embed-v2 bash scripts/103_serve_embed_on_thor.sh 8016` | `ZZAIMY_EMBED_URL=http://211.170.162.121:8016/embed` |
| 8015 | 운영 리랭커, ZZAIMY-Rerank v1 | `MODEL=/models/zzaimy-rerank-v1 bash scripts/102_serve_reranker_on_thor.sh 8015` | `ZZAIMY_RERANK_URL=http://211.170.162.121:8015/score`, `ZZAIMY_RERANK_MIN=0.005` |
| 8017 | KURE-v2 다중 벡터 검색, 비교용(운영은 부르지 않음) | `bash scripts/128_kure2_index_on_thor.sh` 뒤 `bash scripts/129_serve_kure2_on_thor.sh 8017` | 측정에만 `ZZAIMY_ALT_DENSE_URL=http://211.170.162.121:8017/search` |

학습본을 서빙으로 올릴 때는 `bash scripts/116_ship_and_serve.sh <rerank|embed> <학습본이름>`을 쓴다.
옮기기, 서비스 교체, 임베딩이면 재색인, 리랭커면 근거 하한 재측정, 설정 갱신, 점검까지 한 번에
한다. 학습 장비에서 가져오려면 `--from <계정@주소>:<경로>`를 붙인다. 점검은 `bash
scripts/110_serving_check.sh` 한 줄이며 서비스 생존, 운영 서버 설정, 하한이 모델과 맞는지 함께 본다.

색인과 질의 임베딩 모델은 한 짝이다. 한쪽만 바꾸면 벡터 공간이 어긋나 검색이 조용히 망가진다.
색인은 `MODEL=… bash scripts/96_embed_on_thor.sh --apply`로 만들고 쓰인 모델은
`data/platform/chunk_embeddings.meta.json`에 적힌다(ADR-0021). 리랭커 모델을 바꾸면 근거 하한을
`scripts/105_rerank_floor.py`로 다시 재고 `.env.local`의 `ZZAIMY_RERANK_MIN`을 갱신한다. 눈금은
모델마다 다르다(베이스 0.271, 학습본 0.005). 학습은 `scripts/104`, 홀드아웃 검증은 `scripts/106`,
결정 근거는 ADR-0020이다.

두 서비스는 OpenAI 규격이 아니라 단일 용도 규약(`/health`, `/score`, `/embed`)이다. 운영 서버는
서비스가 죽으면 스스로 물러난다. 리랭커는 CPU로, 임베딩은 운영 서버 모델을 그때 올리고, 그것도
없으면 어휘 검색만 한다. 검색을 GPU로 옮긴 효과는 리랭킹이 질의당 3.75초에서 0.07초로 줄고 운영
서버 상주 메모리가 634MB에서 358MB로 준 것이다. 학습본까지 더하면 홀드아웃 문서에서 정확한 질문의
R@1이 0.649에서 0.711로 올랐다. 후보 수는 리랭커가 싸진 덕에 10개에서 20개로 늘렸다. 최신 운영
수치는 품질·성능 화면과 `data/platform/eval/retrieval-latest.json`에 있다.

### 운영 서버

ESXi VM `aura@192.168.16.226`이다. 웹, 검색, OCR, 문서 관리를 맡고 GPU가 없다. 학과망 VPN 안에서
`ssh aura@192.168.16.226`으로 접속하며 키 등록이 필요하고 비밀번호는 별도로 전달한다. 웹은
`https://192.168.16.226`(자체 서명 인증서)이고 로그인은 `zzaimy`(담당자)와 `zzdev`(개발자)다. 서비스는
`systemctl --user status zzaimy.service`로 보고 자동 시작과 linger가 설정돼 있다. 로그는 `~/app.log`.

환경은 `~/zzaimy-capstone/.env.local`에 있다. 스택은 python 3.12 가상환경(`.venv`)에 검색(KURE, bge,
kiwi), OCR(MinerU, docling, tesseract), 산출물(python-hwpx, docx, pdf)이며 전부 오프라인 캐시로 돈다
(`HF_HUB_OFFLINE=1`). torch는 2.4.1로 고정한다. 최신 2.14는 torchvision의 nms 오류로 탈락했다.

패키지 추가는 오프라인 절차다. 운영 서버는 pip 네트워크가 없으므로 맥에서 `.venv/bin/pip wheel
<pkg> --no-deps -w data/tmp/wheels`로 휠을 만들어 scp로 옮긴 뒤 `.venv/bin/pip install --no-index
--no-deps --find-links /tmp/wheels <pkg>`로 설치한다. 9월 15일 pyhwp를 이 절차로 설치했다. 그 전에는
.hwp 접수 문서가 빈 채로 처리됐고 `scripts/79 --write`로 다시 처리했다.

새 DGX `220.67.5.51:8022`는 여전히 접속되지 않는다(확인 대기). 지금 쓰는 DGX는 위의
211.170.162.110이다. 옛 Spark(211.170.162.109)는 사용 금지이며 데이터는 이미 운영 서버로 옮겼다.

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

## 3-1. 교내 실물 문서가 오면 (학습까지의 절차)

1. 반입: 화면 업로드 또는 공유 폴더 원천으로 들인다. 문서마다 부서·열람 등급이 붙는다(access_policy, 기본은 올린 사람의
   부서·유형 기본 등급). 접수 문서는 개인정보를 가린 뒤 저장된다. 기준 문서는 같은 제목의 판본·같은 내용을 가리고
   (doc_family, 같은 내용이면 받지 않음) 서류 갈래(공고·양식·계획서 …)를 받는다(ADR-0027). 반입 뒤 `scripts/125`(자가 점검)·
   `78`(스모크)·`84`(반입 상태 판정)를 돌린다.
2. 검색 정답 세트: 담당자가 실무 질의와 정답 조각을 200~500문항 만든다(eval-plan 1.1). 합성 질의(51)는 보조.
3. 베이스라인: 검색은 `scripts/53`(운영 설정으로), 초안은 `scripts/133 --docs <실물 공고 id>` 로 `data/eval/baseline.json`.
   이 기록이 없으면 학습 스크립트가 돌지 않는다.
4. 학습 자료: `/dev/data` 데이터 공방에서 검토 의견·초안·대화를 학습 쌍으로 만들고 수치 검증 통과분만 `data/train/sft.jsonl` 로.
5. 학습(DGX): `baseline.json` 과 `sft.jsonl` 을 DGX 의 같은 자리로 복사한 뒤
   `env PYTHONPATH=src .venv-train/bin/python scripts/83_sft_writer_qlora.py --base ~/zzaimy/models/Qwen3.8-27B --data data/train/sft.jsonl --out ~/zzaimy/train/writer-v1`.
   검색 모델(Embed·Rerank)은 토르 03 의 `104`·`108` 로 다시 학습한다.
6. 이관: 병합 → NVFP4 양자화 → `scripts/116_ship_and_serve.sh writer <이름> --from dgx-01@211.170.162.110:~/zzaimy/train` 로 두 토르에.
   채택은 같은 입력의 대결(검토·판독·초안 검증 결과)로만, 결과는 ADR 로.

## 4. 무엇이 남았나 (진행/예정)

| 항목 | 상태 | 막는 것 |
|---|---|---|
| 새 DGX(220.67.5.51) 확인 | 대기 | 제공자 확인 (접속 불가). 기존 DGX(.110)는 연결 완료 |
| sLLM 서빙 연결 | **완료(9/20)** | DGX(.110)·토르 02·03 연결 등록. 학습본 서빙 경로는 95 자가 점검 통과 |
| ②Rerank 파인튜닝 | **완료·운영 적용(9/20)** | 베이스라인(GPU 조건) 먼저 측정 → 학습(`scripts/104`) → 홀드아웃 검증(`106`) → 하한 재측정(`105`) → 8015 서빙. 홀드아웃 R@1 0.649→0.711. ADR-0020·모델 카드 |
| ①Embed 파인튜닝 | v1 보류 · v2 측정 중(9/20) | v1 은 조밀 단독만 올라 하이브리드 이득 없음. v2 는 오답을 융합 후 후보에서 뽑아 재학습(`scripts/108`), 판정은 후보 진입률(`109`) |
| Writer/Extract 파인튜닝 | 예정 | 교내 본문서·골드 세트 없음(★베이스라인 측정 먼저). 학습 장비는 DGX(.110, SSH 확인 9/22) — 학습 환경(.venv-train·LLaMA-Factory) 설치가 다음 |
| 검색 서빙 GPU 이관 | **완료(9/20)** | 리랭커·질의 임베딩을 토르 서비스로(§1 표). 리랭킹 3.75초→0.07초, 후보 10→20, 운영 지표 정확한 질문 R@1 0.753·상황 0.587. 점검 `scripts/110` |
| 이그레스 에이전트 연동 | 예정 | LLM 서빙(DGX) 연결 후 — 채팅·초안에서 관문 경유 외부 참조 |
| 이그레스 실전송 개방 | 통신 개방됨(9/17) | 서버존 나가는 웹은 Imperva WAF 에서 허용 완료. 남은 것: 허브 개발 키를 LLM 연결에 등록(화면 수정 창) + 외부 참조용 지정 + ZZAIMY_EXTERNAL_ENABLED |
| 외부 자료 수집(학습용) | 예정 | 대상 사이트 지정. **기준 문서와 분리**(학습에만) |
| 한글 실시간 편집 에이전트 | **완료(2026-09-08 실장비 검증)** | 앱 다운로드(/dev/hwp)→연결→서버 지시→화면 반영 왕복 확인(한컴 automation 12.0). 남은 것: 대화창 자연어 변환(LLM 연결 후) |
| OCR 도전자 대결(PaddleOCR-VL) | 유보 | GPU 필요 (CPU에선 1쪽 60분+ 불가 확인) |
| 문서함 전체 재반입(9/20~21) | **완료(9/21 17:05)** | 문서 194건(규정 8 · 교내 113 · 국고 공개 68 · 외부 5) · 기준 조각 4,254개 · 개체 38. 이름은 반입 단계에서 본문 제목으로(`scripts/125` 가 실제 경로로 자가 점검). 남은 실패 1건(내려받기가 막힌 .pdf). 스모크 PASS(PII 자가 점검 19/19 · 잔여 0) · 반입 점검 정상률 80.4% · 서빙 점검 통과. **측정(env 포함, 질의 1,026·표본 300)**: 어휘 R@1 0.430 · 임베딩 0.691 · 하이브리드 0.671 · 운영(리랭커) **0.763** (R@10 0.927, 같은 표본 하이브리드 0.680, 서빙 장비 응답 300·폴백 0). 앞선 세 측정(0.55)은 env 없이 띄워 CPU 베이스로 떨어진 값이라 무효(K-48). 9/20 대비: 운영 0.773→0.763(코퍼스·질의 세트가 바뀜, 재결선 안 된 행 267) |
| 반입 품질 — 표 평문·쪽 번호·판본·갈래 (9/22) | **완료(VM 소급)** | 표 평문은 병합 셀을 한 번만(137: 표 조각 1,444개 평문 갱신), 글자층 직독 조각에 쪽 번호(재처리), 같은 제목 기준 문서의 중복·판본 판정과 붙임 잇기·서류 갈래(136: 판본 8·갈래 173/192·붙임 잇기 37). 판독이 깨진 게시물 9건은 27B 비전으로 재판독. ADR-0027 |
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
