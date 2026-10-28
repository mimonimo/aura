from zzaimy.app.delegate import parse_sub, strip_reasoning


def test_english_reasoning_lines_dropped_korean_kept():
    raw = "Need ensure no 양식 예시 글자 (○○·000). None.\n[본문]\n1. AI 교육 과정 운영(LINC 3.0 연계)\n| 구분 | AI |\n"
    body, _ = parse_sub(raw)
    assert "Need ensure" not in body
    assert "AI 교육 과정 운영(LINC 3.0 연계)" in body and "| 구분 | AI |" in body


def test_mixed_korean_english_reasoning_dropped():
    # 10-28 agent-v3 문서에 그대로 들어간 꼴
    text = ('가) 우리 대학 1학년 전체 학생을 대상으로 AI 교육 사업을 운영함.\n'
            'But "역량 강화" abstract? It has target/method? Maybe "AI 교육 사업을 운영하여" method, target.\n'
            'Need maybe note: > 예산 규모는 담당자가 제시한 추정 규모임.\n'
            '나) ICT·AI 기반 교과를 개설함 (출처: 공고 1쪽).')
    out = strip_reasoning(text)
    assert "But" not in out and "Need maybe" not in out
    assert "가) 우리 대학" in out and "나) ICT·AI 기반" in out


def test_close_think_only():
    body, _ = parse_sub("thinking here...</think>[본문]\n가. 총 목표")
    assert body == "가. 총 목표"


def test_keeps_urls_and_acronyms():
    assert strip_reasoning("ICT 기반 AI 교육") == "ICT 기반 AI 교육"
    line = "누리집 https://www.example.ac.kr/notice/view 참고"
    assert strip_reasoning(line) == line


def test_paths_and_names_not_reasoning():
    for line in ("[그림 1-2] 전체 구조 (docs/images/diagrams/01-system.png, 플랫폼 개발 현황 과 같은 그림)",
                 "검수는 Label Studio 에서 하고 서빙은 vLLM 으로 한다",
                 "Qwen Writer Model 로고"):
        assert strip_reasoning(line) == line


def test_korean_sentence_with_english_term_kept():
    line = "단계 추론(chain-of-thought)[49]은 모델이 답 전에 중간 단계를 글로 써 내려가게 해 정확도를 높이는 방법이다."
    assert strip_reasoning(line) == line


def test_code_lines_kept():
    line = "def find_spans(text: str, detectors: Sequence[tuple[str, Any, Any]]) -> list[tuple[int, int]]:"
    assert strip_reasoning(line) == line
    line2 = "SEMANTIC_MARGIN = 0.10 # 돋보임 폭(최고 − 평균) 하한 — 측정값 .061(무관)·.134(관련) 사이"
    assert strip_reasoning(line2) == line2
