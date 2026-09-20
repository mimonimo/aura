#!/bin/bash
cd ~/zzaimy-capstone
OMP_NUM_THREADS=10 MKL_NUM_THREADS=10 .venv/bin/python scripts/62_refix_overlay.py
OMP_NUM_THREADS=10 ZZAIMY_EMBED_DEVICE=cpu .venv-train/bin/python scripts/52_embed_chunks.py >> /tmp/embed-reproc.log 2>&1
pkill -f "[z]zaimy.app.main"; sleep 1
(nohup ~/start-platform.sh > /tmp/platform.log 2>&1 &)
sleep 8
curl -sk -u zzaimy:password -o /dev/null -w "대시보드: %{http_code}\n" https://localhost:8800/
echo DIGITAL_OVERLAY_DONE
