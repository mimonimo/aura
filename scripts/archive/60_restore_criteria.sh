#!/bin/bash
# 기준 문서 일괄 (재)등록 — /tmp/refs의 원본으로 규정 저장소를 복구한다.
# Spark에서 실행: bash scripts/60_restore_criteria.sh
set -u
PASS=$(cat ~/.zzaimy-pass)
BASE=https://localhost:8800

reg() { # reg <sector> <파일경로> <표시이름>
  code=$(curl -sk -u "zzaimy:$PASS" -o /dev/null -w "%{http_code}" \
    -F "sector=$1" -F "file=@$2;filename=$3" "$BASE/criteria/upload")
  echo "[$code] $3 (sector=$1)"
  # 파싱·조각화가 끝나길 기다렸다가 다음 문서 (순차 처리로 안정성 확보)
  sleep 45
}

reg common  "/tmp/refs/hakchik.pdf"                                        "영남이공대학교_학칙.pdf"
reg grant   "/tmp/refs/대학재정지원사업+매뉴얼(최종).pdf"                    "대학재정지원사업_운영관리_매뉴얼.pdf"
reg recruit "/tmp/refs/채용공고_고교단계_일학습병행_공동훈련센터_지역밀착형_지원기관.pdf" "채용공고_지역밀착형_지원기관.pdf"
reg recruit "/tmp/refs/입학팀+계약직원+채용+공고문(2026.09.21.).pdf"          "입학팀_계약직원_채용공고.pdf"
reg common  "/tmp/refs/iacf/law01.pdf" "IACF_산학협력단_법인_정관.pdf"
reg common  "/tmp/refs/iacf/law02.pdf" "IACF_산학협력단_운영_규정.pdf"
reg common  "/tmp/refs/iacf/law03.pdf" "IACF_산학협력단_사무분장_규정.pdf"
reg recruit "/tmp/refs/iacf/law04.pdf" "IACF_산학협력단_계약직원_임용_내규.pdf"
reg recruit "/tmp/refs/iacf/law05.pdf" "IACF_산학협력단_직원_취업규칙.pdf"
reg common  "/tmp/refs/iacf/law06.pdf" "IACF_연구센터_설치_및_운영_규정.pdf"
reg common  "/tmp/refs/iacf/law07.pdf" "IACF_중소기업산학협력센터_운영_규정.pdf"
reg common  "/tmp/refs/iacf/law08.pdf" "IACF_지식재산권_관리_규정.pdf"

echo "등록 요청 완료 — 처리 상태는 /criteria 에서 확인"
