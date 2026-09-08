## 2026-09-08 — 한글 저작 방향 확정 + 국고 코퍼스 수집·인제스트

- **한글 문서 생성 = COM 에이전트(실제 한글) + Markdown IR, 미리보기 = PDF, rhwp 폐기**
  (ADR-0014, 0013 대체). rhwp는 렌더·생성 품질 부족(폰트·조판)·미성숙으로 탈락 —
  네이티브 HWP 브라우저 뷰어는 성숙 오픈소스가 없다는 결론. 에이전트 고도화 완료
  (여러 한글 창 공존·GetActiveObject, PDF/hwpx 산출물 서버 회수 op, 유휴 자동갱신).
  서버 배관(md→op 변환 `hwp/md_ops.py`, 산출물 회수·서빙, PDF 미리보기) TestClient 검증.
- **공개 국고 문서 수집(파일럿 코퍼스)**: uispc(대학혁신지원) 36 + uniall.nrf 공고 72
  = 108건(`data/samples/국고계획서·국고공고/`, git제외). 재사용 수집기 `scripts/72·73`.
  uniall은 search.do/detail.do/download.do 역분석으로 브라우저 없이 수집.
- **인제스트 파이프라인 붙임**(`scripts/74_ingest_corpus.py`): 파싱→**PII 마스킹(인덱싱 前)**
  →조각화→적재. 실측: 87문서·1,693조각·PII 68건, 0오류(`corpus_pilot.db`). 희소검색
  프로브로 수집→인제스트→검색 end-to-end 증명(dense·rerank는 VM 재색인 후).
- **.hwp 리더 신설**(`ingest/hwp_text.py`, ADR-0014 열린항목 해소): pyhwp(+six)·zip/xml.
  배포용(암호화) HWPX는 EncryptedHwpxError로 구분. 표 셀은 lattice 병용(후속).
- **남은 run-time**: VM에서 `66_reindex.sh`(KURE-v1 임베딩) → dense 활성. PII 스크리닝을
  운영 platform.db 적재 전 재확인.

## 지금 하는 일
- 외부 참조 관문 마무리 — 감사 기록·승인 대기 화면(/dev/egress)까지 붙임.
  실제 외부 전송은 아웃바운드 개방·API 키가 갖춰지면 켠다
- 계획서 초안 생성 = 한글 COM 에이전트(ADR-0014). 한글 워커 붙으면 실물 검증

## 다음 할 일
- 초안에 자료 문서 그림도 넣기
- 실물 스캔 오면 화질 보정 기법(DocRes, UVDoc) 맞대결
- 기획서·중간 발표자료 생성 (양식 받으면 시작)
- 문서-규정 연관 그래프

## 막힌 것 (학교 확인 대기)
- 교내 실물 문서 표본 — 와야 파일럿 시작 가능
- 개인정보 부서 검토, 열람 등급 체계 확정
