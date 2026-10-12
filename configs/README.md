# 설정

모델 서빙 구성 기록(`serving.yaml`), 학습 설정(`training/`), 학습 후보 생성 서비스의 systemd 단위(`systemd/`)를 둔다.
비밀번호와 API 키는 넣지 않는다.

장비 접속 정보(주소·계정·포트)는 저장소 밖 `configs/hosts.env` 에 둔다(.gitignore). 형식은 `hosts.env.example`, 셸은 `scripts/_hosts.sh`, 파이썬은 `zzaimy.hosts` 가 읽는다.
