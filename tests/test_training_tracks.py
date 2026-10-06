import pytest
from zzaimy.dataset.tracks import build


def payload():
    turn = {"question":"대상 사업의 계획서를 준비하려면?", "answer":"공고와 서식을 먼저 확인합니다.",
            "rationale":"작성 항목은 해당 사업의 서식을 따라야 합니다.", "evidence":"",
            "search_plan":{"documents":["공고","서식"],"relations":["사업-문서"],"filters":{"사업":"대상 사업"},
                           "missing":"사업 연도를 확인합니다."}}
    return {"context":"대상 사업의 문서 구조와 작성 기준을 확인하는 대화", "turns":[turn.copy(),dict(turn,question="그다음에는?")]}


def test_program_track_keeps_history_and_rationale():
    result=build("program_retrieval",payload(),"원문",{"id":"internal-doc"})
    assert len(result["messages"]) == 5
    assert "근거 설명:" in result["messages"][2]["content"]
    assert result["approved"] is False and result["execution_trace"] is False
    assert "원문" not in str(result["messages"])


def test_program_track_rejects_facts():
    p=payload();p["turns"][0]["answer"]="실적은 85명입니다."
    with pytest.raises(ValueError,match="business_numbers"):
        build("program_retrieval",p,"실적은 85명",{})
    p=payload();p["turns"][0]["evidence"]="내부 원문"
    with pytest.raises(ValueError,match="business_facts"):
        build("program_retrieval",p,"내부 원문",{})


def test_policy_track_requires_exact_evidence():
    p=payload()
    for turn in p["turns"]: turn["evidence"]="위원회 심의를 거친다."
    result=build("institutional_policy",p,"위원회 심의를 거친다.",{})
    assert "위원회 심의를 거친다." in result["messages"][1]["content"]
    with pytest.raises(ValueError,match="evidence_not_in_source"):
        build("institutional_policy",p,"다른 원문",{})
