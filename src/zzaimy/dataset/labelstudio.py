"""데이터 공방 ↔ Label Studio 왕복 (ADR-0011).

데이터 공방(build.py)이 만든 학습 쌍을 Label Studio 검수 태스크로 내보내고,
사람이 채택/수정/폐기한 결과를 다시 학습 쌍으로 되받는다. 순수 함수로만 두어
DB·라우트·네트워크에 의존하지 않는다 — 연결(내보내기·되받기 버튼)은 서버측.

되받은 수정본에도 수치 검증(verify_numbers)을 적용한다. 근거에 없는 수치가
들어오면 그 쌍은 폐기한다 — 공방의 정제 규칙(_passes)과 같은 사상.

전제: 외부 SaaS가 아니라 로컬 Label Studio 인스턴스. 학습 쌍은 build.py의
형식({"conversations": [{"from","value"}...], "meta": {...}})을 따른다.
"""

from __future__ import annotations

from zzaimy.verify.numbers import verify_numbers

# 검수 판정 값 — 한국어/영어 모두 허용(라벨링 설정·외부 입력 방어)
_DISCARD = {"폐기", "discard", "drop", "reject"}
_EDIT = {"수정", "edit", "fix", "modify"}
_ACCEPT = {"채택", "accept", "keep", "approve"}

# Label Studio 라벨링 설정(XML). 입력(문맥)·AI 출력을 나란히 보여주고
# 채택/수정/폐기 + 수정본 입력 컨트롤을 둔다.
LABEL_CONFIG = """<View>
  <Header value="접수 문맥과 AI 출력을 검수하세요"/>
  <View style="display:flex; gap:1em">
    <View style="width:50%">
      <Header value="입력 (문맥·근거)"/>
      <Text name="input" value="$input"/>
    </View>
    <View style="width:50%">
      <Header value="AI 출력"/>
      <Text name="output" value="$output"/>
    </View>
  </View>
  <Choices name="decision" toName="output" choice="single" showInline="true" required="true">
    <Choice value="채택"/>
    <Choice value="수정"/>
    <Choice value="폐기"/>
  </Choices>
  <Header value="수정본 (수정을 고른 경우에만 작성)"/>
  <TextArea name="corrected" toName="output" rows="8" editable="true" maxSubmissions="1"/>
</View>"""


def _human_turns(pair: dict) -> list[str]:
    """사람 발화(입력·문맥) 값 목록. 수치 검증의 근거로 쓴다."""
    return [
        t.get("value", "")
        for t in pair.get("conversations", [])
        if t.get("from") == "human"
    ]


def _last_gpt_index(pair: dict) -> int:
    convs = pair.get("conversations", [])
    for i in range(len(convs) - 1, -1, -1):
        if convs[i].get("from") == "gpt":
            return i
    return -1


def export_to_label_studio(pairs: list[dict]) -> list[dict]:
    """학습 쌍을 Label Studio import 태스크(JSON)로 변환한다.

    각 태스크의 data에 입력(문맥)·출력을 노출하고, 정확한 복원을 위해 원쌍을
    `_pair`로 보존한다. 반환은 Label Studio가 그대로 import하는 태스크 목록.
    """
    tasks: list[dict] = []
    for i, pair in enumerate(pairs, start=1):
        gi = _last_gpt_index(pair)
        output = pair["conversations"][gi]["value"] if gi >= 0 else ""
        input_text = "\n\n".join(_human_turns(pair))
        meta = pair.get("meta") or {}
        tasks.append({
            "id": i,
            "data": {
                "input": input_text,
                "output": output,
                "source": meta.get("source", ""),
                "doc_id": meta.get("doc_id"),
                "_pair": pair,   # 복원용 원쌍(표시용 아님)
            },
        })
    return tasks


def _decision_and_correction(task: dict) -> tuple[str | None, str | None]:
    """LS 태스크의 검수 결과에서 판정과 수정본을 뽑는다.

    annotations[].result 의 choices(판정)와 textarea(수정본)를 읽는다.
    미검수(annotations 없음)면 (None, None).
    """
    anns = task.get("annotations") or []
    decision: str | None = None
    corrected: str | None = None
    for ann in anns:
        for r in ann.get("result") or []:
            val = r.get("value") or {}
            if "choices" in val and val["choices"]:
                decision = val["choices"][0]
            elif "text" in val and val["text"]:
                corrected = val["text"][0]
    return decision, corrected


def import_from_label_studio(annotations: list[dict]) -> list[dict]:
    """Label Studio 검수 결과를 학습 쌍으로 되받는다.

    - 폐기: 제외.
    - 수정: 수정본을 출력으로 반영.
    - 채택: 원 출력 유지.
    - 되받은 최종 출력이 수치 검증(근거=입력 문맥)을 통과하지 못하면 폐기.
    반환은 build.py 형식의 학습 쌍 목록(검수 메타 부가).
    """
    kept: list[dict] = []
    for task in annotations:
        data = task.get("data") or {}
        pair = data.get("_pair")
        if not pair:
            continue
        decision, corrected = _decision_and_correction(task)
        if decision is None:
            continue  # 미검수
        norm = decision.strip().lower()
        is_discard = decision.strip() in _DISCARD or norm in _DISCARD
        is_edit = decision.strip() in _EDIT or norm in _EDIT
        if is_discard:
            continue

        gi = _last_gpt_index(pair)
        if gi < 0:
            continue
        original_output = pair["conversations"][gi]["value"]
        final_output = corrected if (is_edit and corrected) else original_output
        if not final_output.strip():
            continue

        # 되받은 출력도 수치 검증 — 근거 없는 수치가 있으면 폐기
        if not verify_numbers(final_output, _human_turns(pair)).ok:
            continue

        # 원쌍을 복사해 최종 출력·검수 메타 반영
        new_pair = {
            "conversations": [dict(t) for t in pair["conversations"]],
            "meta": {**(pair.get("meta") or {}), "reviewed": True,
                     "decision": decision.strip()},
        }
        new_pair["conversations"][gi]["value"] = final_output
        kept.append(new_pair)
    return kept
