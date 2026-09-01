# 환경 점검 리포트 (DGX Spark)

> `scripts/00_env_check.py` 자동 생성 — W1-W2 TASK-01

| 항목 | 값 |
|---|---|
| 점검 시각 | 2026-09-01T15:28:59+09:00 |
| 호스트 | spark-1397 |
| OS | Linux 6.17.0-1008-nvidia |
| 아키텍처 | aarch64 |
| 커널 | #8-Ubuntu SMP PREEMPT_DYNAMIC Wed Jan 21 17:56:56 UTC 2026 |
| NVIDIA 드라이버 / GPU | 580.126.09, NVIDIA GB10 |
| CUDA 버전 (nvidia-smi) | 13.0 |
| nvcc | 미확인 (nvcc 없음) |
| GPU compute capability | 12.1 |
| 메모리 총량 | 119.7 GiB |
| 메모리 가용량 | 116.6 GiB |
| Python | 3.12.3 (/home/jun/zzaimy-capstone/.venv/bin/python) |
| pip | pip 24.0 from /home/jun/zzaimy-capstone/.venv/lib/python3.12/site-packages/pip (python 3.12) |

## nvidia-smi 전체 출력

```
Tue Sep  1 15:28:59 2026       
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 580.126.09             Driver Version: 580.126.09     CUDA Version: 13.0     |
+-----------------------------------------+------------------------+----------------------+
| GPU  Name                 Persistence-M | Bus-Id          Disp.A | Volatile Uncorr. ECC |
| Fan  Temp   Perf          Pwr:Usage/Cap |           Memory-Usage | GPU-Util  Compute M. |
|                                         |                        |               MIG M. |
|=========================================+========================+======================|
|   0  NVIDIA GB10                    Off |   0000000F:01:00.0 Off |                  N/A |
| N/A   40C    P0             15W /  N/A  | Not Supported          |      6%      Default |
|                                         |                        |                  N/A |
+-----------------------------------------+------------------------+----------------------+

+-----------------------------------------------------------------------------------------+
| Processes:                                                                              |
|  GPU   GI   CI              PID   Type   Process name                        GPU Memory |
|        ID   ID                                                               Usage      |
|=========================================================================================|
|  No running processes found                                                             |
+-----------------------------------------------------------------------------------------+
```
