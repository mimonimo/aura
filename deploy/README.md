# deploy — 설치·업데이트·백업·점검

새 서버에 AURA 를 깔고 운영하는 명령 모음이다. 설명은 [설치·운영 안내](../docs/ops/README.md).

```
bash deploy/install.sh        # 앱 서버 설치(인터넷) · --offline <묶음> 은 폐쇄망
bash deploy/check.sh          # 점검
bash deploy/update.sh         # 업데이트(뜨지 않으면 앞 판으로)
bash deploy/backup.sh         # 백업(cron 매일 02:30)
bash deploy/restore.sh        # 되살리기
bash deploy/bundle.sh         # 폐쇄망 설치 묶음 만들기(인터넷 되는 PC, Docker)
bash deploy/webhwp/install.sh <한컴 패키지.zip> <tomcat.zip> <license.dat>
bash deploy/model-server/serve.sh writer|embed|rerank --model <가중치 폴더>   # GPU 서버에서
```
