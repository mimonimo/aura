# 설치·운영 안내

AURA 를 새 서버(전용 서버, 다른 기관)에 깔고, 모델 서버와 잇고, 업데이트·백업·점검하는 방법이다.
명령은 모두 저장소의 `deploy/` 아래에 있다. 서버를 옮길 때는 [서버 이전](migration.md) 을 함께 본다.

## 1. 구성과 준비물

| 구성 | 하는 일 | 장비 |
|---|---|---|
| 앱 서버 | 웹 화면·API, PostgreSQL(pgvector), 검색 색인·지식 그래프, 공동 편집, 한글 읽기(kordoc), 주기 작업 | GPU 없는 서버 1대. 지금 운영: CPU 8, 메모리 64GB, 디스크 500GB, Ubuntu 22.04 |
| 모델 서버 | 문서 작업 27B(vLLM), 질의 임베딩, 리랭커 | GPU 서버. 지금 운영: 젯슨 AGX 토르 2대(27B NVFP4) |
| 웹 한글 기안기 | 브라우저에서 hwpx 편집(한컴 제품) | 앱 서버 안(127.0.0.1) |
| 일괄 판독·학습(선택) | 스캔 PDF 판독(MinerU), 모델 학습 | DGX 등. 없어도 서비스는 돈다 |

설치본이 대신할 수 없어 기관이 먼저 갖출 것:

- 서버와 OS(Ubuntu 22.04·24.04), 모델 서버의 GPU 드라이버·Docker·NVIDIA 컨테이너 런타임
- 한컴 웹 한글 기안기 패키지·라이선스(`license.dat`) — 기관마다 한컴에서 받는다
- 모델 가중치(수십 GB) — 디스크로 받거나 내려받는다
- 외부에 열 때 TLS 인증서. 사내망 안에서만 쓰면 앞단 프록시 없이 앱 서버가 직접 443 을 연다

## 2. 앱 서버 설치

인터넷이 되는 서버:

```
git clone https://github.com/mimonimo/aura.git ~/aura && cd ~/aura
bash deploy/install.sh --port 443 --host 0.0.0.0 \
     --tls-cert /etc/ssl/aura.crt --tls-key /etc/ssl/aura.key --public-url https://aura.example.ac.kr
```

인터넷이 막힌 서버(사내 폐쇄망): 인터넷 되는 PC(Docker 필요)에서 설치 묶음 하나를 만들어 옮긴다.

```
bash deploy/bundle.sh                       # dist/aura-<판>.tar.gz — 코드·파이썬 패키지·검색 모델·kordoc·Node.js
# 서버에서
tar -xzf aura-<판>.tar.gz && cd aura-<판>/aura && bash deploy/install.sh --offline ..
```

`install.sh` 가 하는 일(다시 돌려도 같은 결과): 점검 → 파이썬 환경 → PostgreSQL·pgvector(sudo 가 되면 설치·역할·스키마까지)
→ `.env.local`(0600) → 데이터 폴더 → CPU 비상용 검색 모델 → kordoc → 사용자 서비스(`zzaimy.service`) → 주기 작업(cron)
→ 기동 확인. sudo 가 필요한 일을 못 하면 끝에 남은 일로 알려 준다. 옵션은 `bash deploy/install.sh --help`.

시험 삼아 DB 없이 띄울 때는 `--db sqlite`. 운영은 PostgreSQL 을 쓴다.

## 3. 첫 설정(관리자 계정으로, 화면에서)

설치가 끝나면 개발자(관리자) 계정 `zzdev` 하나가 있다. 첫 비밀번호는 `data/platform/initial_dev_password.txt`(0600)에만
남고, 첫 로그인 때 새 비밀번호로 바꾸게 한다. 그 뒤 모든 연동은 화면에서 한다.

1. 「도구·모델 연결」(`/dev/tools`) → LLM 연결 → 서버 추가: `http://<GPU 서버>:8001/v1`. 단계별 모델에서 문서 작업·반입 검토·판독·도우미에 쓸 서버를 고른다.
2. 같은 화면 「서버 설정」: 임베딩 서버(`…/embed`), 리랭커 서버(`…/score`), 원본 폴더, 웹 검색 키. 줄마다 「확인」 으로 연결을 본다.
   화면 값은 바로 반영되고, 「서버 파일 값으로」 를 누르면 `.env.local` 값으로 돌아간다. 비밀 값(키)은 다시 보이지 않는다.
3. 「조직 관리」(`/team`) → 부서 만들기 → 전체 사용자 「일괄 등록」: 엑셀·CSV(아이디·이름·부서·역할·등급·상위 부서).
   임시 비밀번호는 등록 결과 화면에서 파일로 한 번만 받는다. 각자 첫 로그인 때 바꾼다. 양식은 같은 창의 「양식 받기」.
4. 한글 웹 기안기: `bash deploy/webhwp/install.sh <한컴 패키지.zip> <apache-tomcat-9.x.zip> <license.dat>`
5. 기관 서식 프로필(선택): 원본 폴더에 그 기관이 쓴 계획서·보고서가 들어오면
   `env PYTHONPATH=src .venv/bin/python scripts/229_house_profile.py --inst <기관 이름>` — 양식이 본문 모양을 정하지 않은 자리에
   에이전트가 그 기관 문서의 글꼴·크기로 쓴다.
6. 점검: `bash deploy/check.sh`

## 4. 연동

| 대상 | 정하는 곳 | 비고 |
|---|---|---|
| 문서 작업 27B 등 LLM | 「도구·모델 연결」 LLM 연결·단계별 모델 | OpenAI 호환이면 어느 서버든. 모델 이름·문맥 길이는 서버에서 읽는다. 외부 서버는 교내 문서가 지나가므로 용도에 바로 쓰지 않는다 |
| 임베딩·리랭커 | 「서버 설정」 검색 서빙 | 비우면 앱 서버 CPU(느림). 임베딩 모델을 바꾸면 전체 재색인 |
| 원본 폴더 | 「서버 설정」 원본 폴더 | 서버 안 폴더(NAS 를 OS 에서 붙인 자리, 예 `/mnt/nas/원본`) 또는 `계정@주소:경로`(SSH, 읽기 전용 키). 1분마다 새 문서를 들인다 |
| 웹 검색 | 「서버 설정」 웹 검색 | 키가 있으면 Google·네이버, 없으면 DuckDuckGo. 질문 낱말만 나간다 |
| 한글 웹 기안기 | `deploy/webhwp/install.sh` | 127.0.0.1 에만 열고 AURA 가 넘겨 준다. 포트를 바꾸면 `.env.local` 의 `ZZAIMY_WEBHWP_URL` |
| 구글 드라이브·독스(선택) | 「조직 관리」 구글 연결 | 앱 등록은 관리자, 사용자마다 학교 계정 연결 |

모델 서버(GPU 서버에서):

```
bash deploy/model-server/serve.sh writer --model /srv/models/Qwen3.8-27B-NVFP4 --port 8001
bash deploy/model-server/serve.sh embed  --model /srv/models/zzaimy-embed-v2  --port 8016
bash deploy/model-server/serve.sh rerank --model /srv/models/zzaimy-rerank-v1 --port 8015
bash deploy/model-server/serve.sh status
```

젯슨(토르)과 일반 GPU 서버를 알아서 가려 이미지를 고른다. 컨테이너는 재부팅 뒤에도 뜬다.

## 5. 배포·업데이트

정본은 GitHub 저장소 하나다. 데이터(`data/`)와 `.env.local` 은 깃에 없고 서버 것이 그대로 남는다.

| 상황 | 명령 |
|---|---|
| 서버에서 최신으로 | `bash deploy/update.sh` |
| 정한 판으로(되돌리기 포함) | `bash deploy/update.sh --ref <태그·커밋>` |
| 폐쇄망 서버 | `bash deploy/update.sh --bundle aura-<새 판>.tar.gz` |
| 개발 PC 에서 운영 서버로 | `bash scripts/99_deploy.sh --restart` |

`update.sh` 는 서버에서 고친 파일이 있으면 멈추고, 패키지 목록이 바뀌었을 때만 다시 설치하고, 진행 중인 문서 작성 요청이
끝나기를 기다렸다가(최대 30분) 다시 시작한다. 새 판이 뜨지 않으면 앞 판으로 저절로 되돌린다. DB 표는 앱이 뜰 때 스스로 맞춘다.

## 6. 백업·복구

`install.sh` 가 매일 02:30 백업을 cron 에 넣는다. 손으로 돌릴 때는 `bash deploy/backup.sh`.

- DB: PostgreSQL 은 앱 스키마만 `pg_dump` 날짜별(기본 최근 7개, `AURA_BACKUP_KEEP`). SQLite 는 온라인 백업.
- 데이터 폴더 거울: 원본·생성 문서·장부(캐시·임시 제외). 지운 파일도 거울에는 남는다.
- 설정: `.env.local`·`configs/hosts.env`·계정 파일(0600).
- 자리: `AURA_BACKUP_DIR`(기본 `~/aura-backups`). 다른 디스크·NAS 로 두는 것이 좋다.

되살리기: `bash deploy/restore.sh` 로 날짜를 보고 `bash deploy/restore.sh <날짜> [--with-data]`. 되돌리기 전에 지금 상태를
한 번 더 백업하므로, 되돌린 것도 다시 되돌릴 수 있다.

## 7. 점검과 장애 대응

`bash deploy/check.sh` — 서비스·로그인 화면·DB·LLM 연결·검색 서빙·디스크·메모리·마지막 백업·주기 작업·원본 갱신 기록을 ✓/✗ 로.

| 증상 | 먼저 볼 것 |
|---|---|
| 화면이 안 열림 | `systemctl --user status zzaimy` · `journalctl --user -u zzaimy -n 80` |
| 답·초안이 안 나옴 | `check.sh` 의 LLM 연결 줄 → GPU 서버 `serve.sh status`·`docker logs` |
| 검색이 느림 | 임베딩·리랭커 서버 줄이 「정하지 않음」 이면 CPU 로 도는 중 |
| 한글 문서가 안 열림 | `systemctl --user status webhwp-tomcat webhwp-filter` |
| 새 원본이 안 들어옴 | `/tmp/zz_sync.log`, 「서버 설정」 원본 폴더의 「확인」 |
| 재부팅 뒤 안 뜸 | `loginctl show-user $(id -un) -p Linger` 가 yes 인지(아니면 `sudo loginctl enable-linger <계정>`) |
| 메모리 부족 | 배치는 `scripts/zz_run.sh` 상한 안에서 돈다. `zzaimy.service` 의 `MemoryMax` 를 서버에 맞게 |

## 8. 파일 지도

```
deploy/install.sh      앱 서버 설치            deploy/update.sh     업데이트·되돌리기
deploy/backup.sh       백업                    deploy/restore.sh    되살리기
deploy/check.sh        점검                    deploy/bundle.sh     폐쇄망 설치 묶음 만들기
deploy/webhwp/         한컴 웹 한글 기안기      deploy/model-server/ GPU 서버(27B·임베딩·리랭커)
deploy/systemd/        서비스 틀               deploy/cron/         주기 작업 틀
deploy/env.template    .env.local 틀
```
