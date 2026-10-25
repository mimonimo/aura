#!/usr/bin/env python3
"""서버 이전 목록 — 플랫폼을 다른 서버로 옮길 때 돌아가는 서비스에 필요한 것만 고른다.

경로마다 네 갈래 중 하나로 정한다(규칙은 아래 RULES 한 곳, 위에서부터 처음 맞는 규칙이 이긴다).
  옮김                    서비스가 읽고 쓰는 원본·장부·설정. 새 서버로 그대로 복사한다
  다시 만듦               DB·코드·원격 장비에서 다시 만들 수 있다(색인·캐시·그래프 산출·코드·설치물)
  옮기지 않음             백업·측정 산출물·임시·기록(log)·가상환경·모델 가중치
  학습(아스트라) — 따로   학습 자료·학습본. 아스트라가 따로 옮긴다
비밀값(.env.local·토큰·세션 서명·접속 키)은 「옮김」이지만 「비밀값 — 따로 안전하게 옮김」으로 따로 적는다.
파일 내용은 읽지 않는다 — 비밀값을 화면이나 목록 파일에 찍지 않는다(경로만 적는다).

PostgreSQL 운영 DB(문서·조각·개체·장부의 정본, ADR-0037)는 파일 복사 대상이 아니다. pack 이 pg_dump 명령을 함께 적는다.

사용(VM):
  cd ~/zzaimy-capstone && set -a; . ./.env.local; set +a
  PYTHONPATH=src .venv/bin/python scripts/209_migration_manifest.py report [--db] [--strict]
      실제 트리를 훑어 갈래별 개수·크기, 규칙에 안 걸린 1MB 넘는 경로를 data/platform/migration/manifest.json·manifest.md 로
      --db 는 DB 의 documents.stored_path·files.path 가 「옮김」 밖을 가리키는지 읽기만 해서 센다
  PYTHONPATH=src .venv/bin/python scripts/209_migration_manifest.py pack --out DIR [--dest 계정@새서버]
      rsync --files-from 용 목록 파일과 실행할 명령(commands.sh)을 DIR 에 쓴다. 복사는 하지 않는다
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MOVE = "옮김"
REGEN = "다시 만듦"
SKIP = "옮기지 않음"
TRAIN = "학습(아스트라) — 따로"
DECISIONS = (MOVE, REGEN, SKIP, TRAIN)
SECRET_NOTE = "비밀값 — 따로 안전하게 옮김"
UNMATCHED = "규칙 없음"
LARGE = 1 << 20  # 규칙에 안 걸린 것 중 이보다 큰 파일은 하나씩 적는다


@dataclass(frozen=True)
class Rule:
    pattern: str        # 저장소 뿌리 기준 상대 경로, ~/ 는 홈, data/platform/·data/train/ 은 ZZAIMY_DATA_DIR·ZZAIMY_TRAIN_DIR, **/ 는 어디서나
    decision: str
    reason: str
    secret: bool = False


def R(pattern, decision, reason, secret=False) -> Rule:
    return Rule(pattern, decision, reason, secret)


# 위에서부터 처음 맞는 규칙이 이긴다 — 좁은 규칙을 넓은 규칙보다 위에 둔다
RULES: tuple[Rule, ...] = (
    # ---- 어디서나 빼는 것 ----
    R("**/__pycache__/**", SKIP, "파이썬 실행 캐시"),
    R("**/*.pyc", SKIP, "파이썬 실행 캐시"),
    R("**/.DS_Store", SKIP, "운영체제 부산물"),
    R("**/*.bak*", SKIP, "백업 사본(*.bak*) — 원본이 옮겨진다"),
    R("**/*.retired-*.sqlite3", SKIP, "PostgreSQL 전환 전 옛 SQLite(복구용 보존본)"),
    R("**/*.log", SKIP, "실행 기록(log)"),
    R("**/.pytest_cache/**", SKIP, "시험 캐시"),
    R("**/.ruff_cache/**", SKIP, "검사 캐시"),
    R("**/.mypy_cache/**", SKIP, "검사 캐시"),
    R(".claude/**", SKIP, "작업 도구의 임시 작업 트리"),
    R("data/platform/*_marker.json", MOVE, "배치 단계 표시(다시 돌 때 이미 한 일을 건너뛴다)"),
    R("data/platform/kg_built.stamp", MOVE, "그래프 다시 만들기 표시"),
    R("data/platform/pipeline_failed.jsonl", MOVE, "처리 실패 장부(다시 시도·화면 표시)"),
    R("data/platform/pipeline_missed.jsonl", MOVE, "처리 누락 장부"),
    R("data/platform/failed_dropped.jsonl", MOVE, "처리 포기 장부"),
    R("data/platform/uploads_pushed.jsonl", MOVE, "올린 원본 장부"),
    R("data/platform/form_choice_episodes.jsonl", MOVE, "양식 고르기 기록(에이전트 기록)"),
    R("data/platform/weekly/**", MOVE, "주간 보고"),
    R("data/platform/doc_assign_snapshot.json", SKIP, "사업 배정 한때 사본"),
    R("data/platform/dedupe-removed-*.json", SKIP, "지난 중복 정리 기록"),
    R("data/platform/big_rels.txt", SKIP, "점검 중간 산출물(코드가 읽지 않음)"),
    R("data/platform/class_review_groups.json", SKIP, "검토 묶음 중간 산출물(코드가 읽지 않음)"),
    R("data/platform/kg_external_orgs_proposal.json", SKIP, "기관 제안 초안(코드가 읽지 않음)"),
    R("data/platform/zzaimy.db", SKIP, "초기 시험 DB(코드가 읽지 않음)"),
    R("data/platform/labelstudio-backup/**", SKIP, "라벨링 도구 설정 백업"),
    R("data/template-validation/**", SKIP, "양식 검증 산출물"),
    R("data/hwp-artifacts/**", SKIP, "한글 변환 시험 산출물"),
    R("data/demo/**", SKIP, "시연용 예시"),
    R("data/raw/**", SKIP, "빈 자리"),
    R("data/processed/**", SKIP, "빈 자리"),
    R(".githooks/**", REGEN, "git 훅 — git clone"),

    # ---- 설정·비밀값(저장소 뿌리) ----
    R(".env.local", MOVE, "서비스·cron 공통 설정과 접속 값(DB·모델·인증서 경로)", secret=True),
    R("configs/hosts.env", MOVE, "장비 접속 정보(저장소 밖 파일, configs/hosts.env.example 꼴)", secret=True),

    # ---- 코드 ----
    R(".git/**", REGEN, "코드 — 깃허브에서 git clone"),
    R(".venv/**", SKIP, "가상환경 — 새 서버에서 다시 설치"),
    R(".venv-*/**", SKIP, "가상환경 — 새 서버에서 다시 설치"),
    R("models/**", SKIP, "모델 가중치 — 새 서버에서 다시 내려받음"),
    R("logs/**", SKIP, "실행 기록"),
    R("src/**", REGEN, "코드 — git clone"),
    R("scripts/**", REGEN, "코드 — git clone"),
    R("tests/**", REGEN, "코드 — git clone"),
    R("docs/**", REGEN, "문서 — git clone"),
    R("configs/**", REGEN, "설정 견본 — git clone"),
    R("research/**", REGEN, "연구 자료 — git clone"),
    R("*.md", REGEN, "문서 — git clone"),
    R("*.toml", REGEN, "코드 — git clone"),
    R(".gitignore", REGEN, "코드 — git clone"),
    R(".env.example", REGEN, "설정 견본 — git clone"),

    # ---- data/platform: 비밀값 ----
    R("data/platform/accounts.json", MOVE, "계정·등급·비밀번호 해시", secret=True),
    R("data/platform/llm_connections.json", MOVE, "모델 연결 설정(열쇠 포함 가능)", secret=True),
    R("data/platform/gdrive_oauth.json", MOVE, "구글 앱 등록 값", secret=True),
    R("data/platform/gdrive_tokens.json", MOVE, "사용자별 구글 연결 토큰 — 없으면 모두 다시 연결", secret=True),
    R("data/platform/nas_sources.json", MOVE, "NAS 연결 설정(접속 값 포함)", secret=True),
    R("data/platform/.session_secret", MOVE, "로그인 세션 서명 — 없으면 새로 생겨 모두 다시 로그인", secret=True),
    R("data/platform/.collect_salt", MOVE, "수집 자료 가명 소금 — 바뀌면 가명이 이어지지 않음", secret=True),
    R("data/platform/initial_dev_password.txt", MOVE, "첫 관리자 비밀번호 안내 파일", secret=True),

    # ---- data/platform: 빼는 것(넓은 옮김 규칙보다 먼저) ----
    R("data/platform/backups/**", SKIP, "백업(조각·색인 스냅샷, 되돌리기 기록)"),
    R("data/platform/backup/**", SKIP, "옛 백업 자리"),
    R("data/platform/migration/**", SKIP, "이 도구의 산출물"),
    R("data/platform/tmp/**", SKIP, "임시 작업 자리"),
    R("data/platform/inbox/**", SKIP, "업로드 임시 자리 — 처리 뒤 documents/ 로 옮겨진다(report --db 로 남은 문서 확인)"),
    R("data/platform/knowledge/eval/**", SKIP, "검색 품질 측정 산출물"),
    R("data/platform/eval/**", SKIP, "옛 측정 산출물 자리"),

    # ---- data/platform: 다시 만듦 ----
    R("data/platform/cache/**", REGEN, "캐시(글자 좌표·쪽 그림·복원 PDF·미리보기) — 쓰일 때 다시 만든다"),
    R("data/platform/knowledge/index/**", REGEN, "검색 색인 — scripts/66_reindex.sh·170 --index 로 DB 조각에서 다시"),
    R("data/platform/knowledge/exports/**", REGEN, "외부 이용 묶음 — scripts/140 으로 다시"),
    R("data/platform/exports/**", REGEN, "수집 자료 묶음 — /dev/collect 에서 다시"),
    R("data/platform/ontology/**", REGEN, "온톨로지 내보내기 — scripts/177 로 다시"),
    R("data/platform/webhwp_forms/**", REGEN, "기안기 양식 고르기 작업 자리 — scripts/200·208 로 다시"),
    R("data/platform/chunk_embeddings*", REGEN, "옛 자리 색인 — 재색인으로 다시"),
    R("data/platform/kg_built.stamp", REGEN, "그래프 지은 표시 — 없으면 다음 주기가 다시 짓는다"),
    R("data/platform/.kg-dirty", REGEN, "그래프 다시 짓기 표시"),
    R("data/platform/.reindex-needed", REGEN, "재색인 표시"),
    R("data/platform/archive_facts.jsonl", REGEN, "원본 파일 사실 장부 — 170 이 DGX 에서 다시 받는다"),
    R("data/platform/sync_status.json", REGEN, "동기화 마지막 실행 기록 — 다음 주기가 다시 쓴다"),
    R("data/platform/pipeline_audit.json", REGEN, "연동 점검 결과 — scripts/175 가 다시 쓴다"),
    R("data/platform/program_core_docs.json", REGEN, "사업 핵심 문서 목록 — 그래프 재구축(157)이 다시 낸다"),
    R("data/platform/dept_dictionary.json", REGEN, "부서 사전 — 입력 지문으로 스스로 다시 만든다"),
    R("data/platform/reimport_rels.json", REGEN, "다시 들일 목록 — scripts/175 --find-reimport 가 다시"),

    # ---- data/platform: 옮김 ----
    R("data/platform/documents/**", MOVE, "1층 문서 — 반입·첨부·생성·보고·기안기 문서함(DB stored_path 가 가리킨다)"),
    R("data/platform/platform.db", MOVE, "DB 선택 안내 파일 — PostgreSQL 연결이 이 경로로 정해진다"),
    R("data/platform/history/**", MOVE, "장부 이력(되돌리기)"),
    R("data/platform/dept_parents.json", MOVE, "관리자가 정한 상위 부서 — 권한 판정"),
    R("data/platform/program_admin.json", MOVE, "사업 관리 장부"),
    R("data/platform/rag_spaces.json", MOVE, "부서 RAG 범위 설정"),
    R("data/platform/kg_external.json", MOVE, "외부 확인 사업 장부(출처 포함) — 그래프 입력"),
    R("data/platform/class_review*.jsonl", MOVE, "폴더·파일 검토 판정 — 그래프 입력"),
    R("data/platform/origins.jsonl", MOVE, "원본 경로 장부 — 그래프 입력"),
    R("data/platform/access_audit.jsonl", MOVE, "접근 감사 기록"),
    R("data/platform/gdocs_audit.jsonl", MOVE, "구글 문서 작업 감사 기록"),
    R("data/platform/regulation_register.jsonl", MOVE, "규정 등록·해제 기록"),
    R("data/platform/llm_usage.json", MOVE, "모델 사용량 기록"),
    R("data/platform/*_episodes.jsonl", MOVE, "에이전트 실행 기록 — 수집 자료 화면"),
    R("data/platform/platform_actions.jsonl", MOVE, "플랫폼 조작 기록 — 수집 자료 화면"),
    R("data/platform/fill_gaps.jsonl", MOVE, "못 채운 칸 기록 — 수집 자료 화면"),
    R("data/platform/gdocs_fills.json", MOVE, "구글 문서 채운 칸 기록 — 다시 쓰기가 그 칸만 비운다"),
    R("data/platform/nas_state.json", MOVE, "NAS 동기화 상태 — 없으면 처음부터 다시 훑는다"),
    R("data/platform/parse_failures.jsonl", MOVE, "처리 실패 기록 — 반입 현황"),
    R("data/platform/doc_program_changes.jsonl", MOVE, "문서 사업 배정 변경 기록"),
    R("data/platform/.sync_seen", MOVE, "동기화 확인 시각 표시"),
    R("data/platform/.sync_backlog", MOVE, "동기화 밀린 반입 표시"),

    # ---- 저장소 data/ 의 나머지 ----
    R("data/generated/templates/gdocs/published.json", MOVE, "공통 양식 드라이브 게시 기록 — 없으면 같은 양식이 다시 올라간다"),
    R("data/generated/**", REGEN, "사업별 공통 양식 — 동기화 주기(162·172)가 다시 만든다"),
    R("data/inbox/parsed/.offsets.json", MOVE, "DGX 처리 결과를 읽은 자리 — 없으면 결과 파일을 처음부터 다시 훑는다"),
    R("data/inbox/parsed/**", REGEN, "DGX 처리 결과·학습셋 목록 — 170 --parsed 가 5분마다 다시 받는다"),
    R("data/inbox/core/**", SKIP, "DGX 원본 보관소에서 받은 반입 뼈대 — 반입 뒤 documents/ 로 옮겨지고 남은 것은 다시 받는다"),
    R("data/inbox/**", SKIP, "반입 대기 자리 — 원본은 DGX 원본 보관소에 있다"),
    R("data/train/**", TRAIN, "학습 자료·학습본(3층)"),
    R("data/training/**", TRAIN, "학습 자료"),
    R("data/interim/sft/**", TRAIN, "SFT 학습셋 원본"),
    R("data/interim/**", SKIP, "측정용 합성 질의 등 중간 산출물 — 서비스가 읽지 않는다"),
    R("data/cache/**", REGEN, "한글 변환 캐시(hwp5xml) — 쓰일 때 다시 만든다"),
    R("data/scraped/**", SKIP, "공개 국고 문서 수집본 — 서비스가 읽지 않는다(반입 점검 125 만)"),
    R("data/external/**", SKIP, "외부 참고 자료 — 서비스 코드가 읽지 않는다"),
    R("data/dist/**", SKIP, "배포 묶음 산출물"),
    R("data/vision/**", SKIP, "쪽 그림 실험 산출(scripts/134)"),
    R("data/eval/**", SKIP, "평가 세트·측정 산출물"),
    R("data/tmp/**", SKIP, "임시 작업 자리(휠 포함)"),
    R("data/samples/**", SKIP, "실물 표본 — 개발용"),

    # ---- 홈 ----
    R("~/.ssh/id_ed25519_dgx_ro", MOVE, "DGX 원본 보관소 읽기 전용 키(DGX authorized_keys 에 등록됨)", secret=True),
    R("~/.ssh/id_ed25519_dgx_ro.pub", MOVE, "DGX 읽기 전용 키 공개 짝"),
    # 10-23 VM 첫 report 의 「규칙 없음」 을 보고 더함
    R("~/.config/systemd/user/zzaimy-tb-feed*", TRAIN, "학습 모니터 단위(학습 장비 담당)"),
    R("~/.config/systemd/user/zzaimy-candidate-*", TRAIN, "학습 후보 생성·게시 단위(학습 장비 담당)"),
    R("~/.config/systemd/user/label-studio.service", TRAIN, "라벨링 도구 단위(학습 장비 담당)"),
    R("~/.config/systemd/user/llamaboard.service", TRAIN, "학습 도구 단위(학습 장비 담당)"),
    R("~/.config/systemd/user/tensorboard.service", TRAIN, "학습 모니터 단위(학습 장비 담당)"),
    R("~/.config/systemd/user/zzaimy*", MOVE, "사용자 서비스 단위(zzaimy.service 등)"),
    R("~/.config/systemd/user/webhwp-*", MOVE, "웹 한글 기안기 서비스 단위"),
    R("~/.config/systemd/user/*.target.wants/**", MOVE, "서비스 켜기 연결(systemctl enable 결과)"),
    R("~/opt/dist/**", MOVE, "오프라인 설치본(kordoc 판 고정·node) — 폐쇄망에서 다시 받기 어렵다"),
    R("~/opt/webhwp/logs/**", SKIP, "기안기 Tomcat 실행 기록"),
    R("~/opt/webhwp/temp/**", SKIP, "기안기 임시 자리"),
    R("~/opt/webhwp/work/**", SKIP, "기안기 Tomcat 작업 자리"),
    R("~/opt/webhwp/**/*licen*", MOVE, "기안기 라이선스(2027-01-18 까지)", secret=True),
    R("~/opt/webhwp/**/*.lic", MOVE, "기안기 라이선스(2027-01-18 까지)", secret=True),
    R("~/opt/webhwp/README.txt", MOVE, "기안기 설치 순서"),
    R("~/opt/webhwp/conf/**", MOVE, "기안기 Tomcat 설정(127.0.0.1:8090)"),
    R("~/opt/webhwp/**", REGEN, "기안기 설치물 — README.txt 순서로 다시 설치"),
    R("~/opt/node/**", REGEN, "Node 공식 tar — 같은 판으로 다시 설치"),
    R("~/opt/kordoc/**", REGEN, "kordoc — 같은 판으로 npm 다시 설치"),
    R("~/opt/lo/**", REGEN, "LibreOffice — 다시 설치"),
    R("~/ops/**", SKIP, "운영 보조 스크립트(일회성)"),
    R("~/.cache/huggingface/**", SKIP, "모델 캐시 — 다시 내려받음"),
    R("~/app.log", SKIP, "서비스 기록"),
)

# 홈에서 훑는 곳 — 홈 전체를 훑지 않는다(서비스와 무관한 개인 파일이 「규칙 없음」으로 쏟아지지 않게)
HOME_SCAN = (".ssh/id_ed25519_dgx_ro", ".ssh/id_ed25519_dgx_ro.pub", ".config/systemd/user", "opt", "ops",
             ".cache/huggingface", "app.log")

# 환경 변수가 가리키는 파일 — 이름에 이 낱말이 있거나 설정·인증서 꼴 파일일 때만 본다(실행 파일 경로는 설치물이라 뺀다)
_SECRET_KEY = re.compile(r"KEY|SECRET|TOKEN|PASS|CRED", re.I)
_POINTER_KEY = re.compile(r"TLS|CERT|KEY|SECRET|TOKEN|PASS|CRED|CONF", re.I)
_POINTER_SUFFIX = {".pem", ".key", ".crt", ".cer", ".p12", ".json", ".env", ".conf", ".ini", ".yaml", ".yml", ".toml"}


def _seg_regex(seg: str) -> str:
    out = []
    for ch in seg:
        out.append("[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch))
    return "".join(out)


def _glob_regex(pattern: str) -> str:
    parts = pattern.split("/")
    out = []
    for i, seg in enumerate(parts):
        last = i == len(parts) - 1
        if seg == "**":
            out.append(".*" if last else "(?:[^/]+/)*")
        else:
            out.append(_seg_regex(seg) + ("" if last else "/"))
    return "".join(out)


@dataclass
class Layout:
    root: Path
    home: Path
    data_dir: Path
    train_dir: Path
    env: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, root: Path, home: Path, env: dict) -> "Layout":
        def under_root(v: str, default: str) -> Path:
            p = Path(os.path.expanduser(v or default))
            return (p if p.is_absolute() else root / p).resolve()
        return cls(root=root.resolve(), home=home.resolve(),
                   data_dir=under_root(env.get("ZZAIMY_DATA_DIR", ""), "data/platform"),
                   train_dir=under_root(env.get("ZZAIMY_TRAIN_DIR", ""), "data/train"), env=env)

    def base_of(self, pattern: str) -> tuple[str, str]:
        """규칙 꼴 → (절대 기준 경로, 나머지 꼴)."""
        if pattern.startswith("**/"):
            return "", pattern
        if pattern.startswith("~/"):
            return str(self.home), pattern[2:]
        for prefix, base in (("data/platform/", self.data_dir), ("data/train/", self.train_dir)):
            if pattern.startswith(prefix):
                return str(base), pattern[len(prefix):]
        return str(self.root), pattern

    def show(self, path: Path) -> str:
        p = str(path)
        for base, label in ((self.root, ""), (self.home, "~/")):
            b = str(base) + "/"
            if p.startswith(b) and not (label == "~/" and p.startswith(str(self.root) + "/")):
                return label + p[len(b):]
        return p


class Classifier:
    def __init__(self, layout: Layout, rules=RULES, extra: tuple[Rule, ...] = ()):
        self.layout = layout
        self.rules = tuple(rules) + tuple(extra)
        self._compiled = []
        for r in self.rules:
            if r.pattern.startswith("/"):                                  # 환경 변수가 가리키는 절대 경로
                rx = re.escape(r.pattern)
            else:
                base, rest = layout.base_of(r.pattern)
                rx = (re.escape(base) + "/" if base else "(?:.*/)?") + _glob_regex(rest.removeprefix("**/") if not base else rest)
            self._compiled.append((re.compile(rx + r"\Z"), r))

    def classify(self, path: Path) -> Rule | None:
        s = str(path)
        for rx, rule in self._compiled:
            if rx.match(s):
                return rule
        return None


def read_env(env_file: Path, include_os: bool = True) -> dict:
    """.env.local 의 키=값(값은 경로 찾기에만 쓴다, 찍지 않는다) — 이미 켜진 환경 변수가 앞선다."""
    vals: dict[str, str] = {}
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().removeprefix("export ").strip()
            vals[k] = v.strip().strip("'\"")
    if include_os:
        vals.update({k: v for k, v in os.environ.items() if k.startswith("ZZAIMY_")})
    return vals


def env_pointer_rules(layout: Layout) -> tuple[Rule, ...]:
    """환경 변수가 가리키는 파일(인증서·열쇠 등) — 다른 규칙에 안 걸리면 옮김."""
    out = []
    for k, v in sorted(layout.env.items()):
        if not v or not (v.startswith("/") or v.startswith("~")):
            continue
        p = Path(os.path.expanduser(v))
        if not p.is_file() or not (_POINTER_KEY.search(k) or p.suffix.lower() in _POINTER_SUFFIX):
            continue
        out.append(Rule(str(p.resolve()), MOVE, f"환경 변수 {k} 가 가리키는 파일", secret=bool(_SECRET_KEY.search(k))))
    return tuple(out)


def scan_roots(layout: Layout, extra: tuple[Rule, ...]) -> list[Path]:
    roots = [layout.root]
    for d in (layout.data_dir, layout.train_dir):
        if not str(d).startswith(str(layout.root) + "/"):
            roots.append(d)
    roots += [layout.home / h for h in HOME_SCAN]
    roots += [Path(r.pattern) for r in extra]
    seen, out = set(), []
    for r in roots:
        if r.exists() and str(r) not in seen and not any(str(r).startswith(str(o) + "/") for o in out):
            seen.add(str(r))
            out.append(r)
    return out


def walk(roots: list[Path]):
    """(경로, 크기) — 심볼릭 링크는 따라가지 않고 링크 자체만 센다."""
    for r in roots:
        if r.is_file() or r.is_symlink():
            yield r, r.lstat().st_size
            continue
        for dirpath, dirnames, filenames in os.walk(r, followlinks=False):
            dirnames.sort()
            for name in sorted(filenames):
                p = Path(dirpath) / name
                try:
                    yield p, p.lstat().st_size
                except OSError:
                    continue
            for name in dirnames:                       # 디렉터리 링크는 링크 하나로 센다
                p = Path(dirpath) / name
                if p.is_symlink():
                    yield p, p.lstat().st_size


def build(layout: Layout, rules=RULES) -> dict:
    extra = env_pointer_rules(layout)
    clf = Classifier(layout, rules, extra)
    totals = {d: {"count": 0, "bytes": 0} for d in DECISIONS}
    totals[UNMATCHED] = {"count": 0, "bytes": 0}
    per_rule: dict[str, dict] = {}
    files: dict[str, list] = defaultdict(list)      # 갈래 → [(보이는 경로, 절대 경로, 크기, 비밀값)]
    unmatched_dirs: dict[str, dict] = defaultdict(lambda: {"count": 0, "bytes": 0})
    unmatched_large = []
    secrets = []
    for p, size in walk(scan_roots(layout, extra)):
        rule = clf.classify(p)
        shown = layout.show(p)
        if rule is None:
            totals[UNMATCHED]["count"] += 1
            totals[UNMATCHED]["bytes"] += size
            key = "/".join(shown.split("/")[:3]) if shown.count("/") >= 3 else str(Path(shown).parent)
            unmatched_dirs[key]["count"] += 1
            unmatched_dirs[key]["bytes"] += size
            if size > LARGE:
                unmatched_large.append({"path": shown, "bytes": size})
            continue
        totals[rule.decision]["count"] += 1
        totals[rule.decision]["bytes"] += size
        pr = per_rule.setdefault(rule.pattern, {"pattern": rule.pattern, "decision": rule.decision,
                                                "reason": rule.reason, "secret": rule.secret, "count": 0, "bytes": 0})
        pr["count"] += 1
        pr["bytes"] += size
        if rule.decision == MOVE:
            files["secret" if rule.secret else "move"].append(p)
            if rule.secret:
                secrets.append({"path": shown, "reason": rule.reason, "note": SECRET_NOTE})
    unmatched_large.sort(key=lambda x: -x["bytes"])
    order = {r.pattern: i for i, r in enumerate(clf.rules)}
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "roots": {"repo": str(layout.root), "home": str(layout.home), "data_dir": layout.show(layout.data_dir),
                  "train_dir": layout.show(layout.train_dir)},
        "totals": totals,
        "rules": sorted(per_rule.values(), key=lambda x: order.get(x["pattern"], 1 << 30)),
        "rules_unused": [{"pattern": r.pattern, "decision": r.decision, "reason": r.reason}
                         for r in clf.rules if r.pattern not in per_rule],
        "secrets": secrets,
        "unmatched_large": unmatched_large,
        "unmatched_dirs": sorted(({"dir": k, **v} for k, v in unmatched_dirs.items()), key=lambda x: -x["bytes"]),
        "_files": files,
        "_layout": layout,
        "_classifier": clf,
    }


def check_db(result: dict, db_path: Path | None = None, rows=None) -> dict:
    """DB 가 가리키는 파일 경로(documents.stored_path·files.path)를 갈래로 센다 — 옮기지 않는 곳을 가리키면 알린다(읽기만)."""
    layout: Layout = result["_layout"]
    clf: Classifier = result["_classifier"]
    if rows is None:
        sys.path.insert(0, str(layout.root / "src"))
        from zzaimy.app.database_backend import connect
        path = db_path or Path(layout.env.get("ZZAIMY_PLATFORM_SQLITE_PATH") or layout.data_dir / "platform.db")
        conn = connect(path if Path(path).is_absolute() else layout.root / path, readonly=True)
        try:
            rows = [("documents", r[0]) for r in conn.execute("SELECT stored_path FROM documents").fetchall()]
            rows += [("files", r[0]) for r in conn.execute("SELECT path FROM files").fetchall()]
        finally:
            conn.close()
    counts: dict[str, int] = defaultdict(int)
    outside, missing = [], 0
    for table, raw in rows:
        raw = (raw or "").strip()
        if not raw or "://" in raw:                     # dgx:// 등 원격 보관 문서는 파일 복사와 무관
            counts["원격·빈 경로"] += 1
            continue
        p = Path(os.path.expanduser(raw))
        p = p if p.is_absolute() else layout.root / p
        rule = clf.classify(p)
        dec = rule.decision if rule else UNMATCHED
        counts[dec] += 1
        if not p.exists():
            missing += 1
        if dec != MOVE:
            outside.append({"table": table, "path": layout.show(p), "decision": dec})
    return {"counts": dict(counts), "missing_on_disk": missing, "outside_move": outside}


def _size(n: int) -> str:
    v = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{v:.0f}{unit}" if unit == "B" else f"{v:.1f}{unit}"
        v /= 1024
    return f"{n}B"


def public(result: dict) -> dict:
    return {k: v for k, v in result.items() if not k.startswith("_")}


def render_md(result: dict) -> str:
    t = result["totals"]
    lines = ["# 서버 이전 목록", "", f"만든 때 {result['generated_at']}. 저장소 {result['roots']['repo']}, "
             f"데이터 {result['roots']['data_dir']}, 학습 {result['roots']['train_dir']}.", "",
             "PostgreSQL 운영 DB 는 파일 목록에 없다 — pack 이 적는 pg_dump 명령으로 따로 옮긴다.", "",
             "## 갈래별", "", "| 갈래 | 파일 수 | 크기 |", "|---|---:|---:|"]
    for d in (*DECISIONS, UNMATCHED):
        lines.append(f"| {d} | {t[d]['count']:,} | {_size(t[d]['bytes'])} |")
    lines += ["", "## 규칙별", "", "| 갈래 | 꼴 | 파일 수 | 크기 | 까닭 |", "|---|---|---:|---:|---|"]
    for r in result["rules"]:
        reason = r["reason"] + (f" ({SECRET_NOTE})" if r["secret"] else "")
        lines.append(f"| {r['decision']} | `{r['pattern']}` | {r['count']:,} | {_size(r['bytes'])} | {reason} |")
    lines += ["", "## 비밀값", "", "내용은 적지 않는다. 경로만 적고, 새 서버로는 따로 안전하게 옮긴다(권한 600).", ""]
    lines += [f"- {s['path']} — {s['reason']}" for s in result["secrets"]] or ["- 없음"]
    lines += ["", "## 규칙에 안 걸린 경로", "",
              f"모두 {t[UNMATCHED]['count']:,}개, {_size(t[UNMATCHED]['bytes'])}. 아무 목록에도 들지 않으니 규칙을 더하거나 판단해 옮긴다.", "",
              "폴더별(큰 순서, 50개까지):", ""]
    lines += [f"- {u['dir']} — {u['count']:,}개, {_size(u['bytes'])}" for u in result["unmatched_dirs"][:50]] or ["- 없음"]
    lines += ["", "1MB 넘는 파일(큰 순서, 200개까지 — 전부는 manifest.json):", ""]
    lines += [f"- {u['path']} — {_size(u['bytes'])}" for u in result["unmatched_large"][:200]] or ["- 없음"]
    db = result.get("db")
    if db:
        lines += ["", "## DB 가 가리키는 파일", ""]
        if db.get("error"):
            lines.append(f"읽지 못함: {db['error']}")
        else:
            lines += [f"- {k}: {v:,}" for k, v in sorted(db["counts"].items())]
            lines.append(f"- 디스크에 없는 경로: {db['missing_on_disk']:,}")
            out = db["outside_move"]
            lines += ["", f"「옮김」 밖을 가리키는 것 {len(out):,}건(50건까지):", ""]
            lines += [f"- {o['table']}: {o['path']} ({o['decision']})" for o in out[:50]] or ["- 없음"]
    return "\n".join(lines) + "\n"


def write_report(result: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    j, m = out_dir / "manifest.json", out_dir / "manifest.md"
    j.write_text(json.dumps(public(result), ensure_ascii=False, indent=1), encoding="utf-8")
    m.write_text(render_md(result), encoding="utf-8")
    return j, m


def pack(result: dict, out: Path, dest: str = "<새서버>", dest_root: str = "zzaimy-capstone") -> Path:
    """rsync --files-from 목록과 명령 — 복사는 하지 않는다. 비밀값은 따로 된 목록·명령으로."""
    layout: Layout = result["_layout"]
    out.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list[str]] = defaultdict(list)
    skipped = []
    for kind in ("move", "secret"):
        for p in result["_files"].get(kind, []):
            s = str(p)
            if "\n" in s:
                skipped.append(s)
                continue
            if s.startswith(str(layout.root) + "/"):
                groups[f"{kind}:repo"].append(s[len(str(layout.root)) + 1:])
            elif s.startswith(str(layout.home) + "/"):
                groups[f"{kind}:home"].append(s[len(str(layout.home)) + 1:])
            else:
                groups[f"{kind}:abs"].append(s.lstrip("/"))
    names = {"move:repo": "files-repo.txt", "move:home": "files-home.txt", "move:abs": "files-abs.txt",
             "secret:repo": "secrets-repo.txt", "secret:home": "secrets-home.txt", "secret:abs": "secrets-abs.txt"}
    src = {"repo": str(layout.root) + "/", "home": str(layout.home) + "/", "abs": "/"}
    dst = {"repo": f"{dest}:{dest_root}/", "home": f"{dest}:", "abs": f"{dest}:/"}
    q = shlex.quote
    cmds = ["#!/bin/bash", "# 209_migration_manifest.py pack 이 쓴 이전 명령 — 옛 서버에서 차례로 실행한다. 서비스를 멈춘 뒤에.",
            "# 이 파일은 실행되지 않은 채로 만들어졌다. 목적지(" + dest + ")를 확인하고 쓴다.", "set -euo pipefail", "",
            "# 0) 서비스 멈춤(쓰기 멈춤) — cron 도 잠시 끈다", "systemctl --user stop zzaimy.service",
            f"crontab -l > {q(str(out / 'crontab.txt'))}",
            f"crontab -r   # 복사 중 동기화가 쓰지 않게. 되돌리기: crontab {q(str(out / 'crontab.txt'))}", "",
            "# 1) PostgreSQL 운영 DB — 정본. 값은 .env.local 에서 읽고 화면에 찍지 않는다",
            f"cd {q(str(layout.root))} && set -a && . ./.env.local && set +a",
            f'pg_dump -Fc --no-owner --schema="$ZZAIMY_DATABASE_SCHEMA" -f {q(str(out / "platform.dump"))} "$ZZAIMY_DATABASE_URL"',
            f"rsync -a {q(str(out / 'platform.dump'))} {q(str(out / 'crontab.txt'))} {q(dest + ':migration/')}", ""]
    step = 2
    for kind, title in (("move", "옮김 — 일반 파일"), ("secret", SECRET_NOTE + " (권한 600 으로)")):
        cmds.append(f"# {step}) {title}")
        for where in ("repo", "home", "abs"):
            key = f"{kind}:{where}"
            if not groups.get(key):
                continue
            listing = out / names[key]
            listing.write_text("\n".join(sorted(groups[key])) + "\n", encoding="utf-8")
            if kind == "secret":
                listing.chmod(0o600)
            opts = "-a --protect-args" + (" --chmod=F600" if kind == "secret" else " --info=progress2")
            cmds.append(f"rsync {opts} --files-from={q(str(listing))} {q(src[where])} {q(dst[where])}")
        cmds.append("")
        step += 1
    cmds += ["# 받은 쪽에서: docs/ops/migration.md 의 「새 서버에서」 순서(설치 → DB 복원 → 다시 만들기 → 시작 → 확인)"]
    sh = out / "commands.sh"
    sh.write_text("\n".join(cmds) + "\n", encoding="utf-8")
    summary = {"generated_at": result["generated_at"], "dest": dest, "dest_root": dest_root,
               "lists": {names[k]: len(v) for k, v in sorted(groups.items())}, "skipped_newline_paths": len(skipped),
               "totals": result["totals"]}
    (out / "pack.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return sh


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="서버 이전 목록(옮김·다시 만듦·옮기지 않음·학습)")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="저장소 뿌리")
    ap.add_argument("--home", type=Path, default=Path.home())
    ap.add_argument("--env-file", type=Path, default=None, help="기본 <뿌리>/.env.local")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("report", help="트리를 훑어 manifest.json·manifest.md 를 쓴다")
    rp.add_argument("--out", type=Path, default=None, help="기본 <데이터>/migration")
    rp.add_argument("--db", action="store_true", help="DB 의 문서 경로가 옮김 안에 있는지 읽기만 해서 센다")
    rp.add_argument("--strict", action="store_true", help="1MB 넘는 규칙 없는 파일이 있으면 종료 코드 1")
    pk = sub.add_parser("pack", help="rsync --files-from 목록과 명령을 쓴다(복사하지 않는다)")
    pk.add_argument("--out", type=Path, required=True)
    pk.add_argument("--dest", default="<새서버>", help="계정@새서버")
    pk.add_argument("--dest-root", default="zzaimy-capstone", help="새 서버 홈 아래 저장소 폴더")
    a = ap.parse_args(argv)

    root = a.root.resolve()
    env = read_env(a.env_file or root / ".env.local")
    layout = Layout.from_env(root, a.home, env)
    result = build(layout)
    t = result["totals"]
    for d in (*DECISIONS, UNMATCHED):
        print(f"{d:<14} {t[d]['count']:>9,}개 {_size(t[d]['bytes']):>9}")
    print(f"비밀값 {len(result['secrets'])}개(경로만 목록에) — {SECRET_NOTE}")
    if a.cmd == "report":
        if a.db:
            try:
                result["db"] = check_db(result)
            except Exception as e:  # DB 를 못 읽어도 파일 목록은 낸다
                result["db"] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            db = result["db"]
            if "error" in db:
                print("DB 점검 실패:", db["error"])
            else:
                print(f"DB 경로: 옮김 밖 {len(db['outside_move']):,}건, 디스크에 없음 {db['missing_on_disk']:,}건")
        j, m = write_report(result, a.out or layout.data_dir / "migration")
        print(f"규칙 없음 1MB 초과 {len(result['unmatched_large']):,}개 — {m}")
        print(f"목록: {j}")
        return 1 if a.strict and result["unmatched_large"] else 0
    sh = pack(result, a.out, a.dest, a.dest_root)
    print(f"명령: {sh} (실행하지 않았다)")
    print(sh.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
